from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import sys
import time
import urllib.parse
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from curl_cffi import requests

if __package__ in (None, ""):
    _CURRENT_DIR = Path(__file__).resolve().parent
    _SRC_DIR = _CURRENT_DIR.parent
    _PYTHON_SHARED_SRC = _CURRENT_DIR.parents[2] / "python_shared" / "src"
    for _candidate in (_CURRENT_DIR, _SRC_DIR, _PYTHON_SHARED_SRC):
        candidate_text = str(_candidate)
        if candidate_text not in sys.path:
            sys.path.append(candidate_text)
    from others.bootstrap import ensure_local_bundle_imports
    ensure_local_bundle_imports()
    from others.runtime import flow_network_env, lease_flow_proxy
    from others.storage import load_json_payload
else:
    from .others.bootstrap import ensure_local_bundle_imports
    ensure_local_bundle_imports()
    from .others.runtime import flow_network_env, lease_flow_proxy
    from .others.storage import load_json_payload

from shared_proxy import build_request_proxies, env_flag

CHATGPT_BASE_URL = "https://chatgpt.com"
AUTH_OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
DEFAULT_INVITE_ROLE = "standard-user"
DEFAULT_SEAT_TYPE = (
    os.environ.get("CHATGPT_TEAM_INVITE_SEAT_TYPE")
    or os.environ.get("REGISTER_TEAM_INVITE_SEAT_TYPE")
    or "usage_based"
).strip() or "usage_based"
DEFAULT_TEAM_AUTH_GLOB = "*-team.json"
DEFAULT_TEAM_AUTH_PATH_ENV = "REGISTER_TEAM_AUTH_PATH"
DEFAULT_TEAM_AUTH_DIR_ENV = "REGISTER_TEAM_AUTH_DIR"
DEFAULT_TEAM_AUTH_DIRS_ENV = "REGISTER_TEAM_AUTH_DIRS"
DEFAULT_TEAM_AUTH_GLOB_ENV = "REGISTER_TEAM_AUTH_GLOB"
DEFAULT_HTTP_TIMEOUT_SECONDS = 30
DEFAULT_HTTP_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 0.8
DEFAULT_REFRESH_WINDOW_SECONDS = 600
DEFAULT_TEAM_CAPACITY_LIMIT = max(1, int(str(os.environ.get("REGISTER_TEAM_CAPACITY_LIMIT") or "9").strip() or "9"))
DEFAULT_TEAM_CHATGPT_SEAT_LIMIT = max(
    0,
    min(
        DEFAULT_TEAM_CAPACITY_LIMIT,
        int(str(os.environ.get("REGISTER_TEAM_CHATGPT_SEAT_LIMIT") or "4").strip() or "4"),
    ),
)
DEFAULT_TEAM_CODEX_SEAT_LIMIT = max(
    0,
    min(
        DEFAULT_TEAM_CAPACITY_LIMIT,
        int(str(os.environ.get("REGISTER_TEAM_CODEX_SEAT_LIMIT") or str(DEFAULT_TEAM_CAPACITY_LIMIT)).strip() or str(DEFAULT_TEAM_CAPACITY_LIMIT)),
    ),
)
DEFAULT_TEAM_EXPAND_REQUIRED_CHATGPT_SEATS = max(
    1,
    int(str(os.environ.get("REGISTER_TEAM_MEMBER_COUNT") or "4").strip() or "4"),
)
DEFAULT_TEAM_INVITE_SWEEP_AGE_SECONDS = max(
    0,
    int(str(os.environ.get("REGISTER_TEAM_INVITE_SWEEP_AGE_SECONDS") or "180").strip() or "180"),
)
DEFAULT_TEAM_SEAT_LOOKUP_TIMEOUT_SECONDS = max(
    0,
    int(str(os.environ.get("REGISTER_TEAM_SEAT_LOOKUP_TIMEOUT_SECONDS") or "30").strip() or "30"),
)
DEFAULT_TEAM_SEAT_LOOKUP_INTERVAL_SECONDS = max(
    1,
    int(str(os.environ.get("REGISTER_TEAM_SEAT_LOOKUP_INTERVAL_SECONDS") or "2").strip() or "2"),
)
DEFAULT_CODEX_SEAT_TYPES = {
    item.strip().lower()
    for item in str(os.environ.get("REGISTER_TEAM_CODEX_SEAT_TYPES") or "usage_based,codex").split(",")
    if item.strip()
}
DEFAULT_REFRESH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_CHATGPT_CLIENT_VERSION = (
    os.environ.get("CHATGPT_TEAM_INVITE_CLIENT_VERSION") or "prod-5efc0c09646aabd56007fd08c040e0faa085a7b8"
).strip() or "prod-5efc0c09646aabd56007fd08c040e0faa085a7b8"
DEFAULT_CHATGPT_CLIENT_BUILD_NUMBER = (
    os.environ.get("CHATGPT_TEAM_INVITE_CLIENT_BUILD_NUMBER") or "6039550"
).strip() or "6039550"
DEFAULT_CHATGPT_USER_AGENT = (
    os.environ.get("CHATGPT_TEAM_INVITE_USER_AGENT")
    or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0"
).strip()
DEFAULT_IMPERSONATE = (os.environ.get("PROTOCOL_HTTP_IMPERSONATE") or "chrome").strip() or "chrome"
OTHERS_DIR = Path(__file__).resolve().parent / "others"
DEFAULT_TEAM_AUTH_DEFAULT_DIR = Path.home() / ".cli-proxy-api"


