from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import uuid
import contextlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

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
    from others.models import PLATFORM_LOGIN_URL, PlatformProtocolRegistrationResult
    from others.paths import DEFAULT_REGISTER_PROTOCOL_OUTPUT_DIR
    from others.runtime import flow_network_env, lease_flow_proxy, resolve_mailbox, seed_device_cookie
    from others.storage import load_json_payload, persist_small_success_record
else:
    from .others.bootstrap import ensure_local_bundle_imports

    ensure_local_bundle_imports()
    from .others.models import PLATFORM_LOGIN_URL, PlatformProtocolRegistrationResult
    from .others.paths import DEFAULT_REGISTER_PROTOCOL_OUTPUT_DIR
    from .others.runtime import flow_network_env, lease_flow_proxy, resolve_mailbox, seed_device_cookie
    from .others.storage import load_json_payload, persist_small_success_record

from curl_cffi import requests

from shared_mailbox.easy_email_client import get_mailbox_latest_message_id, release_mailbox, wait_openai_code
from shared_proxy import env_flag, normalize_proxy_env_url

from protocol_runtime.errors import ProtocolRuntimeError, ensure_protocol_runtime_error
from protocol_runtime.protocol_register import (
    ABOUT_YOU_REFERER,
    AUTH_BASE,
    AUTHORIZE_CONTINUE_URL,
    CREATE_ACCOUNT_PASSWORD_REFERER,
    CREATE_ACCOUNT_REFERER,
    CREATE_ACCOUNT_URL,
    DEFAULT_OTP_TIMEOUT_SECONDS,
    DEFAULT_PROTOCOL_USER_AGENT,
    EMAIL_OTP_VALIDATE_URL,
    EMAIL_VERIFICATION_REFERER,
    LOGIN_OR_CREATE_ACCOUNT_REFERER,
    MAILCREATE_BASE_URL,
    MAILCREATE_CUSTOM_AUTH,
    PLATFORM_OPENAI_LOGIN_URL,
    PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV,
    PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV,
    PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV,
    USER_REGISTER_URL,
    _build_protocol_headers,
    _clone_protocol_sentinel_context,
    _deduped_cookie_header_for_request,
    _extract_page_type,
    _get_session_cookie,
    _get_sentinel_header_for_signup,
    _new_protocol_sentinel_context,
    _maybe_prime_protocol_auth_session_with_browser,
    _normalize_auth_url_device_id,
    _protocol_auth_cookie_summary,
    _random_birthdate,
    _response_continue_url,
    _response_error_code,
    _response_has_phone_wall,
    _response_has_cloudflare_challenge,
    _response_preview,
    _send_email_otp,
    _session_request,
    _submit_browser_native_signup_user_register,
)
from protocol_runtime.register_inputs import generate_name, generate_pwd


_PLATFORM_AUTH0_DOMAIN = "https://auth.openai.com/api/accounts"
_PLATFORM_AUTH0_ISSUER = "https://auth.openai.com"
_PLATFORM_AUTH0_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
_PLATFORM_AUTH0_AUDIENCE = "https://api.openai.com/v1"
_PLATFORM_AUTH0_REDIRECT_URI = "https://platform.openai.com/auth/callback"
_PLATFORM_AUTH0_SCOPE = "openid profile email offline_access"
_PLATFORM_AUTH0_CLIENT_INFO = {"name": "auth0-spa-js", "version": "1.21.0"}
_PLATFORM_AUTH0_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6"
_PLATFORM_AUTH0_SEC_CH_UA = '"Chromium";v="146", "Google Chrome";v="146", "Not.A/Brand";v="99"'


def _resolve_create_openai_account_flow_timeout_seconds() -> int:
    for env_name in (
        "REGISTER_FLOW_TIMEOUT_SECONDS",
        "REGISTER_MAILBOX_TTL_SECONDS",
        "OTP_TIMEOUT_SECONDS",
    ):
        raw = str(os.environ.get(env_name) or "").strip()
        if not raw:
            continue
        try:
            return max(1, int(float(raw)))
        except Exception:
            continue
    return max(1, int(DEFAULT_OTP_TIMEOUT_SECONDS))


def _minimal_user_register_cookie_header(session: requests.Session) -> str:
    preferred_names = (
        "cf_clearance",
        "__cf_bm",
        "_cfuvid",
        "oai-did",
        "auth_provider",
        "hydra_redirect",
        "login_session",
        "auth-session-minimized",
        "auth-session-minimized-client-checksum",
        "unified_session_manifest",
        "oai-client-auth-session",
    )
    pairs: list[str] = []
    for name in preferred_names:
        value = _get_session_cookie(
            session,
            name,
            preferred_domains=("auth.openai.com", ".openai.com"),
        )
        if value:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _augment_create_openai_account_error(*, exc: ProtocolRuntimeError, mailbox: Any) -> ProtocolRuntimeError:
    if mailbox is None:
        return exc
    text = str(exc or "")
    if "mailbox_provider=" in text or "email=" in text:
        return exc
    provider = str(getattr(mailbox, "provider", "") or "").strip()
    email = str(getattr(mailbox, "email", "") or "").strip()
    session_id = str(getattr(mailbox, "session_id", "") or "").strip()
    context_bits: list[str] = []
    if provider:
        context_bits.append(f"mailbox_provider={provider}")
    if email:
        context_bits.append(f"email={email}")
    if session_id:
        context_bits.append(f"session_id={session_id}")
    if not context_bits:
        return exc
    return ProtocolRuntimeError(
        f"{text} [{' '.join(context_bits)}]",
        stage=exc.stage,
        detail=exc.detail,
        category=exc.category,
    )


def _open_platform_login(*, session: requests.Session, explicit_proxy: str | None) -> Any:
    response = _session_request(
        session,
        "GET",
        PLATFORM_LOGIN_URL,
        explicit_proxy=explicit_proxy,
        request_label="platform-login",
        timeout=20,
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "accept-language": "en-US,en;q=0.9",
            "upgrade-insecure-requests": "1",
            "user-agent": str(session.headers.get("user-agent") or DEFAULT_PROTOCOL_USER_AGENT),
        },
    )
    _raise_if_unexpected_http(
        response,
        expected_statuses={200},
        stage="stage_platform_login",
        detail="platform_login",
    )
    return response


def _open_login_or_create_account(*, session: requests.Session, explicit_proxy: str | None) -> Any:
    response = _session_request(
        session,
        "GET",
        LOGIN_OR_CREATE_ACCOUNT_REFERER,
        explicit_proxy=explicit_proxy,
        request_label="openai-login-init-entry",
        timeout=20,
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "accept-language": _PLATFORM_AUTH0_ACCEPT_LANGUAGE,
            "upgrade-insecure-requests": "1",
            "user-agent": str(session.headers.get("user-agent") or DEFAULT_PROTOCOL_USER_AGENT),
            "sec-ch-ua": _PLATFORM_AUTH0_SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
        },
    )
    _raise_if_unexpected_http(
        response,
        expected_statuses={200},
        stage="stage_auth_continue",
        detail="openai_login_init_entry",
    )
    return response


def _submit_email_otp_validate(
    *,
    session: requests.Session,
    code: str,
    device_id: str,
    sentinel_context: Any,
    explicit_proxy: str | None,
) -> Any:
    sentinel_header = _get_sentinel_header_for_signup(
        session,
        device_id=device_id,
        flow="email_otp_validate",
        request_kind="otp-validate",
        explicit_proxy=explicit_proxy,
        sentinel_context=sentinel_context,
    )
    headers = _build_protocol_headers(request_kind="", referer=EMAIL_VERIFICATION_REFERER)
    headers["openai-sentinel-token"] = sentinel_header
    cookie_header = _deduped_cookie_header_for_request(session, EMAIL_OTP_VALIDATE_URL)
    if cookie_header:
        headers["cookie"] = cookie_header
    return _session_request(
        session,
        "POST",
        EMAIL_OTP_VALIDATE_URL,
        explicit_proxy=explicit_proxy,
        request_label="platform-email-otp-validate",
        headers=headers,
        json={"code": code},
    )


