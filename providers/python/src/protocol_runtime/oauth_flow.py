from __future__ import annotations

import base64
import hashlib
import json
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

from shared_proxy import debug_log_system_native_proxy_decision, resolve_system_native_proxy_decision


AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

DEFAULT_CALLBACK_PORT = 1455
DEFAULT_REDIRECT_URI = f"http://localhost:{DEFAULT_CALLBACK_PORT}/auth/callback"
DEFAULT_SCOPE = "openid email profile offline_access"

CHATGPT_WEB_CLIENT_ID = "app_X8zY6vW2pQ9tR3dE7nK1jL5gH"
CHATGPT_WEB_AUTH_URL = "https://auth.openai.com/api/accounts/authorize"
CHATGPT_WEB_REDIRECT_URI = "https://chatgpt.com/api/auth/callback/openai"
CHATGPT_WEB_SCOPE = "openid email profile offline_access model.request model.read organization.read organization.write"
CHATGPT_WEB_SCREEN_HINT = "login_or_signup"


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sha256_b64url_no_pad(s: str) -> str:
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())


def _random_state(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def _parse_callback_url(callback_url: str) -> dict[str, str]:
    candidate = callback_url.strip()
    if not candidate:
        return {
            "code": "",
            "state": "",
            "error": "",
            "error_description": "",
        }

    if "://" not in candidate:
        if candidate.startswith("?"):
            candidate = f"http://localhost{candidate}"
        elif any(ch in candidate for ch in "/?#") or ":" in candidate:
            candidate = f"http://{candidate}"
        elif "=" in candidate:
            candidate = f"http://localhost/?{candidate}"

    parsed = urllib.parse.urlparse(candidate)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    fragment = urllib.parse.parse_qs(parsed.fragment, keep_blank_values=True)

    for key, values in fragment.items():
        if key not in query or not query[key] or not (query[key][0] or "").strip():
            query[key] = values

    def get1(k: str) -> str:
        v = query.get(k, [""])
        return (v[0] or "").strip()

    code = get1("code")
    state = get1("state")
    error = get1("error")
    error_description = get1("error_description")

    if code and not state and "#" in code:
        code, state = code.split("#", 1)

    if not error and error_description:
        error, error_description = error_description, ""

    return {
        "code": code,
        "state": state,
        "error": error,
        "error_description": error_description,
    }


def _jwt_claims_no_verify(id_token: str) -> dict[str, Any]:
    if not id_token or id_token.count(".") < 2:
        return {}
    payload_b64 = id_token.split(".")[1]
    pad = "=" * ((4 - (len(payload_b64) % 4)) % 4)
    try:
        payload = base64.urlsafe_b64decode((payload_b64 + pad).encode("ascii"))
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return {}


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _build_opener(
    proxy: str | None = None,
    *,
    verify_tls: bool = True,
):
    handlers: list[Any] = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        # Disable environment proxies so the "direct" route is actually direct.
        handlers.append(urllib.request.ProxyHandler({}))
    if not verify_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def _post_form(
    url: str,
    data: dict[str, str],
    *,
    timeout: int = 30,
    proxy: str | None = None,
    verify_tls: bool = False,
    try_direct_first: bool = True,
    max_retries: int = 6,
) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    retry_total = max(1, max_retries)
    for attempt in range(retry_total):
        routes: list[tuple[str, str | None]] = []
        if proxy:
            if try_direct_first:
                routes.extend([("direct", None), ("proxy", proxy)])
            else:
                routes.extend([("proxy", proxy), ("direct", None)])
        else:
            routes.append(("direct", None))

        for route_kind, route_proxy in routes:
            if route_kind == "proxy":
                debug_log_system_native_proxy_decision(
                    "oauth-flow",
                    resolve_system_native_proxy_decision(url, explicit_proxy=route_proxy),
                    extra_fields={"requestLabel": "oauth-token"},
                )
            else:
                debug_log_system_native_proxy_decision(
                    "oauth-flow",
                    resolve_system_native_proxy_decision(url),
                    extra_fields={"requestLabel": "oauth-token"},
                )
            try:
                with _build_opener(
                    route_proxy,
                    verify_tls=verify_tls,
                ).open(req, timeout=timeout) as resp:
                    raw = resp.read()
                    if resp.status != 200:
                        raise RuntimeError(
                            f"token exchange failed: {resp.status}: {raw.decode('utf-8', 'replace')}"
                        )
                    return json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                raise RuntimeError(
                    f"token exchange failed: {exc.code}: {raw.decode('utf-8', 'replace')}"
                ) from exc
            except Exception:
                continue

        time.sleep(2)

    raise RuntimeError("Failed to post form after max retries")


@dataclass(frozen=True)
class OAuthStart:
    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str


def generate_oauth_url(
    *,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    scope: str = DEFAULT_SCOPE,
    auth_url: str = AUTH_URL,
    client_id: str = CLIENT_ID,
    extra_params: dict[str, str] | None = None,
    include_pkce: bool = True,
    prompt: str | None = "login",
) -> OAuthStart:
    state = _random_state()
    code_verifier = _pkce_verifier() if include_pkce else ""

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
    }
    normalized_prompt = str(prompt or "").strip() if prompt is not None else ""
    if prompt is not None and normalized_prompt:
        params["prompt"] = normalized_prompt
    if include_pkce:
        params.update({
            "code_challenge": _sha256_b64url_no_pad(code_verifier),
            "code_challenge_method": "S256",
        })
    if client_id == CLIENT_ID:
        # Codex CLI 专属参数
        params.update({
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
        })
    if extra_params:
        params.update(extra_params)

    full_auth_url = f"{auth_url}?{urllib.parse.urlencode(params)}"
    return OAuthStart(
        auth_url=full_auth_url,
        state=state,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )


