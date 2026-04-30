from __future__ import annotations

from .models import PLATFORM_LOGIN_URL, PlatformProtocolRegistrationResult, ProtocolOAuthResult, SecondOAuthResult
from .paths import (
    DEFAULT_REGISTER_PROTOCOL_OUTPUT_DIR,
    FIRST_PHONE_DIRNAME,
    REGISTER_PROTOCOL_ROOT,
    SMALL_SUCCESS_DIRNAME,
    SUCCESS_DIRNAME,
    resolve_first_phone_dir,
    resolve_output_root,
    resolve_small_success_dir,
    resolve_success_dir,
)

__all__ = [
    "DEFAULT_REGISTER_PROTOCOL_OUTPUT_DIR",
    "FIRST_PHONE_DIRNAME",
    "REGISTER_PROTOCOL_ROOT",
    "PLATFORM_LOGIN_URL",
    "PlatformProtocolRegistrationResult",
    "ProtocolOAuthResult",
    "SMALL_SUCCESS_DIRNAME",
    "SecondOAuthResult",
    "SUCCESS_DIRNAME",
    "resolve_first_phone_dir",
    "resolve_output_root",
    "resolve_small_success_dir",
    "resolve_success_dir",
]
