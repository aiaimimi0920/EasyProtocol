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
from new_protocol_register import easyprotocol_flow  # noqa: E402
from new_protocol_register.magic import _classify_invite_error  # noqa: E402
from new_protocol_register import protocol_chatgpt_login  # noqa: E402
from new_protocol_register import protocol_small_success  # noqa: E402
from new_protocol_register.others import runtime as protocol_runtime  # noqa: E402
from protocol_runtime import protocol_register  # noqa: E402
from shared_captcha import service_client as captcha_service_client  # noqa: E402
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

    def test_obtain_team_mother_oauth_force_email_auth_skips_refresh(self) -> None:
        with mock.patch.object(
            easyprotocol_flow,
            "load_json_payload",
            return_value={
                "email": "mother@example.com",
                "refresh_token": "rt_demo",
            },
        ), mock.patch.object(
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
                    "source_path": "C:/tmp/mother.json",
                    "output_dir": "C:/tmp/out",
                    "force_email_auth": True,
                },
            )
        refresh_team_auth_once.assert_not_called()
        run_protocol_oauth_from_path.assert_called_once()
        self.assertEqual("email", result["authMode"])
        self.assertFalse(bool(result.get("refreshOnly")))

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


if __name__ == "__main__":
    unittest.main()
