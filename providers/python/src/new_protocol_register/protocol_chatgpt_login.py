from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    _CURRENT_DIR = Path(__file__).resolve().parent
    _SRC_DIR = _CURRENT_DIR.parent
    for _candidate in (_CURRENT_DIR, _SRC_DIR):
        candidate_text = str(_candidate)
        if candidate_text not in sys.path:
            sys.path.append(candidate_text)
    from others.bootstrap import ensure_local_bundle_imports

    ensure_local_bundle_imports()
    from others.runtime import flow_network_env, seed_device_cookie
    from others.storage import load_json_payload
else:
    from .others.bootstrap import ensure_local_bundle_imports

    ensure_local_bundle_imports()
    from .others.runtime import flow_network_env, seed_device_cookie
    from .others.storage import load_json_payload

from curl_cffi import requests

from shared_mailbox.easy_email_client import get_mailbox_latest_message_id, wait_openai_code
from shared_proxy import env_flag, normalize_proxy_env_url

from protocol_runtime.errors import ProtocolRuntimeError, ensure_protocol_runtime_error
from protocol_runtime.protocol_register import (
    AUTHORIZE_CONTINUE_URL,
    CHATGPT_BASE,
    CHATGPT_LOGIN_URL,
    CHATGPT_NEXTAUTH_CSRF_URL,
    CHATGPT_NEXTAUTH_SIGNIN_OPENAI_URL,
    DEFAULT_OTP_TIMEOUT_SECONDS,
    DEFAULT_PROTOCOL_USER_AGENT,
    EMAIL_OTP_VALIDATE_URL,
    EMAIL_VERIFICATION_REFERER,
    LOGIN_OR_CREATE_ACCOUNT_REFERER,
    LOGIN_PASSWORD_REFERER,
    MAILCREATE_BASE_URL,
    MAILCREATE_CUSTOM_AUTH,
    _build_protocol_headers,
    _complete_external_continue_url,
    _extract_page_type,
    _extract_workspace_id_from_session,
    _fetch_chatgpt_account_entries_from_session,
    _get_session_cookie,
    _get_sentinel_header_for_signup,
    _is_callback_url,
    _new_protocol_sentinel_context,
    _response_has_cloudflare_challenge,
    _response_preview,
    _response_url,
    _send_email_otp,
    _session_request,
    _submit_workspace_selection_for_callback,
    _verify_login_password,
    temporary_workspace_selector_overrides,
)


def _chatgpt_login_network_error_is_retryable(exc: BaseException) -> bool:
    text = str(exc or "").strip().lower()
    if not text:
        return False
    retry_markers = (
        "connection closed abruptly",
        "handshake operation timed out",
        "proxy connect aborted",
        "proxy connect failed",
        "operation timed out",
        "recv failure",
        "connection reset by peer",
        "curl: (56)",
        "curl: (35)",
        "curl: (28)",
        "curl: (7)",
        "urlopen error",
    )
    return any(marker in text for marker in retry_markers)


def _chatgpt_login_request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    explicit_proxy: str | None,
    request_label: str,
    **kwargs: Any,
) -> Any:
    last_exc: BaseException | None = None
    for attempt in range(1, 3):
        try:
            return _session_request(
                session,
                method,
                url,
                explicit_proxy=explicit_proxy,
                request_label=request_label,
                **kwargs,
            )
        except Exception as exc:
            last_exc = exc
            if attempt >= 2 or not _chatgpt_login_network_error_is_retryable(exc):
                raise
            print(
                "[protocol-chatgpt-login] retrying transient request "
                f"label={request_label} attempt={attempt} err={exc}",
                flush=True,
            )
            time.sleep(min(1.0, 0.3 * attempt))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"chatgpt_login_request_failed label={request_label}")


def _normalize_seed_login_context(
    seed_payload: dict[str, Any],
    *,
    mailbox_ref_override: str = "",
    mailbox_session_id_override: str = "",
) -> dict[str, str]:
    platform_auth = seed_payload.get("platformAuth") if isinstance(seed_payload.get("platformAuth"), dict) else {}
    email = str(seed_payload.get("email") or "").strip()
    password = str(seed_payload.get("password") or "").strip()
    mailbox_ref = str(
        mailbox_ref_override
        or seed_payload.get("mailboxRef")
        or seed_payload.get("mailbox_ref")
        or seed_payload.get("mailboxAccessKey")
        or ""
    ).strip()
    mailbox_session_id = str(
        mailbox_session_id_override
        or seed_payload.get("mailboxSessionId")
        or seed_payload.get("mailbox_session_id")
        or seed_payload.get("session_id")
        or ""
    ).strip()
    device_id = str(platform_auth.get("deviceId") or seed_payload.get("deviceId") or "").strip() or str(uuid.uuid4())
    return {
        "email": email,
        "password": password,
        "mailboxRef": mailbox_ref,
        "mailboxSessionId": mailbox_session_id,
        "deviceId": device_id,
    }


