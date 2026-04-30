from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_health(base_url: str, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            return request_json(f"{base_url}/health")
        except Exception as exc:  # pragma: no cover - startup timing
            last_error = str(exc)
            time.sleep(1.0)
    raise RuntimeError(f"python protocol manager health check timed out: {last_error}")


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def format_logs(label: str, path: Path) -> str:
    try:
        if not path.exists():
            return f"{label}: <missing>"
        content = path.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            return f"{label}: <empty>"
        tail = "\n".join(content.splitlines()[-40:])
        return f"{label}:\n{tail}"
    except Exception as exc:  # pragma: no cover - defensive diagnostics
        return f"{label}: <unavailable: {exc}>"


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the python protocol manager pool.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    server_path = repo_root / "providers" / "python" / "src" / "server.py"
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env.update(
        {
            "PYTHON_PROTOCOL_HOST": "127.0.0.1",
            "PYTHON_PROTOCOL_PORT": str(port),
            "PYTHON_PROTOCOL_MIN_WARM_WORKERS": "0",
            "PYTHON_PROTOCOL_MAX_WORKERS": "2",
            "PYTHON_PROTOCOL_IDLE_TIMEOUT_SECONDS": "600",
            "PYTHON_PROTOCOL_TASK_TIMEOUT_SECONDS": "60",
            "PYTHON_PROTOCOL_ACQUIRE_TIMEOUT_SECONDS": "10",
            "PYTHON_PROTOCOL_MAX_TASKS_PER_WORKER": "1",
            "PYTHON_PROTOCOL_REAPER_INTERVAL_SECONDS": "1",
        }
    )

    temp_dir = Path(tempfile.mkdtemp(prefix="python-protocol-manager-smoke-"))
    stdout_path = temp_dir / "server.stdout.log"
    stderr_path = temp_dir / "server.stderr.log"

    with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open("w", encoding="utf-8") as stderr_handle:
        process = subprocess.Popen(
            [sys.executable, str(server_path)],
            cwd=str(repo_root),
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
        )

        try:
            health_before = wait_for_health(base_url, timeout_seconds=args.timeout_seconds)
            capabilities = request_json(f"{base_url}/capabilities")

            pool_before = health_before.get("pool") or {}
            expect(pool_before.get("mode") == "dynamic_process_pool", "health pool mode mismatch")
            expect(int(pool_before.get("minWarmWorkers", -1)) == 0, "min warm worker count mismatch")
            expect(int(pool_before.get("maxWorkers", -1)) == 2, "max worker count mismatch")
            expect(int(pool_before.get("totalWorkers", -1)) == 0, "expected zero pre-warmed workers")

            capabilities_pool = capabilities.get("pool") or {}
            expect(int(capabilities_pool.get("maxWorkers", -1)) == 2, "capabilities pool stats missing")

            payload = {
                "request_id": "pool-smoke-01",
                "operation": "codex.semantic.step",
                "payload": {
                    "step_type": "worker_runtime_probe",
                    "step_input": {"label": "pool-smoke"},
                },
            }
            first = request_json(f"{base_url}/invoke", method="POST", payload=payload)
            second = request_json(
                f"{base_url}/invoke",
                method="POST",
                payload={
                    **payload,
                    "request_id": "pool-smoke-02",
                },
            )

            expect(
                str(first.get("status") or "") == "succeeded",
                f"first step should succeed inside worker pool: {json.dumps(first, ensure_ascii=False)}",
            )
            expect(
                str(second.get("status") or "") == "succeeded",
                f"second step should succeed inside worker pool: {json.dumps(second, ensure_ascii=False)}",
            )

            first_result = ((first.get("result") or {}).get("step_result") or {}) if isinstance(first, dict) else {}
            second_result = ((second.get("result") or {}).get("step_result") or {}) if isinstance(second, dict) else {}
            worker_id_1 = str(first_result.get("workerId") or "").strip()
            worker_id_2 = str(second_result.get("workerId") or "").strip()
            expect(bool(worker_id_1), "first worker id missing from step result")
            expect(bool(worker_id_2), "second worker id missing from step result")
            expect(worker_id_1 != worker_id_2, "expected worker recycle after max_tasks_per_worker=1")

            health_after = request_json(f"{base_url}/health")
            pool_after = health_after.get("pool") or {}
            expect(int(pool_after.get("totalWorkers", -1)) == 0, "expected retired workers to drain back to zero")

            print(
                json.dumps(
                    {
                        "baseUrl": base_url,
                        "poolBefore": pool_before,
                        "poolAfter": pool_after,
                        "workerIds": [worker_id_1, worker_id_2],
                    },
                    ensure_ascii=False,
                )
            )
        except Exception as exc:
            diagnostics = "\n\n".join(
                [
                    format_logs("server.stdout", stdout_path),
                    format_logs("server.stderr", stderr_path),
                ]
            )
            raise RuntimeError(f"{exc}\n\n{diagnostics}") from exc
        finally:
            try:
                process.terminate()
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
