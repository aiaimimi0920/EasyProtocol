from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.parse
from pathlib import Path
from typing import Any

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
    from others.runtime import flow_network_env, seed_device_cookie
    from others.storage import load_json_payload
else:
    from .others.bootstrap import ensure_local_bundle_imports

    ensure_local_bundle_imports()
    from .others.runtime import flow_network_env, seed_device_cookie
    from .others.storage import load_json_payload

from curl_cffi import requests

from shared_proxy import env_flag, normalize_proxy_env_url

from protocol_runtime.protocol_register import (
    DEFAULT_PROTOCOL_SEC_CH_UA,
    DEFAULT_PROTOCOL_USER_AGENT,
    _get_sentinel_header_for_signup,
    _new_protocol_sentinel_context,
    _response_preview,
    _session_request,
)


_PLATFORM_AUTH0_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
_PLATFORM_AUTH0_REDIRECT_URI = "https://platform.openai.com/auth/callback"
_PLATFORM_AUTH0_AUDIENCE = "https://api.openai.com/v1"
_PLATFORM_AUTH0_SCOPE = "openid profile email offline_access"
_PLATFORM_AUTH0_CLIENT_INFO = {"name": "auth0-spa-js", "version": "1.21.0"}
_PLATFORM_AUTH0_TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
_PLATFORM_ONBOARDING_LOGIN_URL = "https://api.openai.com/dashboard/onboarding/login"
_PLATFORM_ORGANIZATION_PERMISSIONS_URL = "https://api.openai.com/v1/dashboard/organization/permissions"
_PLATFORM_PROJECT_PERMISSIONS_TEMPLATE = "https://api.openai.com/v1/dashboard/projects/{project_id}/permissions"
_PLATFORM_ORGANIZATION_UPDATE_TEMPLATE = "https://api.openai.com/v1/organizations/{org_id}"
_PLATFORM_ORGANIZATION_USER_UPDATE_TEMPLATE = "https://api.openai.com/v1/organizations/{org_id}/users/{user_id}"
_PLATFORM_REFERER = "https://platform.openai.com/"
_PLATFORM_ORIGIN = "https://platform.openai.com"
_PLATFORM_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"