def _load_accounts_with_personal_preference(
    *,
    session: requests.Session,
    explicit_proxy: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    with temporary_workspace_selector_overrides({"PROTOCOL_PREFERRED_WORKSPACE_KIND": "personal"}):
        entries = _fetch_chatgpt_account_entries_from_session(
            session,
            explicit_proxy=explicit_proxy,
        )
    personal_entry: dict[str, Any] | None = None
    for entry in entries:
        if str(entry.get("kind") or "").strip().lower() == "personal":
            personal_entry = entry
            break
    return entries, personal_entry


def _wait_for_email_otp(*, mailbox_ref: str, mailbox_session_id: str, min_mail_id: int) -> str:
    try:
        code = wait_openai_code(
            mailbox_ref=mailbox_ref,
            session_id=mailbox_session_id,
            mailcreate_base_url=MAILCREATE_BASE_URL,
            mailcreate_custom_auth=MAILCREATE_CUSTOM_AUTH,
            timeout_seconds=max(
                60,
                int(
                    (os.environ.get("OTP_TIMEOUT_SECONDS") or str(DEFAULT_OTP_TIMEOUT_SECONDS)).strip()
                    or str(DEFAULT_OTP_TIMEOUT_SECONDS)
                ),
            ),
            min_mail_id=min_mail_id,
        )
    except Exception as exc:
        raise ProtocolRuntimeError(
            f"chatgpt_login_email_otp_wait_failed:{exc}",
            stage="stage_otp_validate",
            detail="chatgpt_login_email_otp_wait",
            category="otp_timeout",
        ) from exc
    normalized_code = str(code or "").strip()
    if not normalized_code:
        raise ProtocolRuntimeError(
            "chatgpt_login_email_otp_timeout",
            stage="stage_otp_validate",
            detail="chatgpt_login_email_otp_wait",
            category="otp_timeout",
        )
    return normalized_code


def _complete_chatgpt_nextauth_callback(
    *,
    session: requests.Session,
    callback_url: str,
    explicit_proxy: str | None,
) -> Any:
    response = _chatgpt_login_request(
        session,
        "GET",
        callback_url,
        explicit_proxy=explicit_proxy,
        request_label="chatgpt-nextauth-callback",
        timeout=20,
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "referer": "https://auth.openai.com/",
        },
    )
    if int(getattr(response, "status_code", 0) or 0) >= 400:
        raise ProtocolRuntimeError(
            f"chatgpt_nextauth_callback_failed status={getattr(response, 'status_code', 0)} "
            f"body={_response_preview(response, 240)}",
            stage="stage_callback",
            detail="chatgpt_nextauth_callback",
            category="auth_error",
        )
    return response


def _is_chatgpt_callback_url(url: str) -> bool:
    normalized = str(url or "").strip()
    return normalized.startswith("https://chatgpt.com/api/auth/callback/openai") and _is_callback_url(normalized)


