from __future__ import annotations

import base64
import contextlib
import contextvars
import http.cookiejar
import importlib
import json
import os
import random
import re
import secrets
import shutil
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from curl_cffi import requests

from shared_sentinel.config import get_data_build
from shared_sentinel.proof_of_work import get_pow_token, generate_proof_token
from shared_captcha import (
    solve_browser_auth_bootstrap,
    solve_browser_sentinel_token,
    solve_cloudflare_clearance,
    solve_turnstile_vm_token,
)

from .oauth_flow import CHATGPT_WEB_CLIENT_ID, CHATGPT_WEB_REDIRECT_URI, OAuthStart, generate_oauth_url, submit_callback_url
from .register_inputs import generate_name, generate_pwd
from shared_mailbox.easy_email_client import (
    Mailbox,
    create_mailbox,
    get_mailbox_latest_message_id,
    wait_openai_code,
)
from shared_captcha import solve_turnstile_token
from shared_proxy import (
    build_request_proxies,
    debug_log_system_native_proxy_decision,
    env_flag,
    normalize_proxy_env_url,
    resolve_system_native_proxy_decision,
)
from .errors import ProtocolRuntimeError, ensure_protocol_runtime_error


AUTH_BASE = "https://auth.openai.com"
CHATGPT_BASE = "https://chatgpt.com"
PLATFORM_OPENAI_LOGIN_URL = "https://platform.openai.com/login"
CHATGPT_HOME_URL = f"{CHATGPT_BASE}/"
CHATGPT_LOGIN_URL = f"{CHATGPT_BASE}/auth/login"
CHATGPT_NEXTAUTH_CSRF_URL = f"{CHATGPT_BASE}/api/auth/csrf"
CHATGPT_NEXTAUTH_SIGNIN_OPENAI_URL = f"{CHATGPT_BASE}/api/auth/signin/openai"
CHATGPT_ACCOUNTS_URL = f"{CHATGPT_BASE}/backend-api/accounts"
AUTHORIZE_CONTINUE_URL = f"{AUTH_BASE}/api/accounts/authorize/continue"
USER_REGISTER_URL = f"{AUTH_BASE}/api/accounts/user/register"
PASSWORD_VERIFY_URL = f"{AUTH_BASE}/api/accounts/password/verify"
EMAIL_OTP_SEND_URL = f"{AUTH_BASE}/api/accounts/email-otp/send"
EMAIL_OTP_VALIDATE_URL = f"{AUTH_BASE}/api/accounts/email-otp/validate"
PASSWORDLESS_SEND_OTP_URL = f"{AUTH_BASE}/api/accounts/passwordless/send-otp"
CREATE_ACCOUNT_URL = f"{AUTH_BASE}/api/accounts/create_account"
WORKSPACE_SELECT_URL = f"{AUTH_BASE}/api/accounts/workspace/select"
CLIENT_AUTH_SESSION_DUMP_URL = f"{AUTH_BASE}/api/accounts/client_auth_session_dump"
PROTOCOL_VERBOSE_TRACE_ENV = "PROTOCOL_VERBOSE_TRACE"
_WORKSPACE_SELECTOR_CONTEXT: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "protocol_workspace_selector_context",
    default=None,
)

