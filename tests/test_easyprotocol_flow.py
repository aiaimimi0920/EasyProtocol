from __future__ import annotations

import contextlib
import datetime as dt
import json
import sys
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "providers" / "python" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

PYTHON_SHARED_ROOT = Path(__file__).resolve().parents[1] / "providers" / "python" / "python_shared" / "src"
if str(PYTHON_SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_SHARED_ROOT))

from new_protocol_register.easyprotocol_flow import _update_team_expand_progress_payload  # noqa: E402
from new_protocol_register import easyprotocol_flow  # noqa: E402
from new_protocol_register.magic import _classify_invite_error  # noqa: E402
from new_protocol_register import protocol_chatgpt_login  # noqa: E402
from new_protocol_register import protocol_account_availability  # noqa: E402
from new_protocol_register import protocol_community_login  # noqa: E402
from new_protocol_register import protocol_oauth  # noqa: E402
from new_protocol_register import protocol_platform_org  # noqa: E402
from new_protocol_register import protocol_phone_verification  # noqa: E402
from new_protocol_register import protocol_small_success  # noqa: E402
from new_protocol_register.others import runtime as protocol_runtime  # noqa: E402
from protocol_runtime import protocol_register  # noqa: E402
from protocol_runtime.errors import ProtocolRuntimeError  # noqa: E402
from shared_mailbox import easy_email_client  # noqa: E402
from shared_captcha import service_client as captcha_service_client  # noqa: E402
from new_protocol_register.protocol_small_success import (  # noqa: E402
    PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV,
    PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV,
    PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV,
    _protocol_only_env,
)