def _manual_free_oauth_preserve_enabled() -> bool:
    return str(os.environ.get("REGISTER_FREE_MANUAL_OAUTH_PRESERVE_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass(frozen=True)
class RegisterInviteResult:
    ok: bool
    status: str
    invite_email: str
    team_account_id: str
    team_email: str
    status_code: int = 0
    detail: str = ""
    response: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RevokeInviteResult:
    ok: bool
    status: str
    invite_id: str
    invite_email: str
    team_account_id: str
    team_email: str
    status_code: int = 0
    detail: str = ""
    response: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RefreshTeamAuthResult:
    ok: bool
    refreshed: bool
    storage_path: str
    team_email: str
    team_account_id: str
    expired: str
    last_refresh: str
    auth_payload: dict[str, Any]
    detail: str = ""
    response: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CleanupCodexCapacityResult:
    ok: bool
    status: str
    team_account_id: str
    team_email: str
    revoked_invites: int = 0
    removed_users: int = 0
    skipped_invites: int = 0
    skipped_users: int = 0
    detail: str = ""
    response: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CleanupTeamSeatsResult:
    ok: bool
    status: str
    team_account_id: str
    team_email: str
    revoked_invites: int = 0
    removed_users: int = 0
    skipped_invites: int = 0
    skipped_users: int = 0
    had_existing_seats: bool = False
    detail: str = ""
    response: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UpdateTeamSeatResult:
    ok: bool
    status: str
    invite_id: str
    user_id: str
    member_email: str
    seat_type: str
    team_account_id: str
    team_email: str
    status_code: int = 0
    detail: str = ""
    response: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TeamInviteClient:
    def __init__(
        self,
        *,
        auth_token: str,
        chatgpt_account_id: str,
        oai_device_id: str = "",
        explicit_proxy: str = "",
        timeout: int = DEFAULT_HTTP_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_HTTP_MAX_RETRIES,
        retry_backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    ) -> None:
        self._auth_token = _extract_bearer(auth_token)
        self._chatgpt_account_id = str(chatgpt_account_id or "").strip()
        self._oai_device_id = str(oai_device_id or "").strip()
        self._explicit_proxy = str(explicit_proxy or "").strip()
        self._timeout = max(1, int(timeout))
        self._max_retries = max(1, int(max_retries))
        self._retry_backoff = max(0.1, float(retry_backoff))
        self._verify_tls = env_flag("PROTOCOL_HTTP_VERIFY_TLS", False)
        self._oai_session_id = str(uuid.uuid4())
        self._session = requests.Session(
            impersonate=DEFAULT_IMPERSONATE,
            timeout=self._timeout,
            verify=self._verify_tls,
        )
        self._session.headers.update({"user-agent": DEFAULT_CHATGPT_USER_AGENT})
        _seed_chatgpt_device_cookie(self._session, self._oai_device_id)

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass

    def list_users(self) -> tuple[bool, list[dict[str, Any]]]:
        status, payload = self._request_json("GET", f"/backend-api/accounts/{self._chatgpt_account_id}/users")
        if not (200 <= status < 300):
            return False, []
        return True, _extract_items(payload)

    def list_invites(self) -> tuple[bool, list[dict[str, Any]]]:
        status, payload = self._request_json("GET", f"/backend-api/accounts/{self._chatgpt_account_id}/invites")
        if not (200 <= status < 300):
            return False, []
        return True, _extract_items(payload)

    def send_invite(self, invite_email: str, *, seat_type: str | None = None) -> tuple[bool, dict[str, Any]]:
        resolved_seat_type = str(seat_type or DEFAULT_SEAT_TYPE).strip() or DEFAULT_SEAT_TYPE
        status, payload = self._request_json(
            "POST",
            f"/backend-api/accounts/{self._chatgpt_account_id}/invites",
            {
                "email_addresses": [invite_email],
                "role": DEFAULT_INVITE_ROLE,
                "seat_type": resolved_seat_type,
                "resend_emails": True,
            },
        )
        payload_dict = payload if isinstance(payload, dict) else {"payload": payload}
        payload_dict["status_code"] = status
        if 200 <= status < 300:
            account_invites = payload_dict.get("account_invites")
            if isinstance(account_invites, list) and account_invites:
                return True, payload_dict
            errored = payload_dict.get("errored_emails")
            if errored:
                return False, {
                    "error": _classify_invite_error(status, payload_dict),
                    "status_code": status,
                    "detail": _extract_detail(payload_dict),
                    "errored_emails": errored,
                    "payload": payload_dict,
                }
            return True, payload_dict
        return False, {
            "error": _classify_invite_error(status, payload_dict),
            "status_code": status,
            "detail": _extract_detail(payload_dict),
            "payload": payload_dict,
        }

    def revoke_invite(self, invite_id: str, *, invite_email: str = "") -> tuple[bool, dict[str, Any]]:
        target_invite_id = str(invite_id or "").strip()
        normalized_email = _normalize_email(invite_email)

        if not normalized_email and target_invite_id:
            invites_ok, invites = self.list_invites()
            if invites_ok:
                for item in invites:
                    candidate_id = str(item.get("id") or item.get("invite_id") or "").strip()
                    if candidate_id != target_invite_id:
                        continue
                    normalized_email = _normalize_email(item.get("email_address") or item.get("email") or "")
                    break

        if not normalized_email:
            return False, {"error": "invite_email_required", "detail": "invite_email is required"}

        delete_status, delete_payload = self._request_json(
            "DELETE",
            f"/backend-api/accounts/{self._chatgpt_account_id}/invites",
            {"email_address": normalized_email},
        )
        delete_result = delete_payload if isinstance(delete_payload, dict) else {"payload": delete_payload}
        delete_result["status_code"] = delete_status
        delete_result["method"] = "DELETE"
        delete_result["email_address"] = normalized_email

        still_exists = False
        verification_error = ""
        for attempt in range(3):
            invites_ok, invites = self.list_invites()
            if not invites_ok:
                verification_error = "invite_verification_failed"
                break
            still_exists = False
            for item in invites:
                candidate_id = str(item.get("id") or item.get("invite_id") or "").strip()
                candidate_email = _normalize_email(item.get("email_address") or item.get("email") or "")
                if target_invite_id and candidate_id == target_invite_id:
                    still_exists = True
                    break
                if candidate_email and candidate_email == normalized_email:
                    still_exists = True
                    break
            if not still_exists:
                break
            if attempt < 2:
                time.sleep(1.0)

        if 200 <= delete_status < 300 and not still_exists:
            return True, delete_result
        if 404 == delete_status and not still_exists:
            delete_result["detail"] = _extract_detail(delete_result) or "invite_not_found"
            return True, delete_result
        if still_exists:
            return False, {
                "error": "revoke_failed",
                "status_code": delete_status,
                "detail": "invite_still_present_after_delete",
                "payload": delete_result,
            }
        return False, {
            "error": "revoke_failed",
            "status_code": delete_status,
            "detail": verification_error or _extract_detail(delete_result),
            "payload": delete_result,
        }

    def remove_user(self, user_id: str) -> tuple[bool, dict[str, Any]]:
        target_user_id = str(user_id or "").strip()
        if not target_user_id:
            return False, {"error": "user_id_required", "detail": "user_id is required"}

        status, payload = self._request_json(
            "DELETE",
            f"/backend-api/accounts/{self._chatgpt_account_id}/users/{target_user_id}",
        )
        result = payload if isinstance(payload, dict) else {"payload": payload}
        result["status_code"] = status
        result["method"] = "DELETE"
        result["user_id"] = target_user_id
        if 200 <= status < 300:
            return True, result
        return False, {
            "error": "remove_user_failed",
            "status_code": status,
            "detail": _extract_detail(result),
            "payload": result,
        }

    def update_invite_seat_type(self, invite_id: str, *, seat_type: str) -> tuple[bool, dict[str, Any]]:
        target_invite_id = str(invite_id or "").strip()
        resolved_seat_type = str(seat_type or "").strip()
        if not target_invite_id:
            return False, {"error": "invite_id_required", "detail": "invite_id is required"}
        if not resolved_seat_type:
            return False, {"error": "seat_type_required", "detail": "seat_type is required"}

        status, payload = self._request_json(
            "PATCH",
            f"/backend-api/accounts/{self._chatgpt_account_id}/invites/{target_invite_id}",
            {"seat_type": resolved_seat_type},
        )
        result = payload if isinstance(payload, dict) else {"payload": payload}
        result["status_code"] = status
        result["method"] = "PATCH"
        result["invite_id"] = target_invite_id
        result["seat_type"] = resolved_seat_type
        if 200 <= status < 300:
            return True, result
        return False, {
            "error": "update_invite_seat_type_failed",
            "status_code": status,
            "detail": _extract_detail(result),
            "payload": result,
        }

    def _headers(self, path: str) -> dict[str, str]:
        headers = {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "authorization": f"Bearer {self._auth_token}",
            "chatgpt-account-id": self._chatgpt_account_id,
            "content-type": "application/json",
            "oai-client-build-number": DEFAULT_CHATGPT_CLIENT_BUILD_NUMBER,
            "oai-client-version": DEFAULT_CHATGPT_CLIENT_VERSION,
            "oai-language": "zh-CN",
            "oai-session-id": self._oai_session_id,
            "origin": CHATGPT_BASE_URL,
            "priority": "u=1, i",
            "referer": f"{CHATGPT_BASE_URL}/admin/members",
            "sec-ch-ua": '"Microsoft Edge";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-openai-target-path": path,
            "x-openai-target-route": _canonical_target_route(path),
        }
        if self._oai_device_id:
            headers["oai-device-id"] = self._oai_device_id
        return headers

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
        url = f"{CHATGPT_BASE_URL}{path}"
        last_status = 599
        last_payload: Any = {}
        for attempt in range(self._max_retries):
            try:
                request_kwargs: dict[str, Any] = {
                    "headers": self._headers(path),
                    "json": payload,
                }
                if self._explicit_proxy:
                    request_kwargs["proxies"] = build_request_proxies(self._explicit_proxy)
                response = self._session.request(method, url, **request_kwargs)
                status = int(getattr(response, "status_code", 599) or 599)
                parsed = _parse_response_payload(response)
                if _should_retry_status(status) and attempt + 1 < self._max_retries:
                    time.sleep(self._retry_backoff * (2**attempt))
                    continue
                return status, parsed
            except Exception as exc:
                last_status = 599
                last_payload = {"error": str(exc)}
                if attempt + 1 < self._max_retries:
                    time.sleep(self._retry_backoff * (2**attempt))
        return last_status, last_payload


def run_register_invite_once(
    *,
    invite_email: str,
    team_auth_path: str | Path | None = None,
    team_auth_payload: dict[str, Any] | None = None,
    force_refresh: bool = False,
    explicit_proxy: str | None = None,
    seat_type: str | None = None,
) -> RegisterInviteResult:
    normalized_email = _normalize_email(invite_email)
    if not normalized_email:
        raise RuntimeError("invite_email_required")

    with flow_network_env():
        if explicit_proxy is None:
            flow_proxy_cm = lease_flow_proxy(
                flow_name="invite",
                metadata={"targetEmail": normalized_email},
                probe_url=f"{CHATGPT_BASE_URL}/",
                probe_expected_statuses={200, 307, 308},
            )
        else:
            flow_proxy_cm = contextlib.nullcontext(SimpleNamespace(proxy_url=explicit_proxy))
        with flow_proxy_cm as flow_proxy:
            explicit_proxy = str(flow_proxy.proxy_url or "").strip() or None
            team_auth, _, _, _ = _load_team_auth_context(
                team_auth_path=team_auth_path,
                team_auth_payload=team_auth_payload,
                force_refresh=force_refresh,
                explicit_proxy=explicit_proxy,
            )
            client = TeamInviteClient(
                auth_token=team_auth["access_token"],
                chatgpt_account_id=team_auth["account_id"],
                oai_device_id=team_auth["oai_device_id"],
                explicit_proxy=str(explicit_proxy or ""),
            )
            try:
                invites_ok, invites = client.list_invites()
                if invites_ok and not _manual_free_oauth_preserve_enabled():
                    for item in list(invites):
                        invited_email = _normalize_email(item.get("email_address") or item.get("email") or "")
                        if invited_email == normalized_email:
                            continue
                        if not _is_codex_invite(item):
                            continue
                        if not _is_stale_pending_invite(
                            item,
                            stale_after_seconds=DEFAULT_TEAM_INVITE_SWEEP_AGE_SECONDS,
                        ):
                            continue
                        client.revoke_invite(
                            _extract_invite_id(item),
                            invite_email=invited_email,
                        )
                    invites_ok, invites = client.list_invites()

                if invites_ok:
                    for item in invites:
                        invited_email = _normalize_email(item.get("email_address") or item.get("email") or "")
                        if invited_email == normalized_email:
                            return RegisterInviteResult(
                                ok=True,
                                status="already_invited",
                                invite_email=normalized_email,
                                team_account_id=team_auth["account_id"],
                                team_email=team_auth["email"],
                                detail="invite_exists",
                                response=item,
                        )

                users_ok, users = client.list_users()
                if users_ok:
                    for item in users:
                        member_email = _normalize_email(item.get("email") or item.get("email_address") or "")
                        if member_email == normalized_email:
                            return RegisterInviteResult(
                                ok=True,
                                status="already_member",
                                invite_email=normalized_email,
                                team_account_id=team_auth["account_id"],
                                team_email=team_auth["email"],
                                detail="member_exists",
                                response=item,
                            )

                if invites_ok and users_ok:
                    codex_users = [
                        item
                        for item in users
                        if _is_codex_user(item, owner_email=team_auth["email"])
                    ]
                    codex_invites = [
                        item
                        for item in invites
                        if _is_codex_invite(item)
                    ]
                    occupied_seats = len(codex_users) + len(codex_invites)
                    if occupied_seats >= DEFAULT_TEAM_CAPACITY_LIMIT:
                        return RegisterInviteResult(
                            ok=False,
                            status="team_seats_full",
                            invite_email=normalized_email,
                            team_account_id=team_auth["account_id"],
                            team_email=team_auth["email"],
                            status_code=0,
                            detail=(
                                f"workspace_capacity_full users={len(codex_users)} "
                                f"pending_invites={len(codex_invites)} limit={DEFAULT_TEAM_CAPACITY_LIMIT}"
                            ),
                            response={
                                "users": len(codex_users),
                                "pending_invites": len(codex_invites),
                                "capacity_limit": DEFAULT_TEAM_CAPACITY_LIMIT,
                                "listed_users": len(users),
                                "listed_pending_invites": len(invites),
                            },
                        )

                ok, result = client.send_invite(normalized_email, seat_type=seat_type)
                status_text = str(
                    result.get("status") or ("invited" if ok else result.get("error") or "invite_failed")
                ).strip()
                return RegisterInviteResult(
                    ok=ok,
                    status=status_text or ("invited" if ok else "invite_failed"),
                    invite_email=normalized_email,
                    team_account_id=team_auth["account_id"],
                    team_email=team_auth["email"],
                    status_code=int(result.get("status_code") or 0),
                    detail=_extract_detail(result),
                    response=result,
                )
            finally:
                client.close()


def run_register_invite_from_path(
    *,
    source_path: str | Path,
    team_auth_path: str | Path | None = None,
    team_auth_payload: dict[str, Any] | None = None,
    force_refresh: bool = False,
    explicit_proxy: str | None = None,
    seat_type: str | None = None,
) -> RegisterInviteResult:
    source_payload = load_json_payload(source_path)
    invite_email = _extract_invite_email(source_payload)
    return run_register_invite_once(
        invite_email=invite_email,
        team_auth_path=team_auth_path,
        team_auth_payload=team_auth_payload,
        force_refresh=force_refresh,
        explicit_proxy=explicit_proxy,
        seat_type=seat_type,
    )


def run_revoke_invite_once(
    *,
    invite_id: str | None = None,
    invite_email: str | None = None,
    member_user_id: str | None = None,
    team_auth_path: str | Path | None = None,
    team_auth_payload: dict[str, Any] | None = None,
    force_refresh: bool = False,
    explicit_proxy: str | None = None,
) -> RevokeInviteResult:
    normalized_invite_id = str(invite_id or "").strip()
    normalized_email = _normalize_email(invite_email)
    normalized_member_user_id = str(member_user_id or "").strip()
    if not normalized_invite_id and not normalized_email and not normalized_member_user_id:
        raise RuntimeError("invite_id_or_email_required")

    with flow_network_env():
        if explicit_proxy is None:
            flow_proxy_cm = lease_flow_proxy(
                flow_name="revoke",
                metadata={
                    "targetEmail": normalized_email,
                    "memberUserId": normalized_member_user_id,
                },
                probe_url=f"{CHATGPT_BASE_URL}/",
                probe_expected_statuses={200, 307, 308},
            )
        else:
            flow_proxy_cm = contextlib.nullcontext(SimpleNamespace(proxy_url=explicit_proxy))
        with flow_proxy_cm as flow_proxy:
            explicit_proxy = str(flow_proxy.proxy_url or "").strip() or None
            team_auth, _, _, _ = _load_team_auth_context(
                team_auth_path=team_auth_path,
                team_auth_payload=team_auth_payload,
                force_refresh=force_refresh,
                explicit_proxy=explicit_proxy,
            )
            client = TeamInviteClient(
                auth_token=team_auth["access_token"],
                chatgpt_account_id=team_auth["account_id"],
                oai_device_id=team_auth["oai_device_id"],
                explicit_proxy=str(explicit_proxy or ""),
            )

            def _match_user_id_by_email(users: list[dict[str, Any]]) -> str:
                if not normalized_email:
                    return ""
                for item in users:
                    member_email = _normalize_email(item.get("email") or item.get("email_address") or "")
                    if member_email != normalized_email:
                        continue
                    user_id = _extract_user_id(item)
                    if user_id:
                        return user_id
                return ""

            def _match_invite_id_by_email(invites: list[dict[str, Any]]) -> str:
                if not normalized_email:
                    return ""
                for item in invites:
                    invite_email_text = _normalize_email(item.get("email_address") or item.get("email") or "")
                    if invite_email_text != normalized_email:
                        continue
                    invite_id_text = str(item.get("invite_id") or item.get("id") or item.get("invite") or "").strip()
                    if invite_id_text:
                        return invite_id_text
                return ""

            def _verify_email_cleanup() -> dict[str, Any]:
                if not normalized_email:
                    return {"checked": False, "email": ""}
                verification: dict[str, Any] = {
                    "checked": True,
                    "email": normalized_email,
                    "verified_absent": False,
                    "user_present": False,
                    "invite_present": False,
                    "user_id": "",
                    "invite_id": "",
                    "attempts": 0,
                }
                for attempt in range(1, 4):
                    users_ok, users = client.list_users()
                    invites_ok, invites = client.list_invites()
                    matched_user_id = _match_user_id_by_email(users if users_ok else [])
                    matched_invite_id = _match_invite_id_by_email(invites if invites_ok else [])
                    verification = {
                        "checked": True,
                        "email": normalized_email,
                        "verified_absent": bool(users_ok and invites_ok and not matched_user_id and not matched_invite_id),
                        "user_present": bool(matched_user_id),
                        "invite_present": bool(matched_invite_id),
                        "user_id": matched_user_id,
                        "invite_id": matched_invite_id,
                        "users_ok": bool(users_ok),
                        "invites_ok": bool(invites_ok),
                        "attempts": attempt,
                    }
                    if verification["verified_absent"]:
                        return verification
                    if matched_user_id:
                        client.remove_user(matched_user_id)
                    if matched_invite_id:
                        client.revoke_invite(matched_invite_id, invite_email=normalized_email)
                    time.sleep(1.0)
                return verification

            def _wrap_revoke_response(result: Any) -> Any:
                verification = _verify_email_cleanup()
                if not verification.get("checked"):
                    return result
                if isinstance(result, dict):
                    return {
                        "primary": result,
                        "post_verify": verification,
                    }
                return {
                    "primary": result,
                    "post_verify": verification,
                }
            try:
                if normalized_member_user_id:
                    ok, result = client.remove_user(normalized_member_user_id)
                    if ok:
                        return RevokeInviteResult(
                            ok=True,
                            status="removed_member",
                            invite_id=normalized_member_user_id,
                            invite_email=normalized_email,
                            team_account_id=team_auth["account_id"],
                            team_email=team_auth["email"],
                            status_code=int(result.get("status_code") or 0),
                            detail=_extract_detail(result),
                            response=_wrap_revoke_response(result),
                        )

                if normalized_email:
                    users_ok, users = client.list_users()
                    if users_ok:
                        for item in users:
                            member_email = _normalize_email(item.get("email") or item.get("email_address") or "")
                            if member_email != normalized_email:
                                continue
                            user_id = _extract_user_id(item)
                            if user_id:
                                ok, result = client.remove_user(user_id)
                                return RevokeInviteResult(
                                    ok=ok,
                                    status=(
                                        "removed_member"
                                        if ok
                                        else str(result.get("error") or "remove_user_failed").strip()
                                        or "remove_user_failed"
                                    ),
                                invite_id=str(user_id),
                                invite_email=normalized_email,
                                team_account_id=team_auth["account_id"],
                                team_email=team_auth["email"],
                                status_code=int(result.get("status_code") or 0),
                                detail=_extract_detail(result),
                                response=_wrap_revoke_response(result) if ok else result,
                            )

                matched_item: dict[str, Any] | None = None
                if not normalized_invite_id:
                    invites_ok, invites = client.list_invites()
                    if not invites_ok:
                        return RevokeInviteResult(
                            ok=False,
                            status="invite_lookup_failed",
                            invite_id="",
                            invite_email=normalized_email,
                            team_account_id=team_auth["account_id"],
                            team_email=team_auth["email"],
                            detail="unable_to_list_invites",
                        )
                    for item in invites:
                        candidate_email = _normalize_email(item.get("email_address") or item.get("email") or "")
                        if candidate_email == normalized_email:
                            candidate_id = str(
                                item.get("invite_id") or item.get("id") or item.get("invite") or ""
                            ).strip()
                            if candidate_id:
                                normalized_invite_id = candidate_id
                                matched_item = item
                                break
                    if not normalized_invite_id:
                        return RevokeInviteResult(
                            ok=True,
                            status="not_found",
                            invite_id="",
                            invite_email=normalized_email,
                            team_account_id=team_auth["account_id"],
                            team_email=team_auth["email"],
                            detail="no_pending_invite_or_member",
                        )

                ok, result = client.revoke_invite(normalized_invite_id, invite_email=normalized_email)
                if (not ok) and int(result.get("status_code") or 0) == 404 and normalized_email:
                    for _ in range(3):
                        users_ok, users = client.list_users()
                        if users_ok:
                            matched_user_id = ""
                            for item in users:
                                member_email = _normalize_email(item.get("email") or item.get("email_address") or "")
                                if member_email != normalized_email:
                                    continue
                                matched_user_id = _extract_user_id(item)
                                if matched_user_id:
                                    break
                            if matched_user_id:
                                member_ok, member_result = client.remove_user(matched_user_id)
                                return RevokeInviteResult(
                                    ok=member_ok,
                                    status=(
                                        "removed_member"
                                        if member_ok
                                        else str(member_result.get("error") or "remove_user_failed").strip()
                                        or "remove_user_failed"
                                    ),
                                    invite_id=matched_user_id,
                                    invite_email=normalized_email,
                                    team_account_id=team_auth["account_id"],
                                    team_email=team_auth["email"],
                                    status_code=int(member_result.get("status_code") or 0),
                                    detail=_extract_detail(member_result),
                                    response=_wrap_revoke_response(member_result) if member_ok else member_result,
                                )
                        time.sleep(2.0)
                resolved_email = normalized_email or _normalize_email(
                    (matched_item or {}).get("email_address") or (matched_item or {}).get("email") or ""
                )
                return RevokeInviteResult(
                    ok=ok,
                    status="revoked" if ok else str(result.get("error") or "revoke_failed").strip() or "revoke_failed",
                    invite_id=normalized_invite_id,
                    invite_email=resolved_email,
                    team_account_id=team_auth["account_id"],
                    team_email=team_auth["email"],
                    status_code=int(result.get("status_code") or 0),
                    detail=_extract_detail(result),
                    response=_wrap_revoke_response(result) if ok else result,
                )
            finally:
                client.close()


def run_update_team_seat_once(
    *,
    invite_id: str | None = None,
    user_id: str | None = None,
    member_email: str | None = None,
    seat_type: str,
    team_auth_path: str | Path | None = None,
    team_auth_payload: dict[str, Any] | None = None,
    force_refresh: bool = False,
    explicit_proxy: str | None = None,
) -> UpdateTeamSeatResult:
    normalized_invite_id = str(invite_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    normalized_email = _normalize_email(member_email)
    normalized_seat_type = _normalize_seat_type(seat_type)
    if not normalized_invite_id and not normalized_user_id and not normalized_email:
        raise RuntimeError("invite_id_or_user_id_or_member_email_required")
    if not normalized_seat_type:
        raise RuntimeError("seat_type_required")
    lookup_timeout_seconds = DEFAULT_TEAM_SEAT_LOOKUP_TIMEOUT_SECONDS
    lookup_interval_seconds = DEFAULT_TEAM_SEAT_LOOKUP_INTERVAL_SECONDS

    with flow_network_env():
        if explicit_proxy is None:
            flow_proxy_cm = lease_flow_proxy(
                flow_name="update_team_seat",
                metadata={
                    "inviteId": normalized_invite_id,
                    "memberEmail": normalized_email,
                    "userId": normalized_user_id,
                    "seatType": normalized_seat_type,
                },
                probe_url=f"{CHATGPT_BASE_URL}/",
                probe_expected_statuses={200, 307, 308},
            )
        else:
            flow_proxy_cm = contextlib.nullcontext(SimpleNamespace(proxy_url=explicit_proxy))
        with flow_proxy_cm as flow_proxy:
            explicit_proxy = str(flow_proxy.proxy_url or "").strip() or None
            team_auth, _, _, _ = _load_team_auth_context(
                team_auth_path=team_auth_path,
                team_auth_payload=team_auth_payload,
                force_refresh=force_refresh,
                explicit_proxy=explicit_proxy,
            )
            client = TeamInviteClient(
                auth_token=team_auth["access_token"],
                chatgpt_account_id=team_auth["account_id"],
                oai_device_id=team_auth["oai_device_id"],
                explicit_proxy=str(explicit_proxy or ""),
            )
            try:
                if normalized_invite_id:
                    ok, result = client.update_invite_seat_type(
                        normalized_invite_id,
                        seat_type=normalized_seat_type,
                    )
                    return UpdateTeamSeatResult(
                        ok=ok,
                        status="updated" if ok else "update_failed",
                        invite_id=normalized_invite_id,
                        user_id="",
                        member_email=normalized_email,
                        seat_type=normalized_seat_type,
                        team_account_id=team_auth["account_id"],
                        team_email=team_auth["email"],
                        status_code=int(result.get("status_code") or 0),
                        detail=_extract_detail(result),
                        response=result,
                    )
                if not normalized_user_id and normalized_email:
                    deadline = time.monotonic() + float(lookup_timeout_seconds)
                    last_lookup_detail = "list_users_failed"
                    last_users_snapshot: dict[str, Any] | None = None
                    attempt = 0
                    while True:
                        attempt += 1
                        users_ok, users = client.list_users()
                        if users_ok:
                            last_lookup_detail = "member_user_id_unavailable"
                            last_users_snapshot = {
                                "attempt": attempt,
                                "users_count": len(users),
                            }
                            for item in users:
                                candidate_email = _normalize_email(item.get("email") or item.get("email_address") or "")
                                if candidate_email != normalized_email:
                                    continue
                                normalized_user_id = _extract_user_id(item)
                                if normalized_user_id:
                                    break
                            if normalized_user_id:
                                break
                        if time.monotonic() >= deadline:
                            break
                        time.sleep(float(lookup_interval_seconds))
                    if not normalized_user_id and last_lookup_detail == "list_users_failed":
                        return UpdateTeamSeatResult(
                            ok=False,
                            status="user_lookup_failed",
                            invite_id="",
                            user_id="",
                            member_email=normalized_email,
                            seat_type=normalized_seat_type,
                            team_account_id=team_auth["account_id"],
                            team_email=team_auth["email"],
                            status_code=0,
                            detail="list_users_failed_after_retry",
                            response=last_users_snapshot,
                        )
                if not normalized_user_id:
                    return UpdateTeamSeatResult(
                        ok=False,
                        status="user_not_found",
                        invite_id="",
                        user_id="",
                        member_email=normalized_email,
                        seat_type=normalized_seat_type,
                        team_account_id=team_auth["account_id"],
                        team_email=team_auth["email"],
                        status_code=0,
                        detail=f"member_user_id_unavailable_after_wait:{lookup_timeout_seconds}s",
                        response={
                            "lookupTimeoutSeconds": lookup_timeout_seconds,
                            "lookupIntervalSeconds": lookup_interval_seconds,
                        },
                    )

                return UpdateTeamSeatResult(
                    ok=False,
                    status="user_upgrade_path_disabled",
                    invite_id="",
                    user_id=normalized_user_id,
                    member_email=normalized_email,
                    seat_type=normalized_seat_type,
                    team_account_id=team_auth["account_id"],
                    team_email=team_auth["email"],
                    status_code=0,
                    detail="invite_id_required_for_seat_upgrade",
                    response=None,
                )
            finally:
                client.close()


def run_revoke_invite_from_path(
    *,
    source_path: str | Path,
    team_auth_path: str | Path | None = None,
    team_auth_payload: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> RevokeInviteResult:
    source_payload = load_json_payload(source_path)
    invite_email = _extract_invite_email(source_payload)
    member_user_id = _extract_member_user_id_from_seed_payload(source_payload)
    return run_revoke_invite_once(
        invite_email=invite_email,
        member_user_id=member_user_id or None,
        team_auth_path=team_auth_path,
        team_auth_payload=team_auth_payload,
        force_refresh=force_refresh,
    )


def run_cleanup_codex_capacity_once(
    *,
    team_auth_path: str | Path | None = None,
    team_auth_payload: dict[str, Any] | None = None,
    force_refresh: bool = False,
    explicit_proxy: str | None = None,
) -> CleanupCodexCapacityResult:
    with flow_network_env():
        if explicit_proxy is None:
            flow_proxy_cm = lease_flow_proxy(
                flow_name="cleanup_codex_capacity",
                metadata={"operation": "cleanup_codex_capacity"},
                probe_url=f"{CHATGPT_BASE_URL}/",
                probe_expected_statuses={200, 307, 308},
            )
        else:
            flow_proxy_cm = contextlib.nullcontext(SimpleNamespace(proxy_url=explicit_proxy))
        with flow_proxy_cm as flow_proxy:
            explicit_proxy = str(flow_proxy.proxy_url or "").strip() or None
            team_auth, _, _, _ = _load_team_auth_context(
                team_auth_path=team_auth_path,
                team_auth_payload=team_auth_payload,
                force_refresh=force_refresh,
                explicit_proxy=explicit_proxy,
            )
            client = TeamInviteClient(
                auth_token=team_auth["access_token"],
                chatgpt_account_id=team_auth["account_id"],
                oai_device_id=team_auth["oai_device_id"],
                explicit_proxy=str(explicit_proxy or ""),
            )
            try:
                invites_ok, invites = client.list_invites()
                users_ok, users = client.list_users()
                if not invites_ok and not users_ok:
                    return CleanupCodexCapacityResult(
                        ok=False,
                        status="cleanup_lookup_failed",
                        team_account_id=team_auth["account_id"],
                        team_email=team_auth["email"],
                        detail="unable_to_list_invites_and_users",
                    )

                current_entries: list[dict[str, Any]] = []
                for item in invites if invites_ok else []:
                    entry = _team_seat_entry_from_invite(item)
                    if entry:
                        current_entries.append(entry)
                for item in users if users_ok else []:
                    entry = _team_seat_entry_from_user(item, owner_email=team_auth["email"])
                    if entry:
                        current_entries.append(entry)

                revoked_invites = 0
                removed_users = 0
                skipped_invites = 0
                skipped_users = 0
                failures: list[str] = []
                operations: list[dict[str, Any]] = []
                actual_remaining_entries = list(current_entries)

                for item in invites if invites_ok else []:
                    if not _is_codex_invite(item):
                        skipped_invites += 1
                        continue
                    invite_id = _extract_invite_id(item)
                    invite_email = _normalize_email(item.get("email_address") or item.get("email") or "")
                    ok, result = client.revoke_invite(invite_id, invite_email=invite_email)
                    operations.append(
                        {
                            "kind": "invite",
                            "id": invite_id,
                            "email": invite_email,
                            "seat_type": _normalize_seat_type(item.get("seat_type") or item.get("seatType")),
                            "ok": bool(ok),
                            "result": result,
                        }
                    )
                    if ok:
                        revoked_invites += 1
                        actual_remaining_entries = _team_seat_remove_matching_entry(actual_remaining_entries, operations[-1])
                    else:
                        failures.append(f"invite:{invite_email or invite_id}:{_extract_detail(result)}")

                for item in users if users_ok else []:
                    if not _is_codex_user(item, owner_email=team_auth["email"]):
                        skipped_users += 1
                        continue
                    user_id = _extract_user_id(item)
                    user_email = _normalize_email(item.get("email") or item.get("email_address") or "")
                    ok, result = client.remove_user(user_id)
                    operations.append(
                        {
                            "kind": "user",
                            "id": user_id,
                            "email": user_email,
                            "seat_type": _normalize_seat_type(item.get("seat_type") or item.get("seatType")),
                            "role": str(item.get("role") or "").strip(),
                            "ok": bool(ok),
                            "result": result,
                        }
                    )
                    if ok:
                        removed_users += 1
                        actual_remaining_entries = _team_seat_remove_matching_entry(actual_remaining_entries, operations[-1])
                    elif int(result.get("status_code") or 0) == 404:
                        skipped_users += 1
                        actual_remaining_entries = _team_seat_remove_matching_entry(actual_remaining_entries, operations[-1])
                    else:
                        failures.append(f"user:{user_email or user_id}:{_extract_detail(result)}")

                return CleanupCodexCapacityResult(
                    ok=not failures,
                    status="cleaned" if not failures else "cleanup_partial_failed",
                    team_account_id=team_auth["account_id"],
                    team_email=team_auth["email"],
                    revoked_invites=revoked_invites,
                    removed_users=removed_users,
                    skipped_invites=skipped_invites,
                    skipped_users=skipped_users,
                    detail="; ".join(failures),
                    response={
                        "operations": operations,
                        "listedInvites": len(invites) if invites_ok else None,
                        "listedUsers": len(users) if users_ok else None,
                        "seatSnapshotBefore": _team_seat_snapshot(current_entries),
                        "seatSnapshotAfterProjected": _team_seat_snapshot(actual_remaining_entries),
                    },
                )
            finally:
                client.close()


def run_cleanup_team_seats_once(
    *,
    team_auth_path: str | Path | None = None,
    team_auth_payload: dict[str, Any] | None = None,
    force_refresh: bool = False,
    explicit_proxy: str | None = None,
    preserve_member_emails: list[str] | None = None,
    required_chatgpt_seats: int | None = None,
) -> CleanupTeamSeatsResult:
    with flow_network_env():
        if explicit_proxy is None:
            flow_proxy_cm = lease_flow_proxy(
                flow_name="cleanup_team_all_seats",
                metadata={"operation": "cleanup_team_all_seats"},
                probe_url=f"{CHATGPT_BASE_URL}/",
                probe_expected_statuses={200, 307, 308},
            )
        else:
            flow_proxy_cm = contextlib.nullcontext(SimpleNamespace(proxy_url=explicit_proxy))
        with flow_proxy_cm as flow_proxy:
            explicit_proxy = str(flow_proxy.proxy_url or "").strip() or None
            team_auth, _, _, _ = _load_team_auth_context(
                team_auth_path=team_auth_path,
                team_auth_payload=team_auth_payload,
                force_refresh=force_refresh,
                explicit_proxy=explicit_proxy,
            )
            client = TeamInviteClient(
                auth_token=team_auth["access_token"],
                chatgpt_account_id=team_auth["account_id"],
                oai_device_id=team_auth["oai_device_id"],
                explicit_proxy=str(explicit_proxy or ""),
            )
            try:
                invites_ok, invites = client.list_invites()
                users_ok, users = client.list_users()
                if not invites_ok or not users_ok:
                    failed_lookups: list[str] = []
                    if not invites_ok:
                        failed_lookups.append("invites")
                    if not users_ok:
                        failed_lookups.append("users")
                    return CleanupTeamSeatsResult(
                        ok=False,
                        status="cleanup_lookup_failed",
                        team_account_id=team_auth["account_id"],
                        team_email=team_auth["email"],
                        detail=f"unable_to_list_{'_and_'.join(failed_lookups)}",
                        response={
                            "listedInvites": len(invites) if invites_ok else None,
                            "listedUsers": len(users) if users_ok else None,
                        },
                    )

                removable_users = [
                    item
                    for item in users
                    if not _is_owner_user(item, owner_email=team_auth["email"])
                ]
                preserved_owner_users = max(0, len(users) - len(removable_users))
                current_entries: list[dict[str, Any]] = []
                for item in invites:
                    entry = _team_seat_entry_from_invite(item)
                    if entry:
                        current_entries.append(entry)
                for item in removable_users:
                    entry = _team_seat_entry_from_user(item, owner_email=team_auth["email"])
                    if entry:
                        current_entries.append(entry)
                seat_snapshot_before = _team_seat_snapshot(current_entries)
                seat_summary_before = seat_snapshot_before.get("summary") if isinstance(seat_snapshot_before, dict) else {}
                preserved_email_set = {
                    _normalize_email(item)
                    for item in (preserve_member_emails or [])
                    if _normalize_email(item)
                }
                had_existing_seats = bool(invites or removable_users)
                if not had_existing_seats:
                    return CleanupTeamSeatsResult(
                        ok=True,
                        status="idle",
                        team_account_id=team_auth["account_id"],
                        team_email=team_auth["email"],
                        had_existing_seats=False,
                        detail="no_existing_team_seats",
                        response={
                            "operations": [],
                            "listedInvites": len(invites),
                            "listedUsers": len(users),
                            "preservedOwnerUsers": preserved_owner_users,
                            "seatSnapshotBefore": seat_snapshot_before,
                            "seatSnapshotAfterProjected": seat_snapshot_before,
                        },
                    )

                normalized_required_chatgpt_seats = max(
                    0,
                    int(DEFAULT_TEAM_EXPAND_REQUIRED_CHATGPT_SEATS if required_chatgpt_seats is None else required_chatgpt_seats),
                )
                target_used_chatgpt = max(0, DEFAULT_TEAM_CHATGPT_SEAT_LIMIT - normalized_required_chatgpt_seats)
                target_used_total = max(0, DEFAULT_TEAM_CAPACITY_LIMIT - normalized_required_chatgpt_seats)
                if (
                    int(seat_summary_before.get("available_total") or 0) >= normalized_required_chatgpt_seats
                    and int(seat_summary_before.get("available_chatgpt") or 0) >= normalized_required_chatgpt_seats
                ):
                    return CleanupTeamSeatsResult(
                        ok=True,
                        status="capacity_already_available",
                        team_account_id=team_auth["account_id"],
                        team_email=team_auth["email"],
                        had_existing_seats=True,
                        detail="enough_team_capacity_for_expand",
                        response={
                            "operations": [],
                            "listedInvites": len(invites),
                            "listedUsers": len(users),
                            "preservedOwnerUsers": preserved_owner_users,
                            "requiredChatgptSeats": normalized_required_chatgpt_seats,
                            "targetUsedChatgptSeats": target_used_chatgpt,
                            "targetUsedTotalSeats": target_used_total,
                            "preservedMemberEmails": sorted(preserved_email_set),
                            "seatSnapshotBefore": seat_snapshot_before,
                            "seatSnapshotAfterProjected": seat_snapshot_before,
                        },
                    )

                def _entry_sort_key(entry: dict[str, Any], *, prefer_chatgpt: bool) -> tuple[int, int, str]:
                    category = str(entry.get("seat_category") or "").strip().lower()
                    kind = str(entry.get("kind") or "").strip().lower()
                    if prefer_chatgpt:
                        category_rank = 0 if category == "chatgpt" else 1
                    else:
                        category_rank = 0 if category == "codex" else 1
                    kind_rank = 0 if kind == "invite" else 1
                    return (category_rank, kind_rank, str(entry.get("email") or entry.get("invite_email") or "").strip().lower())

                planned_remaining_entries = list(current_entries)
                planned_removals: list[dict[str, Any]] = []

                while int(_team_seat_summary_from_entries(planned_remaining_entries).get("used_chatgpt") or 0) > target_used_chatgpt:
                    chatgpt_candidates = [
                        item
                        for item in planned_remaining_entries
                        if str(item.get("seat_category") or "").strip().lower() == "chatgpt"
                        and _normalize_email(item.get("invite_email") or item.get("email") or "") not in preserved_email_set
                    ]
                    if not chatgpt_candidates:
                        break
                    candidate = sorted(chatgpt_candidates, key=lambda item: _entry_sort_key(item, prefer_chatgpt=True))[0]
                    planned_removals.append(candidate)
                    planned_remaining_entries = _team_seat_remove_matching_entry(
                        planned_remaining_entries,
                        {
                            "kind": candidate.get("kind"),
                            "id": candidate.get("invite_id") if str(candidate.get("kind") or "").strip().lower() == "invite" else candidate.get("member_user_id"),
                            "email": candidate.get("invite_email") or candidate.get("email"),
                        },
                    )

                while int(_team_seat_summary_from_entries(planned_remaining_entries).get("used_total") or 0) > target_used_total:
                    total_candidates = sorted(
                        [
                            item
                            for item in planned_remaining_entries
                            if _normalize_email(item.get("invite_email") or item.get("email") or "") not in preserved_email_set
                        ],
                        key=lambda item: _entry_sort_key(item, prefer_chatgpt=False),
                    )
                    if not total_candidates:
                        break
                    candidate = total_candidates[0]
                    planned_removals.append(candidate)
                    planned_remaining_entries = _team_seat_remove_matching_entry(
                        planned_remaining_entries,
                        {
                            "kind": candidate.get("kind"),
                            "id": candidate.get("invite_id") if str(candidate.get("kind") or "").strip().lower() == "invite" else candidate.get("member_user_id"),
                            "email": candidate.get("invite_email") or candidate.get("email"),
                        },
                    )

                revoked_invites = 0
                removed_users = 0
                skipped_invites = 0
                skipped_users = 0
                failures: list[str] = []
                operations: list[dict[str, Any]] = []
                actual_remaining_entries = list(current_entries)

                for planned in planned_removals:
                    kind = str(planned.get("kind") or "").strip().lower()
                    seat_type = _normalize_seat_type(planned.get("seat_type") or "")
                    if kind == "invite":
                        invite_id = str(planned.get("invite_id") or "").strip()
                        invite_email = _normalize_email(planned.get("invite_email") or planned.get("email") or "")
                        ok, result = client.revoke_invite(invite_id, invite_email=invite_email)
                        operation = {
                            "kind": "invite",
                            "id": invite_id,
                            "email": invite_email,
                            "seat_type": seat_type,
                            "ok": bool(ok),
                            "result": result,
                        }
                        operations.append(operation)
                        if ok:
                            revoked_invites += 1
                            actual_remaining_entries = _team_seat_remove_matching_entry(actual_remaining_entries, operation)
                        else:
                            failures.append(f"invite:{invite_email or invite_id}:{_extract_detail(result)}")
                    else:
                        user_id = str(planned.get("member_user_id") or "").strip()
                        user_email = _normalize_email(planned.get("invite_email") or planned.get("email") or "")
                        ok, result = client.remove_user(user_id)
                        operation = {
                            "kind": "user",
                            "id": user_id,
                            "email": user_email,
                            "seat_type": seat_type,
                            "role": str(planned.get("role") or "").strip(),
                            "ok": bool(ok),
                            "result": result,
                        }
                        operations.append(operation)
                        if ok:
                            removed_users += 1
                            actual_remaining_entries = _team_seat_remove_matching_entry(actual_remaining_entries, operation)
                        elif int(result.get("status_code") or 0) == 404:
                            skipped_users += 1
                            actual_remaining_entries = _team_seat_remove_matching_entry(actual_remaining_entries, operation)
                        else:
                            failures.append(f"user:{user_email or user_id}:{_extract_detail(result)}")

                return CleanupTeamSeatsResult(
                    ok=not failures,
                    status="cleaned" if not failures else "cleanup_partial_failed",
                    team_account_id=team_auth["account_id"],
                    team_email=team_auth["email"],
                    revoked_invites=revoked_invites,
                    removed_users=removed_users,
                    skipped_invites=skipped_invites,
                    skipped_users=skipped_users,
                    had_existing_seats=True,
                    detail="; ".join(failures),
                    response={
                        "operations": operations,
                        "listedInvites": len(invites),
                        "listedUsers": len(users),
                        "preservedOwnerUsers": preserved_owner_users,
                        "requiredChatgptSeats": normalized_required_chatgpt_seats,
                        "targetUsedChatgptSeats": target_used_chatgpt,
                        "targetUsedTotalSeats": target_used_total,
                        "preservedMemberEmails": sorted(preserved_email_set),
                        "plannedRemovalCount": len(planned_removals),
                        "seatSnapshotBefore": seat_snapshot_before,
                        "seatSnapshotAfterProjected": _team_seat_snapshot(actual_remaining_entries),
                    },
                )
            finally:
                client.close()


def refresh_team_auth_once(
    *,
    team_auth_path: str | Path | None = None,
    team_auth_payload: dict[str, Any] | None = None,
    force: bool = False,
    explicit_proxy: str | None = None,
) -> RefreshTeamAuthResult:
    resolved_path = _resolve_optional_team_auth_path(team_auth_path, team_auth_payload)
    raw_payload = _load_team_auth_payload(team_auth_path=resolved_path, team_auth_payload=team_auth_payload)
    normalized = _normalize_team_auth_payload(raw_payload, validate_expiry=False)
    expires_at = _resolve_team_auth_expiration(raw_payload)
    due_for_refresh = _team_auth_needs_refresh(raw_payload)

    if not force and not due_for_refresh:
        return RefreshTeamAuthResult(
            ok=True,
            refreshed=False,
            storage_path=str(resolved_path or ""),
            team_email=normalized["email"],
            team_account_id=normalized["account_id"],
            expired=_format_datetime_text(expires_at),
            last_refresh=str(raw_payload.get("last_refresh") or "").strip(),
            auth_payload=raw_payload,
            detail="refresh_not_needed",
        )

    refresh_token = normalized["refresh_token"]
    if not refresh_token:
        raise RuntimeError("team_refresh_token_required")

    client_id = normalized["client_id"] or DEFAULT_REFRESH_CLIENT_ID
    if explicit_proxy is None:
        with flow_network_env():
            with lease_flow_proxy(
                flow_name="team_auth_refresh",
                metadata={
                    "teamEmail": normalized["email"],
                    "teamAccountId": normalized["account_id"],
                },
                probe_url=f"{CHATGPT_BASE_URL}/",
                probe_expected_statuses={200, 307, 308},
            ) as flow_proxy:
                token_response = _perform_refresh_token_exchange(
                    refresh_token=refresh_token,
                    client_id=client_id,
                    explicit_proxy=str(flow_proxy.proxy_url or "").strip() or None,
                )
    else:
        token_response = _perform_refresh_token_exchange(
            refresh_token=refresh_token,
            client_id=client_id,
            explicit_proxy=explicit_proxy,
        )

    refreshed_payload = dict(raw_payload)
    refreshed_payload["access_token"] = _extract_bearer(str(token_response.get("access_token") or ""))
    refreshed_payload["refresh_token"] = str(token_response.get("refresh_token") or refresh_token).strip()
    if str(token_response.get("id_token") or "").strip():
        refreshed_payload["id_token"] = str(token_response.get("id_token") or "").strip()
    refreshed_payload["last_refresh"] = _format_datetime_text(datetime.now().astimezone())

    refreshed_claims = _decode_jwt_payload(refreshed_payload["access_token"])
    refreshed_auth_claims = refreshed_claims.get("https://api.openai.com/auth", {}) if isinstance(refreshed_claims, dict) else {}
    refreshed_payload["account_id"] = str(
        refreshed_payload.get("account_id")
        or refreshed_payload.get("chatgpt_account_id")
        or refreshed_auth_claims.get("chatgpt_account_id")
        or normalized["account_id"]
    ).strip()
    refreshed_payload["email"] = _normalize_email(refreshed_payload.get("email") or refreshed_claims.get("email") or normalized["email"])
    refreshed_payload["client_id"] = client_id

    new_expiration = _resolve_team_auth_expiration(refreshed_payload)
    expires_in = int(token_response.get("expires_in") or 0)
    if new_expiration is None and expires_in > 0:
        new_expiration = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    if new_expiration is not None:
        refreshed_payload["expired"] = _format_datetime_text(new_expiration)

    final_normalized = _normalize_team_auth_payload(refreshed_payload, validate_expiry=True)
    storage_path = ""
    if resolved_path is not None:
        resolved_path.write_text(json.dumps(refreshed_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        storage_path = str(resolved_path)

    return RefreshTeamAuthResult(
        ok=True,
        refreshed=True,
        storage_path=storage_path,
        team_email=final_normalized["email"],
        team_account_id=final_normalized["account_id"],
        expired=str(refreshed_payload.get("expired") or ""),
        last_refresh=str(refreshed_payload.get("last_refresh") or ""),
        auth_payload=refreshed_payload,
        detail="refresh_succeeded",
        response=token_response,
    )


def resolve_team_auth_path(team_auth_path: str | Path | None = None) -> Path:
    explicit_path = str(team_auth_path or "").strip()
    if explicit_path:
        return _resolve_team_auth_from_pathlike(explicit_path, source="argument")

    env_path = str(os.environ.get(DEFAULT_TEAM_AUTH_PATH_ENV) or "").strip()
    if env_path:
        return _resolve_team_auth_from_pathlike(env_path, source=DEFAULT_TEAM_AUTH_PATH_ENV)

    for candidate_dir in _iter_team_auth_search_dirs():
        resolved = _find_team_auth_in_dir(candidate_dir)
        if resolved is not None:
            return resolved

    searched = [str(path) for path in _iter_team_auth_search_dirs()]
    raise RuntimeError(
        "team_auth_file_not_found: "
        + ", ".join(searched or [str(DEFAULT_TEAM_AUTH_DEFAULT_DIR)])
    )


def _resolve_team_auth_glob() -> str:
    return str(os.environ.get(DEFAULT_TEAM_AUTH_GLOB_ENV) or "").strip() or DEFAULT_TEAM_AUTH_GLOB


def _iter_team_auth_search_dirs() -> list[Path]:
    candidates: list[Path] = []

    env_dir = str(os.environ.get(DEFAULT_TEAM_AUTH_DIR_ENV) or "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser())

    env_dirs = str(os.environ.get(DEFAULT_TEAM_AUTH_DIRS_ENV) or "").strip()
    if env_dirs:
        for raw in env_dirs.split(os.pathsep):
            normalized = str(raw or "").strip()
            if normalized:
                candidates.append(Path(normalized).expanduser())

    candidates.extend([
        DEFAULT_TEAM_AUTH_DEFAULT_DIR,
        OTHERS_DIR,
    ])

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(resolved)
    return deduped


def _resolve_team_auth_from_pathlike(value: str, *, source: str) -> Path:
    candidate = Path(value).expanduser().resolve()
    if candidate.is_file():
        return candidate
    if candidate.is_dir():
        resolved = _find_team_auth_in_dir(candidate)
        if resolved is not None:
            return resolved
        raise RuntimeError(f"team_auth_file_not_found: {candidate}")
    raise RuntimeError(f"team_auth_file_not_found: {candidate}")


def _find_team_auth_in_dir(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    pattern = _resolve_team_auth_glob()
    candidates = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _load_team_auth_context(
    *,
    team_auth_path: str | Path | None,
    team_auth_payload: dict[str, Any] | None,
    force_refresh: bool,
    explicit_proxy: str | None = None,
) -> tuple[dict[str, str], dict[str, Any], Path | None, RefreshTeamAuthResult | None]:
    resolved_path = _resolve_optional_team_auth_path(team_auth_path, team_auth_payload)
    raw_payload = _load_team_auth_payload(team_auth_path=resolved_path, team_auth_payload=team_auth_payload)
    refresh_result: RefreshTeamAuthResult | None = None
    if force_refresh or _team_auth_needs_refresh(raw_payload):
        refresh_result = refresh_team_auth_once(
            team_auth_path=resolved_path,
            team_auth_payload=raw_payload,
            force=force_refresh,
            explicit_proxy=explicit_proxy,
        )
        raw_payload = refresh_result.auth_payload
        if refresh_result.storage_path:
            resolved_path = Path(refresh_result.storage_path)
    normalized = _normalize_team_auth_payload(raw_payload, validate_expiry=True)
    return normalized, raw_payload, resolved_path, refresh_result


def _resolve_optional_team_auth_path(
    team_auth_path: str | Path | None,
    team_auth_payload: dict[str, Any] | None,
) -> Path | None:
    if team_auth_payload is not None:
        if team_auth_path is not None and str(team_auth_path).strip():
            try:
                return resolve_team_auth_path(team_auth_path)
            except Exception:
                return None
        return None
    if team_auth_path is not None and str(team_auth_path).strip():
        return resolve_team_auth_path(team_auth_path)
    return resolve_team_auth_path(None)


def _load_team_auth_payload(
    *,
    team_auth_path: str | Path | None,
    team_auth_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if team_auth_payload is not None:
        if not isinstance(team_auth_payload, dict):
            raise RuntimeError("team_auth_payload_required")
        return dict(team_auth_payload)
    if team_auth_path is None:
        raise RuntimeError("team_auth_payload_required")
    return load_json_payload(team_auth_path)


def _normalize_team_auth_payload(payload: dict[str, Any], *, validate_expiry: bool) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise RuntimeError("team_auth_payload_required")
    if bool(payload.get("disabled")):
        raise RuntimeError("team_auth_disabled")

    access_token = _extract_bearer(str(payload.get("access_token") or payload.get("token") or "").strip())
    if not access_token:
        raise RuntimeError("team_access_token_required")
    claims = _decode_jwt_payload(access_token)
    auth_claims = claims.get("https://api.openai.com/auth", {}) if isinstance(claims, dict) else {}

    account_id = str(
        payload.get("account_id")
        or payload.get("chatgpt_account_id")
        or auth_claims.get("chatgpt_account_id")
        or ""
    ).strip()
    if not account_id:
        raise RuntimeError("team_account_id_required")

    email = _normalize_email(payload.get("email") or claims.get("email") or "")
    if not email:
        raise RuntimeError("team_email_required")

    plan_type = str(auth_claims.get("chatgpt_plan_type") or "").strip().lower()
    if plan_type and plan_type != "team":
        raise RuntimeError(f"team_access_token_required plan_type={plan_type}")

    expires_at = _resolve_team_auth_expiration(payload)
    if validate_expiry and expires_at is not None and expires_at <= _now_for_compare(expires_at):
        raise RuntimeError(f"team_auth_expired: {_format_datetime_text(expires_at)}")

    return {
        "access_token": access_token,
        "refresh_token": str(payload.get("refresh_token") or "").strip(),
        "account_id": account_id,
        "email": email,
        "client_id": _resolve_refresh_client_id(payload, access_claims=claims),
        "expired": _format_datetime_text(expires_at),
        "oai_device_id": str(
            payload.get("oai_device_id")
            or payload.get("oaiDeviceId")
            or payload.get("device_id")
            or ""
        ).strip(),
    }


def _team_auth_needs_refresh(payload: dict[str, Any]) -> bool:
    expires_at = _resolve_team_auth_expiration(payload)
    if expires_at is None:
        return False
    return expires_at <= _now_for_compare(expires_at) + timedelta(seconds=DEFAULT_REFRESH_WINDOW_SECONDS)


def _resolve_team_auth_expiration(payload: dict[str, Any]) -> datetime | None:
    expired_text = str(payload.get("expired") or "").strip()
    parsed_expired = _parse_datetime_text(expired_text)
    if parsed_expired is not None:
        return parsed_expired

    access_token = _extract_bearer(str(payload.get("access_token") or payload.get("token") or "").strip())
    claims = _decode_jwt_payload(access_token)
    exp_value = claims.get("exp")
    try:
        exp_int = int(exp_value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(exp_int, tz=timezone.utc)


def _resolve_refresh_client_id(payload: dict[str, Any], *, access_claims: dict[str, Any] | None = None) -> str:
    direct = str(payload.get("client_id") or "").strip()
    if direct:
        return direct

    access_claims = access_claims or _decode_jwt_payload(str(payload.get("access_token") or ""))
    access_client_id = str(access_claims.get("client_id") or "").strip()
    if access_client_id:
        return access_client_id

    id_claims = _decode_jwt_payload(str(payload.get("id_token") or ""))
    audience = id_claims.get("aud")
    if isinstance(audience, list) and audience:
        candidate = str(audience[0] or "").strip()
        if candidate:
            return candidate
    if isinstance(audience, str) and audience.strip():
        return audience.strip()
    return DEFAULT_REFRESH_CLIENT_ID


def _perform_refresh_token_exchange(
    *,
    refresh_token: str,
    client_id: str,
    explicit_proxy: str | None,
) -> dict[str, Any]:
    verify_tls = env_flag("PROTOCOL_HTTP_VERIFY_TLS", False)
    session = requests.Session(
        impersonate=DEFAULT_IMPERSONATE,
        timeout=DEFAULT_HTTP_TIMEOUT_SECONDS,
        verify=verify_tls,
    )
    session.headers.update({"user-agent": DEFAULT_CHATGPT_USER_AGENT})
    try:
        with flow_network_env():
            request_kwargs: dict[str, Any] = {
                "headers": {
                    "accept": "application/json",
                    "content-type": "application/x-www-form-urlencoded",
                    "origin": CHATGPT_BASE_URL,
                    "referer": f"{CHATGPT_BASE_URL}/",
                },
                "data": urllib.parse.urlencode(
                    {
                        "grant_type": "refresh_token",
                        "refresh_token": str(refresh_token or "").strip(),
                        "client_id": str(client_id or "").strip(),
                    }
                ),
            }
            if explicit_proxy:
                request_kwargs["proxies"] = build_request_proxies(explicit_proxy)
            response = session.post(
                AUTH_OPENAI_TOKEN_URL,
                **request_kwargs,
            )
        status = int(getattr(response, "status_code", 599) or 599)
        payload = _parse_response_payload(response)
    finally:
        try:
            session.close()
        except Exception:
            pass

    if not (200 <= status < 300):
        raise RuntimeError(f"team_refresh_failed status={status} detail={_extract_detail(payload)}")
    if not isinstance(payload, dict):
        raise RuntimeError("team_refresh_invalid_payload")
    access_token = _extract_bearer(str(payload.get("access_token") or ""))
    if not access_token:
        raise RuntimeError("team_refresh_missing_access_token")
    return payload


def _extract_invite_email(payload: dict[str, Any]) -> str:
    email = _normalize_email(
        payload.get("email")
        or payload.get("inviteEmail")
        or payload.get("targetEmail")
        or ""
    )
    if not email:
        raise RuntimeError("invite_email_not_found")
    return email


def _extract_member_user_id_from_seed_payload(payload: dict[str, Any]) -> str:
    direct = _extract_account_user_id(payload)
    if direct:
        return direct

    pipeline = payload.get("pipeline")
    if not isinstance(pipeline, dict):
        return ""
    oauth = pipeline.get("oauth")
    if not isinstance(oauth, dict):
        return ""
    success_path = str(oauth.get("successPath") or "").strip()
    if not success_path:
        return ""
    try:
        success_payload = load_json_payload(success_path)
    except Exception:
        return ""
    return _extract_account_user_id(success_payload)


def _extract_account_user_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    auth_claims = payload.get("https://api.openai.com/auth")
    if isinstance(auth_claims, dict):
        for key in ("chatgpt_user_id", "user_id"):
            value = str(auth_claims.get(key) or "").strip()
            if value:
                return value
    for key in ("chatgpt_user_id", "user_id", "member_user_id", "userId"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    access_claims = _decode_jwt_payload(str(payload.get("access_token") or ""))
    if isinstance(access_claims, dict):
        nested_auth = access_claims.get("https://api.openai.com/auth")
        if isinstance(nested_auth, dict):
            for key in ("chatgpt_user_id", "user_id"):
                value = str(nested_auth.get(key) or "").strip()
                if value:
                    return value
    return ""


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
        parsed = json.loads(decoded)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_datetime_text(text: str) -> datetime | None:
    normalized = str(text or "").strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_datetime_text(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().isoformat(timespec="seconds")


def _now_for_compare(reference: datetime | None = None) -> datetime:
    if reference is not None and reference.tzinfo is not None:
        return datetime.now(reference.tzinfo)
    return datetime.now(timezone.utc)


def _extract_bearer(token: str) -> str:
    text = str(token or "").strip()
    if text.lower().startswith("bearer "):
        return text[7:].strip()
    return text


def _normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get("items", payload.get("results", []))
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _extract_invite_id(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("invite_id", "id", "invite"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
    return ""


def _extract_invite_created_at(payload: Any) -> datetime | None:
    if not isinstance(payload, dict):
        return None
    for key in ("created_time", "created_at", "createdAt"):
        parsed = _parse_datetime_text(str(payload.get(key) or "").strip())
        if parsed is not None:
            return parsed
    return None


def _is_stale_pending_invite(payload: dict[str, Any], *, stale_after_seconds: int) -> bool:
    if stale_after_seconds <= 0:
        return False
    created_at = _extract_invite_created_at(payload)
    if created_at is None:
        return False
    return (_now_for_compare(created_at) - created_at).total_seconds() >= stale_after_seconds


def _extract_user_id(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("user_id", "id", "member_id", "userId"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
    return ""


def _is_owner_user(payload: Any, *, owner_email: str = "") -> bool:
    if not isinstance(payload, dict):
        return False
    normalized_email = _normalize_email(payload.get("email") or payload.get("email_address") or "")
    if normalized_email and normalized_email == _normalize_email(owner_email):
        return True
    role = str(payload.get("role") or "").strip().lower()
    return role in {"account-owner", "owner"}


def _normalize_seat_type(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_codex_invite(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    seat_type = _normalize_seat_type(payload.get("seat_type") or payload.get("seatType"))
    return seat_type in DEFAULT_CODEX_SEAT_TYPES


def _is_codex_user(payload: Any, *, owner_email: str = "") -> bool:
    if not isinstance(payload, dict):
        return False
    if _is_owner_user(payload, owner_email=owner_email):
        return False
    seat_type = _normalize_seat_type(payload.get("seat_type") or payload.get("seatType"))
    return seat_type in DEFAULT_CODEX_SEAT_TYPES


def _seat_category_from_seat_type(value: Any) -> str:
    seat_type = _normalize_seat_type(value)
    if seat_type in DEFAULT_CODEX_SEAT_TYPES:
        return "codex"
    return "chatgpt"


def _team_seat_entry_from_invite(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    invite_id = _extract_invite_id(payload)
    invite_email = _normalize_email(payload.get("email_address") or payload.get("email") or "")
    seat_type = _normalize_seat_type(payload.get("seat_type") or payload.get("seatType"))
    if not invite_id and not invite_email:
        return None
    return {
        "kind": "invite",
        "invite_id": invite_id,
        "member_user_id": "",
        "invite_email": invite_email,
        "email": invite_email,
        "seat_type": seat_type,
        "seat_category": _seat_category_from_seat_type(seat_type),
        "role": str(payload.get("role") or "").strip(),
    }


def _team_seat_entry_from_user(payload: Any, *, owner_email: str = "") -> dict[str, Any] | None:
    if not isinstance(payload, dict) or _is_owner_user(payload, owner_email=owner_email):
        return None
    user_id = _extract_user_id(payload)
    user_email = _normalize_email(payload.get("email") or payload.get("email_address") or "")
    seat_type = _normalize_seat_type(payload.get("seat_type") or payload.get("seatType"))
    if not user_id and not user_email:
        return None
    return {
        "kind": "user",
        "invite_id": "",
        "member_user_id": user_id,
        "invite_email": user_email,
        "email": user_email,
        "seat_type": seat_type,
        "seat_category": _seat_category_from_seat_type(seat_type),
        "role": str(payload.get("role") or "").strip(),
    }


def _team_seat_summary_from_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    used_chatgpt = sum(
        1
        for item in entries
        if isinstance(item, dict) and str(item.get("seat_category") or "").strip().lower() == "chatgpt"
    )
    used_codex = sum(
        1
        for item in entries
        if isinstance(item, dict) and str(item.get("seat_category") or "").strip().lower() == "codex"
    )
    used_total = max(0, used_chatgpt + used_codex)
    available_total = max(0, DEFAULT_TEAM_CAPACITY_LIMIT - used_total)
    available_chatgpt = max(0, min(DEFAULT_TEAM_CHATGPT_SEAT_LIMIT - used_chatgpt, available_total))
    available_codex = max(0, min(DEFAULT_TEAM_CODEX_SEAT_LIMIT - used_codex, available_total))
    return {
        "total_limit": DEFAULT_TEAM_CAPACITY_LIMIT,
        "chatgpt_limit": DEFAULT_TEAM_CHATGPT_SEAT_LIMIT,
        "codex_limit": DEFAULT_TEAM_CODEX_SEAT_LIMIT,
        "required_chatgpt_for_expand": DEFAULT_TEAM_EXPAND_REQUIRED_CHATGPT_SEATS,
        "used_total": used_total,
        "used_chatgpt": used_chatgpt,
        "used_codex": used_codex,
        "available_total": available_total,
        "available_chatgpt": available_chatgpt,
        "available_codex": available_codex,
    }


def _team_seat_snapshot(entries: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_entries: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        normalized_entries.append(
            {
                "seat_category": str(item.get("seat_category") or "").strip().lower(),
                "seat_type": _normalize_seat_type(item.get("seat_type") or ""),
                "invite_email": str(item.get("invite_email") or item.get("email") or "").strip(),
                "invite_id": str(item.get("invite_id") or "").strip(),
                "member_user_id": str(item.get("member_user_id") or "").strip(),
                "role": str(item.get("role") or "").strip(),
                "kind": str(item.get("kind") or "").strip().lower(),
                "status": "active",
            }
        )
    return {
        "allocations": normalized_entries,
        "summary": _team_seat_summary_from_entries(normalized_entries),
    }


def _team_seat_remove_matching_entry(entries: list[dict[str, Any]], operation: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(operation, dict):
        return list(entries)
    operation_kind = str(operation.get("kind") or "").strip().lower()
    operation_id = str(operation.get("id") or "").strip().lower()
    operation_email = _normalize_email(operation.get("email") or "")
    filtered: list[dict[str, Any]] = []
    removed = False
    for item in entries:
        if not isinstance(item, dict):
            continue
        item_kind = str(item.get("kind") or "").strip().lower()
        item_invite_id = str(item.get("invite_id") or "").strip().lower()
        item_user_id = str(item.get("member_user_id") or "").strip().lower()
        item_email = _normalize_email(item.get("invite_email") or item.get("email") or "")
        matches = False
        if operation_kind == "invite":
            matches = item_kind == "invite" and (
                (operation_id and item_invite_id == operation_id)
                or (operation_email and item_email == operation_email)
            )
        elif operation_kind == "user":
            matches = item_kind == "user" and (
                (operation_id and item_user_id == operation_id)
                or (operation_email and item_email == operation_email)
            )
        if matches and not removed:
            removed = True
            continue
        filtered.append(item)
    return filtered


def _parse_response_payload(response: Any) -> Any:
    text = str(getattr(response, "text", "") or "")
    if not text:
        return {}
    try:
        return response.json()
    except Exception:
        return {"raw": text}


def _should_retry_status(status: int) -> bool:
    return int(status or 0) in {408, 425, 429, 500, 502, 503, 504}


def _extract_detail(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("detail", "message", "error_description", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        errored = payload.get("errored_emails")
        if errored:
            return str(errored)
        raw = payload.get("raw")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return str(payload)
    if isinstance(payload, str):
        return payload.strip()
    return str(payload)


def _classify_invite_error(status: int, payload: Any) -> str:
    detail = _extract_detail(payload).lower()
    if "maximum number of seats" in detail or "workspace has reached maximum number of seats" in detail:
        return "team_seats_full"
    if "seat" in detail and ("full" in detail or "limit" in detail):
        return "team_seats_full"
    if "already invited" in detail:
        return "already_invited"
    if "already a member" in detail or ("already" in detail and "member" in detail):
        return "already_member"
    if status == 401:
        return "unauthorized"
    if status == 403:
        return "forbidden"
    if status == 404:
        return "team_not_found"
    if status == 409:
        return "conflict"
    if status == 429:
        return "rate_limited"
    if 500 <= status < 600:
        return "server_error"
    return "invite_failed"


def _should_retry_team_default_invite_via_codex(payload: Any) -> bool:
    try:
        lowered = json.dumps(payload, ensure_ascii=False).lower()
    except Exception:
        lowered = str(payload).lower()
    return "unable to invite user due to an error" in lowered


def _canonical_target_route(path: str) -> str:
    normalized = str(path or "").strip() or "/"
    parts = [segment for segment in normalized.split("/") if segment]
    if len(parts) >= 3 and parts[:2] == ["backend-api", "accounts"]:
        canonical = [parts[0], parts[1], "{account_id}"]
        tail = parts[3:]
        if tail:
            canonical.append(tail[0])
            if len(tail) > 1:
                placeholder = "{invite_id}" if tail[0] == "invites" else "{user_id}" if tail[0] == "users" else "{id}"
                canonical.append(placeholder)
        return "/" + "/".join(canonical)
    return normalized


def _seed_chatgpt_device_cookie(session: requests.Session, device_id: str) -> None:
    normalized = str(device_id or "").strip()
    if not normalized:
        return
    for domain in (".chatgpt.com", "chatgpt.com"):
        try:
            session.cookies.set("oai-did", normalized, domain=domain)
        except Exception:
            continue


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send, revoke, or refresh ChatGPT team invite credentials.")
    parser.add_argument(
        "--action",
        choices=("invite", "revoke", "refresh"),
        default="invite",
        help="Choose whether to send an invite, revoke an invite, or refresh the team auth file.",
    )
    parser.add_argument("--email", help="Target email for invite or revoke.")
    parser.add_argument("--source-json", help="Read the target email from a registration json file.")
    parser.add_argument("--invite-id", help="Pending invite id to revoke directly.")
    parser.add_argument("--team-auth", help="Path to a *-team.json file. Defaults to the newest file under others/.")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force a token refresh before invite/revoke, or force refresh immediately when action=refresh.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.action == "refresh":
        result = refresh_team_auth_once(team_auth_path=args.team_auth, force=bool(args.force_refresh))
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.ok else 1

    if args.action == "invite":
        if args.invite_id:
            parser.error("--invite-id is only valid when --action revoke")
        if bool(args.email) == bool(args.source_json):
            parser.error("--action invite requires exactly one of --email or --source-json")
        if args.email:
            result = run_register_invite_once(
                invite_email=args.email,
                team_auth_path=args.team_auth,
                force_refresh=bool(args.force_refresh),
            )
        else:
            result = run_register_invite_from_path(
                source_path=args.source_json,
                team_auth_path=args.team_auth,
                force_refresh=bool(args.force_refresh),
            )
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.ok else 1

    if args.action == "revoke":
        provided_count = int(bool(args.invite_id)) + int(bool(args.email)) + int(bool(args.source_json))
        if provided_count != 1:
            parser.error("--action revoke requires exactly one of --invite-id, --email, or --source-json")
        if args.invite_id:
            result = run_revoke_invite_once(
                invite_id=args.invite_id,
                team_auth_path=args.team_auth,
                force_refresh=bool(args.force_refresh),
            )
        elif args.email:
            result = run_revoke_invite_once(
                invite_email=args.email,
                team_auth_path=args.team_auth,
                force_refresh=bool(args.force_refresh),
            )
        else:
            result = run_revoke_invite_from_path(
                source_path=args.source_json,
                team_auth_path=args.team_auth,
                force_refresh=bool(args.force_refresh),
            )
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.ok else 1

    parser.error(f"unsupported action: {args.action}")
    return 2


__all__ = [
    "CleanupCodexCapacityResult",
    "CleanupTeamSeatsResult",
    "RefreshTeamAuthResult",
    "RegisterInviteResult",
    "RevokeInviteResult",
    "UpdateTeamSeatResult",
    "TeamInviteClient",
    "main",
    "run_cleanup_codex_capacity_once",
    "run_cleanup_team_seats_once",
    "refresh_team_auth_once",
    "resolve_team_auth_path",
    "run_register_invite_from_path",
    "run_register_invite_once",
    "run_revoke_invite_from_path",
    "run_revoke_invite_once",
    "run_update_team_seat_once",
    "_should_retry_team_default_invite_via_codex",
]


if __name__ == "__main__":
    raise SystemExit(main())
