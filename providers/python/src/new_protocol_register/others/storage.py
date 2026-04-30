from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .paths import resolve_first_phone_dir, resolve_small_success_dir, resolve_success_dir


def load_json_payload(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("invalid_json_payload")
    return payload


def persist_first_phone_record(
    *,
    output_dir: str | None,
    outcome: str = "phone_wall",
    email: str,
    password: str,
    mailbox_provider: str,
    mailbox_access_key: str,
    mailbox_ref: str,
    mailbox_session_id: str,
    first_name: str,
    last_name: str,
    birthdate: str,
    page_type: str,
    final_url: str,
) -> str:
    target_dir = resolve_first_phone_dir(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / _build_filename(email=email, prefix="")
    payload = {
        "outcome": str(outcome or "").strip() or "phone_wall",
        "site": "platform.openai.com",
        "registrationMode": "protocol-platform-first",
        "email": str(email or ""),
        "password": str(password or ""),
        "emailServiceProvider": "EasyEmail",
        "mailboxProvider": str(mailbox_provider or ""),
        "mailboxAccessKey": str(mailbox_access_key or ""),
        "mailboxRef": str(mailbox_ref or ""),
        "mailboxSessionId": str(mailbox_session_id or ""),
        "firstName": str(first_name or ""),
        "lastName": str(last_name or ""),
        "birthdate": str(birthdate or ""),
        "pageType": str(page_type or ""),
        "finalUrl": str(final_url or ""),
        "createdAt": _utc_now_text(),
    }
    file_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(file_path)


def persist_success_auth_json(
    *,
    output_dir: str | None,
    email: str,
    auth_obj: Any,
) -> str:
    target_dir = resolve_success_dir(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / _build_filename(email=email, prefix="codex")
    payload = _normalize_json_payload(auth_obj)
    file_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(file_path)


def persist_small_success_record(
    *,
    output_dir: str | None,
    outcome: str = "small_success",
    email: str,
    password: str,
    mailbox_provider: str,
    mailbox_access_key: str,
    mailbox_ref: str,
    mailbox_session_id: str,
    first_name: str,
    last_name: str,
    birthdate: str,
    page_type: str,
    final_url: str,
    browser_backend: str = "",
    source: str = "browser_flow",
    registration_mode: str = "browser-platform-first",
    extra_payload: dict[str, Any] | None = None,
) -> str:
    target_dir = resolve_small_success_dir(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / _build_filename(email=email, prefix="small")
    payload = {
        "outcome": str(outcome or "").strip() or "small_success",
        "site": "platform.openai.com",
        "registrationMode": str(registration_mode or "").strip() or "browser-platform-first",
        "source": str(source or "").strip() or "browser_flow",
        "email": str(email or ""),
        "password": str(password or ""),
        "emailServiceProvider": "EasyEmail",
        "mailboxProvider": str(mailbox_provider or ""),
        "mailboxAccessKey": str(mailbox_access_key or ""),
        "mailboxRef": str(mailbox_ref or ""),
        "mailboxSessionId": str(mailbox_session_id or ""),
        "firstName": str(first_name or ""),
        "lastName": str(last_name or ""),
        "birthdate": str(birthdate or ""),
        "pageType": str(page_type or ""),
        "finalUrl": str(final_url or ""),
        "browserBackend": str(browser_backend or ""),
        "createdAt": _utc_now_text(),
    }
    if isinstance(extra_payload, dict) and extra_payload:
        payload.update(extra_payload)
    file_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(file_path)


def _normalize_json_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return ""
        try:
            return json.loads(text)
        except Exception:
            return payload
    return payload


def _build_filename(*, email: str, prefix: str) -> str:
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    suffix = uuid.uuid4().hex[:6]
    safe_email = _safe_filename_fragment(email, default="unknown")
    normalized_prefix = f"{prefix}-" if str(prefix or "").strip() else ""
    return f"{normalized_prefix}{timestamp}-{safe_email}-{suffix}.json"


def _safe_filename_fragment(value: str, *, default: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9@._+-]+", "_", str(value or "").strip())
    normalized = normalized.strip("._-")
    return normalized[:160] or default


def _utc_now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