def _submit_create_account(
    *,
    session: requests.Session,
    name: str,
    birthdate: str,
    device_id: str,
    sentinel_context: Any,
    explicit_proxy: str | None,
) -> Any:
    sentinel_header = _get_sentinel_header_for_signup(
        session,
        device_id=device_id,
        flow="oauth_create_account",
        request_kind="signup-create-account",
        explicit_proxy=explicit_proxy,
        sentinel_context=sentinel_context,
    )
    headers = _build_protocol_headers(request_kind="", referer=ABOUT_YOU_REFERER)
    headers["openai-sentinel-token"] = sentinel_header
    cookie_header = _deduped_cookie_header_for_request(session, CREATE_ACCOUNT_URL)
    if cookie_header:
        headers["cookie"] = cookie_header
    return _session_request(
        session,
        "POST",
        CREATE_ACCOUNT_URL,
        explicit_proxy=explicit_proxy,
        request_label="platform-create-account",
        headers=headers,
        json={"name": name, "birthdate": birthdate},
    )


def _raise_if_unexpected_http(response: Any, *, expected_statuses: set[int], stage: str, detail: str) -> None:
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code in expected_statuses:
        return
    raise ProtocolRuntimeError(
        f"{detail} status={status_code} body={_response_preview(response, 260)}",
        stage=stage,
        detail=detail,
        category="flow_error",
    )


def _urlsafe_b64_no_padding(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _platform_auth0_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(str(verifier or "").encode("utf-8")).digest()
    return _urlsafe_b64_no_padding(digest)


def _build_platform_auth0_authorize_context(*, email: str, device_id: str) -> dict[str, str]:
    code_verifier = _urlsafe_b64_no_padding(secrets.token_bytes(32))
    state = _urlsafe_b64_no_padding(secrets.token_bytes(24))
    nonce = _urlsafe_b64_no_padding(secrets.token_bytes(24))
    auth0_client = _urlsafe_b64_no_padding(
        json.dumps(_PLATFORM_AUTH0_CLIENT_INFO, separators=(",", ":")).encode("utf-8")
    )
    params = {
        "client_id": _PLATFORM_AUTH0_CLIENT_ID,
        "audience": _PLATFORM_AUTH0_AUDIENCE,
        "redirect_uri": _PLATFORM_AUTH0_REDIRECT_URI,
        "scope": _PLATFORM_AUTH0_SCOPE,
        "response_type": "code",
        "response_mode": "query",
        "state": state,
        "nonce": nonce,
        "code_challenge": _platform_auth0_code_challenge(code_verifier),
        "code_challenge_method": "S256",
        "screen_hint": "login_or_signup",
        "login_hint": str(email or "").strip(),
        "max_age": "0",
        "device_id": str(device_id or "").strip(),
        "auth0Client": auth0_client,
    }
    return {
        "url": f"{_PLATFORM_AUTH0_DOMAIN}/authorize?{urllib.parse.urlencode(params)}",
        "codeVerifier": code_verifier,
        "state": state,
        "nonce": nonce,
        "auth0Client": auth0_client,
        "deviceId": str(device_id or "").strip(),
    }


def _decode_cookie_payload(cookie_value: str | None) -> dict[str, Any]:
    token = str(cookie_value or "").strip()
    if not token:
        return {}
    try:
        padded = token + ("=" * (-len(token) % 4))
        payload = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        parsed = json.loads(payload)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _decode_current_auth_session_payload(session: requests.Session) -> dict[str, Any]:
    return _decode_cookie_payload(
        _get_session_cookie(
            session,
            "oai-client-auth-session",
            preferred_domains=(".openai.com", "auth.openai.com"),
        )
    )


def _submit_authorize_continue_login_or_signup(
    *,
    session: requests.Session,
    email: str,
    device_id: str,
    sentinel_context: Any,
    explicit_proxy: str | None,
) -> Any:
    sentinel_header = _get_sentinel_header_for_signup(
        session,
        device_id=device_id,
        flow="authorize_continue",
        request_kind="signup-authorize-continue",
        explicit_proxy=explicit_proxy,
        sentinel_context=sentinel_context,
    )
    headers = _build_protocol_headers(request_kind="", referer=CREATE_ACCOUNT_REFERER)
    headers["openai-sentinel-token"] = sentinel_header
    headers["accept-language"] = _PLATFORM_AUTH0_ACCEPT_LANGUAGE
    headers["sec-ch-ua"] = _PLATFORM_AUTH0_SEC_CH_UA
    cookie_header = _deduped_cookie_header_for_request(session, AUTHORIZE_CONTINUE_URL)
    if cookie_header:
        headers["cookie"] = cookie_header
    return _session_request(
        session,
        "POST",
        AUTHORIZE_CONTINUE_URL,
        explicit_proxy=explicit_proxy,
        request_label="platform-authorize-continue-login-or-signup",
        headers=headers,
        json={"username": {"value": email, "kind": "email"}, "screen_hint": "login_or_signup"},
    )


def _submit_authorize_continue_login_or_create_account(
    *,
    session: requests.Session,
    email: str,
    device_id: str,
    sentinel_context: Any,
    explicit_proxy: str | None,
) -> Any:
    sentinel_header = _get_sentinel_header_for_signup(
        session,
        device_id=device_id,
        flow="authorize_continue",
        request_kind="signup-authorize-continue",
        explicit_proxy=explicit_proxy,
        sentinel_context=sentinel_context,
    )
    headers = _build_protocol_headers(request_kind="", referer=LOGIN_OR_CREATE_ACCOUNT_REFERER)
    headers["openai-sentinel-token"] = sentinel_header
    headers["accept-language"] = _PLATFORM_AUTH0_ACCEPT_LANGUAGE
    headers["sec-ch-ua"] = _PLATFORM_AUTH0_SEC_CH_UA
    cookie_header = _deduped_cookie_header_for_request(session, AUTHORIZE_CONTINUE_URL)
    if cookie_header:
        headers["cookie"] = cookie_header
    return _session_request(
        session,
        "POST",
        AUTHORIZE_CONTINUE_URL,
        explicit_proxy=explicit_proxy,
        request_label="openai-login-init-authorize-continue",
        headers=headers,
        json={"username": {"value": email, "kind": "email"}, "screen_hint": "login_or_signup"},
    )


def _wrap_protocol_error(
    exc: BaseException,
    *,
    stage: str,
    detail: str,
    category: str | None = None,
) -> ProtocolRuntimeError:
    return ensure_protocol_runtime_error(
        exc,
        stage=stage,
        detail=detail,
        category=category,
    )


@contextlib.contextmanager
def _protocol_only_env() -> Iterator[None]:
    managed_keys = (
        PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV,
    )
    previous: dict[str, str | None] = {}
    for key in managed_keys:
        previous[key] = os.environ.get(key)
        os.environ[key] = "0"
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _response_target_url(response: Any) -> str:
    response_url = str(getattr(response, "url", "") or "").strip()
    continue_url = _response_continue_url(response)
    if continue_url:
        try:
            return urllib.parse.urljoin(response_url or AUTH_BASE, continue_url)
        except Exception:
            return continue_url
    return response_url


def _login_session_cookie(session: requests.Session) -> str:
    return _get_session_cookie(
        session,
        "login_session",
        preferred_domains=(".openai.com", "auth.openai.com"),
    )


def _platform_auth0_authorize_response_needs_retry(response: Any) -> bool:
    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
    except Exception:
        status_code = 0
    return bool(
        status_code == 403
        or _response_has_cloudflare_challenge(response)
        or _response_error_code(response).lower() == "invalid_state"
    )


def _submit_platform_auth0_authorize_with_retry(
    *,
    session: requests.Session,
    auth_url: str,
    sentinel_context: Any,
    explicit_proxy: str | None,
) -> tuple[Any, Any]:
    response = _session_request(
        session,
        "GET",
        auth_url,
        explicit_proxy=explicit_proxy,
        request_label="platform-authorize-init",
        timeout=20,
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "accept-language": _PLATFORM_AUTH0_ACCEPT_LANGUAGE,
            "upgrade-insecure-requests": "1",
            "user-agent": str(session.headers.get("user-agent") or DEFAULT_PROTOCOL_USER_AGENT),
            "sec-ch-ua": _PLATFORM_AUTH0_SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-site",
        },
    )
    login_session = _login_session_cookie(session)
    needs_retry = (not login_session) or _platform_auth0_authorize_response_needs_retry(response)
    if needs_retry:
        print(
            "[protocol-small-success] platform authorize retry "
            f"status={getattr(response, 'status_code', 0)} "
            f"login_session={'yes' if login_session else 'no'} "
            f"error_code={_response_error_code(response) or '<none>'} "
            f"challenge={'yes' if _response_has_cloudflare_challenge(response) else 'no'}",
            flush=True,
        )
        sentinel_context, browser_result = _maybe_prime_protocol_auth_session_with_browser(
            session,
            sentinel_context=sentinel_context,
            explicit_proxy=explicit_proxy,
            reason="platform_authorize_init",
        )
        if browser_result is not None:
            response = _session_request(
                session,
                "GET",
                auth_url,
                explicit_proxy=explicit_proxy,
                request_label="platform-authorize-init-retry",
                timeout=20,
                headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "accept-language": _PLATFORM_AUTH0_ACCEPT_LANGUAGE,
                    "upgrade-insecure-requests": "1",
                    "user-agent": str(session.headers.get("user-agent") or DEFAULT_PROTOCOL_USER_AGENT),
                    "sec-ch-ua": _PLATFORM_AUTH0_SEC_CH_UA,
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": "\"Windows\"",
                    "sec-fetch-dest": "document",
                    "sec-fetch-mode": "navigate",
                    "sec-fetch-site": "same-site",
                },
            )
            login_session = _login_session_cookie(session)
    if not login_session:
        raise ProtocolRuntimeError(
            "authorize_init_missing_login_session",
            stage="stage_auth_continue",
            detail="oauth_authorize",
            category="auth_error",
        )
    _raise_if_unexpected_http(
        response,
        expected_statuses={200, 302},
        stage="stage_auth_continue",
        detail="oauth_authorize",
    )
    return sentinel_context, response