def _bootstrap_chatgpt_login_with_redirect(
    *,
    session: requests.Session,
    device_id: str,
    explicit_proxy: str | None,
) -> str:
    login_response = _chatgpt_login_request(
        session,
        "GET",
        CHATGPT_LOGIN_URL,
        explicit_proxy=explicit_proxy,
        request_label="chatgpt-login",
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout=20,
    )
    if int(getattr(login_response, "status_code", 0) or 0) >= 400:
        raise ProtocolRuntimeError(
            f"chatgpt_login status={getattr(login_response, 'status_code', 0)} "
            f"body={_response_preview(login_response, 200)}",
            stage="stage_auth_continue",
            detail="chatgpt_login_bootstrap",
            category="flow_error",
        )

    csrf_response = _chatgpt_login_request(
        session,
        "GET",
        CHATGPT_NEXTAUTH_CSRF_URL,
        explicit_proxy=explicit_proxy,
        request_label="chatgpt-nextauth-csrf",
        headers={
            "accept": "application/json",
            "referer": CHATGPT_LOGIN_URL,
        },
        timeout=20,
    )
    if int(getattr(csrf_response, "status_code", 0) or 0) != 200:
        raise ProtocolRuntimeError(
            f"chatgpt_nextauth_csrf status={getattr(csrf_response, 'status_code', 0)} "
            f"body={_response_preview(csrf_response, 200)}",
            stage="stage_auth_continue",
            detail="chatgpt_login_bootstrap",
            category="flow_error",
        )
    try:
        csrf_payload = csrf_response.json() or {}
    except Exception:
        csrf_payload = {}
    csrf_token = str(csrf_payload.get("csrfToken") or "").strip() if isinstance(csrf_payload, dict) else ""
    if not csrf_token:
        raise ProtocolRuntimeError(
            "chatgpt_nextauth_csrf_missing_token",
            stage="stage_auth_continue",
            detail="chatgpt_login_bootstrap",
            category="auth_error",
        )

    auth_session_logging_id = str(uuid.uuid4())
    signin_query = urllib.parse.urlencode(
        {
            "prompt": "login",
            "screen_hint": "login",
            "device_id": device_id,
            "ext-oai-did": device_id,
            "auth_session_logging_id": auth_session_logging_id,
        }
    )
    signin_response = _chatgpt_login_request(
        session,
        "POST",
        f"{CHATGPT_NEXTAUTH_SIGNIN_OPENAI_URL}?{signin_query}",
        explicit_proxy=explicit_proxy,
        request_label="chatgpt-nextauth-signin-openai",
        headers={
            "accept": "application/json",
            "content-type": "application/x-www-form-urlencoded",
            "origin": CHATGPT_BASE,
            "referer": CHATGPT_LOGIN_URL,
        },
        data=urllib.parse.urlencode(
            {
                "csrfToken": csrf_token,
                "callbackUrl": "https://chatgpt.com/auth/login_with",
                "json": "true",
            }
        ),
        timeout=20,
    )
    if int(getattr(signin_response, "status_code", 0) or 0) != 200:
        raise ProtocolRuntimeError(
            f"chatgpt_nextauth_signin status={getattr(signin_response, 'status_code', 0)} "
            f"body={_response_preview(signin_response, 200)}",
            stage="stage_auth_continue",
            detail="chatgpt_login_bootstrap",
            category="flow_error",
        )
    try:
        signin_payload = signin_response.json() or {}
    except Exception:
        signin_payload = {}
    auth_url = str(signin_payload.get("url") or "").strip() if isinstance(signin_payload, dict) else ""
    if not auth_url:
        raise ProtocolRuntimeError(
            "chatgpt_nextauth_signin_missing_url",
            stage="stage_auth_continue",
            detail="chatgpt_login_bootstrap",
            category="auth_error",
        )
    nextauth_state = _get_session_cookie(
        session,
        "__Secure-next-auth.state",
        preferred_domains=("chatgpt.com", ".chatgpt.com"),
    )
    if not nextauth_state:
        raise ProtocolRuntimeError(
            "chatgpt_nextauth_signin_missing_state_cookie",
            stage="stage_auth_continue",
            detail="chatgpt_login_bootstrap",
            category="auth_error",
        )
    return auth_url


