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
from new_protocol_register.protocol_browser_session import BrowserDocumentChallenge, BrowserLoginSession
from protocol_runtime.errors import ProtocolRuntimeError
from protocol_runtime import protocol_register as browser_runtime


class PlatformBrowserOtpValidateTests(unittest.TestCase):
    def test_challenged_document_is_blocked_without_opt_in_otp_replay(self) -> None:
        for enabled in ("0", "1"):
            with self.subTest(enabled=enabled):
                error = BrowserDocumentChallenge("browser_document_challenged")
                with mock.patch.dict(os.environ, {"PROTOCOL_ENABLE_BROWSER_OTP_FALLBACK": enabled}), mock.patch.object(
                    flow, "_get_sentinel_header_for_signup", return_value="fixture",
                ), mock.patch.object(flow, "_deduped_cookie_header_for_request", return_value=""), mock.patch.object(
                    flow, "_session_request", side_effect=error,
                ) as request, mock.patch.object(flow, "_recover_platform_otp_validate_in_browser") as recovery:
                    kwargs = dict(session=SimpleNamespace(), code="123456", device_id="fixture-device",
                                  sentinel_context=None, explicit_proxy=None)
                    with self.assertRaisesRegex(ProtocolRuntimeError, "browser_verification_required") as caught:
                        flow._submit_email_otp_validate(**kwargs)
                self.assertEqual("stage_otp_validate", caught.exception.stage)
                self.assertEqual("email_otp_validate", caught.exception.detail)
                self.assertEqual("blocked", caught.exception.category)
                self.assertIs(error, caught.exception.__cause__)
                self.assertNotIn("123456", str(caught.exception))
                request.assert_called_once()
                recovery.assert_not_called()

    def test_challenged_otp_post_is_blocked_before_browser_fallback(self) -> None:
        for enabled in ("0", "1"):
            for status in (200, 403):
                challenge = SimpleNamespace(
                    status_code=status, headers={"cf-mitigated": "challenge"}, text="private-response-123456",
                )
                with self.subTest(enabled=enabled, status=status), mock.patch.dict(
                    os.environ, {"PROTOCOL_ENABLE_BROWSER_OTP_FALLBACK": enabled},
                ), mock.patch.object(flow, "_get_sentinel_header_for_signup", return_value="fixture-sentinel"), mock.patch.object(
                    flow, "_deduped_cookie_header_for_request", return_value="fixture=cookie",
                ), mock.patch.object(flow, "_session_request", return_value=challenge) as request, mock.patch.object(
                    flow, "_recover_platform_otp_validate_in_browser",
                ) as recovery:
                    with self.assertRaisesRegex(ProtocolRuntimeError, "browser_verification_required") as caught:
                        flow._submit_email_otp_validate(
                            session=SimpleNamespace(), code="123456", device_id="fixture-device",
                            sentinel_context=None, explicit_proxy="http://fixture-proxy:8080",
                        )
                    self.assertEqual("stage_otp_validate", caught.exception.stage)
                    self.assertEqual("email_otp_validate", caught.exception.detail)
                    self.assertEqual("blocked", caught.exception.category)
                    self.assertNotIn("123456", str(caught.exception))
                    self.assertNotIn("private-response", str(caught.exception))
                    request.assert_called_once()
                    self.assertEqual({"code": "123456"}, request.call_args.kwargs["json"])
                    recovery.assert_not_called()

    def test_ordinary_otp_response_is_preserved_without_browser_recovery(self) -> None:
        for enabled, status in (("0", 200), ("1", 200), ("1", 400), ("1", 403), ("1", 429)):
            response = SimpleNamespace(status_code=status, headers={}, text='{"fixture":true}')
            with self.subTest(enabled=enabled, status=status), mock.patch.dict(
                os.environ, {"PROTOCOL_ENABLE_BROWSER_OTP_FALLBACK": enabled},
            ), mock.patch.object(flow, "_get_sentinel_header_for_signup", return_value="fixture"), mock.patch.object(
                flow, "_deduped_cookie_header_for_request", return_value="",
            ), mock.patch.object(flow, "_session_request", return_value=response), mock.patch.object(
                flow, "_recover_platform_otp_validate_in_browser", create=True,
            ) as recovery:
                result = flow._submit_email_otp_validate(
                    session=SimpleNamespace(), code="123456", device_id="fixture-device",
                    sentinel_context=None, explicit_proxy=None,
                )
                self.assertIs(response, result)
                recovery.assert_not_called()

    def test_otp_transport_error_is_not_reclassified_as_browser_verification(self) -> None:
        error = RuntimeError("fixture transport timeout")
        with mock.patch.dict(os.environ, {"PROTOCOL_ENABLE_BROWSER_OTP_FALLBACK": "1"}), mock.patch.object(
            flow, "_get_sentinel_header_for_signup", return_value="fixture",
        ), mock.patch.object(flow, "_deduped_cookie_header_for_request", return_value=""), mock.patch.object(
            flow, "_session_request", side_effect=error,
        ) as request, mock.patch.object(flow, "_recover_platform_otp_validate_in_browser") as recovery:
            with self.assertRaises(RuntimeError) as caught:
                flow._submit_email_otp_validate(
                    session=SimpleNamespace(), code="123456", device_id="fixture-device",
                    sentinel_context=None, explicit_proxy=None,
                )
        self.assertIs(error, caught.exception)
        request.assert_called_once()
        recovery.assert_not_called()

    def recover(self, *, username="fixture@example.test", status=200, browser_status=200, browser_session=False,
                minimized=False):
        def cookie(email):
            payload = {"username": {"value": email}, "original_screen_hint": "login_or_signup"}
            return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=") + ".time.signature"

        session = BrowserLoginSession() if browser_session else flow.requests.Session()
        self.session = session
        self.addCleanup(session.close)
        session.cookies.set("login_session", "fixture-login", domain="auth.openai.com", secure=True)
        session.cookies.set("oai-client-auth-session", cookie("fixture@example.test"), domain="auth.openai.com", secure=True)
        for name in ("cf_clearance", "__cf_bm", "_cfuvid"):
            session.cookies.set(name, "protocol-client-bound-cookie", domain="auth.openai.com", secure=True)
        driver = mock.Mock()
        driver.current_url = flow.EMAIL_VERIFICATION_REFERER
        driver.page_source = "<html>Check your email</html>"
        driver.execute_script.return_value = browser_status
        cookies = [
            {"name": "login_session", "value": "fixture-login", "domain": "auth.openai.com", "path": "/", "secure": True},
            {"name": "oai-client-auth-session", "value": cookie(username), "domain": "auth.openai.com", "path": "/", "secure": True},
            {"name": "cf_clearance", "value": "browser-client-bound-cookie", "domain": "auth.openai.com", "path": "/", "secure": True},
        ]
        if minimized:
            claims = {"session_id": "fixture-session", "auth_session_logging_id": "fixture-log",
                      "openai_client_id": flow._PLATFORM_AUTH0_CLIENT_ID}
            encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
            value = encode({"alg": "ES256", "typ": "JWT"}) + "." + encode(claims) + ".fixture-signature"
            payload = {**claims, "username": {"value": "fixture@example.test"},
                       "email": "fixture@example.test", "original_screen_hint": "login_or_signup"}
            session.cookies.delete("login_session", domain="auth.openai.com")
            session.cookies.delete("oai-client-auth-session", domain="auth.openai.com")
            session.cookies.set("auth-session-minimized", value, domain="auth.openai.com", secure=True)
            session._browser_verified_auth_session = {"cookie": value, "payload": payload}
            cookies = [{"name": "auth-session-minimized", "value": value,
                        "domain": "auth.openai.com", "path": "/", "secure": True}]
            driver.execute_script.side_effect = [browser_status, {**payload, "username": {"value": username}}]
        driver.get_cookies.return_value = cookies
        driver.execute_cdp_cmd.return_value = {"cookies": cookies}
        driver.execute_async_script.return_value = {
            "status": status, "url": flow.EMAIL_OTP_VALIDATE_URL,
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"page": {"type": "about_you"}}) if status == 200 else '{"error":"invalid_code"}',
        }
        with mock.patch.object(
            browser_runtime, "_load_protocol_browser_new_driver",
            return_value=mock.Mock(return_value=(driver, None)),
        ):
            response = flow._recover_platform_otp_validate_in_browser(
                session=session, code="123456", headers={"cookie": "fixture-login", "openai-sentinel-token": "fixture-token"},
                explicit_proxy="http://fixture-proxy:8080",
            )
        if browser_session and status == 200:
            driver.quit.assert_not_called()
            self.assertIs(session._driver, driver)
        else:
            driver.quit.assert_called_once()
        driver.get.assert_called_once_with(flow.EMAIL_VERIFICATION_REFERER)
        return response, driver

    def test_native_response_and_cookie_identity_are_preserved(self) -> None:
        response, driver = self.recover()
        self.assertEqual(200, response.status_code)
        self.assertEqual("about_you", response.json()["page"]["type"])
        self.assertEqual(flow.EMAIL_OTP_VALIDATE_URL, response.url)
        driver.execute_async_script.assert_called_once()
        args = driver.execute_async_script.call_args.args[1]
        self.assertEqual("123456", args["code"])
        self.assertNotIn("cookie", args["headers"])
        self.assertEqual("fixture-token", args["headers"]["openai-sentinel-token"])

    def test_minimized_session_can_continue_otp_without_legacy_cookies(self) -> None:
        response, driver = self.recover(minimized=True)
        self.assertIsNotNone(response)
        self.assertEqual(200, response.status_code)
        driver.execute_async_script.assert_called_once()

    def test_minimized_session_still_requires_verified_navigation_and_identity(self) -> None:
        for settings in ({"username": "different@example.test"}, {"browser_status": 0}, {"browser_status": 403}):
            with self.subTest(settings=settings):
                response, driver = self.recover(minimized=True, **settings)
                self.assertIsNone(response)
                driver.execute_async_script.assert_not_called()

    def test_successful_recovery_browser_is_owned_until_session_close(self) -> None:
        response, driver = self.recover(browser_session=True)
        self.assertEqual(200, response.status_code)
        self.assertIsNotNone(self.session._adopted_cleanup)
        self.session.close()
        self.session.close()
        driver.quit.assert_called_once()
        self.assertIsNone(self.session._adopted_cleanup)

    def test_rejected_recovery_does_not_replace_session_browser(self) -> None:
        response, driver = self.recover(browser_session=True, status=400)
        self.assertEqual(400, response.status_code)
        self.assertIsNone(self.session._driver)
        driver.quit.assert_called_once()

    def test_browser_application_rejection_is_returned_without_manufactured_success(self) -> None:
        response, _ = self.recover(status=400)
        self.assertEqual(400, response.status_code)
        self.assertEqual("invalid_code", response.json()["error"])

    def test_browser_does_not_reuse_protocol_cloudflare_cookies(self) -> None:
        _, driver = self.recover()
        seeded = next(call.args[1]["cookies"] for call in driver.execute_cdp_cmd.call_args_list if call.args[0] == "Network.setCookies")
        names = {cookie["name"] for cookie in seeded}
        self.assertEqual({"login_session", "oai-client-auth-session"}, names)
        self.assertEqual("protocol-client-bound-cookie", self.session.cookies.get("cf_clearance", domain="auth.openai.com"))

    def test_mismatched_identity_or_failed_navigation_does_not_submit_otp(self) -> None:
        for settings in ({"username": "different@example.test"}, {"browser_status": 403}):
            with self.subTest(settings=settings):
                response, driver = self.recover(**settings)
                self.assertIsNone(response)
                driver.execute_async_script.assert_not_called()


if __name__ == "__main__":
    unittest.main()
