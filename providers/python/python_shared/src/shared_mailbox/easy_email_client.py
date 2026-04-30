from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

DEFAULT_OTP_POLL_INTERVAL_SECONDS = 4
DEFAULT_MAIL_SERVICE_READY_TIMEOUT_SECONDS = 90
DEFAULT_MAIL_SERVICE_READY_PROBE_INTERVAL_SECONDS = 2
DEFAULT_MAIL_SERVICE_REQUEST_ATTEMPTS = 3
OPENAI_OTP_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
OPENAI_OTP_CONTEXT_RE = re.compile(
    r"(?:verification\s*code|verify\s*code|security\s*code|one[-\s]*time\s*(?:pass)?code|login\s*code|sign[\s-]*in\s*code|confirmation\s*code|email\s*code|otp|passcode|验证码|校验码|动态码|动态密码|口令|代码为|代码是|enter\s+this\s+temporary\s+verification\s+code)[^0-9]{0,80}(\d{6})(?!\d)",
    re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.IGNORECASE)



@dataclass(frozen=True)
class Mailbox:
    provider: str
    email: str
    ref: str
    session_id: str


def _mail_service_base_url() -> str:
    value = (os.environ.get("MAILBOX_SERVICE_BASE_URL") or "").strip().rstrip("/")
    if not value:
        raise RuntimeError("MAILBOX_SERVICE_BASE_URL is required")
    return value


def _mail_service_headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    api_key = (os.environ.get("MAILBOX_SERVICE_API_KEY") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _mail_service_ready_timeout_seconds() -> int:
    raw = str(
        os.environ.get("MAILBOX_SERVICE_READY_TIMEOUT_SECONDS")
        or DEFAULT_MAIL_SERVICE_READY_TIMEOUT_SECONDS
    ).strip()
    try:
        return max(5, int(raw))
    except Exception:
        return DEFAULT_MAIL_SERVICE_READY_TIMEOUT_SECONDS


def _mail_service_ready_probe_interval_seconds() -> int:
    raw = str(
        os.environ.get("MAILBOX_SERVICE_READY_PROBE_INTERVAL_SECONDS")
        or DEFAULT_MAIL_SERVICE_READY_PROBE_INTERVAL_SECONDS
    ).strip()
    try:
        return max(1, int(raw))
    except Exception:
        return DEFAULT_MAIL_SERVICE_READY_PROBE_INTERVAL_SECONDS


def _mail_service_request_attempts() -> int:
    raw = str(
        os.environ.get("MAILBOX_SERVICE_REQUEST_ATTEMPTS")
        or DEFAULT_MAIL_SERVICE_REQUEST_ATTEMPTS
    ).strip()
    try:
        return max(1, int(raw))
    except Exception:
        return DEFAULT_MAIL_SERVICE_REQUEST_ATTEMPTS


def _build_opener() -> urllib.request.OpenerDirector:
    """Build an opener that bypasses system proxy for local/internal mail-service URLs."""
    base_url = _mail_service_base_url()
    parsed = urllib.parse.urlparse(base_url)
    host = parsed.hostname or ""
    should_bypass_proxy = host in ("127.0.0.1", "localhost", "::1", "0.0.0.0", "easy-email-service")
    if not should_bypass_proxy and host:
        try:
            ip = ipaddress.ip_address(host)
            should_bypass_proxy = bool(ip.is_loopback or ip.is_private or ip.is_link_local)
        except ValueError:
            should_bypass_proxy = False
    if should_bypass_proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _is_plain_route_not_found(body: str) -> bool:
    normalized = str(body or "").strip().lower()
    return normalized in {"404 page not found", "not found"}


def _is_transient_mail_service_http_body(status_code: int, body: str) -> bool:
    normalized = str(body or "").strip().lower()
    if int(status_code or 0) in (429, 500, 502, 503, 504):
        return any(
            token in normalized
            for token in (
                "no available moemail credentials for generate",
                "maximum mailbox",
                "max mailbox",
                "mailbox count limit",
                "最大邮箱数量限制",
                "too many requests",
                "rate limit",
                "quota",
            )
        )
    return False


def _is_transient_mail_service_error(exc: Exception, *, path: str) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        if exc.code in (502, 503, 504):
            return True
        if _is_transient_mail_service_http_body(exc.code, body):
            return True
        if exc.code == 404 and path.startswith("/mail/") and _is_plain_route_not_found(body):
            return True
        return False
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (ConnectionRefusedError, TimeoutError, socket.timeout)):
            return True
        return any(
            token in str(reason or "").lower()
            for token in ("connection refused", "timed out", "actively refused")
        )
    return isinstance(exc, (ConnectionRefusedError, TimeoutError, socket.timeout))