def _openai_login_init_error_is_retryable(exc: BaseException) -> bool:
    text = str(exc or "").strip().lower()
    if not text:
        return False
    return any(
        marker in text
        for marker in (
            "authorize_init_missing_login_session",
            "status=403",
            "invalid_state",
            "challenge",
        )
    )


def _openai_login_init_response_needs_retry(response: Any) -> bool:
    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
    except Exception:
        status_code = 0
    return bool(
        status_code == 403
        or _response_has_cloudflare_challenge(response)
        or _response_error_code(response).lower() == "invalid_state"
    )


def _prime_openai_login_session_with_browser(
    *,
    session: requests.Session,
    sentinel_context: Any,
    explicit_proxy: str | None,
    reason: str,
) -> tuple[Any, bool]:
    updated_context, browser_result = _maybe_prime_protocol_auth_session_with_browser(
        session,
        sentinel_context=sentinel_context,
        explicit_proxy=explicit_proxy,
        reason=reason,
    )
    return updated_context, browser_result is not None


def _openai_login_init_entry_with_retry(
    *,
    session: requests.Session,
    sentinel_context: Any,
    explicit_proxy: str | None,
) -> tuple[Any, Any]:
    response = None
    try:
        print(
            "[protocol-small-success] openai login init entry request "
            f"proxy={'direct' if not explicit_proxy else explicit_proxy}",
            flush=True,
        )
        response = _open_login_or_create_account(
            session=session,
            explicit_proxy=explicit_proxy,
        )
    except ProtocolRuntimeError as exc:
        print(
            "[protocol-small-success] openai login init entry error "
            f"retryable={'yes' if _openai_login_init_error_is_retryable(exc) else 'no'} "
            f"err={exc}",
            flush=True,
        )
        if not _openai_login_init_error_is_retryable(exc):
            raise
        sentinel_context, bootstrapped = _prime_openai_login_session_with_browser(
            session=session,
            sentinel_context=sentinel_context,
            explicit_proxy=explicit_proxy,
            reason="openai_login_init_entry_error",
        )
        print(
            "[protocol-small-success] openai login init browser bootstrap "
            f"reason=entry_error bootstrapped={'yes' if bootstrapped else 'no'} "
            f"login_session={'yes' if _login_session_cookie(session) else 'no'}",
            flush=True,
        )
        if not bootstrapped:
            raise
        response = _open_login_or_create_account(
            session=session,
            explicit_proxy=explicit_proxy,
        )
    if response is not None and _openai_login_init_response_needs_retry(response):
        print(
            "[protocol-small-success] openai login init entry response retry "
            f"status={getattr(response, 'status_code', 0)} "
            f"error_code={_response_error_code(response) or '<none>'} "
            f"challenge={'yes' if _response_has_cloudflare_challenge(response) else 'no'}",
            flush=True,
        )
        sentinel_context, bootstrapped = _prime_openai_login_session_with_browser(
            session=session,
            sentinel_context=sentinel_context,
            explicit_proxy=explicit_proxy,
            reason="openai_login_init_entry_response",
        )
        print(
            "[protocol-small-success] openai login init browser bootstrap "
            f"reason=entry_response bootstrapped={'yes' if bootstrapped else 'no'} "
            f"login_session={'yes' if _login_session_cookie(session) else 'no'}",
            flush=True,
        )
        if bootstrapped:
            response = _open_login_or_create_account(
                session=session,
                explicit_proxy=explicit_proxy,
            )
    if response is not None:
        print(
            "[protocol-small-success] openai login init entry final "
            f"status={getattr(response, 'status_code', 0)} "
            f"target_url={_response_target_url(response) or '<none>'} "
            f"login_session={'yes' if _login_session_cookie(session) else 'no'}",
            flush=True,
        )
    return sentinel_context, response


def _ensure_openai_login_session_ready(
    *,
    session: requests.Session,
    sentinel_context: Any,
    explicit_proxy: str | None,
    stage_detail: str,
) -> Any:
    if _login_session_cookie(session):
        print(
            "[protocol-small-success] openai login init login_session ready stage="
            f"{stage_detail} source=cookies",
            flush=True,
        )
        return sentinel_context
    sentinel_context, bootstrapped = _prime_openai_login_session_with_browser(
        session=session,
        sentinel_context=sentinel_context,
        explicit_proxy=explicit_proxy,
        reason=stage_detail,
    )
    print(
        "[protocol-small-success] openai login init login_session bootstrap "
        f"stage={stage_detail} bootstrapped={'yes' if bootstrapped else 'no'} "
        f"login_session={'yes' if _login_session_cookie(session) else 'no'}",
        flush=True,
    )
    if not bootstrapped or not _login_session_cookie(session):
        raise ProtocolRuntimeError(
            "authorize_init_missing_login_session",
            stage="stage_auth_continue",
            detail=stage_detail,
            category="auth_error",
        )
    return sentinel_context


def _submit_openai_login_init_authorize_continue_with_retry(
    *,
    session: requests.Session,
    email: str,
    device_id: str,
    sentinel_context: Any,
    explicit_proxy: str | None,
) -> tuple[Any, Any]:
    response = _submit_authorize_continue_login_or_create_account(
        session=session,
        email=email,
        device_id=device_id,
        sentinel_context=sentinel_context,
        explicit_proxy=explicit_proxy,
    )
    print(
        "[protocol-small-success] openai login init authorize continue "
        f"status={getattr(response, 'status_code', 0)} "
        f"page_type={_extract_page_type(response) or '<none>'} "
        f"error_code={_response_error_code(response) or '<none>'} "
        f"challenge={'yes' if _response_has_cloudflare_challenge(response) else 'no'}",
        flush=True,
    )
    if _openai_login_init_response_needs_retry(response):
        sentinel_context, bootstrapped = _prime_openai_login_session_with_browser(
            session=session,
            sentinel_context=sentinel_context,
            explicit_proxy=explicit_proxy,
            reason="openai_login_init_authorize_continue_retry",
        )
        print(
            "[protocol-small-success] openai login init browser bootstrap "
            f"reason=authorize_continue bootstrapped={'yes' if bootstrapped else 'no'} "
            f"login_session={'yes' if _login_session_cookie(session) else 'no'}",
            flush=True,
        )
        if bootstrapped:
            sentinel_context = _ensure_openai_login_session_ready(
                session=session,
                sentinel_context=sentinel_context,
                explicit_proxy=explicit_proxy,
                stage_detail="openai_login_init_authorize_continue",
            )
            response = _submit_authorize_continue_login_or_create_account(
                session=session,
                email=email,
                device_id=device_id,
                sentinel_context=sentinel_context,
                explicit_proxy=explicit_proxy,
            )
            print(
                "[protocol-small-success] openai login init authorize continue retry "
                f"status={getattr(response, 'status_code', 0)} "
                f"page_type={_extract_page_type(response) or '<none>'} "
                f"error_code={_response_error_code(response) or '<none>'} "
                f"challenge={'yes' if _response_has_cloudflare_challenge(response) else 'no'}",
                flush=True,
            )
    return sentinel_context, response