class EasyProtocolFlowTests(unittest.TestCase):
    def test_get_mailbox_latest_message_id_uses_wall_clock_when_existing_code_has_no_marker(self) -> None:
        with mock.patch.object(
            easy_email_client,
            "_get_json",
            return_value={"code": {"code": "123456"}},
        ), mock.patch.object(easy_email_client.time, "time", return_value=1779975000.9):
            marker = easy_email_client.get_mailbox_latest_message_id(session_id="mailbox_123")

        self.assertEqual(1779975000, marker)

    def test_wait_openai_code_resolves_default_floor_before_polling(self) -> None:
        calls: list[str] = []

        def _fake_get_json(path: str) -> dict:
            calls.append(path)
            if path == "/mail/mailboxes/mailbox_123/code":
                return {
                    "code": {
                        "code": "111111",
                        "receivedAt": "2026-06-06T10:00:00Z",
                    }
                }
            if path == "/mail/snapshot":
                return {
                    "snapshot": {
                        "messages": [
                            {
                                "sessionId": "mailbox_123",
                                "extractedCode": "222222",
                                "receivedAt": "2026-06-06T10:00:05Z",
                            }
                        ]
                    }
                }
            raise AssertionError(f"unexpected path: {path}")

        with mock.patch.object(easy_email_client, "_get_json", side_effect=_fake_get_json):
            code = easy_email_client.wait_openai_code(
                mailbox_ref="moemail:mailbox_123",
                session_id="mailbox_123",
                timeout_seconds=5,
            )

        self.assertEqual("222222", code)
        self.assertEqual(
            [
                "/mail/mailboxes/mailbox_123/code",
                "/mail/mailboxes/mailbox_123/code",
                "/mail/snapshot",
            ],
            calls,
        )

    def test_wait_openai_code_uses_snapshot_after_transient_code_endpoint_error(self) -> None:
        calls: list[str] = []

        def _fake_get_json(path: str) -> dict:
            calls.append(path)
            if path == "/mail/mailboxes/mailbox_123/code":
                if calls.count(path) == 1:
                    return {
                        "code": {
                            "code": "111111",
                            "receivedAt": "2026-06-06T10:00:00Z",
                        }
                    }
                raise RuntimeError("mail service GET /mail/mailboxes/mailbox_123/code failed: HTTP 502")
            if path == "/mail/snapshot":
                return {
                    "snapshot": {
                        "messages": [
                            {
                                "sessionId": "mailbox_123",
                                "extractedCode": "222222",
                                "receivedAt": "2026-06-06T10:00:05Z",
                            }
                        ]
                    }
                }
            raise AssertionError(f"unexpected path: {path}")

        with mock.patch.object(easy_email_client, "_get_json", side_effect=_fake_get_json):
            code = easy_email_client.wait_openai_code(
                mailbox_ref="moemail:mailbox_123",
                session_id="mailbox_123",
                timeout_seconds=5,
            )

        self.assertEqual("222222", code)
        self.assertIn("/mail/snapshot", calls)

    def test_wait_openai_code_accepts_code_equal_to_auto_floor(self) -> None:
        with mock.patch.object(
            easy_email_client,
            "_resolve_openai_code_floor",
            return_value=123,
        ), mock.patch.object(
            easy_email_client,
            "_get_json",
            return_value={
                "code": {
                    "code": "654321",
                    "receivedAt": "1970-01-01T00:02:03+00:00",
                }
            },
        ), mock.patch.object(
            easy_email_client,
            "_snapshot_session_openai_code",
            return_value=("", 0),
        ), mock.patch.object(
            easy_email_client.time,
            "time",
            side_effect=[0, 0, 0, 11],
        ), mock.patch.object(
            easy_email_client.time,
            "sleep",
            return_value=None,
        ):
            code = easy_email_client.wait_openai_code(
                mailbox_ref="moemail:mailbox_123",
                session_id="mailbox_123",
                timeout_seconds=10,
            )

        self.assertEqual("654321", code)

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

    def test_protocol_only_env_disables_browser_bootstrap_and_sentinel(self) -> None:
        original_bootstrap = os.environ.get(PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV)
        original_sentinel = os.environ.get(PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV)
        original_stage2 = os.environ.get(PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV)
        try:
            os.environ[PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV] = "1"
            os.environ[PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV] = "1"
            os.environ[PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV] = "1"
            with _protocol_only_env():
                self.assertEqual("0", os.environ.get(PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV))
                self.assertEqual("0", os.environ.get(PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV))
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

    def test_full_flow_platform_login_retries_after_transient_block(self) -> None:
        session = mock.Mock()
        initial_context = SimpleNamespace(user_agent="ua")
        refreshed_context = SimpleNamespace(user_agent="ua", refreshed=True)
        response = SimpleNamespace(status_code=200)
        blocked = ProtocolRuntimeError(
            "platform_login status=403",
            stage="stage_platform_login",
            detail="platform_login",
        )
        with mock.patch.object(
            protocol_small_success,
            "_open_platform_login",
            side_effect=[blocked, response],
        ) as open_platform_login, mock.patch.object(
            protocol_small_success,
            "_prime_openai_login_session_with_browser",
            return_value=(refreshed_context, True),
        ) as prime_browser:
            sentinel_context, result = protocol_small_success._open_platform_login_with_browser_retry(
                session=session,
                sentinel_context=initial_context,
                explicit_proxy="http://proxy:8080",
            )

        self.assertIs(refreshed_context, sentinel_context)
        self.assertIs(response, result)
        self.assertEqual(2, open_platform_login.call_count)
        prime_browser.assert_called_once_with(
            session=session,
            sentinel_context=initial_context,
            explicit_proxy="http://proxy:8080",
            reason="openai_login_platform_login_retry",
        )

    def test_full_flow_platform_login_continues_with_browser_session_when_retry_stays_blocked(self) -> None:
        session = mock.Mock()
        initial_context = SimpleNamespace(user_agent="ua")
        first_block = ProtocolRuntimeError(
            "platform_login status=403",
            stage="stage_platform_login",
            detail="platform_login",
        )
        second_block = ProtocolRuntimeError(
            "platform_login status=403",
            stage="stage_platform_login",
            detail="platform_login",
        )
        refreshed_context = SimpleNamespace(user_agent="ua-browser", device_id="did-browser")
        with mock.patch.object(
            protocol_small_success,
            "_open_platform_login",
            side_effect=[first_block, second_block],
        ) as open_platform_login, mock.patch.object(
            protocol_small_success,
            "_prime_openai_login_session_with_browser",
            return_value=(refreshed_context, True),
        ) as prime_browser:
            result_context, result = protocol_small_success._open_platform_login_with_browser_retry(
                session=session,
                sentinel_context=initial_context,
                explicit_proxy="http://proxy:8080",
            )

        self.assertIs(refreshed_context, result_context)
        self.assertIsNone(result)
        self.assertEqual(2, open_platform_login.call_count)
        prime_browser.assert_called_once()

    def test_platform_auth0_authorize_recovers_invalid_state_and_refreshes_device_id(self) -> None:
        session = mock.Mock()
        initial_context = SimpleNamespace(user_agent="ua", device_id="did-old")
        refreshed_context = SimpleNamespace(user_agent="ua-browser", device_id="did-new")
        blocked = SimpleNamespace(status_code=400, headers={}, text="invalid_state")
        success = SimpleNamespace(status_code=302, headers={}, text="", url="https://auth.openai.com/callback")
        auth_url = "https://auth.openai.com/api/accounts/authorize?device_id=did-old&ext-oai-did=did-old&state=state"
        with mock.patch.object(
            protocol_small_success,
            "_session_request",
            return_value=blocked,
        ) as session_request, mock.patch.object(
            protocol_small_success,
            "_recover_platform_auth0_authorize_in_browser",
            return_value=(
                refreshed_context,
                success,
                "https://auth.openai.com/api/accounts/authorize?state=state&device_id=did-new&ext-oai-did=did-new",
                "did-new",
            ),
        ) as browser_recovery, mock.patch.object(
            protocol_small_success,
            "_openai_login_init_response_needs_retry",
            return_value=True,
        ), mock.patch.object(
            protocol_small_success,
            "_login_session_cookie",
            return_value="login-session",
        ):
            result_context, result, result_url, result_device_id = (
                protocol_small_success._platform_auth0_authorize_with_browser_retry(
                    session=session,
                    auth_url=auth_url,
                    device_id="did-old",
                    sentinel_context=initial_context,
                    explicit_proxy="http://proxy:8080",
                    request_label="authorize-test",
                )
            )

        self.assertIs(refreshed_context, result_context)
        self.assertIs(success, result)
        self.assertEqual("did-new", result_device_id)
        self.assertIn("device_id=did-new", result_url)
        self.assertIn("ext-oai-did=did-new", result_url)
        self.assertEqual(1, session_request.call_count)
        browser_recovery.assert_called_once_with(
            session=session,
            auth_url=auth_url,
            device_id="did-old",
            sentinel_context=initial_context,
            explicit_proxy="http://proxy:8080",
        )

    def test_platform_auth0_authorize_requires_interaction_when_challenged_without_cookie(self) -> None:
        session = mock.Mock()
        initial_context = SimpleNamespace(user_agent="ua", device_id="did-old")
        hydrated_context = SimpleNamespace(user_agent="ua-browser", device_id="did-browser")
        blocked = SimpleNamespace(status_code=403, headers={"cf-mitigated": "challenge"}, text="challenge")
        browser_response = SimpleNamespace(status_code=200, headers={}, text="", url="https://auth.openai.com/create-account/password")
        auth_url = "https://auth.openai.com/api/accounts/authorize?device_id=did-old&state=state"
        with mock.patch.object(
            protocol_small_success,
            "_session_request",
            side_effect=[blocked, blocked],
        ), mock.patch.object(
            protocol_small_success,
            "_openai_login_init_response_needs_retry",
            return_value=True,
        ), mock.patch.object(
            protocol_small_success,
            "_login_session_cookie",
            return_value="",
        ), mock.patch.object(
            protocol_small_success,
            "_recover_platform_auth0_authorize_in_browser",
            return_value=(hydrated_context, browser_response, auth_url, "did-browser"),
        ) as browser_recovery:
            with self.assertRaisesRegex(ProtocolRuntimeError, "browser_verification_required") as caught:
                protocol_small_success._platform_auth0_authorize_with_browser_retry(
                    session=session,
                    auth_url=auth_url,
                    device_id="did-old",
                    sentinel_context=initial_context,
                    explicit_proxy="http://proxy:8080",
                    request_label="authorize-test",
                )

        self.assertEqual("stage_auth_continue", caught.exception.stage)
        self.assertEqual("oauth_authorize", caught.exception.detail)
        browser_recovery.assert_not_called()

    def test_classify_invite_error_detects_deactivated_workspace(self) -> None:
        payload = {
            "detail": {
                "code": "deactivated_workspace",
            },
            "status_code": 402,
        }
        self.assertEqual("deactivated_workspace", _classify_invite_error(402, payload))

    def test_build_signup_sentinel_candidates_keeps_trying_other_personas(self) -> None:
        session = mock.Mock()
        sentinel_context = SimpleNamespace(user_agent="ua")
        with mock.patch.object(
            protocol_small_success,
            "_get_sentinel_header_for_signup",
            side_effect=[
                "token-current-with-email",
                "token-current-without-email",
                "token-har1-with-email",
                "token-har1-without-email",
                "token-har2-with-email",
                "token-har2-without-email",
            ],
        ) as get_sentinel, mock.patch.object(
            protocol_small_success,
            "_sentinel_token_lengths",
            side_effect=[
                (1312, 665, True),
                (1336, 665, True),
                (1290, 620, True),
                (1400, 700, True),
                (1280, 610, True),
                (1390, 690, True),
            ],
        ):
            candidates = protocol_small_success._build_signup_sentinel_candidates(
                session=session,
                email="demo@example.com",
                device_id="device-id",
                explicit_proxy="http://proxy:8080",
                sentinel_context=sentinel_context,
                network_attempt=1,
            )
        self.assertIn(("har1:without_email", "token-har1-without-email"), candidates)
        self.assertIn(("har2:without_email", "token-har2-without-email"), candidates)
        self.assertEqual(6, get_sentinel.call_count)

    def test_openai_login_init_response_needs_retry_for_rate_limit_and_blocked_code(self) -> None:
        rate_limited_response = SimpleNamespace(
            status_code=429,
            headers={},
            text="",
            url="https://auth.openai.com/api/accounts/authorize/continue",
            json=lambda: {},
        )
        blocked_response = SimpleNamespace(
            status_code=400,
            headers={},
            text="",
            url="https://auth.openai.com/api/accounts/authorize/continue",
            json=lambda: {"error": {"code": "authorize_continue_blocked"}},
        )

        self.assertTrue(protocol_small_success._openai_login_init_response_needs_retry(rate_limited_response))
        self.assertTrue(protocol_small_success._openai_login_init_response_needs_retry(blocked_response))

    def test_authorize_continue_browser_retry_preserves_main_signup_submitter(self) -> None:
        session = mock.Mock()
        initial_context = SimpleNamespace(name="initial")
        updated_context = SimpleNamespace(name="updated")
        blocked_response = SimpleNamespace(
            status_code=400,
            headers={},
            text="",
            url="https://auth.openai.com/api/accounts/authorize/continue",
            json=lambda: {"error": {"code": "authorize_continue_blocked"}},
        )
        success_response = SimpleNamespace(
            status_code=200,
            headers={},
            text="",
            url="https://auth.openai.com/create-account/password",
            json=lambda: {"page": {"type": "create_account"}},
        )

        with mock.patch.object(
            protocol_small_success,
            "_submit_authorize_continue_login_or_signup",
            side_effect=[blocked_response, success_response],
        ) as submit_signup, mock.patch.object(
            protocol_small_success,
            "_submit_authorize_continue_login_or_create_account",
        ) as submit_login_or_create, mock.patch.object(
            protocol_small_success,
            "_prime_openai_login_session_with_browser",
            return_value=(updated_context, True),
        ) as prime_browser, mock.patch.object(
            protocol_small_success,
            "_ensure_openai_login_session_ready",
            return_value=updated_context,
        ) as ensure_ready:
            result_context, result_response = (
                protocol_small_success._submit_authorize_continue_with_browser_retry(
                    session=session,
                    email="demo@example.com",
                    device_id="device-id",
                    sentinel_context=initial_context,
                    explicit_proxy="http://proxy:8080",
                    use_create_account_referer=True,
                )
            )

        self.assertIs(updated_context, result_context)
        self.assertIs(success_response, result_response)
        self.assertEqual(2, submit_signup.call_count)
        self.assertIs(initial_context, submit_signup.call_args_list[0].kwargs["sentinel_context"])
        self.assertIs(updated_context, submit_signup.call_args_list[1].kwargs["sentinel_context"])
        submit_login_or_create.assert_not_called()
        prime_browser.assert_called_once()
        ensure_ready.assert_called_once()

    def test_authorize_continue_browser_retry_preserves_login_init_submitter(self) -> None:
        session = mock.Mock()
        sentinel_context = SimpleNamespace(name="initial")
        success_response = SimpleNamespace(
            status_code=200,
            headers={},
            text="",
            url="https://auth.openai.com/create-account/password",
            json=lambda: {"page": {"type": "create_account"}},
        )

        with mock.patch.object(
            protocol_small_success,
            "_submit_authorize_continue_login_or_signup",
        ) as submit_signup, mock.patch.object(
            protocol_small_success,
            "_submit_authorize_continue_login_or_create_account",
            return_value=success_response,
        ) as submit_login_or_create, mock.patch.object(
            protocol_small_success,
            "_prime_openai_login_session_with_browser",
        ) as prime_browser:
            result_context, result_response = (
                protocol_small_success._submit_authorize_continue_with_browser_retry(
                    session=session,
                    email="demo@example.com",
                    device_id="device-id",
                    sentinel_context=sentinel_context,
                    explicit_proxy="http://proxy:8080",
                    use_create_account_referer=False,
                )
            )

        self.assertIs(sentinel_context, result_context)
        self.assertIs(success_response, result_response)
        submit_signup.assert_not_called()
        submit_login_or_create.assert_called_once()
        prime_browser.assert_not_called()

    def test_prepare_passwordless_email_otp_keeps_auto_sent_verification_code(self) -> None:
        session = mock.Mock()
        sentinel_context = SimpleNamespace(name="sentinel")
        with mock.patch.object(protocol_small_success, "_send_email_otp") as send_email_otp:
            passwordless = protocol_small_success._prepare_passwordless_email_otp(
                session=session,
                page_type="email_otp_verification",
                explicit_proxy="http://proxy:8080",
                sentinel_context=sentinel_context,
            )

        self.assertTrue(passwordless)
        send_email_otp.assert_not_called()

    def test_prepare_passwordless_email_otp_sends_when_requested(self) -> None:
        session = mock.Mock()
        sentinel_context = SimpleNamespace(name="sentinel")
        with mock.patch.object(protocol_small_success, "_send_email_otp") as send_email_otp:
            passwordless = protocol_small_success._prepare_passwordless_email_otp(
                session=session,
                page_type="email_otp_send",
                explicit_proxy="http://proxy:8080",
                sentinel_context=sentinel_context,
            )

        self.assertTrue(passwordless)
        send_email_otp.assert_called_once_with(
            session,
            explicit_proxy="http://proxy:8080",
            header_builder=sentinel_context,
        )

    def test_prepare_passwordless_email_otp_leaves_password_signup_unchanged(self) -> None:
        with mock.patch.object(protocol_small_success, "_send_email_otp") as send_email_otp:
            passwordless = protocol_small_success._prepare_passwordless_email_otp(
                session=mock.Mock(),
                page_type="create_account",
                explicit_proxy=None,
                sentinel_context=SimpleNamespace(name="sentinel"),
            )

        self.assertFalse(passwordless)
        send_email_otp.assert_not_called()

    def test_captcha_service_client_rejects_easybrowser_base_url(self) -> None:
        original_base_url = os.environ.get("CAPTCHA_SERVICE_BASE_URL")
        try:
            os.environ["CAPTCHA_SERVICE_BASE_URL"] = "http://easy-browser:18080"
            with self.assertRaises(RuntimeError) as ctx:
                captcha_service_client._post_json("/createTask", {"task": {"type": "Demo"}})
            self.assertIn("EasyBrowser attach service", str(ctx.exception))
        finally:
            if original_base_url is None:
                os.environ.pop("CAPTCHA_SERVICE_BASE_URL", None)
            else:
                os.environ["CAPTCHA_SERVICE_BASE_URL"] = original_base_url

    def test_chatgpt_login_request_retries_transient_network_error(self) -> None:
        session = mock.Mock()
        response = SimpleNamespace(status_code=200, url="https://chatgpt.com/auth/login_with")
        with mock.patch.object(
            protocol_chatgpt_login,
            "_session_request",
            side_effect=[RuntimeError("curl: (7) Connection closed abruptly"), response],
        ) as session_request:
            result = protocol_chatgpt_login._chatgpt_login_request(
                session,
                "GET",
                "https://chatgpt.com/auth/login_with",
                explicit_proxy="http://proxy:8080",
                request_label="chatgpt-login",
                timeout=20,
            )
        self.assertIs(result, response)
        self.assertEqual(2, session_request.call_count)

    def test_chatgpt_login_step_retries_missing_client_auth_session(self) -> None:
        self.assertTrue(
            protocol_chatgpt_login._chatgpt_login_step_retryable(
                RuntimeError(
                    'client_auth_session_dump status=404 '
                    'body={"error":{"code":"missing_session"}}'
                )
            )
        )
        self.assertFalse(
            protocol_chatgpt_login._chatgpt_login_step_retryable(
                RuntimeError("client_auth_session_dump status=403 body=access_denied")
            )
        )

    def test_chatgpt_login_request_recomputes_retry_timeout_from_deadline(self) -> None:
        session = mock.Mock()
        response = SimpleNamespace(status_code=200, url="https://chatgpt.com/auth/login_with")
        request_timeouts: list[object] = []
        request_deadlines: list[object] = []

        def _fake_session_request(*_args: object, **kwargs: object) -> SimpleNamespace:
            request_timeouts.append(kwargs.get("timeout"))
            request_deadlines.append(kwargs.get("deadline"))
            if len(request_timeouts) == 1:
                raise RuntimeError("curl: (28) Operation timed out")
            return response

        with mock.patch.object(
            protocol_chatgpt_login,
            "_session_request",
            side_effect=_fake_session_request,
        ), mock.patch.object(
            protocol_chatgpt_login.time,
            "monotonic",
            side_effect=[100.0, 100.0, 115.0, 115.0],
        ), mock.patch.object(protocol_chatgpt_login.time, "sleep", return_value=None):
            result = protocol_chatgpt_login._chatgpt_login_request(
                session,
                "GET",
                "https://chatgpt.com/auth/login_with",
                explicit_proxy="http://proxy:8080",
                request_label="chatgpt-login",
                timeout=20,
                deadline=120.0,
            )

        self.assertIs(result, response)
        self.assertEqual([20, 5], request_timeouts)
        self.assertEqual([None, None], request_deadlines)

    def test_send_email_otp_retries_transient_network_error(self) -> None:
        session = mock.Mock()
        response = SimpleNamespace(status_code=200, url="https://auth.openai.com/api/accounts/email-otp/send")
        with mock.patch.object(
            protocol_register,
            "_build_protocol_headers",
            return_value={},
        ), mock.patch.object(
            protocol_register,
            "_session_request",
            side_effect=[RuntimeError("curl: (28) Operation timed out"), response],
        ) as session_request:
            result = protocol_register._send_email_otp(
                session,
                explicit_proxy="http://proxy:8080",
                header_builder=None,
            )
        self.assertIs(result, response)
        self.assertEqual(2, session_request.call_count)

    def test_submit_phone_number_retries_transient_network_error(self) -> None:
        session = mock.Mock()
        response = SimpleNamespace(status_code=200, url="https://auth.openai.com/api/accounts/add-phone/send")
        with mock.patch.object(
            protocol_register,
            "_phone_resume_sentinel_context",
            return_value=mock.Mock(),
        ), mock.patch.object(
            protocol_register,
            "_build_protocol_headers",
            return_value={"referer": "https://auth.openai.com/add-phone"},
        ), mock.patch.object(
            protocol_register,
            "_session_request",
            side_effect=[RuntimeError("curl: (28) Operation timed out"), response],
        ) as session_request, mock.patch.object(
            protocol_register,
            "_extract_page_type",
            return_value="sms_verification",
        ):
            result = protocol_register._submit_phone_number_via_protocol_session(
                resume_context={"continueUrl": "https://auth.openai.com/add-phone"},
                session=session,
                phone_number="+15551234567",
                explicit_proxy="http://proxy:8080",
            )

        self.assertEqual("phone_number_submitted", result["status"])
        self.assertEqual("sms_verification", result["pageType"])
        self.assertEqual(2, session_request.call_count)

    def test_get_sentinel_header_for_signup_uses_configurable_sentinel_timeout(self) -> None:
        response = SimpleNamespace(status_code=200)
        response.json = lambda: {
            "token": "sentinel-c",
            "proofofwork": {"required": False},
            "turnstile": {"required": False},
        }
        with mock.patch.dict(
            os.environ,
            {"PROTOCOL_SENTINEL_HTTP_TIMEOUT_SECONDS": "41.5"},
            clear=False,
        ), mock.patch.object(
            protocol_register,
            "get_pow_token",
            return_value="req-token",
        ), mock.patch.object(
            protocol_register,
            "_session_request",
            return_value=response,
        ) as session_request:
            header = protocol_register._get_sentinel_header_for_signup(
                mock.Mock(),
                device_id="device-123",
                flow="authorize_continue",
                request_kind="repair-authorize-continue",
                explicit_proxy="http://proxy:8080",
                sentinel_context=SimpleNamespace(user_agent="ua", data_build="build-1", profile={}),
            )

        self.assertEqual(41.5, session_request.call_args.kwargs["timeout"])
        payload = json.loads(header)
        self.assertEqual("req-token", payload["p"])
        self.assertEqual("", payload["t"])
        self.assertEqual("sentinel-c", payload["c"])
        self.assertEqual("device-123", payload["id"])
        self.assertEqual("authorize_continue", payload["flow"])

    def test_generate_sentinel_headers_for_session_uses_configurable_sentinel_timeout(self) -> None:
        response = SimpleNamespace(status_code=200)
        response.json = lambda: {
            "token": "sentinel-chat",
            "turnstile": {},
            "proofofwork": {"required": False},
        }
        with mock.patch.dict(
            os.environ,
            {"PROTOCOL_SENTINEL_HTTP_TIMEOUT_SECONDS": "44"},
            clear=False,
        ), mock.patch.object(
            protocol_register,
            "get_pow_token",
            return_value="req-token",
        ), mock.patch.object(
            protocol_register,
            "generate_proof_token",
            return_value="proof-token",
        ), mock.patch.object(
            protocol_register,
            "_session_request",
            return_value=response,
        ) as session_request:
            headers = protocol_register._generate_sentinel_headers_for_session(
                mock.Mock(),
                explicit_proxy="http://proxy:8080",
                user_agent="ua",
                device_id="device-456",
                data_build="build-2",
                request_kind="repair-password-verify",
            )

        self.assertEqual("sentinel-chat-requirements", session_request.call_args.kwargs["request_label"])
        self.assertEqual(44.0, session_request.call_args.kwargs["timeout"])
        payload = json.loads(headers["openai-sentinel-token"])
        self.assertEqual("proof-token", payload["p"])
        self.assertEqual("", payload["t"])
        self.assertEqual("sentinel-chat", payload["c"])
        self.assertEqual("device-456", payload["id"])
        self.assertEqual("password_verify", payload["flow"])

    def test_extract_chatgpt_client_bootstrap_reads_access_token(self) -> None:
        html = """
        <html>
          <body>
            <script id="client-bootstrap" type="application/json">
              {"authStatus":"logged_in","session":{"accessToken":"tok_demo","account":{"id":"acct_1","planType":"free","structure":"personal"},"user":{"id":"user_1","email":"demo@example.com"}}}
            </script>
          </body>
        </html>
        """
        payload = protocol_chatgpt_login._extract_chatgpt_client_bootstrap(html)
        self.assertEqual("logged_in", payload.get("authStatus"))
        self.assertEqual("tok_demo", (payload.get("session") or {}).get("accessToken"))

    def test_platform_org_init_persists_oauth_refresh_material_without_returning_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            seed_path = Path(tmp_dir) / "small-success.json"
            organization_update_payloads: list[dict[str, object]] = []
            seed_path.write_text(
                json.dumps(
                    {
                        "email": "user@example.com",
                        "mailboxRef": "mailbox-ref",
                        "mailboxSessionId": "mailbox-session",
                        "finalUrl": "https://platform.openai.com/auth/callback?code=code_123&state=state_123",
                        "platformAuth": {
                            "codeVerifier": "verifier_123",
                            "state": "state_123",
                            "deviceId": "device_123",
                        },
                    }
                ),
                encoding="utf-8",
            )

            def request_side_effect(*_args: object, **kwargs: object) -> SimpleNamespace:
                request_label = str(kwargs.get("request_label") or "")
                if request_label == "platform-oauth-token":
                    return SimpleNamespace(
                        status_code=200,
                        json=lambda: {
                            "access_token": "access.demo",
                            "refresh_token": "refresh.demo",
                            "id_token": "id.demo",
                            "expires_in": 3600,
                            "token_type": "Bearer",
                        },
                    )
                if request_label == "platform-onboarding-login":
                    return SimpleNamespace(
                        status_code=200,
                        json=lambda: {
                            "user": {
                                "id": "user_123",
                                "session": {"sensitive_id": "session_token_123"},
                                "orgs": {
                                    "data": [
                                        {
                                            "id": "org_123",
                                            "title": "personal",
                                            "name": "personal",
                                            "settings": {"completed_platform_onboarding": False},
                                            "projects": {"data": [{"id": "proj_123", "title": "Default"}]},
                                        }
                                    ]
                                },
                            }
                        },
                    )
                if request_label == "platform-organization-update":
                    request_json = kwargs.get("json")
                    self.assertIsInstance(request_json, dict)
                    organization_update_payloads.append(dict(request_json))
                    return SimpleNamespace(status_code=200, json=lambda: {"ok": True})
                if request_label == "platform-organization-user-update":
                    return SimpleNamespace(status_code=200, json=lambda: {"ok": True})
                raise AssertionError(f"unexpected request label: {request_label}")

            with mock.patch.object(protocol_platform_org, "flow_network_env", return_value=contextlib.nullcontext()), mock.patch.object(
                protocol_platform_org,
                "_session_request",
                side_effect=request_side_effect,
            ), mock.patch.object(
                protocol_platform_org,
                "_build_platform_headers",
                return_value={},
            ), mock.patch.object(protocol_platform_org, "_best_effort_warm_platform_permissions"):
                result = protocol_platform_org.run_protocol_platform_organization_init_from_path(
                    source_path=seed_path,
                    explicit_proxy="http://proxy:8080",
                    organization_name="Personal",
                    organization_title="Personal",
                )

            self.assertTrue(result["ok"])
            self.assertEqual("completed", result["status"])
            self.assertNotIn("refreshToken", result)
            self.assertNotIn("refresh_token", result)

            persisted = json.loads(seed_path.read_text(encoding="utf-8"))
            self.assertEqual("access.demo", persisted["accessToken"])
            self.assertEqual("refresh.demo", persisted["refreshToken"])
            self.assertEqual("id.demo", persisted["idToken"])
            self.assertTrue(str(persisted["expiresAt"]).endswith("Z"))
            self.assertEqual(protocol_platform_org._PLATFORM_AUTH0_CLIENT_ID, persisted["oauthClientId"])
            self.assertEqual(protocol_platform_org._PLATFORM_AUTH0_TOKEN_URL, persisted["oauthTokenEndpoint"])
            self.assertEqual("oauth_token", persisted["refreshStrategy"])
            oauth_tokens = persisted["chatgptLoginDetails"]["oauthTokens"]
            self.assertEqual("access.demo", oauth_tokens["access_token"])
            self.assertEqual("refresh.demo", oauth_tokens["refresh_token"])
            self.assertEqual("id.demo", oauth_tokens["id_token"])
            self.assertEqual(3600, oauth_tokens["expires_in"])
            self.assertEqual("Bearer", oauth_tokens["token_type"])
            self.assertTrue(str(oauth_tokens["exchanged_at"]).endswith("Z"))
            self.assertEqual(1, len(organization_update_payloads))
            self.assertEqual("personal", organization_update_payloads[0]["name"])
            self.assertEqual("Personal", organization_update_payloads[0]["title"])

    def test_initialize_platform_organization_dispatch_defaults_to_personal_title(self) -> None:
        with mock.patch.object(
            easyprotocol_flow,
            "run_protocol_platform_organization_init_from_path",
            return_value={"ok": True, "status": "completed"},
        ) as platform_org_init:
            result = easyprotocol_flow.dispatch_easyprotocol_step(
                step_type="initialize_platform_organization",
                step_input={"source_path": "/tmp/source.json", "proxy_url": "http://proxy.local:8080"},
            )

        self.assertTrue(result["ok"])
        platform_org_init.assert_called_once_with(
            source_path="/tmp/source.json",
            explicit_proxy="http://proxy.local:8080",
            organization_name="Personal",
            organization_title="Personal",
            developer_persona="student",
        )

    def test_login_openai_community_dispatch_uses_easybrowser_login_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = Path(tmp_dir) / "small-success.json"
            source_path.write_text(
                json.dumps(
                    {
                        "email": "community-user@example.com",
                        "password": "test-password",
                        "mailboxRef": "cloudflare_temp_email:community-user@example.com",
                        "mailboxSessionId": "mailbox-session-1",
                    }
                ),
                encoding="utf-8",
            )

            captured: dict[str, object] = {}

            def _fake_run_easybrowser_login_flow(**kwargs: object) -> dict[str, object]:
                captured.update(kwargs)
                return {
                    "ok": True,
                    "target_url": "https://community.openai.com/",
                    "email": kwargs.get("email"),
                    "mailbox_ref": kwargs.get("mailbox_ref"),
                    "session_id": "browser-session-1",
                    "task_id": "task-community-1",
                    "status": "completed",
                }

            with mock.patch.object(
                protocol_community_login,
                "run_easybrowser_openai_web_login_flow",
                side_effect=_fake_run_easybrowser_login_flow,
            ):
                result = easyprotocol_flow.dispatch_easyprotocol_step(
                    step_type="login_openai_community",
                    step_input={
                        "source_path": str(source_path),
                        "proxy_url": "http://easy-proxy.local:25001",
                        "startup_url": "https://community.openai.com/",
                    },
                )

        self.assertTrue(result["ok"])
        self.assertEqual("community_login_completed", result["status"])
        self.assertEqual("community-user@example.com", result["email"])
        self.assertEqual("https://community.openai.com/", result["targetUrl"])
        self.assertEqual("community-user@example.com", captured["email"])
        self.assertEqual("test-password", captured["password"])
        self.assertEqual("cloudflare_temp_email:community-user@example.com", captured["mailbox_ref"])
        self.assertEqual("mailbox-session-1", captured["mailbox_session_id"])
        self.assertEqual("http://easy-proxy.local:25001", captured["proxy_url"])
        self.assertEqual("https://community.openai.com/", captured["startup_url"])

    def test_login_openai_community_rejects_non_community_target_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = Path(tmp_dir) / "small-success.json"
            source_path.write_text(
                json.dumps(
                    {
                        "email": "community-user@example.com",
                        "password": "test-password",
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(
                protocol_community_login,
                "run_easybrowser_openai_web_login_flow",
                return_value={
                    "ok": True,
                    "target_url": "https://auth.openai.com/api/accounts/authorize",
                    "status": "completed",
                },
            ):
                result = easyprotocol_flow.dispatch_easyprotocol_step(
                    step_type="login_openai_community",
                    step_input={
                        "source_path": str(source_path),
                        "startup_url": "https://community.openai.com/",
                    },
                )

        self.assertFalse(result["ok"])
        self.assertEqual("community_login_target_mismatch", result["status"])
        self.assertEqual("https://auth.openai.com/api/accounts/authorize", result["targetUrl"])

    def test_account_availability_audit_dispatch_recovers_mailbox_and_classifies_http_login_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            loginable_path = Path(tmp_dir) / "loginable.json"
            deleted_path = Path(tmp_dir) / "deleted.json"
            loginable_path.write_text(
                json.dumps(
                    {
                        "email": "ok@example.com",
                        "password": "ok-password",
                    }
                ),
                encoding="utf-8",
            )
            deleted_path.write_text(
                json.dumps(
                    {
                        "email": "deleted@example.com",
                        "password": "deleted-password",
                    }
                ),
                encoding="utf-8",
            )
            login_calls: list[dict[str, object]] = []

            def _fake_recover_mailbox_by_email(**kwargs: object) -> dict[str, object]:
                email = str(kwargs.get("email_address") or "")
                self.assertEqual(
                    {
                        "providerTypeKey": "cloudflare_temp_email",
                        "providerInstanceId": "cloudflare_temp_email_shared_default",
                    },
                    kwargs.get("recovery_data_credential"),
                )
                return {
                    "recovered": True,
                    "session": {
                        "id": f"session-{email}",
                        "emailAddress": email,
                        "mailboxRef": f"cloudflare_temp_email:{email}",
                        "providerTypeKey": "cloudflare_temp_email",
                    },
                }

            def _fake_run_protocol_chatgpt_login_init_from_path(**kwargs: object) -> dict[str, object]:
                login_calls.append(dict(kwargs))
                source_path = str(kwargs.get("source_path") or "")
                if source_path.endswith("deleted.json"):
                    raise protocol_chatgpt_login.ProtocolRuntimeError(
                        "chatgpt_login_otp_validate_failed status=403 body=You do not have an account because it has been deleted or deactivated",
                        stage="stage_otp_validate",
                        detail="chatgpt_login_email_otp_validate",
                        category="flow_error",
                    )
                return {
                    "ok": True,
                    "status": "completed",
                    "finalUrl": "https://chatgpt.com/",
                }

            with mock.patch.object(
                protocol_account_availability,
                "recover_mailbox_by_email",
                side_effect=_fake_recover_mailbox_by_email,
            ), mock.patch.object(
                protocol_account_availability,
                "run_protocol_chatgpt_login_init_from_path",
                side_effect=_fake_run_protocol_chatgpt_login_init_from_path,
            ):
                result = easyprotocol_flow.dispatch_easyprotocol_step(
                    step_type="audit_openai_account_availability",
                    step_input={
                        "targets": [
                            {
                                "source_path": str(loginable_path),
                                "email": "ok@example.com",
                                "recovery_data_credential": {
                                    "providerTypeKey": "cloudflare_temp_email",
                                    "providerInstanceId": "cloudflare_temp_email_shared_default",
                                },
                            },
                            {
                                "source_path": str(deleted_path),
                                "email": "deleted@example.com",
                                "recovery_data_credential": {
                                    "providerTypeKey": "cloudflare_temp_email",
                                    "providerInstanceId": "cloudflare_temp_email_shared_default",
                                },
                            },
                        ],
                        "proxy_url": "http://easy-proxy.local:25001",
                        "login_entry_url": "https://chatgpt.com/",
                        "recover_mailbox": True,
                    },
                )

        self.assertTrue(result["ok"])
        self.assertEqual("completed", result["status"])
        self.assertEqual(
            ["login_succeeded", "deleted_confirmed"],
            [item["status"] for item in result["results"]],
        )
        self.assertEqual("session-ok@example.com", login_calls[0]["mailbox_session_id"])
        self.assertEqual("cloudflare_temp_email:ok@example.com", login_calls[0]["mailbox_ref"])
        self.assertEqual("http://easy-proxy.local:25001", login_calls[0]["explicit_proxy"])

    def test_account_availability_audit_dispatch_parses_recover_mailbox_false_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = Path(tmp_dir) / "seed.json"
            source_path.write_text(
                json.dumps(
                    {
                        "email": "seed@example.com",
                        "password": "secret",
                        "recoveryDataCredential": {
                            "emailAddress": "seed@example.com",
                            "providerTypeKey": "cloudflare_temp_email",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(
                protocol_account_availability,
                "recover_mailbox_by_email",
            ) as recover_mailbox_by_email, mock.patch.object(
                protocol_account_availability,
                "run_protocol_chatgpt_login_init_from_path",
                return_value={"ok": True, "status": "completed", "finalUrl": "https://chatgpt.com/"},
            ):
                result = easyprotocol_flow.dispatch_easyprotocol_step(
                    step_type="audit_openai_account_availability",
                    step_input={
                        "targets": [
                            {
                                "source_path": str(source_path),
                                "email": "seed@example.com",
                            }
                        ],
                        "recover_mailbox": "false",
                    },
                )

        self.assertTrue(result["ok"])
        recover_mailbox_by_email.assert_not_called()

    def test_account_availability_audit_passes_timeout_budget_to_http_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = Path(tmp_dir) / "seed.json"
            source_path.write_text(
                json.dumps(
                    {
                        "email": "budget@example.com",
                        "password": "secret",
                        "mailboxRef": "cloudflare_temp_email:budget@example.com",
                        "mailboxSessionId": "mailbox-session-budget",
                    }
                ),
                encoding="utf-8",
            )
            login_calls: list[dict[str, object]] = []

            def _fake_run_protocol_chatgpt_login_init_from_path(**kwargs: object) -> dict[str, object]:
                login_calls.append(dict(kwargs))
                return {"ok": True, "status": "completed", "finalUrl": "https://chatgpt.com/"}

            with mock.patch.object(
                protocol_account_availability,
                "run_protocol_chatgpt_login_init_from_path",
                side_effect=_fake_run_protocol_chatgpt_login_init_from_path,
            ):
                result = easyprotocol_flow.dispatch_easyprotocol_step(
                    step_type="audit_openai_account_availability",
                    step_input={
                        "targets": [
                            {
                                "source_path": str(source_path),
                                "email": "budget@example.com",
                            }
                        ],
                        "timeout_seconds": 17,
                    },
                )

        self.assertTrue(result["ok"])
        self.assertEqual("login_succeeded", result["results"][0]["status"])
        self.assertEqual(1, len(login_calls))
        self.assertIn("timeout_seconds", login_calls[0])
        self.assertGreater(float(login_calls[0]["timeout_seconds"]), 0)
        self.assertLessEqual(float(login_calls[0]["timeout_seconds"]), 17)

    def test_chatgpt_login_email_otp_wait_respects_remaining_timeout_budget(self) -> None:
        wait_calls: list[dict[str, object]] = []

        def _fake_wait_openai_code(**kwargs: object) -> str:
            wait_calls.append(dict(kwargs))
            return "123456"

        with mock.patch.object(
            protocol_chatgpt_login,
            "wait_openai_code",
            side_effect=_fake_wait_openai_code,
        ), mock.patch.dict(os.environ, {"OTP_TIMEOUT_SECONDS": "300"}, clear=False):
            code = protocol_chatgpt_login._wait_for_email_otp(
                mailbox_ref="cloudflare_temp_email:budget@example.com",
                mailbox_session_id="mailbox-session-budget",
                min_mail_id=0,
                timeout_seconds=7,
            )

        self.assertEqual("123456", code)
        self.assertEqual(1, len(wait_calls))
        self.assertEqual(7, wait_calls[0]["timeout_seconds"])

    def test_account_availability_audit_returns_timeout_before_http_login_when_budget_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = Path(tmp_dir) / "seed.json"
            source_path.write_text(
                json.dumps(
                    {
                        "email": "timeout@example.com",
                        "password": "secret",
                        "mailboxRef": "cloudflare_temp_email:timeout@example.com",
                        "mailboxSessionId": "mailbox-session-timeout",
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(
                protocol_account_availability.time,
                "monotonic",
                side_effect=[100.0, 100.5, 101.1],
            ), mock.patch.object(
                protocol_account_availability,
                "run_protocol_chatgpt_login_init_from_path",
            ) as run_login:
                result = easyprotocol_flow.dispatch_easyprotocol_step(
                    step_type="audit_openai_account_availability",
                    step_input={
                        "targets": [
                            {
                                "source_path": str(source_path),
                                "email": "timeout@example.com",
                            }
                        ],
                        "recover_mailbox": False,
                        "timeout_seconds": 1,
                    },
                )

        self.assertTrue(result["ok"])
        self.assertEqual("inconclusive", result["results"][0]["status"])
        self.assertEqual("account_audit_timeout_exceeded", result["results"][0]["detail"])
        self.assertEqual("timeout", result["results"][0]["browser"]["status"])
        run_login.assert_not_called()

    def test_account_availability_audit_prefers_refresh_token_validation_before_http_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = Path(tmp_dir) / "refresh-seed.json"
            source_path.write_text(
                json.dumps(
                    {
                        "email": "refresh-ok@example.com",
                        "password": "secret",
                        "refresh_token": "refresh-token-1",
                        "access_token": "access-token-1",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(
                protocol_account_availability,
                "_perform_refresh_token_exchange",
                return_value={"access_token": "new-access-token"},
            ) as refresh_exchange, mock.patch.object(
                protocol_account_availability,
                "run_protocol_chatgpt_login_init_from_path",
            ) as run_login:
                result = easyprotocol_flow.dispatch_easyprotocol_step(
                    step_type="audit_openai_account_availability",
                    step_input={
                        "targets": [
                            {
                                "source_path": str(source_path),
                                "email": "refresh-ok@example.com",
                            }
                        ],
                        "proxy_url": "http://easy-proxy.local:25001",
                    },
                )

        self.assertTrue(result["ok"])
        self.assertEqual("login_succeeded", result["results"][0]["status"])
        self.assertEqual("refresh_token_valid", result["results"][0]["detail"])
        refresh_exchange.assert_called_once()
        run_login.assert_not_called()

    def test_account_availability_audit_falls_back_to_http_login_and_classifies_deleted_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = Path(tmp_dir) / "deleted-seed.json"
            source_path.write_text(
                json.dumps(
                    {
                        "email": "deleted@example.com",
                        "password": "secret",
                        "mailboxRef": "cloudflare_temp_email:deleted@example.com",
                        "mailboxSessionId": "mailbox-session-deleted",
                        "chatgptLoginDetails": {
                            "clientBootstrap": {
                                "accessToken": "bootstrap-access-token",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            def _fake_recover_mailbox_by_email(**_: object) -> dict[str, object]:
                return {
                    "recovered": True,
                    "session": {
                        "id": "mailbox-session-deleted-recovered",
                        "mailboxRef": "cloudflare_temp_email:deleted@example.com",
                    },
                }

            with mock.patch.object(
                protocol_account_availability,
                "_perform_refresh_token_exchange",
                side_effect=RuntimeError("team_refresh_token_required"),
            ) as refresh_exchange, mock.patch.object(
                protocol_account_availability,
                "recover_mailbox_by_email",
                side_effect=_fake_recover_mailbox_by_email,
            ) as recover_mailbox_by_email, mock.patch.object(
                protocol_account_availability,
                "run_protocol_chatgpt_login_init_from_path",
                side_effect=protocol_chatgpt_login.ProtocolRuntimeError(
                    "chatgpt_login_otp_validate_failed status=403 body=You do not have an account because it has been deleted or deactivated",
                    stage="stage_otp_validate",
                    detail="chatgpt_login_email_otp_validate",
                    category="flow_error",
                ),
            ) as run_login:
                result = easyprotocol_flow.dispatch_easyprotocol_step(
                    step_type="audit_openai_account_availability",
                    step_input={
                        "targets": [
                            {
                                "source_path": str(source_path),
                                "email": "deleted@example.com",
                            }
                        ],
                        "proxy_url": "http://easy-proxy.local:25001",
                        "recover_mailbox": True,
                    },
                )

        self.assertTrue(result["ok"])
        self.assertEqual("deleted_confirmed", result["results"][0]["status"])
        self.assertIn("deleted or deactivated", result["results"][0]["detail"])
        refresh_exchange.assert_not_called()
        recover_mailbox_by_email.assert_called_once()
        run_login.assert_called_once()

    def test_recover_mailbox_by_email_posts_recovery_data_credential(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        def _fake_post_json(path: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((path, payload))
            return {"result": {"recovered": True}}

        recovery_data_credential = {
            "emailAddress": "recoverable@example.com",
            "providerTypeKey": "cloudflare_temp_email",
            "providerInstanceId": "cloudflare_temp_email_shared_default",
            "hostId": "python-register-orchestration",
            "opaque": "keep-this",
        }

        with mock.patch.object(easy_email_client, "_post_json", side_effect=_fake_post_json):
            result = easy_email_client.recover_mailbox_by_email(
                email_address="recoverable@example.com",
                provider_type_key="cloudflare_temp_email",
                host_id="python-register-orchestration",
                recovery_data_credential=recovery_data_credential,
            )

        self.assertTrue(result["recovered"])
        self.assertEqual("/mail/mailboxes/recover-by-email", calls[0][0])
        self.assertEqual(
            {
                "emailAddress": "recoverable@example.com",
                "providerTypeKey": "cloudflare_temp_email",
                "hostId": "python-register-orchestration",
                "recoveryDataCredential": recovery_data_credential,
            },
            calls[0][1],
        )

    def test_openai_community_easybrowser_client_executes_login_flow_and_releases_session(self) -> None:
        post_calls: list[tuple[str, dict[str, object]]] = []
        get_calls: list[str] = []

        def _fake_post_json(_session: object, _base_url: str, path: str, payload: dict[str, object]) -> dict[str, object]:
            post_calls.append((path, payload))
            if path == "/v1/browser/sessions/acquire":
                return {
                    "success": True,
                    "data": {
                        "session": {
                            "session_id": "browser-session-1",
                        }
                    },
                }
            if path == "/v1/browser/sessions/browser-session-1/flows/execute":
                return {
                    "success": True,
                    "data": {
                        "task_id": "task-community-1",
                    },
                }
            if path == "/v1/browser/sessions/browser-session-1/release":
                return {
                    "success": True,
                    "data": {},
                }
            raise AssertionError(path)

        def _fake_get_json(_session: object, _base_url: str, path: str) -> dict[str, object]:
            get_calls.append(path)
            self.assertEqual("/v1/tasks/task-community-1", path)
            return {
                "success": True,
                "data": {
                    "state": "succeeded",
                    "result": {
                        "artifacts": {
                            "target_url": "https://community.openai.com/",
                        }
                    },
                },
            }

        with mock.patch.object(protocol_community_login, "_post_json", side_effect=_fake_post_json), mock.patch.object(
            protocol_community_login,
            "_get_json",
            side_effect=_fake_get_json,
        ), mock.patch.object(protocol_community_login.requests, "Session", return_value=object()):
            result = protocol_community_login.run_easybrowser_openai_web_login_flow(
                email="community-user@example.com",
                password="test-password",
                mailbox_ref="cloudflare_temp_email:community-user@example.com",
                mailbox_session_id="mailbox-session-1",
                proxy_url="http://easy-proxy.local:25001",
                startup_url="https://community.openai.com/",
                easybrowser_base_url="http://easy-browser.local:8080",
                timeout_seconds=3,
                poll_interval_seconds=0.1,
            )

        self.assertTrue(result["ok"])
        self.assertEqual("https://community.openai.com/", result["target_url"])
        self.assertEqual(["/v1/tasks/task-community-1"], get_calls)
        self.assertEqual(
            [
                "/v1/browser/sessions/acquire",
                "/v1/browser/sessions/browser-session-1/flows/execute",
                "/v1/browser/sessions/browser-session-1/release",
            ],
            [path for path, _payload in post_calls],
        )
        execute_payload = post_calls[1][1]
        acquire_payload = post_calls[0][1]
        self.assertEqual("direct", acquire_payload["mode"])
        self.assertEqual("chrome", acquire_payload["provider_hint"])
        self.assertEqual("chrome", acquire_payload["browser_backend"])
        self.assertEqual("login", execute_payload["flow_type"])
        step_payload = execute_payload["steps"][0]
        self.assertEqual("openai_web_login", step_payload["step_type"])
        self.assertEqual("https://community.openai.com/", step_payload["input"]["startup_url"])
        self.assertEqual("community-user@example.com", step_payload["input"]["auth"]["email"])
        self.assertEqual("mailbox-session-1", step_payload["input"]["auth"]["mailbox_session_id"])

    def test_openai_community_easybrowser_client_allows_isolated_backend_override(self) -> None:
        post_calls: list[tuple[str, dict[str, object]]] = []

        def _fake_post_json(_session: object, _base_url: str, path: str, payload: dict[str, object]) -> dict[str, object]:
            post_calls.append((path, payload))
            if path == "/v1/browser/sessions/acquire":
                return {"success": True, "data": {"session": {"session_id": "browser-session-1"}}}
            if path == "/v1/browser/sessions/browser-session-1/flows/execute":
                return {"success": True, "data": {"task_id": "task-community-1"}}
            if path == "/v1/browser/sessions/browser-session-1/release":
                return {"success": True, "data": {}}
            raise AssertionError(path)

        def _fake_get_json(_session: object, _base_url: str, path: str) -> dict[str, object]:
            self.assertEqual("/v1/tasks/task-community-1", path)
            return {
                "success": True,
                "data": {
                    "state": "succeeded",
                    "result": {"artifacts": {"target_url": "https://community.openai.com/"}},
                },
            }

        with mock.patch.dict(
            os.environ,
            {
                "OPENAI_COMMUNITY_BROWSER_PROVIDER_HINT": "camoufox",
                "OPENAI_COMMUNITY_BROWSER_BACKEND": "camoufox",
            },
        ), mock.patch.object(protocol_community_login, "_post_json", side_effect=_fake_post_json), mock.patch.object(
            protocol_community_login,
            "_get_json",
            side_effect=_fake_get_json,
        ), mock.patch.object(protocol_community_login.requests, "Session", return_value=object()):
            result = protocol_community_login.run_easybrowser_openai_web_login_flow(
                email="community-user@example.com",
                password="test-password",
                mailbox_ref="cloudflare_temp_email:community-user@example.com",
                mailbox_session_id="mailbox-session-1",
                proxy_url="http://easy-proxy.local:25001",
                startup_url="https://community.openai.com/",
                easybrowser_base_url="http://easy-browser.local:8080",
                timeout_seconds=3,
                poll_interval_seconds=0.1,
            )

        self.assertTrue(result["ok"])
        acquire_payload = post_calls[0][1]
        self.assertEqual("camoufox", acquire_payload["provider_hint"])
        self.assertEqual("camoufox", acquire_payload["browser_backend"])

    def test_openai_community_easybrowser_client_releases_session_after_failed_task(self) -> None:
        post_paths: list[str] = []

        def _fake_post_json(_session: object, _base_url: str, path: str, payload: dict[str, object]) -> dict[str, object]:
            post_paths.append(path)
            if path == "/v1/browser/sessions/acquire":
                return {
                    "success": True,
                    "data": {
                        "session": {
                            "session_id": "browser-session-1",
                        }
                    },
                }
            if path == "/v1/browser/sessions/browser-session-1/flows/execute":
                return {
                    "success": True,
                    "data": {
                        "task_id": "task-community-1",
                    },
                }
            if path == "/v1/browser/sessions/browser-session-1/release":
                return {
                    "success": True,
                    "data": {},
                }
            raise AssertionError(path)

        def _fake_get_json(_session: object, _base_url: str, path: str) -> dict[str, object]:
            self.assertEqual("/v1/tasks/task-community-1", path)
            return {
                "success": True,
                "data": {
                    "state": "failed",
                    "error": {
                        "code": "auth_challenge",
                        "message": "Cloudflare challenge blocked login",
                    },
                    "result": {
                        "artifacts": {
                            "target_url": "https://auth.openai.com/api/accounts/authorize",
                        }
                    },
                },
            }

        with mock.patch.object(protocol_community_login, "_post_json", side_effect=_fake_post_json), mock.patch.object(
            protocol_community_login,
            "_get_json",
            side_effect=_fake_get_json,
        ), mock.patch.object(protocol_community_login.requests, "Session", return_value=object()):
            result = protocol_community_login.run_easybrowser_openai_web_login_flow(
                email="community-user@example.com",
                password="test-password",
                mailbox_ref="cloudflare_temp_email:community-user@example.com",
                mailbox_session_id="mailbox-session-1",
                proxy_url="http://easy-proxy.local:25001",
                startup_url="https://community.openai.com/",
                easybrowser_base_url="http://easy-browser.local:8080",
                timeout_seconds=3,
                poll_interval_seconds=0.1,
            )

        self.assertFalse(result["ok"])
        self.assertEqual("auth_challenge", result["status"])
        self.assertEqual("https://auth.openai.com/api/accounts/authorize", result["target_url"])
        self.assertEqual("/v1/browser/sessions/browser-session-1/release", post_paths[-1])

    def test_chatgpt_login_details_merge_preserves_existing_oauth_tokens(self) -> None:
        authenticated_session = {
            "version": 1,
            "capturedAt": 123,
            "proxyFingerprint": "fingerprint",
            "sessionCookies": [{"name": "session", "value": "value", "domain": ".chatgpt.com"}],
            "browser": {"userAgent": "ua", "deviceId": "did"},
        }
        details = protocol_chatgpt_login._merge_chatgpt_login_details(
            seed_payload={
                "chatgptLoginDetails": {
                    "oauthTokens": {
                        "access_token": "access.demo",
                        "refresh_token": "refresh.demo",
                        "id_token": "id.demo",
                    }
                }
            },
            account_entries=[{"id": "acct_1"}],
            client_bootstrap={
                "authStatus": "logged_in",
                "accountId": "acct_1",
                "planType": "free",
                "structure": "personal",
                "accessTokenPresent": True,
                "accessToken": "bootstrap.demo",
                "userId": "user_1",
                "email": "user@example.com",
            },
            page_type="chatgpt_logged_in",
            network_attempt=2,
            authenticated_session=authenticated_session,
        )

        self.assertEqual("refresh.demo", details["oauthTokens"]["refresh_token"])
        self.assertEqual("bootstrap.demo", details["clientBootstrap"]["accessToken"])
        self.assertEqual([{"id": "acct_1"}], details["accounts"])
        self.assertEqual("chatgpt_logged_in", details["pageType"])
        self.assertEqual(2, details["networkAttempt"])
        self.assertEqual(authenticated_session, details["authenticatedSession"])

    def test_authenticated_session_context_is_bound_to_proxy_and_filters_cookie_domains(self) -> None:
        session = SimpleNamespace(
            cookies=[
                SimpleNamespace(
                    name="session",
                    value="session-value",
                    domain=".chatgpt.com",
                    path="/",
                    secure=True,
                    expires=2000,
                ),
                SimpleNamespace(
                    name="unrelated",
                    value="ignored",
                    domain="example.com",
                    path="/",
                    secure=True,
                    expires=2000,
                ),
                SimpleNamespace(
                    name="expired",
                    value="ignored-expired",
                    domain=".openai.com",
                    path="/",
                    secure=True,
                    expires=999,
                ),
            ]
        )
        with mock.patch.object(protocol_register.time, "time", return_value=1000):
            context = protocol_register.export_authenticated_session_context(
                session=session,
                user_agent="test-user-agent",
                device_id="did-123",
                explicit_proxy="http://proxy.local:8080",
            )

        self.assertEqual(1, len(context["sessionCookies"]))
        self.assertEqual(".chatgpt.com", context["sessionCookies"][0]["domain"])
        self.assertNotIn("proxy.local", json.dumps(context))
        restored_session = mock.Mock()
        with mock.patch.object(protocol_register.time, "time", return_value=1001), mock.patch.object(
            protocol_register,
            "_restore_protocol_session_from_resume_context",
            return_value=restored_session,
        ) as restore_session:
            result = protocol_register.restore_authenticated_session_from_context(
                context,
                explicit_proxy="http://proxy.local:8080",
            )
        self.assertIs(restored_session, result)
        restore_session.assert_called_once()

        with mock.patch.object(protocol_register.time, "time", return_value=1001):
            with self.assertRaisesRegex(RuntimeError, "authenticated_session_proxy_mismatch"):
                protocol_register.restore_authenticated_session_from_context(
                    context,
                    explicit_proxy="http://different-proxy.local:8080",
                )

    def test_obtain_team_mother_oauth_force_email_auth_skips_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            source_path = tmp_path / "mother.json"
            source_path.write_text(
                json.dumps(
                    {
                        "email": "mother@example.com",
                        "refresh_token": "rt_demo",
                    }
                ),
                encoding="utf-8",
            )
            output_dir = tmp_path / "out"
            with mock.patch.object(
                easyprotocol_flow,
                "refresh_team_auth_once",
            ) as refresh_team_auth_once, mock.patch.object(
                easyprotocol_flow,
                "run_protocol_oauth_from_path",
                return_value=SimpleNamespace(
                    auth={"email": "mother@example.com", "user_id": "user_123"},
                    email="mother@example.com",
                    account_id="acct_123",
                    storage_path="/tmp/codex-123.json",
                ),
            ) as run_protocol_oauth_from_path:
                result = easyprotocol_flow.dispatch_easyprotocol_step(
                    step_type="obtain_team_mother_oauth",
                    step_input={
                        "source_path": str(source_path),
                        "output_dir": str(output_dir),
                        "force_email_auth": True,
                    },
                )
        refresh_team_auth_once.assert_not_called()
        run_protocol_oauth_from_path.assert_called_once()
        self.assertEqual("email", result["authMode"])
        self.assertFalse(bool(result.get("refreshOnly")))

    def test_run_protocol_oauth_once_returns_phone_verification_required_result(self) -> None:
        seed_payload = {
            "email": "user@example.com",
            "password": "pw",
            "mailboxRef": "mailtm:test",
            "mailboxSessionId": "mailbox_123",
            "firstName": "User",
            "lastName": "Example",
            "birthdate": "2000-01-01",
        }

        class _FlowProxy:
            proxy_url = "http://easy-proxy:25000"

        with mock.patch.object(
            protocol_oauth,
            "_refresh_seed_mailbox_binding",
            return_value=(
                {
                    "email": "user@example.com",
                    "password": "pw",
                    "mailbox_ref": "mailtm:test",
                    "session_id": "mailbox_123",
                    "first_name": "User",
                    "last_name": "Example",
                    "birthdate": "2000-01-01",
                },
                {},
            ),
        ), mock.patch.object(
            protocol_oauth,
            "_ensure_protocol_oauth_easy_runtime_defaults",
        ), mock.patch.object(
            protocol_oauth,
            "flow_network_env",
            return_value=contextlib.nullcontext(),
        ), mock.patch.object(
            protocol_oauth,
            "lease_flow_proxy",
            return_value=contextlib.nullcontext(_FlowProxy()),
        ), mock.patch.object(
            protocol_oauth,
            "run_protocol_repair_once",
            return_value=protocol_register.ProtocolRegistrationResult(
                email="user@example.com",
                auth={"mailboxRef": "mailtm:test"},
                phone_verification_required=True,
                page_type="add_phone",
                final_url="https://auth.openai.com/add-phone",
                resume_context={"continueUrl": "https://auth.openai.com/add-phone", "token": "resume_123"},
            ),
        ), mock.patch.object(
            protocol_oauth,
            "persist_first_phone_record",
            return_value="C:/tmp/first-phone.json",
        ) as persist_first_phone_record, mock.patch.object(
            protocol_oauth,
            "persist_success_auth_json",
        ) as persist_success_auth_json, mock.patch.object(
            protocol_oauth,
            "release_mailbox_sessions_by_email",
            return_value=[],
        ):
            result = protocol_oauth.run_protocol_oauth_once(seed_payload=seed_payload, output_dir="C:/tmp/out")

        self.assertTrue(result.phone_verification_required)
        self.assertEqual("add_phone", result.page_type)
        self.assertEqual("https://auth.openai.com/add-phone", result.final_url)
        self.assertEqual("resume_123", result.resume_context["token"])
        self.assertEqual("C:/tmp/first-phone.json", result.storage_path)
        persist_first_phone_record.assert_called_once()
        persist_success_auth_json.assert_not_called()

    def test_run_protocol_oauth_once_reuses_completed_chatgpt_session_without_password_repair(self) -> None:
        authenticated_session = {
            "version": 1,
            "capturedAt": 123,
            "proxyFingerprint": "fingerprint",
            "sessionCookies": [{"name": "session", "value": "value", "domain": ".chatgpt.com"}],
            "browser": {"userAgent": "ua", "deviceId": "did"},
        }
        seed_payload = {
            "email": "user@example.com",
            "password": "pw",
            "mailboxRef": "mailtm:test",
            "mailboxSessionId": "mailbox_123",
            "firstName": "User",
            "lastName": "Example",
            "birthdate": "2000-01-01",
            "chatgptLogin": {
                "ok": True,
                "status": "completed",
                "personalWorkspaceId": "workspace_123",
            },
            "chatgptLoginDetails": {"authenticatedSession": authenticated_session},
        }
        normalized_auth = protocol_oauth._normalize_seed_payload(seed_payload)
        restored_session = mock.Mock()

        with mock.patch.object(
            protocol_oauth,
            "_refresh_seed_mailbox_binding",
            return_value=(normalized_auth, {}),
        ), mock.patch.object(
            protocol_oauth,
            "_ensure_protocol_oauth_easy_runtime_defaults",
        ), mock.patch.object(
            protocol_oauth,
            "flow_network_env",
            return_value=contextlib.nullcontext(),
        ), mock.patch.object(
            protocol_oauth,
            "restore_authenticated_session_from_context",
            return_value=restored_session,
        ) as restore_session, mock.patch.object(
            protocol_oauth,
            "handoff_authenticated_chatgpt_session_to_codex",
            return_value=protocol_register.ProtocolRegistrationResult(
                email="user@example.com",
                auth={"mailboxRef": "mailtm:test"},
                phone_verification_required=True,
                page_type="add_phone",
                final_url="https://auth.openai.com/add-phone",
                resume_context={"continueUrl": "https://auth.openai.com/add-phone"},
            ),
        ) as session_handoff, mock.patch.object(
            protocol_oauth,
            "run_protocol_repair_once",
        ) as password_repair, mock.patch.object(
            protocol_oauth,
            "persist_first_phone_record",
            return_value="C:/tmp/first-phone.json",
        ), mock.patch.object(
            protocol_oauth,
            "release_mailbox_sessions_by_email",
            return_value=[],
        ):
            result = protocol_oauth.run_protocol_oauth_once(
                seed_payload=seed_payload,
                output_dir="C:/tmp/out",
                explicit_proxy="http://proxy.local:8080",
            )

        self.assertTrue(result.phone_verification_required)
        restore_session.assert_called_once_with(
            authenticated_session,
            explicit_proxy="http://proxy.local:8080",
        )
        session_handoff.assert_called_once()
        self.assertEqual(
            "workspace_123",
            session_handoff.call_args.kwargs["preferred_workspace_id"],
        )
        password_repair.assert_not_called()
        restored_session.close.assert_called_once()

    def test_authenticated_session_exchange_prefers_workspace_from_completed_chatgpt_login(self) -> None:
        session = mock.Mock()
        oauth = SimpleNamespace()
        initial_result = protocol_register.ProtocolRegistrationResult(
            email="user@example.com",
            auth={"account_id": "account_123"},
        )
        completed_result = protocol_register.ProtocolRegistrationResult(
            email="user@example.com",
            auth={"account_id": "account_123"},
        )

        with mock.patch.object(
            protocol_register,
            "_extract_workspace_id_from_session",
        ) as extract_workspace, mock.patch.object(
            protocol_register,
            "_submit_workspace_selection_for_callback",
            return_value="http://localhost:1455/auth/callback?code=abc&state=state_123",
        ) as submit_workspace, mock.patch.object(
            protocol_register,
            "_callback_result_from_url",
            return_value=initial_result,
        ), mock.patch.object(
            protocol_register,
            "_maybe_recover_personal_protocol_result",
            return_value=completed_result,
        ):
            result = protocol_register._exchange_authenticated_session_for_codex_result(
                session=session,
                oauth=oauth,
                explicit_proxy="http://proxy.local:8080",
                default_email="user@example.com",
                mailbox_ref="mailtm:test",
                password="",
                first_name="User",
                last_name="Example",
                birthdate="2000-01-01",
                workspace_request_label="workspace-select-test",
                preferred_workspace_id="workspace_123",
            )

        self.assertIs(completed_result, result)
        extract_workspace.assert_not_called()
        self.assertEqual("workspace_123", submit_workspace.call_args.kwargs["workspace_id"])

    def test_run_protocol_oauth_from_path_clears_consumed_authenticated_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            seed_path = Path(tmpdir) / "seed.json"
            seed_path.write_text(
                json.dumps(
                    {
                        "email": "user@example.com",
                        "chatgptLoginDetails": {
                            "authenticatedSession": {"version": 1, "sessionCookies": [{"value": "secret"}]},
                            "oauthTokens": {"refresh_token": "refresh.demo"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            expected_result = SimpleNamespace(ok=True)
            with mock.patch.object(
                protocol_oauth,
                "run_protocol_oauth_once",
                return_value=expected_result,
            ):
                result = protocol_oauth.run_protocol_oauth_from_path(seed_path=seed_path)

            persisted = json.loads(seed_path.read_text(encoding="utf-8"))

        self.assertIs(expected_result, result)
        self.assertNotIn("authenticatedSession", persisted["chatgptLoginDetails"])
        self.assertEqual("refresh.demo", persisted["chatgptLoginDetails"]["oauthTokens"]["refresh_token"])

    def test_refresh_seed_mailbox_binding_reuses_recent_existing_binding(self) -> None:
        created_at = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=2)
        ).isoformat().replace("+00:00", "Z")
        auth_obj = {
            "email": "prudence96e088@pek.blaizesmp.net",
            "mailbox_ref": "tempmail-lol:tempmail_lol_shared_default:%7Bdemo%7D",
            "session_id": "mailbox_20260527131801_1762",
            "created_at": created_at,
        }

        with mock.patch.object(
            protocol_oauth,
            "release_mailbox_sessions_by_email",
        ) as release_mailbox_sessions_by_email, mock.patch.object(
            protocol_oauth,
            "resolve_mailbox",
        ) as resolve_mailbox:
            updated_auth, refresh = protocol_oauth._refresh_seed_mailbox_binding(auth_obj)

        release_mailbox_sessions_by_email.assert_not_called()
        resolve_mailbox.assert_not_called()
        self.assertEqual(auth_obj["mailbox_ref"], updated_auth["mailbox_ref"])
        self.assertEqual(auth_obj["session_id"], updated_auth["session_id"])
        self.assertEqual("reuse_existing", refresh["strategy"])

    def test_refresh_seed_mailbox_binding_reuses_stale_existing_binding(self) -> None:
        created_at = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
        ).isoformat().replace("+00:00", "Z")
        auth_obj = {
            "email": "user@example.com",
            "mailbox_ref": "moemail:old-ref",
            "session_id": "mailbox_old",
            "created_at": created_at,
        }
        with mock.patch.object(
            protocol_oauth,
            "release_mailbox_sessions_by_email",
        ) as release_mailbox_sessions_by_email, mock.patch.object(
            protocol_oauth,
            "resolve_mailbox",
        ) as resolve_mailbox:
            updated_auth, refresh = protocol_oauth._refresh_seed_mailbox_binding(auth_obj)

        release_mailbox_sessions_by_email.assert_not_called()
        resolve_mailbox.assert_not_called()
        self.assertEqual("mailbox_old", updated_auth["session_id"])
        self.assertEqual("moemail:old-ref", updated_auth["mailbox_ref"])
        self.assertEqual("reuse_existing", refresh["strategy"])

    def test_refresh_seed_mailbox_binding_recreates_when_session_binding_missing(self) -> None:
        auth_obj = {
            "email": "user@example.com",
            "mailbox_ref": "",
            "session_id": "",
            "created_at": "",
        }
        resolved_mailbox = protocol_runtime.Mailbox(
            provider="moemail",
            email="user@example.com",
            ref="moemail:new-ref",
            session_id="mailbox_new",
        )

        with mock.patch.object(
            protocol_oauth,
            "release_mailbox_sessions_by_email",
            return_value=[],
        ) as release_mailbox_sessions_by_email, mock.patch.object(
            protocol_oauth,
            "resolve_mailbox",
            return_value=resolved_mailbox,
        ) as resolve_mailbox:
            updated_auth, refresh = protocol_oauth._refresh_seed_mailbox_binding(auth_obj)

        release_mailbox_sessions_by_email.assert_called_once()
        resolve_mailbox.assert_called_once()
        self.assertEqual("mailbox_new", updated_auth["session_id"])
        self.assertEqual("moemail:new-ref", updated_auth["mailbox_ref"])
        self.assertEqual("recreate_existing", refresh["strategy"])

    def test_obtain_codex_oauth_phone_wall_result_contains_resume_context(self) -> None:
        with mock.patch.object(
            easyprotocol_flow,
            "run_protocol_oauth_from_path",
            return_value=SimpleNamespace(
                phone_verification_required=True,
                page_type="add_phone",
                final_url="https://chatgpt.com/auth/add-phone",
                resume_context={"flow": "oauth", "token": "resume_123"},
                storage_path="C:/tmp/first-phone.json",
            ),
        ):
            result = easyprotocol_flow.dispatch_easyprotocol_step(
                step_type="obtain_codex_oauth",
                step_input={"source_path": "C:/tmp/small.json", "output_dir": "C:/tmp/out"},
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["phoneVerificationRequired"])
        self.assertEqual("add_phone", result["pageType"])
        self.assertEqual("resume_123", result["resumeContext"]["token"])

    def test_dispatch_submit_phone_verification_code_returns_oauth_payload(self) -> None:
        with mock.patch.object(
            easyprotocol_flow,
            "submit_phone_verification_code_from_path",
            return_value={
                "ok": True,
                "status": "completed",
                "successPath": "C:/tmp/codex-free.json",
                "userId": "user_123",
            },
        ):
            result = easyprotocol_flow.dispatch_easyprotocol_step(
                step_type="submit_phone_verification_code",
                step_input={
                    "source_path": "C:/tmp/small.json",
                    "resume_context": {"token": "resume_123"},
                    "sms_code": "123456",
                },
            )

        self.assertEqual("completed", result["status"])
        self.assertEqual("user_123", result["userId"])

    def test_submit_phone_verification_code_persists_success_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            source_dir = output_dir / "small_success"
            source_dir.mkdir(parents=True)
            source_path = source_dir / "small.json"
            source_path.write_text(
                json.dumps({"email": "user@example.com"}),
                encoding="utf-8",
            )
            auth_payload = {
                "email": "user@example.com",
                "account_id": "account_123",
                "https://api.openai.com/auth": {"chatgpt_user_id": "user_123"},
            }

            with mock.patch.object(
                protocol_register,
                "submit_phone_verification_code_for_resume",
                return_value={
                    "auth": auth_payload,
                    "email": "user@example.com",
                    "accountId": "account_123",
                },
            ):
                result = protocol_phone_verification.submit_phone_verification_code_from_path(
                    source_path=str(source_path),
                    resume_context={"continueUrl": "https://auth.openai.com/sms-verification"},
                    sms_code="123456",
                    explicit_proxy=None,
                )

            success_path = Path(result["successPath"])
            self.assertEqual("completed", result["status"])
            self.assertEqual(output_dir / "success", success_path.parent)
            self.assertTrue(success_path.is_file())
            self.assertEqual(auth_payload, json.loads(success_path.read_text(encoding="utf-8")))

    def test_submit_phone_number_for_resume_updates_resume_context_after_browser_step(self) -> None:
        class _FakeDriver:
            def __init__(self) -> None:
                self.current_url = "https://auth.openai.com/add-phone"

            def get(self, url: str) -> None:
                self.current_url = "https://auth.openai.com/sms-verification"

            def quit(self) -> None:
                return None

        with mock.patch.object(
            protocol_register,
            "_load_protocol_browser_new_driver",
            return_value=lambda explicit_proxy, browser_backend=None: (_FakeDriver(), None),
        ), mock.patch.object(
            protocol_register,
            "_hydrate_browser_driver_with_protocol_session_cookies",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_import_browser_driver_cookies_into_session",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_browser_try_submit_phone_number",
            return_value=True,
        ), mock.patch.object(
            protocol_register,
            "_export_protocol_session_cookies",
            return_value=[{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
        ), mock.patch.object(
            protocol_register,
            "_extract_page_type",
            return_value="sms_verification",
        ):
            result = protocol_register.submit_phone_number_for_resume(
                source_payload={"email": "user@example.com"},
                resume_context={
                    "continueUrl": "https://auth.openai.com/add-phone",
                    "sessionCookies": [{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
                    "oauth": {
                        "authUrl": "https://auth.openai.com/api/accounts/authorize?x=1",
                        "state": "state_123",
                        "codeVerifier": "verifier_123",
                        "redirectUri": "https://chatgpt.com/api/auth/callback/openai",
                    },
                },
                phone_number="+15551234567",
                explicit_proxy=None,
            )

        self.assertEqual("sms_verification", result["pageType"])
        self.assertEqual("https://auth.openai.com/sms-verification", result["resumeContext"]["continueUrl"])
        self.assertEqual(1, len(result["resumeContext"]["sessionCookies"]))

    def test_submit_phone_verification_code_for_resume_can_finish_matching_callback_url(self) -> None:
        class _FakeDriver:
            def __init__(self) -> None:
                self.current_url = "http://localhost:1455/auth/callback?code=abc&state=state_123"

            def get(self, url: str) -> None:
                self.current_url = "http://localhost:1455/auth/callback?code=abc&state=state_123"

            def quit(self) -> None:
                return None

        with mock.patch.object(
            protocol_register,
            "_load_protocol_browser_new_driver",
            return_value=lambda explicit_proxy, browser_backend=None: (_FakeDriver(), None),
        ), mock.patch.object(
            protocol_register,
            "_hydrate_browser_driver_with_protocol_session_cookies",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_import_browser_driver_cookies_into_session",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_browser_try_submit_phone_code",
            return_value=True,
        ), mock.patch.object(
            protocol_register,
            "_callback_result_from_url",
            return_value=protocol_register.ProtocolRegistrationResult(
                email="user@example.com",
                auth={"user_id": "user_123"},
            ),
        ) as callback_result_from_url, mock.patch.object(
            protocol_register,
            "_continue_authenticated_codex_oauth",
        ) as continue_authenticated_codex_oauth:
            result = protocol_register.submit_phone_verification_code_for_resume(
                source_payload={
                    "email": "user@example.com",
                    "password": "pw",
                    "mailboxRef": "mailtm:test",
                    "firstName": "User",
                    "lastName": "Example",
                    "birthdate": "2000-01-01",
                },
                resume_context={
                    "continueUrl": "https://auth.openai.com/sms-verification",
                    "sessionCookies": [{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
                    "oauth": {
                        "authUrl": "https://auth.openai.com/oauth/authorize?x=1",
                        "state": "state_123",
                        "codeVerifier": "verifier_123",
                        "redirectUri": "http://localhost:1455/auth/callback",
                    },
                },
                sms_code="123456",
                explicit_proxy=None,
            )

        self.assertEqual("user@example.com", result["email"])
        self.assertEqual("user_123", result["auth"]["user_id"])
        callback_result_from_url.assert_called_once()
        continue_authenticated_codex_oauth.assert_not_called()

    def test_submit_phone_verification_code_replays_codex_oauth_after_chatgpt_web_callback(self) -> None:
        class _FakeDriver:
            def __init__(self) -> None:
                self.current_url = "https://chatgpt.com/api/auth/callback/openai?code=abc&state=state_123"

            def get(self, url: str) -> None:
                self.current_url = "https://chatgpt.com/api/auth/callback/openai?code=abc&state=state_123"

            def quit(self) -> None:
                return None

        completed = protocol_register.ProtocolRegistrationResult(
            email="user@example.com",
            auth={
                "account_id": "acct_codex",
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "acct_codex",
                    "chatgpt_plan_type": "free",
                    "organizations": [{"title": "personal", "role": "owner"}],
                },
            },
        )
        chatgpt_web_result = protocol_register.ProtocolRegistrationResult(
            email="user@example.com",
            auth={
                "account_id": "acct_web",
                "https://api.openai.com/auth": {"user_id": "user_web"},
            },
        )

        with mock.patch.object(
            protocol_register,
            "_load_protocol_browser_new_driver",
            return_value=lambda explicit_proxy, browser_backend=None: (_FakeDriver(), None),
        ), mock.patch.object(
            protocol_register,
            "_hydrate_browser_driver_with_protocol_session_cookies",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_import_browser_driver_cookies_into_session",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_browser_try_submit_phone_code",
            return_value=True,
        ), mock.patch.object(
            protocol_register,
            "_callback_result_from_url",
            return_value=chatgpt_web_result,
        ) as callback_result_from_url, mock.patch.object(
            protocol_register,
            "_continue_authenticated_codex_oauth",
            return_value=completed,
        ) as continue_authenticated_codex_oauth:
            result = protocol_register.submit_phone_verification_code_for_resume(
                source_payload={
                    "email": "user@example.com",
                    "password": "pw",
                    "mailboxRef": "mailtm:test",
                    "firstName": "User",
                    "lastName": "Example",
                    "birthdate": "2000-01-01",
                },
                resume_context={
                    "continueUrl": "https://auth.openai.com/sms-verification",
                    "sessionCookies": [{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
                    "oauth": {
                        "authUrl": "https://auth.openai.com/oauth/authorize?x=1",
                        "state": "state_123",
                        "codeVerifier": "verifier_123",
                        "redirectUri": "http://localhost:1455/auth/callback",
                    },
                },
                sms_code="123456",
                explicit_proxy=None,
            )

        self.assertEqual("acct_codex", result["accountId"])
        self.assertEqual("free", result["auth"]["https://api.openai.com/auth"]["chatgpt_plan_type"])
        callback_result_from_url.assert_not_called()
        continue_authenticated_codex_oauth.assert_called_once()

    def test_submit_phone_verification_code_replays_oauth_in_browser_before_http_fallback(self) -> None:
        class _FakeDriver:
            def __init__(self) -> None:
                self.current_url = "https://auth.openai.com/sms-verification"
                self.visited: list[str] = []

            def get(self, url: str) -> None:
                self.visited.append(url)
                if "/oauth/authorize" in url:
                    self.current_url = "http://localhost:1455/auth/callback?code=abc&state=state_123"
                else:
                    self.current_url = "https://auth.openai.com/sms-verification"

            def quit(self) -> None:
                return None

        driver = _FakeDriver()

        def _submit_code(browser_driver: _FakeDriver, *, sms_code: str) -> bool:
            self.assertEqual("123456", sms_code)
            browser_driver.current_url = "https://chatgpt.com/api/auth/callback/openai?code=web&state=web_state"
            return True

        with mock.patch.object(
            protocol_register,
            "_load_protocol_browser_new_driver",
            return_value=lambda explicit_proxy, browser_backend=None: (driver, None),
        ), mock.patch.object(
            protocol_register,
            "_hydrate_browser_driver_with_protocol_session_cookies",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_import_browser_driver_cookies_into_session",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_browser_try_submit_phone_code",
            side_effect=_submit_code,
        ), mock.patch.object(
            protocol_register,
            "_callback_result_from_url",
            return_value=protocol_register.ProtocolRegistrationResult(
                email="user@example.com",
                auth={"account_id": "acct_codex"},
            ),
        ) as callback_result_from_url, mock.patch.object(
            protocol_register,
            "_continue_authenticated_codex_oauth",
        ) as continue_authenticated_codex_oauth:
            result = protocol_register.submit_phone_verification_code_for_resume(
                source_payload={
                    "email": "user@example.com",
                    "password": "pw",
                    "mailboxRef": "mailtm:test",
                    "firstName": "User",
                    "lastName": "Example",
                    "birthdate": "2000-01-01",
                },
                resume_context={
                    "continueUrl": "https://auth.openai.com/sms-verification",
                    "sessionCookies": [{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
                    "oauth": {
                        "authUrl": "https://auth.openai.com/oauth/authorize?x=1",
                        "state": "state_123",
                        "codeVerifier": "verifier_123",
                        "redirectUri": "http://localhost:1455/auth/callback",
                    },
                },
                sms_code="123456",
                explicit_proxy=None,
            )

        self.assertEqual("acct_codex", result["accountId"])
        self.assertEqual(
            [
                "https://auth.openai.com/sms-verification",
                "https://auth.openai.com/oauth/authorize?x=1",
            ],
            driver.visited,
        )
        callback_result_from_url.assert_called_once()
        continue_authenticated_codex_oauth.assert_not_called()

    def test_submit_phone_verification_code_for_resume_accepts_preexisting_callback_before_manual_input(self) -> None:
        class _FakeDriver:
            def __init__(self) -> None:
                self.current_url = "http://localhost:1455/auth/callback?code=abc&state=state_browser"

            def get(self, url: str) -> None:
                self.current_url = "http://localhost:1455/auth/callback?code=abc&state=state_browser"

            def quit(self) -> None:
                return None

        with mock.patch.object(
            protocol_register,
            "_load_protocol_browser_new_driver",
            return_value=lambda explicit_proxy, browser_backend=None: (_FakeDriver(), None),
        ), mock.patch.object(
            protocol_register,
            "_hydrate_browser_driver_with_protocol_session_cookies",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_import_browser_driver_cookies_into_session",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_browser_try_submit_phone_code",
            return_value=False,
        ) as browser_try_submit_phone_code, mock.patch.object(
            protocol_register,
            "_callback_result_from_url",
            return_value=protocol_register.ProtocolRegistrationResult(
                email="user@example.com",
                auth={"user_id": "user_123"},
            ),
        ) as callback_result_from_url, mock.patch.object(
            protocol_register,
            "_continue_authenticated_codex_oauth",
        ) as continue_authenticated_codex_oauth:
            result = protocol_register.submit_phone_verification_code_for_resume(
                source_payload={
                    "email": "user@example.com",
                    "password": "pw",
                    "mailboxRef": "mailtm:test",
                    "firstName": "User",
                    "lastName": "Example",
                    "birthdate": "2000-01-01",
                },
                resume_context={
                    "continueUrl": "https://auth.openai.com/sms-verification",
                    "sessionCookies": [{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
                    "oauth": {
                        "authUrl": "https://auth.openai.com/oauth/authorize?x=1",
                        "state": "state_browser",
                        "codeVerifier": "verifier_123",
                        "redirectUri": "http://localhost:1455/auth/callback",
                    },
                },
                sms_code="123456",
                explicit_proxy=None,
            )

        self.assertEqual("user@example.com", result["email"])
        self.assertEqual("user_123", result["auth"]["user_id"])
        browser_try_submit_phone_code.assert_not_called()
        callback_result_from_url.assert_called_once()
        continue_authenticated_codex_oauth.assert_not_called()

    def test_browser_submit_phone_code_skips_hidden_otp_inputs(self) -> None:
        class _FakeElement:
            def __init__(self, *, displayed: bool, label: str) -> None:
                self.displayed = displayed
                self.label = label
                self.sent: list[str] = []

            def is_displayed(self) -> bool:
                return self.displayed

            def is_enabled(self) -> bool:
                return True

            @property
            def rect(self) -> dict[str, int]:
                return {"width": 24 if self.displayed else 0, "height": 24 if self.displayed else 0}

            def click(self) -> None:
                if not self.displayed:
                    raise RuntimeError(f"{self.label} hidden")

            def send_keys(self, *values: object) -> None:
                if not self.displayed:
                    raise RuntimeError(f"{self.label} hidden")
                self.sent.extend(str(value) for value in values)

        class _FakeSwitch:
            def default_content(self) -> None:
                return None

        class _FakeDriver:
            def __init__(self) -> None:
                self.switch_to = _FakeSwitch()
                self.hidden = _FakeElement(displayed=False, label="hidden")
                self.visible = [_FakeElement(displayed=True, label=f"digit-{index}") for index in range(4)]

            def find_elements(self, by: object, selector: str) -> list[object]:
                if selector == 'input[maxlength="1"], input[inputmode="numeric"], input[autocomplete="one-time-code"]':
                    return [self.hidden, *self.visible]
                if selector == "button":
                    return []
                return []

        driver = _FakeDriver()

        self.assertTrue(protocol_register._browser_try_submit_phone_code(driver, sms_code="1234"))
        self.assertEqual(["1"], driver.visible[0].sent[-1:])
        self.assertEqual(["4"], driver.visible[3].sent[-1:])

    def test_browser_submit_phone_code_skips_non_interactable_visible_otp_inputs(self) -> None:
        class _FakeElement:
            def __init__(self, *, label: str, interactable: bool = True) -> None:
                self.label = label
                self.interactable = interactable
                self.sent: list[str] = []

            def is_displayed(self) -> bool:
                return True

            def is_enabled(self) -> bool:
                return True

            @property
            def rect(self) -> dict[str, int]:
                return {"width": 24, "height": 24}

            def click(self) -> None:
                if not self.interactable:
                    raise RuntimeError(f"{self.label} not interactable")

            def send_keys(self, *values: object) -> None:
                if not self.interactable:
                    raise RuntimeError(f"{self.label} not interactable")
                self.sent.extend(str(value) for value in values)

        class _FakeSwitch:
            def default_content(self) -> None:
                return None

        class _FakeDriver:
            def __init__(self) -> None:
                self.switch_to = _FakeSwitch()
                self.bad = _FakeElement(label="bad", interactable=False)
                self.visible = [_FakeElement(label=f"digit-{index}") for index in range(4)]

            def find_elements(self, by: object, selector: str) -> list[object]:
                if selector == 'input[maxlength="1"], input[inputmode="numeric"], input[autocomplete="one-time-code"]':
                    return [self.bad, *self.visible]
                if selector == "button":
                    return []
                return []

        driver = _FakeDriver()

        self.assertTrue(protocol_register._browser_try_submit_phone_code(driver, sms_code="1234"))
        self.assertEqual(["1"], driver.visible[0].sent[-1:])
        self.assertEqual(["4"], driver.visible[3].sent[-1:])

    def test_browser_submit_phone_code_skips_non_interactable_text_code_input(self) -> None:
        class _FakeElement:
            def __init__(self, *, label: str, interactable: bool = True) -> None:
                self.label = label
                self.interactable = interactable
                self.sent: list[str] = []

            def is_displayed(self) -> bool:
                return True

            def is_enabled(self) -> bool:
                return True

            @property
            def rect(self) -> dict[str, int]:
                return {"width": 120, "height": 24}

            def click(self) -> None:
                if not self.interactable:
                    raise RuntimeError(f"{self.label} not interactable")

            def send_keys(self, *values: object) -> None:
                if not self.interactable:
                    raise RuntimeError(f"{self.label} not interactable")
                self.sent.extend(str(value) for value in values)

        class _FakeSwitch:
            def default_content(self) -> None:
                return None

        class _FakeDriver:
            def __init__(self) -> None:
                self.switch_to = _FakeSwitch()
                self.bad = _FakeElement(label="bad", interactable=False)
                self.good = _FakeElement(label="good")

            def find_elements(self, by: object, selector: str) -> list[object]:
                if selector == 'input[maxlength="1"], input[inputmode="numeric"], input[autocomplete="one-time-code"]':
                    return []
                if selector == 'input[name*="code"]':
                    return [self.bad, self.good]
                if selector == "button":
                    return []
                return []

            def find_element(self, by: object, selector: str) -> object:
                elements = self.find_elements(by, selector)
                if not elements:
                    raise RuntimeError(f"not found: {selector}")
                return elements[0]

        driver = _FakeDriver()

        self.assertTrue(protocol_register._browser_try_submit_phone_code(driver, sms_code="1234"))
        self.assertEqual(["1234"], driver.good.sent[-1:])

    def test_browser_submit_phone_code_returns_false_when_submit_button_is_not_clickable(self) -> None:
        class _FakeInput:
            def is_displayed(self) -> bool:
                return True

            def is_enabled(self) -> bool:
                return True

            @property
            def rect(self) -> dict[str, int]:
                return {"width": 120, "height": 24}

            def click(self) -> None:
                return None

            def send_keys(self, *values: object) -> None:
                return None

        class _FakeButton:
            text = "Continue"

            def is_displayed(self) -> bool:
                return True

            def is_enabled(self) -> bool:
                return True

            @property
            def rect(self) -> dict[str, int]:
                return {"width": 80, "height": 24}

            def click(self) -> None:
                raise RuntimeError("button not interactable")

        class _FakeSwitch:
            def default_content(self) -> None:
                return None

        class _FakeDriver:
            def __init__(self) -> None:
                self.switch_to = _FakeSwitch()
                self.text_input = _FakeInput()
                self.button = _FakeButton()

            def find_elements(self, by: object, selector: str) -> list[object]:
                if selector == 'input[maxlength="1"], input[inputmode="numeric"], input[autocomplete="one-time-code"]':
                    return []
                if selector == 'input[name*="code"]':
                    return [self.text_input]
                if selector == "button":
                    return [self.button]
                return []

            def find_element(self, by: object, selector: str) -> object:
                elements = self.find_elements(by, selector)
                if not elements:
                    raise RuntimeError(f"not found: {selector}")
                return elements[0]

        self.assertFalse(protocol_register._browser_try_submit_phone_code(_FakeDriver(), sms_code="1234"))

    def test_submit_phone_number_for_resume_waits_for_phone_surface_before_failing(self) -> None:
        class _FakeDriver:
            def __init__(self) -> None:
                self.current_url = "https://auth.openai.com/sms-verification"
                self.title = "Phone number required"

            def get(self, url: str) -> None:
                self.current_url = "https://auth.openai.com/sms-verification"

            def quit(self) -> None:
                return None

        with mock.patch.object(
            protocol_register,
            "_load_protocol_browser_new_driver",
            return_value=lambda explicit_proxy, browser_backend=None: (_FakeDriver(), None),
        ), mock.patch.object(
            protocol_register,
            "_submit_phone_number_via_protocol_session",
            side_effect=RuntimeError("phone_number_send_unavailable"),
        ), mock.patch.object(
            protocol_register,
            "_hydrate_browser_driver_with_protocol_session_cookies",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_import_browser_driver_cookies_into_session",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_browser_try_submit_phone_number",
            side_effect=[False, False, True],
        ) as submit_phone_number, mock.patch.object(
            protocol_register,
            "_export_protocol_session_cookies",
            return_value=[{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
        ), mock.patch.object(
            protocol_register.time,
            "sleep",
            return_value=None,
        ), mock.patch.object(
            protocol_register.time,
            "monotonic",
            side_effect=[0.0, 0.1, 0.2, 0.3],
        ):
            result = protocol_register.submit_phone_number_for_resume(
                source_payload={"email": "user@example.com"},
                resume_context={
                    "continueUrl": "https://auth.openai.com/add-phone",
                    "sessionCookies": [{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
                    "oauth": {
                        "authUrl": "https://auth.openai.com/api/accounts/authorize?x=1",
                        "state": "state_123",
                        "codeVerifier": "verifier_123",
                        "redirectUri": "https://chatgpt.com/api/auth/callback/openai",
                    },
                },
                phone_number="+15551234567",
                explicit_proxy=None,
            )

        self.assertEqual(3, submit_phone_number.call_count)
        self.assertEqual("sms_verification", result["pageType"])

    def test_submit_phone_number_for_resume_returns_terminal_phone_result_without_browser(self) -> None:
        with mock.patch.object(
            protocol_register,
            "_submit_phone_number_via_protocol_session",
            return_value={
                "status": "phone_verification_terminal",
                "pageType": "add_phone",
                "resumeContext": {"continueUrl": "https://auth.openai.com/add-phone"},
                "phoneVerificationAttempted": True,
                "phoneVerificationTerminal": True,
                "phoneVerificationTerminalCode": "phone_number_in_use",
                "phoneVerificationTerminalMessage": "Phone number already in use.",
                "phoneVerificationTerminalStatusCode": 403,
            },
        ), mock.patch.object(
            protocol_register,
            "_load_protocol_browser_new_driver",
        ) as new_driver:
            result = protocol_register.submit_phone_number_for_resume(
                source_payload={"email": "user@example.com"},
                resume_context={
                    "continueUrl": "https://auth.openai.com/add-phone",
                    "sessionCookies": [{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
                    "oauth": {
                        "authUrl": "https://auth.openai.com/api/accounts/authorize?x=1",
                        "state": "state_123",
                        "codeVerifier": "verifier_123",
                        "redirectUri": "https://chatgpt.com/api/auth/callback/openai",
                    },
                },
                phone_number="+15551234567",
                explicit_proxy=None,
            )

        self.assertTrue(result["phoneVerificationAttempted"])
        self.assertTrue(result["phoneVerificationTerminal"])
        self.assertEqual("phone_number_in_use", result["phoneVerificationTerminalCode"])
        new_driver.assert_not_called()

    def test_submit_phone_verification_number_from_path_passes_through_terminal_phone_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = Path(tmp_dir) / "small-success.json"
            source_path.write_text('{"email":"user@example.com"}', encoding="utf-8")
            with mock.patch.object(
                protocol_phone_verification.protocol_register,
                "submit_phone_number_for_resume",
                return_value={
                    "status": "phone_verification_terminal",
                    "pageType": "add_phone",
                    "resumeContext": {"continueUrl": "https://auth.openai.com/add-phone"},
                    "phoneVerificationAttempted": True,
                    "phoneVerificationTerminal": True,
                    "phoneVerificationTerminalCode": "rate_limit_exceeded",
                    "phoneVerificationTerminalMessage": "Too many requests.",
                    "phoneVerificationTerminalStatusCode": 403,
                },
            ):
                result = protocol_phone_verification.submit_phone_verification_number_from_path(
                    source_path=str(source_path),
                    resume_context={"continueUrl": "https://auth.openai.com/add-phone"},
                    phone_number="+15551234567",
                )

        self.assertEqual("phone_verification_terminal", result["status"])
        self.assertTrue(result["phoneVerificationTerminal"])
        self.assertEqual("rate_limit_exceeded", result["phoneVerificationTerminalCode"])

    def test_requested_email_candidates_prefer_cloudflare_for_mail_aiaimimi(self) -> None:
        with mock.patch.object(
            protocol_runtime,
            "resolve_mailbox_provider_order",
            return_value=("moemail", "m2u"),
        ):
            candidates = protocol_runtime._requested_email_provider_candidates(
                "",
                "ambervoyage217803@mail.aiaimimi.com",
            )
        self.assertEqual(("cloudflare_temp_email", "moemail", "m2u"), candidates)

    def test_resolve_mailbox_recreates_same_cloudflare_address_when_recovery_not_supported(self) -> None:
        expected_mailbox = protocol_runtime.Mailbox(
            provider="cloudflare_temp_email",
            email="ambervoyage217803@mail.aiaimimi.com",
            ref="cloudflare_temp_email:cloudflare_temp_email_shared_default:demo",
            session_id="mailbox_123",
        )
        with mock.patch.object(protocol_runtime, "ensure_easy_email_env_defaults"), mock.patch.object(
            protocol_runtime,
            "_resolve_mailbox_ttl_seconds",
            return_value=90,
        ), mock.patch.object(
            protocol_runtime,
            "_requested_email_provider_candidates",
            return_value=("cloudflare_temp_email", "moemail"),
        ) as provider_candidates, mock.patch.object(
            protocol_runtime,
            "recover_mailbox_by_email",
            return_value={
                "recovered": False,
                "strategy": "not_supported",
                "detail": "provider_recovery_not_supported",
            },
        ) as recover_mailbox_by_email, mock.patch.object(
            protocol_runtime,
            "create_mailbox",
            return_value=expected_mailbox,
        ) as create_mailbox:
            mailbox = protocol_runtime.resolve_mailbox(
                preallocated_email="ambervoyage217803@mail.aiaimimi.com",
                preallocated_session_id=None,
                preallocated_mailbox_ref=None,
                recreate_preallocated_email=True,
            )
        provider_candidates.assert_called_once()
        recover_mailbox_by_email.assert_called_once()
        create_mailbox.assert_called_once()
        self.assertEqual("cloudflare_temp_email", create_mailbox.call_args.kwargs["provider"])
        self.assertEqual(expected_mailbox, mailbox)

    def test_ensure_easy_email_env_defaults_uses_docker_alias_inside_docker(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            protocol_runtime, "_running_in_docker", return_value=True
        ):
            protocol_runtime.ensure_easy_email_env_defaults()
            self.assertEqual("http://easy-email:8080", os.environ.get("MAILBOX_SERVICE_BASE_URL"))

    def test_protocol_oauth_defaults_mailbox_base_url_to_easy_email_inside_docker(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            protocol_oauth, "_default_easyemail_base_url", return_value="http://easy-email:8080"
        ), mock.patch.object(
            protocol_oauth, "_read_easyemail_server_api_key", return_value=""
        ):
            protocol_oauth._ensure_protocol_oauth_easy_runtime_defaults()
            self.assertEqual("http://easy-email:8080", os.environ.get("MAILBOX_SERVICE_BASE_URL"))

    def test_send_passwordless_login_otp_posts_authapi_login_endpoint(self) -> None:
        response = SimpleNamespace(status_code=200)
        with mock.patch.object(
            protocol_register,
            "_build_protocol_headers",
            return_value={"referer": protocol_register.LOGIN_PASSWORD_REFERER},
        ) as build_headers, mock.patch.object(
            protocol_register,
            "_session_request",
            return_value=response,
        ) as session_request, mock.patch.object(
            protocol_register,
            "_extract_page_type",
            return_value="email_otp_verification",
        ):
            result = protocol_register._send_passwordless_login_otp(
                mock.Mock(),
                explicit_proxy="http://proxy:8080",
                header_builder=SimpleNamespace(),
            )
        build_headers.assert_called_once_with(
            request_kind="",
            referer=protocol_register.LOGIN_PASSWORD_REFERER,
            sentinel_context=mock.ANY,
        )
        session_request.assert_called_once_with(
            mock.ANY,
            "POST",
            protocol_register.PASSWORDLESS_SEND_OTP_URL,
            explicit_proxy="http://proxy:8080",
            request_label="passwordless-login-send-otp",
            headers={"referer": protocol_register.LOGIN_PASSWORD_REFERER},
            timeout=45,
        )
        self.assertIs(result, response)

    def test_resolve_repair_oauth_entry_uses_passwordless_send_otp_fallback_when_password_missing(self) -> None:
        signup_response = SimpleNamespace()
        otp_response = SimpleNamespace()
        with mock.patch.object(
            protocol_register,
            "_extract_page_type",
            side_effect=["login_password", "email_otp_verification"],
        ), mock.patch.object(
            protocol_register,
            "_send_passwordless_login_otp",
            return_value=otp_response,
        ) as send_passwordless_login_otp, mock.patch.object(
            protocol_register,
            "_verify_login_password",
        ) as verify_login_password:
            oauth_entry_response, page_type, oauth_entry_referer = protocol_register._resolve_repair_oauth_entry(
                mock.Mock(),
                signup_response=signup_response,
                password="",
                mailbox_ref="cloudflare_temp_email:mailbox_123",
                explicit_proxy="http://proxy:8080",
                header_builder=SimpleNamespace(),
            )
        verify_login_password.assert_not_called()
        send_passwordless_login_otp.assert_called_once()
        self.assertIs(oauth_entry_response, otp_response)
        self.assertEqual("email_otp_verification", page_type)
        self.assertEqual(protocol_register.EMAIL_VERIFICATION_REFERER, oauth_entry_referer)

    def test_run_protocol_repair_once_returns_phone_verification_required_when_password_verify_hits_add_phone(self) -> None:
        session = mock.Mock()
        session.headers = {}
        oauth = SimpleNamespace(
            auth_url="https://auth.openai.com/api/accounts/authorize?x=1",
            state="state_123",
            code_verifier="verifier",
            redirect_uri="https://chatgpt.com/api/auth/callback/openai",
        )
        signup_response = SimpleNamespace(status_code=200)
        phone_response = SimpleNamespace(
            status_code=200,
            url="https://auth.openai.com/add-phone",
        )
        phone_response.json = lambda: {
            "page": {"type": "add_phone"},
            "continue_url": "https://auth.openai.com/add-phone",
        }

        with mock.patch.object(
            protocol_register,
            "generate_oauth_url",
            return_value=oauth,
        ), mock.patch.object(
            protocol_register,
            "get_mailbox_latest_message_id",
            return_value=0,
        ), mock.patch.object(
            protocol_register,
            "_session_request",
            side_effect=[SimpleNamespace(status_code=200), signup_response],
        ), mock.patch.object(
            protocol_register,
            "_get_session_cookie",
            return_value="did_123",
        ), mock.patch.object(
            protocol_register,
            "_build_protocol_headers",
            return_value={},
        ), mock.patch.object(
            protocol_register,
            "_resolve_repair_oauth_entry",
            return_value=(phone_response, "add_phone", protocol_register.LOGIN_PASSWORD_REFERER),
        ), mock.patch.object(
            protocol_register,
            "_export_protocol_session_cookies",
            return_value=[{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
        ):
            result = protocol_register.run_protocol_repair_once(
                auth_obj={
                    "email": "user@example.com",
                    "password": "pw",
                    "mailbox_ref": "mailtm:test",
                    "session_id": "mailbox_123",
                },
                existing_session=session,
                existing_sentinel_context=SimpleNamespace(user_agent="ua", device_id="device"),
            )

        self.assertTrue(result.phone_verification_required)
        self.assertEqual("add_phone", result.page_type)
        self.assertEqual("https://auth.openai.com/add-phone", result.final_url)
        self.assertEqual("repair_page_type", result.resume_context["context"])
        self.assertEqual("https://auth.openai.com/add-phone", result.resume_context["continueUrl"])
        self.assertEqual("device", result.resume_context["browser"]["deviceId"])
        self.assertEqual("state_123", result.resume_context["oauth"]["state"])

    def test_authenticated_session_handoff_preserves_workspace_phone_wall_for_sms_resume(self) -> None:
        session = mock.Mock()
        session.headers = {"user-agent": "ua"}
        oauth = SimpleNamespace(
            auth_url="https://auth.openai.com/oauth/authorize?state=state_123",
            state="state_123",
            code_verifier="verifier",
            redirect_uri="http://localhost:1455/auth/callback",
        )
        authorize_response = SimpleNamespace(
            status_code=302,
            headers={"Location": "https://auth.openai.com/oauth/resume?state=state_123"},
            url=oauth.auth_url,
            text="",
            json=lambda: {},
        )
        choose_account_response = SimpleNamespace(
            status_code=302,
            headers={"Location": "/choose-an-account"},
            url="https://auth.openai.com/oauth/resume?state=state_123",
            text="",
            json=lambda: {},
        )
        phone_response = SimpleNamespace(
            status_code=200,
            headers={},
            url="https://auth.openai.com/add-phone",
            text="",
            json=lambda: {
                "page": {"type": "add_phone"},
                "continue_url": "https://auth.openai.com/add-phone",
            },
        )

        events: list[str] = []

        def request_side_effect(*args: object, **kwargs: object) -> object:
            events.append(str(kwargs.get("request_label") or "request"))
            return authorize_response if len(events) == 1 else choose_account_response

        def exchange_side_effect(**kwargs: object) -> object:
            events.append("workspace-exchange")
            raise protocol_register._PhoneWallResponseError(
                response=phone_response,
                context="workspace_select",
            )

        with mock.patch.object(
            protocol_register,
            "generate_oauth_url",
            return_value=oauth,
        ), mock.patch.object(
            protocol_register,
            "_session_request",
            side_effect=request_side_effect,
        ) as session_request, mock.patch.object(
            protocol_register,
            "_maybe_finish_codex_oauth_from_response",
            return_value=None,
        ) as finish_oauth, mock.patch.object(
            protocol_register,
            "_complete_codex_oauth_with_browser",
            side_effect=RuntimeError("codex_browser_handoff_incomplete"),
        ) as browser_handoff, mock.patch.object(
            protocol_register,
            "_exchange_authenticated_session_for_codex_result",
            side_effect=exchange_side_effect,
        ) as exchange_session, mock.patch.object(
            protocol_register,
            "_get_session_cookie",
            return_value="did-123",
        ), mock.patch.object(
            protocol_register,
            "_export_protocol_session_cookies",
            return_value=[{"name": "session", "value": "value", "domain": ".chatgpt.com", "path": "/"}],
        ):
            result = protocol_register.handoff_authenticated_chatgpt_session_to_codex(
                session=session,
                explicit_proxy="http://proxy.local:8080",
                email="user@example.com",
                mailbox_ref="mailtm:test",
                first_name="User",
                last_name="Example",
                birthdate="2000-01-01",
            )

        self.assertTrue(result.phone_verification_required)
        self.assertEqual("add_phone", result.page_type)
        self.assertEqual("session_handoff_workspace_select", result.resume_context["context"])
        self.assertEqual("state_123", result.resume_context["oauth"]["state"])
        self.assertEqual("did-123", result.resume_context["browser"]["deviceId"])
        self.assertEqual(
            [
                "oauth-authorize-codex-handoff",
                "oauth-authorize-codex-handoff-prime",
                "workspace-exchange",
            ],
            events,
        )
        self.assertEqual(
            "https://auth.openai.com/oauth/resume?state=state_123",
            session_request.call_args_list[1].args[2],
        )
        self.assertEqual(
            oauth.auth_url,
            session_request.call_args_list[1].kwargs["headers"]["referer"],
        )
        finish_oauth.assert_called_once()
        browser_handoff.assert_called_once()
        self.assertEqual(
            "https://auth.openai.com/choose-an-account",
            exchange_session.call_args.kwargs["workspace_referer"],
        )

    def test_authenticated_session_handoff_uses_browser_for_account_picker_html(self) -> None:
        session = mock.Mock()
        session.headers = {"user-agent": "ua"}
        oauth = SimpleNamespace(
            auth_url="https://auth.openai.com/oauth/authorize?state=state_123",
            state="state_123",
            code_verifier="verifier",
            redirect_uri="http://localhost:1455/auth/callback",
        )
        authorize_response = SimpleNamespace(
            status_code=302,
            headers={"Location": "https://auth.openai.com/oauth/resume?state=state_123"},
            url=oauth.auth_url,
            text="",
            json=lambda: {},
        )
        account_picker_response = SimpleNamespace(
            status_code=200,
            headers={},
            url="https://auth.openai.com/choose-an-account",
            text="<html><body>Choose an account</body></html>",
            json=lambda: {},
        )
        callback_url = "http://localhost:1455/auth/callback?code=code_123&state=state_123"
        initial_result = object()
        expected_result = object()

        with mock.patch.object(
            protocol_register,
            "generate_oauth_url",
            return_value=oauth,
        ), mock.patch.object(
            protocol_register,
            "_session_request",
            side_effect=[authorize_response, account_picker_response],
        ), mock.patch.object(
            protocol_register,
            "_maybe_finish_codex_oauth_from_response",
            return_value=None,
        ), mock.patch.object(
            protocol_register,
            "_complete_codex_oauth_with_browser",
            return_value=(callback_url, None),
        ) as browser_handoff, mock.patch.object(
            protocol_register,
            "_callback_result_from_url",
            return_value=initial_result,
        ) as callback_result, mock.patch.object(
            protocol_register,
            "_maybe_recover_personal_protocol_result",
            return_value=expected_result,
        ) as recover_personal, mock.patch.object(
            protocol_register,
            "_exchange_authenticated_session_for_codex_result",
        ) as exchange_session:
            result = protocol_register.handoff_authenticated_chatgpt_session_to_codex(
                session=session,
                explicit_proxy="http://proxy.local:8080",
                email="user@example.com",
                mailbox_ref="mailtm:test",
                first_name="User",
                last_name="Example",
                birthdate="2000-01-01",
                preferred_workspace_id="workspace-personal",
            )

        self.assertIs(expected_result, result)
        browser_handoff.assert_called_once_with(
            session=session,
            oauth=oauth,
            explicit_proxy="http://proxy.local:8080",
            default_email="user@example.com",
            preferred_workspace_id="workspace-personal",
            entry_url="https://auth.openai.com/choose-an-account",
        )
        callback_result.assert_called_once_with(
            callback_url=callback_url,
            oauth=oauth,
            explicit_proxy="http://proxy.local:8080",
            default_email="user@example.com",
            mailbox_ref="mailtm:test",
            password="",
            first_name="User",
            last_name="Example",
            birthdate="2000-01-01",
            token_post_try_direct_first=True,
        )
        recover_personal.assert_called_once()
        self.assertIs(initial_result, recover_personal.call_args.kwargs["initial_result"])
        exchange_session.assert_not_called()

    def test_browser_codex_handoff_clicks_account_and_returns_callback(self) -> None:
        session = SimpleNamespace(headers={})
        oauth = SimpleNamespace(
            auth_url="https://auth.openai.com/oauth/authorize?state=state_123",
            state="state_123",
            redirect_uri="http://localhost:1455/auth/callback",
        )
        callback_url = "http://localhost:1455/auth/callback?code=code_123&state=state_123"
        browser_user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
        )
        driver = SimpleNamespace(
            current_url="https://auth.openai.com/choose-an-account",
            get=mock.Mock(),
            execute_script=mock.Mock(return_value=browser_user_agent),
            quit=mock.Mock(),
        )

        def click_account(*args: object, **kwargs: object) -> str:
            driver.current_url = callback_url
            return "account"

        with mock.patch.object(
            protocol_register,
            "_load_protocol_browser_new_driver",
            return_value=lambda *args, **kwargs: (driver, "proxy-dir"),
        ), mock.patch.object(
            protocol_register,
            "_hydrate_browser_driver_with_protocol_session_cookies",
            return_value=2,
        ) as hydrate_browser, mock.patch.object(
            protocol_register,
            "_browser_collect_page_state",
            return_value={
                "href": "https://auth.openai.com/choose-an-account",
                "title": "Choose an account",
                "bodyText": "Choose an account",
                "readyState": "complete",
            },
        ), mock.patch.object(
            protocol_register,
            "_browser_try_click_codex_oauth_action",
            side_effect=click_account,
        ) as click_action, mock.patch.object(
            protocol_register,
            "_import_browser_driver_cookies_into_session",
            return_value=2,
        ) as import_cookies, mock.patch.object(
            protocol_register.time,
            "sleep",
        ), mock.patch.object(
            protocol_register.shutil,
            "rmtree",
        ) as remove_tree:
            result = protocol_register._complete_codex_oauth_with_browser(
                session=session,
                oauth=oauth,
                explicit_proxy="http://proxy.local:8080",
                default_email="user@example.com",
                preferred_workspace_id="workspace-personal",
                entry_url="https://auth.openai.com/choose-an-account",
            )

        self.assertEqual((callback_url, None), result)
        driver.get.assert_called_once_with("https://auth.openai.com/choose-an-account")
        hydrate_browser.assert_called_once_with(driver, session=session)
        click_action.assert_called_once_with(
            driver,
            default_email="user@example.com",
            preferred_workspace_id="workspace-personal",
        )
        import_cookies.assert_called_once_with(session, driver=driver)
        self.assertEqual(browser_user_agent, session.headers["user-agent"])
        driver.quit.assert_called_once_with()
        remove_tree.assert_called_once_with("proxy-dir", ignore_errors=True)

    def test_browser_codex_handoff_uses_native_workspace_selection_in_same_driver(self) -> None:
        session = SimpleNamespace(headers={})
        oauth = SimpleNamespace(
            auth_url="https://auth.openai.com/oauth/authorize?state=state_123",
            state="state_123",
            redirect_uri="http://localhost:1455/auth/callback",
        )
        entry_url = "https://auth.openai.com/choose-an-account"
        continue_url = "https://auth.openai.com/oauth/resume"
        callback_url = "http://localhost:1455/auth/callback?code=code_123&state=state_123"
        browser_user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
        )
        driver = SimpleNamespace(
            current_url=entry_url,
            get=mock.Mock(),
            execute_async_script=mock.Mock(
                return_value={
                    "ok": True,
                    "status": 200,
                    "continueUrl": continue_url,
                }
            ),
            execute_script=mock.Mock(return_value=browser_user_agent),
            quit=mock.Mock(),
        )

        def navigate(url: str) -> None:
            if url == continue_url:
                driver.current_url = callback_url

        driver.get.side_effect = navigate
        with mock.patch.object(
            protocol_register,
            "_load_protocol_browser_new_driver",
            return_value=lambda *args, **kwargs: (driver, "proxy-dir"),
        ), mock.patch.object(
            protocol_register,
            "_hydrate_browser_driver_with_protocol_session_cookies",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_browser_collect_page_state",
            return_value={"readyState": "complete", "bodyText": "Choose an account"},
        ), mock.patch.object(
            protocol_register,
            "_browser_try_click_codex_oauth_action",
        ) as click_action, mock.patch.object(
            protocol_register,
            "_import_browser_driver_cookies_into_session",
            return_value=2,
        ), mock.patch.object(
            protocol_register.time,
            "sleep",
        ), mock.patch.object(
            protocol_register.shutil,
            "rmtree",
        ):
            result = protocol_register._complete_codex_oauth_with_browser(
                session=session,
                oauth=oauth,
                explicit_proxy="http://proxy.local:8080",
                default_email="user@example.com",
                preferred_workspace_id="workspace-personal",
                entry_url=entry_url,
            )

        self.assertEqual((callback_url, None), result)
        self.assertEqual([mock.call(entry_url), mock.call(continue_url)], driver.get.call_args_list)
        self.assertEqual(
            "workspace-personal",
            driver.execute_async_script.call_args.args[1],
        )
        native_script = driver.execute_async_script.call_args.args[0]
        self.assertIn("/api/accounts/workspace/select", native_script)
        self.assertIn("credentials: 'include'", native_script)
        self.assertNotIn("workspace-personal", native_script)
        click_action.assert_not_called()

    def test_browser_native_workspace_selection_4xx_is_safe_and_single_flight(self) -> None:
        driver = SimpleNamespace(
            current_url="https://auth.openai.com/choose-an-account?state=must-not-persist",
            execute_async_script=mock.Mock(
                return_value={
                    "ok": False,
                    "status": 403,
                    "continueUrl": "secret=must-not-escape",
                }
            ),
        )

        first = protocol_register._browser_try_submit_codex_workspace_selection(
            driver,
            preferred_workspace_id="workspace-personal",
        )
        second = protocol_register._browser_try_submit_codex_workspace_selection(
            driver,
            preferred_workspace_id="workspace-personal",
        )

        self.assertEqual(("", "http_4xx"), first)
        self.assertEqual(("", "already_attempted"), second)
        self.assertNotIn("secret", " ".join(first + second))
        driver.execute_async_script.assert_called_once()

    def test_workspace_selection_uses_session_browser_user_agent_and_matching_client_hints(self) -> None:
        browser_user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
        )
        session = SimpleNamespace(headers={"User-Agent": browser_user_agent})
        workspace_response = SimpleNamespace(
            status_code=200,
            json=lambda: {"continue_url": "https://auth.openai.com/oauth/resume"},
        )
        with mock.patch.object(
            protocol_register,
            "_session_request",
            return_value=workspace_response,
        ) as session_request, mock.patch.object(
            protocol_register,
            "_raise_if_phone_wall_response",
        ), mock.patch.object(
            protocol_register,
            "_follow_redirect_chain_for_callback",
            return_value="http://localhost:1455/auth/callback?code=abc&state=state_123",
        ):
            callback_url = protocol_register._submit_workspace_selection_for_callback(
                session=session,
                workspace_id="workspace_123",
                explicit_proxy="http://proxy.local:8080",
                referer=protocol_register.CONSENT_REFERER,
                workspace_request_label="workspace-select-test",
            )

        self.assertIn("code=abc", callback_url)
        request_headers = session_request.call_args.kwargs["headers"]
        self.assertEqual(browser_user_agent, request_headers["user-agent"])
        self.assertIn('"145"', request_headers["sec-ch-ua"])
        self.assertEqual('"Windows"', request_headers["sec-ch-ua-platform"])

    def test_workspace_selection_prefers_sentinel_user_agent_over_session(self) -> None:
        sentinel_user_agent = (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
        )
        headers = protocol_register._build_workspace_selection_headers(
            session=SimpleNamespace(
                headers={"user-agent": protocol_register.DEFAULT_PROTOCOL_USER_AGENT}
            ),
            referer=protocol_register.CONSENT_REFERER,
            header_builder=SimpleNamespace(user_agent=sentinel_user_agent),
        )

        self.assertEqual(sentinel_user_agent, headers["user-agent"])
        self.assertIn('"146"', headers["sec-ch-ua"])
        self.assertEqual('"Linux"', headers["sec-ch-ua-platform"])

    def test_browser_codex_handoff_recovers_auth_root_once(self) -> None:
        session = mock.Mock()
        oauth = SimpleNamespace(
            auth_url="https://auth.openai.com/oauth/authorize?state=state_123",
            state="state_123",
            redirect_uri="http://localhost:1455/auth/callback",
        )
        entry_url = "https://auth.openai.com/choose-an-account"
        callback_url = "http://localhost:1455/auth/callback?code=code_123&state=state_123"
        driver = SimpleNamespace(
            current_url=entry_url,
            window_handles=["main"],
            get=mock.Mock(),
            quit=mock.Mock(),
        )

        def navigate(url: str) -> None:
            if url == oauth.auth_url:
                driver.current_url = callback_url

        driver.get.side_effect = navigate

        def click_account(*args: object, **kwargs: object) -> str:
            driver.current_url = "https://auth.openai.com/?state=state_123"
            return "account"

        with mock.patch.object(
            protocol_register,
            "_load_protocol_browser_new_driver",
            return_value=lambda *args, **kwargs: (driver, "proxy-dir"),
        ), mock.patch.object(
            protocol_register,
            "_hydrate_browser_driver_with_protocol_session_cookies",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_browser_collect_page_state",
            return_value={"readyState": "complete"},
        ), mock.patch.object(
            protocol_register,
            "_browser_try_click_codex_oauth_action",
            side_effect=click_account,
        ) as click_action, mock.patch.object(
            protocol_register,
            "_browser_codex_action_probe",
            return_value={},
        ), mock.patch.object(
            protocol_register,
            "_browser_switch_to_single_new_window",
            return_value=(False, False),
        ), mock.patch.object(
            protocol_register,
            "_browser_codex_action_diagnostics",
            return_value={},
        ), mock.patch.object(
            protocol_register,
            "_import_browser_driver_cookies_into_session",
            return_value=2,
        ), mock.patch.object(
            protocol_register.time,
            "sleep",
        ), mock.patch.object(
            protocol_register.shutil,
            "rmtree",
        ):
            result = protocol_register._complete_codex_oauth_with_browser(
                session=session,
                oauth=oauth,
                explicit_proxy="http://proxy.local:8080",
                default_email="user@example.com",
                preferred_workspace_id="workspace-personal",
                entry_url=entry_url,
            )

        self.assertEqual((callback_url, None), result)
        self.assertEqual([mock.call(entry_url), mock.call(oauth.auth_url)], driver.get.call_args_list)
        click_action.assert_called_once()
        self.assertEqual("", driver._protocol_last_codex_account_picker_action_key)

    def test_browser_cookie_hydration_prefers_host_only_cdp_cookie_injection(self) -> None:
        cookie = SimpleNamespace(
            name="__Host-session",
            value="test-value",
            domain="auth.openai.com",
            domain_specified=False,
            path="/",
            secure=True,
            expires=1780000000,
            _rest={"HttpOnly": True, "SameSite": "Lax"},
        )
        session = SimpleNamespace(cookies=[cookie])
        driver = SimpleNamespace(
            get=mock.Mock(),
            execute_cdp_cmd=mock.Mock(return_value={"success": True}),
            add_cookie=mock.Mock(),
        )

        imported = protocol_register._hydrate_browser_driver_with_protocol_session_cookies(
            driver,
            session=session,
        )

        self.assertEqual(1, imported)
        driver.execute_cdp_cmd.assert_called_once()
        command, payload = driver.execute_cdp_cmd.call_args.args
        self.assertEqual("Network.setCookie", command)
        self.assertEqual("https://auth.openai.com/", payload["url"])
        self.assertNotIn("domain", payload)
        self.assertTrue(payload["httpOnly"])
        self.assertEqual("Lax", payload["sameSite"])
        driver.add_cookie.assert_not_called()

    def test_browser_cookie_hydration_falls_back_when_cdp_rejects_cookie(self) -> None:
        cookie = SimpleNamespace(
            name="session",
            value="test-value",
            domain=".chatgpt.com",
            domain_specified=True,
            path="/",
            secure=True,
            expires=None,
            _rest={},
        )
        session = SimpleNamespace(cookies=[cookie])
        driver = SimpleNamespace(
            get=mock.Mock(),
            execute_cdp_cmd=mock.Mock(return_value={"success": False}),
            add_cookie=mock.Mock(),
        )

        imported = protocol_register._hydrate_browser_driver_with_protocol_session_cookies(
            driver,
            session=session,
        )

        self.assertEqual(1, imported)
        driver.add_cookie.assert_called_once()
        self.assertEqual("chatgpt.com", driver.add_cookie.call_args.args[0]["domain"])

    def test_browser_cookie_hydration_preserves_domain_cookie_scope_and_false_http_only(self) -> None:
        cookie = SimpleNamespace(
            name="session",
            value="test-value",
            domain=".openai.com",
            domain_specified=True,
            path="/auth",
            secure=True,
            expires=None,
            _rest={"HttpOnly": "false", "SameSite": "Strict"},
        )
        session = SimpleNamespace(cookies=[cookie])
        driver = SimpleNamespace(
            get=mock.Mock(),
            execute_cdp_cmd=mock.Mock(return_value={"success": True}),
            add_cookie=mock.Mock(),
        )

        imported = protocol_register._hydrate_browser_driver_with_protocol_session_cookies(
            driver,
            session=session,
        )

        self.assertEqual(1, imported)
        command, payload = driver.execute_cdp_cmd.call_args.args
        self.assertEqual("Network.setCookie", command)
        self.assertEqual(".openai.com", payload["domain"])
        self.assertNotIn("url", payload)
        self.assertFalse(payload["httpOnly"])
        self.assertEqual("Strict", payload["sameSite"])
        driver.add_cookie.assert_not_called()

    def test_browser_cookie_hydration_falls_back_on_ambiguous_cdp_result(self) -> None:
        cookie = SimpleNamespace(
            name="session",
            value="test-value",
            domain="chatgpt.com",
            domain_specified=False,
            path="/",
            secure=False,
            expires=None,
            _rest={"HttpOnly": None, "SameSite": "None"},
        )
        session = SimpleNamespace(cookies=[cookie])
        driver = SimpleNamespace(
            get=mock.Mock(),
            execute_cdp_cmd=mock.Mock(return_value={}),
            add_cookie=mock.Mock(),
        )

        imported = protocol_register._hydrate_browser_driver_with_protocol_session_cookies(
            driver,
            session=session,
        )

        self.assertEqual(1, imported)
        command, cdp_payload = driver.execute_cdp_cmd.call_args.args
        self.assertEqual("Network.setCookie", command)
        self.assertEqual("https://chatgpt.com/", cdp_payload["url"])
        self.assertNotIn("domain", cdp_payload)
        self.assertTrue(cdp_payload["secure"])
        self.assertTrue(cdp_payload["httpOnly"])
        self.assertEqual("None", cdp_payload["sameSite"])
        fallback_payload = driver.add_cookie.call_args.args[0]
        self.assertNotIn("domain", fallback_payload)
        self.assertTrue(fallback_payload["secure"])
        self.assertTrue(fallback_payload["httpOnly"])
        self.assertEqual("None", fallback_payload["sameSite"])

    def test_codex_account_picker_entry_url_prefers_trusted_picker_location(self) -> None:
        response = SimpleNamespace(
            url="https://auth.openai.com/oauth/resume?state=state_123",
            headers={"Location": "/choose-an-account?state=state_123"},
        )

        entry_url = protocol_register._codex_account_picker_entry_url(
            response,
            fallback_url="https://auth.openai.com/oauth/authorize?state=state_123",
        )

        self.assertEqual(
            "https://auth.openai.com/choose-an-account?state=state_123",
            entry_url,
        )

    def test_codex_account_picker_entry_url_rejects_untrusted_redirects(self) -> None:
        response = SimpleNamespace(
            url="https://untrusted.example/choose-an-account",
            headers={"Location": "https://untrusted.example/choose-an-account"},
        )

        entry_url = protocol_register._codex_account_picker_entry_url(
            response,
            fallback_url="https://untrusted.example/oauth/authorize",
        )

        self.assertEqual("", entry_url)

    def test_codex_account_picker_detection_rejects_login_html_copy(self) -> None:
        response = SimpleNamespace(
            url="https://auth.openai.com/log-in",
            headers={},
            text="<html><body>Sign in or choose an account to continue</body></html>",
        )

        self.assertFalse(protocol_register._is_codex_account_picker_response(response))
        self.assertEqual(
            "",
            protocol_register._codex_account_picker_entry_url(
                response,
                fallback_url="https://auth.openai.com/oauth/authorize?state=state_123",
            ),
        )

    def test_browser_codex_action_result_and_diagnostics_are_value_safe(self) -> None:
        driver = mock.Mock()
        native_click_target = mock.Mock()
        driver.execute_script.side_effect = [
            {
                "target": native_click_target,
                "kind": "account",
                "mode": "identity_ranked",
                "tag": "BUTTON",
                "isButton": True,
                "disabled": False,
                "ariaDisabled": "absent",
                "buttonType": "button",
                "hasForm": False,
                "hasOnClick": True,
                "hasReactOnClick": True,
            },
            {
                "accountBoundaryCount": 1,
                "accountBusinessButtonCount": 0,
                "accountButtonWithFormCount": 1,
                "accountButtonWithOnClickCount": 0,
                "accountClickableButtonCount": 1,
                "accountContinueButtonCount": 0,
                "accountEligibleButtonCount": 1,
                "accountForeignButtonCount": 1,
                "accountOtherButtonCount": 0,
                "accountPersonalButtonCount": 1,
                "accountPickerUrl": True,
                "accountRejectedButtonCount": 1,
                "accountTeamButtonCount": 0,
                "accountVisibleButtonCount": 2,
                "bodyTextLength": 120,
                "elementCount": 25,
                "buttonCount": 1,
                "anchorCount": 0,
                "formCount": 1,
                "inputCount": 2,
                "roleButtonCount": 0,
                "roleOptionCount": 1,
                "tabIndexCount": 1,
                "iframeCount": 0,
                "accountSemanticCount": 1,
                "emailMatchCount": 3,
                "workspaceMatchCount": 2,
                "consentUrl": False,
                "hasBody": True,
                "unexpectedSensitiveValue": "must-not-escape",
            },
        ]

        action = protocol_register._browser_try_click_codex_oauth_action(
            driver,
            default_email="user@example.com",
            preferred_workspace_id="workspace-personal",
        )
        diagnostics = protocol_register._browser_codex_action_diagnostics(
            driver,
            default_email="user@example.com",
            preferred_workspace_id="workspace-personal",
        )

        self.assertEqual("account", action)
        native_click_target.click.assert_called_once_with()
        self.assertEqual(
            {
                "mode": "identity_ranked",
                "tag": "button",
                "isButton": True,
                "disabled": False,
                "ariaDisabled": "absent",
                "buttonType": "button",
                "hasForm": False,
                "hasOnClick": True,
                "hasReactOnClick": True,
            },
            driver._protocol_last_codex_action_metadata,
        )
        self.assertEqual(1, diagnostics["accountBoundaryCount"])
        self.assertEqual(0, diagnostics["accountBusinessButtonCount"])
        self.assertEqual(1, diagnostics["accountButtonWithFormCount"])
        self.assertEqual(0, diagnostics["accountButtonWithOnClickCount"])
        self.assertEqual(1, diagnostics["accountClickableButtonCount"])
        self.assertEqual(0, diagnostics["accountContinueButtonCount"])
        self.assertEqual(1, diagnostics["accountEligibleButtonCount"])
        self.assertEqual(1, diagnostics["accountForeignButtonCount"])
        self.assertEqual(0, diagnostics["accountOtherButtonCount"])
        self.assertEqual(1, diagnostics["accountPersonalButtonCount"])
        self.assertEqual(1, diagnostics["accountRejectedButtonCount"])
        self.assertEqual(0, diagnostics["accountTeamButtonCount"])
        self.assertEqual(2, diagnostics["accountVisibleButtonCount"])
        self.assertTrue(diagnostics["accountPickerUrl"])
        self.assertFalse(diagnostics["consentUrl"])
        self.assertEqual(3, diagnostics["emailMatchCount"])
        self.assertEqual(2, diagnostics["workspaceMatchCount"])
        self.assertTrue(diagnostics["hasBody"])
        self.assertNotIn("unexpectedSensitiveValue", diagnostics)
        self.assertEqual(2, driver.execute_script.call_count)
        self.assertEqual("user@example.com", driver.execute_script.call_args_list[0].args[1])
        self.assertEqual("workspace-personal", driver.execute_script.call_args_list[0].args[2])
        action_script = driver.execute_script.call_args_list[0].args[0]
        diagnostics_script = driver.execute_script.call_args_list[1].args[0]
        self.assertNotIn('[data-testid*="account" i]', action_script)
        self.assertNotIn('[class*="account" i]', action_script)
        self.assertNotIn("'[class], [id]'", action_script)
        self.assertIn("current.getAttribute('aria-hidden') === 'true'", action_script)
        self.assertIn("targetContainsMultipleIdentities", action_script)
        self.assertIn("targetContainsForeignIdentity", action_script)
        self.assertIn("fullDescriptor(boundary)", action_script)
        self.assertIn("emailTokens.includes(email)", action_script)
        self.assertNotIn("descriptor.includes(email)", action_script)
        self.assertNotIn("descriptor.includes(preferredWorkspaceId)", action_script)
        self.assertNotIn(
            "if (email || preferredWorkspaceId) {\n                return { clicked: false, kind: '' };",
            action_script,
        )
        self.assertIn("genericTargets.size > 1", action_script)
        self.assertIn("genericTargets.size === 1", action_script)
        self.assertIn("if (email || preferredWorkspaceId)", action_script)
        self.assertIn("accountButtonTargets.size !== 1", action_script)
        self.assertIn("topIdentityTargets.has(element)", action_script)
        self.assertIn("tiedAccountButtonTargets.size === 1", action_script)
        self.assertIn("rejectedAccountAction", action_script)
        self.assertIn("use another(?: account)?|choose another(?: account)?|switch account", action_script)
        self.assertIn("log in|sign in|create account|register|continue with", action_script)
        self.assertIn("element.form || element.closest('form')", action_script)
        self.assertIn("actionUrl.origin !== location.origin", action_script)
        self.assertIn("consentCandidates.size !== 1", action_script)
        self.assertIn("window.__easyProtocolCodexActionProbe", action_script)
        self.assertIn("target.getAttribute('aria-disabled')", action_script)
        self.assertIn("target.getAttribute('type') || target.type", action_script)
        self.assertIn("typeof reactProps.onClick === 'function'", action_script)
        self.assertIn("event.isTrusted", action_script)
        self.assertIn("const wrappedFetch = originalFetch ? function()", action_script)
        self.assertIn("const wrappedXhrSend = originalXhrSend ? function()", action_script)
        self.assertIn("actionHost.endsWith('.openai.com')", action_script)
        self.assertIn("actionHost.endsWith('.chatgpt.com')", action_script)
        self.assertIn("responsePromise.then(", action_script)
        self.assertIn("window.fetch === wrappedFetch", action_script)
        self.assertIn("xhrPrototype.send === wrappedXhrSend", action_script)
        self.assertNotIn("element.click()", action_script)
        self.assertNotIn("clickTarget", action_script)
        self.assertIn("selectedTarget(target, 'account', 'identity_tied_button')", action_script)
        self.assertNotIn('[data-testid*="account" i]', diagnostics_script)
        self.assertIn("leafBoundaryCount > 1", diagnostics_script)

    def test_browser_codex_action_native_click_failure_is_not_counted(self) -> None:
        driver = mock.Mock()
        native_click_target = mock.Mock()
        native_click_target.click.side_effect = RuntimeError("intercepted")
        driver.execute_script.return_value = {
            "target": native_click_target,
            "kind": "account",
        }

        action = protocol_register._browser_try_click_codex_oauth_action(
            driver,
            default_email="user@example.com",
            preferred_workspace_id="workspace-personal",
        )

        self.assertEqual("", action)
        native_click_target.click.assert_called_once_with()
        self.assertEqual(2, driver.execute_script.call_count)
        cleanup_script = driver.execute_script.call_args_list[1].args[0]
        self.assertIn("probe.cleanup()", cleanup_script)

    def test_browser_codex_account_picker_click_is_single_flight_per_page(self) -> None:
        driver = mock.Mock()
        driver.current_url = "https://auth.openai.com/choose-an-account?secret=must-not-persist"
        native_click_target = mock.Mock()
        driver.execute_script.return_value = {
            "target": native_click_target,
            "kind": "account",
        }

        first_action = protocol_register._browser_try_click_codex_oauth_action(
            driver,
            default_email="user@example.com",
            preferred_workspace_id="workspace-personal",
        )
        second_action = protocol_register._browser_try_click_codex_oauth_action(
            driver,
            default_email="user@example.com",
            preferred_workspace_id="workspace-personal",
        )

        self.assertEqual("account", first_action)
        self.assertEqual("", second_action)
        native_click_target.click.assert_called_once_with()
        driver.execute_script.assert_called_once()
        self.assertEqual(
            "auth.openai.com/choose-an-account",
            driver._protocol_last_codex_account_picker_action_key,
        )
        self.assertNotIn("secret", driver._protocol_last_codex_account_picker_action_key)

    def test_codex_auth_root_url_is_query_insensitive_and_host_strict(self) -> None:
        self.assertTrue(
            protocol_register._is_codex_auth_root_url(
                "https://auth.openai.com/?state=must-not-persist"
            )
        )
        self.assertFalse(
            protocol_register._is_codex_auth_root_url(
                "https://auth.openai.com/choose-an-account?state=must-not-persist"
            )
        )
        self.assertFalse(protocol_register._is_codex_auth_root_url("https://example.invalid/"))

    def test_codex_browser_handoff_error_code_drops_detail(self) -> None:
        code = protocol_register._codex_browser_handoff_error_code(
            RuntimeError("codex_browser_handoff_incomplete secret=must-not-escape")
        )

        self.assertEqual("codex_browser_handoff_incomplete", code)
        self.assertNotIn("secret", code)

    def test_browser_codex_action_probe_is_value_safe(self) -> None:
        driver = mock.Mock()
        driver.execute_script.return_value = {
            "available": True,
            "targetConnected": False,
            "captureCount": 2,
            "trustedCaptureCount": 2,
            "bubbleCount": 1,
            "defaultPrevented": True,
            "mutationCount": 5000,
            "formSubmitCount": 1,
            "windowErrorCount": 2,
            "unhandledRejectionCount": 3,
            "resourceErrorCount": 4,
            "resourceCount": 5,
            "resourceFetchCount": 2,
            "resourceXhrCount": 1,
            "resourceStatusUnknownCount": 1,
            "resource2xxCount": 2,
            "resource3xxCount": 0,
            "resource4xxCount": 1,
            "resource5xxCount": 1,
            "resource400Count": 1,
            "resource401Count": 2,
            "resource403Count": 3,
            "resource404Count": 4,
            "resource409Count": 5,
            "resource429Count": 6,
            "resourceOther4xxCount": 7,
            "resourceAuthAccounts4xxCount": 8,
            "resourceOauth4xxCount": 9,
            "resourceCodex4xxCount": 10,
            "resourceAuthOther4xxCount": 11,
            "resourceExternal4xxCount": 12,
            "resource403FetchCount": 13,
            "resource403XhrCount": 14,
            "resource403ApiCount": 15,
            "resource403BackendApiCount": 16,
            "resource403CdnCount": 17,
            "resource403PickerCount": 18,
            "resource403StaticCount": 19,
            "resource403OtherAuthCount": 20,
            "resourceActionAuthAccountsCount": 21,
            "resourceActionOauthCount": 22,
            "resourceActionCodexCount": 23,
            "resourceActionApiCount": 24,
            "resourceActionBackendApiCount": 25,
            "resourceActionCdnCount": 26,
            "resourceActionPickerCount": 27,
            "resourceActionStaticCount": 28,
            "resourceActionOtherAuthCount": 29,
            "resourceActionExternalCount": 30,
            "fetchCallCount": 31,
            "xhrSendCount": 32,
            "actionAuthAccountsCallCount": 33,
            "actionOauthCallCount": 34,
            "actionCodexCallCount": 35,
            "actionApiCallCount": 36,
            "actionBackendApiCallCount": 37,
            "actionCdnCallCount": 38,
            "actionPickerCallCount": 39,
            "actionStaticCallCount": 40,
            "actionOtherAuthCallCount": 41,
            "actionExternalCallCount": 42,
            "actionOpenAiCallCount": 43,
            "actionChatgptCallCount": 44,
            "actionNonHttpCallCount": 45,
            "actionThirdPartyCallCount": 46,
            "fetchResolvedCount": 47,
            "fetchRejectedCount": 48,
            "fetch4xxCount": 49,
            "fetch5xxCount": 50,
            "unexpectedSensitiveValue": "must-not-escape",
        }

        probe = protocol_register._browser_codex_action_probe(driver)

        self.assertEqual(
            {
                "available": True,
                "targetConnected": False,
                "defaultPrevented": True,
                "captureCount": 2,
                "trustedCaptureCount": 2,
                "bubbleCount": 1,
                "mutationCount": 999,
                "formSubmitCount": 1,
                "windowErrorCount": 2,
                "unhandledRejectionCount": 3,
                "resourceErrorCount": 4,
                "resourceCount": 5,
                "resourceFetchCount": 2,
                "resourceXhrCount": 1,
                "resourceStatusUnknownCount": 1,
                "resource2xxCount": 2,
                "resource3xxCount": 0,
                "resource4xxCount": 1,
                "resource5xxCount": 1,
                "resource400Count": 1,
                "resource401Count": 2,
                "resource403Count": 3,
                "resource404Count": 4,
                "resource409Count": 5,
                "resource429Count": 6,
                "resourceOther4xxCount": 7,
                "resourceAuthAccounts4xxCount": 8,
                "resourceOauth4xxCount": 9,
                "resourceCodex4xxCount": 10,
                "resourceAuthOther4xxCount": 11,
                "resourceExternal4xxCount": 12,
                "resource403FetchCount": 13,
                "resource403XhrCount": 14,
                "resource403ApiCount": 15,
                "resource403BackendApiCount": 16,
                "resource403CdnCount": 17,
                "resource403PickerCount": 18,
                "resource403StaticCount": 19,
                "resource403OtherAuthCount": 20,
                "resourceActionAuthAccountsCount": 21,
                "resourceActionOauthCount": 22,
                "resourceActionCodexCount": 23,
                "resourceActionApiCount": 24,
                "resourceActionBackendApiCount": 25,
                "resourceActionCdnCount": 26,
                "resourceActionPickerCount": 27,
                "resourceActionStaticCount": 28,
                "resourceActionOtherAuthCount": 29,
                "resourceActionExternalCount": 30,
                "fetchCallCount": 31,
                "xhrSendCount": 32,
                "actionAuthAccountsCallCount": 33,
                "actionOauthCallCount": 34,
                "actionCodexCallCount": 35,
                "actionApiCallCount": 36,
                "actionBackendApiCallCount": 37,
                "actionCdnCallCount": 38,
                "actionPickerCallCount": 39,
                "actionStaticCallCount": 40,
                "actionOtherAuthCallCount": 41,
                "actionExternalCallCount": 42,
                "actionOpenAiCallCount": 43,
                "actionChatgptCallCount": 44,
                "actionNonHttpCallCount": 45,
                "actionThirdPartyCallCount": 46,
                "fetchResolvedCount": 47,
                "fetchRejectedCount": 48,
                "fetch4xxCount": 49,
                "fetch5xxCount": 50,
            },
            probe,
        )
        self.assertNotIn("unexpectedSensitiveValue", probe)
        probe_script = driver.execute_script.call_args.args[0]
        self.assertIn("window.__easyProtocolCodexActionProbe", probe_script)
        self.assertIn("probe.cleanup()", probe_script)
        self.assertIn("window.__easyProtocolCodexActionProbe = null", probe_script)
        self.assertIn("performance.getEntriesByType('resource')", probe_script)
        self.assertIn("entry.responseStatus", probe_script)
        self.assertIn("entry.name", probe_script)
        self.assertIn("resourceAuthAccounts4xxCount", probe_script)
        self.assertIn("resourceActionAuthAccountsCount", probe_script)
        self.assertIn("initiatorType === 'fetch' || initiatorType === 'xmlhttprequest'", probe_script)
        self.assertNotIn("resourceUrl", probe)

    def test_browser_codex_action_probe_attempts_cleanup_after_script_failure(self) -> None:
        driver = mock.Mock()
        driver.execute_script.side_effect = [RuntimeError("transient"), None]

        probe = protocol_register._browser_codex_action_probe(driver)

        self.assertFalse(probe["available"])
        self.assertEqual(2, driver.execute_script.call_count)
        cleanup_script = driver.execute_script.call_args_list[1].args[0]
        self.assertIn("probe.cleanup()", cleanup_script)
        self.assertIn("window.__easyProtocolCodexActionProbe = null", cleanup_script)

    def test_browser_codex_switches_only_to_one_new_window(self) -> None:
        driver = SimpleNamespace(
            window_handles=["existing", "new"],
            switch_to=SimpleNamespace(
                window=mock.Mock(),
                default_content=mock.Mock(),
            ),
        )

        opened, switched = protocol_register._browser_switch_to_single_new_window(
            driver,
            before_handles=["existing"],
        )

        self.assertTrue(opened)
        self.assertTrue(switched)
        driver.switch_to.window.assert_called_once_with("new")
        driver.switch_to.default_content.assert_called_once_with()

        driver.window_handles = ["existing", "new", "other"]
        driver.switch_to.window.reset_mock()
        opened, switched = protocol_register._browser_switch_to_single_new_window(
            driver,
            before_handles=["existing"],
        )
        self.assertTrue(opened)
        self.assertFalse(switched)
        driver.switch_to.window.assert_not_called()

    def test_browser_codex_action_transition_diagnostics_do_not_emit_urls(self) -> None:
        transition = protocol_register._browser_codex_action_transition_diagnostics(
            action_kind="account",
            before_url="https://example.invalid/choose?secret=before",
            after_url="https://example.invalid/next?secret=after",
            action_metadata={
                "mode": "identity_ranked",
                "tag": "button",
                "isButton": True,
                "disabled": False,
                "ariaDisabled": "absent",
                "buttonType": "button",
                "hasForm": False,
                "hasOnClick": True,
                "hasReactOnClick": True,
            },
            action_probe={
                "available": True,
                "targetConnected": False,
                "captureCount": 1,
                "trustedCaptureCount": 1,
                "bubbleCount": 1,
                "defaultPrevented": True,
                "mutationCount": 4,
                "formSubmitCount": 1,
                "windowErrorCount": 2,
                "unhandledRejectionCount": 3,
                "resourceErrorCount": 4,
                "resourceCount": 5,
                "resourceFetchCount": 2,
                "resourceXhrCount": 1,
                "resourceStatusUnknownCount": 1,
                "resource2xxCount": 2,
                "resource3xxCount": 0,
                "resource4xxCount": 1,
                "resource5xxCount": 1,
                "resource400Count": 1,
                "resource401Count": 0,
                "resource403Count": 0,
                "resource404Count": 0,
                "resource409Count": 0,
                "resource429Count": 0,
                "resourceOther4xxCount": 0,
                "resourceAuthAccounts4xxCount": 1,
                "resourceOauth4xxCount": 0,
                "resourceCodex4xxCount": 0,
                "resourceAuthOther4xxCount": 0,
                "resourceExternal4xxCount": 0,
                "resource403FetchCount": 1,
                "resource403XhrCount": 0,
                "resource403ApiCount": 1,
                "resource403BackendApiCount": 0,
                "resource403CdnCount": 0,
                "resource403PickerCount": 0,
                "resource403StaticCount": 0,
                "resource403OtherAuthCount": 0,
                "resourceActionAuthAccountsCount": 2,
                "resourceActionOauthCount": 1,
                "resourceActionCodexCount": 3,
                "resourceActionApiCount": 4,
                "resourceActionBackendApiCount": 5,
                "resourceActionCdnCount": 6,
                "resourceActionPickerCount": 7,
                "resourceActionStaticCount": 8,
                "resourceActionOtherAuthCount": 9,
                "resourceActionExternalCount": 10,
                "fetchCallCount": 31,
                "xhrSendCount": 32,
                "actionAuthAccountsCallCount": 33,
                "actionOauthCallCount": 34,
                "actionCodexCallCount": 35,
                "actionApiCallCount": 36,
                "actionBackendApiCallCount": 37,
                "actionCdnCallCount": 38,
                "actionPickerCallCount": 39,
                "actionStaticCallCount": 40,
                "actionOtherAuthCallCount": 41,
                "actionExternalCallCount": 42,
                "actionOpenAiCallCount": 43,
                "actionChatgptCallCount": 44,
                "actionNonHttpCallCount": 45,
                "actionThirdPartyCallCount": 46,
                "fetchResolvedCount": 47,
                "fetchRejectedCount": 48,
                "fetch4xxCount": 49,
                "fetch5xxCount": 50,
            },
            new_window_opened=True,
            new_window_switched=True,
            after_diagnostics={
                "accountClickableButtonCount": 2,
                "accountPersonalButtonCount": 1,
                "accountTeamButtonCount": 1,
                "accountPickerUrl": True,
                "consentUrl": False,
                "hasBody": True,
            },
        )

        self.assertEqual("account", transition["actionKind"])
        self.assertEqual("identity_ranked", transition["actionMode"])
        self.assertEqual("button", transition["targetTag"])
        self.assertTrue(transition["targetIsButton"])
        self.assertFalse(transition["targetDisabled"])
        self.assertEqual("absent", transition["targetAriaDisabled"])
        self.assertEqual("button", transition["targetButtonType"])
        self.assertFalse(transition["targetHasForm"])
        self.assertTrue(transition["targetHasOnClick"])
        self.assertTrue(transition["targetHasReactOnClick"])
        self.assertTrue(transition["clickProbeAvailable"])
        self.assertFalse(transition["targetConnectedAfter"])
        self.assertTrue(transition["clickDefaultPrevented"])
        self.assertEqual(1, transition["captureCount"])
        self.assertEqual(1, transition["trustedCaptureCount"])
        self.assertEqual(1, transition["bubbleCount"])
        self.assertEqual(4, transition["mutationCount"])
        self.assertTrue(transition["newWindowOpened"])
        self.assertTrue(transition["newWindowSwitched"])
        self.assertEqual(1, transition["formSubmitCount"])
        self.assertEqual(2, transition["windowErrorCount"])
        self.assertEqual(3, transition["unhandledRejectionCount"])
        self.assertEqual(4, transition["resourceErrorCount"])
        self.assertEqual(5, transition["resourceCount"])
        self.assertEqual(2, transition["resourceFetchCount"])
        self.assertEqual(1, transition["resourceXhrCount"])
        self.assertEqual(1, transition["resourceStatusUnknownCount"])
        self.assertEqual(2, transition["resource2xxCount"])
        self.assertEqual(1, transition["resource4xxCount"])
        self.assertEqual(1, transition["resource5xxCount"])
        self.assertEqual(1, transition["resource400Count"])
        self.assertEqual(1, transition["resourceAuthAccounts4xxCount"])
        self.assertEqual(0, transition["resourceExternal4xxCount"])
        self.assertEqual(1, transition["resource403FetchCount"])
        self.assertEqual(1, transition["resource403ApiCount"])
        self.assertEqual(2, transition["resourceActionAuthAccountsCount"])
        self.assertEqual(3, transition["resourceActionCodexCount"])
        self.assertEqual(10, transition["resourceActionExternalCount"])
        self.assertEqual(31, transition["fetchCallCount"])
        self.assertEqual(32, transition["xhrSendCount"])
        self.assertEqual(33, transition["actionAuthAccountsCallCount"])
        self.assertEqual(35, transition["actionCodexCallCount"])
        self.assertEqual(42, transition["actionExternalCallCount"])
        self.assertEqual(43, transition["actionOpenAiCallCount"])
        self.assertEqual(44, transition["actionChatgptCallCount"])
        self.assertEqual(45, transition["actionNonHttpCallCount"])
        self.assertEqual(46, transition["actionThirdPartyCallCount"])
        self.assertEqual(47, transition["fetchResolvedCount"])
        self.assertEqual(48, transition["fetchRejectedCount"])
        self.assertEqual(49, transition["fetch4xxCount"])
        self.assertEqual(50, transition["fetch5xxCount"])
        self.assertTrue(transition["urlChanged"])
        self.assertEqual(2, transition["accountClickableButtonCount"])
        self.assertEqual(1, transition["accountPersonalButtonCount"])
        self.assertEqual(1, transition["accountTeamButtonCount"])
        serialized = json.dumps(transition, sort_keys=True)
        self.assertNotIn("example.invalid", serialized)
        self.assertNotIn("secret", serialized)

    def test_browser_codex_action_trace_summary_has_constant_size(self) -> None:
        transition = protocol_register._browser_codex_action_transition_diagnostics(
            action_kind="account",
            before_url="https://example.invalid/before?secret=before",
            after_url="https://example.invalid/after?secret=after",
            action_metadata={
                "mode": "identity_ranked",
                "tag": "button",
                "isButton": True,
                "disabled": False,
                "ariaDisabled": "absent",
                "buttonType": "button",
                "hasForm": False,
                "hasOnClick": True,
                "hasReactOnClick": True,
            },
            action_probe={
                "available": True,
                "targetConnected": True,
                "captureCount": 1,
                "trustedCaptureCount": 1,
                "bubbleCount": 1,
                "defaultPrevented": False,
                "mutationCount": 3,
                "formSubmitCount": 1,
                "windowErrorCount": 2,
                "unhandledRejectionCount": 3,
                "resourceErrorCount": 4,
                "resourceCount": 5,
                "resourceFetchCount": 2,
                "resourceXhrCount": 1,
                "resourceStatusUnknownCount": 1,
                "resource2xxCount": 2,
                "resource3xxCount": 0,
                "resource4xxCount": 1,
                "resource5xxCount": 1,
                "resource400Count": 1,
                "resource401Count": 2,
                "resource403Count": 3,
                "resource404Count": 4,
                "resource409Count": 5,
                "resource429Count": 6,
                "resourceOther4xxCount": 7,
                "resourceAuthAccounts4xxCount": 8,
                "resourceOauth4xxCount": 9,
                "resourceCodex4xxCount": 10,
                "resourceAuthOther4xxCount": 11,
                "resourceExternal4xxCount": 12,
                "resource403FetchCount": 13,
                "resource403XhrCount": 14,
                "resource403ApiCount": 15,
                "resource403BackendApiCount": 16,
                "resource403CdnCount": 17,
                "resource403PickerCount": 18,
                "resource403StaticCount": 19,
                "resource403OtherAuthCount": 20,
                "resourceActionAuthAccountsCount": 21,
                "resourceActionOauthCount": 22,
                "resourceActionCodexCount": 23,
                "resourceActionApiCount": 24,
                "resourceActionBackendApiCount": 25,
                "resourceActionCdnCount": 26,
                "resourceActionPickerCount": 27,
                "resourceActionStaticCount": 28,
                "resourceActionOtherAuthCount": 29,
                "resourceActionExternalCount": 30,
                "fetchCallCount": 31,
                "xhrSendCount": 32,
                "actionAuthAccountsCallCount": 33,
                "actionOauthCallCount": 34,
                "actionCodexCallCount": 35,
                "actionApiCallCount": 36,
                "actionBackendApiCallCount": 37,
                "actionCdnCallCount": 38,
                "actionPickerCallCount": 39,
                "actionStaticCallCount": 40,
                "actionOtherAuthCallCount": 41,
                "actionExternalCallCount": 42,
                "actionOpenAiCallCount": 43,
                "actionChatgptCallCount": 44,
                "actionNonHttpCallCount": 45,
                "actionThirdPartyCallCount": 46,
                "fetchResolvedCount": 47,
                "fetchRejectedCount": 48,
                "fetch4xxCount": 49,
                "fetch5xxCount": 50,
            },
            after_diagnostics={
                "accountClickableButtonCount": 1,
                "accountOtherButtonCount": 1,
                "accountPickerUrl": True,
                "consentUrl": False,
                "hasBody": True,
            },
        )
        summary: dict[str, int | bool | str] = {}
        for _ in range(100):
            summary = protocol_register._browser_codex_action_trace_summary(summary, transition)

        self.assertEqual(100, summary["count"])
        self.assertEqual(100, summary["urlChangedCount"])
        self.assertEqual(100, summary["accountPickerAfterCount"])
        self.assertEqual(1, summary["lastAccountClickableButtonCount"])
        self.assertEqual("identity_ranked", summary["lastActionMode"])
        self.assertEqual("button", summary["lastTargetTag"])
        self.assertTrue(summary["lastTargetIsButton"])
        self.assertEqual("button", summary["lastTargetButtonType"])
        self.assertTrue(summary["lastTargetHasOnClick"])
        self.assertTrue(summary["lastTargetHasReactOnClick"])
        self.assertTrue(summary["lastClickProbeAvailable"])
        self.assertTrue(summary["lastTargetConnectedAfter"])
        self.assertEqual(1, summary["lastCaptureCount"])
        self.assertEqual(1, summary["lastTrustedCaptureCount"])
        self.assertEqual(1, summary["lastBubbleCount"])
        self.assertEqual(3, summary["lastMutationCount"])
        self.assertEqual(1, summary["lastFormSubmitCount"])
        self.assertEqual(2, summary["lastWindowErrorCount"])
        self.assertEqual(3, summary["lastUnhandledRejectionCount"])
        self.assertEqual(4, summary["lastResourceErrorCount"])
        self.assertEqual(5, summary["lastResourceCount"])
        self.assertEqual(2, summary["lastResourceFetchCount"])
        self.assertEqual(1, summary["lastResourceXhrCount"])
        self.assertEqual(1, summary["lastResourceStatusUnknownCount"])
        self.assertEqual(2, summary["lastResource2xxCount"])
        self.assertEqual(1, summary["lastResource4xxCount"])
        self.assertEqual(1, summary["lastResource5xxCount"])
        self.assertEqual(3, summary["lastResource403Count"])
        self.assertEqual(13, summary["lastResource403FetchCount"])
        self.assertEqual(14, summary["lastResource403XhrCount"])
        self.assertEqual(15, summary["lastResource403ApiCount"])
        self.assertEqual(16, summary["lastResource403BackendApiCount"])
        self.assertEqual(17, summary["lastResource403CdnCount"])
        self.assertEqual(18, summary["lastResource403PickerCount"])
        self.assertEqual(19, summary["lastResource403StaticCount"])
        self.assertEqual(20, summary["lastResource403OtherAuthCount"])
        self.assertEqual(21, summary["lastResourceActionAuthAccountsCount"])
        self.assertEqual(22, summary["lastResourceActionOauthCount"])
        self.assertEqual(23, summary["lastResourceActionCodexCount"])
        self.assertEqual(24, summary["lastResourceActionApiCount"])
        self.assertEqual(25, summary["lastResourceActionBackendApiCount"])
        self.assertEqual(26, summary["lastResourceActionCdnCount"])
        self.assertEqual(27, summary["lastResourceActionPickerCount"])
        self.assertEqual(28, summary["lastResourceActionStaticCount"])
        self.assertEqual(29, summary["lastResourceActionOtherAuthCount"])
        self.assertEqual(30, summary["lastResourceActionExternalCount"])
        self.assertEqual(31, summary["lastFetchCallCount"])
        self.assertEqual(32, summary["lastXhrSendCount"])
        self.assertEqual(33, summary["lastActionAuthAccountsCallCount"])
        self.assertEqual(34, summary["lastActionOauthCallCount"])
        self.assertEqual(35, summary["lastActionCodexCallCount"])
        self.assertEqual(36, summary["lastActionApiCallCount"])
        self.assertEqual(37, summary["lastActionBackendApiCallCount"])
        self.assertEqual(38, summary["lastActionCdnCallCount"])
        self.assertEqual(39, summary["lastActionPickerCallCount"])
        self.assertEqual(40, summary["lastActionStaticCallCount"])
        self.assertEqual(41, summary["lastActionOtherAuthCallCount"])
        self.assertEqual(42, summary["lastActionExternalCallCount"])
        self.assertEqual(43, summary["lastActionOpenAiCallCount"])
        self.assertEqual(44, summary["lastActionChatgptCallCount"])
        self.assertEqual(45, summary["lastActionNonHttpCallCount"])
        self.assertEqual(46, summary["lastActionThirdPartyCallCount"])
        self.assertEqual(47, summary["lastFetchResolvedCount"])
        self.assertEqual(48, summary["lastFetchRejectedCount"])
        self.assertEqual(49, summary["lastFetch4xxCount"])
        self.assertEqual(50, summary["lastFetch5xxCount"])
        serialized = json.dumps(summary, sort_keys=True, separators=(",", ":"))
        self.assertLess(len(serialized), 3072)
        self.assertNotIn("example.invalid", serialized)
        self.assertNotIn("secret", serialized)

        log_payload = protocol_register._browser_codex_action_trace_log_payload(summary)
        log_serialized = json.dumps(log_payload, separators=(",", ":"))
        self.assertEqual(100, log_payload["n"])
        self.assertEqual(100, log_payload["nav"])
        self.assertEqual(1, log_payload["tr"])
        self.assertEqual(31, log_payload["fc"])
        self.assertEqual(32, log_payload["xc"])
        self.assertEqual(33, log_payload["aa"])
        self.assertEqual(34, log_payload["ao"])
        self.assertEqual(35, log_payload["ac"])
        self.assertEqual(36, log_payload["ai"])
        self.assertEqual(37, log_payload["ba"])
        self.assertEqual(42, log_payload["ex"])
        self.assertEqual(43, log_payload["oi"])
        self.assertEqual(44, log_payload["cg"])
        self.assertEqual(45, log_payload["nh"])
        self.assertEqual(46, log_payload["tp"])
        self.assertEqual(47, log_payload["fr"])
        self.assertEqual(48, log_payload["fj"])
        self.assertEqual(49, log_payload["f4"])
        self.assertEqual(50, log_payload["f5"])
        self.assertEqual(3, log_payload["mut"])
        self.assertFalse(log_payload["dp"])
        self.assertEqual(1, log_payload["cb"])
        self.assertEqual(
            {
                "n", "nav", "picker", "fc", "xc", "aa", "ao", "ac", "ai", "ba",
                "ex", "tr", "oi", "cg", "nh", "tp", "fr", "fj", "f4", "f5",
                "mut", "dp", "cb",
            },
            set(log_payload),
        )
        self.assertLess(len(log_serialized), 220)
        self.assertTrue(log_serialized.startswith('{"n":100,"nav":100,'))
        self.assertNotIn("example.invalid", log_serialized)
        self.assertNotIn("secret", log_serialized)

        message = protocol_register._browser_codex_handoff_incomplete_message(
            action_count=100,
            action_trace=summary,
            ready_state="complete",
            final_url="https://example.invalid/choose?secret=not-logged",
            action_diagnostics={"accountClickableButtonCount": 1},
        )
        trace_start = message.index("trace=") + len("trace=")
        trace_end = message.index(" ready_state=", trace_start)
        current_start = message.index(" current=", trace_end)
        diagnostics_start = message.index(" diagnostics=", current_start)
        message_trace = json.loads(message[trace_start:trace_end])

        self.assertEqual(log_payload, message_trace)
        self.assertLess(trace_start, current_start)
        self.assertLess(current_start, diagnostics_start)
        self.assertNotIn("not-logged", message)

    def test_browser_codex_action_script_failure_is_not_silently_retried(self) -> None:
        driver = mock.Mock()
        driver.execute_script.side_effect = ValueError("unsupported browser script")

        with self.assertRaisesRegex(RuntimeError, "codex_browser_action_script_failed"):
            protocol_register._browser_try_click_codex_oauth_action(
                driver,
                default_email="user@example.com",
                preferred_workspace_id="workspace-personal",
            )

    def test_run_protocol_repair_once_refreshes_oauth_state_from_browser_bootstrap_after_authorize_challenge(self) -> None:
        session = mock.Mock()
        session.headers = {}
        oauth = SimpleNamespace(
            auth_url="https://auth.openai.com/api/accounts/authorize?x=1",
            state="state_123",
            code_verifier="verifier",
            redirect_uri="https://chatgpt.com/api/auth/callback/openai",
        )
        challenge_response = SimpleNamespace(
            status_code=403,
            headers={"cf-mitigated": "challenge"},
            url="https://auth.openai.com/api/accounts/authorize?x=1",
            text="challenge",
        )
        challenge_response.json = lambda: {}
        signup_response = SimpleNamespace(status_code=200, headers={}, url=protocol_register.AUTHORIZE_CONTINUE_URL, text="ok")
        signup_response.json = lambda: {}
        phone_response = SimpleNamespace(
            status_code=200,
            url="https://auth.openai.com/add-phone",
        )
        phone_response.json = lambda: {
            "page": {"type": "add_phone"},
            "continue_url": "https://auth.openai.com/add-phone",
        }
        browser_bootstrap = protocol_register.ProtocolBrowserBootstrapResult(
            current_url="https://chatgpt.com/auth/login",
            did="did_browser",
            user_agent="ua-browser",
            imported_cookie_count=4,
            auth_url="https://auth.openai.com/api/accounts/authorize?browser=1&state=state_browser",
            auth_state="state_browser",
        )

        with mock.patch.object(
            protocol_register,
            "generate_oauth_url",
            return_value=oauth,
        ), mock.patch.object(
            protocol_register,
            "get_mailbox_latest_message_id",
            return_value=0,
        ), mock.patch.object(
            protocol_register,
            "_session_request",
            side_effect=[challenge_response, signup_response],
        ), mock.patch.object(
            protocol_register,
            "_get_session_cookie",
            return_value="",
        ), mock.patch.object(
            protocol_register,
            "_maybe_prime_protocol_auth_session_with_easycaptcha_browser_bootstrap",
            return_value=(SimpleNamespace(user_agent="ua-browser", device_id="did_browser"), browser_bootstrap),
        ) as easycaptcha_browser_bootstrap, mock.patch.object(
            protocol_register,
            "_maybe_prime_protocol_auth_session_with_browser",
        ) as local_browser_bootstrap, mock.patch.object(
            protocol_register,
            "_build_protocol_headers",
            return_value={},
        ), mock.patch.object(
            protocol_register,
            "_resolve_repair_oauth_entry",
            return_value=(phone_response, "add_phone", protocol_register.LOGIN_PASSWORD_REFERER),
        ), mock.patch.object(
            protocol_register,
            "_export_protocol_session_cookies",
            return_value=[{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
        ):
            result = protocol_register.run_protocol_repair_once(
                auth_obj={
                    "email": "user@example.com",
                    "password": "pw",
                    "mailbox_ref": "mailtm:test",
                    "session_id": "mailbox_123",
                },
                existing_session=session,
                existing_sentinel_context=SimpleNamespace(user_agent="ua", device_id="device"),
            )

        self.assertTrue(result.phone_verification_required)
        self.assertEqual("state_browser", result.resume_context["oauth"]["state"])
        self.assertEqual(
            "https://auth.openai.com/api/accounts/authorize?browser=1&state=state_browser",
            result.resume_context["oauth"]["authUrl"],
        )
        easycaptcha_browser_bootstrap.assert_called_once()
        local_browser_bootstrap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
