import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


_CURRENT_DIR = Path(__file__).resolve().parent
if str(_CURRENT_DIR) not in sys.path:
    sys.path.append(str(_CURRENT_DIR))

from worker_pool import (
    StepExecutionPool,
    WorkerAcquireTimeout,
    WorkerExecutionTimeout,
    WorkerPoolError,
)


HOST = os.getenv("PYTHON_PROTOCOL_HOST", "127.0.0.1")
PORT = int(os.getenv("PYTHON_PROTOCOL_PORT", "11003"))
CAPABILITIES = [
    "health.inspect",
    "protocol.echo",
    "protocol.regex.extract",
    "protocol.text.slugify",
    "protocol.data.flatten",
    "codex.semantic.step",
]

STEP_POOL: StepExecutionPool | None = None


def build_result(parsed, extra):
    result = {
        "language": "python",
        "operation": parsed.get("operation", ""),
        "mode": parsed.get("mode", ""),
    }
    result.update(extra)
    return result


def flatten_value(value, prefix="", out=None):
    if out is None:
        out = {}
    if isinstance(value, dict):
        for key, nested in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            flatten_value(nested, next_prefix, out)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            next_prefix = f"{prefix}.{index}" if prefix else str(index)
            flatten_value(nested, next_prefix, out)
    else:
        out[prefix] = value
    return out


def execute_operation(parsed):
    payload = parsed.get("payload") or {}
    operation = parsed.get("operation", "")

    if operation == "health.inspect":
        pool_snapshot = STEP_POOL.snapshot() if STEP_POOL is not None else {}
        return {
            "result": build_result(parsed, {
                "service": "PythonProtocol",
                "status": "ok",
                "listen": f"{HOST}:{PORT}",
                "pool": pool_snapshot,
            })
        }

    if operation == "protocol.echo":
        return {
            "result": build_result(parsed, {
                "echo": payload
            })
        }

    if operation == "protocol.regex.extract":
        pattern = payload.get("pattern")
        text = payload.get("text")
        if not isinstance(pattern, str) or not isinstance(text, str):
            return {
                "error": {
                    "category": "validation_error",
                    "message": "payload.pattern and payload.text must be strings"
                }
            }
        try:
            matches = re.findall(pattern, text)
        except re.error as exc:
            return {
                "error": {
                    "category": "validation_error",
                    "message": f"invalid regex pattern: {exc}"
                }
            }
        return {
            "result": build_result(parsed, {
                "matches": matches
            })
        }

    if operation == "protocol.text.slugify":
        text = payload.get("text")
        if not isinstance(text, str):
            return {
                "error": {
                    "category": "validation_error",
                    "message": "payload.text must be a string"
                }
            }
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        return {
            "result": build_result(parsed, {
                "slug": slug
            })
        }

    if operation == "protocol.data.flatten":
        if "input" not in payload:
            return {
                "error": {
                    "category": "validation_error",
                    "message": "payload.input is required"
                }
            }
        return {
            "result": build_result(parsed, {
                "flattened": flatten_value(payload.get("input"))
            })
        }

    if operation == "codex.semantic.step":
        step_type = payload.get("step_type")
        step_input = payload.get("step_input")
        if not isinstance(step_type, str) or not step_type.strip():
            return {
                "error": {
                    "category": "validation_error",
                    "message": "payload.step_type must be a non-empty string",
                }
            }
        if step_input is None:
            step_input = {}
        if not isinstance(step_input, dict):
            return {
                "error": {
                    "category": "validation_error",
                    "message": "payload.step_input must be an object",
                }
            }
        if STEP_POOL is None:
            return {
                "error": {
                    "category": "service_runtime_error",
                    "message": "execution pool is not initialized",
                }
            }
        try:
            result = STEP_POOL.execute_step(
                step_type=step_type.strip(),
                step_input=step_input,
            )
            if "_error" in result:
                return {"error": result["_error"]}
        except WorkerAcquireTimeout as exc:
            return {
                "error": {
                    "category": "service_unavailable",
                    "message": str(exc),
                    "details": {
                        "operation": operation,
                        "step_type": step_type.strip(),
                    },
                }
            }
        except WorkerExecutionTimeout as exc:
            return {
                "error": {
                    "category": "timeout_error",
                    "message": str(exc),
                    "details": {
                        "operation": operation,
                        "step_type": step_type.strip(),
                    },
                }
            }
        except WorkerPoolError as exc:
            return {
                "error": {
                    "category": "service_runtime_error",
                    "message": str(exc),
                    "details": {
                        "operation": operation,
                        "step_type": step_type.strip(),
                    },
                }
            }
        except RuntimeError as exc:
            return {
                "error": {
                    "category": "operation_error",
                    "message": str(exc),
                    "details": {
                        "operation": operation,
                        "step_type": step_type.strip(),
                    },
                }
            }
        except Exception as exc:
            return {
                "error": {
                    "category": "service_runtime_error",
                    "message": str(exc),
                    "details": {
                        "operation": operation,
                        "step_type": step_type.strip(),
                    },
                }
            }
        return {
            "result": build_result(parsed, {
                "step_type": step_type.strip(),
                "step_result": result,
            })
        }

    return {
        "error": {
            "category": "unsupported_operation",
            "message": "service does not support operation",
            "details": {
                "operation": operation
            }
        }
    }


class Handler(BaseHTTPRequestHandler):
    def _write_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _write_failure(self, status, request_id, error):
        self._write_json(status, {
            "request_id": request_id or "",
            "service": "PythonProtocol",
            "status": "failed",
            "error": error
        })

    def do_GET(self):
        if self.path == "/health":
            pool_snapshot = STEP_POOL.snapshot() if STEP_POOL is not None else {}
            self._write_json(200, {
                "service": "PythonProtocol",
                "status": "ok",
                "listen": f"{HOST}:{PORT}",
                "pool": pool_snapshot,
            })
            return

        if self.path == "/capabilities":
            self._write_json(200, {
                "service": "PythonProtocol",
                "language": "python",
                "operations": CAPABILITIES,
                "pool": STEP_POOL.snapshot() if STEP_POOL is not None else {},
            })
            return

        self._write_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/invoke":
            self._write_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            self._write_failure(400, "", {
                "category": "validation_error",
                "message": "invalid request body"
            })
            return

        outcome = execute_operation(parsed)
        if "error" in outcome:
            self._write_failure(200, parsed.get("request_id", ""), outcome["error"])
            return

        self._write_json(200, {
            "request_id": parsed.get("request_id", ""),
            "service": "PythonProtocol",
            "status": "succeeded",
            "result": outcome["result"]
        })

    def log_message(self, format, *args):
        return


class Server(ThreadingHTTPServer):
    daemon_threads = True


def main():
    global STEP_POOL
    STEP_POOL = StepExecutionPool()
    server = Server((HOST, PORT), Handler)
    print(f"PythonProtocol listening on {HOST}:{PORT}")
    try:
        server.serve_forever()
    finally:
        if STEP_POOL is not None:
            STEP_POOL.close()
            STEP_POOL = None


if __name__ == "__main__":
    main()
