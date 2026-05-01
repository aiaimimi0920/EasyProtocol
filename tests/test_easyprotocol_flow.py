from __future__ import annotations

import sys
import os
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "providers" / "python" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from new_protocol_register.easyprotocol_flow import _update_team_expand_progress_payload  # noqa: E402
from new_protocol_register.magic import _classify_invite_error  # noqa: E402
from new_protocol_register import protocol_small_success  # noqa: E402
from new_protocol_register.protocol_small_success import (  # noqa: E402
    PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV,
    PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV,
    PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV,
    _protocol_only_env,
)


class EasyProtocolFlowTests(unittest.TestCase):
    def test_update_team_expand_progress_payload_sets_last_updated_at(self) -> None:
        payload = {
            "teamFlow": {
                "teamExpandProgress": {
                    "targetCount": 1,
                    "successfulMemberEmails": [],
                    "successfulArtifacts": [],
                    "successCount": 0,
                    "remainingCount": 1,
                    "readyForMotherCollection": False,
                }
            }
        }

        updated = _update_team_expand_progress_payload(
            payload,
            success_email="member@example.com",
            success_path="/tmp/member.json",
            account_id="acct_12345678",
        )

        progress = updated["teamFlow"]["teamExpandProgress"]
        self.assertEqual(["member@example.com"], progress["successfulMemberEmails"])
        self.assertEqual(1, progress["successCount"])
        self.assertEqual(0, progress["remainingCount"])
        self.assertTrue(progress["readyForMotherCollection"])
        self.assertTrue(str(progress.get("lastUpdatedAt") or "").endswith("Z"))

    def test_protocol_only_env_preserves_browser_bootstrap_and_sentinel(self) -> None:
        original_bootstrap = os.environ.get(PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV)
        original_sentinel = os.environ.get(PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV)
        original_stage2 = os.environ.get(PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV)
        try:
            os.environ[PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV] = "1"
            os.environ[PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV] = "1"
            os.environ[PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV] = "1"
            with _protocol_only_env():
                self.assertEqual("1", os.environ.get(PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV))
                self.assertEqual("1", os.environ.get(PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV))
                self.assertEqual("0", os.environ.get(PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV))
        finally:
            if original_bootstrap is None:
                os.environ.pop(PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV, None)
            else:
                os.environ[PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV] = original_bootstrap
            if original_sentinel is None:
                os.environ.pop(PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV, None)
            else:
                os.environ[PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV] = original_sentinel
            if original_stage2 is None:
                os.environ.pop(PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV, None)
            else:
                os.environ[PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV] = original_stage2

    def test_classify_invite_error_detects_deactivated_workspace(self) -> None:
        payload = {
            "detail": {
                "code": "deactivated_workspace",
            },
            "status_code": 402,
        }
        self.assertEqual("deactivated_workspace", _classify_invite_error(402, payload))

    def test_submit_platform_auth0_authorize_with_retry_bootstraps_when_login_session_missing(self) -> None:
        sentinel = SimpleNamespace(device_id="did", user_agent="ua")
        session = mock.Mock()
        session.headers = {"user-agent": "ua"}
        first_response = SimpleNamespace(status_code=200, url="https://auth.openai.com/authorize")
        second_response = SimpleNamespace(status_code=200, url="https://auth.openai.com/authorize")
        with mock.patch.object(
            protocol_small_success,
            "_session_request",
            side_effect=[first_response, second_response],
        ) as session_request, mock.patch.object(
            protocol_small_success,
            "_login_session_cookie",
            side_effect=["", "login-session"],
        ), mock.patch.object(
            protocol_small_success,
            "_maybe_prime_protocol_auth_session_with_browser",
            return_value=(sentinel, object()),
        ) as browser_bootstrap:
            returned_sentinel, response = protocol_small_success._submit_platform_auth0_authorize_with_retry(
                session=session,
                auth_url="https://auth.openai.com/api/accounts/authorize?x=1",
                sentinel_context=sentinel,
                explicit_proxy="http://proxy:8080",
            )
        self.assertIs(returned_sentinel, sentinel)
        self.assertIs(response, second_response)
        self.assertEqual(2, session_request.call_count)
        browser_bootstrap.assert_called_once()

    def test_submit_user_register_protocol_uses_browser_native_fallback(self) -> None:
        session = mock.Mock()
        failed_response = SimpleNamespace(status_code=400, url="https://auth.openai.com/api/accounts/user/register")
        browser_response = SimpleNamespace(status_code=200, url="https://auth.openai.com/api/accounts/user/register")
        attempt_history: list[dict[str, object]] = []
        with mock.patch.object(
            protocol_small_success,
            "_build_signup_sentinel_candidates",
            return_value=[("sentinel-a", "sentinel-token-a")],
        ), mock.patch.object(
            protocol_small_success,
            "_session_request",
            return_value=failed_response,
        ), mock.patch.object(
            protocol_small_success,
            "_build_protocol_headers",
            return_value={},
        ), mock.patch.object(
            protocol_small_success,
            "_minimal_user_register_cookie_header",
            return_value="",
        ), mock.patch.object(
            protocol_small_success,
            "_deduped_cookie_header_for_request",
            return_value="",
        ), mock.patch.object(
            protocol_small_success,
            "_protocol_auth_cookie_summary",
            return_value="summary",
        ), mock.patch.object(
            protocol_small_success,
            "_submit_browser_native_signup_user_register",
            return_value=browser_response,
        ) as browser_fallback:
            response, winning_attempt = protocol_small_success._submit_user_register_protocol(
                session=session,
                email="demo@example.com",
                password="password",
                device_id="device-id",
                sentinel_context=SimpleNamespace(),
                explicit_proxy="http://proxy:8080",
                network_attempt=1,
                attempt_history=attempt_history,
            )
        self.assertIs(response, browser_response)
        self.assertEqual("browser_native", str((winning_attempt or {}).get("variant") or ""))
        self.assertTrue(any(str(item.get("variant") or "") == "browser_native" for item in attempt_history))
        browser_fallback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
