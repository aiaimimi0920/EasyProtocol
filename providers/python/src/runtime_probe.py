from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any


def build_worker_runtime_probe(step_input: dict[str, Any]) -> dict[str, Any]:
    sleep_seconds = 0.0
    try:
        if "sleep_seconds" in (step_input or {}):
            sleep_seconds = max(0.0, float(step_input.get("sleep_seconds") or 0.0))
        elif "sleep_ms" in (step_input or {}):
            sleep_seconds = max(0.0, float(step_input.get("sleep_ms") or 0.0) / 1000.0)
    except Exception:
        sleep_seconds = 0.0
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)
    return {
        "ok": True,
        "status": "completed",
        "workerId": str(os.getenv("PYTHON_PROTOCOL_WORKER_ID") or "").strip(),
        "workerLaunchedAt": str(os.getenv("PYTHON_PROTOCOL_WORKER_LAUNCHED_AT") or "").strip(),
        "pid": os.getpid(),
        "sleptSeconds": sleep_seconds,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "echo": dict(step_input or {}),
    }