def _auth0_client_header_value() -> str:
    return base64.urlsafe_b64encode(
        json.dumps(_PLATFORM_AUTH0_CLIENT_INFO, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")


def _parse_callback_code(final_url: str) -> str:
    raw = str(final_url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
    except Exception:
        return ""
    values = urllib.parse.parse_qs(parsed.query or "")
    return str((values.get("code") or [""])[0] or "").strip()


def _parse_callback_state(final_url: str) -> str:
    raw = str(final_url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
    except Exception:
        return ""
    values = urllib.parse.parse_qs(parsed.query or "")
    return str((values.get("state") or [""])[0] or "").strip()


def _decode_jwt_without_verify(token: str) -> dict[str, Any]:
    raw = str(token or "").strip()
    parts = raw.split(".")
    if len(parts) < 2:
        return {}
    payload_segment = parts[1]
    payload_segment += "=" * (-len(payload_segment) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload_segment.encode("ascii")).decode("utf-8")
        parsed = json.loads(decoded)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _select_default_org(orgs: list[dict[str, Any]]) -> dict[str, Any]:
    if not orgs:
        return {}
    for entry in orgs:
        if bool(entry.get("is_default")):
            return entry
    return orgs[-1]


def _extract_login_context(login_payload: dict[str, Any], access_claims: dict[str, Any]) -> dict[str, Any]:
    user_payload = login_payload.get("user") if isinstance(login_payload.get("user"), dict) else {}
    session_payload = user_payload.get("session") if isinstance(user_payload.get("session"), dict) else {}
    orgs_payload = user_payload.get("orgs") if isinstance(user_payload.get("orgs"), dict) else {}
    org_entries = orgs_payload.get("data") if isinstance(orgs_payload.get("data"), list) else []
    normalized_orgs = [item for item in org_entries if isinstance(item, dict)]
    selected_org = _select_default_org(normalized_orgs)
    projects_payload = selected_org.get("projects") if isinstance(selected_org.get("projects"), dict) else {}
    project_entries = projects_payload.get("data") if isinstance(projects_payload.get("data"), list) else []
    project_entry = project_entries[0] if project_entries and isinstance(project_entries[0], dict) else {}
    nested_access_claims = (
        access_claims.get("https://api.openai.com/auth")
        if isinstance(access_claims.get("https://api.openai.com/auth"), dict)
        else {}
    )
    return {
        "userId": str(
            user_payload.get("id")
            or nested_access_claims.get("user_id")
            or nested_access_claims.get("chatgpt_user_id")
            or ""
        ).strip(),
        "sessionToken": str(session_payload.get("sensitive_id") or "").strip(),
        "organizationId": str(selected_org.get("id") or "").strip(),
        "organizationTitle": str(selected_org.get("title") or "").strip(),
        "organizationName": str(selected_org.get("name") or "").strip(),
        "organizationDescription": str(selected_org.get("description") or "").strip(),
        "projectId": str(project_entry.get("id") or "").strip(),
        "projectTitle": str(project_entry.get("title") or "").strip(),
        "completedPlatformOnboarding": bool(
            ((selected_org.get("settings") or {}) if isinstance(selected_org.get("settings"), dict) else {}).get(
                "completed_platform_onboarding"
            )
        ),
        "rawOrganization": selected_org,
        "rawUser": user_payload,
    }


def _build_platform_headers(
    *,
    authorization_token: str,
    content_type: str = "application/json",
    include_sentinel: bool = False,
    session: requests.Session | None = None,
    device_id: str = "",
    explicit_proxy: str | None = None,
) -> dict[str, str]:
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": _PLATFORM_ACCEPT_LANGUAGE,
        "authorization": f"Bearer {authorization_token}",
        "content-type": content_type,
        "origin": _PLATFORM_ORIGIN,
        "referer": _PLATFORM_REFERER,
        "sec-ch-ua": DEFAULT_PROTOCOL_SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
        "user-agent": DEFAULT_PROTOCOL_USER_AGENT,
    }
    if include_sentinel and session is not None and device_id:
        sentinel_context = _new_protocol_sentinel_context(
            session,
            explicit_proxy=explicit_proxy,
            user_agent=DEFAULT_PROTOCOL_USER_AGENT,
        )
        headers["openai-sentinel-token"] = _get_sentinel_header_for_signup(
            session,
            device_id=device_id,
            flow="update_organization",
            request_kind="platform-update-organization",
            explicit_proxy=explicit_proxy,
            sentinel_context=sentinel_context,
        )
    return headers


def _best_effort_warm_platform_permissions(
    *,
    session: requests.Session,
    session_token: str,
    org_id: str,
    project_id: str,
    explicit_proxy: str | None,
) -> None:
    if not session_token or not org_id:
        return
    headers = _build_platform_headers(
        authorization_token=session_token,
        session=session,
        explicit_proxy=explicit_proxy,
    )
    headers["openai-organization"] = org_id
    try:
        _session_request(
            session,
            "GET",
            _PLATFORM_ORGANIZATION_PERMISSIONS_URL,
            explicit_proxy=explicit_proxy,
            request_label="platform-organization-permissions",
            headers=headers,
            timeout=20,
        )
    except Exception:
        pass
    if project_id:
        project_headers = dict(headers)
        project_headers["openai-project"] = project_id
        try:
            _session_request(
                session,
                "GET",
                _PLATFORM_PROJECT_PERMISSIONS_TEMPLATE.format(project_id=project_id),
                explicit_proxy=explicit_proxy,
                request_label="platform-project-permissions",
                headers=project_headers,
                timeout=20,
            )
        except Exception:
            pass


def run_protocol_platform_organization_init_from_path(
    *,
    source_path: str | Path,
    explicit_proxy: str | None = None,
    organization_name: str = "personal",
    organization_title: str = "personal",
    developer_persona: str = "student",
) -> dict[str, Any]:
    seed_path = Path(source_path).resolve()
    seed_payload = load_json_payload(seed_path)
    existing_state = seed_payload.get("platformOrganization")
    if isinstance(existing_state, dict) and str(existing_state.get("status") or "").strip().lower() == "completed":
        return {
            "ok": True,
            "status": "already_initialized",
            "sourcePath": str(seed_path),
            "organizationId": str(existing_state.get("organizationId") or "").strip(),
            "projectId": str(existing_state.get("projectId") or "").strip(),
            "userId": str(existing_state.get("userId") or "").strip(),
        }

    final_url = str(seed_payload.get("finalUrl") or seed_payload.get("final_url") or "").strip()
    callback_code = _parse_callback_code(final_url)
    callback_state = _parse_callback_state(final_url)
    platform_auth = seed_payload.get("platformAuth") if isinstance(seed_payload.get("platformAuth"), dict) else {}
    code_verifier = str(platform_auth.get("codeVerifier") or "").strip()
    expected_state = str(platform_auth.get("state") or "").strip()
    device_id = str(platform_auth.get("deviceId") or "").strip()
    if not callback_code or not code_verifier:
        return {
            "ok": True,
            "status": "skipped_missing_callback_context",
            "sourcePath": str(seed_path),
            "callbackCodePresent": bool(callback_code),
            "codeVerifierPresent": bool(code_verifier),
        }
    if expected_state and callback_state and expected_state != callback_state:
        raise RuntimeError("platform_callback_state_mismatch")

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

    try:
        with flow_network_env():
            token_response = _session_request(
                session,
                "POST",
                _PLATFORM_AUTH0_TOKEN_URL,
                explicit_proxy=explicit_proxy,
                request_label="platform-oauth-token",
                timeout=20,
                headers={
                    "accept": "application/json",
                    "accept-language": _PLATFORM_ACCEPT_LANGUAGE,
                    "auth0-client": _auth0_client_header_value(),
                    "content-type": "application/json",
                    "referer": _PLATFORM_REFERER,
                    "sec-ch-ua": DEFAULT_PROTOCOL_SEC_CH_UA,
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": "\"Windows\"",
                    "user-agent": DEFAULT_PROTOCOL_USER_AGENT,
                },
                json={
                    "client_id": _PLATFORM_AUTH0_CLIENT_ID,
                    "code_verifier": code_verifier,
                    "grant_type": "authorization_code",
                    "code": callback_code,
                    "redirect_uri": _PLATFORM_AUTH0_REDIRECT_URI,
                },
            )
            if int(getattr(token_response, "status_code", 0) or 0) != 200:
                raise RuntimeError(
                    "platform_oauth_token_exchange_failed "
                    f"status={getattr(token_response, 'status_code', 0)} "
                    f"body={_response_preview(token_response, 300)}"
                )
            token_payload = token_response.json() if hasattr(token_response, "json") else {}
            if not isinstance(token_payload, dict):
                raise RuntimeError("platform_oauth_token_payload_invalid")
            access_token = str(token_payload.get("access_token") or "").strip()
            if not access_token:
                raise RuntimeError("platform_oauth_access_token_missing")

            onboarding_login_response = _session_request(
                session,
                "POST",
                _PLATFORM_ONBOARDING_LOGIN_URL,
                explicit_proxy=explicit_proxy,
                request_label="platform-onboarding-login",
                timeout=20,
                headers=_build_platform_headers(
                    authorization_token=access_token,
                    session=session,
                    explicit_proxy=explicit_proxy,
                ),
                json={"app": "api"},
            )
            if int(getattr(onboarding_login_response, "status_code", 0) or 0) != 200:
                raise RuntimeError(
                    "platform_onboarding_login_failed "
                    f"status={getattr(onboarding_login_response, 'status_code', 0)} "
                    f"body={_response_preview(onboarding_login_response, 400)}"
                )
            onboarding_login_payload = onboarding_login_response.json() if hasattr(onboarding_login_response, "json") else {}
            if not isinstance(onboarding_login_payload, dict):
                raise RuntimeError("platform_onboarding_login_payload_invalid")

            access_claims = _decode_jwt_without_verify(access_token)
            context = _extract_login_context(onboarding_login_payload, access_claims)
            org_id = str(context.get("organizationId") or "").strip()
            user_id = str(context.get("userId") or "").strip()
            project_id = str(context.get("projectId") or "").strip()
            session_token = str(context.get("sessionToken") or "").strip()
            if not org_id:
                raise RuntimeError("platform_organization_id_missing")
            if not user_id:
                raise RuntimeError("platform_user_id_missing")
            if not session_token:
                raise RuntimeError("platform_session_token_missing")

            _best_effort_warm_platform_permissions(
                session=session,
                session_token=session_token,
                org_id=org_id,
                project_id=project_id,
                explicit_proxy=explicit_proxy,
            )

            org_update_response = _session_request(
                session,
                "POST",
                _PLATFORM_ORGANIZATION_UPDATE_TEMPLATE.format(org_id=org_id),
                explicit_proxy=explicit_proxy,
                request_label="platform-organization-update",
                timeout=20,
                headers=_build_platform_headers(
                    authorization_token=session_token,
                    include_sentinel=True,
                    session=session,
                    device_id=device_id,
                    explicit_proxy=explicit_proxy,
                ),
                json={
                    "name": str(organization_name or "").strip() or "personal",
                    "title": str(organization_title or "").strip() or "personal",
                    "settings": {
                        "completed_platform_onboarding": True,
                    },
                },
            )
            if int(getattr(org_update_response, "status_code", 0) or 0) != 200:
                raise RuntimeError(
                    "platform_organization_update_failed "
                    f"status={getattr(org_update_response, 'status_code', 0)} "
                    f"body={_response_preview(org_update_response, 400)}"
                )
            org_update_payload = org_update_response.json() if hasattr(org_update_response, "json") else {}

            user_update_response = _session_request(
                session,
                "POST",
                _PLATFORM_ORGANIZATION_USER_UPDATE_TEMPLATE.format(org_id=org_id, user_id=user_id),
                explicit_proxy=explicit_proxy,
                request_label="platform-organization-user-update",
                timeout=20,
                headers=_build_platform_headers(
                    authorization_token=session_token,
                    session=session,
                    explicit_proxy=explicit_proxy,
                ),
                json={
                    "developer_persona": str(developer_persona or "").strip() or "student",
                },
            )
            if int(getattr(user_update_response, "status_code", 0) or 0) != 200:
                raise RuntimeError(
                    "platform_organization_user_update_failed "
                    f"status={getattr(user_update_response, 'status_code', 0)} "
                    f"body={_response_preview(user_update_response, 400)}"
                )
            user_update_payload = user_update_response.json() if hasattr(user_update_response, "json") else {}

        platform_state = {
            "ok": True,
            "status": "completed",
            "organizationId": org_id,
            "organizationTitle": str(context.get("organizationTitle") or "").strip(),
            "organizationName": str(context.get("organizationName") or "").strip(),
            "organizationDescription": str(context.get("organizationDescription") or "").strip(),
            "projectId": project_id,
            "projectTitle": str(context.get("projectTitle") or "").strip(),
            "userId": user_id,
            "developerPersona": str(developer_persona or "").strip() or "student",
            "completedPlatformOnboarding": bool(context.get("completedPlatformOnboarding")),
            "oauthClientId": _PLATFORM_AUTH0_CLIENT_ID,
            "sourcePath": str(seed_path),
        }
        updated_payload = dict(seed_payload)
        updated_payload["platformOrganization"] = platform_state
        updated_payload["platformOrganizationDetails"] = {
            "onboardingLogin": onboarding_login_payload,
            "organizationUpdate": org_update_payload,
            "userUpdate": user_update_payload,
        }
        seed_path.write_text(json.dumps(updated_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return platform_state
    finally:
        try:
            session.close()
        except Exception:
            pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize platform organization from a small_success seed.")
    parser.add_argument("source", help="Path to small_success JSON.")
    parser.add_argument("--proxy", default="", help="Optional explicit proxy URL.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = run_protocol_platform_organization_init_from_path(
        source_path=args.source,
        explicit_proxy=str(args.proxy or "").strip() or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
