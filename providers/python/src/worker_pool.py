from __future__ import annotations

import atexit
import multiprocessing as mp
import os
import queue as stdlib_queue
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


_CURRENT_DIR = Path(__file__).resolve().parent
if str(_CURRENT_DIR) not in sys.path:
    sys.path.append(str(_CURRENT_DIR))


def _dispatch_step(step_type: str, step_input: dict[str, Any]) -> dict[str, Any]:
    if str(step_type or "").strip() == "worker_runtime_probe":
        from runtime_probe import build_worker_runtime_probe

        return build_worker_runtime_probe(step_input)

    from new_protocol_register.easyprotocol_flow import dispatch_easyprotocol_step

    return dispatch_easyprotocol_step(step_type=step_type, step_input=step_input)


def _int_from_env(name: str, default: int, *, minimum: int = 0) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, int(raw))
    except Exception:
        return max(minimum, default)


def _float_from_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, float(raw))
    except Exception:
        return max(minimum, default)


def _resolve_mp_context() -> mp.context.BaseContext:
    preferred = str(os.getenv("PYTHON_PROTOCOL_POOL_START_METHOD", "") or "").strip().lower()
    if preferred:
        try:
            return mp.get_context(preferred)
        except ValueError:
            pass
    return mp.get_context("spawn")