def _extract_chatgpt_client_bootstrap(html_text: str) -> dict[str, Any]:
    match = re.search(
        r'<script[^>]+id="client-bootstrap"[^>]*>(?P<body>.*?)</script>',
        str(html_text or ""),
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return {}
    raw_body = str(match.group("body") or "").strip()
    if not raw_body:
        return {}
    try:
        payload = json.loads(raw_body)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def run_protocol_chatgpt_login_init_from_path(
    *,
    source_path: str | Path,
    explicit_proxy: str | None = None,
    mailbox_ref: str | None = None,
    mailbox_session_id: str | None = None,
) -> dict[str, Any]:
    seed_path = Path(source_path).resolve()
    seed_payload = load_json_payload(seed_path)
    existing_state = seed_payload.get("chatgptLogin")
    if isinstance(existing_state, dict) and str(existing_state.get("status") or "").strip().lower() == "completed":
        return {
            "ok": True,
            "status": "already_initialized",
            "sourcePath": str(seed_path),
            "workspaceId": str(existing_state.get("workspaceId") or "").strip(),
            "personalWorkspaceId": str(existing_state.get("personalWorkspaceId") or "").strip(),
            "accountCount": int(existing_state.get("accountCount") or 0),
        }

    context = _normalize_seed_login_context(
        seed_payload,
        mailbox_ref_override=str(mailbox_ref or "").strip(),
        mailbox_session_id_override=str(mailbox_session_id or "").strip(),
    )
    email = str(context.get("email") or "").strip()
    password = str(context.get("password") or "").strip()
    mailbox_ref_value = str(context.get("mailboxRef") or "").strip()
    mailbox_session_id_value = str(context.get("mailboxSessionId") or "").strip()
    device_id = str(context.get("deviceId") or "").strip()
    if not email:
        raise ProtocolRuntimeError(
            "chatgpt_login_requires_email",
            stage="stage_auth_continue",
            detail="chatgpt_login_email_missing",
            category="flow_error",
        )
    if not mailbox_ref_value or not mailbox_session_id_value:
        raise ProtocolRuntimeError(
            "chatgpt_login_requires_mailbox_context",
            stage="stage_auth_continue",
            detail="chatgpt_login_mailbox_missing",
            category="flow_error",
        )

    explicit_proxy = normalize_proxy_env_url(explicit_proxy) or None
    verify_tls = env_flag("PROTOCOL_HTTP_VERIFY_TLS", False)
    session = requests.Session(
        impersonate="chrome",
        timeout=30,
        verify=verify_tls,
    )
    session.headers.update({"user-agent": DEFAULT_PROTOCOL_USER_AGENT})
    if device_id:
        try:
            seed_device_cookie(session, device_id)
        except Exception:
            pass

    otp_min_mail_id = 0
    used_password_verify = False
    used_email_otp = False
    auth_url = ""
    callback_url = ""
    final_url = ""
    selected_workspace_id = ""
    personal_workspace_id = ""
    account_entries: list[dict[str, Any]] = []

    try:
        with flow_network_env():
            try:
                otp_min_mail_id = get_mailbox_latest_message_id(
                    mailbox_ref=mailbox_ref_value,
                    session_id=mailbox_session_id_value,
                    mailcreate_base_url=MAILCREATE_BASE_URL,
                    mailcreate_custom_auth=MAILCREATE_CUSTOM_AUTH,
                )
            except Exception:
                otp_min_mail_id = 0

            sentinel_context = _new_protocol_sentinel_context(
                session,
                explicit_proxy=explicit_proxy,
                user_agent=DEFAULT_PROTOCOL_USER_AGENT,
            )
            auth_url = _bootstrap_chatgpt_login_with_redirect(
                session=session,
                device_id=device_id,
                explicit_proxy=explicit_proxy,
            )
            authorize_init_response = _chatgpt_login_request(
                session,
                "GET",
                auth_url,
                explicit_proxy=explicit_proxy,
                request_label="chatgpt-login-authorize-init",
                timeout=20,
            )
            if int(getattr(authorize_init_response, "status_code", 0) or 0) >= 400:
                raise ProtocolRuntimeError(
                    f"chatgpt_login_authorize_init_failed status={getattr(authorize_init_response, 'status_code', 0)} "
                    f"body={_response_preview(authorize_init_response, 220)}",
                    stage="stage_auth_continue",
                    detail="chatgpt_login_authorize_init",
                    category="auth_error" if _response_has_cloudflare_challenge(authorize_init_response) else "flow_error",
                )

            authorize_continue_headers = _build_protocol_headers(
                request_kind="",
                referer=LOGIN_OR_CREATE_ACCOUNT_REFERER,
            )
            authorize_continue_headers["openai-sentinel-token"] = _get_sentinel_header_for_signup(
                session,
                device_id=device_id,
                flow="authorize_continue",
                request_kind="chatgpt-login-authorize-continue",
                explicit_proxy=explicit_proxy,
                sentinel_context=sentinel_context,
            )
            authorize_continue_headers["oai-device-id"] = device_id
            continue_response = _chatgpt_login_request(
                session,
                "POST",
                AUTHORIZE_CONTINUE_URL,
                explicit_proxy=explicit_proxy,
                request_label="chatgpt-login-authorize-continue",
                headers=authorize_continue_headers,
                data=json.dumps(
                    {
                        "username": {
                            "value": email,
                            "kind": "email",
                        },
                        "screen_hint": "login",
                    }
                ),
            )
            if int(getattr(continue_response, "status_code", 0) or 0) != 200:
                raise ProtocolRuntimeError(
                    f"chatgpt_login_authorize_continue_failed status={getattr(continue_response, 'status_code', 0)} "
                    f"body={_response_preview(continue_response, 220)}",
                    stage="stage_auth_continue",
                    detail="chatgpt_login_authorize_continue",
                    category="blocked" if _response_has_cloudflare_challenge(continue_response) else "flow_error",
                )

            oauth_entry_response = continue_response
            page_type = _extract_page_type(oauth_entry_response)
            if page_type == "login_password":
                if not password:
                    raise ProtocolRuntimeError(
                        "chatgpt_login_password_required",
                        stage="stage_create_account",
                        detail="chatgpt_login_password_verify",
                        category="auth_error",
                    )
                oauth_entry_response = _verify_login_password(
                    session,
                    password=password,
                    explicit_proxy=explicit_proxy,
                    header_builder=sentinel_context,
                )
                used_password_verify = True
                page_type = _extract_page_type(oauth_entry_response)

            if page_type == "email_otp_send":
                _send_email_otp(
                    session,
                    explicit_proxy=explicit_proxy,
                    header_builder=sentinel_context,
                )
                page_type = "email_otp_verification"

            if page_type == "email_otp_verification":
                otp_code = _wait_for_email_otp(
                    mailbox_ref=mailbox_ref_value,
                    mailbox_session_id=mailbox_session_id_value,
                    min_mail_id=otp_min_mail_id,
                )
                used_email_otp = True
                otp_validate_headers = _build_protocol_headers(
                    request_kind="otp-validate",
                    referer=EMAIL_VERIFICATION_REFERER,
                    sentinel_context=sentinel_context,
                )
                otp_validate_headers["oai-device-id"] = device_id
                oauth_entry_response = _chatgpt_login_request(
                    session,
                    "POST",
                    EMAIL_OTP_VALIDATE_URL,
                    explicit_proxy=explicit_proxy,
                    request_label="chatgpt-login-otp-validate",
                    headers=otp_validate_headers,
                    data=json.dumps({"code": otp_code}),
                )
                if int(getattr(oauth_entry_response, "status_code", 0) or 0) >= 400:
                    raise ProtocolRuntimeError(
                        f"chatgpt_login_otp_validate_failed status={getattr(oauth_entry_response, 'status_code', 0)} "
                        f"body={_response_preview(oauth_entry_response, 220)}",
                        stage="stage_otp_validate",
                        detail="chatgpt_login_email_otp_validate",
                        category="flow_error",
                    )
                page_type = _extract_page_type(oauth_entry_response)

            oauth_entry_response = _complete_external_continue_url(
                session,
                oauth_entry_response,
                explicit_proxy=explicit_proxy,
                request_label="chatgpt-login",
                referer=EMAIL_VERIFICATION_REFERER,
            )

            response_url = _response_url(oauth_entry_response)
            response_html = str(getattr(oauth_entry_response, "text", "") or "")
            client_bootstrap = _extract_chatgpt_client_bootstrap(response_html)
            bootstrap_session = client_bootstrap.get("session") if isinstance(client_bootstrap.get("session"), dict) else {}
            bootstrap_account = bootstrap_session.get("account") if isinstance(bootstrap_session.get("account"), dict) else {}
            bootstrap_user = bootstrap_session.get("user") if isinstance(bootstrap_session.get("user"), dict) else {}
            bootstrap_auth_status = str(client_bootstrap.get("authStatus") or "").strip().lower()
            bootstrap_account_id = str(bootstrap_account.get("id") or "").strip()
            bootstrap_plan_type = str(bootstrap_account.get("planType") or "").strip().lower()
            bootstrap_structure = str(bootstrap_account.get("structure") or "").strip().lower()
            bootstrap_access_token = str(bootstrap_session.get("accessToken") or "").strip()
            personal_entry: dict[str, Any] | None = None
            if (
                bootstrap_auth_status == "logged_in"
                and bootstrap_account_id
                and bootstrap_structure == "personal"
                and bootstrap_plan_type == "free"
            ):
                personal_entry = {
                    "id": bootstrap_account_id,
                    "kind": "personal",
                    "name": "Personal",
                    "title": "Personal",
                    "source": "client_bootstrap",
                }
                account_entries = [personal_entry]
                final_url = response_url
                selected_workspace_id = bootstrap_account_id
            else:
                account_entries, personal_entry = _load_accounts_with_personal_preference(
                    session=session,
                    explicit_proxy=explicit_proxy,
                )
                if personal_entry is not None:
                    final_url = response_url
                    selected_workspace_id = str(personal_entry.get("id") or "").strip()
            if personal_entry is None:
                callback_candidate = response_url if _is_chatgpt_callback_url(response_url) else ""
                if not callback_candidate:
                    callback_candidate = ""
                if callback_candidate:
                    callback_url = callback_candidate
                    callback_response = _complete_chatgpt_nextauth_callback(
                        session=session,
                        callback_url=callback_url,
                        explicit_proxy=explicit_proxy,
                    )
                    final_url = _response_url(callback_response) or callback_url
                    account_entries, personal_entry = _load_accounts_with_personal_preference(
                        session=session,
                        explicit_proxy=explicit_proxy,
                    )
                    if personal_entry is not None:
                        selected_workspace_id = str(personal_entry.get("id") or "").strip()
                if personal_entry is None:
                    with temporary_workspace_selector_overrides({"PROTOCOL_PREFERRED_WORKSPACE_KIND": "personal"}):
                        selected_workspace_id = _extract_workspace_id_from_session(
                            session,
                            explicit_proxy=explicit_proxy,
                        )
                        callback_url = _submit_workspace_selection_for_callback(
                            session=session,
                            workspace_id=selected_workspace_id,
                            explicit_proxy=explicit_proxy,
                            referer=response_url or EMAIL_VERIFICATION_REFERER,
                            workspace_request_label="chatgpt-login-workspace-select",
                            header_builder=sentinel_context,
                        )
                    callback_response = _complete_chatgpt_nextauth_callback(
                        session=session,
                        callback_url=callback_url,
                        explicit_proxy=explicit_proxy,
                    )
                    final_url = _response_url(callback_response) or callback_url
                    account_entries, personal_entry = _load_accounts_with_personal_preference(
                        session=session,
                        explicit_proxy=explicit_proxy,
                    )
            personal_workspace_id = str((personal_entry or {}).get("id") or "").strip()
            if not personal_workspace_id:
                raise ProtocolRuntimeError(
                    "chatgpt_login_personal_workspace_missing",
                    stage="stage_callback",
                    detail="chatgpt_login_personal_workspace_missing",
                    category="auth_error",
                )

        result = {
            "ok": True,
            "status": "completed",
            "sourcePath": str(seed_path),
            "authUrl": auth_url,
            "callbackUrl": callback_url,
            "finalUrl": final_url,
            "workspaceId": selected_workspace_id,
            "personalWorkspaceId": personal_workspace_id,
            "accountCount": len(account_entries),
            "personalAccountCount": sum(
                1 for entry in account_entries if str(entry.get("kind") or "").strip().lower() == "personal"
            ),
            "usedPasswordVerify": used_password_verify,
            "usedEmailOtp": used_email_otp,
            "deviceId": device_id,
            "mailboxRef": mailbox_ref_value,
            "mailboxSessionId": mailbox_session_id_value,
        }
        updated_payload = dict(seed_payload)
        updated_payload["mailboxRef"] = mailbox_ref_value
        updated_payload["mailboxAccessKey"] = mailbox_ref_value
        updated_payload["mailboxSessionId"] = mailbox_session_id_value
        updated_payload["chatgptLogin"] = result
        updated_payload["chatgptLoginDetails"] = {
            "accounts": account_entries,
            "clientBootstrap": {
                "authStatus": bootstrap_auth_status,
                "accountId": bootstrap_account_id,
                "planType": bootstrap_plan_type,
                "structure": bootstrap_structure,
                "accessTokenPresent": bool(bootstrap_access_token),
                "userId": str(bootstrap_user.get("id") or "").strip(),
                "email": str(bootstrap_user.get("email") or "").strip(),
            },
            "pageType": _extract_page_type(oauth_entry_response) or "",
        }
        seed_path.write_text(json.dumps(updated_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return result
    except Exception as exc:
        raise ensure_protocol_runtime_error(
            exc,
            stage="stage_auth_continue",
            detail="chatgpt_login_init",
            category="flow_error",
        ) from exc
    finally:
        try:
            session.close()
        except Exception:
            pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize a ChatGPT login session from a small_success seed.")
    parser.add_argument("source", help="Path to small_success JSON.")
    parser.add_argument("--proxy", default="", help="Optional explicit proxy URL.")
    parser.add_argument("--mailbox-ref", default="", help="Optional mailbox ref override.")
    parser.add_argument("--mailbox-session-id", default="", help="Optional mailbox session id override.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = run_protocol_chatgpt_login_init_from_path(
        source_path=args.source,
        explicit_proxy=str(args.proxy or "").strip() or None,
        mailbox_ref=str(args.mailbox_ref or "").strip() or None,
        mailbox_session_id=str(args.mailbox_session_id or "").strip() or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
