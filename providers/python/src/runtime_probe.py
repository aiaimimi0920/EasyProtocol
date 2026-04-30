from __future__ import annotations

import os
from datetime import datetime
from typing import Any


def build_worker_runtime_probe(step_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "completed",
        "workerId": str(os.getenv("PYTHON_PROTOCOL_WORKER_ID") or "").strip(),
        "workerLaunchedAt": str(os.getenv("PYTHON_PROTOCOL_WORKER_LAUNCHED_AT") or "").strip(),
        "pid": os.getpid(),
        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "echo": dict(step_input or {}),
    }