def _mail_service_request(
    *,
    method: str,
    path: str,
    payload: dict | None = None,
    timeout_seconds: int = 30,
    attempts: int | None = None,
) -> dict:
    request_attempts = attempts or _mail_service_request_attempts()
    last_error: Exception | None = None
    for attempt_index in range(1, request_attempts + 1):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            _mail_service_base_url() + path,
            data=data,
            headers=_mail_service_headers(),
            method=method,
        )
        try:
            with _build_opener().open(req, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            wrapped = RuntimeError(f"mail service {method} {path} failed: HTTP {exc.code}: {body[:200]}")
            if (
                attempt_index < request_attempts
                and (
                    _is_transient_mail_service_error(exc, path=path)
                    or _is_transient_mail_service_http_body(exc.code, body)
                )
            ):
                last_error = wrapped
                time.sleep(min(5, attempt_index))
                continue
            raise wrapped from exc
        except Exception as exc:
            if attempt_index < request_attempts and _is_transient_mail_service_error(exc, path=path):
                last_error = exc
                time.sleep(min(5, attempt_index))
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"mail service {method} {path} failed without a concrete error")


def _post_json(path: str, payload: dict) -> dict:
    return _mail_service_request(method="POST", path=path, payload=payload)


def _get_json(path: str) -> dict:
    return _mail_service_request(method="GET", path=path)


def _wait_mail_service_ready() -> None:
    deadline = time.time() + _mail_service_ready_timeout_seconds()
    interval_seconds = _mail_service_ready_probe_interval_seconds()
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            _mail_service_request(
                method="GET",
                path="/mail/catalog",
                timeout_seconds=10,
                attempts=1,
            )
            return
        except Exception as exc:
            last_error = exc
            time.sleep(interval_seconds)
    if last_error is not None:
        raise RuntimeError(f"mail service not ready after wait: {last_error}") from last_error
    raise RuntimeError("mail service not ready after wait")


def _probe_mail_service() -> str:
    try:
        req = urllib.request.Request(
            _mail_service_base_url() + "/mail/snapshot",
            headers=_mail_service_headers(),
            method="GET",
        )
        with _build_opener().open(req, timeout=10) as response:
            return f"ok:{response.status}"
    except urllib.error.HTTPError as exc:
        return f"http_error:{exc.code}"
    except Exception as exc:
        return f"error:{type(exc).__name__}:{exc}"


