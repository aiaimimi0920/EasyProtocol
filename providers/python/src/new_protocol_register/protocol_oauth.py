from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    from pathlib import Path as _Path

    _CURRENT_DIR = _Path(__file__).resolve().parent
    _SRC_DIR = _CURRENT_DIR.parent
    for _candidate in (_CURRENT_DIR, _SRC_DIR):
        candidate_text = str(_candidate)
        if candidate_text not in sys.path:
            sys.path.append(candidate_text)
    from others.bootstrap import ensure_local_bundle_imports

    ensure_local_bundle_imports()
    from others.models import PLATFORM_LOGIN_URL, ProtocolOAuthResult
    from others.runtime import flow_network_env, lease_flow_proxy, resolve_mailbox
    from others.storage import load_json_payload, persist_success_auth_json
else:
    from .others.bootstrap import ensure_local_bundle_imports

    ensure_local_bundle_imports()
    from .others.models import PLATFORM_LOGIN_URL, ProtocolOAuthResult
    from .others.runtime import flow_network_env, lease_flow_proxy, resolve_mailbox
    from .others.storage import load_json_payload, persist_success_auth_json

from protocol_runtime.protocol_register import run_protocol_repair_once, temporary_workspace_selector_overrides
from shared_mailbox.easy_email_client import release_mailbox, release_mailbox_sessions_by_email


def _services_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _find_easyemail_config() -> Path | None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        direct = parent / "EmailService" / "deploy" / "EasyEmail" / "config.yaml"
        if direct.exists():
            return direct
        nested = parent / "server" / "EmailService" / "deploy" / "EasyEmail" / "config.yaml"
        if nested.exists():
            return nested
    return None


def _read_env_like_value(path: Path, key: str) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*(.+?)\s*$", text)
    if not match:
        return ""
    return str(match.group(1) or "").strip().strip('"').strip("'")


def _read_easyemail_server_api_key() -> str:
    config_path = _find_easyemail_config()
    if config_path is None:
        return ""
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    match = re.search(r'(?m)^\s*apiKey:\s*"([^"]+)"\s*$', text)
    if match:
        return str(match.group(1) or "").strip()
    match = re.search(r"(?m)^\s*apiKey:\s*([^\s#]+)\s*$", text)
    if match:
        return str(match.group(1) or "").strip().strip('"').strip("'")
    return ""


def _ensure_protocol_oauth_easy_runtime_defaults() -> None:
    if not str(os.environ.get("MAILBOX_SERVICE_BASE_URL") or "").strip():
        os.environ["MAILBOX_SERVICE_BASE_URL"] = "http://localhost:18080"
    if not str(os.environ.get("MAILBOX_SERVICE_API_KEY") or "").strip():
        api_key = ""
        current = Path(__file__).resolve()
        for parent in current.parents:
            env_path = parent / ".env"
            if env_path.exists():
                api_key = _read_env_like_value(env_path, "MAILBOX_SERVICE_API_KEY")
                if api_key:
                    break
        if not api_key:
            api_key = _read_easyemail_server_api_key()
        if api_key:
            os.environ["MAILBOX_SERVICE_API_KEY"] = api_key


def _resolve_workspace_selector_overrides(workspace_selector: str | None) -> dict[str, str]:
    raw_selector = str(workspace_selector or "").strip()
    if not raw_selector:
        return {}

    tokens = [token.strip() for token in re.split(r"[;,]+", raw_selector) if token.strip()]
    if not tokens:
        tokens = [raw_selector]

    overrides: dict[str, str] = {}
    for token in tokens:
        lowered = token.lower()
        if lowered in {"auto", "default"}:
            continue
        if lowered in {"first_team", "team_first", "first-team", "team-first"}:
            overrides["PROTOCOL_PREFERRED_WORKSPACE_KIND"] = "team"
            overrides["PROTOCOL_PREFERRED_WORKSPACE_INDEX"] = "0"
            continue
        if lowered in {"last_team", "team_last", "last-team", "team-last"}:
            overrides["PROTOCOL_PREFERRED_WORKSPACE_KIND"] = "team"
            overrides["PROTOCOL_PREFERRED_WORKSPACE_INDEX"] = "-1"
            continue
        if lowered == "team":
            overrides["PROTOCOL_PREFERRED_WORKSPACE_KIND"] = "team"
            continue
        if lowered == "personal":
            overrides["PROTOCOL_PREFERRED_WORKSPACE_KIND"] = "personal"
            continue
        if lowered == "first":
            overrides["PROTOCOL_PREFERRED_WORKSPACE_INDEX"] = "0"
            continue
        if lowered == "last":
            overrides["PROTOCOL_PREFERRED_WORKSPACE_INDEX"] = "-1"
            continue
        if ":" not in token:
            continue
        key, value = token.split(":", 1)
        normalized_key = str(key or "").strip().lower()
        normalized_value = str(value or "").strip()
        if not normalized_value:
            continue
        if normalized_key in {"id", "workspace_id"}:
            overrides["PROTOCOL_PREFERRED_WORKSPACE_ID"] = normalized_value
        elif normalized_key in {"name", "workspace_name"}:
            overrides["PROTOCOL_PREFERRED_WORKSPACE_NAME"] = normalized_value
        elif normalized_key in {"kind", "workspace_kind"}:
            overrides["PROTOCOL_PREFERRED_WORKSPACE_KIND"] = normalized_value
        elif normalized_key in {"index", "workspace_index"}:
            overrides["PROTOCOL_PREFERRED_WORKSPACE_INDEX"] = normalized_value
    return overrides


