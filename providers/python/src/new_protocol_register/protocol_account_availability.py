from __future__ import annotations

import json
import os
import tempfile
import uuid
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    import sys

    _CURRENT_DIR = Path(__file__).resolve().parent
    _SRC_DIR = _CURRENT_DIR.parent
    _PYTHON_SHARED_SRC = _CURRENT_DIR.parents[2] / "python_shared" / "src"
    for _candidate in (_CURRENT_DIR, _SRC_DIR, _PYTHON_SHARED_SRC):
        candidate_text = str(_candidate)
        if candidate_text not in sys.path:
            sys.path.append(candidate_text)
    from others.bootstrap import ensure_local_bundle_imports

    ensure_local_bundle_imports()
    from others.runtime import ensure_easy_email_env_defaults
    from others.storage import load_json_payload
else:
    from .others.bootstrap import ensure_local_bundle_imports

    ensure_local_bundle_imports()
    from .others.runtime import ensure_easy_email_env_defaults
    from .others.storage import load_json_payload

from shared_mailbox.easy_email_client import recover_mailbox_by_email

if __package__ in (None, ""):
    from magic import _perform_refresh_token_exchange, _resolve_refresh_client_id
    from protocol_chatgpt_login import run_protocol_chatgpt_login_init_from_path
    from protocol_easybrowser_login import run_easybrowser_openai_web_login_flow
else:
    from .magic import _perform_refresh_token_exchange, _resolve_refresh_client_id
    from .protocol_chatgpt_login import run_protocol_chatgpt_login_init_from_path
    from .protocol_easybrowser_login import run_easybrowser_openai_web_login_flow