def _parse_mail_timestamp(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except Exception:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _mail_dispatch_code_marker(code_obj: dict) -> int:
    if not isinstance(code_obj, dict):
        return 0
    return max(
        _parse_mail_timestamp(str(code_obj.get("receivedAt") or "")),
        _parse_mail_timestamp(str(code_obj.get("observedAt") or "")),
    )


def _extract_six_digit_openai_code(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if OPENAI_OTP_RE.fullmatch(text):
        return text
    match = OPENAI_OTP_RE.search(text)
    return match.group(1) if match else ""


def _select_openai_verification_code(code_obj: dict | None) -> str:
    if not isinstance(code_obj, dict):
        return ""
    primary = _extract_six_digit_openai_code(code_obj.get("code"))
    if primary:
        return primary
    candidates = code_obj.get("candidates")
    if isinstance(candidates, list):
        for item in candidates:
            candidate = _extract_six_digit_openai_code(item)
            if candidate:
                return candidate
    return ""


def _extract_openai_code_from_message(message: dict | None) -> str:
    if not isinstance(message, dict):
        return ""
    extracted = _extract_six_digit_openai_code(message.get("extractedCode"))
    if extracted:
        return extracted
    candidates = message.get("extractedCandidates")
    if isinstance(candidates, list):
        for item in candidates:
            candidate = _extract_six_digit_openai_code(item)
            if candidate:
                return candidate
    for key in ("subject", "textBody", "htmlBody"):
        text = str(message.get(key) or "")
        if key == "htmlBody":
            text = HTML_TAG_RE.sub(" ", text)
        text = URL_RE.sub(" ", text)
        text = EMAIL_RE.sub(" ", text)
        text = " ".join(text.split())
        contextual = OPENAI_OTP_CONTEXT_RE.search(text)
        if contextual:
            return contextual.group(1)
    for key in ("subject", "textBody", "htmlBody"):
        text = str(message.get(key) or "")
        if key == "htmlBody":
            text = HTML_TAG_RE.sub(" ", text)
        text = URL_RE.sub(" ", text)
        text = EMAIL_RE.sub(" ", text)
        candidate = _extract_six_digit_openai_code(text)
        if candidate:
            return candidate
    return ""


def _snapshot_session_openai_code(*, session_id: str, min_mail_id: int) -> tuple[str, int]:
    response = _get_json("/mail/snapshot")
    snapshot = response.get("snapshot") if isinstance(response.get("snapshot"), dict) else response.get("result")
    root = snapshot if isinstance(snapshot, dict) else response
    messages = root.get("messages") if isinstance(root, dict) else None
    if not isinstance(messages, list):
        return "", 0
    best_code = ""
    best_marker = 0
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("sessionId") or "").strip() != session_id:
            continue
        marker = max(
            _parse_mail_timestamp(str(item.get("observedAt") or "")),
            _parse_mail_timestamp(str(item.get("receivedAt") or "")),
        )
        if int(min_mail_id or 0) > 0 and marker <= int(min_mail_id or 0):
            continue
        code = _extract_openai_code_from_message(item)
        if not code:
            continue
        if marker >= best_marker:
            best_code = code
            best_marker = marker
    return best_code, best_marker


def _normalize_provider(provider: str) -> str:
    p = (provider or os.environ.get("MAILBOX_PROVIDER") or "mailtm").strip().lower()
    if p in ("self", "local", "mailcreate", "self-hosted"):
        return "self-hosted"
    if p in ("gpt", "gptmail"):
        return "gptmail"
    if p in ("duck", "duckmail"):
        return "duckmail"
    if p in ("tempmail-lol", "tempmail.lol", "tempmaillol"):
        return "tempmail-lol"
    if p in ("mailtm", "mail.tm"):
        return "mailtm"
    if p in ("im215", "215.im", "215im", "yyds"):
        return "im215"
    if p in ("moemail", "moe"):
        return "moemail"
    if p in ("guerrillamail", "guerrilla"):
        return "guerrillamail"
    return p  # pass through unknown providers instead of defaulting


def _resolve_mailbox_strategy_payload() -> dict:
    raw = (
        os.environ.get("MAILBOX_STRATEGY_MODE_JSON")
        or os.environ.get("REGISTER_INBOX_STRATEGY_MODE_JSON")
        or ""
    ).strip()
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
    except Exception:
        return {}

    payload: dict[str, object] = {}
    mode_id = str(parsed.get("modeId") or "").strip()
    if mode_id:
        payload["providerStrategyModeId"] = mode_id

    provider_selections = parsed.get("providerSelections")
    if isinstance(provider_selections, list):
        normalized: list[str] = []
        for item in provider_selections:
            value = str(item or "").strip().lower()
            if value in ("self", "local", "self-hosted"):
                normalized.append("self-hosted")
            elif value in ("gpt", "gptmail"):
                normalized.append("gptmail")
            elif value in ("mail-tm", "mailtm", "mail.tm"):
                normalized.append("mailtm")
            elif value in ("duck", "duckmail"):
                normalized.append("duckmail")
            elif value in ("tempmail-lol", "tempmail.lol", "tempmaillol"):
                normalized.append("tempmail-lol")
        if normalized:
            payload["providerGroupSelections"] = normalized

    return payload


def _mailbox_host_id(default_host_id: str) -> str:
    return (os.environ.get("MAILBOX_HOST_ID") or default_host_id).strip() or default_host_id


def _mailbox_source(default_host_id: str) -> str:
    host_id = _mailbox_host_id(default_host_id)
    return (os.environ.get("MAILBOX_SOURCE") or host_id).strip() or host_id


def _encode_ref(provider: str, session_id: str) -> str:
    return f"{provider}:{session_id}"


def _decode_ref(ref: str) -> tuple[str, str]:
    value = (ref or "").strip()
    if ":" not in value:
        provider = _normalize_provider(os.environ.get("MAILBOX_PROVIDER") or "mailtm")
        return provider, value
    provider, session_id = value.split(":", 1)
    return _normalize_provider(provider), session_id.strip()


def _normalize_requested_email_address(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized or "@" not in normalized:
        return ""
    local_part, _, domain = normalized.partition("@")
    local_part = local_part.strip()
    domain = domain.strip().lower()
    if not local_part or not domain:
        return ""
    return f"{local_part}@{domain}"


def create_mailbox(
    *,
    provider: str = "",
    mailcreate_base_url: str = "",
    mailcreate_custom_auth: str = "",
    mailcreate_domain: str = "",
    requested_email_address: Optional[str] = None,
    requested_local_part: Optional[str] = None,
    gptmail_base_url: str = "",
    gptmail_api_key: str = "",
    gptmail_keys_file: str = "",
    gptmail_prefix: Optional[str] = None,
    gptmail_domain: Optional[str] = None,
    mailtm_api_base: str = "",
    default_host_id: str = "python-browser-service",
    prefer_raw_self_hosted_ref: bool = False,
    ttl_minutes: Optional[float] = None,
    ttl_seconds: Optional[float] = None,
    expiry_time_ms: Optional[int] = None,
) -> Mailbox:
    _ = mailcreate_base_url
    _ = mailcreate_custom_auth
    _ = gptmail_base_url
    _ = gptmail_api_key
    _ = gptmail_keys_file
    _ = gptmail_prefix
    _ = gptmail_domain
    _ = mailtm_api_base

    _wait_mail_service_ready()

    requested_provider = str(provider or os.environ.get("MAILBOX_PROVIDER") or "auto").strip().lower()
    provider_key: str | None = None
    if requested_provider not in ("", "auto", "any", "strategy"):
        provider_key = _normalize_provider(requested_provider)

    provision_mode = "always-create-dedicated"
    binding_mode = "dedicated-instance"
    preferred_instance_id = (os.environ.get("MAILBOX_PROVIDER_INSTANCE_ID") or "").strip()
    if provider_key == "self-hosted":
        provision_mode = "auto-create-if-missing"
        binding_mode = "shared-instance"
    elif provider_key is None:
        provision_mode = "auto-create-if-missing"
        binding_mode = "shared-instance"
    ttl_value_minutes: float
    if ttl_seconds is not None:
        ttl_value_minutes = max(1.0 / 60.0, float(ttl_seconds) / 60.0)
    elif ttl_minutes is not None:
        ttl_value_minutes = max(1.0 / 60.0, float(ttl_minutes))
    else:
        ttl_raw = (os.environ.get("MAILBOX_TTL_MINUTES") or "15").strip() or "15"
        ttl_value_minutes = max(1.0 / 60.0, float(ttl_raw))

    ttl_payload_value: int | float
    if abs(ttl_value_minutes - round(ttl_value_minutes)) < 1e-9:
        ttl_payload_value = int(round(ttl_value_minutes))
    else:
        ttl_payload_value = round(ttl_value_minutes, 4)
    resolved_expiry_time_ms = max(60_000, int(round(ttl_value_minutes * 60 * 1000)))
    if expiry_time_ms is not None:
        resolved_expiry_time_ms = max(0, int(expiry_time_ms))

    normalized_requested_email = _normalize_requested_email_address(requested_email_address)
    normalized_requested_local_part = str(requested_local_part or "").strip()
    requested_domain = str(mailcreate_domain or "").strip().lower()
    if normalized_requested_email:
        normalized_requested_local_part, _, requested_domain = normalized_requested_email.partition("@")

    payload = {
        "hostId": _mailbox_host_id(default_host_id),
        "provisionMode": provision_mode,
        "bindingMode": binding_mode,
        "ttlMinutes": ttl_payload_value,
        "metadata": {
            "source": _mailbox_source(default_host_id),
            "expiryTimeMs": str(resolved_expiry_time_ms),
            "expiryTime": str(resolved_expiry_time_ms),
        },
    }
    if provider_key:
        payload["providerTypeKey"] = provider_key
    else:
        payload.update(_resolve_mailbox_strategy_payload())
    if normalized_requested_email:
        payload["metadata"]["requestedEmailAddress"] = normalized_requested_email
    if normalized_requested_local_part:
        payload["metadata"]["requestedLocalPart"] = normalized_requested_local_part
    if requested_domain:
        payload["metadata"]["requestedDomain"] = requested_domain
        payload["metadata"]["mailcreateDomain"] = requested_domain
    if preferred_instance_id:
        payload["preferredInstanceId"] = preferred_instance_id
    response = _post_json("/mail/mailboxes/open", payload)
    result = response.get("result") or {}
    session = result.get("session") or {}
    session_id = str(session.get("id") or "").strip()
    email = str(session.get("emailAddress") or "").strip()
    mailbox_ref = str(session.get("mailboxRef") or "").strip()
    resolved_provider = str(session.get("providerTypeKey") or provider_key or _normalize_provider("")).strip()
    if not session_id or not email:
        raise RuntimeError("mail service returned invalid mailbox session")
    if normalized_requested_email and email.strip().lower() != normalized_requested_email:
        raise RuntimeError(
            "mail service returned mismatched mailbox email: "
            f"requested={normalized_requested_email} actual={email or '<missing>'}"
        )
    ref = mailbox_ref or _encode_ref(resolved_provider, session_id)
    if resolved_provider == "self-hosted" and prefer_raw_self_hosted_ref and mailbox_ref:
        ref = mailbox_ref
    return Mailbox(provider=resolved_provider, email=email, ref=ref, session_id=session_id)


def wait_openai_code(
    *,
    provider: str = "",
    mailbox_ref: str | None = None,
    session_id: str | None = None,
    address_jwt: str | None = None,
    mailcreate_base_url: str = "",
    mailcreate_custom_auth: str = "",
    gptmail_base_url: str = "",
    gptmail_api_key: str = "",
    gptmail_keys_file: str = "",
    mailtm_api_base: str = "",
    timeout_seconds: int = 180,
    min_mail_id: int = 0,
) -> str:
    _ = provider
    _ = mailcreate_base_url
    _ = mailcreate_custom_auth
    _ = gptmail_base_url
    _ = gptmail_api_key
    _ = gptmail_keys_file
    _ = mailtm_api_base

    ref = (mailbox_ref or address_jwt or "").strip()
    if not ref:
        raise RuntimeError("mailbox_ref is required")
    effective_session_id = str(session_id or "").strip()
    if not effective_session_id:
        _, effective_session_id = _decode_ref(ref)
    if not effective_session_id:
        raise RuntimeError("mailbox_ref is invalid")

    try:
        base_url = _mail_service_base_url()
    except Exception:
        base_url = ""
    print(
        "[mailbox] wait_openai_code mail-dispatch "
        f"base={base_url or '<missing>'} "
        f"session_id={effective_session_id} "
        f"probe={_probe_mail_service() if base_url else 'base_url_missing'} "
        f"timeout_seconds={timeout_seconds}"
    )

    deadline = time.time() + max(5, int(timeout_seconds))
    poll_interval = max(
        1,
        int(
            (os.environ.get("MAILBOX_POLL_INTERVAL_SECONDS") or str(DEFAULT_OTP_POLL_INTERVAL_SECONDS)).strip()
            or str(DEFAULT_OTP_POLL_INTERVAL_SECONDS)
        ),
    )
    snapshot_probe_every = max(1, int(max(1, poll_interval) * 3))
    last_snapshot_probe_at = 0.0
    while time.time() < deadline:
        response = _get_json(f"/mail/mailboxes/{effective_session_id}/code")
        code_obj = response.get("code")
        if isinstance(code_obj, dict):
            code = _select_openai_verification_code(code_obj)
            code_marker = _mail_dispatch_code_marker(code_obj)
            if code and (int(min_mail_id or 0) <= 0 or code_marker > int(min_mail_id or 0)):
                print(
                    "[mailbox] wait_openai_code received "
                    f"session_id={effective_session_id} code_len={len(code)} code_marker={code_marker}"
                )
                return code
        if time.time() - last_snapshot_probe_at >= snapshot_probe_every:
            last_snapshot_probe_at = time.time()
            snapshot_code, snapshot_marker = _snapshot_session_openai_code(
                session_id=effective_session_id,
                min_mail_id=int(min_mail_id or 0),
            )
            if snapshot_code:
                print(
                    "[mailbox] wait_openai_code snapshot_fallback "
                    f"session_id={effective_session_id} code_len={len(snapshot_code)} code_marker={snapshot_marker}"
                )
                return snapshot_code
        time.sleep(poll_interval)
    raise RuntimeError("timeout waiting for 6-digit code")


def release_mailbox(
    *,
    mailbox_ref: str | None = None,
    session_id: str | None = None,
    reason: str = "",
) -> dict:
    ref = (mailbox_ref or "").strip()
    effective_session_id = str(session_id or "").strip()
    if not effective_session_id and ref:
        _, effective_session_id = _decode_ref(ref)
    if not effective_session_id:
        return {"released": False, "detail": "missing_session_id"}
    payload = {
        "sessionId": effective_session_id,
    }
    if str(reason or "").strip():
        payload["reason"] = str(reason or "").strip()
    return _post_json("/mail/mailboxes/release", payload).get("result") or {}


def recover_mailbox_by_email(
    *,
    email_address: str,
    provider_type_key: str = "",
    host_id: str = "",
) -> dict:
    normalized_email = _normalize_requested_email_address(email_address)
    if not normalized_email:
        return {"recovered": False, "strategy": "not_supported", "detail": "invalid_email_address"}

    payload: dict[str, object] = {
        "emailAddress": normalized_email,
    }
    normalized_provider_type_key = str(provider_type_key or "").strip().lower()
    normalized_host_id = str(host_id or "").strip()
    if normalized_provider_type_key:
        payload["providerTypeKey"] = normalized_provider_type_key
    if normalized_host_id:
        payload["hostId"] = normalized_host_id
    return _post_json("/mail/mailboxes/recover-by-email", payload).get("result") or {
        "recovered": False,
        "strategy": "not_supported",
        "detail": "mail_service_returned_empty_result",
    }


def release_mailbox_sessions_by_email(
    *,
    email_address: str,
    provider_type_key: str = "",
    reason: str = "",
    limit: int = 200,
) -> list[dict]:
    normalized_email = _normalize_requested_email_address(email_address)
    if not normalized_email:
        return []
    query_parts = [
        f"limit={max(1, int(limit or 200))}",
        "newestFirst=true",
    ]
    normalized_provider_type = str(provider_type_key or "").strip().lower()
    if normalized_provider_type:
        query_parts.append(f"providerTypeKey={urllib.parse.quote(normalized_provider_type)}")
    response = _get_json("/mail/query/mailbox-sessions?" + "&".join(query_parts))
    sessions = response.get("sessions")
    if not isinstance(sessions, list):
        return []
    results: list[dict] = []
    seen_session_ids: set[str] = set()
    target_email = normalized_email.lower()
    effective_reason = str(reason or "").strip() or "same_email_conflict_cleanup"
    for item in sessions:
        if not isinstance(item, dict):
            continue
        session_email = str(item.get("emailAddress") or "").strip().lower()
        if session_email != target_email:
            continue
        current_session_id = str(item.get("id") or "").strip()
        if not current_session_id or current_session_id in seen_session_ids:
            continue
        seen_session_ids.add(current_session_id)
        try:
            release_result = release_mailbox(session_id=current_session_id, reason=effective_reason)
        except Exception as exc:
            release_result = {
                "released": False,
                "detail": "release_failed",
                "error": str(exc),
            }
        results.append(
            {
                "sessionId": current_session_id,
                "email": session_email,
                "release": release_result,
            }
        )
    return results


def cleanup_moemail_mailboxes(
    *,
    stale_after_seconds: int = 300,
    max_delete_count: int = 200,
) -> dict:
    payload = {
        "staleAfterSeconds": max(60, int(stale_after_seconds or 300)),
        "maxDeleteCount": max(1, int(max_delete_count or 200)),
    }
    return _post_json("/mail/providers/moemail/cleanup", payload).get("result") or {}


def get_mailbox_latest_message_id(
    *,
    mailbox_ref: str | None = None,
    session_id: str | None = None,
    address_jwt: str | None = None,
    mailcreate_base_url: str = "",
    mailcreate_custom_auth: str = "",
) -> int:
    effective_session_id = str(session_id or "").strip()
    ref = (mailbox_ref or address_jwt or "").strip()
    if not ref and not effective_session_id:
        return 0
    if not effective_session_id and ref:
        _, effective_session_id = _decode_ref(ref)
    if not effective_session_id:
        return 0
    try:
        response = _get_json(f"/mail/mailboxes/{effective_session_id}/code")
    except Exception:
        return 0
    code_obj = response.get("code")
    if not isinstance(code_obj, dict):
        return 0
    return _mail_dispatch_code_marker(code_obj)