@contextlib.contextmanager
def _temporary_env_value(name: str, value: str | None) -> Iterator[None]:
    previous = os.environ.get(name)
    try:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def _sentinel_token_lengths(token: str) -> tuple[int, int, bool]:
    try:
        payload = json.loads(str(token or "").strip())
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return (
        len(str(payload.get("t") or "")),
        len(str(payload.get("p") or "")),
        bool(str(payload.get("c") or "").strip()),
    )


def _signup_sentinel_candidate_early_stop_thresholds() -> tuple[int, int]:
    raw_t_len = str(
        os.environ.get("PROTOCOL_SIGNUP_SENTINEL_EARLY_STOP_T_LEN")
        or os.environ.get("PROTOCOL_SIGNUP_SENTINEL_T_LEN_EARLY_STOP")
        or "1200"
    ).strip()
    raw_p_len = str(
        os.environ.get("PROTOCOL_SIGNUP_SENTINEL_EARLY_STOP_P_LEN")
        or os.environ.get("PROTOCOL_SIGNUP_SENTINEL_P_LEN_EARLY_STOP")
        or "600"
    ).strip()
    try:
        t_len = int(raw_t_len)
    except Exception:
        t_len = 1200
    try:
        p_len = int(raw_p_len)
    except Exception:
        p_len = 600
    return max(1, t_len), max(1, p_len)


def _signup_sentinel_candidate_is_good_enough(*, t_len: int, p_len: int, has_c: bool) -> bool:
    min_t_len, min_p_len = _signup_sentinel_candidate_early_stop_thresholds()
    return bool(has_c) and int(t_len) >= min_t_len and int(p_len) >= min_p_len


def _build_signup_sentinel_candidates(
    *,
    session: requests.Session,
    email: str,
    device_id: str,
    explicit_proxy: str | None,
    sentinel_context: Any,
    network_attempt: int = 1,
) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str, int, int, bool]] = []
    persona_values: list[tuple[str, str | None]] = [
        ("current", None),
        ("har1", "har1"),
        ("har2", "har2"),
    ]
    email_modes: list[tuple[str, str | None]] = [
        ("with_email", email),
        ("without_email", None),
    ]
    current_persona_has_strong_candidate = False
    for persona_label, persona_value in persona_values:
        with _temporary_env_value("PROTOCOL_SENTINEL_PERSONA", persona_value):
            candidate_context = sentinel_context
            if persona_label != "current":
                candidate_context = _new_protocol_sentinel_context(
                    session,
                    explicit_proxy=explicit_proxy,
                    user_agent=str(getattr(sentinel_context, "user_agent", "") or DEFAULT_PROTOCOL_USER_AGENT),
                )
            for email_mode_label, browser_email in email_modes:
                try:
                    token = _get_sentinel_header_for_signup(
                        session,
                        device_id=device_id,
                        flow="username_password_create",
                        request_kind="signup-user-register",
                        explicit_proxy=explicit_proxy,
                        sentinel_context=candidate_context,
                        browser_email=browser_email,
                    )
                except Exception as exc:
                    print(
                        "[protocol-small-success] sentinel candidate failed "
                        f"persona={persona_label} email_mode={email_mode_label} err={exc}",
                        flush=True,
                    )
                    continue
                t_len, p_len, has_c = _sentinel_token_lengths(token)
                print(
                    "[protocol-small-success] sentinel candidate "
                    f"persona={persona_label} email_mode={email_mode_label} "
                    f"t_len={t_len} p_len={p_len} has_c={has_c}",
                    flush=True,
                )
                candidates.append(
                    (
                        f"{persona_label}:{email_mode_label}",
                        token,
                        t_len,
                        p_len,
                        has_c,
                    )
                )
                if (
                    persona_label == "current"
                    and _signup_sentinel_candidate_is_good_enough(t_len=t_len, p_len=p_len, has_c=has_c)
                ):
                    current_persona_has_strong_candidate = True
            if persona_label == "current" and current_persona_has_strong_candidate:
                print(
                    "[protocol-small-success] sentinel candidate early stop "
                    f"persona={persona_label} thresholds={_signup_sentinel_candidate_early_stop_thresholds()}",
                    flush=True,
                )
                break
        if persona_label == "current" and current_persona_has_strong_candidate:
            break
    preferred_label_order = {
        "har1:without_email": 0,
        "har2:without_email": 1,
        "current:without_email": 2,
        "har1:with_email": 3,
        "har2:with_email": 4,
        "current:with_email": 5,
    }
    candidates.sort(
        key=lambda item: (
            preferred_label_order.get(item[0], 999),
            -item[2],
            -item[3],
            -(1 if item[4] else 0),
        ),
    )
    deduped: list[tuple[str, str]] = []
    seen_tokens: set[str] = set()
    for label, token, _, _, _ in candidates:
        if token in seen_tokens:
            continue
        seen_tokens.add(token)
        deduped.append((label, token))
    return deduped


def _classify_protocol_small_success(*, response: Any, fallback_page_type: str = "") -> tuple[str, str]:
    page_type = str(fallback_page_type or _extract_page_type(response) or "").strip()
    final_url = _response_target_url(response)
    normalized_url = final_url.lower()
    if "platform.openai.com/welcome" in normalized_url:
        return "platform_welcome", final_url
    if "platform.openai.com/auth/callback" in normalized_url and "code=" in normalized_url:
        return "platform_callback", final_url
    if "chatgpt.com/api/auth/callback/openai" in normalized_url:
        return "chatgpt_callback", final_url
    if _response_has_phone_wall(response):
        return "phone_wall", final_url
    return page_type or "registered", final_url


def _build_protocol_small_success_result(
    *,
    output_dir: str | None,
    mailbox: Any,
    password: str,
    first_name: str,
    last_name: str,
    birthdate: str,
    page_type: str,
    final_url: str,
    platform_auth_context: dict[str, Any] | None = None,
) -> PlatformProtocolRegistrationResult:
    storage_path = persist_small_success_record(
        output_dir=output_dir,
        outcome="small_success",
        email=mailbox.email,
        password=password,
        mailbox_provider=mailbox.provider,
        mailbox_access_key=mailbox.ref,
        mailbox_ref=mailbox.ref,
        mailbox_session_id=mailbox.session_id,
        first_name=first_name,
        last_name=last_name,
        birthdate=birthdate,
        page_type=page_type,
        final_url=final_url,
        browser_backend="",
        source="protocol_small_success",
        registration_mode="protocol-platform-first",
        extra_payload={
            "platformAuth": {
                "clientId": _PLATFORM_AUTH0_CLIENT_ID,
                "redirectUri": _PLATFORM_AUTH0_REDIRECT_URI,
                "audience": _PLATFORM_AUTH0_AUDIENCE,
                "scope": _PLATFORM_AUTH0_SCOPE,
                "deviceId": str((platform_auth_context or {}).get("deviceId") or "").strip(),
                "codeVerifier": str((platform_auth_context or {}).get("codeVerifier") or "").strip(),
                "state": str((platform_auth_context or {}).get("state") or "").strip(),
                "nonce": str((platform_auth_context or {}).get("nonce") or "").strip(),
                "auth0Client": str((platform_auth_context or {}).get("auth0Client") or "").strip(),
            }
        },
    )
    return PlatformProtocolRegistrationResult(
        outcome="small_success",
        email=mailbox.email,
        password=password,
        email_service_provider="EasyEmail",
        mailbox_provider=mailbox.provider,
        mailbox_access_key=mailbox.ref,
        mailbox_ref=mailbox.ref,
        mailbox_session_id=mailbox.session_id,
        first_name=first_name,
        last_name=last_name,
        birthdate=birthdate,
        page_type=str(page_type or ""),
        final_url=str(final_url or ""),
        storage_path=storage_path,
        final_stage="small_success",
    )


