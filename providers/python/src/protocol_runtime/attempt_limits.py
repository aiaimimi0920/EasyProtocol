from __future__ import annotations

import os


RETRY_MAX_ATTEMPTS_ENV = "PROTOCOL_RETRY_MAX_ATTEMPTS"


def bounded_attempts(default: int) -> int:
    """Only tighten existing retry limits; reject invalid explicit configuration."""
    if default < 1:
        raise ValueError("invalid_default_attempt_limit")
    raw = str(os.environ.get(RETRY_MAX_ATTEMPTS_ENV) or "").strip()
    if not raw:
        return default
    if not raw.isascii() or not raw.isdecimal() or int(raw) < 1:
        raise ValueError("invalid_protocol_retry_max_attempts")
    return min(default, int(raw))