@contextlib.contextmanager
def _temporary_workspace_selector(workspace_selector: str | None):
    overrides = _resolve_workspace_selector_overrides(workspace_selector)
    if not overrides:
        yield
        return
    print(
        "[protocol_oauth] applying workspace selector overrides "
        f"selector={workspace_selector or '<none>'} overrides={json.dumps(overrides, ensure_ascii=False, sort_keys=True)}"
    )
    with temporary_workspace_selector_overrides(overrides):
        yield


def _extract_account_id(auth_payload: dict[str, Any]) -> str:
    if not isinstance(auth_payload, dict):
        return ""
    direct = str(auth_payload.get("account_id") or auth_payload.get("chatgpt_account_id") or "").strip()
    if direct:
        return direct
    nested = auth_payload.get("https://api.openai.com/auth")
    if isinstance(nested, dict):
        direct = str(nested.get("chatgpt_account_id") or nested.get("account_id") or "").strip()
        if direct:
            return direct
    return ""


def _normalize_seed_payload(seed_payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(seed_payload, dict):
        raise RuntimeError("protocol_oauth_requires_seed_payload")
    return {
        "email": str(seed_payload.get("email") or "").strip(),
        "password": str(seed_payload.get("password") or "").strip(),
        "mailbox_ref": str(
            seed_payload.get("mailboxRef")
            or seed_payload.get("mailbox_ref")
            or seed_payload.get("mailboxAccessKey")
            or ""
        ).strip(),
        "session_id": str(
            seed_payload.get("mailboxSessionId")
            or seed_payload.get("mailbox_session_id")
            or seed_payload.get("session_id")
            or ""
        ).strip(),
        "first_name": str(seed_payload.get("firstName") or seed_payload.get("first_name") or "").strip(),
        "last_name": str(seed_payload.get("lastName") or seed_payload.get("last_name") or "").strip(),
        "birthdate": str(seed_payload.get("birthdate") or "").strip(),
    }


def _refresh_seed_mailbox_binding(auth_obj: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    email = str(auth_obj.get("email") or "").strip()
    if not email:
        raise RuntimeError("protocol_oauth_requires_seed_email")
    previous_mailbox_ref = str(auth_obj.get("mailbox_ref") or "").strip()
    previous_session_id = str(auth_obj.get("session_id") or "").strip()
    pre_refresh_cleanup = release_mailbox_sessions_by_email(
        email_address=email,
        reason="protocol_oauth_pre_refresh_cleanup",
    )
    mailbox = resolve_mailbox(
        preallocated_email=email,
        preallocated_session_id=previous_session_id or None,
        preallocated_mailbox_ref=previous_mailbox_ref or None,
        recreate_preallocated_email=True,
    )
    refresh_details = {
        "email": str(mailbox.email or "").strip(),
        "provider": str(mailbox.provider or "").strip(),
        "mailboxRef": str(mailbox.ref or "").strip(),
        "sessionId": str(mailbox.session_id or "").strip(),
        "previousMailboxRef": previous_mailbox_ref,
        "previousSessionId": previous_session_id,
        "preRefreshCleanup": pre_refresh_cleanup,
    }
    updated_auth = dict(auth_obj)
    updated_auth["email"] = refresh_details["email"]
    updated_auth["mailbox_ref"] = refresh_details["mailboxRef"]
    updated_auth["mailboxRef"] = refresh_details["mailboxRef"]
    updated_auth["mailboxAccessKey"] = refresh_details["mailboxRef"]
    updated_auth["session_id"] = refresh_details["sessionId"]
    updated_auth["mailboxSessionId"] = refresh_details["sessionId"]
    updated_auth["mailboxRefresh"] = refresh_details
    return updated_auth, refresh_details


def _release_refreshed_mailbox(mailbox_refresh: dict[str, Any], *, reason: str) -> dict[str, Any]:
    session_id = str(mailbox_refresh.get("sessionId") or "").strip()
    if not session_id:
        return {}
    try:
        result = release_mailbox(session_id=session_id, reason=reason)
    except Exception as exc:
        return {
            "ok": False,
            "sessionId": session_id,
            "reason": reason,
            "error": str(exc),
        }
    return {
        "ok": True,
        "sessionId": session_id,
        "reason": reason,
        "result": result,
    }


def run_protocol_oauth_once(
    *,
    seed_payload: dict[str, Any],
    output_dir: str | None = None,
    explicit_proxy: str | None = None,
    workspace_selector: str | None = None,
    force_email_otp_resend_on_verification: bool = False,
) -> ProtocolOAuthResult:
    auth_obj = _normalize_seed_payload(seed_payload)
    auth_obj, mailbox_refresh = _refresh_seed_mailbox_binding(auth_obj)
    _ensure_protocol_oauth_easy_runtime_defaults()
    try:
        with _temporary_workspace_selector(workspace_selector):
            with flow_network_env():
                if explicit_proxy is None:
                    with lease_flow_proxy(
                        flow_name="protocol_oauth",
                        metadata={"email": str(auth_obj.get("email") or "").strip()},
                        probe_url=PLATFORM_LOGIN_URL,
                        probe_expected_statuses={200, 307, 308},
                    ) as flow_proxy:
                        resolved_proxy = str(flow_proxy.proxy_url or "").strip() or None
                        protocol_result = run_protocol_repair_once(
                            auth_obj=auth_obj,
                            proxy=resolved_proxy,
                            force_email_otp_resend_on_verification=force_email_otp_resend_on_verification,
                        )
                        result_auth = dict(protocol_result.auth or {})
                else:
                    protocol_result = run_protocol_repair_once(
                        auth_obj=auth_obj,
                        proxy=explicit_proxy,
                        force_email_otp_resend_on_verification=force_email_otp_resend_on_verification,
                    )
                    result_auth = dict(protocol_result.auth or {})
    except Exception:
        _release_refreshed_mailbox(mailbox_refresh, reason="protocol_oauth_failure")
        release_mailbox_sessions_by_email(
            email_address=str(auth_obj.get("email") or "").strip(),
            reason="protocol_oauth_failure_cleanup_by_email",
        )
        raise

    refreshed_mailbox_ref = str(auth_obj.get("mailbox_ref") or "").strip()
    refreshed_session_id = str(auth_obj.get("session_id") or "").strip()
    if refreshed_mailbox_ref:
        result_auth["mailbox_ref"] = refreshed_mailbox_ref
        result_auth["mailboxRef"] = refreshed_mailbox_ref
        result_auth["mailboxAccessKey"] = refreshed_mailbox_ref
    if refreshed_session_id:
        result_auth["session_id"] = refreshed_session_id
        result_auth["mailboxSessionId"] = refreshed_session_id
    if mailbox_refresh:
        result_auth["mailboxRefresh"] = mailbox_refresh
    mailbox_release = _release_refreshed_mailbox(mailbox_refresh, reason="protocol_oauth_success_cleanup")
    if mailbox_release:
        result_auth["mailboxRelease"] = mailbox_release
        if isinstance(result_auth.get("mailboxRefresh"), dict):
            result_auth["mailboxRefresh"]["releasedAfterOAuth"] = bool(mailbox_release.get("ok"))
    post_success_cleanup = release_mailbox_sessions_by_email(
        email_address=str(auth_obj.get("email") or "").strip(),
        reason="protocol_oauth_success_cleanup_by_email",
    )
    if post_success_cleanup and isinstance(result_auth.get("mailboxRefresh"), dict):
        result_auth["mailboxRefresh"]["postSuccessCleanup"] = post_success_cleanup
    storage_path = persist_success_auth_json(
        output_dir=output_dir,
        email=str(result_auth.get("email") or auth_obj.get("email") or "").strip(),
        auth_obj=result_auth,
    )
    resolved_email = str(result_auth.get("email") or auth_obj.get("email") or "").strip()
    resolved_account_id = _extract_account_id(result_auth)
    return ProtocolOAuthResult(
        email=resolved_email,
        account_id=resolved_account_id,
        storage_path=storage_path,
        auth=result_auth,
    )


def run_protocol_oauth_from_path(
    *,
    seed_path: str | Path,
    output_dir: str | None = None,
    explicit_proxy: str | None = None,
    workspace_selector: str | None = None,
    force_email_otp_resend_on_verification: bool = False,
) -> ProtocolOAuthResult:
    return run_protocol_oauth_once(
        seed_payload=load_json_payload(seed_path),
        output_dir=output_dir,
        explicit_proxy=explicit_proxy,
        workspace_selector=workspace_selector,
        force_email_otp_resend_on_verification=force_email_otp_resend_on_verification,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pure protocol OAuth repair from a small_success seed.")
    parser.add_argument("source", help="Path to small_success JSON.")
    parser.add_argument("--output-dir", default="", help="Optional success auth output dir.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = run_protocol_oauth_from_path(
        seed_path=args.source,
        output_dir=str(args.output_dir or "").strip() or None,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
