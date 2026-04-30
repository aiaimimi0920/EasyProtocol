from __future__ import annotations

from pathlib import Path


FIRST_PHONE_DIRNAME = "first_phone"
SMALL_SUCCESS_DIRNAME = "small_success"
SUCCESS_DIRNAME = "success"
REGISTER_PROTOCOL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTER_PROTOCOL_OUTPUT_DIR = str(REGISTER_PROTOCOL_ROOT)


def resolve_output_root(output_dir: str | None = None) -> Path:
    if str(output_dir or "").strip():
        return Path(str(output_dir)).resolve()
    return REGISTER_PROTOCOL_ROOT


def resolve_first_phone_dir(output_dir: str | None = None) -> Path:
    return resolve_output_root(output_dir) / FIRST_PHONE_DIRNAME


def resolve_small_success_dir(output_dir: str | None = None) -> Path:
    return resolve_output_root(output_dir) / SMALL_SUCCESS_DIRNAME


def resolve_success_dir(output_dir: str | None = None) -> Path:
    return resolve_output_root(output_dir) / SUCCESS_DIRNAME