def _worker_process_main(
    worker_id: str,
    task_queue: Any,
    result_queue: Any,
) -> None:
    os.environ["PYTHON_PROTOCOL_WORKER_ID"] = worker_id
    os.environ["PYTHON_PROTOCOL_WORKER_LAUNCHED_AT"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    while True:
        task = task_queue.get()
        if task is None:
            return

        task_id = str(task.get("task_id") or "").strip()
        step_type = str(task.get("step_type") or "").strip()
        step_input = task.get("step_input")
        if not isinstance(step_input, dict):
            step_input = {}

        try:
            result = _dispatch_step(step_type=step_type, step_input=step_input)
            result_queue.put(
                {
                    "task_id": task_id,
                    "worker_id": worker_id,
                    "ok": True,
                    "result": result,
                }
            )
        except RuntimeError as exc:
            result_queue.put(
                {
                    "task_id": task_id,
                    "worker_id": worker_id,
                    "ok": False,
                    "error": {
                        "category": "operation_error",
                        "message": str(exc),
                        "details": {
                            "step_type": step_type,
                            "worker_id": worker_id,
                        },
                    },
                }
            )
        except Exception as exc:  # pragma: no cover - defensive runtime boundary
            result_queue.put(
                {
                    "task_id": task_id,
                    "worker_id": worker_id,
                    "ok": False,
                    "error": {
                        "category": "service_runtime_error",
                        "message": str(exc),
                        "details": {
                            "step_type": step_type,
                            "worker_id": worker_id,
                        },
                    },
                }
            )


class WorkerPoolError(RuntimeError):
    pass


class WorkerAcquireTimeout(WorkerPoolError):
    pass


class WorkerExecutionTimeout(WorkerPoolError):
    pass


@dataclass(frozen=True)
class WorkerPoolSettings:
    min_warm_workers: int
    max_workers: int
    idle_timeout_seconds: float
    task_timeout_seconds: float
    acquire_timeout_seconds: float
    max_tasks_per_worker: int
    reaper_interval_seconds: float

    @classmethod
    def from_env(cls) -> "WorkerPoolSettings":
        min_warm = _int_from_env("PYTHON_PROTOCOL_MIN_WARM_WORKERS", 1, minimum=0)
        max_workers = _int_from_env("PYTHON_PROTOCOL_MAX_WORKERS", 6, minimum=1)
        min_warm = min(min_warm, max_workers)
        return cls(
            min_warm_workers=min_warm,
            max_workers=max_workers,
            idle_timeout_seconds=_float_from_env("PYTHON_PROTOCOL_IDLE_TIMEOUT_SECONDS", 600.0, minimum=5.0),
            task_timeout_seconds=_float_from_env("PYTHON_PROTOCOL_TASK_TIMEOUT_SECONDS", 1800.0, minimum=5.0),
            acquire_timeout_seconds=_float_from_env("PYTHON_PROTOCOL_ACQUIRE_TIMEOUT_SECONDS", 60.0, minimum=1.0),
            max_tasks_per_worker=_int_from_env("PYTHON_PROTOCOL_MAX_TASKS_PER_WORKER", 50, minimum=1),
            reaper_interval_seconds=_float_from_env("PYTHON_PROTOCOL_REAPER_INTERVAL_SECONDS", 5.0, minimum=1.0),
        )


@dataclass
class _WorkerHandle:
    worker_id: str
    process: Any
    task_queue: Any
    result_queue: Any
    busy: bool = False
    tasks_completed: int = 0
    launched_at: float = field(default_factory=time.monotonic)
    last_used_at: float = field(default_factory=time.monotonic)


class StepExecutionPool:
    def __init__(self, settings: WorkerPoolSettings | None = None) -> None:
        self.settings = settings or WorkerPoolSettings.from_env()
        self._context = _resolve_mp_context()
        self._workers: dict[str, _WorkerHandle] = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._closed = False
        self._stop_reaper = threading.Event()
        self._reaper_thread = threading.Thread(
            target=self._reaper_loop,
            name="python-protocol-pool-reaper",
            daemon=True,
        )
        with self._condition:
            self._ensure_min_warm_locked()
        self._reaper_thread.start()
        atexit.register(self.close)

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._stop_reaper.set()
            workers = list(self._workers.values())
            self._workers.clear()
            self._condition.notify_all()

        for worker in workers:
            self._stop_worker(worker)

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            self._prune_dead_workers_locked()
            total = len(self._workers)
            busy = sum(1 for worker in self._workers.values() if worker.busy)
            idle = total - busy
            return {
                "mode": "dynamic_process_pool",
                "minWarmWorkers": self.settings.min_warm_workers,
                "maxWorkers": self.settings.max_workers,
                "idleTimeoutSeconds": self.settings.idle_timeout_seconds,
                "taskTimeoutSeconds": self.settings.task_timeout_seconds,
                "acquireTimeoutSeconds": self.settings.acquire_timeout_seconds,
                "maxTasksPerWorker": self.settings.max_tasks_per_worker,
                "totalWorkers": total,
                "busyWorkers": busy,
                "idleWorkers": idle,
            }

    def execute_step(self, *, step_type: str, step_input: dict[str, Any]) -> dict[str, Any]:
        worker = self._acquire_worker()
        retire_after = False
        try:
            response = self._run_task(worker, step_type=step_type, step_input=step_input)
            if response.get("ok"):
                retire_after = self._record_task_completion(worker)
                return dict(response.get("result") or {})

            retire_after = self._record_task_completion(worker)
            error = response.get("error")
            if isinstance(error, dict):
                return {"_error": error}
            return {
                "_error": {
                    "category": "service_runtime_error",
                    "message": "worker returned malformed error payload",
                    "details": {"worker_id": worker.worker_id},
                }
            }
        except WorkerExecutionTimeout as exc:
            self._drop_worker(worker)
            raise exc
        except WorkerPoolError as exc:
            self._drop_worker(worker)
            raise exc
        finally:
            self._release_worker(worker, retire_after=retire_after)

    def _acquire_worker(self) -> _WorkerHandle:
        deadline = time.monotonic() + self.settings.acquire_timeout_seconds
        with self._condition:
            while True:
                if self._closed:
                    raise WorkerPoolError("execution pool is closed")

                self._prune_dead_workers_locked()
                idle_worker = next((worker for worker in self._workers.values() if not worker.busy), None)
                if idle_worker is not None:
                    idle_worker.busy = True
                    return idle_worker

                if len(self._workers) < self.settings.max_workers:
                    worker = self._spawn_worker_locked()
                    worker.busy = True
                    return worker

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WorkerAcquireTimeout("no execution worker became available before acquire timeout")
                self._condition.wait(timeout=min(1.0, remaining))

    def _release_worker(self, worker: _WorkerHandle, *, retire_after: bool) -> None:
        should_stop = False
        with self._condition:
            current = self._workers.get(worker.worker_id)
            if current is None:
                return
            current.busy = False
            current.last_used_at = time.monotonic()
            if retire_after and len(self._workers) > self.settings.min_warm_workers:
                del self._workers[worker.worker_id]
                should_stop = True
            self._ensure_min_warm_locked()
            self._condition.notify_all()

        if should_stop:
            self._stop_worker(worker)

    def _record_task_completion(self, worker: _WorkerHandle) -> bool:
        with self._condition:
            current = self._workers.get(worker.worker_id)
            if current is None:
                return False
            current.tasks_completed += 1
            return current.tasks_completed >= self.settings.max_tasks_per_worker

    def _drop_worker(self, worker: _WorkerHandle) -> None:
        removed = False
        with self._condition:
            if worker.worker_id in self._workers:
                del self._workers[worker.worker_id]
                removed = True
            self._ensure_min_warm_locked()
            self._condition.notify_all()

        if removed:
            self._stop_worker(worker)

    def _run_task(self, worker: _WorkerHandle, *, step_type: str, step_input: dict[str, Any]) -> dict[str, Any]:
        task_id = uuid.uuid4().hex
        try:
            worker.task_queue.put(
                {
                    "task_id": task_id,
                    "step_type": step_type,
                    "step_input": dict(step_input),
                },
                timeout=1.0,
            )
        except Exception as exc:
            raise WorkerPoolError(f"failed to send task to worker {worker.worker_id}: {exc}") from exc

        deadline = time.monotonic() + self.settings.task_timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkerExecutionTimeout(
                    f"worker {worker.worker_id} timed out after {self.settings.task_timeout_seconds:.0f}s"
                )

            try:
                response = worker.result_queue.get(timeout=min(1.0, remaining))
            except stdlib_queue.Empty:
                if not worker.process.is_alive():
                    raise WorkerPoolError(f"worker {worker.worker_id} exited unexpectedly")
                continue
            except Exception as exc:
                raise WorkerPoolError(f"failed to read worker result {worker.worker_id}: {exc}") from exc

            if str(response.get("task_id") or "").strip() != task_id:
                raise WorkerPoolError(
                    f"worker {worker.worker_id} returned mismatched task id: {response.get('task_id')}"
                )
            return response

    def _reaper_loop(self) -> None:
        while not self._stop_reaper.wait(self.settings.reaper_interval_seconds):
            with self._condition:
                if self._closed:
                    return
                self._prune_dead_workers_locked()
                self._shrink_idle_workers_locked()
                self._ensure_min_warm_locked()
                self._condition.notify_all()

    def _spawn_worker_locked(self) -> _WorkerHandle:
        worker_id = f"py-worker-{uuid.uuid4().hex[:8]}"
        task_queue = self._context.Queue(maxsize=1)
        result_queue = self._context.Queue(maxsize=1)
        process = self._context.Process(
            target=_worker_process_main,
            args=(worker_id, task_queue, result_queue),
            name=worker_id,
        )
        process.start()
        worker = _WorkerHandle(
            worker_id=worker_id,
            process=process,
            task_queue=task_queue,
            result_queue=result_queue,
        )
        self._workers[worker_id] = worker
        return worker

    def _ensure_min_warm_locked(self) -> None:
        while len(self._workers) < self.settings.min_warm_workers:
            self._spawn_worker_locked()

    def _prune_dead_workers_locked(self) -> None:
        for worker_id, worker in list(self._workers.items()):
            if worker.process.is_alive():
                continue
            del self._workers[worker_id]
            self._cleanup_queues(worker)

    def _shrink_idle_workers_locked(self) -> None:
        if self.settings.idle_timeout_seconds <= 0:
            return
        now = time.monotonic()
        idle_workers = [
            worker
            for worker in self._workers.values()
            if not worker.busy and (now - worker.last_used_at) >= self.settings.idle_timeout_seconds
        ]
        idle_workers.sort(key=lambda worker: worker.last_used_at)
        while len(self._workers) > self.settings.min_warm_workers and idle_workers:
            worker = idle_workers.pop(0)
            if worker.worker_id not in self._workers:
                continue
            del self._workers[worker.worker_id]
            threading.Thread(
                target=self._stop_worker,
                args=(worker,),
                name=f"stop-{worker.worker_id}",
                daemon=True,
            ).start()

    def _stop_worker(self, worker: _WorkerHandle) -> None:
        try:
            worker.task_queue.put(None, timeout=0.2)
        except Exception:
            pass

        process = worker.process
        process.join(timeout=1.0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=1.0)

        self._cleanup_queues(worker)

    def _cleanup_queues(self, worker: _WorkerHandle) -> None:
        for maybe_queue in (worker.task_queue, worker.result_queue):
            try:
                maybe_queue.close()
            except Exception:
                pass
            try:
                maybe_queue.join_thread()
            except Exception:
                pass
