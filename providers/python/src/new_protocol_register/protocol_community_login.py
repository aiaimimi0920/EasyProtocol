from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

if __package__ in (None, ""):
    import sys

    _CURRENT_DIR = Path(__file__).resolve().parent
    _SRC_DIR = _CURRENT_DIR.parent
    for _candidate in (_CURRENT_DIR, _SRC_DIR):
        candidate_text = str(_candidate)
        if candidate_text not in sys.path:
            sys.path.append(candidate_text)
    from others.bootstrap import ensure_local_bundle_imports

    ensure_local_bundle_imports()
    from others.storage import load_json_payload
else:
    from .others.bootstrap import ensure_local_bundle_imports

    ensure_local_bundle_imports()
    from .others.storage import load_json_payload

from curl_cffi import requests


DEFAULT_COMMUNITY_URL = "https://community.openai.com/"
DEFAULT_EASYBROWSER_DOCKER_BASE_URL = "http://easy-browser:8080"
DEFAULT_BROWSER_PROVIDER_HINT = "chrome"
DEFAULT_BROWSER_BACKEND = "chrome"


def _env_text(name: str, default: str) -> str:
    text = str(os.environ.get(name) or "").strip()
    return text or default


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _nested_map(value: Any, *path: str) -> dict[str, Any]:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _lookup_text(value: Any, *keys: str) -> str:
    if not isinstance(value, dict):
        return ""
    for key in keys:
        text = str(value.get(key) or "").strip()
        if text:
            return text
    return ""


def _default_easybrowser_base_url() -> str:
    configured = _first_text(
        os.environ.get("EASYBROWSER_SERVICE_BASE_URL"),
        os.environ.get("BROWSER_SERVICE_BASE_URL"),
        os.environ.get("EASY_BROWSER_SERVICE_BASE_URL"),
    )
    if configured:
        return configured.rstrip("/")
    if Path("/.dockerenv").exists():
        return DEFAULT_EASYBROWSER_DOCKER_BASE_URL
    return ""


def _is_url_under_prefix(url: str, prefix: str) -> bool:
    candidate = str(url or "").strip()
    expected_prefix = str(prefix or DEFAULT_COMMUNITY_URL).strip() or DEFAULT_COMMUNITY_URL
    if not candidate:
        return False
    candidate_parts = urlsplit(candidate)
    prefix_parts = urlsplit(expected_prefix)
    if not candidate_parts.scheme or not candidate_parts.netloc:
        return False
    if candidate_parts.scheme.lower() != prefix_parts.scheme.lower():
        return False
    if candidate_parts.netloc.lower() != prefix_parts.netloc.lower():
        return False
    prefix_path = prefix_parts.path or "/"
    candidate_path = candidate_parts.path or "/"
    return candidate_path.startswith(prefix_path)


