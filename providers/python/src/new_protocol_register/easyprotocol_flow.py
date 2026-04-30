from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

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
    from runtime_probe import build_worker_runtime_probe
    from magic import (
        _should_retry_team_default_invite_via_codex,
        run_cleanup_codex_capacity_once,
        run_cleanup_team_seats_once,
        run_register_invite_from_path,
        run_register_invite_once,
        run_revoke_invite_once,
        run_update_team_seat_once,
        refresh_team_auth_once,
    )
    from object_storage.r2_upload import upload_file_to_r2
    from others.storage import load_json_payload
    from protocol_chatgpt_login import run_protocol_chatgpt_login_init_from_path
    from protocol_oauth import run_protocol_oauth_from_path
    from protocol_platform_org import run_protocol_platform_organization_init_from_path
    from protocol_small_success import run_protocol_small_success_once
else:
    from .others.bootstrap import ensure_local_bundle_imports

    ensure_local_bundle_imports()
    from runtime_probe import build_worker_runtime_probe
    from .magic import (
        _should_retry_team_default_invite_via_codex,
        run_cleanup_codex_capacity_once,
        run_cleanup_team_seats_once,
        run_register_invite_from_path,
        run_register_invite_once,
        run_revoke_invite_once,
        run_update_team_seat_once,
        refresh_team_auth_once,
    )
    from object_storage.r2_upload import upload_file_to_r2
    from .others.storage import load_json_payload
    from .protocol_chatgpt_login import run_protocol_chatgpt_login_init_from_path
    from .protocol_oauth import run_protocol_oauth_from_path
    from .protocol_platform_org import run_protocol_platform_organization_init_from_path
    from .protocol_small_success import run_protocol_small_success_once


