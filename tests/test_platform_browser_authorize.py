from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1] / "providers" / "python"
for path in (ROOT / "src", ROOT / "python_shared" / "src"):
    sys.path.insert(0, str(path))

from new_protocol_register import protocol_small_success as flow
from protocol_runtime import protocol_register as browser_runtime
from protocol_runtime.errors import ProtocolRuntimeError


class PlatformBrowserAuthorizeTests(unittest.TestCase):
    auth_url = (
        "https://auth.openai.com/api/accounts/authorize?device_id=fixture-device"
        "&state=fixture-state&login_hint=fixture%40example.test"
    )

    def recover(self, *, status=200, body="<html>Check your email</html>",
                url="https://auth.openai.com/email-verification",
                username="fixture@example.test", browser_cookie=True):
        driver = mock.Mock()
        driver.current_url = url
        driver.page_source = body
        driver.execute_cdp_cmd.return_value = {"success": True}
        driver.execute_script.side_effect = [status, "fixture-browser-user-agent"]
        driver.get_cookies.return_value = (
            [{"name": "login_session", "value": "fixture"}] if browser_cookie else []
        )
        session = SimpleNamespace(headers={})
        context = SimpleNamespace(device_id="fixture-device", user_agent="fixture-curl")
        with mock.patch.object(
            browser_runtime, "_load_protocol_browser_new_driver",
            return_value=mock.Mock(return_value=(driver, None)),
        ), mock.patch.object(
            flow, "_import_browser_driver_cookies_into_session", return_value=1,
        ) as imported, mock.patch.object(
            flow, "_decode_current_auth_session_payload",
            return_value={"username": {"value": username}, "original_screen_hint": "login_or_signup"},
        ), mock.patch.object(flow, "_login_session_cookie", return_value="fixture-cookie"), mock.patch.object(
            flow, "_clone_protocol_sentinel_context", return_value=context,
        ):
            result = flow._recover_platform_auth0_authorize_in_browser(
                session=session, auth_url=self.auth_url, device_id="fixture-device",
                sentinel_context=context, explicit_proxy="http://fixture-proxy:8080",
            )
        driver.get.assert_called_once_with(self.auth_url)
        driver.quit.assert_called_once()
        return result, imported.call_count

    def test_verified_native_response_retains_status_identity_and_oauth_transaction(self) -> None:
        result, imports = self.recover()
        _, response, auth_url, device_id = result
        self.assertEqual(200, response.status_code)
        self.assertIn("Check your email", response.text)
        self.assertEqual("https://auth.openai.com/email-verification", response.url)
        self.assertEqual(self.auth_url, auth_url)
        self.assertEqual("fixture-device", device_id)
        self.assertEqual(1, imports)

    def test_cookie_cannot_turn_failed_or_unobserved_navigation_into_success(self) -> None:
        for settings in (
            {"status": 403}, {"status": 0},
            {"body": "<html>Sorry, you have been blocked</html>"},
            {"url": "https://unrelated.example.test/email-verification"},
            {"browser_cookie": False},
        ):
            with self.subTest(settings=settings):
                result, imports = self.recover(**settings)
                self.assertIsNone(result[1])
                self.assertEqual(0, imports)

    def test_cookie_identity_must_match_authorize_login_hint(self) -> None:
        result, _ = self.recover(username="different@example.test")
        self.assertIsNone(result[1])

    def test_failed_opt_in_browser_recovery_retains_original_challenge_without_retry(self) -> None:
        response = SimpleNamespace(status_code=403, headers={"cf-mitigated": "challenge"}, text="challenge")
        with mock.patch.dict(os.environ, {"PROTOCOL_ENABLE_BROWSER_AUTHORIZE_FALLBACK": "1"}), mock.patch.object(
            flow, "_session_request", return_value=response,
        ) as request, mock.patch.object(
            flow, "_recover_platform_auth0_authorize_in_browser",
            return_value=(None, None, self.auth_url, "fixture-device"),
        ) as recovery:
            with self.assertRaisesRegex(ProtocolRuntimeError, "browser_verification_required"):
                flow._platform_auth0_authorize_with_browser_retry(
                    session=SimpleNamespace(), auth_url=self.auth_url, device_id="fixture-device",
                    sentinel_context=None, explicit_proxy=None, request_label="fixture-authorize",
                )
        request.assert_called_once()
        recovery.assert_called_once()

    def test_verified_otp_page_is_recognized_without_another_authorize_submission(self) -> None:
        page_type = flow._platform_authorize_account_page_type(
            response=SimpleNamespace(url="https://auth.openai.com/email-verification"),
            auth_session_payload={
                "username": {"value": "fixture@example.test"},
                "original_screen_hint": "login_or_signup",
            },
            email="fixture@example.test",
        )
        self.assertEqual("email_otp_verification", page_type)
        with mock.patch.object(flow, "_send_email_otp") as resend:
            self.assertTrue(flow._prepare_passwordless_email_otp(
                session=SimpleNamespace(), page_type=page_type, explicit_proxy=None, sentinel_context=None,
            ))
        resend.assert_not_called()


class AuthSessionCookieDecodingTests(unittest.TestCase):
    def test_signed_cookie_metadata_retains_the_same_identity_as_unsigned_cookie(self) -> None:
        payload = {
            "username": {"value": "fixture@example.test"},
            "original_screen_hint": "login_or_signup",
        }
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        self.assertEqual(payload, flow._decode_cookie_payload(encoded))
        self.assertEqual(payload, flow._decode_cookie_payload(encoded + ".fixture-time.fixture-signature"))

    def test_invalid_cookie_metadata_is_not_accepted(self) -> None:
        for value in (None, "", "not-json.time.signature", "e30=.time.signature"):
            with self.subTest(value=value):
                self.assertEqual({}, flow._decode_cookie_payload(value))


if __name__ == "__main__":
    unittest.main()