def _update_openai_login_init_state(
    *,
    source_path: Path,
    mailbox: Any,
    page_type: str,
    final_url: str,
    explicit_proxy: str | None,
) -> None:
    payload = load_json_payload(source_path)
    payload["mailboxProvider"] = str(getattr(mailbox, "provider", "") or "").strip()
    payload["mailboxAccessKey"] = str(getattr(mailbox, "ref", "") or "").strip()
    payload["mailboxRef"] = str(getattr(mailbox, "ref", "") or "").strip()
    payload["mailboxSessionId"] = str(getattr(mailbox, "session_id", "") or "").strip()
    payload["personalWorkspaceInitialized"] = True
    payload["personalWorkspaceInitializedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload["openaiLoginInit"] = {
        "ok": True,
        "status": "completed",
        "method": "openai_login_email_otp",
        "pageType": str(page_type or "").strip(),
        "finalUrl": str(final_url or "").strip(),
        "proxyUrl": str(explicit_proxy or "").strip(),
        "mailboxProvider": str(getattr(mailbox, "provider", "") or "").strip(),
        "mailboxRef": str(getattr(mailbox, "ref", "") or "").strip(),
        "mailboxSessionId": str(getattr(mailbox, "session_id", "") or "").strip(),
    }
    source_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def run_protocol_openai_login_init_from_path(
    *,
    source_path: str | Path,
    explicit_proxy: str | None = None,
) -> dict[str, Any]:
    seed_path = Path(source_path).resolve()
    seed_payload = load_json_payload(seed_path)
    email = str(seed_payload.get("email") or "").strip()
    mailbox_ref = str(seed_payload.get("mailboxRef") or seed_payload.get("mailbox_ref") or "").strip()
    mailbox_session_id = str(
        seed_payload.get("mailboxSessionId") or seed_payload.get("mailbox_session_id") or ""
    ).strip()
    if not email:
        raise RuntimeError("protocol_openai_login_init_requires_email")
    existing_init = seed_payload.get("openaiLoginInit")
    if (
        bool(seed_payload.get("personalWorkspaceInitialized"))
        and isinstance(existing_init, dict)
        and str(existing_init.get("status") or "").strip().lower() == "completed"
    ):
        return {
            "ok": True,
            "status": "already_initialized",
            "email": email,
            "sourcePath": str(seed_path),
            "pageType": str(existing_init.get("pageType") or "").strip(),
            "finalUrl": str(existing_init.get("finalUrl") or "").strip(),
            "mailboxRef": mailbox_ref,
            "mailboxSessionId": mailbox_session_id,
        }

    verify_tls = env_flag("PROTOCOL_HTTP_VERIFY_TLS", False)
    impersonate = (os.environ.get("PROTOCOL_HTTP_IMPERSONATE") or "chrome").strip() or "chrome"
    normalized_proxy = normalize_proxy_env_url(explicit_proxy) or None
    mailbox = None
    session = None
    try:
        mailbox = resolve_mailbox(
            preallocated_email=email,
            preallocated_session_id=mailbox_session_id or None,
            preallocated_mailbox_ref=mailbox_ref or None,
        )
        with _protocol_only_env():
            with _temporary_env_value(PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV, "1"):
                with _temporary_env_value(PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV, "1"):
                    with _temporary_env_value("PROTOCOL_ENABLE_BROWSER_AUTHORIZE_CONTINUE_FALLBACK", "1"):
                        with flow_network_env():
                            session = requests.Session(
                                impersonate=impersonate,
                                timeout=30,
                                verify=verify_tls,
                            )
                            session.headers.update({"user-agent": DEFAULT_PROTOCOL_USER_AGENT})
                            device_id = str(uuid.uuid4())
                            seed_device_cookie(session, device_id)
                            sentinel_context = _clone_protocol_sentinel_context(
                                _new_protocol_sentinel_context(
                                    session,
                                    explicit_proxy=normalized_proxy,
                                    user_agent=DEFAULT_PROTOCOL_USER_AGENT,
                                ),
                                device_id=device_id,
                            )
                            try:
                                _open_platform_login(session=session, explicit_proxy=normalized_proxy)
                            except ProtocolRuntimeError as exc:
                                if not _openai_login_init_error_is_retryable(exc):
                                    raise
                                sentinel_context, bootstrapped = _prime_openai_login_session_with_browser(
                                    session=session,
                                    sentinel_context=sentinel_context,
                                    explicit_proxy=normalized_proxy,
                                    reason="openai_login_init_platform_login",
                                )
                                if not bootstrapped:
                                    raise
                                _open_platform_login(session=session, explicit_proxy=normalized_proxy)

                            otp_min_mail_id = 0
                            try:
                                otp_min_mail_id = get_mailbox_latest_message_id(
                                    mailbox_ref=mailbox.ref,
                                    session_id=mailbox.session_id,
                                    mailcreate_base_url=MAILCREATE_BASE_URL,
                                    mailcreate_custom_auth=MAILCREATE_CUSTOM_AUTH,
                                )
                            except Exception:
                                otp_min_mail_id = 0

                            sentinel_context, authorize_response = _openai_login_init_entry_with_retry(
                                session=session,
                                sentinel_context=sentinel_context,
                                explicit_proxy=normalized_proxy,
                            )
                            sentinel_context = _ensure_openai_login_session_ready(
                                session=session,
                                sentinel_context=sentinel_context,
                                explicit_proxy=normalized_proxy,
                                stage_detail="openai_login_init_entry",
                            )

                            sentinel_context, login_response = _submit_openai_login_init_authorize_continue_with_retry(
                                session=session,
                                email=mailbox.email,
                                device_id=device_id,
                                sentinel_context=sentinel_context,
                                explicit_proxy=normalized_proxy,
                            )
                            page_type = str(_extract_page_type(login_response) or "").strip()
                            if _response_has_phone_wall(login_response):
                                raise ProtocolRuntimeError(
                                    "openai_login_init_phone_wall",
                                    stage="stage_auth_continue",
                                    detail="openai_login_init_phone_wall",
                                    category="flow_error",
                                )
                            _raise_if_unexpected_http(
                                login_response,
                                expected_statuses={200},
                                stage="stage_auth_continue",
                                detail="openai_login_init_authorize_continue",
                            )

                            if page_type == "email_otp_send":
                                _send_email_otp(
                                    session,
                                    explicit_proxy=normalized_proxy,
                                    header_builder=sentinel_context,
                                )
                                page_type = "email_otp_verification"
                            elif page_type != "email_otp_verification":
                                classified_page_type, classified_final_url = _classify_protocol_small_success(
                                    response=login_response,
                                    fallback_page_type=page_type,
                                )
                                raise ProtocolRuntimeError(
                                    f"openai_login_init_requires_email_otp page_type={classified_page_type or 'unknown'} "
                                    f"final_url={classified_final_url or '<none>'}",
                                    stage="stage_auth_continue",
                                    detail="openai_login_init_page_type",
                                    category="flow_error",
                                )

                            code = wait_openai_code(
                                mailbox_ref=mailbox.ref,
                                session_id=mailbox.session_id,
                                mailcreate_base_url=MAILCREATE_BASE_URL,
                                mailcreate_custom_auth=MAILCREATE_CUSTOM_AUTH,
                                timeout_seconds=max(60, _resolve_create_openai_account_flow_timeout_seconds()),
                                min_mail_id=otp_min_mail_id,
                            )
                            code = str(code or "").strip()
                            if not code:
                                raise ProtocolRuntimeError(
                                    "otp_timeout",
                                    stage="stage_otp_validate",
                                    detail="email_otp_wait",
                                    category="otp_timeout",
                                )

                            otp_validate_response = _submit_email_otp_validate(
                                session=session,
                                code=code,
                                device_id=device_id,
                                sentinel_context=sentinel_context,
                                explicit_proxy=normalized_proxy,
                            )
                            otp_page_type = str(_extract_page_type(otp_validate_response) or "").strip()
                            if _response_has_phone_wall(otp_validate_response):
                                raise ProtocolRuntimeError(
                                    "openai_login_init_phone_wall",
                                    stage="stage_otp_validate",
                                    detail="openai_login_init_phone_wall",
                                    category="flow_error",
                                )
                            _raise_if_unexpected_http(
                                otp_validate_response,
                                expected_statuses={200},
                                stage="stage_otp_validate",
                                detail="email_otp_validate",
                            )
                            final_page_type, final_url = _classify_protocol_small_success(
                                response=otp_validate_response,
                                fallback_page_type=otp_page_type or page_type,
                            )
                            _update_openai_login_init_state(
                                source_path=seed_path,
                                mailbox=mailbox,
                                page_type=final_page_type,
                                final_url=final_url,
                                explicit_proxy=normalized_proxy,
                            )
                            return {
                                "ok": True,
                                "status": "completed",
                                "email": email,
                                "sourcePath": str(seed_path),
                                "pageType": str(final_page_type or "").strip(),
                                "finalUrl": str(final_url or "").strip(),
                                "mailboxRef": str(mailbox.ref or "").strip(),
                                "mailboxSessionId": str(mailbox.session_id or "").strip(),
                            }
    except ProtocolRuntimeError:
        raise
    except Exception as exc:
        raise _wrap_protocol_error(
            exc,
            stage="stage_other",
            detail="openai_login_init",
            category="flow_error",
        ) from exc
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def _persist_protocol_attempt_diagnostics(
    storage_path: str | None,
    *,
    diagnostics: dict[str, Any] | None,
) -> None:
    if not storage_path or not diagnostics:
        return
    path = Path(str(storage_path or "").strip())
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    payload["protocolAttemptDiagnostics"] = diagnostics
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _cookie_header_excluding_names(cookie_header: str | None, *excluded_names: str) -> str:
    normalized = str(cookie_header or "").strip()
    if not normalized:
        return ""
    excluded = {str(name or "").strip() for name in excluded_names if str(name or "").strip()}
    parts: list[str] = []
    for part in normalized.split(";"):
        token = str(part or "").strip()
        if not token or "=" not in token:
            continue
        name = token.split("=", 1)[0].strip()
        if not name or name in excluded:
            continue
        parts.append(token)
    return "; ".join(parts)