def _post_json(session: requests.Session, base_url: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    response = session.post(urljoin(base_url.rstrip("/") + "/", path.lstrip("/")), json=payload or {}, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"easybrowser_http_error status={response.status_code} path={path} body={response.text[:500]}")
    data = response.json()
    return data if isinstance(data, dict) else {}


def _get_json(session: requests.Session, base_url: str, path: str) -> dict[str, Any]:
    response = session.get(urljoin(base_url.rstrip("/") + "/", path.lstrip("/")), timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"easybrowser_http_error status={response.status_code} path={path} body={response.text[:500]}")
    data = response.json()
    return data if isinstance(data, dict) else {}


def _envelope_data(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("success") is False:
        error = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
        message = str(error.get("message") or envelope.get("message") or "easybrowser_request_failed").strip()
        code = str(error.get("code") or envelope.get("code") or "").strip()
        raise RuntimeError(f"{code}: {message}" if code else message)
    data = envelope.get("data")
    return data if isinstance(data, dict) else {}


def _extract_session_id(acquire_data: dict[str, Any]) -> str:
    session_payload = acquire_data.get("session") if isinstance(acquire_data.get("session"), dict) else {}
    return _lookup_text(session_payload, "session_id", "id")


def _extract_target_url(task_data: dict[str, Any]) -> str:
    result = task_data.get("result") if isinstance(task_data.get("result"), dict) else {}
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    medium_step_results = result.get("medium_step_results") if isinstance(result.get("medium_step_results"), dict) else {}
    login_summary = medium_step_results.get("openai-community-login") if isinstance(medium_step_results.get("openai-community-login"), dict) else {}
    return _first_text(
        _lookup_text(artifacts, "target_url", "url"),
        _lookup_text(login_summary, "target_url", "url"),
        _lookup_text(_nested_map(login_summary, "artifacts"), "target_url", "url"),
    )


def run_easybrowser_openai_web_login_flow(
    *,
    email: str,
    password: str,
    mailbox_ref: str,
    mailbox_session_id: str,
    proxy_url: str,
    startup_url: str,
    easybrowser_base_url: str = "",
    timeout_seconds: int = 420,
    poll_interval_seconds: float = 2.0,
) -> dict[str, Any]:
    base_url = str(easybrowser_base_url or _default_easybrowser_base_url()).strip().rstrip("/")
    if not base_url:
        raise RuntimeError("easybrowser_service_base_url_missing")
    session = requests.Session()
    provider_hint = _env_text("OPENAI_COMMUNITY_BROWSER_PROVIDER_HINT", DEFAULT_BROWSER_PROVIDER_HINT)
    browser_backend = _env_text("OPENAI_COMMUNITY_BROWSER_BACKEND", DEFAULT_BROWSER_BACKEND)
    acquire_payload: dict[str, Any] = {
        "request_id": f"community-login-acquire-{uuid.uuid4().hex[:12]}",
        "mode": "direct",
        "provider_hint": provider_hint,
        "browser_backend": browser_backend,
        "startup_url": startup_url,
        "proxy": proxy_url,
        "session_ttl_seconds": max(600, int(timeout_seconds) + 120),
        "metadata": {
            "caller": "easyprotocol-login-openai-community",
        },
    }
    acquire_data = _envelope_data(_post_json(session, base_url, "/v1/browser/sessions/acquire", acquire_payload))
    session_id = _extract_session_id(acquire_data)
    if not session_id:
        raise RuntimeError("easybrowser_session_id_missing")

    try:
        flow_payload = {
            "request_id": f"community-login-flow-{uuid.uuid4().hex[:12]}",
            "flow_type": "login",
            "timeout_ms": max(1, int(timeout_seconds)) * 1000,
            "metadata": {
                "caller": "easyprotocol-login-openai-community",
            },
            "steps": [
                {
                    "step_type": "openai_web_login",
                    "input": {
                        "startup_url": startup_url,
                        "email": email,
                        "password": password,
                        "mailbox_ref": mailbox_ref,
                        "mailbox_session_id": mailbox_session_id,
                        "auth": {
                            "email": email,
                            "password": password,
                            "mailbox_ref": mailbox_ref,
                            "mailbox_session_id": mailbox_session_id,
                        },
                    },
                    "timeout_ms": max(1, int(timeout_seconds)) * 1000,
                    "metadata": {
                        "id": "openai-community-login",
                    },
                }
            ],
        }
        accepted_data = _envelope_data(
            _post_json(session, base_url, f"/v1/browser/sessions/{session_id}/flows/execute", flow_payload)
        )
        task_id = _lookup_text(accepted_data, "task_id")
        if not task_id:
            raise RuntimeError("easybrowser_flow_task_id_missing")
        deadline = time.time() + max(1, int(timeout_seconds))
        last_task_data: dict[str, Any] = {}
        while time.time() <= deadline:
            task_data = _envelope_data(_get_json(session, base_url, f"/v1/tasks/{task_id}"))
            last_task_data = task_data
            state = str(task_data.get("state") or "").strip().lower()
            if state == "succeeded":
                target_url = _extract_target_url(task_data)
                return {
                    "ok": True,
                    "status": "completed",
                    "session_id": session_id,
                    "task_id": task_id,
                    "target_url": target_url,
                    "result": task_data.get("result") if isinstance(task_data.get("result"), dict) else {},
                }
            if state == "failed":
                error = task_data.get("error") if isinstance(task_data.get("error"), dict) else {}
                return {
                    "ok": False,
                    "status": str(error.get("code") or "easybrowser_flow_failed"),
                    "detail": str(error.get("message") or "EasyBrowser login flow failed"),
                    "session_id": session_id,
                    "task_id": task_id,
                    "target_url": _extract_target_url(task_data),
                    "error": error,
                }
            time.sleep(max(0.1, float(poll_interval_seconds)))
        return {
            "ok": False,
            "status": "easybrowser_flow_timeout",
            "detail": f"timed out waiting for EasyBrowser task {task_id}",
            "session_id": session_id,
            "task_id": task_id,
            "target_url": _extract_target_url(last_task_data),
        }
    finally:
        try:
            _post_json(session, base_url, f"/v1/browser/sessions/{session_id}/release", {})
        except Exception:
            pass


def run_protocol_community_login_from_path(
    *,
    source_path: str,
    explicit_proxy: str | None = None,
    startup_url: str | None = None,
    success_url_prefix: str | None = None,
    mailbox_ref: str | None = None,
    mailbox_session_id: str | None = None,
) -> dict[str, Any]:
    resolved_source_path = Path(str(source_path or "").strip()).resolve()
    payload = load_json_payload(resolved_source_path)
    email = _first_text(payload.get("email"), payload.get("account_email"))
    password = _first_text(payload.get("password"), payload.get("account_password"))
    resolved_mailbox_ref = _first_text(
        mailbox_ref,
        payload.get("mailboxRef"),
        payload.get("mailbox_ref"),
        payload.get("mailboxAccessKey"),
    )
    resolved_mailbox_session_id = _first_text(
        mailbox_session_id,
        payload.get("mailboxSessionId"),
        payload.get("mailbox_session_id"),
        payload.get("session_id"),
        payload.get("sessionId"),
    )
    if not email:
        return {
            "ok": False,
            "status": "community_login_missing_email",
            "detail": "small-success artifact is missing email",
            "sourcePath": str(resolved_source_path),
        }
    if not password:
        return {
            "ok": False,
            "status": "community_login_missing_password",
            "detail": "small-success artifact is missing password",
            "email": email,
            "sourcePath": str(resolved_source_path),
        }
    resolved_startup_url = str(startup_url or "").strip() or DEFAULT_COMMUNITY_URL
    resolved_success_prefix = str(success_url_prefix or "").strip() or DEFAULT_COMMUNITY_URL
    try:
        login_result = run_easybrowser_openai_web_login_flow(
            email=email,
            password=password,
            mailbox_ref=resolved_mailbox_ref,
            mailbox_session_id=resolved_mailbox_session_id,
            proxy_url=str(explicit_proxy or "").strip(),
            startup_url=resolved_startup_url,
        )
    except Exception as exc:
        return {
            "ok": False,
            "status": "community_login_transport_failed",
            "detail": str(exc),
            "email": email,
            "sourcePath": str(resolved_source_path),
        }
    target_url = _first_text(login_result.get("target_url"), login_result.get("targetUrl"))
    if not bool(login_result.get("ok")):
        return {
            "ok": False,
            "status": str(login_result.get("status") or "community_login_failed"),
            "detail": str(login_result.get("detail") or "Community login failed"),
            "email": email,
            "targetUrl": target_url,
            "sourcePath": str(resolved_source_path),
            "browser": login_result,
        }
    if not _is_url_under_prefix(target_url, resolved_success_prefix):
        return {
            "ok": False,
            "status": "community_login_target_mismatch",
            "detail": f"login finished outside community target: {target_url}",
            "email": email,
            "targetUrl": target_url,
            "sourcePath": str(resolved_source_path),
            "browser": login_result,
        }
    return {
        "ok": True,
        "status": "community_login_completed",
        "email": email,
        "targetUrl": target_url,
        "sourcePath": str(resolved_source_path),
        "mailboxRef": resolved_mailbox_ref,
        "mailboxSessionId": resolved_mailbox_session_id,
        "browser": login_result,
    }
