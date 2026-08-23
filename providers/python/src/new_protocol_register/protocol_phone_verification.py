from __future__ import annotations

from pathlib import Path
from typing import Any

from protocol_runtime import protocol_register

if __package__ in (None, ""):
    from others.storage import load_json_payload, persist_success_auth_json
else:
    from .others.storage import load_json_payload, persist_success_auth_json


def _extract_user_id(auth_payload: dict[str, Any]) -> str:
    if not isinstance(auth_payload, dict):
        return ""
    nested = auth_payload.get("https://api.openai.com/auth")
    if isinstance(nested, dict):
        for key in ("chatgpt_user_id", "user_id"):
            value = str(nested.get(key) or "").strip()
            if value:
                return value
    for key in ("chatgpt_user_id", "user_id", "member_user_id", "userId"):
        value = str(auth_payload.get(key) or "").strip()
        if value:
            return value
    return ""


def build_phone_verification_required_payload(
    *,
    source_path: str,
    storage_path: str,
    page_type: str,
    final_url: str,
    resume_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "phone_verification_required",
        "phoneVerificationRequired": True,
        "pageType": str(page_type or "").strip() or "add_phone",
        "finalUrl": str(final_url or "").strip(),
        "resumeContext": dict(resume_context or {}),
        "successPath": str(storage_path or "").strip(),
        "sourcePath": str(source_path or "").strip(),
    }


def submit_phone_verification_number_from_path(
    *,
    source_path: str,
    resume_context: dict[str, Any],
    phone_number: str,
    explicit_proxy: str | None = None,
) -> dict[str, Any]:
    payload = load_json_payload(Path(source_path).resolve())
    response = protocol_register.submit_phone_number_for_resume(
        source_payload=payload,
        resume_context=dict(resume_context or {}),
        phone_number=str(phone_number or "").strip(),
        explicit_proxy=explicit_proxy,
    )
    result = {
        "ok": True,
        "status": str(response.get("status") or "").strip() or "phone_number_submitted",
        "pageType": str(response.get("pageType") or "").strip(),
        "resumeContext": dict(response.get("resumeContext") or resume_context or {}),
    }
    for field_name in (
        "phoneVerificationAttempted",
        "phoneVerificationTerminal",
        "phoneVerificationTerminalCode",
        "phoneVerificationTerminalMessage",
        "phoneVerificationTerminalStatusCode",
    ):
        if field_name in response:
            result[field_name] = response.get(field_name)
    return result


def submit_phone_verification_code_from_path(
    *,
    source_path: str,
    resume_context: dict[str, Any],
    sms_code: str,
    explicit_proxy: str | None = None,
) -> dict[str, Any]:
    resolved_source_path = Path(source_path).resolve()
    payload = load_json_payload(resolved_source_path)
    response = protocol_register.submit_phone_verification_code_for_resume(
        source_payload=payload,
        resume_context=dict(resume_context or {}),
        sms_code=str(sms_code or "").strip(),
        explicit_proxy=explicit_proxy,
    )
    auth_payload = dict(response.get("auth") or {})
    email = str(response.get("email") or payload.get("email") or "").strip()
    output_dir = (
        resolved_source_path.parent.parent
        if resolved_source_path.parent.name in {"first_phone", "small_success"}
        else resolved_source_path.parent
    )
    success_path = persist_success_auth_json(
        output_dir=str(output_dir),
        email=email,
        auth_obj=auth_payload,
    )
    return {
        "ok": True,
        "status": "completed",
        "email": email,
        "accountId": str(response.get("accountId") or "").strip(),
        "userId": _extract_user_id(auth_payload),
        "successPath": success_path,
        "auth": auth_payload,
    }