DEFAULT_CHATGPT_URL = "https://chatgpt.com/"
DEFAULT_ACCOUNT_AUDIT_TIMEOUT_SECONDS = 1500.0
DELETED_MARKERS = (
    "account_deactivated",
    "account has been deleted or deactivated",
    "deleted or deactivated",
    "account_deleted",
    "account_disabled",
)


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def _as_positive_float(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _resolve_account_audit_timeout_seconds(value: Any = None) -> float:
    explicit = _as_positive_float(value)
    if explicit is not None:
        return explicit
    env_value = _as_positive_float(os.environ.get("PYTHON_PROTOCOL_ACCOUNT_AUDIT_TIMEOUT_SECONDS"))
    if env_value is not None:
        return env_value
    return DEFAULT_ACCOUNT_AUDIT_TIMEOUT_SECONDS


def _remaining_account_audit_seconds(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return deadline - time.monotonic()


def _timeout_result(
    *,
    target: dict[str, Any],
    email: str,
    detail: str = "account_audit_timeout_exceeded",
) -> dict[str, Any]:
    return _result_payload(
        target=target,
        email=email,
        status="inconclusive",
        detail=detail,
        browser={
            "ok": False,
            "status": "timeout",
            "detail": detail,
        },
    )


def _nested_map(value: Any, *path: str) -> dict[str, Any]:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _extract_email(*, target: dict[str, Any], payload: dict[str, Any]) -> str:
    profile_claims = payload.get("https://api.openai.com/profile")
    profile_email = profile_claims.get("email") if isinstance(profile_claims, dict) else ""
    return _first_text(
        target.get("email"),
        payload.get("email"),
        payload.get("account_email"),
        payload.get("mailboxEmail"),
        profile_email,
    )


def _extract_password(payload: dict[str, Any]) -> str:
    return _first_text(payload.get("password"), payload.get("account_password"))


def _extract_refresh_token(payload: dict[str, Any]) -> str:
    login_details = payload.get("chatgptLoginDetails") if isinstance(payload.get("chatgptLoginDetails"), dict) else {}
    oauth_tokens = login_details.get("oauthTokens") if isinstance(login_details.get("oauthTokens"), dict) else {}
    protocol_oauth = _nested_map(payload, "teamFlow", "protocolOAuth")
    return _first_text(
        payload.get("refresh_token"),
        payload.get("refreshToken"),
        login_details.get("refresh_token"),
        login_details.get("refreshToken"),
        oauth_tokens.get("refresh_token"),
        oauth_tokens.get("refreshToken"),
        protocol_oauth.get("refresh_token"),
        protocol_oauth.get("refreshToken"),
    )


def _extract_access_token_for_refresh(payload: dict[str, Any]) -> str:
    login_details = payload.get("chatgptLoginDetails") if isinstance(payload.get("chatgptLoginDetails"), dict) else {}
    oauth_tokens = login_details.get("oauthTokens") if isinstance(login_details.get("oauthTokens"), dict) else {}
    client_bootstrap = login_details.get("clientBootstrap") if isinstance(login_details.get("clientBootstrap"), dict) else {}
    return _first_text(
        payload.get("access_token"),
        payload.get("accessToken"),
        login_details.get("access_token"),
        login_details.get("accessToken"),
        oauth_tokens.get("access_token"),
        oauth_tokens.get("accessToken"),
        client_bootstrap.get("accessToken"),
    )


def _extract_id_token_for_refresh(payload: dict[str, Any]) -> str:
    login_details = payload.get("chatgptLoginDetails") if isinstance(payload.get("chatgptLoginDetails"), dict) else {}
    oauth_tokens = login_details.get("oauthTokens") if isinstance(login_details.get("oauthTokens"), dict) else {}
    return _first_text(
        payload.get("id_token"),
        payload.get("idToken"),
        login_details.get("id_token"),
        login_details.get("idToken"),
        oauth_tokens.get("id_token"),
        oauth_tokens.get("idToken"),
    )


def _terminal_status_from_payload(payload: dict[str, Any]) -> tuple[str, str] | None:
    if _as_bool(payload.get("disabled")):
        return "deleted_confirmed", "payload_disabled"
    if _as_bool(payload.get("auth_maintenance_pending_delete")):
        reason = _first_text(payload.get("auth_maintenance_delete_reason"))
        detail = f"payload_pending_delete:{reason}" if reason else "payload_pending_delete"
        return "deleted_confirmed", detail
    return None


def _extract_recovery_data_credential(*, target: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    value = (
        target.get("recovery_data_credential")
        or target.get("recoveryDataCredential")
        or payload.get("recoveryDataCredential")
        or payload.get("recovery_data_credential")
        or payload.get("mailboxRecoveryDataCredential")
        or payload.get("mailbox_recovery_data_credential")
    )
    return dict(value) if isinstance(value, dict) else {}


def _provider_from_mailbox_ref(mailbox_ref: str) -> str:
    text = str(mailbox_ref or "").strip()
    if ":" not in text:
        return ""
    return text.split(":", 1)[0].strip().lower()


def _mailbox_context_from_recovery(*, email: str, target: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    recovery_data = _extract_recovery_data_credential(target=target, payload=payload)
    mailbox_ref = _first_text(
        target.get("mailbox_ref"),
        target.get("mailboxRef"),
        payload.get("mailboxRef"),
        payload.get("mailbox_ref"),
        payload.get("mailboxAccessKey"),
    )
    mailbox_session_id = _first_text(
        target.get("mailbox_session_id"),
        target.get("mailboxSessionId"),
        payload.get("mailboxSessionId"),
        payload.get("mailbox_session_id"),
        payload.get("session_id"),
        payload.get("sessionId"),
    )
    provider = _first_text(
        recovery_data.get("providerTypeKey"),
        recovery_data.get("provider_type_key"),
        target.get("mailbox_provider"),
        target.get("mailboxProvider"),
        payload.get("mailboxProvider"),
        payload.get("mailbox_provider"),
        payload.get("providerTypeKey"),
        _provider_from_mailbox_ref(mailbox_ref),
    )
    return {
        "mailbox_ref": mailbox_ref,
        "mailbox_session_id": mailbox_session_id,
        "provider": provider,
        "host_id": _first_text(
            recovery_data.get("hostId"),
            recovery_data.get("host_id"),
            recovery_data.get("providerInstanceId"),
            recovery_data.get("provider_instance_id"),
        ),
        "recovery_data_credential": recovery_data,
        "email": email,
    }


def _recover_mailbox_if_requested(
    *,
    mailbox_context: dict[str, Any],
    recover_mailbox: bool,
) -> dict[str, Any]:
    if not recover_mailbox:
        return mailbox_context
    email = _first_text(mailbox_context.get("email"))
    if not email:
        return mailbox_context
    ensure_easy_email_env_defaults()
    try:
        recovery = recover_mailbox_by_email(
            email_address=email,
            provider_type_key=_first_text(mailbox_context.get("provider")),
            host_id=_first_text(mailbox_context.get("host_id")),
            recovery_data_credential=mailbox_context.get("recovery_data_credential")
            if isinstance(mailbox_context.get("recovery_data_credential"), dict)
            else None,
        )
    except Exception as exc:
        updated = dict(mailbox_context)
        updated["mailbox_recovery"] = {
            "recovered": False,
            "detail": str(exc),
        }
        return updated
    updated = dict(mailbox_context)
    updated["mailbox_recovery"] = recovery if isinstance(recovery, dict) else {}
    if isinstance(recovery, dict) and recovery.get("recovered"):
        session = recovery.get("session") if isinstance(recovery.get("session"), dict) else {}
        session_id = _first_text(session.get("id"), session.get("sessionId"))
        mailbox_ref = _first_text(session.get("mailboxRef"), session.get("mailbox_ref"))
        if session_id:
            updated["mailbox_session_id"] = session_id
        if mailbox_ref:
            updated["mailbox_ref"] = mailbox_ref
        provider = _first_text(session.get("providerTypeKey"), session.get("provider_type_key"))
        if provider:
            updated["provider"] = provider
    return updated


def _result_payload(
    *,
    target: dict[str, Any],
    email: str,
    status: str,
    detail: str = "",
    browser: dict[str, Any] | None = None,
    mailbox_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    browser_result = browser if isinstance(browser, dict) else {}
    mailbox = mailbox_context if isinstance(mailbox_context, dict) else {}
    return {
        "target_id": _first_text(target.get("target_id"), target.get("targetId")),
        "source_path": _first_text(target.get("source_path"), target.get("sourcePath")),
        "original_path": _first_text(target.get("original_path"), target.get("originalPath")),
        "email": email,
        "status": status,
        "detail": detail,
        "target_url": _first_text(browser_result.get("target_url"), browser_result.get("targetUrl")),
        "mailbox_ref": _first_text(mailbox.get("mailbox_ref")),
        "mailbox_session_id": _first_text(mailbox.get("mailbox_session_id")),
        "mailbox_recovery": mailbox.get("mailbox_recovery") if isinstance(mailbox.get("mailbox_recovery"), dict) else {},
        "browser": browser_result,
    }


def _looks_deleted(*, status: str, detail: str, browser: dict[str, Any] | None = None) -> bool:
    values = [status, detail]
    if isinstance(browser, dict):
        values.extend(
            [
                str(browser.get("status") or ""),
                str(browser.get("detail") or ""),
                str(browser.get("target_url") or browser.get("targetUrl") or ""),
                str(_nested_map(browser, "error").get("code") or ""),
                str(_nested_map(browser, "error").get("message") or ""),
            ]
        )
    joined = " ".join(values).lower()
    return any(marker in joined for marker in DELETED_MARKERS)


def _classify_browser_result(browser: dict[str, Any]) -> tuple[str, str]:
    status = _first_text(browser.get("status"), browser.get("outcome"))
    detail = _first_text(browser.get("detail"), browser.get("message"))
    if bool(browser.get("ok")):
        return "login_succeeded", detail or "standard_login_completed"
    if _looks_deleted(status=status, detail=detail, browser=browser):
        return "deleted_confirmed", detail or status or "account_deactivated"
    return "inconclusive", detail or status or "login_result_not_terminal"


def _build_refresh_validation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    access_token = _extract_access_token_for_refresh(payload)
    id_token = _extract_id_token_for_refresh(payload)
    if access_token and not _first_text(normalized.get("access_token")):
        normalized["access_token"] = access_token
    if id_token and not _first_text(normalized.get("id_token")):
        normalized["id_token"] = id_token
    client_id = _first_text(normalized.get("client_id"), normalized.get("oauthClientId"))
    if client_id and not _first_text(normalized.get("client_id")):
        normalized["client_id"] = client_id
    return normalized


def _try_refresh_validation(
    *,
    payload: dict[str, Any],
    explicit_proxy: str | None,
) -> tuple[str, str, dict[str, Any]] | None:
    refresh_token = _extract_refresh_token(payload)
    if not refresh_token:
        return None
    refresh_payload = _build_refresh_validation_payload(payload)
    client_id = _resolve_refresh_client_id(refresh_payload)
    token_payload = _perform_refresh_token_exchange(
        refresh_token=refresh_token,
        client_id=client_id,
        explicit_proxy=explicit_proxy,
    )
    return "login_succeeded", "refresh_token_valid", token_payload if isinstance(token_payload, dict) else {}


def _run_http_login_validation(
    *,
    source_path: Path,
    payload: dict[str, Any],
    mailbox_context: dict[str, Any],
    explicit_proxy: str | None,
    timeout_seconds: float | None = None,
) -> tuple[str, str, dict[str, Any]]:
    temp_path = Path(tempfile.gettempdir()) / f"account-audit-{uuid.uuid4().hex[:8]}-{source_path.name}"
    temp_payload = dict(payload)
    temp_payload.pop("chatgptLogin", None)
    temp_payload.pop("chatgptLoginDetails", None)
    temp_path.write_text(json.dumps(temp_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        result = run_protocol_chatgpt_login_init_from_path(
            source_path=temp_path,
            explicit_proxy=explicit_proxy,
            mailbox_ref=_first_text(mailbox_context.get("mailbox_ref")) or None,
            mailbox_session_id=_first_text(mailbox_context.get("mailbox_session_id")) or None,
            timeout_seconds=timeout_seconds,
        )
        return "login_succeeded", "http_login_succeeded", result if isinstance(result, dict) else {}
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def run_protocol_account_availability_audit(
    *,
    targets: Any,
    explicit_proxy: str | None = None,
    login_entry_url: str | None = None,
    recover_mailbox: bool = True,
    timeout_seconds: Any = None,
) -> dict[str, Any]:
    if not isinstance(targets, list):
        return {
            "ok": False,
            "status": "account_audit_targets_invalid",
            "detail": "targets must be a list",
            "results": [],
        }
    startup_url = str(login_entry_url or "").strip() or DEFAULT_CHATGPT_URL
    audit_timeout_seconds = _resolve_account_audit_timeout_seconds(timeout_seconds)
    deadline = time.monotonic() + audit_timeout_seconds if audit_timeout_seconds > 0 else None
    results: list[dict[str, Any]] = []
    counts = {
        "login_succeeded": 0,
        "deleted_confirmed": 0,
        "inconclusive": 0,
    }
    for target in targets:
        if not isinstance(target, dict):
            continue
        source_path_text = _first_text(target.get("source_path"), target.get("sourcePath"))
        browser: dict[str, Any] = {}
        try:
            payload = load_json_payload(Path(source_path_text).resolve())
            if not isinstance(payload, dict):
                raise RuntimeError("account_payload_not_object")
        except Exception as exc:
            result = _result_payload(
                target=target,
                email=_first_text(target.get("email")),
                status="inconclusive",
                detail=f"account_payload_load_failed:{exc}",
            )
            results.append(result)
            counts["inconclusive"] += 1
            continue
        email = _extract_email(target=target, payload=payload)
        if not email:
            result = _result_payload(
                target=target,
                email="",
                status="inconclusive",
                detail="account_email_missing",
            )
            results.append(result)
            counts["inconclusive"] += 1
            continue
        remaining_before_target = _remaining_account_audit_seconds(deadline)
        if remaining_before_target is not None and remaining_before_target <= 0:
            results.append(_timeout_result(target=target, email=email))
            counts["inconclusive"] += 1
            continue
        payload_terminal = _terminal_status_from_payload(payload)
        if payload_terminal is not None:
            status, detail = payload_terminal
            results.append(
                _result_payload(
                    target=target,
                    email=email,
                    status=status,
                    detail=detail,
                )
            )
            counts[status] = int(counts.get(status, 0)) + 1
            continue

        try:
            refresh_validation = _try_refresh_validation(
                payload=payload,
                explicit_proxy=explicit_proxy,
            )
        except Exception as exc:
            detail = str(exc)
            if _looks_deleted(status="", detail=detail):
                results.append(
                    _result_payload(
                        target=target,
                        email=email,
                        status="deleted_confirmed",
                        detail=detail,
                    )
                )
                counts["deleted_confirmed"] += 1
                continue
            refresh_validation = None
        if refresh_validation is not None:
            status, detail, refresh_result = refresh_validation
            browser = {
                "ok": True,
                "status": "refresh_token_valid",
                "detail": detail,
                "refresh": refresh_result,
            }
            results.append(
                _result_payload(
                    target=target,
                    email=email,
                    status=status,
                    detail=detail,
                    browser=browser,
                )
            )
            counts[status] = int(counts.get(status, 0)) + 1
            continue

        mailbox_context = _mailbox_context_from_recovery(email=email, target=target, payload=payload)
        mailbox_context = _recover_mailbox_if_requested(
            mailbox_context=mailbox_context,
            recover_mailbox=recover_mailbox,
        )
        if not _first_text(mailbox_context.get("mailbox_ref")) and not _first_text(
            mailbox_context.get("mailbox_session_id")
        ):
            result = _result_payload(
                target=target,
                email=email,
                status="inconclusive",
                detail="mailbox_recovery_unavailable",
                mailbox_context=mailbox_context,
            )
            results.append(result)
            counts["inconclusive"] += 1
            continue
        remaining_before_login = _remaining_account_audit_seconds(deadline)
        if remaining_before_login is not None and remaining_before_login <= 0:
            results.append(_timeout_result(target=target, email=email))
            counts["inconclusive"] += 1
            continue
        try:
            status, detail, protocol_result = _run_http_login_validation(
                source_path=Path(source_path_text).resolve(),
                payload=payload,
                mailbox_context=mailbox_context,
                explicit_proxy=explicit_proxy,
                timeout_seconds=remaining_before_login,
            )
            browser = {
                "ok": status == "login_succeeded",
                "status": status,
                "detail": detail,
                "protocol": protocol_result,
                "target_url": _first_text(protocol_result.get("finalUrl"), protocol_result.get("final_url")),
            }
        except Exception as exc:
            detail = str(exc)
            status = "deleted_confirmed" if _looks_deleted(status="", detail=detail) else "inconclusive"
            browser = {
                "ok": False,
                "status": "exception",
                "detail": detail,
            }
        results.append(
            _result_payload(
                target=target,
                email=email,
                status=status,
                detail=detail,
                browser=browser,
                mailbox_context=mailbox_context,
            )
        )
        counts[status] = int(counts.get(status, 0)) + 1

    return {
        "ok": True,
        "status": "completed",
        "audited_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target_count": len(targets),
        "result_count": len(results),
        "counts": counts,
        "results": results,
    }