def generate_chatgpt_web_oauth_url(device_id: str | None = None) -> OAuthStart:
    """生成用于创建账号的 ChatGPT Web 客户端 OAuth URL (不触发 phone_wall)"""
    device_id = device_id or str(uuid.uuid4())
    return generate_oauth_url(
        auth_url=CHATGPT_WEB_AUTH_URL,
        client_id=CHATGPT_WEB_CLIENT_ID,
        redirect_uri=CHATGPT_WEB_REDIRECT_URI,
        scope=CHATGPT_WEB_SCOPE,
        include_pkce=False,
        extra_params={
            "audience": "https://api.openai.com/v1",
            "device_id": device_id,
            "screen_hint": CHATGPT_WEB_SCREEN_HINT,
            "ext-oai-did": device_id,
            "auth_session_logging_id": str(uuid.uuid4()),
        },
    )


def submit_callback_url(
    *,
    callback_url: str,
    expected_state: str,
    code_verifier: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    proxy: str | None = None,
    mailbox_ref: str = "",
    password: str = "",
    first_name: str = "",
    last_name: str = "",
    birthdate: str = "",
    token_url: str = TOKEN_URL,
    client_id: str = CLIENT_ID,
    token_post_verify_tls: bool = False,
    token_post_try_direct_first: bool = False,
    token_post_max_retries: int = 6,
) -> tuple[str, str]:
    cb = _parse_callback_url(callback_url)
    if cb["error"]:
        desc = cb["error_description"]
        raise RuntimeError(f"oauth error: {cb['error']}: {desc}".strip())

    if not cb["code"]:
        raise ValueError("callback url missing ?code=")
    if not cb["state"]:
        raise ValueError("callback url missing ?state=")
    if cb["state"] != expected_state:
        raise ValueError("state mismatch")

    token_resp = _post_form(
        token_url,
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": cb["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        timeout=30,
        proxy=proxy,
        verify_tls=token_post_verify_tls,
        try_direct_first=token_post_try_direct_first,
        max_retries=token_post_max_retries,
    )
    access_token = str(token_resp.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("token response missing access_token")

    id_token = str(token_resp.get("id_token") or "").strip()
    refresh_token = str(token_resp.get("refresh_token") or "").strip()
    expires_in = _to_int(token_resp.get("expires_in"))

    claims = _jwt_claims_no_verify(id_token)
    access_claims = _jwt_claims_no_verify(access_token)
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    if not isinstance(auth_claims, dict):
        auth_claims = {}
    if not auth_claims:
        fallback_auth_claims = access_claims.get("https://api.openai.com/auth") or {}
        if isinstance(fallback_auth_claims, dict):
            auth_claims = fallback_auth_claims

    email = str(claims.get("email") or "").strip()
    if not email:
        profile_claims = access_claims.get("https://api.openai.com/profile") or {}
        if isinstance(profile_claims, dict):
            email = str(profile_claims.get("email") or "").strip()

    account_id = str(auth_claims.get("chatgpt_account_id") or "").strip()
    if not account_id:
        account_id = str(claims.get("sub") or "").strip()

    now = int(time.time())
    expired_rfc3339 = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0))
    )
    now_rfc3339 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    config = dict(claims)
    config.update({
        "type": "codex",
        "email": email,
        "expired": expired_rfc3339,
        "disabled": False,
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "password": password,
        "birthdate": birthdate,
        "client_id": client_id,
        "last_name": last_name,
        "account_id": account_id,
        "first_name": first_name,
        "session_id": claims.get("session_id", ""),
        "last_refresh": now_rfc3339,
        "pwd_auth_time": claims.get("pwd_auth_time", int(time.time() * 1000)),
        "https://api.openai.com/auth": auth_claims,
        "https://api.openai.com/profile": claims.get("https://api.openai.com/profile", {}),
    })

    schema_defaults = {
        "refresh_token": "",
        "session_id": "",
        "password": "",
        "birthdate": "",
        "first_name": "",
        "last_name": "",
        "mailbox_ref": "",
    }
    for key, value in schema_defaults.items():
        if key not in config:
            config[key] = value

    if mailbox_ref and str(mailbox_ref).strip():
        config["mailbox_ref"] = str(mailbox_ref).strip()

    return email, json.dumps(config, ensure_ascii=False)