def _extract_account_user_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    nested = payload.get("https://api.openai.com/auth")
    if isinstance(nested, dict):
        for key in ("chatgpt_user_id", "user_id"):
            value = str(nested.get(key) or "").strip()
            if value:
                return value
    for key in ("chatgpt_user_id", "user_id", "member_user_id", "userId"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _extract_invite_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("invite_id", "id", "invite"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    account_invites = payload.get("account_invites")
    if isinstance(account_invites, list) and account_invites:
        first = account_invites[0]
        if isinstance(first, dict):
            for key in ("invite_id", "id", "invite"):
                value = str(first.get(key) or "").strip()
                if value:
                    return value
    return ""

def _write_team_flow_update(*, source_path: Path, updater: Any) -> dict[str, Any]:
    payload = load_json_payload(source_path)
    if not isinstance(payload, dict):
        raise RuntimeError("small_success_payload_invalid")
    updated = updater(dict(payload))
    source_path.write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")
    return updated


def _build_oauth_result_payload(oauth_auth: Any, *, email: str, account_id: str, storage_path: str) -> dict[str, Any]:
    auth_payload = dict(oauth_auth or {}) if isinstance(oauth_auth, dict) else {}
    user_id = _extract_account_user_id(auth_payload)
    return {
        "ok": True,
        "status": "completed",
        "email": str(email or "").strip(),
        "accountId": str(account_id or "").strip(),
        "userId": user_id,
        "successPath": str(storage_path or "").strip(),
        "auth": auth_payload,
    }


def _team_expand_target_count(default: int = 4) -> int:
    raw = str(os.environ.get("REGISTER_TEAM_MEMBER_COUNT") or default).strip()
    try:
        return max(1, int(raw or default))
    except Exception:
        return max(1, int(default))


def _sanitize_filename_component(value: str, *, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    for bad in ('<', '>', ':', '"', '/', '\\', '|', '?', '*'):
        text = text.replace(bad, "_")
    text = text.strip().strip(".")
    return text or fallback


def _short_account_id_segment(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for separator in ("-", "_"):
        if separator in text:
            head = text.split(separator, 1)[0].strip()
            if head:
                return head
    return text[:8].strip()


def _canonical_team_artifact_name(*, email: str, account_id: str, is_mother: bool) -> str:
    normalized_email = _sanitize_filename_component(email, fallback="unknown-email")
    normalized_account = _sanitize_filename_component(
        _short_account_id_segment(account_id),
        fallback="unknown-account",
    )
    prefix = "codex-team-mother" if is_mother else "codex-team"
    return f"{prefix}-{normalized_account}-{normalized_email}.json"


def _stage_team_oauth_artifact(
    *,
    source_path: str,
    team_pool_dir: str | None,
    email: str,
    account_id: str,
    is_mother: bool = False,
) -> str:
    resolved_source = Path(str(source_path or "").strip()).resolve()
    if not resolved_source.exists() or not str(team_pool_dir or "").strip():
        return str(resolved_source)
    destination_dir = Path(str(team_pool_dir)).resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / _canonical_team_artifact_name(
        email=email,
        account_id=account_id,
        is_mother=is_mother,
    )
    try:
        if destination.resolve().samefile(resolved_source):
            return str(destination)
    except Exception:
        pass
    if destination.exists():
        destination.unlink(missing_ok=True)
    resolved_source.replace(destination)
    return str(destination)


def _update_team_expand_progress_payload(
    payload: dict[str, Any],
    *,
    success_email: str,
    success_path: str,
    account_id: str,
) -> dict[str, Any]:
    team_flow = dict(payload.get("teamFlow") or {})
    raw_progress = team_flow.get("teamExpandProgress")
    progress = dict(raw_progress) if isinstance(raw_progress, dict) else {}
    target_count = max(
        1,
        int(progress.get("targetCount") or _team_expand_target_count()),
    )
    normalized_email = str(success_email or "").strip().lower()
    existing_emails = progress.get("successfulMemberEmails")
    successful_emails = [
        str(item or "").strip().lower()
        for item in existing_emails
        if str(item or "").strip()
    ] if isinstance(existing_emails, list) else []
    if normalized_email and normalized_email not in successful_emails:
        successful_emails.append(normalized_email)

    existing_artifacts = progress.get("successfulArtifacts")
    successful_artifacts = []
    if isinstance(existing_artifacts, list):
        for item in existing_artifacts:
            if isinstance(item, dict):
                successful_artifacts.append(dict(item))
    if normalized_email:
        retained_artifacts = [
            item for item in successful_artifacts
            if str(item.get("email") or "").strip().lower() != normalized_email
        ]
        retained_artifacts.append(
            {
                "email": normalized_email,
                "successPath": str(success_path or "").strip(),
                "accountId": str(account_id or "").strip(),
            }
        )
        successful_artifacts = retained_artifacts

    success_count = len(successful_emails)
    progress.update(
        {
            "targetCount": target_count,
            "successfulMemberEmails": successful_emails,
            "successfulArtifacts": successful_artifacts,
            "successCount": success_count,
            "remainingCount": max(0, target_count - success_count),
            "readyForMotherCollection": success_count >= target_count,
            "lastUpdatedAt": datetime.utcnow().isoformat() + "Z",
        }
    )
    return {
        **payload,
        "teamFlow": {
            **team_flow,
            "teamExpandProgress": progress,
        },
    }


def _team_expand_progress_from_payload(payload: Any) -> dict[str, Any]:
    team_flow = payload.get("teamFlow") if isinstance(payload, dict) else {}
    raw_progress = team_flow.get("teamExpandProgress") if isinstance(team_flow, dict) else {}
    progress = dict(raw_progress) if isinstance(raw_progress, dict) else {}
    target_count = max(1, int(progress.get("targetCount") or _team_expand_target_count()))
    successful_emails: list[str] = []
    existing_emails = progress.get("successfulMemberEmails")
    if isinstance(existing_emails, list):
        for item in existing_emails:
            email = str(item or "").strip().lower()
            if email and email not in successful_emails:
                successful_emails.append(email)
    success_count = max(len(successful_emails), int(progress.get("successCount") or 0))
    remaining_count = max(0, int(progress.get("remainingCount") or (target_count - success_count)))
    ready = bool(progress.get("readyForMotherCollection")) or success_count >= target_count
    return {
        "targetCount": target_count,
        "successfulMemberEmails": successful_emails,
        "successCount": success_count,
        "remainingCount": remaining_count,
        "readyForMotherCollection": ready,
    }


def _resolve_artifact_source_path(artifact: dict[str, Any], *, label: str) -> Path:
    source_text = str(artifact.get("source_path") or artifact.get("claimed_path") or "").strip()
    if not source_text:
        raise RuntimeError(f"{label}_source_path_required")
    source_path = Path(source_text).resolve()
    if not source_path.exists():
        raise RuntimeError(f"{label}_source_path_missing:{source_path}")
    return source_path


def _normalize_member_artifacts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RuntimeError("team_member_artifacts_required")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"team_member_artifact_invalid:index={index}")
        source_path = _resolve_artifact_source_path(item, label=f"team_member_{index}")
        payload = load_json_payload(source_path)
        email = str(item.get("email") or payload.get("email") or "").strip()
        if not email:
            raise RuntimeError(f"team_member_email_required:index={index}")
        normalized.append(
            {
                "index": index,
                "artifact": item,
                "source_path": source_path,
                "email": email,
            }
        )
    if not normalized:
        raise RuntimeError("team_member_artifacts_required")
    return normalized


def _build_oauth_artifact_user_map(value: Any) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not isinstance(value, list):
        return mapping
    for item in value:
        if not isinstance(item, dict):
            continue
        email = str(item.get("email") or "").strip().lower()
        user_id = str(item.get("userId") or "").strip()
        if email and user_id:
            mapping[email] = user_id
    return mapping


def _build_team_invite_batch_result(
    *,
    status: str,
    requested_emails: list[str],
    invite_results: list[dict[str, Any]],
    team_auth_path: Path,
    failures: list[dict[str, Any]] | None = None,
    success_count: int = 0,
    member_oauth_required: bool,
    restore_members_to_team_pre_pool: bool,
    oauth_artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_failures = list(failures or [])
    normalized_oauth_artifacts = list(oauth_artifacts or [])
    all_attempts_failed = bool(invite_results) and success_count <= 0 and len(normalized_failures) == len(invite_results)
    return {
        "ok": True,
        "status": str(status or "").strip() or "invited",
        "count": len(invite_results),
        "successCount": max(0, int(success_count or 0)),
        "requestedMemberEmails": requested_emails,
        "teamAuthPath": str(team_auth_path),
        "results": invite_results,
        "failureCount": len(normalized_failures),
        "failures": normalized_failures,
        "allInviteAttemptsFailed": all_attempts_failed,
        "partialSuccess": bool(success_count) and bool(normalized_failures),
        "memberOauthRequired": bool(member_oauth_required),
        "restoreMembersToTeamPrePool": bool(restore_members_to_team_pre_pool),
        "oauthArtifacts": normalized_oauth_artifacts,
        "successfulMemberEmails": [
            str(item.get("email") or "").strip()
            for item in normalized_oauth_artifacts
            if isinstance(item, dict) and str(item.get("email") or "").strip()
        ],
        "failedMemberEmails": [
            str(item.get("email") or "").strip()
            for item in normalized_failures
            if isinstance(item, dict) and str(item.get("email") or "").strip()
        ],
    }


def _run_team_member_oauth_once(
    *,
    member_source_path: Path,
    output_dir: str | None,
    explicit_proxy: str | None,
    workspace_selector: str | None,
    retry_attempts: int,
    retry_sleep_seconds: float,
) -> Any:
    last_error: Exception | None = None
    for attempt_index in range(1, max(1, retry_attempts) + 1):
        try:
            return run_protocol_oauth_from_path(
                seed_path=member_source_path,
                output_dir=output_dir,
                explicit_proxy=explicit_proxy,
                workspace_selector=workspace_selector,
                force_email_otp_resend_on_verification=False,
            )
        except Exception as exc:
            last_error = exc
            lowered = str(exc).lower()
            transient_markers = (
                "missing_workspace",
                "wrong_email_otp_code",
                "authorize_missing_login_session",
                "authorize_continue_blocked",
                "authorize_continue_rate_limited",
                "password_verify_blocked",
            )
            if attempt_index >= max(1, retry_attempts) or not any(marker in lowered for marker in transient_markers):
                raise
            time.sleep(max(0.0, retry_sleep_seconds))
    raise last_error or RuntimeError("team_member_oauth_failed")


def dispatch_easyprotocol_step(*, step_type: str, step_input: dict[str, Any]) -> dict[str, Any]:
    normalized_step_type = str(step_type or "").strip()

    if normalized_step_type == "worker_runtime_probe":
        return build_worker_runtime_probe(step_input)

    if normalized_step_type == "upload_file_to_r2":
        return upload_file_to_r2(step_input=step_input)

    if normalized_step_type == "create_openai_account":
        result = run_protocol_small_success_once(
            output_dir=str(step_input.get("output_dir") or "").strip() or None,
            preallocated_email=str(step_input.get("preallocated_email") or "").strip() or None,
            preallocated_session_id=str(step_input.get("preallocated_session_id") or "").strip() or None,
            preallocated_mailbox_ref=str(step_input.get("preallocated_mailbox_ref") or "").strip() or None,
            explicit_proxy=str(step_input.get("proxy_url") or "").strip() or None,
        )
        return result.to_dict()

    if normalized_step_type == "initialize_platform_organization":
        return run_protocol_platform_organization_init_from_path(
            source_path=str(step_input.get("source_path") or "").strip(),
            explicit_proxy=str(step_input.get("proxy_url") or "").strip() or None,
            organization_name=str(step_input.get("organization_name") or "").strip() or "personal",
            organization_title=str(step_input.get("organization_title") or "").strip() or "personal",
            developer_persona=str(step_input.get("developer_persona") or "").strip() or "student",
        )

    if normalized_step_type == "initialize_chatgpt_login_session":
        return run_protocol_chatgpt_login_init_from_path(
            source_path=str(step_input.get("source_path") or "").strip(),
            explicit_proxy=str(step_input.get("proxy_url") or "").strip() or None,
            mailbox_ref=str(step_input.get("mailbox_ref") or "").strip() or None,
            mailbox_session_id=str(step_input.get("mailbox_session_id") or "").strip() or None,
        )

    if normalized_step_type == "invite_codex_member":
        source_path = Path(str(step_input.get("source_path") or "").strip()).resolve()
        invite_result = run_register_invite_from_path(
            source_path=source_path,
            team_auth_path=str(step_input.get("team_auth_path") or "").strip() or None,
            explicit_proxy=str(step_input.get("proxy_url") or "").strip() or None,
            seat_type=str(step_input.get("seat_type") or "").strip() or "usage_based",
        )
        _write_team_flow_update(
            source_path=source_path,
            updater=lambda payload: {
                **payload,
                "teamFlow": {
                    **dict(payload.get("teamFlow") or {}),
                    "invite": invite_result.to_dict(),
                },
            },
        )
        return invite_result.to_dict()

    if normalized_step_type == "obtain_codex_oauth":
        source_path = Path(str(step_input.get("source_path") or "").strip()).resolve()
        oauth_result = run_protocol_oauth_from_path(
            seed_path=source_path,
            output_dir=str(step_input.get("output_dir") or "").strip() or None,
            explicit_proxy=str(step_input.get("proxy_url") or "").strip() or None,
            workspace_selector=str(step_input.get("workspace_selector") or "").strip() or None,
        )
        result = _build_oauth_result_payload(
            oauth_result.auth,
            email=oauth_result.email,
            account_id=oauth_result.account_id,
            storage_path=oauth_result.storage_path,
        )
        _write_team_flow_update(
            source_path=source_path,
            updater=lambda payload: {
                **payload,
                "teamFlow": {
                    **dict(payload.get("teamFlow") or {}),
                    "protocolOAuth": {
                        "email": oauth_result.email,
                        "accountId": oauth_result.account_id,
                        "userId": str(result.get("userId") or "").strip(),
                        "successPath": oauth_result.storage_path,
                    },
                    "secondOAuth": None,
                },
            },
        )
        return result

    if normalized_step_type == "obtain_team_mother_oauth":
        source_path = Path(str(step_input.get("source_path") or "").strip()).resolve()
        source_payload = load_json_payload(source_path)
        explicit_proxy = str(step_input.get("proxy_url") or "").strip() or None
        workspace_selector = str(step_input.get("workspace_selector") or "").strip() or None
        seed_access_token = str(source_payload.get("access_token") or source_payload.get("token") or "").strip()
        seed_password = str(source_payload.get("password") or "").strip()
        seed_refresh_token = str(source_payload.get("refresh_token") or "").strip()
        has_complete_oauth_token = bool(seed_access_token and seed_refresh_token)
        token_validation_error: Exception | None = None
        result: dict[str, Any] | None = None

        if has_complete_oauth_token:
            try:
                refresh_result = refresh_team_auth_once(
                    team_auth_path=source_path,
                    force=False,
                    explicit_proxy=explicit_proxy,
                )
                result = _build_oauth_result_payload(
                    refresh_result.auth_payload,
                    email=refresh_result.team_email,
                    account_id=refresh_result.team_account_id,
                    storage_path=refresh_result.storage_path or str(source_path),
                )
                result["refreshOnly"] = True
                result["workspaceSelector"] = workspace_selector or ""
                result["authMode"] = "token"
            except Exception as exc:
                token_validation_error = exc

        if result is None and seed_password:
            oauth_result = run_protocol_oauth_from_path(
                seed_path=source_path,
                output_dir=str(step_input.get("output_dir") or "").strip() or None,
                explicit_proxy=explicit_proxy,
                workspace_selector=workspace_selector,
                force_email_otp_resend_on_verification=True,
            )
            result = _build_oauth_result_payload(
                oauth_result.auth,
                email=oauth_result.email,
                account_id=oauth_result.account_id,
                storage_path=oauth_result.storage_path,
            )
            result["authMode"] = "email"

        if result is None:
            if token_validation_error is not None:
                raise RuntimeError(
                    f"team_mother_token_validation_failed:{token_validation_error}"
                ) from token_validation_error
            raise RuntimeError("team_mother_auth_requires_password_or_refresh_token")
        result_email = str(result.get("email") or "").strip()
        _write_team_flow_update(
            source_path=source_path,
            updater=lambda payload: {
                **payload,
                "teamFlow": {
                    **dict(payload.get("teamFlow") or {}),
                    "teamMotherOAuth": {
                        "email": result_email,
                        "accountId": str(result.get("accountId") or "").strip(),
                        "userId": str(result.get("userId") or "").strip(),
                        "successPath": str(result.get("successPath") or "").strip(),
                        "workspaceSelector": workspace_selector,
                        "authMode": str(result.get("authMode") or "").strip(),
                        "refreshOnly": bool(result.get("refreshOnly")),
                    },
                },
            },
        )
        return result

    if normalized_step_type == "cleanup_team_all_seats":
        team_auth_path_text = str(step_input.get("team_auth_path") or "").strip()
        team_auth_payload = step_input.get("team_auth_payload")
        if not team_auth_path_text and not isinstance(team_auth_payload, dict):
            raise RuntimeError("team_auth_path_required")
        mother_source_path_text = str(
            step_input.get("team_mother_source_path")
            or step_input.get("source_path")
            or ""
        ).strip()
        preserved_member_emails: list[str] = []
        required_chatgpt_seats: int | None = None
        if mother_source_path_text:
            mother_source_path_for_progress = Path(mother_source_path_text).resolve()
            if mother_source_path_for_progress.exists():
                try:
                    mother_payload = load_json_payload(mother_source_path_for_progress)
                except Exception:
                    mother_payload = {}
                progress = _team_expand_progress_from_payload(mother_payload)
                preserved_member_emails = list(progress.get("successfulMemberEmails") or [])
                required_chatgpt_seats = int(progress.get("remainingCount") or 0)
        cleanup_result = run_cleanup_team_seats_once(
            team_auth_path=team_auth_path_text or None,
            team_auth_payload=team_auth_payload if isinstance(team_auth_payload, dict) else None,
            explicit_proxy=str(step_input.get("proxy_url") or "").strip() or None,
            preserve_member_emails=preserved_member_emails,
            required_chatgpt_seats=required_chatgpt_seats,
        )
        if mother_source_path_text:
            mother_source_path = Path(mother_source_path_text).resolve()
            if mother_source_path.exists():
                _write_team_flow_update(
                    source_path=mother_source_path,
                    updater=lambda payload: {
                        **payload,
                        "teamFlow": {
                            **dict(payload.get("teamFlow") or {}),
                            "teamSeatCleanup": cleanup_result.to_dict(),
                        },
                    },
                )
        return cleanup_result.to_dict()

    if normalized_step_type == "invite_team_members":
        team_auth_path_text = str(
            step_input.get("team_mother_auth_path")
            or step_input.get("team_auth_path")
            or ""
        ).strip()
        team_auth_payload = step_input.get("team_mother_auth_payload")
        if not isinstance(team_auth_payload, dict):
            team_auth_payload = step_input.get("team_auth_payload")
        has_team_auth_payload = isinstance(team_auth_payload, dict)
        if not team_auth_path_text and not has_team_auth_payload:
            raise RuntimeError("team_mother_auth_path_required")
        team_auth_path: Path | None = None
        if team_auth_path_text:
            team_auth_path = Path(team_auth_path_text).resolve()
        if team_auth_path is not None and not team_auth_path.exists() and not has_team_auth_payload:
            raise RuntimeError(f"team_mother_auth_path_missing:{team_auth_path}")

        mother_source_path_text = str(step_input.get("team_mother_source_path") or "").strip()
        mother_source_path = Path(mother_source_path_text).resolve() if mother_source_path_text else None
        member_artifacts = _normalize_member_artifacts(step_input.get("members"))
        requested_seat_type = str(step_input.get("seat_type") or "").strip() or "default"
        workspace_selector = str(step_input.get("workspace_selector") or "").strip() or None
        output_dir = str(step_input.get("output_dir") or "").strip() or None
        team_pool_dir = str(step_input.get("team_pool_dir") or "").strip() or None
        retry_attempts = max(1, int(str(step_input.get("retry_attempts") or "3").strip() or "3"))
        retry_sleep_seconds = max(0.0, float(str(step_input.get("retry_sleep_seconds") or "20").strip() or "20"))
        invite_results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        requested_emails: list[str] = []
        oauth_artifacts: list[dict[str, Any]] = []
        pending_member_updates: list[tuple[Path, dict[str, Any], dict[str, Any] | None]] = []
        for member in member_artifacts:
            email = str(member.get("email") or "").strip()
            invite_result = run_register_invite_once(
                invite_email=email,
                team_auth_path=team_auth_path,
                team_auth_payload=team_auth_payload if has_team_auth_payload else None,
                explicit_proxy=str(step_input.get("proxy_url") or "").strip() or None,
                seat_type=requested_seat_type,
            )
            invite_payload = invite_result.to_dict()
            invite_response = invite_payload.get("response")
            invite_payload["invite_id"] = _extract_invite_id(invite_response)
            invite_payload["user_id"] = _extract_account_user_id(invite_response)
            invite_payload["member_user_id"] = str(
                invite_payload.get("user_id") or invite_payload.get("member_user_id") or ""
            ).strip()
            invite_payload["requestedSeatType"] = requested_seat_type
            invite_payload["effectiveSeatType"] = str(invite_payload.get("response", {}).get("seat_type") or requested_seat_type).strip() or requested_seat_type
            invite_payload["seatUpgradeRequired"] = False
            invite_payload["seatUpgradeTargetType"] = ""
            invite_payload["seatUpgradeSourceType"] = ""
            invite_payload["fallbackReason"] = ""
            if (
                not bool(invite_result.ok)
                and str(requested_seat_type).strip().lower() == "default"
                and _should_retry_team_default_invite_via_codex(invite_payload)
            ):
                fallback_result = run_register_invite_once(
                    invite_email=email,
                    team_auth_path=team_auth_path,
                    team_auth_payload=team_auth_payload if has_team_auth_payload else None,
                    explicit_proxy=str(step_input.get("proxy_url") or "").strip() or None,
                    seat_type="usage_based",
                )
                fallback_payload = fallback_result.to_dict()
                if bool(fallback_result.ok):
                    fallback_invites = fallback_payload.get("response", {}).get("account_invites")
                    fallback_invite_id = ""
                    if isinstance(fallback_invites, list) and fallback_invites:
                        fallback_invite_id = str((fallback_invites[0] or {}).get("id") or "").strip()
                    seat_upgrade_result = run_update_team_seat_once(
                        invite_id=fallback_invite_id or None,
                        member_email=email,
                        seat_type="default",
                        team_auth_path=team_auth_path,
                        team_auth_payload=team_auth_payload if has_team_auth_payload else None,
                        explicit_proxy=str(step_input.get("proxy_url") or "").strip() or None,
                    )
                    seat_upgrade_payload = seat_upgrade_result.to_dict()
                    if bool(seat_upgrade_result.ok):
                        invite_payload = {
                            **fallback_payload,
                            "invite_id": fallback_invite_id,
                            "user_id": str(seat_upgrade_payload.get("user_id") or _extract_account_user_id(fallback_payload.get("response")) or "").strip(),
                            "member_user_id": str(seat_upgrade_payload.get("user_id") or _extract_account_user_id(fallback_payload.get("response")) or "").strip(),
                            "requestedSeatType": requested_seat_type,
                            "effectiveSeatType": "default",
                            "seatUpgradeApplied": True,
                            "seatUpgrade": seat_upgrade_payload,
                            "fallbackReason": "default_invite_failed_retry_with_usage_based_then_patch_default",
                            "defaultInviteFailure": invite_result.to_dict(),
                            "usageBasedInvite": fallback_payload,
                        }
                    else:
                        rollback_result = run_revoke_invite_once(
                            invite_id=fallback_invite_id or None,
                            invite_email=email,
                            member_user_id=str(seat_upgrade_payload.get("user_id") or "").strip() or None,
                            team_auth_path=team_auth_path,
                            team_auth_payload=team_auth_payload if has_team_auth_payload else None,
                            explicit_proxy=str(step_input.get("proxy_url") or "").strip() or None,
                        )
                        invite_payload = {
                            "ok": False,
                            "status": "seat_upgrade_failed_after_usage_based_invite",
                            "invite_email": email,
                            "team_account_id": str(fallback_payload.get("team_account_id") or "").strip(),
                            "team_email": str(fallback_payload.get("team_email") or "").strip(),
                            "status_code": int(seat_upgrade_payload.get("status_code") or 0),
                            "detail": str(seat_upgrade_payload.get("detail") or "").strip() or "seat_upgrade_failed_after_usage_based_invite",
                            "requestedSeatType": requested_seat_type,
                            "effectiveSeatType": "usage_based",
                            "seatUpgradeApplied": False,
                            "seatUpgrade": seat_upgrade_payload,
                            "fallbackReason": "default_invite_failed_retry_with_usage_based_then_patch_default",
                            "defaultInviteFailure": invite_result.to_dict(),
                            "usageBasedInvite": fallback_payload,
                            "rollbackAfterSeatUpgradeFailure": rollback_result.to_dict(),
                        }
                else:
                    invite_payload = {
                        **invite_payload,
                        "fallbackAttempted": True,
                        "fallbackResult": fallback_payload,
                    }
            requested_emails.append(email)
            if not bool(invite_result.ok):
                final_ok = bool(invite_payload.get("ok"))
            else:
                final_ok = True
            oauth_payload: dict[str, Any] | None = None
            if final_ok:
                try:
                    oauth_result = _run_team_member_oauth_once(
                        member_source_path=member["source_path"],
                        output_dir=output_dir,
                        explicit_proxy=str(step_input.get("proxy_url") or "").strip() or None,
                        workspace_selector=workspace_selector,
                        retry_attempts=retry_attempts,
                        retry_sleep_seconds=retry_sleep_seconds,
                    )
                    oauth_payload = _build_oauth_result_payload(
                        oauth_result.auth,
                        email=oauth_result.email,
                        account_id=oauth_result.account_id,
                        storage_path=oauth_result.storage_path,
                    )
                    staged_success_path = _stage_team_oauth_artifact(
                        source_path=str(oauth_payload.get("successPath") or "").strip(),
                        team_pool_dir=team_pool_dir,
                        email=str(oauth_payload.get("email") or email).strip(),
                        account_id=str(oauth_payload.get("accountId") or "").strip(),
                        is_mother=False,
                    )
                    oauth_payload["successPath"] = staged_success_path
                    oauth_payload["teamPoolPath"] = staged_success_path
                    oauth_artifacts.append(
                        {
                            "index": int(member.get("index") or 0),
                            **oauth_payload,
                        }
                    )
                    if mother_source_path is not None and mother_source_path.exists():
                        _write_team_flow_update(
                            source_path=mother_source_path,
                            updater=lambda payload, oauth_payload=oauth_payload: _update_team_expand_progress_payload(
                                payload,
                                success_email=str(oauth_payload.get("email") or "").strip(),
                                success_path=str(oauth_payload.get("successPath") or "").strip(),
                                account_id=str(oauth_payload.get("accountId") or "").strip(),
                            ),
                        )
                    invite_payload = {
                        **invite_payload,
                        "oauthCompleted": True,
                        "oauthStatus": "completed",
                        "oauthSuccessPath": str(oauth_payload.get("successPath") or "").strip(),
                        "user_id": str(oauth_payload.get("userId") or invite_payload.get("user_id") or "").strip(),
                        "member_user_id": str(oauth_payload.get("userId") or invite_payload.get("member_user_id") or "").strip(),
                    }
                except Exception as exc:
                    oauth_error_text = str(exc).strip()
                    lowered_oauth_error = oauth_error_text.lower()
                    invite_response = invite_payload.get("response")
                    materialized_member_id = ""
                    if isinstance(invite_response, dict):
                        materialized_member_id = str(
                            invite_response.get("account_user_id")
                            or invite_response.get("id")
                            or invite_response.get("user_id")
                            or ""
                        ).strip()
                    if not materialized_member_id:
                        materialized_member_id = str(
                            invite_payload.get("member_user_id") or invite_payload.get("user_id") or ""
                        ).strip()
                    transport_failure_after_member_created = bool(materialized_member_id) and (
                        "curl:" in lowered_oauth_error
                        or "connection closed abruptly" in lowered_oauth_error
                        or "operation timed out" in lowered_oauth_error
                        or "timed out" in lowered_oauth_error
                    )
                    discard_member_artifact = (
                        "phone_wall" in lowered_oauth_error
                        or "page_type=add_phone" in lowered_oauth_error
                        or "add_phone" in lowered_oauth_error
                        or transport_failure_after_member_created
                    )
                    rollback_result = run_revoke_invite_once(
                        invite_id=str(invite_payload.get("invite_id") or "").strip() or None,
                        invite_email=email or None,
                        member_user_id=str(invite_payload.get("member_user_id") or invite_payload.get("user_id") or "").strip() or None,
                        team_auth_path=team_auth_path,
                        team_auth_payload=team_auth_payload if has_team_auth_payload else None,
                        explicit_proxy=str(step_input.get("proxy_url") or "").strip() or None,
                    )
                    invite_payload = {
                        **invite_payload,
                        "ok": False,
                        "status": "member_oauth_failed_after_invite",
                        "detail": oauth_error_text or "member_oauth_failed_after_invite",
                        "oauthCompleted": False,
                        "oauthStatus": "failed",
                        "oauthError": oauth_error_text,
                        "discardMemberArtifact": discard_member_artifact,
                        "rollbackAfterOauthFailure": rollback_result.to_dict(),
                    }
                    final_ok = False
            invite_results.append(
                {
                    "index": int(member.get("index") or 0),
                    "email": email,
                    "sourcePath": str(member.get("source_path") or ""),
                    "result": invite_payload,
                    "oauthResult": oauth_payload,
                }
            )
            if not final_ok:
                failures.append(
                    {
                        "email": email,
                        "status": str(invite_payload.get("status") or "").strip(),
                        "detail": str(invite_payload.get("detail") or "").strip(),
                    }
                )
            pending_member_updates.append((member["source_path"], invite_payload, oauth_payload))

        for source_path, invite_payload, oauth_payload in pending_member_updates:
            _write_team_flow_update(
                source_path=source_path,
                updater=lambda payload, invite_payload=invite_payload, oauth_payload=oauth_payload: {
                    **payload,
                    "teamFlow": {
                        **dict(payload.get("teamFlow") or {}),
                        "invite": invite_payload,
                        "teamMemberOAuth": (
                            {
                                "email": str(oauth_payload.get("email") or "").strip(),
                                "accountId": str(oauth_payload.get("accountId") or "").strip(),
                                "userId": str(oauth_payload.get("userId") or "").strip(),
                                "successPath": str(oauth_payload.get("successPath") or "").strip(),
                            }
                            if isinstance(oauth_payload, dict)
                            else None
                        ),
                    },
                },
            )

        success_count = len(oauth_artifacts)
        all_invite_attempts_failed = bool(invite_results) and success_count <= 0 and len(failures) == len(invite_results)
        invite_status = "mother_only_all_invites_failed" if all_invite_attempts_failed else ("partial_success" if failures else "invited")
        invite_batch_result = _build_team_invite_batch_result(
            status=invite_status,
            requested_emails=requested_emails,
            invite_results=invite_results,
            team_auth_path=team_auth_path or Path(team_auth_path_text or "."),
            failures=failures,
            success_count=success_count,
            member_oauth_required=bool(oauth_artifacts),
            restore_members_to_team_pre_pool=all_invite_attempts_failed,
            oauth_artifacts=oauth_artifacts,
        )

        if mother_source_path is not None and mother_source_path.exists():
            _write_team_flow_update(
                source_path=mother_source_path,
                updater=lambda payload, invite_batch_result=invite_batch_result: {
                    **payload,
                    "teamFlow": {
                        **dict(payload.get("teamFlow") or {}),
                        "memberInviteBatch": {
                            **invite_batch_result,
                            "requestedEmails": requested_emails,
                        },
                    },
                },
            )

        failure_summary = ", ".join(
            f"{item['email']}[{item['status'] or 'invite_failed'}]"
            for item in failures
        )
        if all_invite_attempts_failed:
            return {
                **invite_batch_result,
                "detail": failure_summary or "all_team_member_invites_failed",
            }

        if failures:
            return {
                **invite_batch_result,
                "detail": failure_summary or "partial_team_member_failures",
            }

        return invite_batch_result

    if normalized_step_type == "obtain_team_member_oauth_batch":
        invite_result = step_input.get("invite_result")
        if isinstance(invite_result, dict):
            precomputed_artifacts = invite_result.get("oauthArtifacts")
            if isinstance(precomputed_artifacts, list):
                return {
                    "ok": True,
                    "status": "completed" if precomputed_artifacts else "idle",
                    "count": len(precomputed_artifacts),
                    "artifacts": precomputed_artifacts,
                    "precomputed": True,
                }
        member_artifacts = _normalize_member_artifacts(step_input.get("members"))
        workspace_selector = str(step_input.get("workspace_selector") or "").strip() or None
        output_dir = str(step_input.get("output_dir") or "").strip() or None
        explicit_proxy = str(step_input.get("proxy_url") or "").strip() or None
        retry_attempts = max(1, int(str(step_input.get("retry_attempts") or "3").strip() or "3"))
        retry_sleep_seconds = max(0.0, float(str(step_input.get("retry_sleep_seconds") or "20").strip() or "20"))
        artifacts: list[dict[str, Any]] = []
        pending_updates: list[tuple[Path, dict[str, Any]]] = []
        created_paths: list[Path] = []
        try:
            for member in member_artifacts:
                oauth_result = _run_team_member_oauth_once(
                    member_source_path=member["source_path"],
                    output_dir=output_dir,
                    explicit_proxy=explicit_proxy,
                    workspace_selector=workspace_selector,
                    retry_attempts=retry_attempts,
                    retry_sleep_seconds=retry_sleep_seconds,
                )
                created_path = Path(str(oauth_result.storage_path or "").strip()).resolve()
                if created_path.exists():
                    created_paths.append(created_path)
                result = _build_oauth_result_payload(
                    oauth_result.auth,
                    email=oauth_result.email,
                    account_id=oauth_result.account_id,
                    storage_path=oauth_result.storage_path,
                )
                artifacts.append(
                    {
                        "index": int(member.get("index") or 0),
                        **result,
                    }
                )
                pending_updates.append((member["source_path"], result))
        except Exception:
            for created_path in created_paths:
                created_path.unlink(missing_ok=True)
            raise

        for source_path, result in pending_updates:
            _write_team_flow_update(
                source_path=source_path,
                updater=lambda payload, result=result: {
                    **payload,
                    "teamFlow": {
                        **dict(payload.get("teamFlow") or {}),
                        "teamMemberOAuth": {
                            "email": str(result.get("email") or "").strip(),
                            "accountId": str(result.get("accountId") or "").strip(),
                            "userId": str(result.get("userId") or "").strip(),
                            "successPath": str(result.get("successPath") or "").strip(),
                        },
                    },
                },
            )

        return {
            "ok": True,
            "status": "completed",
            "count": len(artifacts),
            "artifacts": artifacts,
        }

    if normalized_step_type == "revoke_team_members":
        invite_result = step_input.get("invite_result")
        if isinstance(invite_result, dict) and (
            bool(invite_result.get("allInviteAttemptsFailed"))
            or not bool(invite_result.get("memberOauthRequired", True))
        ):
            return {
                "ok": True,
                "status": "skipped_mother_only_fallback",
                "count": 0,
                "results": [],
                "team_auth_path": str(step_input.get("team_auth_path") or "").strip(),
            }

        return {
            "ok": True,
            "status": "skipped_team_members_preserved",
            "count": 0,
            "results": [],
            "team_auth_path": str(step_input.get("team_auth_path") or "").strip(),
        }

        team_auth_path_text = str(step_input.get("team_auth_path") or "").strip()
        team_auth_payload = step_input.get("team_auth_payload")
        if not team_auth_path_text and not isinstance(team_auth_payload, dict):
            return {
                "ok": True,
                "status": "skipped_missing_team_mother_auth_path",
                "count": 0,
                "results": [],
            }
        team_auth_path: Path | None = None
        if team_auth_path_text:
            team_auth_path = Path(team_auth_path_text).resolve()
        if team_auth_path is not None and not team_auth_path.exists() and not isinstance(team_auth_payload, dict):
            return {
                "ok": True,
                "status": "skipped_missing_team_mother_auth_file",
                "count": 0,
                "results": [],
                "team_auth_path": str(team_auth_path),
            }

        member_artifacts = _normalize_member_artifacts(step_input.get("members"))
        oauth_user_ids = _build_oauth_artifact_user_map(step_input.get("oauth_artifacts"))
        explicit_proxy = str(step_input.get("proxy_url") or "").strip() or None

        results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for member in member_artifacts:
            email = str(member.get("email") or "").strip()
            user_id = oauth_user_ids.get(email.lower(), "")
            revoke_result = run_revoke_invite_once(
                invite_email=email or None,
                member_user_id=user_id or None,
                team_auth_path=team_auth_path,
                team_auth_payload=team_auth_payload if isinstance(team_auth_payload, dict) else None,
                explicit_proxy=explicit_proxy,
            )
            revoke_payload = revoke_result.to_dict()
            results.append(
                {
                    "index": int(member.get("index") or 0),
                    "email": email,
                    "userId": user_id,
                    "result": revoke_payload,
                }
            )
            if not bool(revoke_result.ok):
                failures.append(
                    {
                        "email": email,
                        "userId": user_id,
                        "status": revoke_result.status,
                        "detail": revoke_result.detail,
                    }
                )

        if failures:
            raise RuntimeError(
                "revoke_team_members_failed: "
                + ", ".join(
                    f"{item['email']}[{item['status']}]"
                    for item in failures
                )
            )

        return {
            "ok": True,
            "status": "revoked",
            "count": len(results),
            "results": results,
        }

    if normalized_step_type == "revoke_codex_member":
        source_path = Path(str(step_input.get("source_path") or "").strip()).resolve()
        revoke_result = run_revoke_invite_once(
            invite_email=str(step_input.get("invite_email") or "").strip() or None,
            member_user_id=str(step_input.get("member_user_id") or "").strip() or None,
            team_auth_path=str(step_input.get("team_auth_path") or "").strip() or None,
            explicit_proxy=str(step_input.get("proxy_url") or "").strip() or None,
        )
        _write_team_flow_update(
            source_path=source_path,
            updater=lambda payload: {
                **payload,
                "teamFlow": {
                    **dict(payload.get("teamFlow") or {}),
                    "revoke": revoke_result.to_dict(),
                },
            },
        )
        return revoke_result.to_dict()

    if normalized_step_type == "cleanup_codex_capacity":
        cleanup_result = run_cleanup_codex_capacity_once(
            team_auth_path=str(step_input.get("team_auth_path") or "").strip() or None,
            explicit_proxy=str(step_input.get("proxy_url") or "").strip() or None,
        )
        return cleanup_result.to_dict()

    raise RuntimeError(f"unsupported_easyprotocol_step:{normalized_step_type}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dispatch a medium EasyProtocol business step.")
    parser.add_argument("--step-type", required=True, help="Generic DST step type.")
    parser.add_argument("--input-json", default="{}", help="JSON object passed as step input.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    payload = json.loads(str(args.input_json or "{}"))
    if not isinstance(payload, dict):
        raise RuntimeError("input_json_must_be_object")
    result = dispatch_easyprotocol_step(
        step_type=str(args.step_type or "").strip(),
        step_input=payload,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