def _submit_user_register_protocol(
    *,
    session: requests.Session,
    email: str,
    password: str,
    device_id: str,
    sentinel_context: Any,
    explicit_proxy: str | None,
    network_attempt: int,
    attempt_history: list[dict[str, Any]] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    passkey_false_header = json.dumps(
        {
            "conditionalCreate": False,
            "conditionalGet": False,
            "relatedOrigins": False,
        },
        separators=(",", ":"),
    )
    request_variants: list[tuple[str, str | None, str | None]] = [
        ("no_cookie", None, None),
        ("minimal_cookie", _minimal_user_register_cookie_header(session), None),
        ("no_cookie_passkey_false", None, passkey_false_header),
        ("deduped_cookie", _deduped_cookie_header_for_request(session, USER_REGISTER_URL), None),
        ("minimal_cookie_passkey_false", _minimal_user_register_cookie_header(session), passkey_false_header),
    ]
    last_response: Any = None
    winning_attempt: dict[str, Any] | None = None
    sentinel_candidates = _build_signup_sentinel_candidates(
        session=session,
        email=email,
        device_id=device_id,
        explicit_proxy=explicit_proxy,
        sentinel_context=sentinel_context,
        network_attempt=network_attempt,
    )
    ordered_attempts: list[tuple[str, str, str | None, str | None]] = []
    for sentinel_label, sentinel_header in sentinel_candidates:
        for variant_name, cookie_header, passkey_header in request_variants:
            ordered_attempts.append((sentinel_label, sentinel_header, cookie_header, passkey_header))
    variant_name_by_headers = {
        (variant_cookie_header, variant_passkey_header): name
        for name, variant_cookie_header, variant_passkey_header in request_variants
    }

    for candidate_index, (sentinel_label, sentinel_header, cookie_header, passkey_header) in enumerate(ordered_attempts, start=1):
        t_len, p_len, has_c = _sentinel_token_lengths(sentinel_header)
        variant_name = variant_name_by_headers.get((cookie_header, passkey_header), "unknown")
        attempt_meta = {
            "networkAttempt": int(network_attempt),
            "candidateIndex": int(candidate_index),
            "sentinelLabel": str(sentinel_label or ""),
            "variant": str(variant_name or ""),
            "tLen": int(t_len),
            "pLen": int(p_len),
            "hasC": bool(has_c),
            "cookieHeaderLen": len(cookie_header or ""),
            "passkey": bool(passkey_header),
            "cookieSummary": _protocol_auth_cookie_summary(session),
        }
        headers = _build_protocol_headers(request_kind="", referer=CREATE_ACCOUNT_PASSWORD_REFERER)
        headers["openai-sentinel-token"] = sentinel_header
        if cookie_header:
            headers["cookie"] = cookie_header
        if passkey_header:
            headers["ext-passkey-client-capabilities"] = passkey_header
        print(
            "[protocol-small-success] user_register attempt "
            f"sentinel={sentinel_label} t_len={t_len} p_len={p_len} has_c={has_c} "
            f"variant={variant_name} email={email} "
            f"cookie_header_len={len(cookie_header or '')} "
            f"passkey={'present' if passkey_header else 'missing'} "
            f"cookie_summary={_protocol_auth_cookie_summary(session)}",
            flush=True,
        )
        response = _session_request(
            session,
            "POST",
            USER_REGISTER_URL,
            explicit_proxy=explicit_proxy,
            request_label=f"protocol-small-success-user-register-{sentinel_label}-{variant_name}",
            headers=headers,
            json={"password": password, "username": email},
        )
        last_response = response
        status_code = int(getattr(response, "status_code", 0) or 0)
        attempt_meta["statusCode"] = status_code
        if attempt_history is not None:
            attempt_history.append(dict(attempt_meta))
        if status_code < 400:
            winning_attempt = dict(attempt_meta)
            return response, winning_attempt
        print(
            "[protocol-small-success] user_register attempt failed "
            f"sentinel={sentinel_label} variant={variant_name} status={status_code} "
            f"body={_response_preview(response, 220)}",
            flush=True,
        )
    if sentinel_candidates:
        browser_sentinel_label, browser_sentinel_header = sentinel_candidates[0]
        browser_t_len, browser_p_len, browser_has_c = _sentinel_token_lengths(browser_sentinel_header)
        browser_response = _submit_browser_native_signup_user_register(
            session=session,
            explicit_proxy=explicit_proxy,
            email=email,
            password=password,
            sentinel_token=browser_sentinel_header,
            passkey_capabilities_header=passkey_false_header,
            sentinel_context=sentinel_context,
        )
        if browser_response is not None:
            status_code = int(getattr(browser_response, "status_code", 0) or 0)
            browser_attempt = {
                "networkAttempt": int(network_attempt),
                "candidateIndex": int(len(ordered_attempts) + 1),
                "sentinelLabel": str(browser_sentinel_label or ""),
                "variant": "browser_native",
                "tLen": int(browser_t_len),
                "pLen": int(browser_p_len),
                "hasC": bool(browser_has_c),
                "cookieHeaderLen": 0,
                "passkey": True,
                "cookieSummary": _protocol_auth_cookie_summary(session),
                "statusCode": status_code,
            }
            if attempt_history is not None:
                attempt_history.append(dict(browser_attempt))
            last_response = browser_response
            if status_code < 400:
                return browser_response, dict(browser_attempt)
            print(
                "[protocol-small-success] user_register browser fallback failed "
                f"sentinel={browser_sentinel_label} status={status_code} "
                f"body={_response_preview(browser_response, 220)}",
                flush=True,
            )
    return last_response, winning_attempt


def _should_retry_after_user_register_error(exc: ProtocolRuntimeError, *, time_remaining_seconds: int) -> bool:
    if str(getattr(exc, "detail", "") or "").strip() != "user_register":
        return False
    if str(getattr(exc, "stage", "") or "").strip() != "stage_create_account":
        return False
    if time_remaining_seconds < 25:
        return False
    return True


def _should_retry_after_authorize_error(exc: ProtocolRuntimeError, *, time_remaining_seconds: int) -> bool:
    if str(getattr(exc, "detail", "") or "").strip() != "oauth_authorize":
        return False
    if str(getattr(exc, "stage", "") or "").strip() != "stage_auth_continue":
        return False
    if time_remaining_seconds < 25:
        return False
    return True


def _should_retry_after_network_exception(exc: BaseException, *, time_remaining_seconds: int) -> bool:
    if time_remaining_seconds < 25:
        return False
    message = str(exc or "").strip().lower()
    if not message:
        return False
    retry_markers = (
        "connection closed abruptly",
        "handshake operation timed out",
        "proxy connect aborted",
        "proxy connect failed",
        "operation timed out",
        "curl: (56)",
        "curl: (28)",
        "curl: (7)",
        "curl: (35)",
        "urlopen error",
    )
    return any(marker in message for marker in retry_markers)


def run_protocol_small_success_once(
    *,
    output_dir: str | None = None,
    preallocated_email: str | None = None,
    preallocated_session_id: str | None = None,
    preallocated_mailbox_ref: str | None = None,
    explicit_proxy: str | None = None,
) -> PlatformProtocolRegistrationResult:
    flow_timeout_seconds = max(_resolve_create_openai_account_flow_timeout_seconds(), 480)
    flow_started_monotonic = time.monotonic()
    output_root = output_dir or DEFAULT_REGISTER_PROTOCOL_OUTPUT_DIR
    mailbox = None
    retain_mailbox = False
    max_network_attempts = 1 if normalize_proxy_env_url(explicit_proxy) else 2

    def _remaining_flow_seconds(*, minimum_seconds: int = 1) -> int:
        remaining = int(flow_timeout_seconds - (time.monotonic() - flow_started_monotonic))
        if remaining < minimum_seconds:
            raise ProtocolRuntimeError(
                "flow_timeout_exceeded",
                stage="stage_other",
                detail="protocol_small_success_timeout",
                category="flow_error",
            )
        return remaining

    def _remaining_flow_seconds_soft() -> int:
        return int(flow_timeout_seconds - (time.monotonic() - flow_started_monotonic))

    mailbox = resolve_mailbox(
        preallocated_email=preallocated_email,
        preallocated_session_id=preallocated_session_id,
        preallocated_mailbox_ref=preallocated_mailbox_ref,
    )
    print(
        "[protocol-small-success] mailbox "
        f"provider={mailbox.provider} email={mailbox.email} session_id={mailbox.session_id}",
        flush=True,
    )
    password = generate_pwd()
    first_name = ""
    last_name = ""
    birthdate = ""
    verify_tls = env_flag("PROTOCOL_HTTP_VERIFY_TLS", False)
    impersonate = (os.environ.get("PROTOCOL_HTTP_IMPERSONATE") or "chrome").strip() or "chrome"
    task_explicit_proxy = normalize_proxy_env_url(explicit_proxy) or None

    try:
        with _protocol_only_env():
            with flow_network_env():
                user_register_attempt_history: list[dict[str, Any]] = []
                for network_attempt in range(1, max_network_attempts + 1):
                    session = None
                    try:
                        flow_proxy_cm = (
                            contextlib.nullcontext(SimpleNamespace(proxy_url=task_explicit_proxy))
                            if task_explicit_proxy
                            else lease_flow_proxy(
                                flow_name="protocol_small_success",
                                metadata={
                                    "email": mailbox.email,
                                    "mailboxProvider": mailbox.provider,
                                    "networkAttempt": str(network_attempt),
                                },
                                probe_url=PLATFORM_LOGIN_URL,
                                probe_expected_statuses={200},
                            )
                        )
                        with flow_proxy_cm as flow_proxy:
                            explicit_proxy = task_explicit_proxy or normalize_proxy_env_url(flow_proxy.proxy_url) or None
                            session = requests.Session(
                                impersonate=impersonate,
                                timeout=30,
                                verify=verify_tls,
                            )
                            session.headers.update({"user-agent": DEFAULT_PROTOCOL_USER_AGENT})
                            device_id = str(uuid.uuid4())
                            print(
                                "[protocol-small-success] network attempt "
                                f"index={network_attempt} email={mailbox.email} "
                                f"proxy={'direct' if not explicit_proxy else explicit_proxy}",
                                flush=True,
                            )
                            seed_device_cookie(session, device_id)
                            _open_platform_login(session=session, explicit_proxy=explicit_proxy)
                            sentinel_context = _clone_protocol_sentinel_context(
                                _new_protocol_sentinel_context(
                                    session,
                                    explicit_proxy=explicit_proxy,
                                    user_agent=DEFAULT_PROTOCOL_USER_AGENT,
                                ),
                                device_id=device_id,
                            )

                            otp_min_mail_id = 0
                            try:
                                otp_min_mail_id = get_mailbox_latest_message_id(
                                    mailbox_ref=mailbox.ref,
                                    session_id=mailbox.session_id,
                                    mailcreate_base_url=MAILCREATE_BASE_URL,
                                    mailcreate_custom_auth=MAILCREATE_CUSTOM_AUTH,
                                )
                            except Exception:
                                otp_min_mail_id = 0

                            platform_auth_context = _build_platform_auth0_authorize_context(
                                email=mailbox.email,
                                device_id=device_id,
                            )
                            auth_url = str(platform_auth_context.get("url") or "").strip()
                            try:
                                setattr(session, "_new_protocol_signup_oauth_auth_url", auth_url)
                            except Exception:
                                pass
                            _remaining_flow_seconds()
                            sentinel_context, authorize_response = _submit_platform_auth0_authorize_with_retry(
                                session=session,
                                auth_url=auth_url,
                                sentinel_context=sentinel_context,
                                explicit_proxy=explicit_proxy,
                            )

                            auth_session_payload = _decode_current_auth_session_payload(session)
                            auth_username = ""
                            auth_username_value = auth_session_payload.get("username")
                            if isinstance(auth_username_value, dict):
                                auth_username = str(auth_username_value.get("value") or "").strip().lower()
                            original_screen_hint = str(auth_session_payload.get("original_screen_hint") or "").strip()
                            authorize_target_url = _response_target_url(authorize_response)
                            reached_password_page = "create-account/password" in str(authorize_target_url or "").lower()
                            auth_state_matches_browser = (
                                reached_password_page
                                and original_screen_hint == "login_or_signup"
                                and auth_username == str(mailbox.email or "").strip().lower()
                            )
                            print(
                                "[protocol-small-success] auth session after authorize "
                                f"email={mailbox.email} target_url={authorize_target_url} "
                                f"screen_hint={original_screen_hint or '<none>'} "
                                f"auth_username={auth_username or '<none>'} "
                                f"matches_browser={'yes' if auth_state_matches_browser else 'no'} "
                                f"cookie_summary={_protocol_auth_cookie_summary(session)}",
                                flush=True,
                            )

                            signup_response = authorize_response
                            if not auth_state_matches_browser:
                                signup_response = _submit_authorize_continue_login_or_signup(
                                    session=session,
                                    email=mailbox.email,
                                    device_id=device_id,
                                    sentinel_context=sentinel_context,
                                    explicit_proxy=explicit_proxy,
                                )
                            _remaining_flow_seconds()
                            signup_page_type = _extract_page_type(signup_response)
                            if _response_has_phone_wall(signup_response):
                                retain_mailbox = True
                                surface, final_url = _classify_protocol_small_success(
                                    response=signup_response,
                                    fallback_page_type=signup_page_type,
                                )
                                return _build_protocol_small_success_result(
                                    output_dir=output_root,
                                    mailbox=mailbox,
                                    password=password,
                                    first_name=first_name,
                                    last_name=last_name,
                                    birthdate=birthdate,
                                    page_type=surface,
                                    final_url=final_url,
                                    platform_auth_context=platform_auth_context,
                                )
                            _raise_if_unexpected_http(
                                signup_response,
                                expected_statuses={200},
                                stage="stage_auth_continue",
                                detail="authorize_continue",
                            )
                            if signup_page_type in ("email_otp_send", "email_otp_verification"):
                                raise ProtocolRuntimeError(
                                    f"existing_account_detected page_type={signup_page_type}",
                                    stage="stage_create_account",
                                    detail="authorize_continue_existing_account",
                                    category="flow_error",
                                )

                            register_response, winning_user_register_attempt = _submit_user_register_protocol(
                                session=session,
                                email=mailbox.email,
                                password=password,
                                device_id=device_id,
                                sentinel_context=sentinel_context,
                                explicit_proxy=explicit_proxy,
                                network_attempt=network_attempt,
                                attempt_history=user_register_attempt_history,
                            )
                            _remaining_flow_seconds()
                            register_page_type = _extract_page_type(register_response)
                            print(
                                "[protocol-small-success] user_register response "
                                f"email={mailbox.email} page_type={register_page_type or '<none>'} "
                                f"final_url={_response_target_url(register_response)} "
                                f"network_attempt={network_attempt}",
                                flush=True,
                            )
                            if _response_has_phone_wall(register_response):
                                retain_mailbox = True
                                surface, final_url = _classify_protocol_small_success(
                                    response=register_response,
                                    fallback_page_type=register_page_type,
                                )
                                result = _build_protocol_small_success_result(
                                    output_dir=output_root,
                                    mailbox=mailbox,
                                    password=password,
                                    first_name=first_name,
                                    last_name=last_name,
                                    birthdate=birthdate,
                                    page_type=surface,
                                    final_url=final_url,
                                    platform_auth_context=platform_auth_context,
                                )
                                _persist_protocol_attempt_diagnostics(
                                    result.storage_path,
                                    diagnostics={
                                        "winningUserRegisterAttempt": winning_user_register_attempt,
                                        "userRegisterAttempts": user_register_attempt_history,
                                    },
                                )
                                return result
                            _raise_if_unexpected_http(
                                register_response,
                                expected_statuses={200},
                                stage="stage_create_account",
                                detail="user_register",
                            )

                            try:
                                _send_email_otp(
                                    session,
                                    explicit_proxy=explicit_proxy,
                                    header_builder=sentinel_context,
                                )
                            except Exception as exc:
                                print(
                                    "[protocol-small-success] email_otp send skipped "
                                    f"email={mailbox.email} err={exc}",
                                    flush=True,
                                )

                            code = wait_openai_code(
                                mailbox_ref=mailbox.ref,
                                session_id=mailbox.session_id,
                                mailcreate_base_url=MAILCREATE_BASE_URL,
                                mailcreate_custom_auth=MAILCREATE_CUSTOM_AUTH,
                                timeout_seconds=max(1, _remaining_flow_seconds()),
                                min_mail_id=otp_min_mail_id,
                            )
                            _remaining_flow_seconds()
                            code = str(code or "").strip()
                            if not code:
                                raise ProtocolRuntimeError(
                                    "otp_timeout",
                                    stage="stage_otp_validate",
                                    detail="email_otp_wait",
                                    category="otp_timeout",
                                )

                            otp_validate_response = _submit_email_otp_validate(
                                session=session,
                                code=code,
                                device_id=device_id,
                                sentinel_context=sentinel_context,
                                explicit_proxy=explicit_proxy,
                            )
                            _remaining_flow_seconds()
                            otp_page_type = _extract_page_type(otp_validate_response)
                            if _response_has_phone_wall(otp_validate_response):
                                retain_mailbox = True
                                surface, final_url = _classify_protocol_small_success(
                                    response=otp_validate_response,
                                    fallback_page_type=otp_page_type,
                                )
                                result = _build_protocol_small_success_result(
                                    output_dir=output_root,
                                    mailbox=mailbox,
                                    password=password,
                                    first_name=first_name,
                                    last_name=last_name,
                                    birthdate=birthdate,
                                    page_type=surface,
                                    final_url=final_url,
                                    platform_auth_context=platform_auth_context,
                                )
                                _persist_protocol_attempt_diagnostics(
                                    result.storage_path,
                                    diagnostics={
                                        "winningUserRegisterAttempt": winning_user_register_attempt,
                                        "userRegisterAttempts": user_register_attempt_history,
                                    },
                                )
                                return result
                            _raise_if_unexpected_http(
                                otp_validate_response,
                                expected_statuses={200},
                                stage="stage_otp_validate",
                                detail="email_otp_validate",
                            )

                            first_name, last_name = generate_name()
                            birthdate = _random_birthdate()
                            create_account_response = _submit_create_account(
                                session=session,
                                name=f"{first_name} {last_name}",
                                birthdate=birthdate,
                                device_id=device_id,
                                sentinel_context=sentinel_context,
                                explicit_proxy=explicit_proxy,
                            )
                            _remaining_flow_seconds()
                            create_page_type = _extract_page_type(create_account_response)
                            _raise_if_unexpected_http(
                                create_account_response,
                                expected_statuses={200},
                                stage="stage_create_account",
                                detail="create_account",
                            )
                            surface, final_url = _classify_protocol_small_success(
                                response=create_account_response,
                                fallback_page_type=create_page_type,
                            )
                            retain_mailbox = True
                            result = _build_protocol_small_success_result(
                                output_dir=output_root,
                                mailbox=mailbox,
                                password=password,
                                first_name=first_name,
                                last_name=last_name,
                                birthdate=birthdate,
                                page_type=surface,
                                final_url=final_url,
                                platform_auth_context=platform_auth_context,
                            )
                            _persist_protocol_attempt_diagnostics(
                                result.storage_path,
                                diagnostics={
                                    "winningUserRegisterAttempt": winning_user_register_attempt,
                                    "userRegisterAttempts": user_register_attempt_history,
                                },
                            )
                            return result
                    except ProtocolRuntimeError as attempt_exc:
                        if _should_retry_after_authorize_error(
                            attempt_exc,
                            time_remaining_seconds=_remaining_flow_seconds_soft(),
                        ) and network_attempt < max_network_attempts:
                            print(
                                "[protocol-small-success] retrying with fresh session "
                                f"after authorize_missing_login_session email={mailbox.email} "
                                f"network_attempt={network_attempt}",
                                flush=True,
                            )
                            continue
                        if _should_retry_after_user_register_error(
                            attempt_exc,
                            time_remaining_seconds=_remaining_flow_seconds_soft(),
                        ) and network_attempt < max_network_attempts:
                            print(
                                "[protocol-small-success] retrying with fresh session "
                                f"after user_register_400 email={mailbox.email} "
                                f"network_attempt={network_attempt}",
                                flush=True,
                            )
                            continue
                        raise
                    except Exception as attempt_exc:
                        if _should_retry_after_network_exception(
                            attempt_exc,
                            time_remaining_seconds=_remaining_flow_seconds_soft(),
                        ) and network_attempt < max_network_attempts:
                            print(
                                "[protocol-small-success] retrying with fresh session "
                                f"after transient network failure email={mailbox.email} "
                                f"network_attempt={network_attempt} err={attempt_exc}",
                                flush=True,
                            )
                            continue
                        raise
                    finally:
                        try:
                            if session is not None:
                                session.close()
                        except Exception:
                            pass
    except ProtocolRuntimeError as exc:
        raise _augment_create_openai_account_error(exc=exc, mailbox=mailbox) from exc
    except Exception as exc:
        wrapped = _wrap_protocol_error(
            exc,
            stage="stage_other",
            detail="protocol_small_success",
            category="flow_error",
        )
        raise _augment_create_openai_account_error(exc=wrapped, mailbox=mailbox) from exc
    finally:
        if mailbox is not None and not retain_mailbox:
            try:
                release_mailbox(
                    mailbox_ref=getattr(mailbox, "ref", None),
                    session_id=getattr(mailbox, "session_id", None),
                    reason="protocol_small_success_cleanup",
                )
            except Exception:
                pass


def run_protocol_small_success_from_path(
    *,
    output_dir: str | None = None,
    preallocated_email: str | None = None,
    preallocated_session_id: str | None = None,
    preallocated_mailbox_ref: str | None = None,
    explicit_proxy: str | None = None,
) -> PlatformProtocolRegistrationResult:
    return run_protocol_small_success_once(
        output_dir=output_dir,
        preallocated_email=preallocated_email,
        preallocated_session_id=preallocated_session_id,
        preallocated_mailbox_ref=preallocated_mailbox_ref,
        explicit_proxy=explicit_proxy,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pure protocol small-success registration flow.")
    parser.add_argument("--output-dir", default="", help="Optional output dir.")
    parser.add_argument("--email", default="", help="Optional preallocated email.")
    parser.add_argument("--session-id", default="", help="Optional preallocated mailbox session id.")
    parser.add_argument("--mailbox-ref", default="", help="Optional preallocated mailbox ref.")
    parser.add_argument("--proxy", default="", help="Optional explicit proxy URL.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = run_protocol_small_success_once(
        output_dir=str(args.output_dir or "").strip() or None,
        preallocated_email=str(args.email or "").strip() or None,
        preallocated_session_id=str(args.session_id or "").strip() or None,
        preallocated_mailbox_ref=str(args.mailbox_ref or "").strip() or None,
        explicit_proxy=str(args.proxy or "").strip() or None,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