_CHROME_PROFILES = [
    {
        "major": 147, "impersonate": "chrome",
        "build": 0, "patch_range": (0, 0),
        "sec_ch_ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    },
]


def _protocol_verbose_trace_enabled() -> bool:
    return env_flag(PROTOCOL_VERBOSE_TRACE_ENV, False)


def _random_chrome_profile() -> tuple[str, str, str]:
    """随机选择 Chrome 版本，返回 (impersonate, user_agent, sec_ch_ua)"""
    profile = random.choice(_CHROME_PROFILES)
    major = profile["major"]
    build = profile["build"]
    patch = random.randint(*profile["patch_range"])
    full_version = f"{major}.0.{build}.{patch}"
    ua = (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{full_version} Safari/537.36"
    )
    return profile["impersonate"], ua, profile["sec_ch_ua"]


# 模块加载时选一次，整个进程内保持一致
_DEFAULT_IMPERSONATE, DEFAULT_PROTOCOL_USER_AGENT, DEFAULT_PROTOCOL_SEC_CH_UA = _random_chrome_profile()
DEFAULT_PROTOCOL_ACCEPT_LANGUAGE = "en"
DEFAULT_PROTOCOL_SEC_CH_UA_MOBILE = "?0"
DEFAULT_PROTOCOL_SEC_CH_UA_PLATFORM = '"Windows"'


def _resolve_sentinel_core() -> int:
    raw = str(os.environ.get("OPENAI_SENTINEL_CORE") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except Exception:
            pass
    return 12


def _resolve_sentinel_screen_sum() -> int:
    raw = str(
        os.environ.get("OPENAI_SENTINEL_SCREEN_SUM")
        or os.environ.get("OPENAI_SENTINEL_SCREEN")
        or ""
    ).strip()
    if raw:
        try:
            return max(1, int(raw))
        except Exception:
            pass
    return 4000


def _sentinel_profile_kwargs(profile: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(profile or {})
    window_flags = payload.get("window_flags")
    if isinstance(window_flags, list):
        payload["window_flags"] = tuple(window_flags)
    return payload


def _random_birthdate() -> str:
    """生成随机生日 (18-45 岁之间)"""
    import datetime
    today = datetime.date.today()
    min_age, max_age = 18, 45
    start = today.replace(year=today.year - max_age)
    end = today.replace(year=today.year - min_age)
    days_range = (end - start).days
    birth = start + datetime.timedelta(days=random.randint(0, days_range))
    return birth.isoformat()


CREATE_ACCOUNT_REFERER = f"{AUTH_BASE}/create-account"
CREATE_ACCOUNT_PASSWORD_REFERER = f"{AUTH_BASE}/create-account/password"
LOGIN_REFERER = f"{AUTH_BASE}/log-in"
LOGIN_OR_CREATE_ACCOUNT_REFERER = f"{AUTH_BASE}/log-in-or-create-account"
LOGIN_PASSWORD_REFERER = f"{AUTH_BASE}/log-in/password"
EMAIL_VERIFICATION_REFERER = f"{AUTH_BASE}/email-verification"
ABOUT_YOU_REFERER = f"{AUTH_BASE}/about-you"
CONSENT_REFERER = f"{AUTH_BASE}/sign-in-with-chatgpt/codex/consent"
DEFAULT_OTP_TIMEOUT_SECONDS = 300
PROTOCOL_ENABLE_EMAIL_OTP_SEND_ENV = "PROTOCOL_ENABLE_EMAIL_OTP_SEND"
PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV = "PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK"
PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV = "PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF"
PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV = "PROTOCOL_ENABLE_BROWSER_SENTINEL"
PROTOCOL_BROWSER_SENTINEL_MAX_ATTEMPTS_ENV = "PROTOCOL_BROWSER_SENTINEL_MAX_ATTEMPTS"
MAILCREATE_BASE_URL = (os.environ.get("MAILCREATE_BASE_URL") or "https://mail.aiaimimi.com").strip()
MAILCREATE_CUSTOM_AUTH = (os.environ.get("MAILCREATE_CUSTOM_AUTH") or "").strip()
SENTINEL_HEADER_WHITELIST = frozenset({
    "signup-authorize-continue",
    "signup-user-register",
    "otp-validate",
    "signup-create-account",
    "platform-update-organization",
    "repair-authorize-continue",
    "repair-password-verify",
})
_FORM_RE = re.compile(r"<form\b(?P<attrs>[^>]*)>(?P<body>.*?)</form>", re.IGNORECASE | re.DOTALL)
_INPUT_RE = re.compile(r"<input\b(?P<attrs>[^>]*)/?>", re.IGNORECASE | re.DOTALL)
_BUTTON_RE = re.compile(r"<button\b(?P<attrs>[^>]*)>(?P<body>.*?)</button>", re.IGNORECASE | re.DOTALL)
_ANCHOR_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.IGNORECASE | re.DOTALL)
_ATTR_RE = re.compile(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:([\"'])(.*?)\2|([^\s>]+))", re.DOTALL)
_WORKSPACES_RE = re.compile(r'"workspaces"\s*:\s*(\[(?:(?:(?!</script>).)*)\])', re.IGNORECASE | re.DOTALL)
_WORKSPACE_ID_RE = re.compile(r'"id"\s*:\s*"([^"]+)"', re.IGNORECASE)


@dataclass(frozen=True)
class ProtocolRegistrationResult:
    email: str
    auth: dict[str, Any]


@dataclass(frozen=True)
class ProtocolSentinelContext:
    session: requests.Session
    explicit_proxy: str | None
    user_agent: str
    device_id: str
    data_build: str
    profile: dict[str, Any]
    turnstile_token_override: str | None = None

    def new_headers(self, *, request_kind: str) -> dict[str, str]:
        return _generate_sentinel_headers_for_session(
            self.session,
            explicit_proxy=self.explicit_proxy,
            user_agent=self.user_agent,
            device_id=self.device_id,
            data_build=self.data_build,
            profile=self.profile,
            request_kind=request_kind,
            turnstile_token_override=self.turnstile_token_override,
        )


@dataclass(frozen=True)
class _StdlibResponse:
    status_code: int
    headers: dict[str, str]
    url: str
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        if not self.body:
            return {}
        return json.loads(self.text)


@dataclass(frozen=True)
class ProtocolBrowserBootstrapResult:
    current_url: str
    did: str
    user_agent: str
    imported_cookie_count: int
    auth_url: str = ""
    auth_state: str = ""


_PRESERVED_BROWSER_NATIVE_FAILURE: dict[str, Any] | None = None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


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


def _raise_protocol_error(
    message: str,
    *,
    stage: str,
    detail: str,
    category: str | None = None,
) -> None:
    raise ProtocolRuntimeError(
        message,
        stage=stage,
        detail=detail,
        category=category,
    )


def _build_protocol_headers(
    *,
    request_kind: str,
    referer: str,
    accept: str = "application/json",
    content_type: str | None = "application/json",
    sentinel_context: ProtocolSentinelContext | None = None,
) -> dict[str, str]:
    resolved_user_agent = (
        str(getattr(sentinel_context, "user_agent", "") or "").strip()
        or DEFAULT_PROTOCOL_USER_AGENT
    )
    parsed_referer = urllib.parse.urlparse(referer)
    origin = f"{parsed_referer.scheme}://{parsed_referer.netloc}" if parsed_referer.scheme and parsed_referer.netloc else AUTH_BASE
    headers = {
        "origin": origin,
        "referer": referer,
        "accept": accept,
        "accept-language": DEFAULT_PROTOCOL_ACCEPT_LANGUAGE,
        "user-agent": resolved_user_agent,
        "sec-ch-ua": DEFAULT_PROTOCOL_SEC_CH_UA,
        "sec-ch-ua-mobile": DEFAULT_PROTOCOL_SEC_CH_UA_MOBILE,
        "sec-ch-ua-platform": DEFAULT_PROTOCOL_SEC_CH_UA_PLATFORM,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    headers.update(_build_rum_trace_headers())
    if content_type:
        headers["content-type"] = content_type
    if request_kind in SENTINEL_HEADER_WHITELIST:
        if sentinel_context is None:
            raise RuntimeError(f"missing_sentinel_context request_kind={request_kind}")
        headers.update(sentinel_context.new_headers(request_kind=request_kind))
    return headers


def _build_rum_trace_headers() -> dict[str, str]:
    trace_id = secrets.randbits(64) or 1
    parent_id = secrets.randbits(64) or 1
    return {
        "traceparent": f"00-0000000000000000{trace_id:016x}-{parent_id:016x}-01",
        "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum",
        "x-datadog-parent-id": str(parent_id),
        "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": str(trace_id),
    }


def _browser_client_hints_for_user_agent(user_agent: str) -> dict[str, str]:
    normalized = str(user_agent or "").strip()
    if not normalized:
        return {}
    major = ""
    browser_match = re.search(r"(?:Chrome|Chromium)/(\d+)", normalized)
    if browser_match:
        major = str(browser_match.group(1) or "").strip()
    platform = '"Windows"'
    if "Mac OS X" in normalized or "Macintosh" in normalized:
        platform = '"macOS"'
    elif "Linux" in normalized and "Android" not in normalized:
        platform = '"Linux"'
    elif "Android" in normalized:
        platform = '"Android"'
    if not major:
        return {
            "sec-ch-ua-platform": platform,
        }
    return {
        "sec-ch-ua": f'"Chromium";v="{major}", "Not-A.Brand";v="24", "Google Chrome";v="{major}"',
        "sec-ch-ua-platform": platform,
    }


def _new_protocol_sentinel_context(
    session: requests.Session,
    *,
    explicit_proxy: str | None,
    user_agent: str,
    turnstile_token_override: str | None = None,
) -> ProtocolSentinelContext:
    session_id = str(uuid.uuid4())
    profile_variants = [
        {
            "persona": "har1",
            "script_url": "https://sentinel.openai.com/backend-api/sentinel/sdk.js",
            "navigator_probe": "canLoadAdAuctionFencedFrame−function canLoadAdAuctionFencedFrame() { [native code] }",
            "window_probe": "onmouseover",
            "performance_now_range": (10850.0, 11850.8),
        },
        {
            "persona": "har2",
            "script_url": "https://sentinel.openai.com/sentinel/20260219f9f6/sdk.js",
            "navigator_probe": "productSub−20030107",
            "window_probe": "close",
            "performance_now_range": (11450.0, 11850.8),
        },
    ]
    preferred_persona = str(
        os.environ.get("PROTOCOL_SENTINEL_PERSONA")
        or os.environ.get("STEALTH_SENTINEL_PERSONA")
        or ""
    ).strip().lower()
    chosen_variant = next(
        (
            variant
            for variant in profile_variants
            if str(variant.get("persona") or "").strip().lower() == preferred_persona
        ),
        profile_variants[0],
    )
    performance_range = chosen_variant.get("performance_now_range") or (9000.0, 12000.8)
    profile = {
        "session_id": session_id,
        "language": "en",
        "languages_join": "en",
        "timezone_offset_min": 480,
        "performance_now": round(random.uniform(float(performance_range[0]), float(performance_range[1])), 12),
        "time_origin": int(time.time() * 1000) - random.randint(5000, 15000),
        "script_url": chosen_variant["script_url"],
        "navigator_probe": chosen_variant["navigator_probe"],
        "document_probe": f"_reactListening{''.join(random.choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(11))}",
        "window_probe": chosen_variant["window_probe"],
        "window_flags": (0, 0, 0, 0, 0, 0, 0),
    }
    return ProtocolSentinelContext(
        session=session,
        explicit_proxy=explicit_proxy,
        user_agent=user_agent,
        device_id=str(uuid.uuid4()),
        data_build=get_data_build(
            fetch_html=lambda: _fetch_chatgpt_home_html(
                session,
                explicit_proxy=explicit_proxy,
            ),
        ),
        profile=profile,
        turnstile_token_override=str(turnstile_token_override or "").strip() or None,
    )


def _decode_jwt_segment(segment: str) -> dict[str, Any]:
    raw = (segment or "").strip()
    if not raw:
        return {}
    try:
        padded = raw + "=" * ((4 - (len(raw) % 4)) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _extract_page_type(response: Any) -> str:
    try:
        payload = response.json()
    except Exception:
        return ""
    page = payload.get("page") if isinstance(payload, dict) else None
    if not isinstance(page, dict):
        return ""
    page_type = page.get("type")
    return str(page_type or "").strip()


def _response_preview(response: Any, limit: int = 300) -> str:
    try:
        text = response.text
    except Exception:
        text = ""
    return str(text or "")[:limit]


def _min_turnstile_token_length() -> int:
    raw = str(os.environ.get("TURNSTILE_MIN_TOKEN_LENGTH") or "64").strip()
    try:
        value = int(raw)
    except Exception:
        value = 64
    return max(1, value)


def _signup_browser_turnstile_min_length() -> int:
    raw = str(
        os.environ.get("PROTOCOL_SIGNUP_BROWSER_TURNSTILE_MIN_LENGTH")
        or os.environ.get("PROTOCOL_BROWSER_SIGNUP_TURNSTILE_MIN_LENGTH")
        or "1400"
    ).strip()
    try:
        value = int(raw)
    except Exception:
        value = 1400
    return max(1, value)


def _allow_browser_signup_fallback_for_request_kind(request_kind: str, *, has_local_turnstile: bool) -> bool:
    if request_kind != "signup-authorize-continue":
        return True
    if not has_local_turnstile:
        return True
    return env_flag("PROTOCOL_ENABLE_BROWSER_AUTHORIZE_CONTINUE_FALLBACK", False)


def _normalize_turnstile_token(
    token: Any,
    *,
    context: str,
) -> str:
    raw = str(token or "").strip()
    if not raw:
        return ""
    min_len = _min_turnstile_token_length()
    if len(raw) >= min_len:
        return raw
    decoded_preview = ""
    try:
        padded = raw + "=" * ((4 - (len(raw) % 4)) % 4)
        decoded_preview = base64.b64decode(padded.encode("ascii"), validate=False).decode("latin-1", errors="replace")[:32]
    except Exception:
        decoded_preview = ""
    if decoded_preview:
        print(
            "[python-protocol-service] rejecting short turnstile token "
            f"context={context} t_len={len(raw)} min_len={min_len} "
            f"decoded_preview={decoded_preview!r}"
        )
    else:
        print(
            "[python-protocol-service] rejecting short turnstile token "
            f"context={context} t_len={len(raw)} min_len={min_len} "
            f"preview={raw[:32]!r}"
        )
    return ""


def _response_url(response: Any) -> str:
    try:
        return str(getattr(response, "url", "") or "").strip()
    except Exception:
        return ""


def _response_location(response: Any) -> str:
    try:
        headers = getattr(response, "headers", None) or {}
        return str(
            headers.get("Location")
            or headers.get("location")
            or ""
        ).strip()
    except Exception:
        return ""


def _response_continue_url(response: Any) -> str:
    try:
        payload = response.json() or {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("continue_url") or "").strip()


def _response_json_dict(response: Any) -> dict[str, Any]:
    try:
        payload = response.json() or {}
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _response_error_dict(response: Any) -> dict[str, Any]:
    payload = _response_json_dict(response)
    error_payload = payload.get("error")
    return error_payload if isinstance(error_payload, dict) else {}


def _response_error_code(response: Any) -> str:
    return str(_response_error_dict(response).get("code") or "").strip()


def _response_error_type(response: Any) -> str:
    return str(_response_error_dict(response).get("type") or "").strip()


def _response_error_message(response: Any) -> str:
    return str(_response_error_dict(response).get("message") or "").strip()


def _response_header(response: Any, name: str) -> str:
    normalized_name = str(name or "").strip().lower()
    if not normalized_name:
        return ""
    try:
        headers = getattr(response, "headers", None) or {}
    except Exception:
        headers = {}
    try:
        for key, value in headers.items():
            if str(key or "").strip().lower() == normalized_name:
                return str(value or "").strip()
    except Exception:
        pass
    return ""


def _response_has_cloudflare_challenge(response: Any) -> bool:
    preview = _response_preview(response, 500).lower()
    response_url = _response_url(response).lower()
    cf_mitigated = _response_header(response, "cf-mitigated").lower()
    return bool(
        cf_mitigated == "challenge"
        or "just a moment" in preview
        or "attention required" in preview
        or "verify you are human" in preview
        or "performing security verification" in preview
        or "cdn-cgi/challenge-platform" in response_url
    )


def _response_has_registration_disallowed(response: Any) -> bool:
    error_code = _response_error_code(response).lower()
    error_message = _response_error_message(response).lower()
    preview = _response_preview(response, 500).lower()
    combined = f"{error_code}\n{error_message}\n{preview}"
    return bool(
        error_code == "registration_disallowed"
        or (
            "terms of use" in combined
            and ("can't create your account" in combined or "cannot create your account" in combined)
        )
        or "cannot create your account with the given information" in combined
    )


def _categorize_protocol_response_error(
    response: Any,
    *,
    default_category: str = "flow_error",
) -> str:
    error_code = _response_error_code(response).lower()
    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
    except Exception:
        status_code = 0
    if _response_has_cloudflare_challenge(response):
        return "blocked"
    if error_code in {"invalid_state", "invalid_client"}:
        return "auth_error"
    if _response_has_registration_disallowed(response):
        return "blocked"
    if status_code in {403, 429}:
        return "blocked"
    return str(default_category or "flow_error").strip() or "flow_error"


def _response_error_summary(response: Any, *, preview_limit: int = 300) -> str:
    parts: list[str] = []
    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
    except Exception:
        status_code = 0
    if status_code:
        parts.append(f"status={status_code}")
    cf_mitigated = _response_header(response, "cf-mitigated")
    if cf_mitigated:
        parts.append(f"cf_mitigated={cf_mitigated}")
    response_url = _response_url(response)
    if response_url:
        parts.append(f"url={_format_logged_url(response_url)}")
    page_type = _extract_page_type(response)
    if page_type:
        parts.append(f"page_type={page_type}")
    error_type = _response_error_type(response)
    if error_type:
        parts.append(f"error_type={error_type}")
    error_code = _response_error_code(response)
    if error_code:
        parts.append(f"error_code={error_code}")
    error_message = _response_error_message(response)
    if error_message:
        parts.append(f"error_message={error_message}")
    preview = _response_preview(response, preview_limit)
    if preview:
        parts.append(f"body={preview}")
    return " ".join(parts) if parts else "no_response_details"


def _raise_protocol_response_error(
    response: Any,
    *,
    prefix: str,
    stage: str,
    detail: str,
    default_category: str = "flow_error",
) -> None:
    _raise_protocol_error(
        f"{prefix} {_response_error_summary(response)}",
        stage=stage,
        detail=detail,
        category=_categorize_protocol_response_error(
            response,
            default_category=default_category,
        ),
    )


def _fetch_sentinel_token_direct(
    session: requests.Session,
    *,
    device_id: str,
    flow: str,
    explicit_proxy: str | None,
) -> str:
    """直接通过 sentinel.openai.com/backend-api/sentinel/req 获取 sentinel c token"""
    req_body = json.dumps({"p": "", "id": device_id, "flow": flow})
    try:
        resp = _session_request(
            session,
            "POST",
            "https://sentinel.openai.com/backend-api/sentinel/req",
            explicit_proxy=explicit_proxy,
            request_label=f"sentinel-req-{flow}",
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "content-type": "text/plain;charset=UTF-8",
            },
            data=req_body,
            timeout=15,
        )
        if resp.status_code != 200:
            print(
                f"[python-protocol-service] sentinel direct req failed "
                f"flow={flow} status={resp.status_code}"
            )
            return ""
        return str((resp.json() or {}).get("token", "")).strip()
    except Exception as exc:
        print(
            f"[python-protocol-service] sentinel direct req error "
            f"flow={flow} err={exc}"
        )
        return ""


def _build_direct_sentinel_header(
    *,
    c_token: str,
    device_id: str,
    flow: str,
) -> str:
    """组装 openai-sentinel-token header 值（直接 API 方式，p 和 t 留空）"""
    return json.dumps(
        {"p": "", "t": "", "c": c_token, "id": device_id, "flow": flow},
        separators=(",", ":"),
    )


def _get_sentinel_header_for_signup(
    session: requests.Session,
    *,
    device_id: str,
    flow: str,
    request_kind: str,
    explicit_proxy: str | None,
    sentinel_context: ProtocolSentinelContext | None = None,
    prefer_pow: bool = False,
    browser_email: str | None = None,
) -> str:
    """获取 sentinel token header 值。

    组合策略：
    1. 本地生成 p (requirements token) — 不需要外部服务
    2. POST sentinel.openai.com/backend-api/sentinel/req 获取 c + 可能的 PoW/turnstile challenge
    3. 如果有 PoW challenge，本地计算 enforcement proof 作为新的 p
    4. t 字段暂留空（turnstile 需要 captcha 服务，可选）
    """
    # Step 1: 生成初始 requirements token。Turnstile DX 解密要用这份初始 token，
    # PoW enforcement 通过后再单独生成最终上送的 p 字段。
    requirements_token = get_pow_token(
        user_agent=(
            str(getattr(sentinel_context, "user_agent", "") or "").strip()
            or DEFAULT_PROTOCOL_USER_AGENT
        ),
        core=_resolve_sentinel_core(),
        screen=_resolve_sentinel_screen_sum(),
        data_build=(
            str(getattr(sentinel_context, "data_build", "") or "").strip()
        ) or None,
        **_sentinel_profile_kwargs(getattr(sentinel_context, "profile", None)),
    )
    p_token = requirements_token

    # Step 2: POST sentinel.openai.com API 获取 c 字段和可能的 challenge
    req_body = json.dumps({"p": requirements_token, "id": device_id, "flow": flow})
    c_token = ""
    pow_seed = ""
    pow_diff = ""
    pow_required = False
    turnstile_dx = ""
    turnstile_required = False

    try:
        resp = _session_request(
            session,
            "POST",
            "https://sentinel.openai.com/backend-api/sentinel/req",
            explicit_proxy=explicit_proxy,
            request_label=f"sentinel-req-{flow}",
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "content-type": "text/plain;charset=UTF-8",
            },
            data=req_body,
            timeout=15,
        )
        if resp.status_code == 200:
            resp_data = resp.json() or {}
            c_token = str(resp_data.get("token") or "").strip()
            pow_obj = resp_data.get("proofofwork") or {}
            pow_required = bool(pow_obj.get("required", False))
            pow_seed = str(pow_obj.get("seed") or "").strip()
            pow_diff = str(pow_obj.get("difficulty") or "").strip()
            turnstile_obj = resp_data.get("turnstile") or {}
            turnstile_required = bool(turnstile_obj.get("required", False))
            turnstile_dx = str(turnstile_obj.get("dx") or "").strip()
            if _protocol_verbose_trace_enabled():
                print(
                    f"[python-protocol-service] sentinel req OK flow={flow} "
                    f"c_len={len(c_token)} pow_required={pow_required} "
                    f"turnstile_required={turnstile_required}"
                )
        else:
            print(
                f"[python-protocol-service] sentinel req failed "
                f"flow={flow} status={resp.status_code}"
            )
    except Exception as exc:
        print(
            f"[python-protocol-service] sentinel req error "
            f"flow={flow} err={exc}"
        )

    # Step 3: 如果有 PoW challenge，生成 enforcement proof token
    if pow_required and pow_seed and pow_diff:
        enforcement_p = generate_proof_token(
            required=True,
            seed=pow_seed,
            difficulty=pow_diff,
            user_agent=(
                str(getattr(sentinel_context, "user_agent", "") or "").strip()
                or DEFAULT_PROTOCOL_USER_AGENT
            ),
            core=_resolve_sentinel_core(),
            screen=_resolve_sentinel_screen_sum(),
            data_build=(
                str(getattr(sentinel_context, "data_build", "") or "").strip()
            ) or None,
            **_sentinel_profile_kwargs(getattr(sentinel_context, "profile", None)),
        )
        if enforcement_p:
            p_token = enforcement_p
            if _protocol_verbose_trace_enabled():
                print(
                    f"[python-protocol-service] sentinel PoW enforcement solved "
                    f"flow={flow} p_len={len(p_token)}"
                )

    # Step 4: 解 turnstile — 优先本地 Go/Python VM，回退到 captcha 服务
    t_token = ""
    if turnstile_required and turnstile_dx:
        if _protocol_verbose_trace_enabled():
            print(
                f"[python-protocol-service] sentinel turnstile solving "
                f"flow={flow} dx_len={len(turnstile_dx)} requirements_p_len={len(requirements_token)}"
            )
        # 策略A: 本地 turnstile VM solver (Go binary + Python fallback)
        try:
            from shared_sentinel.turnstile import process_turnstile
            t_token = _normalize_turnstile_token(
                process_turnstile(turnstile_dx, requirements_token),
                context=f"{request_kind}:local_vm:{flow}",
            )
            if t_token:
                if _protocol_verbose_trace_enabled():
                    print(
                        f"[python-protocol-service] sentinel turnstile solved via local VM "
                        f"flow={flow} t_len={len(t_token)}"
                    )
        except Exception as exc:
            print(
                f"[python-protocol-service] sentinel turnstile local VM failed "
                f"flow={flow} err={exc}"
            )
        # 策略B: 如果本地失败，尝试 captcha 服务
        if not t_token:
            try:
                solved = solve_turnstile_vm_token(
                    dx=turnstile_dx,
                    proof_token=requirements_token,
                )
                t_token = _normalize_turnstile_token(
                    solved.get("token"),
                    context=f"{request_kind}:captcha_service:{flow}",
                )
                if t_token:
                    if _protocol_verbose_trace_enabled():
                        print(
                            f"[python-protocol-service] sentinel turnstile solved via captcha service "
                            f"flow={flow} t_len={len(t_token)}"
                        )
            except Exception as exc2:
                print(
                    f"[python-protocol-service] sentinel turnstile captcha service also failed "
                    f"flow={flow} err={exc2} (continuing without t)"
                )

        signup_flow_map = {
            "signup-authorize-continue": "authorize_continue",
            "signup-user-register": "username_password_create",
            "signup-create-account": "oauth_create_account",
        }
        weak_signup_turnstile = (
            request_kind in signup_flow_map
            and (
                not t_token
                or len(str(t_token or "").strip()) < _signup_browser_turnstile_min_length()
            )
        )
        allow_browser_signup_fallback = _allow_browser_signup_fallback_for_request_kind(
            request_kind,
            has_local_turnstile=bool(str(t_token or "").strip()),
        )
        if weak_signup_turnstile and allow_browser_signup_fallback:
            try:
                if t_token:
                    print(
                        "[python-protocol-service] signup sentinel local token considered weak "
                        f"request_kind={request_kind} flow={flow} "
                        f"t_len={len(str(t_token or '').strip())} "
                        f"min_len={_signup_browser_turnstile_min_length()}"
                    )
                browser_signup_payload = _capture_browser_signup_sentinel_payload(
                    session=session,
                    explicit_proxy=explicit_proxy,
                    request_kind=request_kind,
                    profile=getattr(sentinel_context, "profile", None),
                    browser_email=browser_email,
                )
                token_payload = (
                    browser_signup_payload.get("tokenPayload")
                    if isinstance(browser_signup_payload, dict)
                    and isinstance(browser_signup_payload.get("tokenPayload"), dict)
                    else {}
                )
                browser_device_id = str(
                    (browser_signup_payload or {}).get("deviceId") or device_id
                ).strip() or device_id
                browser_t = _normalize_turnstile_token(
                    token_payload.get("t"),
                    context=f"{request_kind}:browser_fallback:{flow}",
                )
                browser_p = str(token_payload.get("p") or p_token or "").strip()
                browser_c = str(token_payload.get("c") or c_token or "").strip()
                browser_signup_token = ""
                if browser_p and browser_t:
                    browser_signup_token = json.dumps(
                        {
                            "p": browser_p,
                            "t": browser_t,
                            **({"c": browser_c} if browser_c else {}),
                            "id": browser_device_id,
                            "flow": signup_flow_map[request_kind],
                        },
                        separators=(",", ":"),
                    )
                if browser_signup_token:
                    _cache_browser_signup_payload(
                        session,
                        request_kind=request_kind,
                        payload=browser_signup_payload if isinstance(browser_signup_payload, dict) else None,
                    )
                    print(
                        "[python-protocol-service] signup sentinel browser fallback "
                        f"request_kind={request_kind} flow={flow}"
                    )
                    return browser_signup_token
            except Exception as exc:
                print(
                    "[python-protocol-service] signup sentinel browser fallback failed "
                    f"request_kind={request_kind} flow={flow} err={exc}"
                )
        elif request_kind in signup_flow_map:
            _cache_browser_signup_payload(session, request_kind=request_kind, payload=None)

    # 组装最终 sentinel token
    sentinel_payload = {
        "p": p_token,
        "t": t_token,
        "id": device_id,
        "flow": flow,
    }
    if c_token:
        sentinel_payload["c"] = c_token
    result = json.dumps(sentinel_payload, separators=(",", ":"))
    if _protocol_verbose_trace_enabled():
        print(
            f"[python-protocol-service] sentinel token assembled "
            f"flow={flow} p_len={len(p_token)} t_len={len(t_token)} "
            f"c_len={len(c_token)} total_len={len(result)}"
        )
    return result


def _clone_protocol_sentinel_context(
    sentinel_context: ProtocolSentinelContext,
    *,
    user_agent: str | None = None,
    device_id: str | None = None,
    turnstile_token_override: str | None = None,
) -> ProtocolSentinelContext:
    return ProtocolSentinelContext(
        session=sentinel_context.session,
        explicit_proxy=sentinel_context.explicit_proxy,
        user_agent=str(user_agent or sentinel_context.user_agent).strip() or sentinel_context.user_agent,
        device_id=str(device_id or sentinel_context.device_id).strip() or sentinel_context.device_id,
        data_build=sentinel_context.data_build,
        profile=dict(sentinel_context.profile),
        turnstile_token_override=(
            str(turnstile_token_override or "").strip()
            or sentinel_context.turnstile_token_override
        ),
    )


def _protocol_services_root() -> str:
    current = os.path.abspath(os.path.dirname(__file__))
    candidates: list[str] = []
    probe = current
    while True:
        candidates.append(probe)
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent

    for candidate in candidates:
        browser_src = os.path.join(candidate, "python_browser_service", "src")
        if os.path.isdir(browser_src):
            return candidate

    # Fallback to the historical relative path from the non-bundled layout.
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", ".."))


def _load_protocol_browser_new_driver() -> Any:
    browser_src = os.path.join(_protocol_services_root(), "python_browser_service", "src")
    if browser_src not in sys.path:
        sys.path.insert(0, browser_src)
    module = importlib.import_module("browser_runtime.runner")
    new_driver = getattr(module, "_new_driver", None)
    if not callable(new_driver):
        raise RuntimeError("browser_runtime_runner_missing__new_driver")
    return new_driver


def _load_protocol_browser_chatgpt_oauth_bootstrap() -> Any:
    browser_src = os.path.join(_protocol_services_root(), "python_browser_service", "src")
    if browser_src not in sys.path:
        sys.path.insert(0, browser_src)
    module = importlib.import_module("browser_runtime.oauth_flow")
    bootstrap = getattr(module, "generate_chatgpt_web_oauth_url", None)
    if not callable(bootstrap):
        raise RuntimeError("browser_runtime_oauth_flow_missing_generate_chatgpt_web_oauth_url")
    return bootstrap


def _load_protocol_browser_maybe_solve_turnstile_challenge() -> Any:
    browser_src = os.path.join(_protocol_services_root(), "python_browser_service", "src")
    if browser_src not in sys.path:
        sys.path.insert(0, browser_src)
    module = importlib.import_module("browser_runtime.turnstile_runtime")
    solver = getattr(module, "maybe_solve_turnstile_challenge", None)
    if not callable(solver):
        raise RuntimeError("browser_runtime_turnstile_runtime_missing_solver")
    return solver


def _load_protocol_browser_try_native_auth_fill_password() -> Any:
    browser_src = os.path.join(_protocol_services_root(), "python_browser_service", "src")
    if browser_src not in sys.path:
        sys.path.insert(0, browser_src)
    module = importlib.import_module("browser_runtime.camoufox_native")
    helper = getattr(module, "try_native_auth_fill_password", None)
    if not callable(helper):
        raise RuntimeError("browser_runtime_camoufox_native_missing_password_helper")
    return helper


def _load_protocol_browser_try_native_auth_fill_email() -> Any:
    browser_src = os.path.join(_protocol_services_root(), "python_browser_service", "src")
    if browser_src not in sys.path:
        sys.path.insert(0, browser_src)
    module = importlib.import_module("browser_runtime.camoufox_native")
    helper = getattr(module, "try_native_auth_fill_email", None)
    if not callable(helper):
        raise RuntimeError("browser_runtime_camoufox_native_missing_email_helper")
    return helper


def _protocol_browser_native_backend() -> str:
    raw = str(
        os.environ.get("PROTOCOL_BROWSER_NATIVE_BACKEND")
        or os.environ.get("PROTOCOL_BROWSER_BACKEND")
        or ""
    ).strip().lower()
    if raw in {"camoufox", "firefox"}:
        return "camoufox"
    if raw in {"custom", "chrome", "chromium"}:
        return "custom"
    return "custom"


def _protocol_browser_native_keep_failed_browser() -> bool:
    raw = str(os.environ.get("PROTOCOL_BROWSER_NATIVE_KEEP_FAILED_BROWSER") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _protocol_browser_native_close_success_browser() -> bool:
    raw = str(os.environ.get("PROTOCOL_BROWSER_NATIVE_CLOSE_SUCCESS_BROWSER") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _protocol_browser_native_allow_recovery_driver() -> bool:
    raw = str(os.environ.get("PROTOCOL_BROWSER_NATIVE_ALLOW_RECOVERY_DRIVER") or "0").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _close_protocol_browser_native_preserved_failure() -> None:
    global _PRESERVED_BROWSER_NATIVE_FAILURE
    preserved = _PRESERVED_BROWSER_NATIVE_FAILURE
    _PRESERVED_BROWSER_NATIVE_FAILURE = None
    if not isinstance(preserved, dict):
        return
    driver = preserved.get("driver")
    proxy_dir = str(preserved.get("proxy_dir") or "").strip()
    browser_user_data_dir = str(preserved.get("browser_user_data_dir") or "").strip()
    driver_cleanup_user_data_dir = str(preserved.get("driver_cleanup_user_data_dir") or "").strip()
    if driver is not None:
        try:
            driver.quit()
        except Exception:
            pass
    for candidate in (proxy_dir, browser_user_data_dir, driver_cleanup_user_data_dir):
        if candidate:
            try:
                shutil.rmtree(str(candidate), ignore_errors=True)
            except Exception:
                pass


def _preserve_protocol_browser_native_failure(
    *,
    driver: Any,
    proxy_dir: str | None,
    browser_user_data_dir: str,
    driver_cleanup_user_data_dir: str,
    note: str,
) -> None:
    global _PRESERVED_BROWSER_NATIVE_FAILURE
    _close_protocol_browser_native_preserved_failure()
    _PRESERVED_BROWSER_NATIVE_FAILURE = {
        "driver": driver,
        "proxy_dir": proxy_dir,
        "browser_user_data_dir": browser_user_data_dir,
        "driver_cleanup_user_data_dir": driver_cleanup_user_data_dir,
        "note": note,
        "preserved_at": datetime.now(timezone.utc).isoformat(),
    }
    href = ""
    title = ""
    try:
        href = str(getattr(driver, "current_url", "") or "").strip()
    except Exception:
        href = ""
    try:
        title = str(getattr(driver, "title", "") or "").strip()
    except Exception:
        title = ""
    print(
        "[python-protocol-service] browser native preserved failed interface "
        f"note={note} href={href} title={title} "
        f"user_data_dir={browser_user_data_dir or driver_cleanup_user_data_dir or '<none>'}"
    )


def _protocol_browser_native_preserved_failure_note() -> str:
    preserved = _PRESERVED_BROWSER_NATIVE_FAILURE
    if not isinstance(preserved, dict):
        return ""
    return str(preserved.get("note") or "").strip()


def _stash_protocol_browser_native_success(
    session: requests.Session,
    *,
    driver: Any,
    proxy_dir: str | None,
    browser_user_data_dir: str,
    driver_cleanup_user_data_dir: str,
) -> None:
    try:
        setattr(
            session,
            "_new_protocol_browser_native_success_state",
            {
                "driver": driver,
                "proxy_dir": str(proxy_dir or "").strip(),
                "browser_user_data_dir": str(browser_user_data_dir or "").strip(),
                "driver_cleanup_user_data_dir": str(driver_cleanup_user_data_dir or "").strip(),
            },
        )
    except Exception:
        pass


def _take_protocol_browser_native_success(session: requests.Session) -> dict[str, Any] | None:
    try:
        state = getattr(session, "_new_protocol_browser_native_success_state", None)
    except Exception:
        state = None
    try:
        if hasattr(session, "_new_protocol_browser_native_success_state"):
            delattr(session, "_new_protocol_browser_native_success_state")
    except Exception:
        pass
    return state if isinstance(state, dict) else None


def _close_protocol_browser_native_success_state(state: dict[str, Any] | None) -> None:
    if not isinstance(state, dict):
        return
    driver = state.get("driver")
    proxy_dir = str(state.get("proxy_dir") or "").strip()
    browser_user_data_dir = str(state.get("browser_user_data_dir") or "").strip()
    driver_cleanup_user_data_dir = str(state.get("driver_cleanup_user_data_dir") or "").strip()
    if driver is not None:
        try:
            driver.quit()
        except Exception:
            pass
    for candidate in (proxy_dir, browser_user_data_dir, driver_cleanup_user_data_dir):
        if candidate:
            try:
                shutil.rmtree(str(candidate), ignore_errors=True)
            except Exception:
                pass


def _protocol_browser_native_remove_args_csv() -> str:
    defaults = [
        "--incognito",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-component-update",
        "--disable-domain-reliability",
        "--disable-client-side-phishing-detection",
        "--disable-default-apps",
        "--no-default-browser-check",
        "--no-first-run",
        "--disable-search-engine-choice-screen",
        "--disable-signin-promo",
        "--disable-blink-features=AutomationControlled",
        "--disable-in-process-stack-traces",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ]
    extra = str(os.environ.get("PROTOCOL_BROWSER_NATIVE_REMOVE_ARGS") or "").strip()
    if extra:
        defaults.extend(part.strip() for part in extra.replace(";", ",").split(",") if part.strip())
    deduped: list[str] = []
    seen: set[str] = set()
    for arg in defaults:
        key = str(arg or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return ",".join(deduped)


def _protocol_browser_native_captcha_provider(browser_backend: str) -> str:
    raw = str(os.environ.get("PROTOCOL_BROWSER_NATIVE_CAPTCHA_PROVIDER") or "").strip()
    if raw:
        return raw
    normalized_backend = str(browser_backend or "").strip().lower()
    if normalized_backend in {"camoufox", "custom", "chrome"}:
        return "turnstile-solver-camoufox"
    return ""


def _protocol_browser_native_captcha_dbg(kind: str, message: str, **_: Any) -> None:
    print(f"[python-protocol-service] browser native captcha {kind}: {message}")


def _protocol_browser_native_env_overrides(browser_user_data_dir: str) -> dict[str, str]:
    return {
        "BROWSER_USE_EASYBROWSER": "1",
        "USE_UNDETECTED_CHROMEDRIVER": "1",
        "BLOCK_IMAGES": "0",
        "BLOCK_CSS": "0",
        "BLOCK_FONTS": "0",
        "BROWSER_WINDOW_SIZE": "1280,900",
        "ANONYMOUS_MODE": "1",
        "HEADLESS": str(os.environ.get("HEADLESS", "0") or "0"),
        "BROWSER_USER_DATA_DIR": browser_user_data_dir,
        "BROWSER_REMOVE_ARGS_EXTRA": _protocol_browser_native_remove_args_csv(),
        "BROWSER_ENABLE_BWSI": "1",
        "BROWSER_ENABLE_PERFORMANCE_LOGS": "1",
        "BLOCK_GOOGLE_OPT_GUIDE": "0",
        "BLOCK_NOISY_HOSTS": "0",
        "TURNSTILE_SOLVER_INLINE_PREFER": "1",
    }


def _protocol_browser_native_profile_dir() -> str:
    override = str(os.environ.get("PROTOCOL_BROWSER_NATIVE_PROFILE_DIR") or "").strip()
    if override:
        return os.path.abspath(override)
    return os.path.abspath(
        os.path.join(
            _protocol_services_root(),
            "..",
            "..",
            "..",
            "tmp",
            "protocol-browser-native-profile",
        )
    )


def _protocol_browser_native_use_ephemeral_profile() -> bool:
    return env_flag("PROTOCOL_BROWSER_NATIVE_EPHEMERAL_PROFILE", True)


def _protocol_browser_native_use_native_password_helper() -> bool:
    return env_flag("PROTOCOL_BROWSER_NATIVE_USE_NATIVE_PASSWORD_HELPER", False)


def _browser_host_for_cookie_domain(domain: str) -> str:
    normalized = str(domain or "").strip().lower().lstrip(".")
    if not normalized:
        return ""
    if normalized.endswith("chatgpt.com"):
        return "chatgpt.com"
    if normalized.endswith("openai.com"):
        return "auth.openai.com"
    return ""


def _import_exported_cookies_into_browser_driver(
    driver: Any,
    *,
    exported_cookies: list[dict[str, Any]],
) -> int:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for payload in exported_cookies:
        if not isinstance(payload, dict):
            continue
        host = _browser_host_for_cookie_domain(str(payload.get("domain") or ""))
        if not host:
            continue
        grouped.setdefault(host, []).append(payload)
    imported = 0
    for host, payloads in grouped.items():
        target_url = f"https://{host}/"
        try:
            driver.get(target_url)
        except Exception:
            continue
        for payload in payloads:
            cookie_payload = {
                "name": str(payload.get("name") or "").strip(),
                "value": str(payload.get("value") or ""),
                "path": str(payload.get("path") or "/").strip() or "/",
            }
            domain = str(payload.get("domain") or "").strip()
            if domain:
                cookie_payload["domain"] = domain
            if "secure" in payload:
                cookie_payload["secure"] = bool(payload.get("secure"))
            if payload.get("expires") is not None:
                try:
                    cookie_payload["expiry"] = int(float(payload.get("expires")))
                except Exception:
                    pass
            if not cookie_payload["name"]:
                continue
            try:
                driver.add_cookie(cookie_payload)
                imported += 1
                continue
            except Exception:
                pass
            fallback_payload = {
                "name": cookie_payload["name"],
                "value": cookie_payload["value"],
                "path": cookie_payload["path"],
            }
            if "secure" in cookie_payload:
                fallback_payload["secure"] = cookie_payload["secure"]
            if "expiry" in cookie_payload:
                fallback_payload["expiry"] = cookie_payload["expiry"]
            try:
                driver.add_cookie(fallback_payload)
                imported += 1
            except Exception:
                continue
    return imported


def _import_browser_driver_cookies_into_session(
    session: requests.Session,
    *,
    driver: Any,
) -> int:
    imported = 0
    current_url = ""
    try:
        current_url = str(getattr(driver, "current_url", "") or "").strip()
    except Exception:
        current_url = ""
    seeded_hosts = [
        current_url,
        "https://chatgpt.com/",
        "https://chatgpt.com/auth/login",
        "https://auth.openai.com/log-in",
        "https://auth.openai.com/log-in-or-create-account",
        "https://auth.openai.com/",
        "https://auth.openai.com/create-account",
        "https://auth.openai.com/create-account/password",
        "https://auth.openai.com/about-you",
        "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
    ]
    seen: set[tuple[str, str, str]] = set()
    for host_url in seeded_hosts:
        normalized_host_url = str(host_url or "").strip()
        if not normalized_host_url:
            continue
        try:
            driver.get(normalized_host_url)
            cookies = driver.get_cookies() or []
        except Exception:
            continue
        fallback_domain = urllib.parse.urlparse(normalized_host_url).hostname or ""
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            name = str(cookie.get("name") or "").strip()
            value = str(cookie.get("value") or "")
            domain = str(cookie.get("domain") or "").strip() or fallback_domain
            path = str(cookie.get("path") or "/").strip() or "/"
            if not name or not domain:
                continue
            dedupe_key = (name, domain, path)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            try:
                session.cookies.set(
                    name,
                    value,
                    domain=domain,
                    path=path,
                    secure=bool(cookie.get("secure", False)),
                )
                imported += 1
            except Exception:
                continue
    return imported


def _browser_bootstrap_chatgpt_web_oauth_session_on_driver(
    driver: Any,
    *,
    device_id: str,
    timeout_seconds: float = 45.0,
) -> OAuthStart | None:
    effective_did = str(device_id or "").strip() or str(uuid.uuid4())
    auth_session_id = str(uuid.uuid4())
    try:
        driver.get(PLATFORM_OPENAI_LOGIN_URL)
    except Exception:
        pass
    try:
        driver.get(CHATGPT_LOGIN_URL)
    except Exception:
        pass

    def _driver_cookie_value(name: str) -> str:
        try:
            for cookie in driver.get_cookies() or []:
                if str(cookie.get("name") or "").strip() == name:
                    return str(cookie.get("value") or "").strip()
        except Exception:
            return ""
        return ""

    csrf_token = ""
    csrf_deadline = time.time() + max(5.0, float(timeout_seconds))
    while time.time() < csrf_deadline and not csrf_token:
        try:
            raw = driver.execute_async_script(
                """
                const done = arguments[0];
                (async () => {
                  try {
                    const r = await fetch('/api/auth/csrf', {
                      credentials: 'include',
                      headers: { 'Accept': 'application/json' },
                    });
                    const ct = String(r.headers.get('content-type') || '');
                    if (!ct.includes('json')) {
                      done({ ok: false, error: 'not_json:' + ct.substring(0, 40) });
                      return;
                    }
                    const data = await r.json();
                    done({ ok: true, token: String((data && data.csrfToken) || '') });
                  } catch (error) {
                    done({ ok: false, error: String(error) });
                  }
                })();
                """
            ) or {}
        except Exception:
            raw = {}
        if isinstance(raw, dict) and raw.get("ok") and raw.get("token"):
            csrf_token = str(raw.get("token") or "").strip()
            break
        cookie_value = _driver_cookie_value("__Host-next-auth.csrf-token")
        if cookie_value:
            csrf_token = str(cookie_value.split("|", 1)[0] or "").strip()
            if csrf_token:
                print("[python-protocol-service] browser bootstrap reusing csrf cookie token")
                break
        time.sleep(2.0)
    if not csrf_token:
        return None

    signin_result = None
    try:
        signin_result = driver.execute_async_script(
            """
            const args = arguments[0] || {};
            const done = arguments[arguments.length - 1];
            (async () => {
              const csrfToken = String(args.csrfToken || '');
              const deviceId = String(args.deviceId || '');
              const authSessionId = String(args.authSessionId || '');
              const params = new URLSearchParams({
                prompt: 'login',
                screen_hint: 'login_or_signup',
                device_id: deviceId,
                'ext-oai-did': deviceId,
                auth_session_logging_id: authSessionId,
              });
              try {
                const r = await fetch(`/api/auth/signin/openai?${params.toString()}`, {
                  method: 'POST',
                  credentials: 'include',
                  headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Accept': 'application/json',
                  },
                  body: new URLSearchParams({
                    csrfToken: csrfToken,
                    callbackUrl: 'https://chatgpt.com/',
                    json: 'true',
                  }).toString(),
                });
                const data = await r.json();
                done({ ok: true, url: String((data && data.url) || '') });
              } catch (error) {
                done({ ok: false, error: String(error) });
              }
            })();
            """,
            {
                "csrfToken": csrf_token,
                "deviceId": effective_did,
                "authSessionId": auth_session_id,
            },
        )
    except Exception:
        signin_result = None
    if not isinstance(signin_result, dict) or not signin_result.get("ok") or not signin_result.get("url"):
        return None

    auth_url = str(signin_result.get("url") or "").strip()
    parsed_auth_url = urllib.parse.urlparse(auth_url)
    state = str((urllib.parse.parse_qs(parsed_auth_url.query, keep_blank_values=True).get("state") or [""])[0] or "").strip()
    if not auth_url or not state:
        return None

    auth_page_url = auth_url
    auth_page_title = ""
    auth_cookie_ready = False
    try:
        driver.get(auth_url)
    except Exception:
        pass
    auth_deadline = time.time() + min(max(10.0, float(timeout_seconds)), 25.0)
    while time.time() < auth_deadline:
        try:
            auth_page_url = str(getattr(driver, "current_url", "") or auth_url).strip()
        except Exception:
            auth_page_url = auth_url
        try:
            auth_page_title = str(getattr(driver, "title", "") or "").strip()
        except Exception:
            auth_page_title = ""
        auth_cookies = {str(cookie.get("name") or "").strip() for cookie in (driver.get_cookies() or []) if isinstance(cookie, dict)}
        auth_cookie_ready = all(name in auth_cookies for name in ("login_session", "oai-client-auth-session", "hydra_redirect"))
        if auth_cookie_ready and not _browser_page_is_cloudflare_wait(_browser_collect_page_state(driver)):
            break
        time.sleep(2.0)

    login_or_create_url = LOGIN_OR_CREATE_ACCOUNT_REFERER
    try:
        driver.get(login_or_create_url)
    except Exception:
        pass
    entry_cookie_ready = False
    entry_cookie_value_ready = False
    entry_challenge_present = True
    entry_page_url = login_or_create_url
    entry_page_title = ""
    entry_deadline = time.time() + min(max(10.0, float(timeout_seconds)), 30.0)
    while time.time() < entry_deadline:
        page_state = _browser_collect_page_state(driver)
        entry_page_url = str(page_state.get("href") or login_or_create_url)
        entry_page_title = str(page_state.get("title") or "")
        entry_challenge_present = _browser_page_is_cloudflare_wait(page_state)
        entry_cookies = {str(cookie.get("name") or "").strip(): str(cookie.get("value") or "") for cookie in (driver.get_cookies() or []) if isinstance(cookie, dict)}
        entry_cookie_ready = all(name in entry_cookies for name in ("login_session", "oai-client-auth-session", "hydra_redirect", "oai-sc", "rg_context"))
        entry_cookie_value_ready = str(entry_cookies.get("rg_context") or "").strip() == "stb"
        if entry_cookie_value_ready and not entry_challenge_present:
            break
        time.sleep(2.0)

    if not entry_cookie_value_ready or entry_challenge_present:
        print(
            "[python-protocol-service] browser bootstrap auth entry did not fully reach ready state "
            f"url={entry_page_url[:120]} title={entry_page_title[:80]!r} "
            f"auth_cookie_ready={auth_cookie_ready} entry_cookie_ready={entry_cookie_ready} "
            f"entry_cookie_value_ready={entry_cookie_value_ready} challenge_present={entry_challenge_present}"
        )

    return OAuthStart(
        auth_url=auth_url,
        state=state,
        code_verifier="",
        redirect_uri=CHATGPT_WEB_REDIRECT_URI,
    )


def _prime_protocol_auth_session_with_browser(
    session: requests.Session,
    *,
    explicit_proxy: str | None,
    device_id: str,
) -> ProtocolBrowserBootstrapResult:
    new_driver = _load_protocol_browser_new_driver()
    driver = None
    proxy_dir = None
    bootstrap_succeeded = False
    before_cookie_names = _session_cookie_name_summary(session, limit=50)
    try:
        driver, proxy_dir = new_driver(
            explicit_proxy,
            browser_backend=_protocol_browser_native_backend(),
        )
        oauth = _browser_bootstrap_chatgpt_web_oauth_session_on_driver(
            driver,
            device_id=device_id,
        )
        if oauth is None:
            raise RuntimeError("browser_native_chatgpt_oauth_bootstrap_failed")
        imported_cookie_count = _import_browser_driver_cookies_into_session(session, driver=driver)
        user_agent = ""
        try:
            user_agent = str(driver.execute_script("return navigator.userAgent || '';") or "").strip()
        except Exception:
            user_agent = ""
        did = _get_session_cookie(
            session,
            "oai-did",
            preferred_domains=(".openai.com", "auth.openai.com", ".chatgpt.com", "chatgpt.com"),
        )
        bootstrap_succeeded = True
        return ProtocolBrowserBootstrapResult(
            current_url=str(getattr(driver, "current_url", "") or "").strip(),
            did=did or device_id,
            user_agent=user_agent.replace("HeadlessChrome/", "Chrome/"),
            imported_cookie_count=imported_cookie_count,
            auth_url=str(getattr(oauth, "auth_url", "") or "").strip(),
            auth_state=str(getattr(oauth, "state", "") or "").strip(),
        )
    finally:
        if driver is not None and not bootstrap_succeeded and _protocol_browser_native_keep_failed_browser():
            _preserve_protocol_browser_native_failure(
                driver=driver,
                proxy_dir=str(proxy_dir or "").strip() or None,
                browser_user_data_dir="",
                driver_cleanup_user_data_dir=str(getattr(driver, "_protocol_cleanup_user_data_dir", "") or "").strip(),
                note="browser_native_chatgpt_oauth_bootstrap_failed",
            )
        else:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            if proxy_dir:
                try:
                    shutil.rmtree(str(proxy_dir), ignore_errors=True)
                except Exception:
                    pass


def _maybe_prime_protocol_auth_session_with_browser(
    session: requests.Session,
    *,
    sentinel_context: ProtocolSentinelContext,
    explicit_proxy: str | None,
    reason: str,
) -> tuple[ProtocolSentinelContext, ProtocolBrowserBootstrapResult | None]:
    if not env_flag(PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV, True):
        return sentinel_context, None

    before_cookie_names = _session_cookie_name_summary(session, limit=50)
    try:
        result = _prime_protocol_auth_session_with_browser(
            session,
            explicit_proxy=explicit_proxy,
            device_id=sentinel_context.device_id,
        )
    except Exception as exc:
        print(
            "[python-protocol-service] browser bootstrap fallback failed "
            f"reason={reason} err={exc}"
        )
        return sentinel_context, None

    updated_user_agent = str(result.user_agent or sentinel_context.user_agent).strip() or sentinel_context.user_agent
    session.headers.update({"user-agent": updated_user_agent})
    updated_context = _clone_protocol_sentinel_context(
        sentinel_context,
        user_agent=updated_user_agent,
        device_id=result.did or sentinel_context.device_id,
    )
    print(
        "[python-protocol-service] browser bootstrap fallback succeeded "
        f"reason={reason} url={result.current_url or '<none>'} "
        f"imported_cookies={result.imported_cookie_count} "
        f"cookie_names_before={before_cookie_names} "
        f"cookie_names_after={_session_cookie_name_summary(session, limit=50)} "
        f"did_len={len(result.did)}"
    )
    return updated_context, result


def _maybe_prime_protocol_auth_session_with_clearance(
    session: requests.Session,
    *,
    sentinel_context: ProtocolSentinelContext,
    explicit_proxy: str | None,
    reason: str,
    website_url: str,
) -> tuple[ProtocolSentinelContext, bool]:
    try:
        clearance_result = solve_cloudflare_clearance(
            website_url=website_url,
            proxy=explicit_proxy,
            user_agent=sentinel_context.user_agent,
        )
        clearance_cookies = clearance_result.get("cookies")
        imported_cookie_count = _apply_captcha_cookies_to_session(
            session,
            clearance_cookies,
        )
        updated_did = _get_session_cookie(
            session,
            "oai-did",
            preferred_domains=(".openai.com", "auth.openai.com", ".chatgpt.com", "chatgpt.com"),
        )
        updated_context = _clone_protocol_sentinel_context(
            sentinel_context,
            device_id=updated_did or sentinel_context.device_id,
        )
        print(
            "[python-protocol-service] easycaptcha clearance primed protocol session "
            f"reason={reason} url={_format_logged_url(website_url)} imported_cookies={imported_cookie_count} "
            f"cookie_names={_captcha_cookie_name_summary(clearance_cookies)} "
            f"did_len={len(updated_did or sentinel_context.device_id)}"
        )
        return updated_context, True
    except Exception as exc:
        print(
            "[python-protocol-service] easycaptcha clearance prime failed "
            f"reason={reason} url={_format_logged_url(website_url)} err={exc}"
        )
        return sentinel_context, False


def _maybe_prime_browser_driver_with_cloudflare_clearance(
    driver: Any,
    *,
    session: requests.Session,
    website_url: str,
    explicit_proxy: str | None,
    user_agent: str,
    reason: str,
) -> int:
    try:
        clearance_result = solve_cloudflare_clearance(
            website_url=website_url,
            proxy=explicit_proxy,
            user_agent=user_agent,
        )
        clearance_cookies = clearance_result.get("cookies")
        session_import_count = _apply_captcha_cookies_to_session(
            session,
            clearance_cookies,
        )
        browser_import_count = _import_exported_cookies_into_browser_driver(
            driver,
            exported_cookies=list(clearance_cookies or []),
        )
        print(
            "[python-protocol-service] easycaptcha clearance primed browser driver "
            f"reason={reason} url={_format_logged_url(website_url)} "
            f"browser_imported={browser_import_count} session_imported={session_import_count} "
            f"cookie_names={_captcha_cookie_name_summary(clearance_cookies)}"
        )
        return browser_import_count
    except Exception as exc:
        print(
            "[python-protocol-service] easycaptcha browser clearance prime failed "
            f"reason={reason} url={_format_logged_url(website_url)} err={exc}"
        )
        return 0


def _maybe_prime_protocol_auth_session_with_easycaptcha_browser_bootstrap(
    session: requests.Session,
    *,
    sentinel_context: ProtocolSentinelContext,
    explicit_proxy: str | None,
    reason: str,
) -> tuple[ProtocolSentinelContext, ProtocolBrowserBootstrapResult | None]:
    session_cookies = _export_session_cookies_for_browser_sentinel(session)
    before_cookie_names = _session_cookie_name_summary(session, limit=50)
    try:
        bootstrap_result = solve_browser_auth_bootstrap(
            website_url=CHATGPT_LOGIN_URL,
            proxy=explicit_proxy,
            user_agent=sentinel_context.user_agent,
            cookies=session_cookies,
        )
    except Exception as exc:
        print(
            "[python-protocol-service] easycaptcha browser bootstrap failed "
            f"reason={reason} err={exc}"
        )
        return sentinel_context, None

    imported_cookie_count = _apply_captcha_cookies_to_session(
        session,
        bootstrap_result.get("cookies"),
    )
    updated_did = str(
        bootstrap_result.get("deviceId")
        or _get_session_cookie(
            session,
            "oai-did",
            preferred_domains=(".openai.com", "auth.openai.com", ".chatgpt.com", "chatgpt.com"),
        )
        or sentinel_context.device_id
    ).strip() or sentinel_context.device_id
    updated_user_agent = str(
        bootstrap_result.get("userAgent") or sentinel_context.user_agent
    ).strip() or sentinel_context.user_agent
    session.headers.update({"user-agent": updated_user_agent})
    updated_context = _clone_protocol_sentinel_context(
        sentinel_context,
        user_agent=updated_user_agent,
        device_id=updated_did,
    )
    result = ProtocolBrowserBootstrapResult(
        current_url=str(bootstrap_result.get("currentUrl") or CHATGPT_LOGIN_URL).strip(),
        did=updated_did,
        user_agent=updated_user_agent,
        imported_cookie_count=imported_cookie_count,
        auth_url=str(bootstrap_result.get("authUrl") or "").strip(),
        auth_state=str(bootstrap_result.get("authState") or "").strip(),
    )
    print(
        "[python-protocol-service] easycaptcha browser bootstrap succeeded "
        f"reason={reason} url={_format_logged_url(result.current_url)} "
        f"imported_cookies={imported_cookie_count} "
        f"cookie_names_before={before_cookie_names} "
        f"cookie_names_after={_session_cookie_name_summary(session, limit=50)} "
        f"did_len={len(updated_did)} "
        f"auth_state_len={len(result.auth_state)}"
    )
    return updated_context, result


def _browser_sentinel_target_url_for_request_kind(request_kind: str) -> str | None:
    if request_kind == "signup-authorize-continue":
        return CREATE_ACCOUNT_PASSWORD_REFERER
    if request_kind == "signup-user-register":
        return CREATE_ACCOUNT_PASSWORD_REFERER
    if request_kind == "signup-create-account":
        return ABOUT_YOU_REFERER
    return None


def _browser_sentinel_flow_for_request_kind(request_kind: str) -> str | None:
    signup_flow_map = {
        "signup-authorize-continue": "authorize_continue",
        "signup-user-register": "username_password_create",
        "signup-create-account": "oauth_create_account",
        "repair-password-verify": "password_verify",
        "repair-authorize-continue": "authorize_continue",
    }
    return signup_flow_map.get(request_kind)


def _browser_passkey_capability_header_value(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    capabilities = payload.get("passkeyCapabilities")
    if not isinstance(capabilities, dict):
        return None
    normalized = {
        "conditionalCreate": bool(capabilities.get("conditionalCreate")),
        "conditionalGet": bool(capabilities.get("conditionalGet")),
        "relatedOrigins": bool(capabilities.get("relatedOrigins")),
    }
    if not any(normalized.values()):
        return None
    return json.dumps(normalized, separators=(",", ":"))


def _sentinel_browser_timezone_label(offset_minutes: int) -> str:
    if int(offset_minutes) == 480:
        return "中国标准时间"
    if int(offset_minutes) == 0:
        return "Coordinated Universal Time"
    return ""


def _sentinel_browser_date_string(profile: dict[str, Any] | None) -> str:
    payload = dict(profile or {})
    offset_minutes = int(payload.get("timezone_offset_min") or 480)
    tz = timezone(timedelta(minutes=offset_minutes))
    now = datetime.now(tz)
    sign = "+" if offset_minutes >= 0 else "-"
    abs_minutes = abs(offset_minutes)
    hours = abs_minutes // 60
    minutes = abs_minutes % 60
    base = now.strftime("%a %b %d %Y %H:%M:%S")
    label = _sentinel_browser_timezone_label(offset_minutes)
    if label:
        return f"{base} GMT{sign}{hours:02d}{minutes:02d} ({label})"
    return f"{base} GMT{sign}{hours:02d}{minutes:02d}"


def _sentinel_browser_shim_payload(profile: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(profile or {})
    language = str(payload.get("language") or "en").strip() or "en"
    languages_join = str(payload.get("languages_join") or language).strip() or language
    languages = [part.strip() for part in languages_join.split(",") if str(part or "").strip()]
    if not languages:
        languages = [language]
    navigator_probe = str(payload.get("navigator_probe") or "").strip()
    navigator_probe_name = ""
    product_sub = "20030107"
    if "−" in navigator_probe:
        navigator_probe_name, navigator_probe_value = navigator_probe.split("−", 1)
        navigator_probe_name = navigator_probe_name.strip()
        navigator_probe_value = navigator_probe_value.strip()
        if navigator_probe_name == "productSub" and navigator_probe_value:
            product_sub = navigator_probe_value
    elif navigator_probe:
        navigator_probe_name = navigator_probe
    if not navigator_probe_name:
        navigator_probe_name = "canLoadAdAuctionFencedFrame"
    if navigator_probe_name != "productSub":
        product_sub = "20030107"
    window_probe = str(payload.get("window_probe") or "onmouseover")
    script_url = str(payload.get("script_url") or "https://sentinel.openai.com/backend-api/sentinel/sdk.js")
    if "backend-api/sentinel/sdk.js" in script_url and navigator_probe_name == "canLoadAdAuctionFencedFrame" and window_probe == "onmouseover":
        math_random_sequence = [0.15625, 0.28125, 0.421875, 0.125]
    elif navigator_probe_name == "productSub" and window_probe == "close":
        math_random_sequence = [0.78125, 0.4375, 0.5625, 0.25]
    else:
        math_random_sequence = [0.5, 0.28125, 0.421875, 0.125]
    return {
        "language": language,
        "languages": languages,
        "hardwareConcurrency": int(payload.get("core") or payload.get("hardware_concurrency") or 12),
        "performanceNow": float(payload.get("performance_now") or 9272.400000000373),
        "timeOrigin": float(payload.get("time_origin") or (int(time.time() * 1000) - 9000)),
        "dateString": _sentinel_browser_date_string(payload),
        "documentProbe": str(payload.get("document_probe") or "_reactListeningx9ytk7ovr7"),
        "windowProbe": window_probe,
        "navigatorProbeName": navigator_probe_name,
        "productSub": product_sub,
        "scriptUrl": script_url,
        "mathRandomSequence": math_random_sequence,
    }


def _protocol_auth_cookie_summary(session: requests.Session) -> str:
    interesting = [
        "cf_clearance",
        "__cf_bm",
        "_cfuvid",
        "oai-did",
        "__Secure-next-auth.state",
        "__Secure-next-auth.callback-url",
        "auth_provider",
        "hydra_redirect",
        "login_session",
        "oai-client-auth-session",
        "unified_session_manifest",
    ]
    parts: list[str] = []
    for name in interesting:
        value = _get_session_cookie(
            session,
            name,
            preferred_domains=(
                "auth.openai.com",
                ".openai.com",
                "chatgpt.com",
                ".chatgpt.com",
            ),
        )
        if not value:
            continue
        parts.append(f"{name}:{len(value)}")
    return ",".join(parts) if parts else "<none>"


def _sentinel_header_debug_summary(headers: dict[str, str]) -> str:
    sentinel_raw = str(headers.get("openai-sentinel-token") or "").strip()
    passkey_raw = str(headers.get("ext-passkey-client-capabilities") or "").strip()
    if not sentinel_raw:
        return f"token=<missing> passkey={'present' if passkey_raw else 'missing'}"
    try:
        payload = json.loads(sentinel_raw)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    p_fields: list[Any] = []
    try:
        decoded_p = base64.b64decode(str(payload.get("p") or ""), validate=False)
        parsed_p = json.loads(decoded_p.decode("utf-8", errors="replace"))
        if isinstance(parsed_p, list):
            p_fields = parsed_p
    except Exception:
        p_fields = []
    p3 = p_fields[3] if len(p_fields) > 3 else None
    p9 = p_fields[9] if len(p_fields) > 9 else None
    p10 = p_fields[10] if len(p_fields) > 10 else None
    p12 = p_fields[12] if len(p_fields) > 12 else None
    return (
        f"p_len={len(str(payload.get('p') or ''))} "
        f"t_len={len(str(payload.get('t') or ''))} "
        f"has_c={bool(str(payload.get('c') or '').strip())} "
        f"id_len={len(str(payload.get('id') or ''))} "
        f"flow={str(payload.get('flow') or '<none>')} "
        f"p3={p3!r} "
        f"p9={p9!r} "
        f"p10={str(p10 or '')[:80]!r} "
        f"p12={str(p12 or '')[:80]!r} "
        f"passkey={'present' if passkey_raw else 'missing'}"
    )


def _request_header_snapshot(
    session: requests.Session,
    explicit_headers: dict[str, str],
    request_url: str | None = None,
) -> str:
    merged: dict[str, str] = {}
    try:
        for key, value in dict(getattr(session, "headers", {}) or {}).items():
            normalized_key = str(key or "").strip().lower()
            if normalized_key:
                merged[normalized_key] = str(value or "").strip()
    except Exception:
        pass
    for key, value in dict(explicit_headers or {}).items():
        normalized_key = str(key or "").strip().lower()
        if normalized_key:
            merged[normalized_key] = str(value or "").strip()

    interesting_keys = [
        "origin",
        "referer",
        "accept",
        "content-type",
        "accept-language",
        "user-agent",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
        "sec-fetch-site",
        "sec-fetch-mode",
        "sec-fetch-dest",
        "oai-device-id",
        "oai-language",
        "ext-passkey-client-capabilities",
        "openai-sentinel-so-token",
    ]
    snapshot: dict[str, str] = {}
    for key in interesting_keys:
        value = str(merged.get(key) or "").strip()
        if value:
            snapshot[key] = value

    normalized_request_url = str(request_url or "").strip()
    if normalized_request_url:
        cookie_header = _resolve_cookie_header_for_request(
            session,
            normalized_request_url,
            explicit_headers=merged,
        )
        cookie_names = _extract_cookie_names_from_header(cookie_header)
        if cookie_header:
            snapshot["cookie_header_len"] = str(len(cookie_header))
            snapshot["cookie_names"] = ",".join(cookie_names[:24])
            snapshot["cookie_has_oai_client_auth_info"] = str("oai-client-auth-info" in cookie_names).lower()
            snapshot["cookie_has_auth_session_minimized"] = str("auth-session-minimized" in cookie_names).lower()
            snapshot["cookie_has_auth_session_minimized_client_checksum"] = str("auth-session-minimized-client-checksum" in cookie_names).lower()
            snapshot["cookie_has_oai_login_csrf"] = str(any(name.startswith("oai-login-csrf") for name in cookie_names)).lower()
            snapshot["cookie_has_oai_sc"] = str("oai-sc" in cookie_names).lower()

    snapshot["sentinel"] = _sentinel_header_debug_summary(merged)
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))


def _normalize_auth_url_device_id(auth_url: str, *, device_id: str) -> str:
    raw_url = str(auth_url or "").strip()
    normalized_device_id = str(device_id or "").strip()
    if not raw_url or not normalized_device_id:
        return raw_url
    try:
        parsed = urllib.parse.urlsplit(raw_url)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        kept: list[tuple[str, str]] = []
        for key, value in query:
            normalized_key = str(key or "").strip().lower()
            if normalized_key in {"device_id", "ext-oai-did"}:
                continue
            kept.append((key, value))
        kept.append(("device_id", normalized_device_id))
        kept.append(("ext-oai-did", normalized_device_id))
        return urllib.parse.urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urllib.parse.urlencode(kept),
                parsed.fragment,
            )
        )
    except Exception:
        return raw_url


def _resolve_cookie_header_for_request(
    session: requests.Session,
    request_url: str,
    *,
    explicit_headers: dict[str, str] | None = None,
) -> str:
    try:
        request = urllib.request.Request(
            request_url,
            headers=dict(explicit_headers or {}),
            method="GET",
        )
        session.cookies.jar.add_cookie_header(request)
        for key, value in request.header_items():
            if str(key or "").strip().lower() == "cookie":
                return str(value or "").strip()
    except Exception:
        return ""
    return ""


def _extract_cookie_names_from_header(cookie_header: str) -> list[str]:
    normalized = str(cookie_header or "").strip()
    if not normalized:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for part in normalized.split(";"):
        token = str(part or "").strip()
        if not token or "=" not in token:
            continue
        name = token.split("=", 1)[0].strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _masked_debug_value(value: str, *, prefix: int = 8, suffix: int = 6) -> dict[str, Any]:
    normalized = str(value or "").strip()
    if not normalized:
        return {"present": False, "len": 0}
    if len(normalized) <= prefix + suffix:
        masked = normalized
    else:
        masked = f"{normalized[:prefix]}...{normalized[-suffix:]}"
    return {
        "present": True,
        "len": len(normalized),
        "value": masked,
    }


def _extract_query_value(url: str, key: str) -> str:
    normalized_url = str(url or "").strip()
    if not normalized_url:
        return ""
    try:
        parsed = urllib.parse.urlparse(normalized_url)
        values = urllib.parse.parse_qs(parsed.query).get(key) or []
    except Exception:
        values = []
    if not values:
        return ""
    return str(values[0] or "").strip()


def _device_context_debug_summary(
    *,
    session: requests.Session,
    explicit_headers: dict[str, str] | None = None,
    sentinel_context: ProtocolSentinelContext | None = None,
    oauth_auth_url: str | None = None,
    oauth_state: str | None = None,
) -> str:
    headers = dict(explicit_headers or {})
    cookie_did = _get_session_cookie(
        session,
        "oai-did",
        preferred_domains=(".openai.com", "auth.openai.com", ".chatgpt.com", "chatgpt.com"),
    )
    header_did = str(
        headers.get("oai-device-id")
        or headers.get("OAI-Device-Id")
        or ""
    ).strip()
    context_did = str(getattr(sentinel_context, "device_id", "") or "").strip()
    auth_url = str(oauth_auth_url or "").strip()
    auth_url_device_id = _extract_query_value(auth_url, "device_id")
    auth_url_ext_did = _extract_query_value(auth_url, "ext-oai-did")
    auth_url_state = _extract_query_value(auth_url, "state")
    state_value = str(oauth_state or "").strip()

    summary = {
        "context_did": _masked_debug_value(context_did),
        "cookie_oai_did": _masked_debug_value(cookie_did),
        "header_oai_device_id": _masked_debug_value(header_did),
        "auth_url_device_id": _masked_debug_value(auth_url_device_id),
        "auth_url_ext_oai_did": _masked_debug_value(auth_url_ext_did),
        "oauth_state": _masked_debug_value(state_value),
        "auth_url_state": _masked_debug_value(auth_url_state),
        "matches": {
            "context_eq_cookie": bool(context_did and cookie_did and context_did == cookie_did),
            "context_eq_header": bool(context_did and header_did and context_did == header_did),
            "context_eq_auth_url_device_id": bool(context_did and auth_url_device_id and context_did == auth_url_device_id),
            "context_eq_auth_url_ext_did": bool(context_did and auth_url_ext_did and context_did == auth_url_ext_did),
            "cookie_eq_header": bool(cookie_did and header_did and cookie_did == header_did),
            "cookie_eq_auth_url_device_id": bool(cookie_did and auth_url_device_id and cookie_did == auth_url_device_id),
            "cookie_eq_auth_url_ext_did": bool(cookie_did and auth_url_ext_did and cookie_did == auth_url_ext_did),
            "oauth_state_eq_auth_url_state": bool(state_value and auth_url_state and state_value == auth_url_state),
        },
    }
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))


def _hydrate_browser_driver_with_protocol_session_cookies(
    driver: Any,
    *,
    session: requests.Session,
) -> int:
    domain_targets = [
        ("https://chatgpt.com/", {"chatgpt.com", ".chatgpt.com"}),
        ("https://auth.openai.com/", {"auth.openai.com", ".auth.openai.com", ".openai.com", "openai.com"}),
    ]
    imported = 0
    seen: set[tuple[str, str, str]] = set()
    for target_url, allowed_domains in domain_targets:
        try:
            driver.get(target_url)
        except Exception:
            continue
        for cookie in _iter_session_cookie_objects(session):
            name = str(getattr(cookie, "name", "") or "").strip()
            value = str(getattr(cookie, "value", "") or "")
            domain = str(getattr(cookie, "domain", "") or "").strip()
            path = str(getattr(cookie, "path", "") or "/").strip() or "/"
            if not name or not value or domain not in allowed_domains:
                continue
            dedupe = (name, domain, path)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            payload = {
                "name": name,
                "value": value,
                "path": path,
                "secure": bool(getattr(cookie, "secure", False)),
            }
            normalized_domain = domain.lstrip(".")
            if normalized_domain:
                payload["domain"] = normalized_domain
            try:
                expiry = getattr(cookie, "expires", None)
                if expiry:
                    payload["expiry"] = int(expiry)
            except Exception:
                pass
            try:
                driver.add_cookie(payload)
                imported += 1
            except Exception:
                continue
    return imported


def _extract_browser_sentinel_token_payload(token_value: Any) -> dict[str, str] | None:
    parsed: Any = token_value
    if isinstance(parsed, str):
        text = parsed.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except Exception:
            return None
    if not isinstance(parsed, dict):
        return None
    p = str(parsed.get("p") or "").strip()
    t = str(parsed.get("t") or "").strip()
    c = str(parsed.get("c") or "").strip()
    if not p or not t:
        return None
    payload = {"p": p, "t": t}
    if c:
        payload["c"] = c
    return payload


def _decode_wrapped_sentinel_segment(segment: Any) -> str:
    text = str(segment or "").strip()
    if not text:
        return ""
    if text.startswith("gAAAAAB"):
        text = text[len("gAAAAAB"):]
    if text.endswith("~S"):
        text = text[:-2]
    padding = (-len(text)) % 4
    if padding:
        text += "=" * padding
    try:
        return base64.b64decode(text).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _encode_wrapped_sentinel_segment(raw_text: str) -> str:
    body = base64.b64encode(str(raw_text or "").encode("utf-8")).decode("ascii")
    return f"gAAAAAB{body}~S"


def _normalized_browser_signup_p_fields(profile: dict[str, Any] | None) -> dict[int, Any]:
    payload = dict(profile or {})
    persona = str(payload.get("persona") or payload.get("sentinelPersona") or "har1").strip().lower()
    if persona == "har2":
        return {
            0: 4000,
            2: 4294967296,
            3: 25,
            7: "en",
            8: "en",
            9: 14,
            10: "productSub−20030107",
            12: "close",
            13: 11700.79999999702,
            16: 12,
            18: 0,
            19: 0,
            20: 0,
            21: 0,
            22: 0,
            23: 0,
            24: 0,
        }
    return {
        0: 4000,
        2: 4294967296,
        3: 5,
        7: "en",
        8: "en",
        9: 9,
        10: "canLoadAdAuctionFencedFrame−function canLoadAdAuctionFencedFrame() { [native code] }",
        12: "onmouseover",
        13: 9272.400000000373,
        16: 12,
        18: 0,
        19: 0,
        20: 0,
        21: 0,
        22: 0,
        23: 0,
        24: 0,
    }


def _normalize_browser_signup_token_payload(
    token_payload: dict[str, str] | None,
    *,
    profile: dict[str, Any] | None,
) -> dict[str, str] | None:
    if not isinstance(token_payload, dict):
        return token_payload
    p_segment = str(token_payload.get("p") or "").strip()
    if not p_segment:
        return token_payload
    decoded_p = _decode_wrapped_sentinel_segment(p_segment)
    if not decoded_p:
        return token_payload
    try:
        parsed_p = json.loads(decoded_p)
    except Exception:
        return token_payload
    if not isinstance(parsed_p, list) or len(parsed_p) < 25:
        return token_payload
    normalized = list(parsed_p)
    for index, value in _normalized_browser_signup_p_fields(profile).items():
        if 0 <= int(index) < len(normalized):
            normalized[int(index)] = value
    document_probe = str((profile or {}).get("document_probe") or "").strip()
    if document_probe:
        normalized[11] = document_probe
    session_id = str((profile or {}).get("session_id") or "").strip()
    if session_id:
        normalized[14] = session_id
    time_origin = (profile or {}).get("time_origin")
    if time_origin is not None:
        try:
            normalized[17] = float(time_origin)
        except Exception:
            pass
    updated = dict(token_payload)
    updated["p"] = _encode_wrapped_sentinel_segment(
        json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    )
    return updated


def _browser_signup_payload_cache(session: requests.Session) -> dict[str, Any]:
    cache = getattr(session, "_protocol_browser_signup_payload_cache", None)
    if isinstance(cache, dict):
        return cache
    cache = {}
    try:
        setattr(session, "_protocol_browser_signup_payload_cache", cache)
    except Exception:
        pass
    return cache


def _cache_browser_signup_payload(
    session: requests.Session,
    *,
    request_kind: str,
    payload: dict[str, Any] | None,
) -> None:
    cache = _browser_signup_payload_cache(session)
    key = str(request_kind or "").strip()
    if not key:
        return
    if isinstance(payload, dict):
        cache[key] = dict(payload)
    else:
        cache.pop(key, None)


def _get_cached_browser_signup_payload(
    session: requests.Session,
    *,
    request_kind: str,
) -> dict[str, Any] | None:
    cache = _browser_signup_payload_cache(session)
    value = cache.get(str(request_kind or "").strip())
    return dict(value) if isinstance(value, dict) else None


def _export_session_cookies_for_browser_sentinel(
    session: requests.Session,
    *,
    limit: int = 64,
) -> list[dict[str, Any]]:
    exported: list[dict[str, Any]] = []
    for cookie in _iter_session_cookie_objects(session):
        try:
            name = str(getattr(cookie, "name", "") or "").strip()
            if not name:
                continue
            domain = str(getattr(cookie, "domain", "") or "").strip()
            normalized_domain = domain.lower().lstrip(".")
            is_auth_domain = bool(
                normalized_domain.endswith("chatgpt.com")
                or normalized_domain.endswith("openai.com")
                or normalized_domain.endswith("sentinel.openai.com")
            )
            is_nextauth_cookie = (
                name.startswith("__Secure-next-auth")
                or name.startswith("next-auth")
                or name.startswith("__Host-next-auth")
            )
            is_core_cookie = name in {
                "cf_clearance",
                "__cf_bm",
                "_cfuvid",
                "oai-did",
                "oai-client-auth-session",
                "oai-client-auth-info",
                "login_session",
                "unified_session_manifest",
                "hydra_redirect",
                "auth_provider",
                "auth-session-minimized",
                "auth-session-minimized-client-checksum",
                "rg_context",
                "iss_context",
                "oai-sc",
                "oai-asli",
                "g_state",
                "_ga",
                "_ga_9SHBSK2D9J",
                "oai-chat-web-route",
            }
            is_login_csrf_cookie = name.startswith("oai-login-csrf")
            if not (is_auth_domain or is_nextauth_cookie or is_core_cookie or is_login_csrf_cookie):
                continue
            exported.append({
                "name": name,
                "value": str(getattr(cookie, "value", "") or ""),
                "domain": domain,
                "path": str(getattr(cookie, "path", "") or "/").strip() or "/",
                "secure": bool(getattr(cookie, "secure", False)),
                "expires": getattr(cookie, "expires", None),
            })
            if len(exported) >= limit:
                break
        except Exception:
            continue
    return exported


def _capture_browser_signup_sentinel_payload(
    *,
    session: requests.Session,
    explicit_proxy: str | None,
    request_kind: str,
    profile: dict[str, Any] | None = None,
    browser_email: str | None = None,
) -> dict[str, Any] | None:
    target_url = _browser_sentinel_target_url_for_request_kind(request_kind)
    if not target_url or not env_flag(PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV, True):
        return None
    target_flow = _browser_sentinel_flow_for_request_kind(request_kind)
    if not target_flow:
        return None

    session_cookies = _export_session_cookies_for_browser_sentinel(session)
    try:
        service_payload = solve_browser_sentinel_token(
            flow=target_flow,
            website_url=target_url,
            proxy=explicit_proxy,
            user_agent=str(session.headers.get("user-agent") or DEFAULT_PROTOCOL_USER_AGENT),
            cookies=session_cookies,
        )
        imported_cookie_count = _apply_captcha_cookies_to_session(
            session,
            service_payload.get("cookies"),
        )
        token_payload = (
            service_payload.get("tokenPayload")
            if isinstance(service_payload.get("tokenPayload"), dict)
            else {}
        )
        if token_payload:
            browser_did = str(service_payload.get("deviceId") or _get_session_cookie(
                session,
                "oai-did",
                preferred_domains=(".openai.com", "auth.openai.com", ".chatgpt.com", "chatgpt.com"),
            ) or "").strip()
            print(
                "[python-protocol-service] easycaptcha browser sentinel captured "
                f"request_kind={request_kind} url={_format_logged_url(str(service_payload.get('currentUrl') or target_url))} "
                f"has_c={'c' in token_payload} imported_cookies={imported_cookie_count} "
                f"cookie_names={_session_cookie_name_summary(session, limit=50)} "
                f"did_len={len(browser_did)}"
            )
            return {
                "tokenPayload": token_payload,
                "passkeyCapabilities": service_payload.get("passkeyCapabilities"),
                "importedCookieCount": imported_cookie_count,
                "deviceId": browser_did,
                "userAgent": str(service_payload.get("userAgent") or "").strip(),
                "sessionObserverToken": service_payload.get("sessionObserverToken"),
            }
    except Exception as exc:
        print(
            "[python-protocol-service] easycaptcha browser sentinel failed "
            f"request_kind={request_kind} err={exc}"
        )

    try:
        max_attempts = max(
            1,
            int((os.environ.get(PROTOCOL_BROWSER_SENTINEL_MAX_ATTEMPTS_ENV) or "2").strip() or "2"),
        )
    except Exception:
        max_attempts = 2

    new_driver = _load_protocol_browser_new_driver()
    browser_bootstrap = _load_protocol_browser_chatgpt_oauth_bootstrap()
    last_error = ""
    shim_profile = _sentinel_browser_shim_payload(profile)
    for attempt in range(1, max_attempts + 1):
        driver = None
        proxy_dir = None
        browser_token_capture_succeeded = False
        try:
            driver, proxy_dir = new_driver(explicit_proxy)
            browser_bootstrap(driver=driver, proxy=explicit_proxy)
            hydrated_cookie_count = _hydrate_browser_driver_with_protocol_session_cookies(
                driver,
                session=session,
            )
            try:
                driver.set_script_timeout(35)
            except Exception:
                pass
            if request_kind == "signup-user-register" and str(browser_email or "").strip():
                browser_start_url = str(
                    getattr(session, "_new_protocol_signup_oauth_auth_url", "") or ""
                ).strip() or CREATE_ACCOUNT_REFERER
                driver.get(PLATFORM_OPENAI_LOGIN_URL)
                driver.get(browser_start_url)
                authorize_continue_raw = driver.execute_async_script(
                    """
                    const email = String(arguments[0] || "").trim();
                    const done = arguments[1];
                    (async () => {
                      try {
                        const response = await fetch("https://auth.openai.com/api/accounts/authorize/continue", {
                          method: "POST",
                          credentials: "include",
                          headers: {
                            "accept": "application/json",
                            "content-type": "application/json",
                            "origin": "https://auth.openai.com",
                            "referer": "https://auth.openai.com/create-account",
                            "accept-language": "en",
                          },
                          body: JSON.stringify({
                            username: { value: email, kind: "email" },
                            screen_hint: "signup",
                          }),
                        });
                        let payload = {};
                        try {
                          payload = await response.json();
                        } catch (_err) {}
                        done({
                          status: response.status,
                          continueUrl: String((payload && payload.continue_url) || ""),
                          href: location.href,
                          title: document.title,
                        });
                      } catch (error) {
                        done({
                          error: String(error),
                          href: location.href,
                          title: document.title,
                        });
                      }
                    })();
                    """,
                    str(browser_email or "").strip(),
                )
                continue_url = ""
                if isinstance(authorize_continue_raw, dict):
                    browser_authorize_error = str(authorize_continue_raw.get("error") or "").strip()
                    if not browser_authorize_error:
                        continue_url = str(authorize_continue_raw.get("continueUrl") or "").strip()
                used_dom_email_continue = False
                if continue_url:
                    driver.get(continue_url)
                else:
                    try:
                        from selenium.webdriver.common.by import By
                        from selenium.webdriver.common.keys import Keys

                        email_deadline = time.time() + 20.0
                        while time.time() < email_deadline:
                            try:
                                driver.switch_to.default_content()
                            except Exception:
                                pass
                            try:
                                email_input = driver.find_element(
                                    By.CSS_SELECTOR,
                                    'input[type="email"], input[name="email"], input[autocomplete="username"]',
                                )
                                continue_button = None
                                for candidate in driver.find_elements(By.TAG_NAME, "button"):
                                    text = str(getattr(candidate, "text", "") or "").strip()
                                    if text == "Continue":
                                        continue_button = candidate
                                        break
                                if continue_button is not None:
                                    try:
                                        email_input.click()
                                    except Exception:
                                        pass
                                    try:
                                        email_input.send_keys(Keys.CONTROL, "a")
                                    except Exception:
                                        pass
                                    email_input.send_keys(str(browser_email or "").strip())
                                    time.sleep(0.6)
                                    continue_button.click()
                                    used_dom_email_continue = True
                                    break
                            except Exception:
                                pass
                            time.sleep(0.5)
                    except Exception:
                        pass
                    if not used_dom_email_continue:
                        driver.get(target_url)
            else:
                driver.get(PLATFORM_OPENAI_LOGIN_URL)
                driver.get(target_url)
            sdk_ready = False
            deadline = time.time() + 12.0
            while time.time() < deadline:
                try:
                    sdk_ready = bool(
                        driver.execute_script(
                            "return !!(window.SentinelSDK && typeof window.SentinelSDK.token === 'function');"
                        )
                    )
                except Exception:
                    sdk_ready = False
                if sdk_ready:
                    break
                try:
                    time.sleep(0.75)
                except Exception:
                    break
            raw = driver.execute_async_script(
                """
                const profile = arguments[0] || {};
                const done = arguments[1];
                (async () => {
                  try {
                    const defineValue = (target, key, value, enumerable = true) => {
                      try {
                        Object.defineProperty(target, key, {
                          value,
                          configurable: true,
                          enumerable,
                          writable: true,
                        });
                        return true;
                      } catch (_err) {
                        return false;
                      }
                    };
                    const defineGetter = (target, key, getter, enumerable = true) => {
                      try {
                        Object.defineProperty(target, key, {
                          get: getter,
                          configurable: true,
                          enumerable,
                        });
                        return true;
                      } catch (_err) {
                        return false;
                      }
                    };
                    const preferredLang = String(profile.language || "en");
                    const preferredLanguages = Array.isArray(profile.languages) && profile.languages.length
                      ? profile.languages.map((item) => String(item))
                      : [preferredLang];
                    const desiredScriptUrl = String(profile.scriptUrl || "").trim();
                    const navProto = Object.getPrototypeOf(navigator);
                    const docProto = Object.getPrototypeOf(document);
                    const winProto = Object.getPrototypeOf(window);
                    defineGetter(navigator, "language", () => preferredLang, true);
                    defineGetter(navProto, "language", () => preferredLang, false);
                    defineGetter(navigator, "languages", () => preferredLanguages, true);
                    defineGetter(navProto, "languages", () => preferredLanguages, false);
                    defineGetter(navigator, "hardwareConcurrency", () => Number(profile.hardwareConcurrency || 12), true);
                    defineGetter(navProto, "hardwareConcurrency", () => Number(profile.hardwareConcurrency || 12), false);
                    defineGetter(navigator, "productSub", () => String(profile.productSub || "20030107"), true);
                    defineGetter(navProto, "productSub", () => String(profile.productSub || "20030107"), false);
                    if (String(profile.navigatorProbeName || "") === "canLoadAdAuctionFencedFrame") {
                      const fn = function canLoadAdAuctionFencedFrame() { return true; };
                      try {
                        Object.defineProperty(fn, "toString", {
                          value: () => "function canLoadAdAuctionFencedFrame() { [native code] }",
                          configurable: true,
                        });
                      } catch (_err) {}
                      defineValue(navigator, "canLoadAdAuctionFencedFrame", fn, true);
                      defineValue(navProto, "canLoadAdAuctionFencedFrame", fn, false);
                    }
                    const documentProbe = String(profile.documentProbe || "");
                    const windowProbe = String(profile.windowProbe || "");
                    if (documentProbe) {
                      defineValue(document, documentProbe, true, true);
                    }
                    if (windowProbe) {
                      defineValue(window, windowProbe, null, true);
                    }
                    const nativeClose = window.close || function close() {};
                    try {
                      Object.defineProperty(nativeClose, "toString", {
                        value: () => "function close() { [native code] }",
                        configurable: true,
                      });
                    } catch (_err) {}
                    defineValue(window, "close", nativeClose, true);
                    defineValue(window, "onmouseover", window.onmouseover ?? null, true);
                    const perfNow = Number(profile.performanceNow || 9272.400000000373);
                    const perfTimeOrigin = Number(profile.timeOrigin || Date.now() - 9000);
                    try {
                      defineValue(performance, "now", () => perfNow, false);
                    } catch (_err) {}
                    try {
                      defineGetter(performance, "timeOrigin", () => perfTimeOrigin, false);
                    } catch (_err) {}
                    try {
                      const perfProto = Object.getPrototypeOf(performance);
                      if (perfProto) {
                        defineValue(perfProto, "now", () => perfNow, false);
                        defineGetter(perfProto, "timeOrigin", () => perfTimeOrigin, false);
                      }
                    } catch (_err) {}
                    if (profile.dateString) {
                      const originalDateToString = Date.prototype.toString;
                      Date.prototype.toString = function toString() {
                        try {
                          if (this instanceof Date) {
                            return String(profile.dateString);
                          }
                        } catch (_err) {}
                        return originalDateToString.call(this);
                      };
                    }
                    const prioritizeKey = (keys, preferred) => {
                      const normalized = String(preferred || "");
                      if (!normalized) {
                        return keys;
                      }
                      const rest = keys.filter((item) => item !== normalized);
                      return [normalized, ...rest];
                    };
                    const clampKeys = (keys, preferred, extras = []) => {
                      const normalizedPreferred = String(preferred || "").trim();
                      const preferredExtras = (Array.isArray(extras) ? extras : [])
                        .map((item) => String(item || "").trim())
                        .filter(Boolean);
                      const normalizedKeys = (Array.isArray(keys) ? keys : [])
                        .map((item) => String(item || "").trim())
                        .filter(Boolean);
                      const front = [];
                      const pushIfPresent = (candidate) => {
                        if (!candidate) {
                          return;
                        }
                        if (normalizedKeys.includes(candidate) && !front.includes(candidate)) {
                          front.push(candidate);
                        }
                      };
                      pushIfPresent(normalizedPreferred);
                      preferredExtras.forEach(pushIfPresent);
                      if (front.length) {
                        return [front[0]];
                      }
                      return normalizedPreferred ? [normalizedPreferred] : normalizedKeys;
                    };
                    const desiredNavigatorKeys = clampKeys(
                      Object.getOwnPropertyNames(navProto),
                      String(profile.navigatorProbeName || ""),
                      ["productSub", "canLoadAdAuctionFencedFrame"],
                    );
                    const desiredDocumentKeys = clampKeys(
                      Object.getOwnPropertyNames(document),
                      documentProbe,
                      ["_reactListeningx9ytk7ovr7", "location"],
                    );
                    const desiredWindowKeys = clampKeys(
                      Object.getOwnPropertyNames(window),
                      windowProbe,
                      ["close", "onmouseover"],
                    );
                    const realMathRandom = Math.random.bind(Math);
                    const randomSequence = Array.isArray(profile.mathRandomSequence) && profile.mathRandomSequence.length
                      ? profile.mathRandomSequence
                          .map((value) => Number(value))
                          .filter((value) => Number.isFinite(value) && value >= 0 && value < 1)
                      : [];
                    let randomIndex = 0;
                    Math.random = function patchedRandom() {
                      if (randomIndex < randomSequence.length) {
                        const value = randomSequence[randomIndex];
                        randomIndex += 1;
                        return value;
                      }
                      return realMathRandom();
                    };
                    const realObjectKeys = Object.keys.bind(Object);
                    Object.keys = function patchedKeys(obj) {
                      const keys = realObjectKeys(obj);
                      if (obj === document || obj === docProto) {
                        return desiredDocumentKeys.length ? desiredDocumentKeys.slice() : keys;
                      }
                      if (obj === window || obj === globalThis || obj === winProto) {
                        return desiredWindowKeys.length ? desiredWindowKeys.slice() : keys;
                      }
                      const navProbeName = String(profile.navigatorProbeName || "");
                      if ((obj === navigator || obj === navProto) && navProbeName) {
                        return desiredNavigatorKeys.length ? desiredNavigatorKeys.slice() : prioritizeKey(keys.includes(navProbeName) ? keys : [navProbeName, ...keys], navProbeName);
                      }
                      return keys;
                    };
                    const realOwnNames = Object.getOwnPropertyNames.bind(Object);
                    Object.getOwnPropertyNames = function patchedOwnNames(obj) {
                      const keys = realOwnNames(obj);
                      if (obj === document || obj === docProto) {
                        return desiredDocumentKeys.length ? desiredDocumentKeys.slice() : keys;
                      }
                      if (obj === window || obj === globalThis || obj === winProto) {
                        return desiredWindowKeys.length ? desiredWindowKeys.slice() : keys;
                      }
                      const navProbeName = String(profile.navigatorProbeName || "");
                      if ((obj === navigator || obj === navProto) && navProbeName) {
                        return desiredNavigatorKeys.length ? desiredNavigatorKeys.slice() : prioritizeKey(keys.includes(navProbeName) ? keys : [navProbeName, ...keys], navProbeName);
                      }
                      return keys;
                    };
                    const realArrayFrom = Array.from.bind(Array);
                    Array.from = function patchedArrayFrom(source, mapFn, thisArg) {
                      const values = realArrayFrom(source, mapFn, thisArg);
                      try {
                        if (source === document.scripts && desiredScriptUrl) {
                          const normalizedDesired = desiredScriptUrl.trim();
                          const selected = values.filter((item) => {
                            const src = String(item && item.src ? item.src : "");
                            return src === normalizedDesired;
                          });
                          if (selected.length) {
                            return selected;
                          }
                          return [{ src: normalizedDesired }];
                        }
                      } catch (_err) {}
                      return values;
                    };
                    const sdk = window.SentinelSDK;
                    if (!sdk || typeof sdk.token !== 'function') {
                      throw new Error('sentinel_sdk_missing');
                    }
                    const passkeyCapabilities = {
                      conditionalCreate: false,
                      conditionalGet: false,
                      relatedOrigins: false,
                    };
                    const token = await Promise.race([
                      sdk.token(),
                      new Promise((_, reject) => setTimeout(() => reject(new Error('sentinel_token_timeout')), 25000)),
                    ]);
                    done({
                      href: location.href,
                      title: document.title,
                      token,
                      shimProfile: profile,
                      passkeyCapabilities,
                    });
                  } catch (error) {
                    done({
                      error: String(error),
                      href: location.href,
                      title: document.title,
                    });
                  }
                })();
                """,
                shim_profile,
            )
            if not isinstance(raw, dict):
                raise RuntimeError("browser_sentinel_invalid_result")
            error = str(raw.get("error") or "").strip()
            if error:
                raise RuntimeError(error)
            token_payload = _extract_browser_sentinel_token_payload(raw.get("token"))
            if not token_payload:
                raise RuntimeError("browser_sentinel_missing_pt")
            token_payload = _normalize_browser_signup_token_payload(
                token_payload,
                profile=profile,
            ) or token_payload
            imported_cookie_count = _import_browser_driver_cookies_into_session(session, driver=driver)
            browser_cookies: list[dict[str, Any]] = []
            try:
                raw_browser_cookies = driver.get_cookies() or []
                if isinstance(raw_browser_cookies, list):
                    for cookie in raw_browser_cookies:
                        if isinstance(cookie, dict):
                            browser_cookies.append(dict(cookie))
            except Exception:
                browser_cookies = []
            browser_did = _get_session_cookie(
                session,
                "oai-did",
                preferred_domains=(".openai.com", "auth.openai.com", ".chatgpt.com", "chatgpt.com"),
            )
            browser_user_agent = ""
            try:
                browser_user_agent = str(driver.execute_script("return navigator.userAgent || '';") or "").strip()
            except Exception:
                browser_user_agent = ""
            print(
                "[python-protocol-service] browser sentinel token captured "
                f"request_kind={request_kind} attempt={attempt} url={_format_logged_url(str(raw.get('href') or target_url))} "
                f"title={(str(raw.get('title') or '<none>') or '<none>')[:80]} "
                f"has_c={'c' in token_payload} hydrated_cookies={hydrated_cookie_count} "
                f"imported_cookies={imported_cookie_count} "
                f"p_len={len(str(token_payload.get('p') or ''))} "
                f"t_len={len(str(token_payload.get('t') or ''))} "
                f"c_len={len(str(token_payload.get('c') or ''))} "
                f"cookie_names={_session_cookie_name_summary(session, limit=50)} "
                f"did_len={len(browser_did)}"
            )
            browser_token_capture_succeeded = True
            return {
                "tokenPayload": token_payload,
                "passkeyCapabilities": raw.get("passkeyCapabilities"),
                "importedCookieCount": imported_cookie_count,
                "deviceId": browser_did,
                "userAgent": browser_user_agent,
                "browserCookies": browser_cookies,
            }
        except Exception as exc:
            last_error = str(exc)
            print(
                "[python-protocol-service] browser sentinel token capture failed "
                f"request_kind={request_kind} attempt={attempt} err={last_error}"
            )
        finally:
            if driver is not None and not browser_token_capture_succeeded and _protocol_browser_native_keep_failed_browser():
                _preserve_protocol_browser_native_failure(
                    driver=driver,
                    proxy_dir=str(proxy_dir or "").strip() or None,
                    browser_user_data_dir="",
                    driver_cleanup_user_data_dir=str(getattr(driver, "_protocol_cleanup_user_data_dir", "") or "").strip(),
                    note=f"browser_sentinel_capture_failed:{request_kind}:attempt={attempt}",
                )
            else:
                if driver is not None:
                    try:
                        driver.quit()
                    except Exception:
                        pass
                if proxy_dir:
                    try:
                        shutil.rmtree(str(proxy_dir), ignore_errors=True)
                    except Exception:
                        pass

    if last_error:
        print(
            "[python-protocol-service] browser sentinel token capture exhausted "
            f"request_kind={request_kind} err={last_error}"
        )
    return None


def _browser_collect_page_state(driver: Any) -> dict[str, Any]:
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    try:
        return driver.execute_script(
            """
            return {
              href: location.href,
              title: document.title,
              readyState: document.readyState,
              bodyText: String((document.body && document.body.innerText) || '').slice(0, 400),
              hasEmail: !!document.querySelector('input[type="email"], input[name="email"], input[autocomplete="username"]'),
              hasPassword: !!document.querySelector('input[type="password"], input[name="password"], input[name="new-password"]'),
              buttonTexts: Array.from(document.querySelectorAll('button')).map((el) => String((el.innerText || el.textContent || '')).trim()).filter(Boolean).slice(0, 12),
            };
            """
        ) or {}
    except Exception:
        return {}


def _browser_page_requires_recovery(page_state: dict[str, Any]) -> bool:
    href = str(page_state.get("href") or "").lower()
    title = str(page_state.get("title") or "").lower()
    body = str(page_state.get("bodyText") or "").lower()
    combined = "\n".join([href, title, body])
    return any(
        marker in combined
        for marker in (
            "just a moment",
            "oops, an error occurred",
            "attention required",
            "verify you are human",
            "cdn-cgi/challenge-platform",
            "your session has ended",
        )
    )


def _browser_page_is_cloudflare_wait(page_state: dict[str, Any]) -> bool:
    href = str(page_state.get("href") or "").lower()
    title = str(page_state.get("title") or "").lower()
    body = str(page_state.get("bodyText") or "").lower()
    combined = "\n".join([href, title, body])
    return any(
        marker in combined
        for marker in (
            "just a moment",
            "attention required",
            "verify you are human",
            "cdn-cgi/challenge-platform",
            "performing security verification",
        )
    )


def _browser_page_is_password_surface(page_state: dict[str, Any]) -> bool:
    href = str(page_state.get("href") or "")
    title = str(page_state.get("title") or "")
    body = str(page_state.get("bodyText") or "")
    has_password = bool(page_state.get("hasPassword"))
    return bool(
        "https://auth.openai.com/create-account/password" in href
        and (
            has_password
            or "Create a password" in title
            or "Create a password" in body
        )
    )


def _browser_page_is_password_shell(page_state: dict[str, Any]) -> bool:
    href = str(page_state.get("href") or "")
    if "https://auth.openai.com/create-account/password" not in href:
        return False
    title = str(page_state.get("title") or "").strip()
    body = str(page_state.get("bodyText") or page_state.get("body") or "").strip()
    has_password = bool(page_state.get("hasPassword"))
    buttons = list(page_state.get("buttonTexts") or page_state.get("buttons") or [])
    return not has_password and not title and not body and not buttons


def _browser_page_is_email_shell(page_state: dict[str, Any]) -> bool:
    href = str(page_state.get("href") or "")
    if "https://auth.openai.com/log-in-or-create-account" not in href:
        return False
    title = str(page_state.get("title") or "").strip()
    body = str(page_state.get("bodyText") or page_state.get("body") or "").strip()
    has_email = bool(page_state.get("hasEmail"))
    buttons = list(page_state.get("buttonTexts") or page_state.get("buttons") or [])
    return not has_email and not title and not body and not buttons


def _browser_native_request_capture_script() -> str:
    return """
if (!window.__codexCapturedFetchesInstalled) {
  window.__codexCapturedFetchesInstalled = true;
  window.__capturedFetches = [];
  window.__capturedXhrs = [];
  window.__capturedFormSubmits = [];
  window.__codexConsoleErrors = [];
  window.__codexConsoleWarnings = [];
  window.__codexWindowErrors = [];
  window.__codexUnhandledRejections = [];
  window.__codexResourceErrors = [];
  window.__codexLifecycle = [];
  const lifecyclePush = (kind, extra) => {
    try {
      window.__codexLifecycle.push(Object.assign({
        kind,
        href: String(location.href || ''),
        readyState: String(document.readyState || ''),
        ts: Date.now(),
      }, extra || {}));
    } catch (_err) {}
  };
  lifecyclePush('capture-installed');
  document.addEventListener('readystatechange', () => {
    lifecyclePush('readystatechange');
  }, true);
  window.addEventListener('load', () => {
    lifecyclePush('load');
  }, true);
  window.addEventListener('pageshow', () => {
    lifecyclePush('pageshow');
  }, true);
  try {
    if (window.PublicKeyCredential) {
      try { Object.defineProperty(window.PublicKeyCredential, 'isConditionalMediationAvailable', { configurable: true, value: undefined }); } catch (_err) {}
      try { Object.defineProperty(window.PublicKeyCredential, 'getClientCapabilities', { configurable: true, value: undefined }); } catch (_err) {}
    }
    if (navigator.credentials) {
      try { Object.defineProperty(navigator.credentials, 'get', { configurable: true, value: undefined }); } catch (_err) {}
    }
    lifecyclePush('passkey-capabilities-disabled');
  } catch (_err) {}

  const originalConsoleError = console.error ? console.error.bind(console) : null;
  const originalConsoleWarn = console.warn ? console.warn.bind(console) : null;
  console.error = function() {
    try {
      window.__codexConsoleErrors.push({
        args: Array.from(arguments || []).map((value) => {
          try { return String(value); } catch (_err) { return '<unstringifiable>'; }
        }),
        href: String(location.href || ''),
        ts: Date.now(),
      });
    } catch (_err) {}
    if (originalConsoleError) {
      return originalConsoleError.apply(console, arguments);
    }
  };
  console.warn = function() {
    try {
      window.__codexConsoleWarnings.push({
        args: Array.from(arguments || []).map((value) => {
          try { return String(value); } catch (_err) { return '<unstringifiable>'; }
        }),
        href: String(location.href || ''),
        ts: Date.now(),
      });
    } catch (_err) {}
    if (originalConsoleWarn) {
      return originalConsoleWarn.apply(console, arguments);
    }
  };
  window.addEventListener('error', function(event) {
    try {
      window.__codexWindowErrors.push({
        message: String((event && event.message) || ''),
        source: String((event && event.filename) || ''),
        lineno: Number((event && event.lineno) || 0),
        colno: Number((event && event.colno) || 0),
        error: String((event && event.error && event.error.stack) || (event && event.error) || ''),
        href: String(location.href || ''),
        ts: Date.now(),
      });
    } catch (_err) {}
  }, true);
  document.addEventListener('error', function(event) {
    try {
      const target = event && event.target ? event.target : null;
      window.__codexResourceErrors.push({
        tagName: String((target && target.tagName) || ''),
        src: String((target && (target.src || target.href || target.currentSrc)) || ''),
        type: String((target && target.type) || ''),
        rel: String((target && target.rel) || ''),
        href: String(location.href || ''),
        ts: Date.now(),
      });
    } catch (_err) {}
  }, true);
  window.addEventListener('unhandledrejection', function(event) {
    try {
      let reasonText = '';
      try {
        reasonText = String((event && event.reason && event.reason.stack) || (event && event.reason) || '');
      } catch (_err) {}
      window.__codexUnhandledRejections.push({
        reason: reasonText,
        href: String(location.href || ''),
        ts: Date.now(),
      });
    } catch (_err) {}
  }, true);

  try {
    let __codexReactRouterContextValue = undefined;
    Object.defineProperty(window, '__reactRouterContext', {
      configurable: true,
      enumerable: true,
      get() {
        return __codexReactRouterContextValue;
      },
      set(value) {
        __codexReactRouterContextValue = value;
        lifecyclePush('react-router-context-set', {
          hasStream: !!(value && value.stream),
          isSpaMode: !!(value && value.isSpaMode),
        });
      },
    });
  } catch (_err) {}
  try {
    let __codexReactRouterRouteModulesValue = undefined;
    Object.defineProperty(window, '__reactRouterRouteModules', {
      configurable: true,
      enumerable: true,
      get() {
        return __codexReactRouterRouteModulesValue;
      },
      set(value) {
        __codexReactRouterRouteModulesValue = value;
        lifecyclePush('react-router-route-modules-set', {
          keys: value ? Object.keys(value).slice(0, 20) : [],
        });
      },
    });
  } catch (_err) {}

  const originalFetch = window.fetch.bind(window);
  window.fetch = async function(input, init) {
    const req = input instanceof Request ? input : null;
    const url = req ? req.url : String(input || "");
    const method = (init && init.method) || (req && req.method) || "GET";
    const headersObj = {};
    try {
      const headers = new Headers((init && init.headers) || (req && req.headers) || undefined);
      headers.forEach((value, key) => { headersObj[String(key)] = String(value); });
    } catch (_err) {}
    let bodyText = "";
    try {
      const rawBody = init && Object.prototype.hasOwnProperty.call(init, "body") ? init.body : null;
      if (typeof rawBody === "string") {
        bodyText = rawBody;
      }
    } catch (_err) {}
    const record = { url, method, headers: headersObj, body: bodyText, startedAt: Date.now() };
    window.__capturedFetches.push(record);
    try {
      const response = await originalFetch(input, init);
      record.status = response.status;
      record.responseUrl = response.url || url;
      record.responseHeaders = {};
      try {
        response.headers.forEach((value, key) => {
          record.responseHeaders[String(key)] = String(value);
        });
      } catch (_err) {}
      try {
        record.responseBody = await response.clone().text();
      } catch (_err) {
        record.responseBody = "";
      }
      return response;
    } catch (error) {
      record.error = String(error);
      record.stack = String(error && error.stack ? error.stack : "");
      throw error;
    }
  };

  const originalXHROpen = XMLHttpRequest.prototype.open;
  const originalXHRSend = XMLHttpRequest.prototype.send;
  const originalXHRSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__codexRecord = {
      type: 'xhr',
      method: String(method || 'GET'),
      url: String(url || ''),
      headers: {},
      body: '',
      startedAt: Date.now(),
    };
    return originalXHROpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function(key, value) {
    try {
      if (this.__codexRecord && key) {
        this.__codexRecord.headers[String(key)] = String(value);
      }
    } catch (_err) {}
    return originalXHRSetRequestHeader.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function(body) {
    const record = this.__codexRecord || {
      type: 'xhr',
      method: 'GET',
      url: '',
      headers: {},
      body: '',
      startedAt: Date.now(),
    };
    try {
      if (typeof body === 'string') {
        record.body = body;
      }
    } catch (_err) {}
    window.__capturedXhrs.push(record);
    this.addEventListener('loadend', function() {
      try {
        record.status = this.status;
        record.responseUrl = String(this.responseURL || record.url || '');
        record.responseBody = typeof this.responseText === 'string' ? this.responseText : '';
        record.responseHeadersRaw = String(this.getAllResponseHeaders() || '');
      } catch (_err) {}
    });
    return originalXHRSend.apply(this, arguments);
  };

  const recordForm = (form, submitter, mode) => {
    try {
      const action = String((form && form.action) || location.href || '');
      const method = String((form && form.method) || 'GET').toUpperCase();
      const data = new FormData(form);
      const body = [];
      for (const pair of data.entries()) {
        body.push([String(pair[0] || ''), String(pair[1] || '')]);
      }
      window.__capturedFormSubmits.push({
        type: 'form',
        mode,
        action,
        method,
        body,
        submitterText: submitter ? String((submitter.innerText || submitter.textContent || '')).trim() : '',
        startedAt: Date.now(),
      });
    } catch (_err) {}
  };
  const originalFormSubmit = HTMLFormElement.prototype.submit;
  const originalRequestSubmit = HTMLFormElement.prototype.requestSubmit;
  HTMLFormElement.prototype.submit = function() {
    recordForm(this, null, 'submit');
    return originalFormSubmit.apply(this, arguments);
  };
  HTMLFormElement.prototype.requestSubmit = function(submitter) {
    recordForm(this, submitter || null, 'requestSubmit');
    return originalRequestSubmit.apply(this, arguments);
  };
  document.addEventListener('submit', function(event) {
    try {
      recordForm(event.target, event.submitter || null, 'event');
    } catch (_err) {}
  }, true);
}
"""


def _install_browser_native_request_capture_hooks(driver: Any) -> None:
    source = _browser_native_request_capture_script()
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": source})
    except Exception:
        pass
    try:
        driver.execute_script(source)
    except Exception:
        pass


def _browser_native_enable_cdp_network_capture(driver: Any) -> bool:
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception:
        return False
    try:
        driver.get_log("performance")
    except Exception:
        pass
    return True


def _browser_native_drain_cdp_user_register(
    driver: Any,
    *,
    records: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    try:
        entries = driver.get_log("performance")
    except Exception:
        return None
    latest_target: dict[str, Any] | None = None
    for entry in entries:
        try:
            outer = json.loads(str(entry.get("message") or ""))
            message = outer.get("message") or {}
            method = str(message.get("method") or "")
            params = message.get("params") or {}
        except Exception:
            continue
        request_id = str(params.get("requestId") or "")
        if not request_id and method not in {
            "Network.requestWillBeSent",
            "Network.responseReceived",
            "Network.requestWillBeSentExtraInfo",
            "Network.responseReceivedExtraInfo",
            "Network.loadingFinished",
            "Network.loadingFailed",
        }:
            continue

        if method == "Network.requestWillBeSent":
            request = params.get("request") or {}
            url = str(request.get("url") or "")
            if "api/accounts/user/register" not in url:
                continue
            info = records.setdefault(request_id, {"requestId": request_id})
            info["url"] = url
            info["method"] = str(request.get("method") or "")
            info["requestHeaders"] = dict(request.get("headers") or {})
            info["postData"] = str(request.get("postData") or "")
            info["documentURL"] = str(params.get("documentURL") or "")
            info["wallTime"] = params.get("wallTime")
            info["timestamp"] = params.get("timestamp")
            info["initiatorType"] = str((params.get("initiator") or {}).get("type") or "")
            latest_target = info
            continue

        info = records.get(request_id)
        if info is None:
            continue
        latest_target = info
        if method == "Network.requestWillBeSentExtraInfo":
            info["extraRequestHeaders"] = dict(params.get("headers") or {})
        elif method == "Network.responseReceived":
            response = params.get("response") or {}
            info["status"] = int(response.get("status") or 0)
            info["responseUrl"] = str(response.get("url") or info.get("url") or "")
            info["responseHeaders"] = dict(response.get("headers") or {})
            info["mimeType"] = str(response.get("mimeType") or "")
        elif method == "Network.responseReceivedExtraInfo":
            info["extraResponseHeaders"] = dict(params.get("headers") or {})
            info["blockedCookies"] = params.get("blockedCookies") or []
        elif method == "Network.loadingFailed":
            info["loadingFailed"] = True
            info["errorText"] = str(params.get("errorText") or "")
            info["canceled"] = bool(params.get("canceled"))
        elif method == "Network.loadingFinished":
            info["loadingFinished"] = True
            if not info.get("responseBodyFetched") and not info.get("loadingFailed"):
                try:
                    body_result = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id}) or {}
                    body = str(body_result.get("body") or "")
                    if body_result.get("base64Encoded"):
                        body = base64.b64decode(body).decode("utf-8", errors="replace")
                    info["responseBody"] = body
                    info["responseBodyFetched"] = True
                except Exception as exc:
                    info["responseBodyFetchError"] = str(exc)
    return latest_target


def _browser_native_dump_user_register_snapshot(
    *,
    request_headers: dict[str, str],
    response_headers: dict[str, str],
    target: dict[str, Any],
    raw: dict[str, Any],
) -> str:
    snapshot_dir = os.path.abspath(os.path.join(_protocol_services_root(), "..", "tmp"))
    os.makedirs(snapshot_dir, exist_ok=True)
    snapshot_path = os.path.join(snapshot_dir, "browser_native_user_register_last.json")
    payload = {
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "request": {
            "method": str(target.get("method") or ""),
            "url": str(target.get("url") or ""),
            "responseUrl": str(target.get("responseUrl") or ""),
            "headers": request_headers,
            "postData": str(target.get("postData") or ""),
            "initiatorType": str(target.get("initiatorType") or ""),
        },
        "response": {
            "status": int(target.get("status") or 0),
            "headers": response_headers,
            "body": str(target.get("responseBody") or raw.get("body") or ""),
            "errorText": str(target.get("errorText") or ""),
        },
        "page": {
            "href": str(raw.get("href") or ""),
            "title": str(raw.get("title") or ""),
            "cookieNames": list(raw.get("cookieNames") or []),
        },
    }
    with open(snapshot_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return snapshot_path


def _browser_native_dump_page_snapshot(driver: Any, *, note: str) -> str | None:
    snapshot_dir = os.path.abspath(os.path.join(_protocol_services_root(), "..", "tmp"))
    os.makedirs(snapshot_dir, exist_ok=True)
    snapshot_path = os.path.join(snapshot_dir, "browser_native_page_last.json")
    browser_logs: list[Any] = []
    performance_tail: list[Any] = []
    try:
        browser_logs = driver.get_log("browser")[-80:]
    except Exception:
        browser_logs = []
    try:
        perf_entries = driver.get_log("performance")
        normalized_tail: list[Any] = []
        for raw_entry in perf_entries[-120:]:
            try:
                outer = json.loads(str(raw_entry.get("message") or ""))
                message = outer.get("message") or {}
                method = str(message.get("method") or "")
                params = message.get("params") or {}
                if method in {
                    "Network.loadingFailed",
                    "Runtime.exceptionThrown",
                    "Log.entryAdded",
                    "Runtime.consoleAPICalled",
                }:
                    normalized_tail.append({
                        "method": method,
                        "params": params,
                    })
            except Exception:
                continue
        performance_tail = normalized_tail[-60:]
    except Exception:
        performance_tail = []
    try:
        payload = driver.execute_script(
            """
            const readStorageEntries = (storage) => {
              const items = [];
              try {
                for (let i = 0; i < storage.length && i < 80; i += 1) {
                  const key = String(storage.key(i) || '');
                  let value = '';
                  try {
                    value = String(storage.getItem(key) || '').slice(0, 400);
                  } catch (_err) {}
                  items.push([key, value]);
                }
              } catch (_err) {}
              return items;
            };
            const bootstrapScript = document.getElementById('bootstrap-inert-script');
            return {
              href: location.href,
              title: document.title,
              readyState: document.readyState,
              bodyText: String((document.body && document.body.innerText) || '').slice(0, 4000),
              outerHTML: String((document.documentElement && document.documentElement.outerHTML) || '').slice(0, 200000),
              buttons: Array.from(document.querySelectorAll('button')).map((el) => ({
                text: String((el.innerText || el.textContent || '')).trim(),
                disabled: !!el.disabled,
                type: String(el.getAttribute('type') || ''),
              })).slice(0, 30),
              inputs: Array.from(document.querySelectorAll('input')).map((el) => ({
                type: String(el.getAttribute('type') || ''),
                name: String(el.getAttribute('name') || ''),
                autocomplete: String(el.getAttribute('autocomplete') || ''),
                valueLen: String((el.value || '')).length,
              })).slice(0, 30),
              iframes: Array.from(document.querySelectorAll('iframe')).map((el) => ({
                src: String(el.getAttribute('src') || ''),
                title: String(el.getAttribute('title') || ''),
                name: String(el.getAttribute('name') || ''),
              })).slice(0, 30),
              scripts: Array.from(document.scripts || []).map((el) => String(el.src || '')).slice(0, 50),
              cookieNames: document.cookie.split(';').map((entry) => String(entry || '').split('=')[0].trim()).filter(Boolean).slice(0, 100),
              localStorageEntries: readStorageEntries(window.localStorage),
              sessionStorageEntries: readStorageEntries(window.sessionStorage),
              capturedFetches: Array.isArray(window.__capturedFetches) ? window.__capturedFetches.slice(-20) : [],
              capturedXhrs: Array.isArray(window.__capturedXhrs) ? window.__capturedXhrs.slice(-20) : [],
              capturedFormSubmits: Array.isArray(window.__capturedFormSubmits) ? window.__capturedFormSubmits.slice(-20) : [],
              consoleErrors: Array.isArray(window.__codexConsoleErrors) ? window.__codexConsoleErrors.slice(-30) : [],
              consoleWarnings: Array.isArray(window.__codexConsoleWarnings) ? window.__codexConsoleWarnings.slice(-30) : [],
              windowErrors: Array.isArray(window.__codexWindowErrors) ? window.__codexWindowErrors.slice(-30) : [],
              unhandledRejections: Array.isArray(window.__codexUnhandledRejections) ? window.__codexUnhandledRejections.slice(-30) : [],
              resourceErrors: Array.isArray(window.__codexResourceErrors) ? window.__codexResourceErrors.slice(-30) : [],
              lifecycle: Array.isArray(window.__codexLifecycle) ? window.__codexLifecycle.slice(-40) : [],
              reactRouterContextPresent: !!window.__reactRouterContext,
              reactRouterModulesPresent: !!window.__reactRouterRouteModules,
              bootstrapScriptText: String((bootstrapScript && bootstrapScript.textContent) || '').slice(0, 8000),
              performanceNow: (window.performance && typeof window.performance.now === 'function') ? Number(window.performance.now()) : 0,
              timeOrigin: (window.performance && Number(window.performance.timeOrigin || 0)) || 0,
              resourceEntries: (window.performance && typeof window.performance.getEntriesByType === 'function')
                ? window.performance.getEntriesByType('resource').slice(-30).map((entry) => ({
                    name: String(entry.name || ''),
                    initiatorType: String(entry.initiatorType || ''),
                    duration: Number(entry.duration || 0),
                    transferSize: Number(entry.transferSize || 0),
                  }))
                : [],
            };
            """
        ) or {}
    except Exception as exc:
        payload = {
            "href": "",
            "title": "",
            "readyState": "",
            "bodyText": "",
            "outerHTML": "",
            "buttons": [],
            "inputs": [],
            "iframes": [],
            "scripts": [],
            "cookieNames": [],
            "captureError": str(exc),
        }
    payload["capturedAt"] = datetime.now(timezone.utc).isoformat()
    payload["note"] = str(note or "")
    payload["browserLogs"] = browser_logs
    payload["performanceLogTail"] = performance_tail
    with open(snapshot_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return snapshot_path


def _browser_try_click_retry(driver: Any) -> bool:
    try:
        from selenium.webdriver.common.by import By
    except Exception:
        return False
    try:
        for candidate in driver.find_elements(By.TAG_NAME, "button"):
            text = str(getattr(candidate, "text", "") or "").strip().lower()
            if text in {"try again", "retry", "continue"}:
                try:
                    candidate.click()
                    return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


def _browser_nudge_challenge_page(driver: Any) -> None:
    try:
        from selenium.webdriver import ActionChains
        from selenium.webdriver.common.by import By
    except Exception:
        return
    try:
        driver.execute_script(
            """
            try {
              window.scrollTo(0, Math.max(0, Math.floor((document.body && document.body.scrollHeight) || 0) / 3));
            } catch (_err) {}
            """
        )
    except Exception:
        pass
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        ActionChains(driver).move_to_element_with_offset(body, 40, 40).pause(0.2).move_by_offset(120, 60).pause(0.2).perform()
    except Exception:
        pass


def _browser_try_email_continue(driver: Any, *, email: str) -> bool:
    try:
        native_email_helper = _load_protocol_browser_try_native_auth_fill_email()
    except Exception:
        native_email_helper = None
    if callable(native_email_helper):
        try:
            native_result = native_email_helper(driver, str(email or ""), submit=True)
        except Exception:
            native_result = None
        if isinstance(native_result, dict) and native_result.get("ok"):
            print(
                "[python-protocol-service] browser native email primitive "
                f"result={native_result}"
            )
            return True
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
    except Exception:
        return False
    try:
        page_email_input = driver.find_element(
            By.CSS_SELECTOR,
            'input[type="email"], input[name="email"], input[autocomplete="username"]',
        )
    except Exception:
        return False
    page_continue_button = None
    try:
        for candidate in driver.find_elements(By.TAG_NAME, "button"):
            text = str(getattr(candidate, "text", "") or "").strip()
            if text == "Continue":
                page_continue_button = candidate
                break
    except Exception:
        page_continue_button = None
    if page_continue_button is None:
        return False
    try:
        page_email_input.click()
    except Exception:
        pass
    try:
        page_email_input.send_keys(Keys.CONTROL, "a")
    except Exception:
        pass
    page_email_input.send_keys(str(email or ""))
    time.sleep(0.6)
    page_continue_button.click()
    return True


def _browser_try_submit_password_with_selenium(driver: Any, *, password: str) -> bool:
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
    except Exception:
        return False
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    try:
        password_input = driver.find_element(
            By.CSS_SELECTOR,
            'input[type="password"], input[name="new-password"], input[name*="password"], input[id*="password"]',
        )
    except Exception:
        return False
    continue_button = None
    try:
        for candidate in driver.find_elements(By.TAG_NAME, "button"):
            text = str(getattr(candidate, "text", "") or "").strip()
            if text == "Continue":
                continue_button = candidate
                break
    except Exception:
        continue_button = None
    if continue_button is None:
        return False
    try:
        password_input.click()
    except Exception:
        pass
    try:
        password_input.send_keys(Keys.CONTROL, "a")
    except Exception:
        pass
    password_input.send_keys(str(password or ""))
    time.sleep(0.6)
    try:
        continue_button.click()
        return True
    except Exception:
        return False


def _browser_recover_to_password_surface(
    driver: Any,
    *,
    email: str,
    browser_start_url: str,
    timeout_seconds: float = 35.0,
    try_solve_challenge_fn: Any | None = None,
) -> dict[str, Any]:
    deadline = time.time() + max(float(timeout_seconds), 1.0)
    last_state: dict[str, Any] = {}
    last_email_submit_marker = ""
    last_email_submit_at = 0.0
    last_password_shell_at = 0.0
    last_email_shell_at = 0.0
    while time.time() < deadline:
        last_state = _browser_collect_page_state(driver)
        if _browser_page_is_password_surface(last_state):
            return last_state
        href = str(last_state.get("href") or "")
        if _browser_page_is_password_shell(last_state):
            now = time.time()
            if (now - last_password_shell_at) >= 2.5:
                last_password_shell_at = now
                try:
                    driver.refresh()
                except Exception:
                    try:
                        driver.get(browser_start_url)
                    except Exception:
                        pass
                time.sleep(1.2)
                continue
            time.sleep(0.8)
            continue
        if _browser_page_is_email_shell(last_state):
            now = time.time()
            if (now - last_email_shell_at) >= 2.5:
                last_email_shell_at = now
                try:
                    driver.get(PLATFORM_OPENAI_LOGIN_URL)
                except Exception:
                    try:
                        driver.get(LOGIN_OR_CREATE_ACCOUNT_REFERER)
                    except Exception:
                        pass
                time.sleep(1.5)
                continue
            time.sleep(0.8)
            continue
        if last_state.get("hasEmail"):
            marker = "|".join(
                [
                    href,
                    str(last_state.get("title") or ""),
                    ",".join(str(item or "") for item in list(last_state.get("buttonTexts") or [])),
                ]
            )
            if marker != last_email_submit_marker or (time.time() - last_email_submit_at) >= 6.0:
                if _browser_try_email_continue(driver, email=email):
                    last_email_submit_marker = marker
                    last_email_submit_at = time.time()
                    time.sleep(2.5)
                    continue
            time.sleep(0.8)
            continue
        if _browser_page_requires_recovery(last_state):
            if _browser_page_is_cloudflare_wait(last_state):
                challenge_deadline = min(deadline, time.time() + 18.0)
                solved_once = False
                while time.time() < challenge_deadline:
                    solved = False
                    if callable(try_solve_challenge_fn):
                        try:
                            solved = bool(try_solve_challenge_fn("browser-password-surface"))
                        except Exception:
                            solved = False
                    solved_once = solved_once or solved
                    if not solved:
                        _browser_nudge_challenge_page(driver)
                    time.sleep(2.0 if solved or solved_once else 3.0)
                    last_state = _browser_collect_page_state(driver)
                    if not _browser_page_is_cloudflare_wait(last_state):
                        break
                continue
            if _browser_try_click_retry(driver):
                time.sleep(1.2)
                continue
            if browser_start_url and str(browser_start_url).strip():
                try:
                    driver.get(str(browser_start_url).strip())
                except Exception:
                    pass
            time.sleep(1.0)
            continue
        if "https://sentinel.openai.com/" in href.lower():
            settle_deadline = min(deadline, time.time() + 8.0)
            while time.time() < settle_deadline:
                time.sleep(1.0)
                last_state = _browser_collect_page_state(driver)
                settled_href = str(last_state.get("href") or "")
                if "https://sentinel.openai.com/" not in settled_href.lower():
                    break
            if _browser_page_is_password_surface(last_state):
                return last_state
            if _browser_page_is_password_shell(last_state):
                try:
                    driver.refresh()
                except Exception:
                    try:
                        driver.get(browser_start_url)
                    except Exception:
                        pass
                time.sleep(1.2)
                continue
            try:
                driver.get(browser_start_url)
            except Exception:
                pass
            time.sleep(1.0)
            continue
        time.sleep(0.5)
    return last_state


def _browser_focus_password_surface_window(driver: Any) -> dict[str, Any]:
    try:
        handles = list(driver.window_handles or [])
    except Exception:
        handles = []
    best_state: dict[str, Any] = {}
    if not handles:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        return _browser_collect_page_state(driver)
    for handle in handles:
        try:
            driver.switch_to.window(handle)
        except Exception:
            continue
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        state = _browser_collect_page_state(driver)
        href = str(state.get("href") or "")
        if _browser_page_is_password_surface(state) or CREATE_ACCOUNT_PASSWORD_REFERER in href:
            return state
        if not best_state:
            best_state = state
    try:
        driver.switch_to.window(handles[0])
    except Exception:
        pass
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    return best_state or _browser_collect_page_state(driver)


def _submit_browser_native_signup_user_register(
    *,
    session: requests.Session,
    explicit_proxy: str | None,
    email: str,
    password: str,
    sentinel_token: str,
    passkey_capabilities_header: str | None = None,
    sentinel_context: ProtocolSentinelContext | None = None,
) -> _StdlibResponse | None:
    if not env_flag("PROTOCOL_ENABLE_BROWSER_NATIVE_USER_REGISTER", True):
        return None
    if not str(sentinel_token or "").strip():
        return None

    exported_cookies = _export_session_cookies_for_browser_sentinel(session)
    import_browser_cookies = env_flag("PROTOCOL_BROWSER_NATIVE_IMPORT_COOKIES", True)
    if import_browser_cookies and not exported_cookies:
        return None

    requested_browser_start_url = str(
        getattr(session, "_new_protocol_signup_oauth_auth_url", "") or ""
    ).strip() or CREATE_ACCOUNT_PASSWORD_REFERER
    normalized_browser_device_id = str(
        (sentinel_context.device_id if sentinel_context is not None else "")
        or _get_session_cookie(
            session,
            "oai-did",
            preferred_domains=("auth.openai.com", ".openai.com", ".chatgpt.com", "chatgpt.com"),
        )
        or ""
    ).strip()
    if import_browser_cookies:
        browser_start_url = _normalize_auth_url_device_id(
            requested_browser_start_url,
            device_id=normalized_browser_device_id,
        )
    else:
        browser_start_url = PLATFORM_OPENAI_LOGIN_URL

    new_driver = _load_protocol_browser_new_driver()
    browser_backend = _protocol_browser_native_backend()
    captcha_provider = _protocol_browser_native_captcha_provider(browser_backend)
    browser_native_succeeded = False
    preserve_failure_note = "browser_native_user_register_failed"
    maybe_solve_turnstile_challenge = None
    if captcha_provider:
        try:
            maybe_solve_turnstile_challenge = _load_protocol_browser_maybe_solve_turnstile_challenge()
        except Exception:
            maybe_solve_turnstile_challenge = None
    try_solve_challenge_fn = None
    if callable(maybe_solve_turnstile_challenge) and captcha_provider:
        try_solve_challenge_fn = lambda reason: maybe_solve_turnstile_challenge(  # type: ignore[misc]
            driver,
            provider_kind=captcha_provider,
            browser_backend=browser_backend,
            proxy=explicit_proxy,
            dbg_fn=_protocol_browser_native_captcha_dbg,
        )
    driver = None
    proxy_dir = None
    browser_user_data_dir = ""
    cleanup_browser_user_data_dir = False
    browser_env_backup: dict[str, str | None] = {}
    try:
        if _protocol_browser_native_use_ephemeral_profile():
            browser_user_data_dir = tempfile.mkdtemp(prefix="protocol-browser-native-")
            cleanup_browser_user_data_dir = True
        else:
            browser_user_data_dir = _protocol_browser_native_profile_dir()
            os.makedirs(browser_user_data_dir, exist_ok=True)
        browser_env_overrides = _protocol_browser_native_env_overrides(browser_user_data_dir)
        for key, value in browser_env_overrides.items():
            browser_env_backup[key] = os.environ.get(key)
            os.environ[key] = value
        driver, proxy_dir = new_driver(
            proxy=explicit_proxy,
            browser_backend=browser_backend,
        )
        try:
            driver.set_page_load_timeout(45)
        except Exception:
            pass
        try:
            driver.set_script_timeout(45)
        except Exception:
            pass
        driver.get(PLATFORM_OPENAI_LOGIN_URL)
        try:
            driver.delete_all_cookies()
            print(
                "[python-protocol-service] browser native cookie reset "
                "scope=fresh-flow"
            )
        except Exception as exc:
            print(
                "[python-protocol-service] browser native cookie reset failed "
                f"err={exc}"
            )
        if import_browser_cookies:
            imported_browser_cookie_count = _import_exported_cookies_into_browser_driver(
                driver,
                exported_cookies=exported_cookies,
            )
            print(
                "[python-protocol-service] browser native cookie transplant "
                f"imported={imported_browser_cookie_count} "
                f"session_cookie_names={_session_cookie_name_summary(session, limit=50)}"
            )
        driver.get(browser_start_url)
        print(
            "[python-protocol-service] browser native bootstrap "
            f"import_cookies={import_browser_cookies} "
            f"browser_start_url={_format_logged_url(browser_start_url)}"
        )
        page_state = _browser_recover_to_password_surface(
            driver,
            email=str(email or "").strip(),
            browser_start_url=browser_start_url,
            timeout_seconds=45.0,
            try_solve_challenge_fn=try_solve_challenge_fn,
        )
        if (
            not _browser_page_is_password_surface(page_state)
            and _browser_page_requires_recovery(page_state)
            and sentinel_context is not None
            and _protocol_browser_native_allow_recovery_driver()
        ):
            print(
                "[python-protocol-service] browser native auth recovery "
                f"href={page_state.get('href')} title={page_state.get('title')}"
            )
            _saved_browser_user_data_dir = os.environ.pop("BROWSER_USER_DATA_DIR", None)
            _saved_browser_remove_args = os.environ.pop("BROWSER_REMOVE_ARGS_EXTRA", None)
            browser_bootstrap_result = None
            updated_context = sentinel_context
            recovery_driver = None
            recovery_proxy_dir = None
            try:
                browser_bootstrap_result, recovery_driver, recovery_proxy_dir = _prime_protocol_auth_session_with_browser_driver(
                    session,
                    explicit_proxy=explicit_proxy,
                    device_id=sentinel_context.device_id,
                )
                updated_context = _clone_protocol_sentinel_context(
                    sentinel_context,
                    user_agent=str(browser_bootstrap_result.user_agent or sentinel_context.user_agent).strip() or sentinel_context.user_agent,
                    device_id=str(browser_bootstrap_result.did or sentinel_context.device_id).strip() or sentinel_context.device_id,
                )
                session.headers.update({"user-agent": updated_context.user_agent})
                print(
                    "[python-protocol-service] browser bootstrap fallback succeeded "
                    f"reason=browser_native_user_register url={browser_bootstrap_result.current_url or '<none>'} "
                    f"imported_cookies={browser_bootstrap_result.imported_cookie_count} "
                    f"cookie_names_after={_session_cookie_name_summary(session, limit=50)} "
                    f"did_len={len(updated_context.device_id)}"
                )
            except Exception as exc:
                print(
                    "[python-protocol-service] browser bootstrap fallback failed "
                    f"reason=browser_native_user_register err={exc}"
                )
                browser_bootstrap_result = None
                recovery_driver = None
                recovery_proxy_dir = None
            finally:
                if _saved_browser_user_data_dir is not None:
                    os.environ["BROWSER_USER_DATA_DIR"] = _saved_browser_user_data_dir
                if _saved_browser_remove_args is not None:
                    os.environ["BROWSER_REMOVE_ARGS_EXTRA"] = _saved_browser_remove_args
            if recovery_driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
                if proxy_dir:
                    try:
                        shutil.rmtree(str(proxy_dir), ignore_errors=True)
                    except Exception:
                        pass
                driver = recovery_driver
                proxy_dir = recovery_proxy_dir
                browser_start_url = str(
                    browser_bootstrap_result.auth_url or browser_start_url
                ).strip() or browser_start_url
                normalized_browser_device_id = str(
                    browser_bootstrap_result.did
                    or updated_context.device_id
                    or normalized_browser_device_id
                ).strip() or normalized_browser_device_id
                browser_start_url = _normalize_auth_url_device_id(
                    browser_start_url,
                    device_id=normalized_browser_device_id,
                )
                try:
                    driver.get(LOGIN_OR_CREATE_ACCOUNT_REFERER)
                except Exception:
                    pass
                page_state = _browser_recover_to_password_surface(
                    driver,
                    email=str(email or "").strip(),
                    browser_start_url=LOGIN_OR_CREATE_ACCOUNT_REFERER,
                    timeout_seconds=90.0,
                    try_solve_challenge_fn=try_solve_challenge_fn,
                )
                sentinel_context = updated_context
        browser_native_href = str(page_state.get("href") or "")
        browser_native_title = str(page_state.get("title") or "")
        print(
            "[python-protocol-service] browser native page context "
            f"href={browser_native_href} title={browser_native_title} "
            f"ready_state={page_state.get('readyState')} has_password={page_state.get('hasPassword')} "
            f"buttons={page_state.get('buttonTexts')}",
        )
        imported_before_submit = _import_browser_driver_cookies_into_session(session, driver=driver)
        if imported_before_submit:
            print(
                "[python-protocol-service] browser native user_register materialized cookies "
                f"imported={imported_before_submit} summary={_protocol_auth_cookie_summary(session)}"
            )
        _install_browser_native_request_capture_hooks(driver)
        cdp_capture_enabled = _browser_native_enable_cdp_network_capture(driver)
        if passkey_capabilities_header:
            print(
                "[python-protocol-service] browser native passkey header skipped "
                "scope=page-assets note=avoids auth-cdn module CORS breakage"
            )
        cdp_records: dict[str, dict[str, Any]] = {}
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        native_password_helper = None
        if browser_backend == "camoufox" and _protocol_browser_native_use_native_password_helper():
            try:
                native_password_helper = _load_protocol_browser_try_native_auth_fill_password()
            except Exception:
                native_password_helper = None

        if not _browser_page_is_password_surface(page_state):
            page_state = _browser_recover_to_password_surface(
                driver,
                email=str(email or "").strip(),
                browser_start_url=browser_start_url,
                timeout_seconds=45.0,
                try_solve_challenge_fn=try_solve_challenge_fn,
            )
        if (
            not _browser_page_is_password_surface(page_state)
            and _browser_page_requires_recovery(page_state)
            and sentinel_context is not None
            and _protocol_browser_native_allow_recovery_driver()
        ):
            print(
                "[python-protocol-service] browser native control auth recovery "
                f"href={page_state.get('href')} title={page_state.get('title')}"
            )
            _saved_browser_user_data_dir = os.environ.pop("BROWSER_USER_DATA_DIR", None)
            _saved_browser_remove_args = os.environ.pop("BROWSER_REMOVE_ARGS_EXTRA", None)
            browser_bootstrap_result = None
            updated_context = sentinel_context
            recovery_driver = None
            recovery_proxy_dir = None
            try:
                browser_bootstrap_result, recovery_driver, recovery_proxy_dir = _prime_protocol_auth_session_with_browser_driver(
                    session,
                    explicit_proxy=explicit_proxy,
                    device_id=sentinel_context.device_id,
                )
                updated_context = _clone_protocol_sentinel_context(
                    sentinel_context,
                    user_agent=str(browser_bootstrap_result.user_agent or sentinel_context.user_agent).strip() or sentinel_context.user_agent,
                    device_id=str(browser_bootstrap_result.did or sentinel_context.device_id).strip() or sentinel_context.device_id,
                )
                session.headers.update({"user-agent": updated_context.user_agent})
                print(
                    "[python-protocol-service] browser bootstrap fallback succeeded "
                    f"reason=browser_native_user_register_controls url={browser_bootstrap_result.current_url or '<none>'} "
                    f"imported_cookies={browser_bootstrap_result.imported_cookie_count} "
                    f"cookie_names_after={_session_cookie_name_summary(session, limit=50)} "
                    f"did_len={len(updated_context.device_id)}"
                )
            except Exception as exc:
                print(
                    "[python-protocol-service] browser bootstrap fallback failed "
                    f"reason=browser_native_user_register_controls err={exc}"
                )
                browser_bootstrap_result = None
                recovery_driver = None
                recovery_proxy_dir = None
            finally:
                if _saved_browser_user_data_dir is not None:
                    os.environ["BROWSER_USER_DATA_DIR"] = _saved_browser_user_data_dir
                if _saved_browser_remove_args is not None:
                    os.environ["BROWSER_REMOVE_ARGS_EXTRA"] = _saved_browser_remove_args
            if recovery_driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
                if proxy_dir:
                    try:
                        shutil.rmtree(str(proxy_dir), ignore_errors=True)
                    except Exception:
                        pass
                driver = recovery_driver
                proxy_dir = recovery_proxy_dir
                browser_start_url = str(
                    browser_bootstrap_result.auth_url or browser_start_url
                ).strip() or browser_start_url
                normalized_browser_device_id = str(
                    browser_bootstrap_result.did
                    or updated_context.device_id
                    or normalized_browser_device_id
                ).strip() or normalized_browser_device_id
                browser_start_url = _normalize_auth_url_device_id(
                    browser_start_url,
                    device_id=normalized_browser_device_id,
                )
                try:
                    driver.get(CREATE_ACCOUNT_PASSWORD_REFERER)
                except Exception:
                    pass
                page_state = _browser_recover_to_password_surface(
                    driver,
                    email=str(email or "").strip(),
                    browser_start_url=CREATE_ACCOUNT_PASSWORD_REFERER,
                    timeout_seconds=90.0,
                    try_solve_challenge_fn=try_solve_challenge_fn,
                )
                sentinel_context = updated_context
        print(
            "[python-protocol-service] browser native control recovery "
            f"href={page_state.get('href')} title={page_state.get('title')} "
            f"ready_state={page_state.get('readyState')} has_email={page_state.get('hasEmail')} "
            f"has_password={page_state.get('hasPassword')} buttons={page_state.get('buttonTexts')}",
        )
        native_password_submit_result = None
        if _browser_page_is_password_surface(page_state):
            direct_submit_ok = _browser_try_submit_password_with_selenium(
                driver,
                password=str(password or ""),
            )
            if direct_submit_ok:
                native_password_submit_result = {
                    "ok": True,
                    "stage": "password",
                    "action": "selenium_direct",
                }
                print(
                    "[python-protocol-service] browser native password direct "
                    f"result={native_password_submit_result}"
                )
        if (
            not (isinstance(native_password_submit_result, dict) and native_password_submit_result.get("ok"))
            and callable(native_password_helper)
            and _browser_page_is_password_surface(page_state)
        ):
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            try:
                native_password_submit_result = native_password_helper(
                    driver,
                    str(password or ""),
                    submit=True,
                )
            except Exception:
                native_password_submit_result = None
            if isinstance(native_password_submit_result, dict):
                print(
                    "[python-protocol-service] browser native password primitive "
                    f"result={native_password_submit_result}"
                )
                native_stage = str(native_password_submit_result.get("stage") or "")
                native_url = str(native_password_submit_result.get("url") or "")
                if (
                    not native_password_submit_result.get("ok")
                    and native_stage == "missing-password"
                    and "https://sentinel.openai.com/" in native_url.lower()
                ):
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
                    try:
                        native_password_submit_result = native_password_helper(
                            driver,
                            str(password or ""),
                            submit=True,
                        )
                    except Exception:
                        pass
                    if isinstance(native_password_submit_result, dict):
                        print(
                            "[python-protocol-service] browser native password primitive retry "
                            f"result={native_password_submit_result}"
                        )
        password_input = None
        continue_available = False
        last_page_snapshot: dict[str, Any] = {}
        control_deadline = time.time() + 35.0
        while time.time() < control_deadline and not (isinstance(native_password_submit_result, dict) and native_password_submit_result.get("ok")):
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            try:
                last_page_snapshot = driver.execute_script(
                    """
                    return {
                      href: location.href,
                      title: document.title,
                      readyState: document.readyState,
                      body: String((document.body && document.body.innerText) || "").slice(0, 300),
                      buttons: Array.from(document.querySelectorAll('button')).map((el) => String((el.innerText || el.textContent || '')).trim()).filter(Boolean).slice(0, 8),
                    };
                    """
                ) or {}
            except Exception:
                last_page_snapshot = {}
            href = str(last_page_snapshot.get("href") or "")
            title = str(last_page_snapshot.get("title") or "")
            body = str(last_page_snapshot.get("body") or "")
            combined = "\n".join([href.lower(), title.lower(), body.lower()])
            if (
                "https://sentinel.openai.com/backend-api/sentinel/frame.html" in href.lower()
                or _browser_page_is_cloudflare_wait({
                    "href": href,
                    "title": title,
                    "bodyText": body,
                })
                or _browser_page_requires_recovery({
                    "href": href,
                    "title": title,
                    "bodyText": body,
                })
            ):
                print(
                    "[python-protocol-service] browser native post-password challenge "
                    f"href={href} title={title}"
                )
                challenge_snapshot_path = _browser_native_dump_page_snapshot(
                    driver,
                    note="browser_native_post_password_challenge",
                )
                if challenge_snapshot_path:
                    print(
                        "[python-protocol-service] browser native challenge snapshot "
                        f"path={challenge_snapshot_path}"
                    )
                try:
                    _maybe_prime_browser_driver_with_cloudflare_clearance(
                        driver,
                        session=session,
                        website_url=href or CHATGPT_LOGIN_URL,
                        explicit_proxy=explicit_proxy,
                        user_agent=str(session.headers.get("user-agent") or DEFAULT_PROTOCOL_USER_AGENT),
                        reason="browser_native_post_password_submit",
                    )
                except Exception:
                    pass
                if callable(try_solve_challenge_fn):
                    try:
                        try_solve_challenge_fn("browser_native_post_password_submit")
                    except Exception as exc:
                        print(
                            "[python-protocol-service] browser native post-password challenge solve failed "
                            f"err={exc}"
                        )
                try:
                    recovered_state = _browser_recover_to_password_surface(
                        driver,
                        email=str(email or "").strip(),
                        browser_start_url=LOGIN_OR_CREATE_ACCOUNT_REFERER,
                        timeout_seconds=20.0,
                        try_solve_challenge_fn=try_solve_challenge_fn,
                    )
                except Exception:
                    recovered_state = {}
                if isinstance(recovered_state, dict) and recovered_state:
                    last_page_snapshot = {
                        "href": str(recovered_state.get("href") or href),
                        "title": str(recovered_state.get("title") or title),
                        "body": str(recovered_state.get("bodyText") or body),
                        "readyState": recovered_state.get("readyState"),
                        "buttons": recovered_state.get("buttonTexts"),
                    }
                    if _browser_page_is_password_surface(recovered_state):
                        password_input = None
                        continue_available = False
                        time.sleep(0.5)
                        continue
            if "https://auth.openai.com/log-in-or-create-account" in href:
                try:
                    page_email_input = driver.find_element(By.CSS_SELECTOR, 'input[type="email"], input[name="email"], input[autocomplete="username"]')
                    page_continue_button = None
                    for candidate in driver.find_elements(By.TAG_NAME, "button"):
                        text = str(getattr(candidate, "text", "") or "").strip()
                        if text == "Continue":
                            page_continue_button = candidate
                            break
                    if page_continue_button is not None:
                        try:
                            page_email_input.click()
                        except Exception:
                            pass
                        try:
                            page_email_input.send_keys(Keys.CONTROL, "a")
                        except Exception:
                            pass
                        page_email_input.send_keys(str(email or ""))
                        time.sleep(0.6)
                        page_continue_button.click()
                        time.sleep(1.2)
                        continue
                except Exception:
                    pass
            try:
                password_input = driver.find_element(By.CSS_SELECTOR, 'input[type="password"], input[name="new-password"]')
            except Exception:
                password_input = None
            continue_available = False
            if password_input is not None:
                try:
                    for candidate in driver.find_elements(By.TAG_NAME, "button"):
                        text = str(getattr(candidate, "text", "") or "").strip()
                        if text == "Continue":
                            continue_available = True
                            break
                except Exception:
                    continue_available = False
            if password_input is not None and continue_available:
                break
            time.sleep(0.5)
        if not (isinstance(native_password_submit_result, dict) and native_password_submit_result.get("ok")) and (password_input is None or not continue_available):
            dumped_path = _browser_native_dump_page_snapshot(
                driver,
                note="browser_native_user_register_missing_controls",
            )
            if dumped_path:
                print(
                    "[python-protocol-service] browser native page snapshot "
                    f"path={dumped_path}"
                )
            raise RuntimeError(
                "browser_native_user_register_missing_controls "
                f"href={last_page_snapshot.get('href')} title={last_page_snapshot.get('title')} "
                f"body={last_page_snapshot.get('body')} buttons={last_page_snapshot.get('buttons')}"
            )
        if not (isinstance(native_password_submit_result, dict) and native_password_submit_result.get("ok")):
            try:
                password_input.click()
            except Exception:
                pass
            try:
                password_input.send_keys(Keys.CONTROL, "a")
            except Exception:
                pass
            password_input.send_keys(str(password or ""))
            time.sleep(1.0)
            clicked_continue = False
            for _attempt in range(3):
                try:
                    fresh_button = None
                    for candidate in driver.find_elements(By.TAG_NAME, "button"):
                        text = str(getattr(candidate, "text", "") or "").strip()
                        if text == "Continue":
                            fresh_button = candidate
                            break
                    if fresh_button is None:
                        time.sleep(0.5)
                        continue
                    fresh_button.click()
                    clicked_continue = True
                    break
                except Exception:
                    time.sleep(0.5)
            if not clicked_continue:
                raise RuntimeError("browser_native_user_register_continue_click_failed")
        dom_deadline = time.time() + 12.0
        raw = {}
        latest_cdp_target: dict[str, Any] | None = None
        while time.time() < dom_deadline:
            if cdp_capture_enabled:
                latest_cdp_target = _browser_native_drain_cdp_user_register(driver, records=cdp_records)
                if isinstance(latest_cdp_target, dict) and (
                    latest_cdp_target.get("loadingFinished")
                    or latest_cdp_target.get("loadingFailed")
                    or latest_cdp_target.get("status") is not None
                ):
                    if latest_cdp_target.get("responseBodyFetched") or latest_cdp_target.get("loadingFailed"):
                        break
            try:
                raw = driver.execute_script(
                    """
                    return {
                      href: location.href,
                      title: document.title,
                      body: String((document.body && document.body.innerText) || "").slice(0, 800),
                      cookieNames: document.cookie.split(';').map((item) => String(item || '').trim().split('=')[0]).filter(Boolean),
                      capturedFetches: Array.isArray(window.__capturedFetches) ? window.__capturedFetches : [],
                      capturedXhrs: Array.isArray(window.__capturedXhrs) ? window.__capturedXhrs : [],
                      capturedFormSubmits: Array.isArray(window.__capturedFormSubmits) ? window.__capturedFormSubmits : [],
                    };
                    """
                ) or {}
            except Exception:
                raw = {}
            captured = raw.get("capturedFetches")
            if isinstance(captured, list):
                target = next(
                    (
                        item
                        for item in reversed(captured)
                        if isinstance(item, dict)
                        and "api/accounts/user/register" in str(item.get("url") or "")
                    ),
                    None,
                )
                if target is not None and (
                    target.get("status") is not None
                    or str(target.get("error") or "").strip()
                ):
                    break
            body_text = str(raw.get("body") or "")
            if "Failed to create account. Please try again" in body_text:
                break
            if str(raw.get("href") or "").strip() != CREATE_ACCOUNT_PASSWORD_REFERER:
                break
            time.sleep(0.5)
        if not isinstance(raw, dict):
            raise RuntimeError("browser_native_user_register_invalid_result")
        captured_fetches = raw.get("capturedFetches")
        target_fetch = None
        if isinstance(captured_fetches, list):
            target_fetch = next(
                (
                    item
                    for item in reversed(captured_fetches)
                    if isinstance(item, dict)
                    and "api/accounts/user/register" in str(item.get("url") or "")
                ),
                None,
            )
        captured_xhrs = raw.get("capturedXhrs")
        target_xhr = None
        if isinstance(captured_xhrs, list):
            target_xhr = next(
                (
                    item
                    for item in reversed(captured_xhrs)
                    if isinstance(item, dict)
                    and "api/accounts/user/register" in str(item.get("url") or item.get("responseUrl") or "")
                ),
                None,
            )
        captured_form_submits = raw.get("capturedFormSubmits")
        target_form_submit = None
        if isinstance(captured_form_submits, list):
            target_form_submit = next(
                (
                    item
                    for item in reversed(captured_form_submits)
                    if isinstance(item, dict)
                    and "api/accounts/user/register" in str(item.get("action") or "")
                ),
                None,
            )
        target_cdp = None
        if isinstance(latest_cdp_target, dict):
            target_cdp = latest_cdp_target
        elif cdp_records:
            target_cdp = next(
                (
                    item
                    for item in reversed(list(cdp_records.values()))
                    if isinstance(item, dict)
                    and "api/accounts/user/register" in str(item.get("url") or item.get("responseUrl") or "")
                ),
                None,
            )
        if isinstance(target_fetch, dict):
            request_headers = target_fetch.get("headers")
            if not isinstance(request_headers, dict):
                request_headers = {}
            response_headers = target_fetch.get("responseHeaders")
            if not isinstance(response_headers, dict):
                response_headers = {}
            request_header_map = {str(key): str(value) for key, value in request_headers.items()}
            fetch_snapshot_path = _browser_native_dump_user_register_snapshot(
                request_headers=request_header_map,
                response_headers={str(key): str(value) for key, value in response_headers.items()},
                target=target_fetch,
                raw=raw if isinstance(raw, dict) else {},
            )
            body_preview = str(target_fetch.get("responseBody") or raw.get("body") or "")
            body_preview = body_preview.replace("\r", " ").replace("\n", " ")[:260]
            print(
                "[python-protocol-service] browser native user_register captured "
                f"status={target_fetch.get('status')} error={target_fetch.get('error')} "
                f"href={raw.get('href')} title={raw.get('title')} "
                f"sentinel={_sentinel_header_debug_summary(request_header_map)} "
                f"snapshot={fetch_snapshot_path} body_preview={body_preview}"
            )
            imported_after_submit = _import_browser_driver_cookies_into_session(session, driver=driver)
            if imported_after_submit:
                print(
                    "[python-protocol-service] browser native user_register post-submit cookies "
                    f"imported={imported_after_submit} summary={_protocol_auth_cookie_summary(session)}"
                )
            browser_native_succeeded = int(target_fetch.get("status") or 0) < 400
            if not browser_native_succeeded:
                preserve_failure_note = (
                    f"browser_native_user_register_status_{int(target_fetch.get('status') or 0)}"
                )
            return _StdlibResponse(
                status_code=int(target_fetch.get("status") or 0),
                headers={str(key): str(value) for key, value in response_headers.items()},
                url=str(target_fetch.get("responseUrl") or raw.get("href") or CREATE_ACCOUNT_PASSWORD_REFERER),
                body=str(target_fetch.get("responseBody") or raw.get("body") or "").encode("utf-8", errors="replace"),
            )
        if isinstance(target_xhr, dict):
            xhr_snapshot_path = _browser_native_dump_user_register_snapshot(
                request_headers={str(key): str(value) for key, value in dict(target_xhr.get("headers") or {}).items()},
                response_headers={},
                target=target_xhr,
                raw=raw if isinstance(raw, dict) else {},
            )
            xhr_body_preview = str(target_xhr.get("responseBody") or raw.get("body") or "")
            xhr_body_preview = xhr_body_preview.replace("\r", " ").replace("\n", " ")[:260]
            print(
                "[python-protocol-service] browser native user_register captured "
                f"transport=xhr status={target_xhr.get('status')} error={target_xhr.get('error')} "
                f"href={raw.get('href')} title={raw.get('title')} "
                f"sentinel={_sentinel_header_debug_summary({str(key): str(value) for key, value in dict(target_xhr.get('headers') or {}).items()})} "
                f"snapshot={xhr_snapshot_path} body_preview={xhr_body_preview}"
            )
            imported_after_submit = _import_browser_driver_cookies_into_session(session, driver=driver)
            if imported_after_submit:
                print(
                    "[python-protocol-service] browser native user_register post-submit cookies "
                    f"imported={imported_after_submit} summary={_protocol_auth_cookie_summary(session)}"
                )
            response_headers = {}
            raw_headers = str(target_xhr.get("responseHeadersRaw") or "")
            for line in raw_headers.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                response_headers[str(key).strip()] = str(value).strip()
            browser_native_succeeded = int(target_xhr.get("status") or 0) < 400
            if not browser_native_succeeded:
                preserve_failure_note = (
                    f"browser_native_user_register_status_{int(target_xhr.get('status') or 0)}"
                )
            return _StdlibResponse(
                status_code=int(target_xhr.get("status") or 0),
                headers=response_headers,
                url=str(target_xhr.get("responseUrl") or target_xhr.get("url") or raw.get("href") or CREATE_ACCOUNT_PASSWORD_REFERER),
                body=str(target_xhr.get("responseBody") or raw.get("body") or "").encode("utf-8", errors="replace"),
            )
        if isinstance(target_cdp, dict):
            merged_request_headers = {}
            for header_map in (target_cdp.get("requestHeaders"), target_cdp.get("extraRequestHeaders")):
                if isinstance(header_map, dict):
                    for key, value in header_map.items():
                        merged_request_headers[str(key)] = str(value)
            merged_response_headers = {}
            for header_map in (target_cdp.get("responseHeaders"), target_cdp.get("extraResponseHeaders")):
                if isinstance(header_map, dict):
                    for key, value in header_map.items():
                        merged_response_headers[str(key)] = str(value)
            snapshot_path = _browser_native_dump_user_register_snapshot(
                request_headers=merged_request_headers,
                response_headers=merged_response_headers,
                target=target_cdp,
                raw=raw if isinstance(raw, dict) else {},
            )
            print(
                "[python-protocol-service] browser native user_register captured "
                f"transport=cdp status={target_cdp.get('status')} error={target_cdp.get('errorText')} "
                f"href={raw.get('href')} title={raw.get('title')} "
                f"sentinel={_sentinel_header_debug_summary(merged_request_headers)} "
                f"post_len={len(str(target_cdp.get('postData') or ''))} "
                f"cookie_len={len(str(merged_request_headers.get('Cookie') or merged_request_headers.get('cookie') or ''))} "
                f"accept_language={merged_request_headers.get('accept-language') or merged_request_headers.get('Accept-Language') or ''} "
                f"snapshot={snapshot_path}"
            )
            imported_after_submit = _import_browser_driver_cookies_into_session(session, driver=driver)
            if imported_after_submit:
                print(
                    "[python-protocol-service] browser native user_register post-submit cookies "
                    f"imported={imported_after_submit} summary={_protocol_auth_cookie_summary(session)}"
                )
            body_text = str(target_cdp.get("responseBody") or raw.get("body") or "")
            browser_native_succeeded = int(target_cdp.get("status") or 0) < 400
            if not browser_native_succeeded:
                preserve_failure_note = (
                    f"browser_native_user_register_status_{int(target_cdp.get('status') or 0)}"
                )
            return _StdlibResponse(
                status_code=int(target_cdp.get("status") or 0),
                headers=merged_response_headers,
                url=str(target_cdp.get("responseUrl") or target_cdp.get("url") or raw.get("href") or CREATE_ACCOUNT_PASSWORD_REFERER),
                body=body_text.encode("utf-8", errors="replace"),
            )
        if isinstance(target_form_submit, dict):
            print(
                "[python-protocol-service] browser native user_register captured "
                f"transport=form mode={target_form_submit.get('mode')} action={target_form_submit.get('action')} "
                f"method={target_form_submit.get('method')} submitter={target_form_submit.get('submitterText')}"
            )
        error = str(raw.get("error") or "").strip()
        if error:
            cookie_names = raw.get("cookieNames")
            if isinstance(cookie_names, list):
                cookie_names_text = ",".join(str(item or "").strip() for item in cookie_names if str(item or "").strip())
            else:
                cookie_names_text = ""
            extra = []
            if cookie_names_text:
                extra.append(f"cookies={cookie_names_text}")
            if raw.get("href"):
                extra.append(f"href={raw.get('href')}")
            if raw.get("title"):
                extra.append(f"title={raw.get('title')}")
            raise RuntimeError(f"{error} {' '.join(extra)}".strip())
        body_text = str(raw.get("body") or "")
        raise RuntimeError(
            "browser_native_user_register_no_captured_request "
            f"href={raw.get('href')} title={raw.get('title')} body={body_text}"
        )
    except Exception as exc:
        preserve_failure_note = str(exc)
        print(
            "[python-protocol-service] browser native user_register failed "
            f"err={exc}"
        )
        return None
    finally:
        driver_cleanup_user_data_dir = ""
        if driver is not None:
            try:
                driver_cleanup_user_data_dir = str(getattr(driver, "_protocol_cleanup_user_data_dir", "") or "").strip()
            except Exception:
                driver_cleanup_user_data_dir = ""
        should_preserve_failure = (
            driver is not None
            and not browser_native_succeeded
            and _protocol_browser_native_keep_failed_browser()
        )
        if should_preserve_failure:
            _preserve_protocol_browser_native_failure(
                driver=driver,
                proxy_dir=str(proxy_dir or "").strip() or None,
                browser_user_data_dir=browser_user_data_dir if cleanup_browser_user_data_dir else "",
                driver_cleanup_user_data_dir=driver_cleanup_user_data_dir,
                note=preserve_failure_note,
            )
        else:
            success_retained = bool(driver is not None and browser_native_succeeded)
            if success_retained:
                _stash_protocol_browser_native_success(
                    session,
                    driver=driver,
                    proxy_dir=proxy_dir,
                    browser_user_data_dir=browser_user_data_dir if cleanup_browser_user_data_dir else "",
                    driver_cleanup_user_data_dir=driver_cleanup_user_data_dir,
                )
            else:
                if driver is not None and _protocol_browser_native_close_success_browser():
                    try:
                        driver.quit()
                    except Exception:
                        pass
                if proxy_dir:
                    try:
                        shutil.rmtree(str(proxy_dir), ignore_errors=True)
                    except Exception:
                        pass
                if browser_user_data_dir and cleanup_browser_user_data_dir:
                    try:
                        shutil.rmtree(str(browser_user_data_dir), ignore_errors=True)
                    except Exception:
                        pass
                if driver_cleanup_user_data_dir:
                    try:
                        shutil.rmtree(str(driver_cleanup_user_data_dir), ignore_errors=True)
                    except Exception:
                        pass
        for key, previous in browser_env_backup.items():
            try:
                if previous is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous
            except Exception:
                pass


def _prime_protocol_auth_session_with_browser_driver(
    session: requests.Session,
    *,
    explicit_proxy: str | None,
    device_id: str,
) -> tuple[ProtocolBrowserBootstrapResult, Any, Any]:
    new_driver = _load_protocol_browser_new_driver()
    driver = None
    proxy_dir = None
    bootstrap_succeeded = False
    browser_env_backup: dict[str, str | None] = {}
    browser_user_data_dir = tempfile.mkdtemp(prefix="protocol-browser-bootstrap-")
    for key, value in _protocol_browser_native_env_overrides(browser_user_data_dir).items():
        browser_env_backup[key] = os.environ.get(key)
        os.environ[key] = value
    try:
        driver, proxy_dir = new_driver(
            explicit_proxy,
            browser_backend=_protocol_browser_native_backend(),
        )
    finally:
        for key, previous in browser_env_backup.items():
            try:
                if previous is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous
            except Exception:
                pass
    try:
        setattr(driver, "_protocol_cleanup_user_data_dir", browser_user_data_dir)
    except Exception:
        pass
    oauth = _browser_bootstrap_chatgpt_web_oauth_session_on_driver(
        driver,
        device_id=device_id,
    )
    if oauth is None:
        raise RuntimeError("browser_native_chatgpt_oauth_bootstrap_failed")
    imported_cookie_count = _import_browser_driver_cookies_into_session(session, driver=driver)
    user_agent = ""
    try:
        user_agent = str(driver.execute_script("return navigator.userAgent || '';") or "").strip()
    except Exception:
        user_agent = ""
    did = _get_session_cookie(
        session,
        "oai-did",
        preferred_domains=(".openai.com", "auth.openai.com", ".chatgpt.com", "chatgpt.com"),
    )
    result = ProtocolBrowserBootstrapResult(
        current_url=str(getattr(driver, "current_url", "") or "").strip(),
        did=did or device_id,
        user_agent=user_agent.replace("HeadlessChrome/", "Chrome/"),
        imported_cookie_count=imported_cookie_count,
        auth_url=str(getattr(oauth, "auth_url", "") or "").strip(),
        auth_state=str(getattr(oauth, "state", "") or "").strip(),
    )
    return result, driver, proxy_dir


def _bootstrap_chatgpt_web_oauth_session_with_browser_fallback(
    session: requests.Session,
    *,
    sentinel_context: ProtocolSentinelContext,
    explicit_proxy: str | None,
) -> tuple[OAuthStart, ProtocolSentinelContext, ProtocolBrowserBootstrapResult | None]:
    try:
        oauth = _bootstrap_chatgpt_web_oauth_session(
            session,
            device_id=sentinel_context.device_id,
            explicit_proxy=explicit_proxy,
        )
        return oauth, sentinel_context, None
    except Exception as direct_exc:
        updated_context, clearance_ok = _maybe_prime_protocol_auth_session_with_clearance(
            session,
            sentinel_context=sentinel_context,
            explicit_proxy=explicit_proxy,
            reason="chatgpt_oauth_bootstrap",
            website_url=CHATGPT_LOGIN_URL,
        )
        if clearance_ok:
            try:
                oauth = _bootstrap_chatgpt_web_oauth_session(
                    session,
                    device_id=updated_context.device_id,
                    explicit_proxy=explicit_proxy,
                )
                return oauth, updated_context, None
            except Exception:
                sentinel_context = updated_context
        updated_context, captcha_browser_result = _maybe_prime_protocol_auth_session_with_easycaptcha_browser_bootstrap(
            session,
            sentinel_context=sentinel_context,
            explicit_proxy=explicit_proxy,
            reason="chatgpt_oauth_bootstrap",
        )
        if captcha_browser_result is not None:
            auth_url = str(captcha_browser_result.auth_url or "").strip()
            auth_state = str(captcha_browser_result.auth_state or "").strip()
            if auth_url and auth_state:
                print(
                    "[python-protocol-service] nextauth bootstrap source=easycaptcha-browser "
                    f"auth_url={_format_logged_url(auth_url)} state_len={len(auth_state)} "
                    f"did_len={len(captcha_browser_result.did)}"
                )
                return (
                    OAuthStart(
                        auth_url=auth_url,
                        state=auth_state,
                        code_verifier="",
                        redirect_uri="https://chatgpt.com/api/auth/callback/openai",
                    ),
                    updated_context,
                    captcha_browser_result,
                )
            sentinel_context = updated_context
        updated_context, browser_result = _maybe_prime_protocol_auth_session_with_browser(
            session,
            sentinel_context=sentinel_context,
            explicit_proxy=explicit_proxy,
            reason="chatgpt_oauth_bootstrap",
        )
        if browser_result is None:
            raise RuntimeError(
                f"chatgpt_oauth_bootstrap_failed direct_err={direct_exc}"
            ) from direct_exc

        auth_url = str(browser_result.auth_url or "").strip()
        auth_state = str(browser_result.auth_state or "").strip()
        if not auth_url or not auth_state:
            raise RuntimeError(
                "chatgpt_oauth_bootstrap_browser_missing_auth "
                f"direct_err={direct_exc} auth_url={_format_logged_url(auth_url)} "
                f"auth_state_len={len(auth_state)}"
            ) from direct_exc

        print(
            "[python-protocol-service] nextauth bootstrap source=browser-assisted "
            f"auth_url={_format_logged_url(auth_url)} state_len={len(auth_state)} "
            f"did_len={len(browser_result.did)}"
        )
        return (
            OAuthStart(
                auth_url=auth_url,
                state=auth_state,
                code_verifier="",
                redirect_uri="https://chatgpt.com/api/auth/callback/openai",
            ),
            updated_context,
            browser_result,
        )


def _request_email_otp_resend_on_verification_page(
    session: requests.Session,
    *,
    page_type: str | None,
    explicit_proxy: str | None,
    header_builder: ProtocolSentinelContext | None,
    context_label: str,
) -> bool:
    if (page_type or "").strip() != "email_otp_verification":
        return False
    try:
        _send_email_otp(
            session,
            explicit_proxy=explicit_proxy,
            header_builder=header_builder,
        )
        print(
            "[python-protocol-service] proactive email OTP resend requested "
            f"context={context_label} page_type={page_type}"
        )
        return True
    except Exception as exc:
        print(
            "[python-protocol-service] proactive email OTP resend skipped "
            f"context={context_label} page_type={page_type} err={exc}"
        )
        return False


def _iter_session_cookie_objects(session: requests.Session) -> list[Any]:
    jar = getattr(session, "cookies", None)
    if jar is None:
        return []
    iterables: list[Any] = []
    nested_jar = getattr(jar, "jar", None)
    if nested_jar is not None:
        iterables.append(nested_jar)
    iterables.append(jar)
    items: list[Any] = []
    seen: set[tuple[str, str, str]] = set()
    for iterable in iterables:
        try:
            for cookie in iterable:
                name = str(getattr(cookie, "name", "") or "").strip()
                if not name:
                    continue
                domain = str(getattr(cookie, "domain", "") or "").strip()
                path = str(getattr(cookie, "path", "") or "/").strip() or "/"
                dedupe = (name, domain, path)
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                items.append(cookie)
        except Exception:
            continue
    return items


def _cookie_matches_request_url(cookie: Any, request_url: str) -> bool:
    normalized_url = str(request_url or "").strip()
    if not normalized_url:
        return False
    try:
        parsed = urllib.parse.urlparse(normalized_url)
    except Exception:
        return False
    host = str(parsed.hostname or "").strip().lower()
    path = str(parsed.path or "/").strip() or "/"
    scheme = str(parsed.scheme or "").strip().lower()
    domain = str(getattr(cookie, "domain", "") or "").strip().lstrip(".").lower()
    cookie_path = str(getattr(cookie, "path", "") or "/").strip() or "/"
    secure = bool(getattr(cookie, "secure", False))
    if not host or not domain:
        return False
    if host != domain and not host.endswith(f".{domain}"):
        return False
    if secure and scheme != "https":
        return False
    if not path.startswith(cookie_path.rstrip("/") or "/"):
        return False
    return True


def _deduped_cookie_header_for_request(
    session: requests.Session,
    request_url: str,
) -> str:
    normalized_url = str(request_url or "").strip()
    if not normalized_url:
        return ""
    try:
        parsed = urllib.parse.urlparse(normalized_url)
    except Exception:
        return ""
    host = str(parsed.hostname or "").strip().lower()
    path = str(parsed.path or "/").strip() or "/"
    selected: dict[str, tuple[tuple[int, int, int], str]] = {}
    for cookie in _iter_session_cookie_objects(session):
        if not _cookie_matches_request_url(cookie, normalized_url):
            continue
        name = str(getattr(cookie, "name", "") or "").strip()
        value = str(getattr(cookie, "value", "") or "")
        domain = str(getattr(cookie, "domain", "") or "").strip().lstrip(".").lower()
        cookie_path = str(getattr(cookie, "path", "") or "/").strip() or "/"
        if not name:
            continue
        score = (
            1 if domain == host else 0,
            len(domain),
            len(cookie_path) if path.startswith(cookie_path.rstrip("/") or "/") else 0,
        )
        previous = selected.get(name)
        if previous is None or score > previous[0]:
            selected[name] = (score, value)
    if not selected:
        return ""
    return "; ".join(f"{name}={value}" for name, (_score, value) in selected.items())


def _browser_cookie_matches_request_url(cookie: dict[str, Any], request_url: str) -> bool:
    normalized_url = str(request_url or "").strip()
    if not normalized_url:
        return False
    try:
        parsed = urllib.parse.urlparse(normalized_url)
    except Exception:
        return False
    host = str(parsed.hostname or "").strip().lower()
    path = str(parsed.path or "/").strip() or "/"
    scheme = str(parsed.scheme or "").strip().lower()
    domain = str(cookie.get("domain") or "").strip().lstrip(".").lower()
    cookie_path = str(cookie.get("path") or "/").strip() or "/"
    secure = bool(cookie.get("secure", False))
    if not host or not domain:
        return False
    if host != domain and not host.endswith(f".{domain}"):
        return False
    if secure and scheme != "https":
        return False
    normalized_cookie_path = cookie_path.rstrip("/") or "/"
    return path.startswith(normalized_cookie_path)


def _browser_cookie_header_for_request(browser_cookies: list[dict[str, Any]], request_url: str) -> str:
    normalized_url = str(request_url or "").strip()
    if not normalized_url:
        return ""
    try:
        parsed = urllib.parse.urlparse(normalized_url)
    except Exception:
        return ""
    host = str(parsed.hostname or "").strip().lower()
    path = str(parsed.path or "/").strip() or "/"
    selected: dict[str, tuple[tuple[int, int, int], str]] = {}
    for cookie in list(browser_cookies or []):
        if not isinstance(cookie, dict) or not _browser_cookie_matches_request_url(cookie, normalized_url):
            continue
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "")
        domain = str(cookie.get("domain") or "").strip().lstrip(".").lower()
        cookie_path = str(cookie.get("path") or "/").strip() or "/"
        if not name:
            continue
        score = (
            1 if domain == host else 0,
            len(domain),
            len(cookie_path) if path.startswith(cookie_path.rstrip("/") or "/") else 0,
        )
        previous = selected.get(name)
        if previous is None or score > previous[0]:
            selected[name] = (score, value)
    if not selected:
        return ""
    return "; ".join(f"{name}={value}" for name, (_score, value) in selected.items())


def _browser_signup_request_url_for_request_kind(request_kind: str) -> str | None:
    if request_kind == "signup-authorize-continue":
        return AUTHORIZE_CONTINUE_URL
    if request_kind == "signup-user-register":
        return USER_REGISTER_URL
    if request_kind == "signup-create-account":
        return CREATE_ACCOUNT_URL
    return None


def _cookie_debug_snapshot(session: requests.Session, *, limit: int = 12) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    try:
        for cookie in _iter_session_cookie_objects(session):
            name = str(getattr(cookie, "name", "") or "").strip()
            if not name:
                continue
            items.append(
                {
                    "name": name,
                    "domain": str(getattr(cookie, "domain", "") or "").strip(),
                    "len": str(len(str(getattr(cookie, "value", "") or ""))),
                }
            )
            if len(items) >= limit:
                break
    except Exception:
        pass
    return items


def _session_cookie_name_summary(session: requests.Session, *, limit: int = 20) -> str:
    names: list[str] = []
    try:
        for cookie in _iter_session_cookie_objects(session):
            name = str(getattr(cookie, "name", "") or "").strip()
            if not name:
                continue
            names.append(name)
            if len(names) >= limit:
                break
    except Exception:
        return "<unknown>"
    return ",".join(names) if names else "<none>"


def _format_logged_url(url: str, *, max_query_keys: int = 6) -> str:
    normalized = str(url or "").strip()
    if not normalized:
        return "<none>"

    try:
        parsed = urllib.parse.urlsplit(normalized)
    except Exception:
        return normalized[:160] + ("..." if len(normalized) > 160 else "")

    base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    if not parsed.query:
        return base or normalized

    try:
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    except Exception:
        return f"{base}?query=<unparsed>"

    keys: list[str] = []
    seen: set[str] = set()
    for key, _value in pairs:
        key_text = str(key or "").strip()
        if not key_text or key_text in seen:
            continue
        seen.add(key_text)
        keys.append(key_text)

    if not keys:
        return f"{base}?query=present"

    shown_keys = keys[:max_query_keys]
    extra_count = max(0, len(keys) - len(shown_keys))
    suffix = ",".join(shown_keys)
    if extra_count > 0:
        suffix = f"{suffix},+{extra_count}"
    return f"{base}?keys={suffix}"


def _is_callback_url(url: str) -> bool:
    normalized = str(url or "").strip()
    return bool(normalized) and "code=" in normalized and "state=" in normalized


def _html_attrs(raw_attrs: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in _ATTR_RE.finditer(str(raw_attrs or "")):
        key = str(match.group(1) or "").strip().lower()
        value = match.group(3) if match.group(2) else str(match.group(4) or "")
        attrs[key] = str(value or "")
    return attrs


def _strip_html_tags(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""), flags=re.DOTALL)
    return re.sub(r"\s+", " ", text).strip()


def _is_codex_consent_html(*, url: str, html: str) -> bool:
    url_lower = str(url or "").strip().lower()
    html_lower = str(html or "").strip().lower()
    if "sign-in-with-chatgpt/codex/consent" in url_lower:
        return True
    return (
        "sign in to codex with chatgpt" in html_lower
        and "continue" in html_lower
    )


def _is_consent_action_text(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return False
    return any(
        token in normalized
        for token in ("continue", "agree", "accept", "allow", "authorize")
    )


def _submit_codex_consent_form(
    session: requests.Session,
    *,
    consent_url: str,
    response: Any,
    explicit_proxy: str | None,
) -> str:
    html = str(getattr(response, "text", "") or "")
    if not _is_codex_consent_html(url=consent_url, html=html):
        raise RuntimeError("not_codex_consent_page")

    for match in _FORM_RE.finditer(html):
        form_attrs = _html_attrs(match.group("attrs"))
        form_body = str(match.group("body") or "")
        form_text = _strip_html_tags(form_body).lower()

        fields: list[tuple[str, str]] = []
        clicked_submit: tuple[str, str] | None = None
        for input_match in _INPUT_RE.finditer(form_body):
            input_attrs = _html_attrs(input_match.group("attrs"))
            input_name = str(input_attrs.get("name") or "").strip()
            input_type = str(input_attrs.get("type") or "text").strip().lower()
            if input_type in {"submit", "button", "image"}:
                if clicked_submit is None and input_name and _is_consent_action_text(
                    " ".join((
                        str(input_attrs.get("value") or ""),
                        str(input_attrs.get("aria-label") or ""),
                        input_name,
                    ))
                ):
                    clicked_submit = (input_name, str(input_attrs.get("value") or ""))
                continue
            if not input_name:
                continue
            fields.append((input_name, str(input_attrs.get("value") or "")))

        for button_match in _BUTTON_RE.finditer(form_body):
            button_attrs = _html_attrs(button_match.group("attrs"))
            button_text = _strip_html_tags(button_match.group("body") or "").lower()
            if not _is_consent_action_text(button_text):
                continue
            button_name = str(button_attrs.get("name") or "").strip()
            if button_name:
                clicked_submit = (button_name, str(button_attrs.get("value") or ""))
            break
        if clicked_submit is not None:
            fields.append(clicked_submit)

        action = str(form_attrs.get("action") or "").strip()
        method = str(form_attrs.get("method") or "post").strip().upper()
        target_url = urllib.parse.urljoin(consent_url, action or consent_url)
        is_likely_consent_form = (
            _is_consent_action_text(form_text)
            or _is_consent_action_text(str(form_attrs.get("aria-label") or ""))
            or "sign-in-with-chatgpt/codex/consent" in target_url.lower()
        )
        if not is_likely_consent_form and clicked_submit is None:
            continue
        encoded_fields = urllib.parse.urlencode(fields)
        consent_response = _session_request(
            session,
            method or "POST",
            target_url,
            explicit_proxy=explicit_proxy,
            request_label="codex-consent-submit",
            allow_redirects=False,
            timeout=20,
            headers={
                "Referer": consent_url,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": AUTH_BASE,
            },
            data=encoded_fields,
        )
        location = str(
            consent_response.headers.get("Location")
            or consent_response.headers.get("location")
            or ""
        ).strip()
        next_url = urllib.parse.urljoin(target_url, location) if location else str(getattr(consent_response, "url", "") or target_url)
        if "code=" in next_url and "state=" in next_url:
            return next_url
        if location:
            return _follow_redirect_chain_for_callback(
                session,
                next_url,
                explicit_proxy=explicit_proxy,
                referer=consent_url,
            )
        raise RuntimeError(
            "codex_consent_submit_no_redirect "
            f"status={consent_response.status_code} body={_response_preview(consent_response, 200)}"
        )

    for anchor_match in _ANCHOR_RE.finditer(html):
        anchor_attrs = _html_attrs(anchor_match.group("attrs"))
        anchor_text = _strip_html_tags(anchor_match.group("body") or "").lower()
        if not _is_consent_action_text(anchor_text):
            continue
        href = str(anchor_attrs.get("href") or "").strip()
        if not href:
            continue
        next_url = urllib.parse.urljoin(consent_url, href)
        return _follow_redirect_chain_for_callback(
            session,
            next_url,
            explicit_proxy=explicit_proxy,
            referer=consent_url,
        )

    workspace_ids: list[str] = []
    for match in _WORKSPACES_RE.finditer(html):
        workspaces_blob = str(match.group(1) or "")
        for workspace_match in _WORKSPACE_ID_RE.finditer(workspaces_blob):
            workspace_id = str(workspace_match.group(1) or "").strip()
            if workspace_id and workspace_id not in workspace_ids:
                workspace_ids.append(workspace_id)
    if workspace_ids:
        selected_workspace_id = _select_workspace_id_from_id_list(workspace_ids)
        print(
            "[python-protocol-service] consent page using workspace-select fallback "
            f"workspace_id={selected_workspace_id} workspace_count={len(workspace_ids)}"
        )
        return _submit_workspace_selection_for_callback(
            session=session,
            workspace_id=selected_workspace_id,
            explicit_proxy=explicit_proxy,
            referer=consent_url,
            workspace_request_label="workspace-select-codex-consent",
            header_builder=None,
        )

    try:
        dumped_workspace_id = _extract_workspace_id_from_session(
            session,
            explicit_proxy=explicit_proxy,
        )
        print(
            "[python-protocol-service] consent page using client-auth-session dump fallback "
            f"workspace_id={dumped_workspace_id}"
        )
        return _submit_workspace_selection_for_callback(
            session=session,
            workspace_id=dumped_workspace_id,
            explicit_proxy=explicit_proxy,
            referer=consent_url,
            workspace_request_label="workspace-select-codex-consent-dump",
            header_builder=None,
        )
    except RuntimeError as exc:
        dump_workspace_error = str(exc)
    else:
        dump_workspace_error = ""

    form_count = len(list(_FORM_RE.finditer(html)))
    button_count = len(list(_BUTTON_RE.finditer(html)))
    anchor_count = len(list(_ANCHOR_RE.finditer(html)))
    raise RuntimeError(
        "codex_consent_continue_not_found "
        f"forms={form_count} buttons={button_count} anchors={anchor_count} "
        f"dump_workspace_err={dump_workspace_error} html={html[:1200]}"
    )


def _select_stage2_strategy(
    *,
    password: str,
    is_existing_account: bool,
    prefer_authenticated_session: bool = False,
) -> str:
    normalized_password = str(password or "").strip()
    if normalized_password and not prefer_authenticated_session:
        return "repair_with_password"
    if not is_existing_account:
        if prefer_authenticated_session and env_flag(PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV, True):
            return "session_handoff_without_password"
        raise RuntimeError("missing_password_for_new_account_stage2")
    if normalized_password and not env_flag(PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV, True):
        return "repair_with_password"
    return "session_handoff_without_password"


def _get_session_cookie(
    session: requests.Session,
    name: str,
    *,
    preferred_domains: tuple[str, ...] = (),
) -> str:
    def _normalize_domain(value: str) -> str:
        return str(value or "").strip().lower().lstrip(".")

    def _iter_domain_candidates(domain: str) -> list[str]:
        raw = str(domain or "").strip()
        normalized = _normalize_domain(raw)
        candidates: list[str] = []
        for candidate in (raw, normalized, f".{normalized}" if normalized else ""):
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        return candidates

    for domain in preferred_domains:
        for candidate_domain in _iter_domain_candidates(domain):
            try:
                value = session.cookies.get(name, domain=candidate_domain)
            except Exception:
                value = None
            if value:
                return str(value).strip()

    jar = getattr(session, "cookies", None)
    if jar is None:
        return ""

    matches: list[tuple[str, str]] = []
    iterables = [jar]
    nested_jar = getattr(jar, "jar", None)
    if nested_jar is not None and nested_jar is not jar:
        iterables.append(nested_jar)
    for iterable in iterables:
        try:
            for cookie in iterable:
                cookie_name = getattr(cookie, "name", "")
                cookie_value = getattr(cookie, "value", "")
                if cookie_name != name or not cookie_value:
                    continue
                matches.append((
                    str(getattr(cookie, "domain", "") or ""),
                    str(cookie_value or ""),
                ))
        except Exception:
            continue

    if not matches:
        return ""

    normalized_preferred_domains = [
        normalized
        for normalized in (_normalize_domain(domain) for domain in preferred_domains)
        if normalized
    ]
    best_match: str | None = None
    best_score = -1
    for cookie_domain, cookie_value in matches:
        cookie_domain_normalized = _normalize_domain(cookie_domain)
        score = 0
        for preferred_domain in normalized_preferred_domains:
            if cookie_domain_normalized == preferred_domain:
                score = max(score, 300 + len(preferred_domain))
            elif cookie_domain_normalized.endswith(f".{preferred_domain}"):
                score = max(score, 200 + len(preferred_domain))
            elif preferred_domain.endswith(f".{cookie_domain_normalized}"):
                score = max(score, 100 + len(cookie_domain_normalized))
        if score > best_score:
            best_score = score
            best_match = cookie_value.strip()
    if best_match:
        return best_match

    return matches[0][1].strip()


def _get_cloudflare_clearance_cookie(session: requests.Session) -> str:
    return _get_session_cookie(
        session,
        "cf_clearance",
        preferred_domains=(
            "auth.openai.com",
            ".openai.com",
            "chatgpt.com",
            ".chatgpt.com",
        ),
    )


def _apply_captcha_cookies_to_session(
    session: requests.Session,
    cookies: Any,
) -> int:
    if not isinstance(cookies, list):
        return 0
    imported = 0
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "").strip()
        if not name:
            continue
        try:
            session.cookies.set(
                name,
                str(cookie.get("value") or ""),
                domain=str(cookie.get("domain") or "").strip() or None,
                path=str(cookie.get("path") or "/").strip() or "/",
                secure=bool(cookie.get("secure", False)),
            )
            imported += 1
        except Exception:
            continue
    return imported


def _captcha_cookie_name_summary(cookies: Any, *, limit: int = 12) -> str:
    if not isinstance(cookies, list):
        return "<none>"
    names: list[str] = []
    seen: set[str] = set()
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= limit:
            break
    return ",".join(names) if names else "<none>"


def _is_phone_wall_page_type(page_type: str) -> bool:
    normalized = str(page_type or "").strip().lower().replace("-", "_")
    if not normalized:
        return False
    return normalized in {
        "add_phone",
        "add_phone_number",
        "phone",
        "phone_number",
        "phone_verification",
        "phone_verification_required",
        "sms_verification",
    } or "phone" in normalized


def _is_phone_wall_text(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return False
    return (
        "phone_wall" in normalized
        or "/add-phone" in normalized
        or "add-phone" in normalized
        or "phone number required" in normalized
        or "requires a phone number" in normalized
        or "phone verification" in normalized
        or "sms verification" in normalized
        or "需要手机号" in normalized
        or "需要手机号码" in normalized
        or ("手机" in normalized and "号码" in normalized and "需要" in normalized)
    )


def _response_has_phone_wall(response: Any) -> bool:
    page_type = _extract_page_type(response)
    if _is_phone_wall_page_type(page_type):
        return True

    try:
        response_url = str(getattr(response, "url", "") or "")
    except Exception:
        response_url = ""

    return _is_phone_wall_text(
        f"{response_url}\n{page_type}\n{_response_preview(response, 500)}"
    )


def _raise_if_phone_wall_response(response: Any, *, context: str) -> None:
    if not _response_has_phone_wall(response):
        return
    page_type = _extract_page_type(response) or "unknown"
    raise RuntimeError(
        f"phone_wall context={context} page_type={page_type} body={_response_preview(response, 300)}"
    )


def _protocol_request_error_is_retryable(exc: BaseException) -> bool:
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
        "timed out",
    )
    return any(marker in text for marker in retry_markers)


def _protocol_request_with_transient_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    explicit_proxy: str | None,
    request_label: str,
    max_attempts: int = 3,
    retry_sleep_seconds: float = 0.6,
    **kwargs: Any,
) -> Any:
    attempts = max(1, int(max_attempts or 1))
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
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
            if attempt >= attempts or not _protocol_request_error_is_retryable(exc):
                raise
            print(
                "[python-protocol-service] retrying transient request "
                f"label={request_label} attempt={attempt} err={exc}"
            )
            time.sleep(max(0.1, float(retry_sleep_seconds)) * attempt)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"protocol_request_failed label={request_label}")


def _send_email_otp(
    session: requests.Session,
    *,
    explicit_proxy: str | None,
    header_builder: ProtocolSentinelContext | None = None,
    referer: str = EMAIL_VERIFICATION_REFERER,
) -> Any:
    if not env_flag(PROTOCOL_ENABLE_EMAIL_OTP_SEND_ENV, True):
        print(
            "[python-protocol-service] email OTP request skipped "
            f"env={PROTOCOL_ENABLE_EMAIL_OTP_SEND_ENV} server_auto_send=true"
        )
        return

    print("[python-protocol-service] requesting email OTP")
    req_headers = _build_protocol_headers(
        request_kind="email-otp-send",
        referer=referer,
        content_type=None,
        sentinel_context=header_builder,
    )

    response = _protocol_request_with_transient_retry(
        session,
        "GET",
        EMAIL_OTP_SEND_URL,
        explicit_proxy=explicit_proxy,
        request_label="email-otp-send",
        headers=req_headers,
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"send_otp status={response.status_code} body={_response_preview(response)}"
        )
    print(
        "[python-protocol-service] email OTP requested "
        f"status={response.status_code} page_type={_extract_page_type(response) or 'unknown'}"
    )
    return response


def _send_passwordless_login_otp(
    session: requests.Session,
    *,
    explicit_proxy: str | None,
    header_builder: ProtocolSentinelContext | None = None,
) -> Any:
    print("[python-protocol-service] requesting passwordless login OTP from login page")
    req_headers = _build_protocol_headers(
        request_kind="",
        referer=LOGIN_PASSWORD_REFERER,
        sentinel_context=header_builder,
    )

    response = _protocol_request_with_transient_retry(
        session,
        "POST",
        PASSWORDLESS_SEND_OTP_URL,
        explicit_proxy=explicit_proxy,
        request_label="passwordless-login-send-otp",
        headers=req_headers,
        timeout=45,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"passwordless_send_otp status={response.status_code} body={_response_preview(response)}"
        )
    print(
        "[python-protocol-service] passwordless login OTP requested "
        f"status={response.status_code} page_type={_extract_page_type(response) or 'unknown'}"
    )
    return response


def _verify_login_password(
    session: requests.Session,
    *,
    password: str,
    explicit_proxy: str | None,
    header_builder: ProtocolSentinelContext | None = None,
) -> Any:
    req_headers = _build_protocol_headers(
        request_kind="repair-password-verify",
        referer=LOGIN_PASSWORD_REFERER,
        sentinel_context=header_builder,
    )

    response = _session_request(
        session,
        "POST",
        PASSWORD_VERIFY_URL,
        explicit_proxy=explicit_proxy,
        request_label="password-verify",
        headers=req_headers,
        data=json.dumps({
            "password": password,
        }),
    )
    if response.status_code == 401:
        try:
            payload = response.json() or {}
        except Exception:
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else None
        error_code = (
            str(error.get("code") or "").strip()
            if isinstance(error, dict)
            else ""
        )
        if error_code == "invalid_username_or_password":
            raise RuntimeError("invalid_username_or_password")
        raise RuntimeError(
            f"password_verify status={response.status_code} body={_response_preview(response)}"
        )

    if response.status_code >= 400:
        raise RuntimeError(
            f"password_verify status={response.status_code} body={_response_preview(response)}"
        )

    return response


def _resolve_repair_oauth_entry(
    session: requests.Session,
    *,
    signup_response: Any,
    password: str,
    mailbox_ref: str,
    explicit_proxy: str | None,
    header_builder: ProtocolSentinelContext | None = None,
) -> tuple[Any, str, str]:
    page_type = _extract_page_type(signup_response)
    oauth_entry_response: Any | None = signup_response
    oauth_entry_referer = LOGIN_OR_CREATE_ACCOUNT_REFERER
    if page_type == "login_password":
        if password:
            pwd_resp = _verify_login_password(
                session,
                password=password,
                explicit_proxy=explicit_proxy,
                header_builder=header_builder,
            )
            page_type = _extract_page_type(pwd_resp)
            print(
                "[python-protocol-service] password accepted "
                f"next_page_type={page_type or 'unknown'}"
            )
            oauth_entry_response = pwd_resp
            oauth_entry_referer = LOGIN_PASSWORD_REFERER
        elif mailbox_ref:
            print(
                "[python-protocol-service] password missing, attempting "
                "passwordless email OTP fallback from login page"
            )
            otp_resp = _send_passwordless_login_otp(
                session,
                explicit_proxy=explicit_proxy,
                header_builder=header_builder,
            )
            page_type = _extract_page_type(otp_resp) or "email_otp_verification"
            oauth_entry_response = otp_resp
            oauth_entry_referer = EMAIL_VERIFICATION_REFERER
        else:
            _raise_protocol_error(
                "missing_password",
                stage="stage_create_account",
                detail="missing_password",
                category="flow_error",
            )
    elif page_type in {"email_otp_send", "email_otp_verification"}:
        print(
            "[python-protocol-service] passwordless login flow detected "
            f"next_page_type={page_type or 'unknown'}"
        )
    else:
        _raise_protocol_error(
            f"unsupported_protocol_repair_page_type page_type={page_type or 'unknown'}",
            stage="stage_create_account",
            detail="authorize_continue_repair",
            category="flow_error",
        )
    return oauth_entry_response, page_type, oauth_entry_referer


def _fetch_chatgpt_home_html(
    session: requests.Session,
    *,
    explicit_proxy: str | None,
) -> str:
    response = _session_request(
        session,
        "GET",
        CHATGPT_HOME_URL,
        explicit_proxy=explicit_proxy,
        request_label="chatgpt-home",
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout=15,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"chatgpt_home status={response.status_code} body={_response_preview(response, 200)}"
        )
    return str(getattr(response, "text", "") or "")


def _bootstrap_chatgpt_web_oauth_session(
    session: requests.Session,
    *,
    device_id: str,
    explicit_proxy: str | None,
    prompt: str = "login",
    screen_hint: str = "login_or_signup",
) -> OAuthStart:
    login_response = _session_request(
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
    if login_response.status_code >= 400:
        raise RuntimeError(
            f"chatgpt_login status={login_response.status_code} body={_response_preview(login_response, 200)}"
        )

    csrf_response = _session_request(
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
    if csrf_response.status_code != 200:
        raise RuntimeError(
            f"chatgpt_nextauth_csrf status={csrf_response.status_code} body={_response_preview(csrf_response, 200)}"
        )
    try:
        csrf_payload = csrf_response.json() or {}
    except Exception:
        csrf_payload = {}
    csrf_token = str(csrf_payload.get("csrfToken") or "").strip() if isinstance(csrf_payload, dict) else ""
    if not csrf_token:
        raise RuntimeError("chatgpt_nextauth_csrf_missing_token")

    auth_session_logging_id = str(uuid.uuid4())
    signin_query = urllib.parse.urlencode({
        "prompt": str(prompt or "login").strip() or "login",
        "screen_hint": str(screen_hint or "login_or_signup").strip() or "login_or_signup",
        "device_id": device_id,
        "ext-oai-did": device_id,
        "auth_session_logging_id": auth_session_logging_id,
    })
    signin_response = _session_request(
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
        data=urllib.parse.urlencode({
            "csrfToken": csrf_token,
            "callbackUrl": CHATGPT_HOME_URL,
            "json": "true",
        }),
        timeout=20,
    )
    if signin_response.status_code != 200:
        raise RuntimeError(
            f"chatgpt_nextauth_signin status={signin_response.status_code} body={_response_preview(signin_response, 200)}"
        )
    try:
        signin_payload = signin_response.json() or {}
    except Exception:
        signin_payload = {}
    auth_url = str(signin_payload.get("url") or "").strip() if isinstance(signin_payload, dict) else ""
    if not auth_url:
        raise RuntimeError("chatgpt_nextauth_signin_missing_url")
    nextauth_state = _get_session_cookie(
        session,
        "__Secure-next-auth.state",
        preferred_domains=("chatgpt.com", ".chatgpt.com"),
    )
    if not nextauth_state:
        raise RuntimeError("chatgpt_nextauth_signin_missing_state_cookie")
    parsed_auth_url = urllib.parse.urlparse(auth_url)
    auth_query = urllib.parse.parse_qs(parsed_auth_url.query, keep_blank_values=True)
    state = str((auth_query.get("state") or [""])[0] or "").strip()
    if not state:
        raise RuntimeError("chatgpt_nextauth_signin_missing_state")
    print(
        "[python-protocol-service] bootstrap nextauth state "
        f"url_state_len={len(state)} cookie_len={len(nextauth_state)} "
        f"cookie_contains_state={state in nextauth_state} "
        f"auth_url={_format_logged_url(auth_url)} "
        f"cookies={_cookie_debug_snapshot(session)}"
    )
    return OAuthStart(
        auth_url=auth_url,
        state=state,
        code_verifier="",
        redirect_uri="https://chatgpt.com/api/auth/callback/openai",
    )


def _generate_sentinel_headers_for_session(
    session: requests.Session,
    *,
    explicit_proxy: str | None,
    user_agent: str,
    device_id: str,
    data_build: str,
    profile: dict[str, Any] | None = None,
    request_kind: str,
    turnstile_token_override: str | None = None,
) -> dict[str, str]:
    req_token = get_pow_token(
        user_agent=user_agent,
        core=_resolve_sentinel_core(),
        screen=_resolve_sentinel_screen_sum(),
        data_build=data_build,
        **_sentinel_profile_kwargs(profile),
    )

    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.8",
        "content-type": "application/json",
        "origin": CHATGPT_BASE,
        "referer": CHATGPT_HOME_URL,
        "user-agent": user_agent,
        "oai-device-id": device_id,
        "oai-language": "en-US",
    }
    payload = {"p": req_token}

    signup_flow_map = {
        "signup-authorize-continue": "authorize_continue",
        "signup-user-register": "username_password_create",
        "signup-create-account": "oauth_create_account",
        "platform-update-organization": "update_organization",
    }
    repair_flow_map = {
        "repair-authorize-continue": "authorize_continue",
        "repair-password-verify": "password_verify",
    }

    out_headers = {
        "oai-device-id": device_id,
        "oai-language": "en-US",
    }

    def _build_signup_headers_from_browser_payload(
        browser_signup_payload: Any,
        *,
        fallback_c: str = "",
        fallback_t: str = "",
        fallback_p: str = "",
    ) -> dict[str, str] | None:
        token_payload: dict[str, str] = {}
        browser_device_id = device_id
        if isinstance(browser_signup_payload, dict):
            token_payload = (
                browser_signup_payload.get("tokenPayload")
                if isinstance(browser_signup_payload.get("tokenPayload"), dict)
                else {}
            )
            browser_device_id = str(browser_signup_payload.get("deviceId") or device_id).strip() or device_id
        if fallback_c and not str(token_payload.get("c") or "").strip():
            token_payload["c"] = fallback_c
        if fallback_t and not str(token_payload.get("t") or "").strip():
            token_payload["t"] = fallback_t
        if fallback_p and not str(token_payload.get("p") or "").strip():
            token_payload["p"] = fallback_p
        if not str(token_payload.get("p") or "").strip() or not str(token_payload.get("t") or "").strip():
            return None
        signup_headers: dict[str, str] = dict(out_headers)
        browser_user_agent = ""
        browser_cookies: list[dict[str, Any]] = []
        if isinstance(browser_signup_payload, dict):
            browser_user_agent = str(browser_signup_payload.get("userAgent") or "").strip()
            raw_browser_cookies = browser_signup_payload.get("browserCookies")
            if isinstance(raw_browser_cookies, list):
                browser_cookies = [dict(item) for item in raw_browser_cookies if isinstance(item, dict)]
        if browser_user_agent:
            signup_headers["user-agent"] = browser_user_agent
            signup_headers.update(_browser_client_hints_for_user_agent(browser_user_agent))
        request_url_for_cookies = _browser_signup_request_url_for_request_kind(request_kind)
        browser_cookie_header = (
            _browser_cookie_header_for_request(browser_cookies, request_url_for_cookies or "")
            if browser_cookies and request_url_for_cookies
            else ""
        )
        if browser_cookie_header:
            signup_headers["cookie"] = browser_cookie_header
        passkey_header = _browser_passkey_capability_header_value(browser_signup_payload)
        if passkey_header:
            signup_headers["ext-passkey-client-capabilities"] = passkey_header
        signup_headers["openai-sentinel-token"] = json.dumps(
            {
                "p": str(token_payload.get("p") or ""),
                "t": str(token_payload.get("t") or ""),
                **({"c": str(token_payload.get("c") or "")} if str(token_payload.get("c") or "") else {}),
                "id": browser_device_id,
                "flow": signup_flow_map[request_kind],
            },
            separators=(",", ":"),
        )
        if request_kind == "signup-create-account":
            session_observer_token = ""
            if isinstance(browser_signup_payload, dict):
                session_observer_token = str(browser_signup_payload.get("sessionObserverToken") or "").strip()
            if session_observer_token:
                signup_headers["openai-sentinel-so-token"] = json.dumps(
                    {
                        "so": session_observer_token,
                        **({"c": str(token_payload.get("c") or "")} if str(token_payload.get("c") or "") else {}),
                        "id": browser_device_id,
                        "flow": signup_flow_map[request_kind],
                    },
                    separators=(",", ":"),
                )
        return signup_headers

    req_response = _session_request(
        session,
        "POST",
        f"{CHATGPT_BASE}/backend-anon/sentinel/chat-requirements",
        explicit_proxy=explicit_proxy,
        request_label="sentinel-chat-requirements",
        headers=headers,
        json=payload,
        timeout=15,
    )
    if req_response.status_code != 200:
        if request_kind in signup_flow_map:
            browser_signup_payload = _capture_browser_signup_sentinel_payload(
                session=session,
                explicit_proxy=explicit_proxy,
                request_kind=request_kind,
                profile=profile,
            )
            signup_headers = _build_signup_headers_from_browser_payload(
                browser_signup_payload,
                fallback_p=req_token,
            )
            if signup_headers is not None:
                print(
                    "[python-protocol-service] signup sentinel browser fallback after chat_requirements failure "
                    f"request_kind={request_kind} status={req_response.status_code}"
                )
                return signup_headers
        raise RuntimeError(f"chat_requirements_failed status={req_response.status_code} body={_response_preview(req_response, 200)}")

    req_data = req_response.json() or {}
    sentinel_token = req_data.get("token", "")
    turnstile_data = req_data.get("turnstile", {})
    pow_data = req_data.get("proofofwork", {})

    dx = turnstile_data.get("dx", "")
    pow_seed = pow_data.get("seed", "")
    pow_diff = pow_data.get("difficulty", "")
    pow_required = pow_data.get("required", False)

    signup_request_kinds = {
        "signup-authorize-continue",
        "signup-user-register",
        "signup-create-account",
    }
    cf_clearance = _get_cloudflare_clearance_cookie(session)
    if not cf_clearance and request_kind in signup_request_kinds:
        try:
            clearance_result = solve_cloudflare_clearance(
                website_url=CHATGPT_LOGIN_URL,
                proxy=explicit_proxy,
                user_agent=user_agent,
            )
            _apply_captcha_cookies_to_session(session, clearance_result.get("cookies"))
            cf_clearance = str(
                clearance_result.get("cf_clearance")
                or clearance_result.get("token")
                or ""
            ).strip()
            if cf_clearance:
                print(
                    "[python-protocol-service] imported cf_clearance via easycaptcha "
                    f"request_kind={request_kind} len={len(cf_clearance)}"
                )
        except Exception as exc:
            print(
                "[python-protocol-service] easycaptcha clearance fetch skipped "
                f"request_kind={request_kind} err={exc}"
            )
    turnstile_token = str(turnstile_token_override or "").strip()
    if dx and not turnstile_token:
        try:
            from shared_sentinel.turnstile import process_turnstile
            turnstile_token = _normalize_turnstile_token(
                process_turnstile(dx, req_token),
                context=f"{request_kind}:chat_requirements:local_vm",
            )
            if turnstile_token:
                print(
                    "[python-protocol-service] sentinel turnstile solved via local VM "
                    f"request_kind={request_kind}"
                )
        except Exception as exc:
            print(
                "[python-protocol-service] sentinel turnstile local VM failed "
                f"request_kind={request_kind} err={exc}"
            )
        if not turnstile_token:
            try:
                solved = solve_turnstile_vm_token(
                    dx=dx,
                    proof_token=req_token,
                )
                turnstile_token = _normalize_turnstile_token(
                    solved.get("token"),
                    context=f"{request_kind}:chat_requirements:captcha_service",
                )
                if turnstile_token:
                    print(
                        "[python-protocol-service] sentinel turnstile solved via easycaptcha "
                        f"request_kind={request_kind}"
                    )
            except Exception as exc:
                print(
                    "[python-protocol-service] sentinel turnstile easycaptcha skipped "
                    f"request_kind={request_kind} err={exc}"
                )
    proof_token = generate_proof_token(
        required=pow_required,
        seed=pow_seed,
        difficulty=pow_diff,
        user_agent=user_agent,
        core=_resolve_sentinel_core(),
        screen=_resolve_sentinel_screen_sum(),
        data_build=data_build,
        **_sentinel_profile_kwargs(profile),
    )

    if request_kind in signup_flow_map:
        signup_turnstile_len = len(str(turnstile_token or "").strip())
        signup_sentinel_payload = json.dumps(
            {
                "p": str(proof_token or "").strip(),
                "t": str(turnstile_token or "").strip(),
                **({"c": str(sentinel_token or "").strip()} if str(sentinel_token or "").strip() else {}),
                "id": device_id,
                "flow": signup_flow_map[request_kind],
            },
            separators=(",", ":"),
        )
        if str(proof_token or "").strip() and str(turnstile_token or "").strip():
            out_headers["openai-sentinel-token"] = signup_sentinel_payload
        browser_signup_request_kinds = {"signup-create-account"}
        weak_signup_turnstile = signup_turnstile_len > 0 and signup_turnstile_len < 64
        if weak_signup_turnstile:
            print(
                "[python-protocol-service] weak signup turnstile token detected "
                f"request_kind={request_kind} t_len={signup_turnstile_len} "
                "-> enabling browser sentinel fallback"
            )
        if weak_signup_turnstile and _allow_browser_signup_fallback_for_request_kind(
            request_kind,
            has_local_turnstile=signup_turnstile_len > 0,
        ):
            browser_signup_request_kinds = {
                "signup-user-register",
                "signup-create-account",
            }
            if request_kind == "signup-authorize-continue" and env_flag(
                "PROTOCOL_ENABLE_BROWSER_AUTHORIZE_CONTINUE_FALLBACK",
                False,
            ):
                browser_signup_request_kinds.add("signup-authorize-continue")
        if request_kind in browser_signup_request_kinds:
            browser_signup_payload = _capture_browser_signup_sentinel_payload(
                session=session,
                explicit_proxy=explicit_proxy,
                request_kind=request_kind,
                profile=profile,
            )
            signup_headers = _build_signup_headers_from_browser_payload(
                browser_signup_payload,
                fallback_c=str(sentinel_token or "").strip(),
                fallback_t=str(turnstile_token or "").strip(),
                fallback_p=str(proof_token or "").strip(),
            )
            if signup_headers is not None:
                return signup_headers
        return out_headers

    if request_kind in repair_flow_map:
        sentinel_payload = json.dumps(
            {
                "p": proof_token,
                "t": turnstile_token,
                "c": sentinel_token,
                "id": device_id,
                "flow": repair_flow_map[request_kind],
            },
            separators=(",", ":"),
        )
        out_headers["openai-sentinel-token"] = sentinel_payload
        return out_headers

    if sentinel_token:
        out_headers["openai-sentinel-chat-requirements-token"] = sentinel_token
    if turnstile_token:
        out_headers["openai-sentinel-turnstile-token"] = turnstile_token
    if proof_token:
        out_headers["openai-sentinel-proof-token"] = proof_token

    return out_headers


def _follow_redirect_chain_for_callback(
    session: requests.Session,
    start_url: str,
    *,
    explicit_proxy: str | None,
    referer: str = CONSENT_REFERER,
    max_redirects: int = 6,
) -> str:
    current_url = start_url
    last_response_url = ""
    last_status = 0
    last_preview = ""
    for _ in range(max_redirects):
        response = _session_request(
            session,
            "GET",
            current_url,
            explicit_proxy=explicit_proxy,
            request_label="callback-redirect",
            allow_redirects=False,
            timeout=15,
            headers={
                "Referer": referer,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        location = str(
            response.headers.get("Location")
            or response.headers.get("location")
            or ""
        ).strip()
        next_url = urllib.parse.urljoin(current_url, location) if location else current_url
        response_url = str(getattr(response, "url", "") or current_url)
        print(
            "[python-protocol-service] callback chain step "
            f"url={_format_logged_url(current_url)} status={response.status_code} "
            f"location={_format_logged_url(location)} response_url={_format_logged_url(getattr(response, 'url', '') or '')}"
        )
        last_response_url = response_url
        last_status = int(getattr(response, "status_code", 0) or 0)
        last_preview = _response_preview(response, 200)
        if _is_phone_wall_text(f"{current_url}\n{next_url}\n{_response_preview(response, 200)}"):
            raise RuntimeError(
                f"phone_wall context=callback_redirect body={_response_preview(response, 200)}"
            )
        response_html = str(getattr(response, "text", "") or "")
        if _is_codex_consent_html(url=response_url, html=response_html):
            print(
                "[python-protocol-service] callback chain reached codex consent "
                f"url={response_url}"
            )
            return _submit_codex_consent_form(
                session,
                consent_url=response_url,
                response=response,
                explicit_proxy=explicit_proxy,
            )
        if response.status_code not in (301, 302, 303, 307, 308):
            break
        if not location:
            break
        if _is_callback_url(next_url):
            return next_url
        current_url = next_url

    raise RuntimeError(
        "callback_url_not_found "
        f"final_url={last_response_url or current_url or start_url} "
        f"final_status={last_status or '<unknown>'} "
        f"final_body={last_preview}"
    )


def _complete_external_continue_url(
    session: requests.Session,
    response: Any,
    *,
    explicit_proxy: str | None,
    request_label: str,
    referer: str,
) -> Any:
    if _extract_page_type(response) != "external_url":
        return response

    try:
        payload = response.json() or {}
    except Exception:
        payload = {}

    continue_url = str(payload.get("continue_url") or "").strip() if isinstance(payload, dict) else ""
    if not continue_url:
        raise RuntimeError(f"{request_label}_external_url_missing_continue_url")

    print(
        "[python-protocol-service] following external continue url "
        f"label={request_label} url={_format_logged_url(continue_url)}"
    )
    follow_response = _session_request(
        session,
        "GET",
        continue_url,
        explicit_proxy=explicit_proxy,
        request_label=f"{request_label}-external-url",
        timeout=20,
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "referer": referer,
        },
    )
    if follow_response.status_code >= 400:
        raise RuntimeError(
            f"{request_label}_external_url_follow_failed "
            f"status={follow_response.status_code} body={_response_preview(follow_response, 200)}"
        )
    return follow_response


def _extract_workspace_ids_from_auth_session_payload(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    workspaces = payload.get("workspaces")
    if not isinstance(workspaces, list):
        return []
    workspace_ids: list[str] = []
    for item in workspaces:
        if not isinstance(item, dict):
            continue
        workspace_id = str(item.get("id") or "").strip()
        if workspace_id and workspace_id not in workspace_ids:
            workspace_ids.append(workspace_id)
    return workspace_ids


def _extract_workspace_entries_from_auth_session_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    workspaces = payload.get("workspaces")
    if not isinstance(workspaces, list):
        return []
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in workspaces:
        if not isinstance(item, dict):
            continue
        workspace_id = str(item.get("id") or "").strip()
        if not workspace_id or workspace_id in seen_ids:
            continue
        seen_ids.add(workspace_id)
        entries.append(item)
    return entries


def _workspace_selector_value(key: str) -> str:
    overrides = _WORKSPACE_SELECTOR_CONTEXT.get() or {}
    if key in overrides:
        return str(overrides.get(key) or "").strip()
    return str(os.environ.get(key) or "").strip()


@contextlib.contextmanager
def temporary_workspace_selector_overrides(overrides: dict[str, str] | None):
    normalized: dict[str, str] = {}
    for key, value in (overrides or {}).items():
        normalized_key = str(key or "").strip()
        normalized_value = str(value or "").strip()
        if normalized_key and normalized_value:
            normalized[normalized_key] = normalized_value
    token = _WORKSPACE_SELECTOR_CONTEXT.set(normalized or None)
    try:
        yield
    finally:
        _WORKSPACE_SELECTOR_CONTEXT.reset(token)


def _preferred_workspace_kind() -> str:
    return _workspace_selector_value("PROTOCOL_PREFERRED_WORKSPACE_KIND").lower()


def _workspace_kind_aliases(kind: Any) -> set[str]:
    normalized = str(kind or "").strip().lower()
    if not normalized:
        return set()
    if normalized in {"team", "organization", "workspace"}:
        return {"team", "organization", "workspace"}
    return {normalized}


def _workspace_entry_kind(entry: dict[str, Any]) -> str:
    if not isinstance(entry, dict):
        return ""
    return str(
        entry.get("kind")
        or entry.get("structure")
        or entry.get("type")
        or entry.get("account_type")
        or ""
    ).strip().lower()


def _workspace_entry_matches_preferred_kind(entry: dict[str, Any], preferred_kind: str) -> bool:
    expected = _workspace_kind_aliases(preferred_kind)
    if not expected:
        return True
    actual = _workspace_kind_aliases(_workspace_entry_kind(entry))
    return bool(actual & expected)


def _extract_workspace_entries_from_chatgpt_accounts_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        raw_entries = payload.get("accounts")
        if not isinstance(raw_entries, list):
            raw_entries = payload.get("items")
    elif isinstance(payload, list):
        raw_entries = payload
    else:
        raw_entries = None
    if not isinstance(raw_entries, list):
        return []

    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        workspace_id = str(item.get("account_id") or item.get("id") or "").strip()
        if not workspace_id or workspace_id in seen_ids:
            continue
        seen_ids.add(workspace_id)
        structure = str(
            item.get("structure")
            or item.get("kind")
            or item.get("type")
            or item.get("account_type")
            or ""
        ).strip().lower()
        if structure in {"team", "organization", "workspace"}:
            normalized_kind = "team"
        elif structure == "personal":
            normalized_kind = "personal"
        else:
            normalized_kind = structure or "unknown"
        workspace_name = str(
            item.get("name")
            or item.get("title")
            or item.get("workspace_name")
            or item.get("account_name")
            or item.get("email")
            or ""
        ).strip()
        entries.append(
            {
                "id": workspace_id,
                "kind": normalized_kind,
                "name": workspace_name,
                "title": workspace_name,
                "source": "chatgpt_accounts",
            }
        )
    return entries


def _merge_workspace_entries(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for collection in (primary, secondary):
        for entry in collection:
            if not isinstance(entry, dict):
                continue
            workspace_id = str(entry.get("id") or "").strip()
            if not workspace_id or workspace_id in seen_ids:
                continue
            seen_ids.add(workspace_id)
            merged.append(entry)
    return merged


def _select_workspace_entry(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not entries:
        return None

    def _entry_at_preferred_index(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        raw_index = _workspace_selector_value("PROTOCOL_PREFERRED_WORKSPACE_INDEX")
        if not raw_index:
            return None
        try:
            parsed_index = int(raw_index)
        except Exception:
            return None
        if parsed_index < 0:
            parsed_index = len(candidates) + parsed_index
        if 0 <= parsed_index < len(candidates):
            return candidates[parsed_index]
        return None

    preferred_workspace_id = _workspace_selector_value("PROTOCOL_PREFERRED_WORKSPACE_ID")
    if preferred_workspace_id:
        for entry in entries:
            if str(entry.get("id") or "").strip() == preferred_workspace_id:
                return entry

    preferred_workspace_name = _workspace_selector_value("PROTOCOL_PREFERRED_WORKSPACE_NAME").lower()
    if preferred_workspace_name:
        for entry in entries:
            name = str(entry.get("name") or entry.get("title") or "").strip().lower()
            if preferred_workspace_name and preferred_workspace_name in name:
                return entry

    preferred_kind = _workspace_selector_value("PROTOCOL_PREFERRED_WORKSPACE_KIND").lower()
    if preferred_kind:
        kind_matches = [
            entry for entry in entries
            if _workspace_entry_matches_preferred_kind(entry, preferred_kind)
        ]
        if kind_matches:
            indexed_kind_match = _entry_at_preferred_index(kind_matches)
            if indexed_kind_match is not None:
                return indexed_kind_match
            return kind_matches[-1]

    indexed_entry = _entry_at_preferred_index(entries)
    if indexed_entry is not None:
        return indexed_entry

    personal_entries = [
        entry for entry in entries
        if str(entry.get("kind") or "").strip().lower() == "personal"
    ]
    if personal_entries:
        return personal_entries[-1]

    return entries[-1]


def _select_workspace_id_from_entries(entries: list[dict[str, Any]]) -> str:
    selected = _select_workspace_entry(entries)
    if not isinstance(selected, dict):
        return ""
    return str(selected.get("id") or "").strip()


def _select_workspace_id_from_id_list(workspace_ids: list[str]) -> str:
    normalized_ids = [str(item or "").strip() for item in workspace_ids if str(item or "").strip()]
    if not normalized_ids:
        return ""

    preferred_workspace_id = _workspace_selector_value("PROTOCOL_PREFERRED_WORKSPACE_ID")
    if preferred_workspace_id and preferred_workspace_id in normalized_ids:
        return preferred_workspace_id

    raw_index = _workspace_selector_value("PROTOCOL_PREFERRED_WORKSPACE_INDEX")
    if raw_index:
        try:
            parsed_index = int(raw_index)
        except Exception:
            parsed_index = -10**9
        if parsed_index < 0:
            parsed_index = len(normalized_ids) + parsed_index
        if 0 <= parsed_index < len(normalized_ids):
            return normalized_ids[parsed_index]

    return normalized_ids[-1]


def _workspace_debug_summary(entries: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for idx, entry in enumerate(entries):
        workspace_id = str(entry.get("id") or "").strip()
        workspace_kind = _workspace_entry_kind(entry) or "unknown"
        workspace_name = str(entry.get("name") or entry.get("title") or entry.get("profile_picture_alt_text") or "").strip() or "<none>"
        parts.append(f"{idx}:{workspace_kind}:{workspace_name}:{workspace_id}")
    return " | ".join(parts) if parts else "<none>"


def _select_org_entry(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not entries:
        return None

    preferred_org_id = str(os.environ.get("PROTOCOL_PREFERRED_ORG_ID") or "").strip()
    if preferred_org_id:
        for entry in entries:
            if str(entry.get("id") or "").strip() == preferred_org_id:
                return entry

    preferred_org_name = str(os.environ.get("PROTOCOL_PREFERRED_ORG_NAME") or "").strip().lower()
    if preferred_org_name:
        for entry in entries:
            title = str(entry.get("title") or entry.get("name") or "").strip().lower()
            if preferred_org_name and preferred_org_name in title:
                return entry

    return entries[-1]


def _select_project_id_from_org_entry(entry: dict[str, Any]) -> str:
    projects = entry.get("projects")
    if not isinstance(projects, list):
        return ""

    preferred_project_id = str(os.environ.get("PROTOCOL_PREFERRED_PROJECT_ID") or "").strip()
    if preferred_project_id:
        for item in projects:
            if str((item or {}).get("id") or "").strip() == preferred_project_id:
                return preferred_project_id

    normalized_project_ids = [
        str((item or {}).get("id") or "").strip()
        for item in projects
        if isinstance(item, dict) and str((item or {}).get("id") or "").strip()
    ]
    if not normalized_project_ids:
        return ""
    return normalized_project_ids[-1]


def _fetch_client_auth_session_dump(
    session: requests.Session,
    *,
    explicit_proxy: str | None,
) -> dict[str, Any]:
    response = _session_request(
        session,
        "GET",
        CLIENT_AUTH_SESSION_DUMP_URL,
        explicit_proxy=explicit_proxy,
        request_label="client-auth-session-dump",
        headers={
            "accept": "application/json",
            "referer": CONSENT_REFERER,
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"client_auth_session_dump status={response.status_code} body={_response_preview(response, 300)}"
        )
    payload = response.json() or {}
    if not isinstance(payload, dict):
        raise RuntimeError("invalid_client_auth_session_dump")
    return payload


def _fetch_chatgpt_account_entries_from_session(
    session: requests.Session,
    *,
    explicit_proxy: str | None,
) -> list[dict[str, Any]]:
    response = _session_request(
        session,
        "GET",
        CHATGPT_ACCOUNTS_URL,
        explicit_proxy=explicit_proxy,
        request_label="chatgpt-accounts",
        headers=_build_protocol_headers(
            request_kind="",
            referer=CHATGPT_HOME_URL,
            content_type=None,
        ),
        timeout=20,
    )
    if response.status_code >= 400:
        print(
            "[python-protocol-service] chatgpt accounts fallback unavailable "
            f"status={response.status_code} body={_response_preview(response, 240)}"
        )
        return []
    try:
        payload = response.json()
    except Exception as exc:
        print(f"[python-protocol-service] chatgpt accounts fallback invalid json err={exc}")
        return []
    entries = _extract_workspace_entries_from_chatgpt_accounts_payload(payload)
    if entries:
        print(
            "[python-protocol-service] chatgpt accounts fallback candidates "
            f"count={len(entries)} candidates={_workspace_debug_summary(entries)}"
        )
    else:
        print("[python-protocol-service] chatgpt accounts fallback returned no entries")
    return entries


def _extract_bearer_token(value: Any) -> str:
    token = str(value or "").strip()
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()
    return token


def _extract_account_id_from_auth_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    direct = str(payload.get("account_id") or payload.get("chatgpt_account_id") or "").strip()
    if direct:
        return direct
    nested = payload.get("https://api.openai.com/auth")
    if isinstance(nested, dict):
        nested_direct = str(nested.get("chatgpt_account_id") or nested.get("account_id") or "").strip()
        if nested_direct:
            return nested_direct
    return ""


def _fetch_chatgpt_account_entries_from_auth_payload(
    session: requests.Session,
    *,
    auth_payload: dict[str, Any],
    explicit_proxy: str | None,
) -> list[dict[str, Any]]:
    access_token = _extract_bearer_token(auth_payload.get("access_token"))
    current_account_id = _extract_account_id_from_auth_payload(auth_payload)
    if not access_token or not current_account_id:
        return []

    path = "/backend-api/accounts"
    headers = _build_protocol_headers(
        request_kind="",
        referer=CHATGPT_HOME_URL,
        content_type=None,
    )
    headers["authorization"] = f"Bearer {access_token}"
    headers["chatgpt-account-id"] = current_account_id
    headers["x-openai-target-path"] = path
    headers["x-openai-target-route"] = path
    oai_device_id = str(
        _get_session_cookie(
            session,
            "oai-did",
            preferred_domains=(".chatgpt.com", "chatgpt.com", ".openai.com", "openai.com"),
        ) or ""
    ).strip()
    if oai_device_id:
        headers["oai-device-id"] = oai_device_id
    headers["oai-session-id"] = str(uuid.uuid4())

    response = _session_request(
        session,
        "GET",
        CHATGPT_ACCOUNTS_URL,
        explicit_proxy=explicit_proxy,
        request_label="chatgpt-accounts-auth",
        headers=headers,
        timeout=20,
    )
    if response.status_code >= 400:
        print(
            "[python-protocol-service] chatgpt accounts bearer lookup failed "
            f"status={response.status_code} body={_response_preview(response, 240)}"
        )
        return []
    try:
        payload = response.json()
    except Exception as exc:
        print(f"[python-protocol-service] chatgpt accounts bearer lookup invalid json err={exc}")
        return []
    entries = _extract_workspace_entries_from_chatgpt_accounts_payload(payload)
    if entries:
        print(
            "[python-protocol-service] chatgpt accounts bearer lookup candidates "
            f"count={len(entries)} candidates={_workspace_debug_summary(entries)}"
        )
    else:
        print("[python-protocol-service] chatgpt accounts bearer lookup returned no entries")
    return entries


def _maybe_recover_personal_protocol_result(
    *,
    session: requests.Session,
    initial_result: ProtocolRegistrationResult,
    oauth: Any,
    explicit_proxy: str | None,
    default_email: str,
    mailbox_ref: str,
    password: str,
    first_name: str,
    last_name: str,
    birthdate: str,
    workspace_request_label: str,
    header_builder: ProtocolSentinelContext | None = None,
) -> ProtocolRegistrationResult:
    if _preferred_workspace_kind() != "personal":
        return initial_result

    current_account_id = _extract_account_id_from_auth_payload(initial_result.auth)
    if not current_account_id:
        return initial_result

    account_entries = _fetch_chatgpt_account_entries_from_auth_payload(
        session,
        auth_payload=initial_result.auth,
        explicit_proxy=explicit_proxy,
    )
    if not account_entries:
        return initial_result

    personal_entries = [
        entry for entry in account_entries
        if _workspace_entry_matches_preferred_kind(entry, "personal")
    ]
    if not personal_entries:
        print(
            "[python-protocol-service] personal workspace recovery skipped "
            f"reason=no_personal_account current_account_id={current_account_id}"
        )
        return initial_result

    selected_personal_entry = _select_workspace_entry(account_entries)
    personal_account_id = str((selected_personal_entry or {}).get("id") or "").strip()
    if not personal_account_id or personal_account_id == current_account_id:
        print(
            "[python-protocol-service] personal workspace recovery skipped "
            f"reason=already_personal current_account_id={current_account_id}"
        )
        return initial_result

    print(
        "[python-protocol-service] personal workspace recovery attempting switch "
        f"current_account_id={current_account_id} personal_account_id={personal_account_id} "
        f"candidates={_workspace_debug_summary(account_entries)}"
    )
    try:
        callback_url = _submit_workspace_selection_for_callback(
            session=session,
            workspace_id=personal_account_id,
            explicit_proxy=explicit_proxy,
            referer=CONSENT_REFERER,
            workspace_request_label=workspace_request_label,
            header_builder=header_builder,
        )
        recovered_result = _callback_result_from_url(
            callback_url=callback_url,
            oauth=oauth,
            explicit_proxy=explicit_proxy,
            default_email=default_email,
            mailbox_ref=mailbox_ref,
            password=password,
            first_name=first_name,
            last_name=last_name,
            birthdate=birthdate,
            token_post_try_direct_first=True,
        )
    except Exception as exc:
        print(f"[python-protocol-service] personal workspace recovery failed err={exc}")
        return initial_result

    recovered_account_id = _extract_account_id_from_auth_payload(recovered_result.auth)
    if recovered_account_id != personal_account_id:
        print(
            "[python-protocol-service] personal workspace recovery returned unexpected account "
            f"expected={personal_account_id} got={recovered_account_id or '<none>'}"
        )
        return initial_result

    print(
        "[python-protocol-service] personal workspace recovery succeeded "
        f"current_account_id={current_account_id} recovered_account_id={recovered_account_id}"
    )
    return recovered_result


def _extract_workspace_id_from_session(
    session: requests.Session,
    *,
    explicit_proxy: str | None,
) -> str:
    auth_cookie = _get_session_cookie(
        session,
        "oai-client-auth-session",
        preferred_domains=(".openai.com", "auth.openai.com", ".chatgpt.com", "chatgpt.com"),
    )
    if auth_cookie:
        auth_payload = _decode_jwt_segment((str(auth_cookie).split(".")[0] or "").strip())
        cookie_workspace_entries = _extract_workspace_entries_from_auth_session_payload(auth_payload)
        selected_cookie_entry = _select_workspace_entry(cookie_workspace_entries)
        selected_cookie_workspace_id = str((selected_cookie_entry or {}).get("id") or "").strip()
        preferred_kind = _preferred_workspace_kind()
        if selected_cookie_workspace_id and (
            not preferred_kind or _workspace_entry_matches_preferred_kind(selected_cookie_entry or {}, preferred_kind)
        ):
            print(
                "[python-protocol-service] workspace selected from auth cookie "
                f"workspace_id={selected_cookie_workspace_id} "
                f"candidates={_workspace_debug_summary(cookie_workspace_entries)}"
            )
            return selected_cookie_workspace_id
        if cookie_workspace_entries:
            account_workspace_entries = _fetch_chatgpt_account_entries_from_session(
                session,
                explicit_proxy=explicit_proxy,
            )
            merged_entries = _merge_workspace_entries(cookie_workspace_entries, account_workspace_entries)
            selected_merged_entry = _select_workspace_entry(merged_entries)
            selected_merged_workspace_id = str((selected_merged_entry or {}).get("id") or "").strip()
            if selected_merged_workspace_id and (
                not preferred_kind or _workspace_entry_matches_preferred_kind(selected_merged_entry or {}, preferred_kind)
            ):
                print(
                    "[python-protocol-service] workspace selected from chatgpt accounts fallback "
                    f"workspace_id={selected_merged_workspace_id} "
                    f"candidates={_workspace_debug_summary(merged_entries)}"
                )
                return selected_merged_workspace_id
        if selected_cookie_workspace_id:
            print(
                "[python-protocol-service] workspace fallback kept auth cookie selection "
                f"workspace_id={selected_cookie_workspace_id} "
                f"preferred_kind={preferred_kind or '<none>'} "
                f"candidates={_workspace_debug_summary(cookie_workspace_entries)}"
            )
            return selected_cookie_workspace_id

    dump_payload = _fetch_client_auth_session_dump(
        session,
        explicit_proxy=explicit_proxy,
    )
    dump_workspace_entries = _extract_workspace_entries_from_auth_session_payload(
        dump_payload.get("client_auth_session"),
    )
    selected_dump_workspace_id = _select_workspace_id_from_entries(dump_workspace_entries)
    if selected_dump_workspace_id:
        print(
            "[python-protocol-service] workspace recovered from client auth session dump "
            f"workspace_id={selected_dump_workspace_id} "
            f"workspace_count={len(dump_workspace_entries)} "
            f"candidates={_workspace_debug_summary(dump_workspace_entries)}"
        )
        return selected_dump_workspace_id

    if not auth_cookie:
        raise RuntimeError("missing_auth_cookie")
    raise RuntimeError("missing_workspace")


def _callback_result_from_url(
    *,
    callback_url: str,
    oauth: Any,
    explicit_proxy: str | None,
    default_email: str,
    mailbox_ref: str,
    password: str,
    first_name: str,
    last_name: str,
    birthdate: str,
    token_post_try_direct_first: bool = True,
) -> ProtocolRegistrationResult:
    result_email, auth_json_text = submit_callback_url(
        callback_url=callback_url,
        code_verifier=oauth.code_verifier,
        redirect_uri=oauth.redirect_uri,
        expected_state=oauth.state,
        proxy=explicit_proxy,
        mailbox_ref=mailbox_ref,
        password=password,
        first_name=first_name,
        last_name=last_name,
        birthdate=birthdate,
        token_post_try_direct_first=token_post_try_direct_first,
    )
    auth = json.loads(auth_json_text)
    if not isinstance(auth, dict):
        raise RuntimeError("invalid_auth_payload")
    return ProtocolRegistrationResult(email=result_email or default_email, auth=auth)


def _maybe_finish_codex_oauth_from_response(
    *,
    session: requests.Session,
    response: Any,
    oauth: Any,
    explicit_proxy: str | None,
    default_email: str,
    mailbox_ref: str,
    password: str,
    first_name: str,
    last_name: str,
    birthdate: str,
    referer: str,
    context_label: str,
) -> ProtocolRegistrationResult | None:
    response_url = _response_url(response)
    response_location = _response_location(response)
    response_continue_url = _response_continue_url(response)
    page_type = _extract_page_type(response) or "unknown"
    print(
        "[python-protocol-service] inspect oauth completion response "
        f"label={context_label} status={getattr(response, 'status_code', '<unknown>')} "
        f"url={response_url or '<none>'} location={response_location or '<none>'} "
        f"page_type={page_type}"
    )

    direct_candidates = (
        ("response_url", response_url),
        (
            "location",
            urllib.parse.urljoin(response_url or referer or oauth.auth_url, response_location)
            if response_location
            else "",
        ),
        ("continue_url", response_continue_url),
    )
    for source_name, candidate_url in direct_candidates:
        if not _is_callback_url(candidate_url):
            continue
        print(
            "[python-protocol-service] oauth completion received callback "
            f"label={context_label} source={source_name} url={_format_logged_url(candidate_url)}"
        )
        initial_result = _callback_result_from_url(
            callback_url=candidate_url,
            oauth=oauth,
            explicit_proxy=explicit_proxy,
            default_email=default_email,
            mailbox_ref=mailbox_ref,
            password=password,
            first_name=first_name,
            last_name=last_name,
            birthdate=birthdate,
            token_post_try_direct_first=True,
        )
        return _maybe_recover_personal_protocol_result(
            session=session,
            initial_result=initial_result,
            oauth=oauth,
            explicit_proxy=explicit_proxy,
            default_email=default_email,
            mailbox_ref=mailbox_ref,
            password=password,
            first_name=first_name,
            last_name=last_name,
            birthdate=birthdate,
            workspace_request_label="workspace-select-response-personal-recovery",
            header_builder=None,
        )

    response_html = str(getattr(response, "text", "") or "")
    if _is_codex_consent_html(url=response_url or referer, html=response_html):
        try:
            print(
                "[python-protocol-service] oauth completion reached codex consent "
                f"label={context_label} url={_format_logged_url(response_url or referer)}"
            )
            consent_callback_url = _submit_codex_consent_form(
                session,
                consent_url=response_url or referer,
                response=response,
                explicit_proxy=explicit_proxy,
            )
            initial_result = _callback_result_from_url(
                callback_url=consent_callback_url,
                oauth=oauth,
                explicit_proxy=explicit_proxy,
                default_email=default_email,
                mailbox_ref=mailbox_ref,
                password=password,
                first_name=first_name,
                last_name=last_name,
                birthdate=birthdate,
                token_post_try_direct_first=True,
            )
            return _maybe_recover_personal_protocol_result(
                session=session,
                initial_result=initial_result,
                oauth=oauth,
                explicit_proxy=explicit_proxy,
                default_email=default_email,
                mailbox_ref=mailbox_ref,
                password=password,
                first_name=first_name,
                last_name=last_name,
                birthdate=birthdate,
                workspace_request_label="workspace-select-consent-personal-recovery",
                header_builder=None,
            )
        except RuntimeError as exc:
            print(
                "[python-protocol-service] oauth completion consent handling failed "
                f"label={context_label} err={exc}"
            )

    redirect_candidates = (
        ("continue_url", response_continue_url),
        (
            "location",
            urllib.parse.urljoin(response_url or referer or oauth.auth_url, response_location)
            if response_location
            else "",
        ),
    )
    for source_name, candidate_url in redirect_candidates:
        normalized_candidate = str(candidate_url or "").strip()
        if not normalized_candidate:
            continue
        try:
            print(
                "[python-protocol-service] oauth completion following redirect candidate "
                f"label={context_label} source={source_name} url={_format_logged_url(normalized_candidate)}"
            )
            callback_url = _follow_redirect_chain_for_callback(
                session,
                normalized_candidate,
                explicit_proxy=explicit_proxy,
                referer=response_url or referer or oauth.auth_url,
            )
            initial_result = _callback_result_from_url(
                callback_url=callback_url,
                oauth=oauth,
                explicit_proxy=explicit_proxy,
                default_email=default_email,
                mailbox_ref=mailbox_ref,
                password=password,
                first_name=first_name,
                last_name=last_name,
                birthdate=birthdate,
                token_post_try_direct_first=True,
            )
            return _maybe_recover_personal_protocol_result(
                session=session,
                initial_result=initial_result,
                oauth=oauth,
                explicit_proxy=explicit_proxy,
                default_email=default_email,
                mailbox_ref=mailbox_ref,
                password=password,
                first_name=first_name,
                last_name=last_name,
                birthdate=birthdate,
                workspace_request_label="workspace-select-redirect-personal-recovery",
                header_builder=None,
            )
        except RuntimeError as exc:
            print(
                "[python-protocol-service] oauth completion redirect candidate failed "
                f"label={context_label} source={source_name} err={exc}"
            )

    return None


def _submit_workspace_selection_for_callback(
    *,
    session: requests.Session,
    workspace_id: str,
    explicit_proxy: str | None,
    referer: str,
    workspace_request_label: str,
    header_builder: ProtocolSentinelContext | None = None,
) -> str:
    normalized_workspace_id = str(workspace_id or "").strip()
    if not normalized_workspace_id:
        raise RuntimeError("empty_workspace_id")

    workspace_response = _session_request(
        session,
        "POST",
        WORKSPACE_SELECT_URL,
        explicit_proxy=explicit_proxy,
        request_label=workspace_request_label,
        headers=_build_protocol_headers(
            request_kind="workspace-select",
            referer=referer,
            sentinel_context=header_builder,
        ),
        data=json.dumps({"workspace_id": normalized_workspace_id}),
    )
    if workspace_response.status_code != 200:
        raise RuntimeError(
            f"workspace_select status={workspace_response.status_code} body={_response_preview(workspace_response)}"
        )
    _raise_if_phone_wall_response(workspace_response, context="workspace_select")

    continue_url = str((workspace_response.json() or {}).get("continue_url") or "").strip()
    if not continue_url:
        raise RuntimeError("missing_continue_url")

    return _follow_redirect_chain_for_callback(
        session,
        continue_url,
        explicit_proxy=explicit_proxy,
        referer=referer,
    )


def _exchange_authenticated_session_for_codex_result(
    *,
    session: requests.Session,
    oauth: Any,
    explicit_proxy: str | None,
    default_email: str,
    mailbox_ref: str,
    password: str,
    first_name: str,
    last_name: str,
    birthdate: str,
    workspace_request_label: str,
    header_builder: ProtocolSentinelContext | None = None,
) -> ProtocolRegistrationResult:
    workspace_id = _extract_workspace_id_from_session(
        session,
        explicit_proxy=explicit_proxy,
    )
    callback_url = _submit_workspace_selection_for_callback(
        session=session,
        workspace_id=workspace_id,
        explicit_proxy=explicit_proxy,
        referer=CONSENT_REFERER,
        workspace_request_label=workspace_request_label,
        header_builder=header_builder,
    )
    initial_result = _callback_result_from_url(
        callback_url=callback_url,
        oauth=oauth,
        explicit_proxy=explicit_proxy,
        default_email=default_email,
        mailbox_ref=mailbox_ref,
        password=password,
        first_name=first_name,
        last_name=last_name,
        birthdate=birthdate,
        token_post_try_direct_first=True,
    )
    return _maybe_recover_personal_protocol_result(
        session=session,
        initial_result=initial_result,
        oauth=oauth,
        explicit_proxy=explicit_proxy,
        default_email=default_email,
        mailbox_ref=mailbox_ref,
        password=password,
        first_name=first_name,
        last_name=last_name,
        birthdate=birthdate,
        workspace_request_label=f"{workspace_request_label}-personal-recovery",
        header_builder=header_builder,
    )


def _continue_authenticated_codex_oauth(
    *,
    session: requests.Session,
    oauth: Any,
    explicit_proxy: str | None,
    default_email: str,
    mailbox_ref: str,
    password: str,
    first_name: str,
    last_name: str,
    birthdate: str,
    request_label: str,
    prior_response: Any | None = None,
    prior_response_referer: str = EMAIL_VERIFICATION_REFERER,
) -> ProtocolRegistrationResult:
    if prior_response is not None:
        live_result = _maybe_finish_codex_oauth_from_response(
            session=session,
            response=prior_response,
            oauth=oauth,
            explicit_proxy=explicit_proxy,
            default_email=default_email,
            mailbox_ref=mailbox_ref,
            password=password,
            first_name=first_name,
            last_name=last_name,
            birthdate=birthdate,
            referer=prior_response_referer,
            context_label=f"{request_label}-prior-response",
        )
        if live_result is not None:
            return live_result

    response = _session_request(
        session,
        "GET",
        oauth.auth_url,
        explicit_proxy=explicit_proxy,
        request_label=request_label,
        allow_redirects=False,
        timeout=20,
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    location = str(
        response.headers.get("Location")
        or response.headers.get("location")
        or ""
    ).strip()
    next_url = urllib.parse.urljoin(oauth.auth_url, location) if location else ""
    print(
        "[python-protocol-service] authenticated oauth redirect entry "
        f"status={response.status_code} location={_format_logged_url(location)}"
    )
    replay_result = _maybe_finish_codex_oauth_from_response(
        session=session,
        response=response,
        oauth=oauth,
        explicit_proxy=explicit_proxy,
        default_email=default_email,
        mailbox_ref=mailbox_ref,
        password=password,
        first_name=first_name,
        last_name=last_name,
        birthdate=birthdate,
        referer=oauth.auth_url,
        context_label=f"{request_label}-replayed-oauth",
    )
    if replay_result is not None:
        return replay_result
    raise RuntimeError(
        "authenticated_codex_oauth_no_redirect "
        f"status={response.status_code} location={location or '<none>'} "
        f"url={_response_url(response) or '<none>'} body={_response_preview(response, 200)}"
    )


def _handoff_authenticated_chatgpt_session_to_codex(
    *,
    session: requests.Session,
    explicit_proxy: str | None,
    email: str,
    mailbox_ref: str,
    first_name: str,
    last_name: str,
    birthdate: str,
) -> ProtocolRegistrationResult:
    if not env_flag(PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV, True):
        raise RuntimeError("browser_stage2_handoff_disabled")
    print(
        "[python-protocol-service] starting codex session handoff "
        f"email={email} strategy=session_handoff_without_password"
    )
    oauth = generate_oauth_url(prompt=None)
    response = _session_request(
        session,
        "GET",
        oauth.auth_url,
        explicit_proxy=explicit_proxy,
        request_label="oauth-authorize-codex-handoff",
        allow_redirects=False,
        timeout=20,
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    location = str(
        response.headers.get("Location")
        or response.headers.get("location")
        or ""
    ).strip()
    next_url = urllib.parse.urljoin(oauth.auth_url, location) if location else ""
    if next_url and "code=" in next_url and "state=" in next_url:
        print(
            "[python-protocol-service] codex session handoff received direct callback redirect "
            f"email={email}"
        )
        initial_result = _callback_result_from_url(
            callback_url=next_url,
            oauth=oauth,
            explicit_proxy=explicit_proxy,
            default_email=email,
            mailbox_ref=mailbox_ref,
            password="",
            first_name=first_name,
            last_name=last_name,
            birthdate=birthdate,
            token_post_try_direct_first=True,
        )
        return _maybe_recover_personal_protocol_result(
            session=session,
            initial_result=initial_result,
            oauth=oauth,
            explicit_proxy=explicit_proxy,
            default_email=email,
            mailbox_ref=mailbox_ref,
            password="",
            first_name=first_name,
            last_name=last_name,
            birthdate=birthdate,
            workspace_request_label="workspace-select-handoff-direct-personal-recovery",
            header_builder=None,
        )

    try:
        print(
            "[python-protocol-service] codex session handoff attempting workspace exchange "
            f"email={email} location={_format_logged_url(next_url)}"
        )
        return _exchange_authenticated_session_for_codex_result(
            session=session,
            oauth=oauth,
            explicit_proxy=explicit_proxy,
            default_email=email,
            mailbox_ref=mailbox_ref,
            password="",
            first_name=first_name,
            last_name=last_name,
            birthdate=birthdate,
            workspace_request_label="workspace-select-codex-handoff",
            header_builder=None,
        )
    except RuntimeError as workspace_exc:
        if next_url:
            try:
                print(
                    "[python-protocol-service] codex session handoff workspace exchange failed, "
                    f"trying redirect chain email={email} location={_format_logged_url(next_url)}"
                )
                callback_url = _follow_redirect_chain_for_callback(
                    session,
                    next_url,
                    explicit_proxy=explicit_proxy,
                    referer=oauth.auth_url,
                )
                initial_result = _callback_result_from_url(
                    callback_url=callback_url,
                    oauth=oauth,
                    explicit_proxy=explicit_proxy,
                    default_email=email,
                    mailbox_ref=mailbox_ref,
                    password="",
                    first_name=first_name,
                    last_name=last_name,
                    birthdate=birthdate,
                    token_post_try_direct_first=True,
                )
                return _maybe_recover_personal_protocol_result(
                    session=session,
                    initial_result=initial_result,
                    oauth=oauth,
                    explicit_proxy=explicit_proxy,
                    default_email=email,
                    mailbox_ref=mailbox_ref,
                    password="",
                    first_name=first_name,
                    last_name=last_name,
                    birthdate=birthdate,
                    workspace_request_label="workspace-select-handoff-redirect-personal-recovery",
                    header_builder=None,
                )
            except RuntimeError as redirect_exc:
                raise RuntimeError(
                    "codex_session_handoff_failed "
                    "strategy=session_handoff_without_password "
                    f"status={response.status_code} location={next_url or '<none>'} "
                    f"workspace_err={workspace_exc} redirect_err={redirect_exc}"
                ) from redirect_exc
        raise RuntimeError(
            "codex_session_handoff_failed "
            "strategy=session_handoff_without_password "
            f"status={response.status_code} location={next_url or '<none>'} "
            f"workspace_err={workspace_exc} body={_response_preview(response, 200)}"
        ) from workspace_exc


def _session_request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    explicit_proxy: str | None,
    request_label: str,
    **kwargs: Any,
) -> Any:
    decision = resolve_system_native_proxy_decision(url, explicit_proxy=explicit_proxy)
    debug_log_system_native_proxy_decision(
        "python-protocol",
        decision,
        extra_fields={"requestLabel": request_label},
    )
    request_kwargs = dict(kwargs)
    forwarded_proxy = _forwarded_request_proxy(decision)
    if forwarded_proxy:
        request_kwargs["proxies"] = build_request_proxies(forwarded_proxy)
    try:
        return session.request(
            method,
            url,
            **request_kwargs,
        )
    except Exception as exc:
        message = str(exc or "").lower()
        is_tls_transport_failure = (
            "tls connect error" in message
            or "openssl_internal" in message
            or "curl: (35)" in message
            or "invalid library" in message
            or "curl: (55)" in message
            or "send failure: connection was aborted" in message
        )
        if not is_tls_transport_failure:
            raise
        print(
            "[python-protocol-service] curl transport failed, falling back to urllib "
            f"label={request_label} url={url} err={exc}"
        )
        return _session_request_via_urllib(
            session=session,
            method=method,
            url=url,
            explicit_proxy=forwarded_proxy,
            allow_redirects=bool(request_kwargs.pop("allow_redirects", True)),
            timeout=int(request_kwargs.pop("timeout", 30) or 30),
            headers=request_kwargs.pop("headers", None),
            data=request_kwargs.pop("data", None),
            json_body=request_kwargs.pop("json", None),
        )


def _session_request_via_urllib(
    *,
    session: requests.Session,
    method: str,
    url: str,
    explicit_proxy: str | None,
    allow_redirects: bool,
    timeout: int,
    headers: dict[str, str] | None,
    data: Any = None,
    json_body: Any = None,
) -> _StdlibResponse:
    request_headers = dict(headers or {})
    body: bytes | None = None
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        request_headers.setdefault("content-type", "application/json")
    elif isinstance(data, str):
        body = data.encode("utf-8")
    elif isinstance(data, bytes):
        body = data
    elif data is not None:
        body = str(data).encode("utf-8")

    cookie_jar = _resolve_urllib_cookie_jar(session)
    handlers: list[Any] = []
    if cookie_jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookie_jar))
    if explicit_proxy:
        handlers.append(urllib.request.ProxyHandler({"http": explicit_proxy, "https": explicit_proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler())

    verify_tls = bool(getattr(session, "verify", True))
    if not verify_tls:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=context))
    if not allow_redirects:
        handlers.append(_NoRedirectHandler())

    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers=request_headers,
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            body_bytes = response.read()
            _sync_urllib_cookie_jar(session, cookie_jar)
            return _StdlibResponse(
                status_code=int(getattr(response, "status", 200) or 200),
                headers=dict(response.headers.items()),
                url=str(getattr(response, "url", url) or url),
                body=body_bytes,
            )
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read()
        _sync_urllib_cookie_jar(session, cookie_jar)
        return _StdlibResponse(
            status_code=int(exc.code),
            headers=dict(exc.headers.items()),
            url=str(getattr(exc, "url", url) or url),
            body=body_bytes,
        )


def _resolve_urllib_cookie_jar(session: requests.Session) -> http.cookiejar.CookieJar | None:
    session_cookies = getattr(session, "cookies", None)
    if session_cookies is None:
        return None

    cookie_jar = getattr(session_cookies, "jar", None)
    if cookie_jar is not None and hasattr(cookie_jar, "add_cookie_header"):
        return cookie_jar
    if hasattr(session_cookies, "add_cookie_header"):
        return session_cookies

    compat_jar = http.cookiejar.CookieJar()
    try:
        compat_jar.update(session_cookies)
        return compat_jar
    except Exception:
        pass

    try:
        for cookie in session_cookies:
            name = str(getattr(cookie, "name", "") or "").strip()
            if not name:
                continue
            value = str(getattr(cookie, "value", "") or "")
            domain = str(getattr(cookie, "domain", "") or "")
            path = str(getattr(cookie, "path", "/") or "/")
            expires = getattr(cookie, "expires", None)
            if expires in ("", 0):
                expires = None
            compat_jar.set_cookie(http.cookiejar.Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=bool(domain),
                domain_initial_dot=domain.startswith("."),
                path=path,
                path_specified=True,
                secure=bool(getattr(cookie, "secure", False)),
                expires=int(expires) if expires is not None else None,
                discard=expires is None,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            ))
    except Exception:
        return None
    return compat_jar


def _sync_urllib_cookie_jar(session: requests.Session, cookie_jar: http.cookiejar.CookieJar | None) -> None:
    if cookie_jar is None:
        return
    session_cookies = getattr(session, "cookies", None)
    if session_cookies is None or not hasattr(session_cookies, "update"):
        return
    try:
        session_cookies.update(cookie_jar)
    except Exception:
        return


def _forwarded_request_proxy(decision: Any) -> str | None:
    mode = str(getattr(decision, "mode", "") or "").strip().lower()
    proxy = normalize_proxy_env_url(getattr(decision, "proxy", None))
    if mode == "explicit":
        return proxy
    return None


def _codex_cli_login_for_token(
    *,
    email: str,
    password: str,
    mailbox_ref: str,
    mailbox_session_id: str,
    first_name: str,
    last_name: str,
    birthdate: str,
    explicit_proxy: str | None,
    otp_min_mail_id: int = 0,
) -> ProtocolRegistrationResult:
    """Stage 2: 注册完成后，开新 session 走 Codex CLI OAuth 登录获取 token。

    流程：
      1. 清新 session + GET /oauth/authorize (Codex CLI, PKCE)
      2. POST /api/accounts/authorize/continue (screen_hint=login)
      3. POST /api/accounts/password/verify
      4. 如需二次 OTP → 等待验证码 → POST email-otp/validate
      5. 解析 oai-client-auth-session cookie → workspace_id
      6. POST /api/accounts/workspace/select
      7. 跟随重定向链 (consent → callback)
      8. POST /oauth/token 换取 tokens
    """
    from .oauth_flow import (
        CLIENT_ID,
        TOKEN_URL,
        generate_oauth_url,
        submit_callback_url,
    )

    impersonate = (os.environ.get("PROTOCOL_HTTP_IMPERSONATE") or "").strip() or _DEFAULT_IMPERSONATE
    verify_tls = env_flag("PROTOCOL_HTTP_VERIFY_TLS", False)

    session = requests.Session(
        impersonate=impersonate,
        timeout=30,
        verify=verify_tls,
    )
    session.headers.update({"user-agent": DEFAULT_PROTOCOL_USER_AGENT})

    try:
        device_id = str(uuid.uuid4())
        session.cookies.set("oai-did", device_id, domain=".auth.openai.com")
        session.cookies.set("oai-did", device_id, domain="auth.openai.com")

        # Step 1: GET /oauth/authorize — Codex CLI OAuth with PKCE
        oauth = generate_oauth_url(prompt="login")
        print(
            f"[python-protocol-service] Stage2 login: GET /oauth/authorize "
            f"email={email}"
        )
        login_init_resp = _session_request(
            session,
            "GET",
            oauth.auth_url,
            explicit_proxy=explicit_proxy,
            request_label="stage2-oauth-authorize",
            timeout=20,
        )
        has_login_session = bool(_get_session_cookie(
            session, "login_session",
            preferred_domains=(".openai.com", "auth.openai.com"),
        ))
        print(
            f"[python-protocol-service] Stage2 authorize init "
            f"status={login_init_resp.status_code} "
            f"login_session={'yes' if has_login_session else 'no'}"
        )

        # Step 2: POST /api/accounts/authorize/continue — 提交邮箱 (login)
        sentinel_header = _get_sentinel_header_for_signup(
            session,
            device_id=device_id,
            flow="authorize_continue",
            request_kind="repair-authorize-continue",
            explicit_proxy=explicit_proxy,
        )
        login_headers = _build_protocol_headers(
            request_kind="",
            referer=LOGIN_REFERER,
        )
        login_headers["openai-sentinel-token"] = sentinel_header
        login_continue_resp = _session_request(
            session,
            "POST",
            AUTHORIZE_CONTINUE_URL,
            explicit_proxy=explicit_proxy,
            request_label="stage2-authorize-continue",
            headers=login_headers,
            data=json.dumps({
                "username": {"value": email, "kind": "email"},
                "screen_hint": "login",
            }),
        )
        print(
            f"[python-protocol-service] Stage2 authorize-continue "
            f"status={login_continue_resp.status_code} "
            f"page_type={_extract_page_type(login_continue_resp) or '<none>'}"
        )
        if login_continue_resp.status_code != 200:
            _raise_protocol_response_error(
                login_continue_resp,
                prefix="stage2_authorize_continue",
                stage="stage_callback",
                detail="stage2_authorize_continue",
            )

        # Step 3: POST /api/accounts/password/verify
        pwd_sentinel = _get_sentinel_header_for_signup(
            session,
            device_id=device_id,
            flow="password_verify",
            request_kind="repair-password-verify",
            explicit_proxy=explicit_proxy,
        )
        pwd_headers = _build_protocol_headers(
            request_kind="",
            referer=LOGIN_PASSWORD_REFERER,
        )
        pwd_headers["openai-sentinel-token"] = pwd_sentinel
        pwd_resp = _session_request(
            session,
            "POST",
            PASSWORD_VERIFY_URL,
            explicit_proxy=explicit_proxy,
            request_label="stage2-password-verify",
            headers=pwd_headers,
            data=json.dumps({"password": password}),
        )
        pwd_page_type = _extract_page_type(pwd_resp)
        print(
            f"[python-protocol-service] Stage2 password-verify "
            f"status={pwd_resp.status_code} page_type={pwd_page_type or '<none>'}"
        )
        if pwd_resp.status_code != 200:
            _raise_protocol_response_error(
                pwd_resp,
                prefix="stage2_password_verify",
                stage="stage_callback",
                detail="stage2_password_verify",
            )

        # Step 4: 如果触发二次 OTP
        pwd_continue_url = _response_continue_url(pwd_resp)
        need_login_otp = (
            "otp" in pwd_page_type.lower()
            or "verification" in pwd_page_type.lower()
            or "email-verification" in pwd_continue_url
            or "verify" in pwd_continue_url
        )
        if need_login_otp:
            print(
                f"[python-protocol-service] Stage2 login triggered OTP, waiting for code..."
            )
            try:
                login_code = wait_openai_code(
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
                    min_mail_id=otp_min_mail_id,
                )
            except Exception as exc:
                raise _wrap_protocol_error(
                    exc,
                    stage="stage_callback",
                    detail="stage2_login_otp_wait",
                    category="otp_timeout",
                ) from exc
            login_code = str(login_code or "").strip()
            if not login_code:
                _raise_protocol_error(
                    "stage2_login_otp_timeout",
                    stage="stage_callback",
                    detail="stage2_login_otp_wait",
                    category="otp_timeout",
                )
            otp_resp = _session_request(
                session,
                "POST",
                EMAIL_OTP_VALIDATE_URL,
                explicit_proxy=explicit_proxy,
                request_label="stage2-otp-validate",
                headers=_build_protocol_headers(
                    request_kind="",
                    referer=EMAIL_VERIFICATION_REFERER,
                ),
                data=json.dumps({"code": login_code}),
            )
            print(
                f"[python-protocol-service] Stage2 login OTP validate "
                f"status={otp_resp.status_code}"
            )
            if otp_resp.status_code >= 400:
                _raise_protocol_response_error(
                    otp_resp,
                    prefix="stage2_login_otp_validate",
                    stage="stage_callback",
                    detail="stage2_login_otp_validate",
                )

        # Step 5: 解析 oai-client-auth-session cookie → workspace_id
        workspace_id = _extract_workspace_id_from_session(
            session,
            explicit_proxy=explicit_proxy,
        )
        print(f"[python-protocol-service] Stage2 resolved workspace_id={workspace_id or '<none>'}")
        if not workspace_id:
            _raise_protocol_error(
                "stage2_missing_workspace_id",
                stage="stage_callback",
                detail="stage2_workspace_parse",
                category="auth_error",
            )

        # Step 6: POST /api/accounts/workspace/select
        ws_resp = _session_request(
            session,
            "POST",
            WORKSPACE_SELECT_URL,
            explicit_proxy=explicit_proxy,
            request_label="stage2-workspace-select",
            headers=_build_protocol_headers(
                request_kind="",
                referer=CONSENT_REFERER,
                content_type="application/json",
            ),
            data=json.dumps({"workspace_id": workspace_id}),
        )
        print(
            f"[python-protocol-service] Stage2 workspace/select "
            f"status={ws_resp.status_code}"
        )
        if ws_resp.status_code != 200:
            _raise_protocol_response_error(
                ws_resp,
                prefix="stage2_workspace_select",
                stage="stage_callback",
                detail="stage2_workspace_select",
            )
        continue_url = _response_continue_url(ws_resp)

        # Step 6b: 尝试 organization/select（如果 workspace/select 返回 org 信息）
        try:
            ws_data = ws_resp.json() if ws_resp.status_code == 200 else {}
            orgs = (ws_data.get("data") or {}).get("orgs") or []
            if orgs:
                selected_org = _select_org_entry(
                    [item for item in orgs if isinstance(item, dict)]
                )
                org_id = str((selected_org or {}).get("id") or "").strip()
                if org_id:
                    org_body: dict[str, str] = {"org_id": org_id}
                    project_id = _select_project_id_from_org_entry(selected_org or {})
                    if project_id:
                        org_body["project_id"] = project_id
                    org_resp = _session_request(
                        session,
                        "POST",
                        f"{AUTH_BASE}/api/accounts/organization/select",
                        explicit_proxy=explicit_proxy,
                        request_label="stage2-org-select",
                        headers=_build_protocol_headers(
                            request_kind="",
                            referer=CONSENT_REFERER,
                            content_type="application/json",
                        ),
                        data=json.dumps(org_body),
                    )
                    org_continue = _response_continue_url(org_resp)
                    if org_continue:
                        continue_url = org_continue
        except Exception as exc:
            print(f"[python-protocol-service] Stage2 org select skipped: {exc}")

        if not continue_url:
            _raise_protocol_error(
                "stage2_missing_continue_url",
                stage="stage_callback",
                detail="stage2_workspace_select",
                category="auth_error",
            )

        # Step 7: 跟随重定向链 (consent → callback)
        print(
            f"[python-protocol-service] Stage2 following redirect chain "
            f"start_url={_format_logged_url(continue_url)}"
        )
        current_url = continue_url
        callback_url = ""
        for _ in range(15):
            redirect_resp = _session_request(
                session,
                "GET",
                current_url,
                explicit_proxy=explicit_proxy,
                request_label="stage2-redirect-chain",
                allow_redirects=False,
                timeout=15,
            )
            if redirect_resp.status_code in (301, 302, 303, 307, 308):
                next_url = urllib.parse.urljoin(
                    current_url,
                    _response_location(redirect_resp) or "",
                )
            elif redirect_resp.status_code == 200:
                # consent 页面需要 POST accept
                if "consent_challenge=" in current_url or _is_codex_consent_html(
                    url=current_url, html=_response_preview(redirect_resp, 2000),
                ):
                    consent_resp = _session_request(
                        session,
                        "POST",
                        current_url,
                        explicit_proxy=explicit_proxy,
                        request_label="stage2-consent-accept",
                        headers={
                            "content-type": "application/x-www-form-urlencoded",
                            "referer": current_url,
                        },
                        data="action=accept",
                        allow_redirects=False,
                        timeout=15,
                    )
                    next_url = (
                        urllib.parse.urljoin(
                            current_url,
                            _response_location(consent_resp) or "",
                        )
                        if consent_resp.status_code in (301, 302, 303, 307, 308)
                        else ""
                    )
                else:
                    # 尝试 meta refresh
                    meta_match = re.search(
                        r'content=["\']?\d+;\s*url=([^"\'>\s]+)',
                        _response_preview(redirect_resp, 2000),
                        re.IGNORECASE,
                    )
                    next_url = (
                        urllib.parse.urljoin(current_url, meta_match.group(1))
                        if meta_match
                        else ""
                    )
                if not next_url:
                    break
            else:
                break

            # 检查是否到达 callback URL (含 code= 和 state=)
            if "code=" in next_url and "state=" in next_url:
                callback_url = next_url
                break
            current_url = next_url
            time.sleep(0.3)

        if not callback_url:
            _raise_protocol_error(
                "stage2_callback_not_found",
                stage="stage_callback",
                detail="stage2_redirect_chain",
                category="auth_error",
            )

        # Step 8: POST /oauth/token — 用 code + code_verifier 换取 tokens
        print(
            f"[python-protocol-service] Stage2 callback captured, exchanging for tokens"
        )
        result_email, result_json = submit_callback_url(
            callback_url=callback_url,
            expected_state=oauth.state,
            code_verifier=oauth.code_verifier,
            redirect_uri=oauth.redirect_uri,
            proxy=explicit_proxy,
            mailbox_ref=mailbox_ref,
            password=password,
            first_name=first_name,
            last_name=last_name,
            birthdate=birthdate,
        )
        if not result_email:
            result_email = email
        auth = json.loads(result_json) if isinstance(result_json, str) else result_json
        print(
            f"[python-protocol-service] Stage2 token exchange successful "
            f"email={result_email} "
            f"has_access_token={bool(auth.get('access_token'))}"
        )
        return ProtocolRegistrationResult(email=result_email, auth=auth)

    finally:
        try:
            session.close()
        except Exception:
            pass


def run_protocol_registration_once(
    *,
    proxy: str | None = None,
    preallocated_email: str | None = None,
    preallocated_session_id: str | None = None,
    preallocated_mailbox_ref: str | None = None,
) -> ProtocolRegistrationResult:
    """协议注册完整流程 (Codex CLI OAuth 路径)

    Stage 1 (注册):
      1. GET /oauth/authorize (Codex CLI, PKCE, screen_hint=signup)
      2. POST /api/accounts/authorize/continue
      3. POST /api/accounts/user/register
      4. 等待 email OTP → POST /api/accounts/email-otp/validate
      5. POST /api/accounts/create_account

    Stage 2 (登录获取 Token):
      优先: _codex_cli_login_for_token() (新 session 完整 OAuth 登录)
      回退: run_protocol_repair_once() (现有修复登录路径)
    """
    from .oauth_flow import generate_oauth_url as _generate_codex_oauth_url

    explicit_proxy = normalize_proxy_env_url(proxy)
    if preallocated_email and preallocated_mailbox_ref:
        mailbox = Mailbox(
            provider="preallocated",
            email=str(preallocated_email).strip(),
            ref=str(preallocated_mailbox_ref).strip(),
            session_id=str(preallocated_session_id or "").strip(),
        )
    elif preallocated_email and preallocated_session_id:
        mailbox = Mailbox(
            provider="mail-dispatch",
            email=str(preallocated_email).strip(),
            ref=f"mail-dispatch:{str(preallocated_session_id).strip()}",
            session_id=str(preallocated_session_id).strip(),
        )
    else:
        try:
            mailbox = create_mailbox(
                default_host_id="python-protocol-service",
                prefer_raw_self_hosted_ref=True,
            )
        except Exception as exc:
            raise _wrap_protocol_error(
                exc,
                stage="stage_other",
                detail="create_mailbox",
                category="flow_error",
            ) from exc
    email = mailbox.email
    impersonate = (os.environ.get("PROTOCOL_HTTP_IMPERSONATE") or "").strip() or _DEFAULT_IMPERSONATE
    verify_tls = env_flag("PROTOCOL_HTTP_VERIFY_TLS", False)

    session = requests.Session(
        impersonate=impersonate,
        timeout=30,
        verify=verify_tls,
    )
    session_user_agent = DEFAULT_PROTOCOL_USER_AGENT
    session.headers.update({"user-agent": session_user_agent})

    password = ""
    first_name = ""
    last_name = ""
    birthdate = ""
    device_id = str(uuid.uuid4())

    try:
        # =====================================================================
        # Stage 1: 注册 (Codex CLI OAuth 路径)
        # =====================================================================

        # 设置 oai-did cookie
        session.cookies.set("oai-did", device_id, domain=".auth.openai.com")
        session.cookies.set("oai-did", device_id, domain="auth.openai.com")

        # 初始化 sentinel context (用于 PoW 回退)
        try:
            sentinel_context = _new_protocol_sentinel_context(
                session,
                explicit_proxy=explicit_proxy,
                user_agent=session_user_agent,
            )
        except Exception as exc:
            raise _wrap_protocol_error(
                exc,
                stage="stage_auth_continue",
                detail="sentinel_context_init",
                category="flow_error",
            ) from exc

        def current_stage2_auth_obj() -> dict[str, Any]:
            return {
                "email": email,
                "password": password,
                "mailbox_ref": mailbox.ref,
                "session_id": mailbox.session_id,
                "first_name": first_name,
                "last_name": last_name,
                "birthdate": birthdate,
            }

        # Step 1: GET /oauth/authorize (Codex CLI, PKCE, screen_hint=signup)
        oauth = _generate_codex_oauth_url(
            prompt="login",
            extra_params={"screen_hint": "signup"},
        )
        print(
            f"[python-protocol-service] Stage1: GET /oauth/authorize "
            f"email={email} device_id={device_id[:8]}..."
        )

        otp_min_mail_id = 0
        try:
            otp_min_mail_id = get_mailbox_latest_message_id(
                mailbox_ref=mailbox.ref,
                session_id=mailbox.session_id,
                mailcreate_base_url=MAILCREATE_BASE_URL,
                mailcreate_custom_auth=MAILCREATE_CUSTOM_AUTH,
            )
        except Exception as exc:
            print(f"[python-protocol-service] OTP baseline lookup skipped: {exc}")

        # Step 1: GET /oauth/authorize — 跟随重定向获取 login_session cookie
        try:
            response = _session_request(
                session,
                "GET",
                oauth.auth_url,
                explicit_proxy=explicit_proxy,
                request_label="oauth-authorize",
                timeout=20,
            )
        except Exception as exc:
            raise _wrap_protocol_error(
                exc,
                stage="stage_auth_continue",
                detail="oauth_authorize",
                category="auth_error",
            ) from exc
        has_login_session = bool(_get_session_cookie(
            session, "login_session",
            preferred_domains=(".openai.com", "auth.openai.com"),
        ))
        print(
            "[python-protocol-service] Stage1 authorize init "
            f"status={response.status_code} "
            f"login_session={'yes' if has_login_session else 'no'} "
            f"cookies={_cookie_debug_snapshot(session)}"
        )
        if not has_login_session:
            _raise_protocol_error(
                f"authorize_init_missing_login_session status={response.status_code} "
                f"body={_response_preview(response, 200)}",
                stage="stage_auth_continue",
                detail="oauth_authorize",
                category="auth_error",
            )

        # Step 2: POST /api/accounts/authorize/continue — 提交邮箱 (signup)
        sentinel_header = _get_sentinel_header_for_signup(
            session,
            device_id=device_id,
            flow="authorize_continue",
            request_kind="signup-authorize-continue",
            explicit_proxy=explicit_proxy,
            sentinel_context=sentinel_context,
        )
        signup_headers = _build_protocol_headers(
            request_kind="",
            referer=CREATE_ACCOUNT_REFERER,
        )
        signup_headers["openai-sentinel-token"] = sentinel_header
        signup_headers["oai-device-id"] = device_id
        print(
            "[python-protocol-service] Stage1 authorize-continue request "
            f"email_domain={email.split('@')[-1] if '@' in email else '<none>'}"
        , flush=True)

        try:
            signup_response = _session_request(
                session,
                "POST",
                AUTHORIZE_CONTINUE_URL,
                explicit_proxy=explicit_proxy,
                request_label="authorize-continue",
                headers=signup_headers,
                data=json.dumps({
                    "username": {
                        "value": email,
                        "kind": "email",
                    },
                    "screen_hint": "signup",
                }),
            )
        except Exception as exc:
            raise _wrap_protocol_error(
                exc,
                stage="stage_auth_continue",
                detail="authorize_continue",
                category="flow_error",
            ) from exc
        if signup_response.status_code != 200:
            _raise_protocol_response_error(
                signup_response,
                prefix="authorize_continue",
                stage="stage_auth_continue",
                detail="authorize_continue",
                default_category="flow_error",
            )

        page_type = _extract_page_type(signup_response)
        print(
            "[python-protocol-service] Stage1 authorize-continue response "
            f"status={signup_response.status_code} page_type={page_type or '<none>'} "
            f"body={_response_preview(signup_response, 220)}"
        )

        if _is_phone_wall_page_type(page_type):
            _raise_protocol_error(
                f"phone_wall context=signup page_type={page_type}",
                stage="stage_create_account",
                detail="authorize_continue_page",
                category="blocked",
            )
        is_existing_account = page_type in ("email_otp_send", "email_otp_verification")

        if is_existing_account and page_type == "email_otp_send":
            try:
                _send_email_otp(
                    session,
                    explicit_proxy=explicit_proxy,
                    header_builder=sentinel_context,
                )
            except Exception as exc:
                raise _wrap_protocol_error(
                    exc,
                    stage="stage_send_otp",
                    detail="email_otp_send",
                    category="flow_error",
                ) from exc
        elif is_existing_account:
            _request_email_otp_resend_on_verification_page(
                session,
                page_type=page_type,
                explicit_proxy=explicit_proxy,
                header_builder=sentinel_context,
                context_label="signup-existing-account",
            )

        # Step 3: POST /api/accounts/user/register — 注册用户 (新账号)
        if not is_existing_account:
            password = generate_pwd()
            register_sentinel = _get_sentinel_header_for_signup(
                session,
                device_id=device_id,
                flow="username_password_create",
                request_kind="signup-user-register",
                explicit_proxy=explicit_proxy,
                sentinel_context=sentinel_context,
            )
            register_headers = _build_protocol_headers(
                request_kind="",
                referer=CREATE_ACCOUNT_PASSWORD_REFERER,
            )
            register_headers["openai-sentinel-token"] = register_sentinel
            register_headers["oai-device-id"] = device_id
            print(
                "[python-protocol-service] Stage1 user-register request "
                f"email_domain={email.split('@')[-1] if '@' in email else '<none>'}"
            , flush=True)

            try:
                register_response = _session_request(
                    session,
                    "POST",
                    USER_REGISTER_URL,
                    explicit_proxy=explicit_proxy,
                    request_label="user-register",
                    headers=register_headers,
                    data=json.dumps({
                        "password": password,
                        "username": email,
                    }),
                )
            except Exception as exc:
                raise _wrap_protocol_error(
                    exc,
                    stage="stage_create_account",
                    detail="user_register",
                    category="flow_error",
                ) from exc
            register_page_type = _extract_page_type(register_response)
            print(
                "[python-protocol-service] Stage1 user-register response "
                f"status={register_response.status_code} "
                f"page_type={register_page_type or '<none>'} "
                f"body={_response_preview(register_response, 220)}"
            )
            if register_response.status_code != 200:
                _raise_protocol_response_error(
                    register_response,
                    prefix="user_register",
                    stage="stage_create_account",
                    detail="user_register",
                    default_category="flow_error",
                )
            try:
                _raise_if_phone_wall_response(register_response, context="user_register")
            except Exception as exc:
                raise _wrap_protocol_error(
                    exc,
                    stage="stage_create_account",
                    detail="user_register_phone_wall",
                    category="blocked",
                ) from exc

        # Step 3.5: 触发 OTP 邮件发送 (follow continue_url)
        try:
            _send_email_otp(
                session,
                explicit_proxy=explicit_proxy,
                header_builder=sentinel_context,
            )
        except Exception as exc:
            print(
                f"[python-protocol-service] email OTP send request failed "
                f"(non-fatal, server may auto-send): {exc}"
            )

        # Step 4: 等待 email OTP
        try:
            code = wait_openai_code(
                mailbox_ref=mailbox.ref,
                session_id=mailbox.session_id,
                mailcreate_base_url=MAILCREATE_BASE_URL,
                mailcreate_custom_auth=MAILCREATE_CUSTOM_AUTH,
                timeout_seconds=max(
                    60,
                    int(
                        (os.environ.get("OTP_TIMEOUT_SECONDS") or str(DEFAULT_OTP_TIMEOUT_SECONDS)).strip()
                        or str(DEFAULT_OTP_TIMEOUT_SECONDS)
                    ),
                ),
                min_mail_id=otp_min_mail_id,
            )
        except Exception as exc:
            raise _wrap_protocol_error(
                exc,
                stage="stage_otp_validate",
                detail="email_otp_wait",
                category="otp_timeout",
            ) from exc
        code = str(code or "").strip()
        if not code:
            _raise_protocol_error(
                "otp_timeout",
                stage="stage_otp_validate",
                detail="email_otp_wait",
                category="otp_timeout",
            )

        # Step 5: POST /api/accounts/email-otp/validate
        otp_sentinel = _get_sentinel_header_for_signup(
            session,
            device_id=device_id,
            flow="email_otp_validate",
            request_kind="otp-validate",
            explicit_proxy=explicit_proxy,
            sentinel_context=sentinel_context,
        )
        otp_headers = _build_protocol_headers(
            request_kind="",
            referer=EMAIL_VERIFICATION_REFERER,
        )
        otp_headers["openai-sentinel-token"] = otp_sentinel
        otp_headers["oai-device-id"] = device_id
        try:
            otp_validate_response = _session_request(
                session,
                "POST",
                EMAIL_OTP_VALIDATE_URL,
                explicit_proxy=explicit_proxy,
                request_label="otp-validate",
                headers=otp_headers,
                data=json.dumps({"code": code}),
            )
        except Exception as exc:
            raise _wrap_protocol_error(
                exc,
                stage="stage_otp_validate",
                detail="email_otp_validate",
                category="flow_error",
            ) from exc
        print(
            "[python-protocol-service] Stage1 otp-validate response "
            f"status={otp_validate_response.status_code} "
            f"body={_response_preview(otp_validate_response, 220)}"
        )
        if otp_validate_response.status_code >= 400:
            body = _response_preview(otp_validate_response)
            body_lower = body.lower()
            if "account_deactivated" in body_lower or "deleted or deactivated" in body_lower:
                _raise_protocol_error(
                    "account_deactivated",
                    stage="stage_otp_validate",
                    detail="email_otp_validate",
                    category="auth_error",
                )
            _raise_protocol_error(
                f"otp_validate status={otp_validate_response.status_code} body={body}",
                stage="stage_otp_validate",
                detail="email_otp_validate",
                category="flow_error",
            )

        # Step 6: POST /api/accounts/create_account — 提交姓名+生日 (新账号)
        if not is_existing_account:
            first_name, last_name = generate_name()
            birthdate = _random_birthdate()
            create_sentinel = _get_sentinel_header_for_signup(
                session,
                device_id=device_id,
                flow="oauth_create_account",
                request_kind="signup-create-account",
                explicit_proxy=explicit_proxy,
                sentinel_context=sentinel_context,
            )
            create_headers = _build_protocol_headers(
                request_kind="",
                referer=ABOUT_YOU_REFERER,
            )
            create_headers["openai-sentinel-token"] = create_sentinel
            create_headers["oai-device-id"] = device_id
            create_headers["origin"] = AUTH_BASE
            print(
                "[python-protocol-service] Stage1 create-account request "
                f"name={first_name} {last_name} birthdate={birthdate}"
            , flush=True)

            try:
                create_account_response = _session_request(
                    session,
                    "POST",
                    CREATE_ACCOUNT_URL,
                    explicit_proxy=explicit_proxy,
                    request_label="create-account",
                    headers=create_headers,
                    data=json.dumps({
                        "name": f"{first_name} {last_name}",
                        "birthdate": birthdate,
                    }),
                )
            except Exception as exc:
                raise _wrap_protocol_error(
                    exc,
                    stage="stage_create_account",
                    detail="create_account",
                    category="flow_error",
                ) from exc
            print(
                "[python-protocol-service] Stage1 create-account response "
                f"status={create_account_response.status_code} "
                f"body={_response_preview(create_account_response, 220)}"
            , flush=True)
            if create_account_response.status_code != 200:
                _raise_protocol_error(
                    f"create_account status={create_account_response.status_code} "
                    f"body={_response_preview(create_account_response)}",
                    stage="stage_create_account",
                    detail="create_account",
                    category="flow_error",
                )
            try:
                _raise_if_phone_wall_response(create_account_response, context="create_account")
            except Exception as exc:
                raise _wrap_protocol_error(
                    exc,
                    stage="stage_create_account",
                    detail="create_account_phone_wall",
                    category="blocked",
                ) from exc

        # =====================================================================
        # Stage 2: 登录获取 Codex Token
        # 优先: 新 session Codex CLI OAuth 登录
        # 回退: run_protocol_repair_once
        # =====================================================================
        print(
            "[python-protocol-service] Stage 1 completed, proceeding to Stage 2 "
            f"email={email} existing_account={is_existing_account}"
        )

        # 关闭 Stage 1 session
        try:
            session.close()
        except Exception:
            pass

        # 策略1: 新 session Codex CLI OAuth 登录
        if password:
            print(
                "[python-protocol-service] Stage2: attempting Codex CLI OAuth login "
                f"email={email}"
            )
            try:
                return _codex_cli_login_for_token(
                    email=email,
                    password=password,
                    mailbox_ref=mailbox.ref,
                    mailbox_session_id=mailbox.session_id,
                    first_name=first_name,
                    last_name=last_name,
                    birthdate=birthdate,
                    explicit_proxy=explicit_proxy,
                    otp_min_mail_id=otp_min_mail_id,
                )
            except Exception as stage2_exc:
                print(
                    "[python-protocol-service] Stage2 Codex CLI login failed, "
                    f"falling back to repair email={email} err={stage2_exc}"
                )
                # 策略2: 回退到 repair
                try:
                    return run_protocol_repair_once(
                        auth_obj=current_stage2_auth_obj(),
                        proxy=explicit_proxy,
                    )
                except Exception as fallback_exc:
                    raise _wrap_protocol_error(
                        fallback_exc,
                        stage="stage_callback",
                        detail="repair_after_codex_login",
                        category="auth_error",
                    ) from fallback_exc
        else:
            # 已有账号没有密码，无法做 Stage 2
            _raise_protocol_error(
                "existing_account_no_password",
                stage="stage_callback",
                detail="stage2_no_password",
                category="auth_error",
            )

    except ProtocolRuntimeError:
        raise
    except Exception as exc:
        raise _wrap_protocol_error(
            exc,
            stage="stage_other",
            detail="registration_unhandled",
            category="flow_error",
        ) from exc



def run_protocol_repair_once(
    *,
    auth_obj: dict[str, Any],
    proxy: str | None = None,
    existing_session: requests.Session | None = None,
    existing_sentinel_context: ProtocolSentinelContext | None = None,
    force_email_otp_resend_on_verification: bool = False,
) -> ProtocolRegistrationResult:
    if not isinstance(auth_obj, dict):
        _raise_protocol_error(
            "protocol repair requires auth object",
            stage="stage_other",
            detail="invalid_auth_object",
            category="flow_error",
        )

    email = str(auth_obj.get("email") or "").strip()
    password = str(auth_obj.get("password") or "").strip()
    mailbox_ref = str(auth_obj.get("mailbox_ref") or "").strip()
    session_id = str(auth_obj.get("session_id") or "").strip()

    if not email:
        _raise_protocol_error(
            "missing_email",
            stage="stage_other",
            detail="missing_email",
            category="flow_error",
        )
    if not mailbox_ref and session_id:
        mailbox_ref = f"mail-dispatch:{session_id}"

    explicit_proxy = normalize_proxy_env_url(proxy)
    oauth = generate_oauth_url()
    owns_session = existing_session is None
    if not existing_session:
        impersonate = (os.environ.get("PROTOCOL_HTTP_IMPERSONATE") or "").strip() or _DEFAULT_IMPERSONATE
        verify_tls = env_flag("PROTOCOL_HTTP_VERIFY_TLS", False)

        session = requests.Session(
            impersonate=impersonate,
            timeout=30,
            verify=verify_tls,
        )
        session_user_agent = DEFAULT_PROTOCOL_USER_AGENT
        session.headers.update({"user-agent": session_user_agent})
        try:
            sentinel_context = _new_protocol_sentinel_context(
                session,
                explicit_proxy=explicit_proxy,
                user_agent=session_user_agent,
            )
        except Exception as exc:
            raise _wrap_protocol_error(
                exc,
                stage="stage_auth_continue",
                detail="sentinel_context_init",
                category="flow_error",
            ) from exc
    else:
        session = existing_session
        sentinel_context = existing_sentinel_context
        if sentinel_context is None:
            session_user_agent = str(
                session.headers.get("user-agent")
                or session.headers.get("User-Agent")
                or DEFAULT_PROTOCOL_USER_AGENT
            ).strip()
            try:
                sentinel_context = _new_protocol_sentinel_context(
                    session,
                    explicit_proxy=explicit_proxy,
                    user_agent=session_user_agent,
                )
            except Exception as exc:
                raise _wrap_protocol_error(
                    exc,
                    stage="stage_auth_continue",
                    detail="sentinel_context_init",
                    category="flow_error",
                ) from exc

    try:
        otp_min_mail_id = 0
        try:
            otp_min_mail_id = get_mailbox_latest_message_id(
                mailbox_ref=mailbox_ref,
                session_id=session_id,
                mailcreate_base_url=MAILCREATE_BASE_URL,
                mailcreate_custom_auth=MAILCREATE_CUSTOM_AUTH,
            )
        except Exception as exc:
            print(f"[python-protocol-service] OTP baseline lookup skipped: {exc}")

        try:
            response = _session_request(
                session,
                "GET",
                oauth.auth_url,
                explicit_proxy=explicit_proxy,
                request_label="oauth-authorize-repair",
                timeout=20,
            )
        except Exception as exc:
            raise _wrap_protocol_error(
                exc,
                stage="stage_auth_continue",
                detail="oauth_authorize_repair",
                category="auth_error",
            ) from exc
        did = _get_session_cookie(
            session,
            "oai-did",
            preferred_domains=(".openai.com", "auth.openai.com", ".chatgpt.com", "chatgpt.com"),
        )
        browser_bootstrap_result = None
        if _response_has_cloudflare_challenge(response) or not did:
            retry_reason = (
                "oauth_authorize_repair_challenge"
                if _response_has_cloudflare_challenge(response)
                else "oauth_authorize_repair_missing_did"
            )
            sentinel_context, browser_bootstrap_result = _maybe_prime_protocol_auth_session_with_browser(
                session,
                sentinel_context=sentinel_context,
                explicit_proxy=explicit_proxy,
                reason=retry_reason,
            )
            if browser_bootstrap_result is not None:
                did = browser_bootstrap_result.did or _get_session_cookie(
                    session,
                    "oai-did",
                    preferred_domains=(".openai.com", "auth.openai.com", ".chatgpt.com", "chatgpt.com"),
                )
        if _response_has_cloudflare_challenge(response) and browser_bootstrap_result is None:
            _raise_protocol_response_error(
                response,
                prefix="oauth_authorize_repair_challenge",
                stage="stage_auth_continue",
                detail="oauth_authorize_repair",
                default_category="blocked",
            )
        if not did:
            _raise_protocol_response_error(
                response,
                prefix="authorize_init_missing_did",
                stage="stage_auth_continue",
                detail="oauth_authorize_repair",
                default_category="auth_error",
            )

        req_headers = _build_protocol_headers(
            request_kind="repair-authorize-continue",
            referer=LOGIN_REFERER,
            sentinel_context=sentinel_context,
        )

        try:
            signup_response = _session_request(
                session,
                "POST",
                AUTHORIZE_CONTINUE_URL,
                explicit_proxy=explicit_proxy,
                request_label="authorize-continue-repair",
                headers=req_headers,
                data=json.dumps({
                    "username": {
                        "value": email,
                        "kind": "email",
                    },
                    "screen_hint": "login",
                }),
            )
        except Exception as exc:
            raise _wrap_protocol_error(
                exc,
                stage="stage_auth_continue",
                detail="authorize_continue_repair",
                category="flow_error",
            ) from exc
        if signup_response.status_code != 200 and (
            _response_error_code(signup_response).lower() == "invalid_state"
            or _response_has_cloudflare_challenge(signup_response)
        ):
            retry_reason = (
                "authorize_continue_repair_challenge"
                if _response_has_cloudflare_challenge(signup_response)
                else "authorize_continue_repair_invalid_state"
            )
            sentinel_context, browser_bootstrap_result = _maybe_prime_protocol_auth_session_with_browser(
                session,
                sentinel_context=sentinel_context,
                explicit_proxy=explicit_proxy,
                reason=retry_reason,
            )
            if browser_bootstrap_result is not None:
                req_headers = _build_protocol_headers(
                    request_kind="repair-authorize-continue",
                    referer=LOGIN_REFERER,
                    sentinel_context=sentinel_context,
                )
                signup_response = _session_request(
                    session,
                    "POST",
                    AUTHORIZE_CONTINUE_URL,
                    explicit_proxy=explicit_proxy,
                    request_label="authorize-continue-repair",
                    headers=req_headers,
                    data=json.dumps({
                        "username": {
                            "value": email,
                            "kind": "email",
                        },
                        "screen_hint": "login",
                    }),
                )
        if signup_response.status_code != 200:
            _raise_protocol_response_error(
                signup_response,
                prefix="authorize_continue",
                stage="stage_auth_continue",
                detail="authorize_continue_repair",
                default_category="flow_error",
            )

        try:
            oauth_entry_response, page_type, oauth_entry_referer = _resolve_repair_oauth_entry(
                session,
                signup_response=signup_response,
                password=password,
                mailbox_ref=mailbox_ref,
                explicit_proxy=explicit_proxy,
                header_builder=sentinel_context,
            )
        except ProtocolRuntimeError:
            raise
        except Exception as exc:
            raise _wrap_protocol_error(
                exc,
                stage="stage_create_account",
                detail="password_verify",
                category="auth_error",
            ) from exc
        if page_type == "email_otp_send":
            if not mailbox_ref:
                _raise_protocol_error(
                    "missing_mailbox_ref",
                    stage="stage_send_otp",
                    detail="missing_mailbox_ref",
                    category="flow_error",
                )
            try:
                _send_email_otp(
                    session,
                    explicit_proxy=explicit_proxy,
                    header_builder=sentinel_context,
                )
            except Exception as exc:
                raise _wrap_protocol_error(
                    exc,
                    stage="stage_send_otp",
                    detail="email_otp_send",
                    category="flow_error",
                ) from exc
            page_type = "email_otp_verification"
        elif page_type == "email_otp_verification":
            print("[python-protocol-service] OTP input page already active")
            if force_email_otp_resend_on_verification:
                _request_email_otp_resend_on_verification_page(
                    session,
                    page_type=page_type,
                    explicit_proxy=explicit_proxy,
                    header_builder=sentinel_context,
                    context_label="repair-login-forced",
                )
            else:
                print(
                    "[python-protocol-service] skipping proactive OTP resend "
                    "context=repair-login reason=avoid_invalidating_auto_sent_code"
                )

        otp_validate_response: Any | None = None
        if page_type == "email_otp_verification":
            if not mailbox_ref:
                _raise_protocol_error(
                    "missing_mailbox_ref",
                    stage="stage_otp_validate",
                    detail="missing_mailbox_ref",
                    category="flow_error",
                )
            print(
                "[python-protocol-service] waiting for OTP "
                f"mailbox_ref={mailbox_ref} session_id={session_id or '<missing>'}"
            )

            try:
                code = wait_openai_code(
                    mailbox_ref=mailbox_ref,
                    session_id=session_id,
                    mailcreate_base_url=MAILCREATE_BASE_URL,
                    mailcreate_custom_auth=MAILCREATE_CUSTOM_AUTH,
                    timeout_seconds=max(
                        60,
                        int(
                            (os.environ.get("OTP_TIMEOUT_SECONDS") or str(DEFAULT_OTP_TIMEOUT_SECONDS)).strip()
                            or str(DEFAULT_OTP_TIMEOUT_SECONDS)
                        ),
                    ),
                    min_mail_id=otp_min_mail_id,
                )
            except Exception as exc:
                raise _wrap_protocol_error(
                    exc,
                    stage="stage_otp_validate",
                    detail="email_otp_wait",
                    category="otp_timeout",
                ) from exc
            code = str(code or "").strip()
            if not code:
                _raise_protocol_error(
                    "otp_timeout",
                    stage="stage_otp_validate",
                    detail="email_otp_wait",
                    category="otp_timeout",
                )

            try:
                otp_validate_response = _session_request(
                    session,
                    "POST",
                    EMAIL_OTP_VALIDATE_URL,
                    explicit_proxy=explicit_proxy,
                    request_label="otp-validate-repair",
                    headers=_build_protocol_headers(
                        request_kind="otp-validate",
                        referer=EMAIL_VERIFICATION_REFERER,
                        sentinel_context=sentinel_context,
                    ),
                    data=json.dumps({"code": code}),
                )
            except Exception as exc:
                raise _wrap_protocol_error(
                    exc,
                    stage="stage_otp_validate",
                    detail="email_otp_validate",
                    category="flow_error",
                ) from exc
            if otp_validate_response.status_code >= 400:
                body = _response_preview(otp_validate_response)
                body_lower = body.lower()
                if "account_deactivated" in body_lower or "deleted or deactivated" in body_lower:
                    _raise_protocol_error(
                        "account_deactivated",
                        stage="stage_otp_validate",
                        detail="email_otp_validate",
                        category="auth_error",
                    )
                if "invalid" in body.lower() or "incorrect" in body.lower():
                    _raise_protocol_error(
                        f"otp_incorrect body={body}",
                        stage="stage_otp_validate",
                        detail="email_otp_validate",
                        category="flow_error",
                    )
                _raise_protocol_error(
                    f"otp_validate status={otp_validate_response.status_code} body={body}",
                    stage="stage_otp_validate",
                    detail="email_otp_validate",
                    category="flow_error",
                )
            print(
                "[python-protocol-service] OTP accepted "
                f"status={otp_validate_response.status_code} "
                f"url={_response_url(otp_validate_response) or '<none>'} "
                f"page_type={_extract_page_type(otp_validate_response) or 'unknown'} "
                f"body={_response_preview(otp_validate_response, 800)}"
            )
            try:
                _raise_if_phone_wall_response(otp_validate_response, context="repair_otp_validate")
            except Exception as exc:
                raise _wrap_protocol_error(
                    exc,
                    stage="stage_otp_validate",
                    detail="repair_email_otp_validate_phone_wall",
                    category="blocked",
                ) from exc
            oauth_entry_response = otp_validate_response
            oauth_entry_referer = EMAIL_VERIFICATION_REFERER
        elif page_type == "sign_in_with_chatgpt_codex_consent":
            print("[python-protocol-service] consent page already active after password verify")
        elif page_type == "workspace":
            print("[python-protocol-service] workspace page already active after password verify")
        else:
            _raise_protocol_error(
                f"unsupported_protocol_repair_page_type page_type={page_type or 'unknown'}",
                stage="stage_create_account",
                detail="repair_page_type",
                category="flow_error",
            )

        first_name = str(auth_obj.get("first_name") or "")
        last_name = str(auth_obj.get("last_name") or "")
        birthdate = str(auth_obj.get("birthdate") or "")
        try:
            return _continue_authenticated_codex_oauth(
                session=session,
                oauth=oauth,
                explicit_proxy=explicit_proxy,
                default_email=email,
                mailbox_ref=mailbox_ref,
                password=password,
                first_name=first_name,
                last_name=last_name,
                birthdate=birthdate,
                request_label="oauth-authorize-repair-post-otp",
                prior_response=oauth_entry_response,
                prior_response_referer=oauth_entry_referer,
            )
        except Exception as exc:
            print(
                "[python-protocol-service] authenticated oauth continuation failed, "
                "falling back to workspace exchange "
                f"email={email} err={exc}"
            )
            try:
                return _exchange_authenticated_session_for_codex_result(
                    session=session,
                    oauth=oauth,
                    explicit_proxy=explicit_proxy,
                    default_email=email,
                    mailbox_ref=mailbox_ref,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    birthdate=birthdate,
                    workspace_request_label="workspace-select-repair",
                    header_builder=sentinel_context,
                )
            except Exception as fallback_exc:
                raise _wrap_protocol_error(
                    fallback_exc,
                    stage="stage_workspace",
                    detail="workspace_exchange_repair",
                    category="auth_error",
                ) from fallback_exc
    except ProtocolRuntimeError:
        raise
    except Exception as exc:
        raise _wrap_protocol_error(
            exc,
            stage="stage_other",
            detail="repair_unhandled",
            category="flow_error",
        ) from exc
    finally:
        if owns_session:
            try:
                session.close()
            except Exception:
                pass

