from __future__ import annotations

import contextlib
import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from selenium.common.exceptions import TimeoutException


ROOT = Path(__file__).resolve().parents[1] / "providers" / "python"
for path in (ROOT / "src", ROOT / "python_shared" / "src"):
    sys.path.insert(0, str(path))

from new_protocol_register import protocol_chatgpt_login as flow
from new_protocol_register import protocol_small_success as signup
from new_protocol_register.protocol_browser_session import BrowserLoginSession
from protocol_runtime.errors import ProtocolRuntimeError
from protocol_runtime import protocol_register as browser_runtime


class BrowserLoginSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = BrowserLoginSession()
        self.addCleanup(self.session.close)
        self.challenge = flow.requests.Response()
        self.challenge.status_code = 403
        self.challenge.headers = {"cf-mitigated": "challenge"}
        self.challenge.content = b"challenge"

    @contextlib.contextmanager
    def browser(self, *, status: int = 200, cookies: list | None = None):
        driver = mock.Mock()
        driver.current_url = "about:blank"
        driver.page_source = "<html>Log in</html>"
        driver.title = "Log in"
        browser_cookies = cookies if cookies is not None else []

        def navigate(url):
            driver.current_url = url

        driver.get.side_effect = navigate
        driver.execute_script.side_effect = lambda script: (
            "complete" if script == "return document.readyState"
            else "fixture-browser" if "navigator.userAgent" in script else status
        )
        driver.execute_cdp_cmd.side_effect = lambda command, params: {"cookies": list(browser_cookies)} if command == "Network.getAllCookies" else {}
        factory = mock.Mock(return_value=(driver, None))
        with mock.patch.object(browser_runtime, "_load_protocol_browser_new_driver", return_value=factory):
            yield driver, factory, browser_cookies

    def test_success_and_application_rejection_do_not_launch_browser(self) -> None:
        for status in (200, 400, 403):
            response = flow.requests.Response()
            response.status_code = status
            with self.subTest(status=status), mock.patch.object(
                flow.requests.Session, "request", return_value=response,
            ), mock.patch.object(self.session, "_native_request") as native:
                self.assertIs(response, self.session.get(flow.CHATGPT_LOGIN_URL))
                native.assert_not_called()

    def test_unsupported_requests_keep_original_transport(self) -> None:
        for method, url, options in (
            ("GET", "https://example.test/", {}),
            ("DELETE", flow.CHATGPT_LOGIN_URL, {}),
            ("GET", flow.CHATGPT_LOGIN_URL, {"allow_redirects": False}),
            ("GET", flow.CHATGPT_LOGIN_URL, {"stream": True}),
        ):
            with self.subTest(method=method, url=url, options=options), mock.patch.object(
                flow.requests.Session, "request", return_value=self.challenge,
            ), mock.patch.object(self.session, "_native_request") as native:
                self.assertIs(self.challenge, self.session.request(method, url, **options))
                native.assert_not_called()

    def test_otp_challenge_is_not_replayed_by_transport(self) -> None:
        for recover in (False, True):
            with self.subTest(recover=recover), BrowserLoginSession(recover_challenges=recover) as session, mock.patch.object(
                flow.requests.Session, "request", return_value=self.challenge,
            ) as protocol, mock.patch.object(session, "_native_request") as native:
                response = session.post(signup.EMAIL_OTP_VALIDATE_URL, json={"code": "123456"})
                self.assertIs(self.challenge, response)
                protocol.assert_called_once()
                native.assert_not_called()

    def test_signup_reports_transport_otp_challenge_without_any_browser_replay(self) -> None:
        with mock.patch.object(
            flow.requests.Session, "request", return_value=self.challenge,
        ) as protocol, mock.patch.object(self.session, "_native_request") as native, mock.patch.object(
            signup, "_get_sentinel_header_for_signup", return_value="fixture-sentinel",
        ), mock.patch.object(signup, "_recover_platform_otp_validate_in_browser") as separate_browser:
            with self.assertRaisesRegex(ProtocolRuntimeError, "browser_verification_required") as caught:
                signup._submit_email_otp_validate(
                    session=self.session, code="123456", device_id="fixture-device",
                    sentinel_context=None, explicit_proxy=None,
                )
        self.assertEqual("stage_otp_validate", caught.exception.stage)
        self.assertEqual("email_otp_validate", caught.exception.detail)
        self.assertEqual("blocked", caught.exception.category)
        protocol.assert_called_once()
        native.assert_not_called()
        separate_browser.assert_not_called()

    def test_deferred_recovery_keeps_protocol_until_verified_browser_handoff(self) -> None:
        with BrowserLoginSession(recover_challenges=False) as session, mock.patch.object(
            flow.requests.Session, "request", return_value=self.challenge,
        ) as protocol, mock.patch.object(session, "_native_request") as native:
            self.assertIs(self.challenge, session.get(flow.CHATGPT_LOGIN_URL))
            native.assert_not_called()
            with contextlib.ExitStack() as cleanup:
                driver = mock.Mock()
                driver.execute_cdp_cmd.return_value = {"cookies": []}
                session.adopt_browser(driver, proxy=None, proxy_dir=None, cleanup=cleanup)
            session.get(flow.CHATGPT_LOGIN_URL)
            native.assert_called_once()
            protocol.assert_called_once()

    def test_unobserved_navigation_status_keeps_original_challenge(self) -> None:
        self.session.cookies.set("login_session", "fixture", domain="auth.openai.com")
        with self.browser(status=0) as (driver, _, _), mock.patch.object(
            flow.requests.Session, "request", return_value=self.challenge,
        ):
            response = self.session.get(flow.CHATGPT_LOGIN_URL)
        self.assertIs(self.challenge, response)
        driver.quit.assert_called_once()
        self.assertIsNone(self.session._driver)
        self.assertIsNone(self.session._profile)

    def test_html_navigation_accepts_interactive_document_with_observed_status(self) -> None:
        with self.browser() as (driver, _, _), mock.patch.object(
            flow.requests.Session, "request", return_value=self.challenge,
        ):
            driver.execute_script.side_effect = lambda script: (
                "interactive" if script == "return document.readyState"
                else "fixture-browser" if "navigator.userAgent" in script else 200
            )
            response = self.session.get(flow.CHATGPT_LOGIN_URL)
        self.assertEqual(200, response.status_code)
        self.assertEqual(flow.CHATGPT_LOGIN_URL, response.url)

    def test_navigation_timeout_accepts_only_a_committed_new_document(self) -> None:
        for new_document, ready_state, accepted in (
            (True, "interactive", True),
            (True, "complete", True),
            (False, "complete", False),
            (True, "loading", False),
        ):
            with self.subTest(new_document=new_document, ready_state=ready_state):
                with BrowserLoginSession() as session, self.browser() as (driver, _, _), mock.patch.object(
                    flow.requests.Session, "request", return_value=self.challenge,
                ):
                    document_origins = iter((1000, 2000 if new_document else 1000))

                    def evaluate(script):
                        if script == "return performance.timeOrigin":
                            return next(document_origins)
                        if script == "return document.readyState":
                            return ready_state
                        return "fixture-browser" if "navigator.userAgent" in script else 200

                    def navigate(url):
                        driver.current_url = url
                        raise TimeoutException("eager navigation deadline exceeded")

                    driver.get.side_effect = navigate
                    driver.execute_script.side_effect = evaluate
                    response = session.get(flow.CHATGPT_LOGIN_URL)
                    if accepted:
                        self.assertEqual(200, response.status_code)
                        self.assertIsNot(response, self.challenge)
                    else:
                        self.assertIs(self.challenge, response)
                        self.assertIsNone(session._driver)

    def test_cookie_sync_preserves_application_state_without_copying_edge_cookies(self) -> None:
        self.session.cookies.set("oai-did", "fixture-device", domain="chatgpt.com", secure=True)
        self.session.cookies.set("old-state", "stale", domain="chatgpt.com", secure=True)
        self.session.cookies.set("unrelated", "keep", domain="example.test")
        for name in ("cf_clearance", "__cf_bm", "_cfuvid"):
            self.session.cookies.set(name, "protocol-edge", domain="chatgpt.com", secure=True)
        cookies = [
            {"name": "__Secure-next-auth.state", "value": "fixture-state", "domain": ".chatgpt.com",
             "path": "/", "secure": True, "httpOnly": True, "expires": 2000000000, "sameSite": "Lax"},
            {"name": "cf_clearance", "value": "browser-edge", "domain": ".chatgpt.com", "path": "/"},
        ]
        with self.browser(cookies=cookies) as (driver, _, _), mock.patch.object(
            flow.requests.Session, "request", return_value=self.challenge,
        ):
            self.session.get(flow.CHATGPT_LOGIN_URL)
        seeded = next(call.args[1]["cookies"] for call in driver.execute_cdp_cmd.call_args_list if call.args[0] == "Network.setCookies")
        self.assertEqual({"oai-did", "old-state"}, {cookie["name"] for cookie in seeded})
        self.assertEqual("fixture-state", self.session.cookies.get("__Secure-next-auth.state"))
        self.assertEqual("keep", self.session.cookies.get("unrelated"))
        self.assertIsNone(self.session.cookies.get("old-state"))
        self.assertIsNone(self.session.cookies.get("cf_clearance"))
        state = next(cookie for cookie in self.session.cookies.jar if cookie.name == "__Secure-next-auth.state")
        self.assertTrue(state.has_nonstandard_attr("HttpOnly"))
        self.assertEqual(2000000000, state.expires)

    def test_chatgpt_bootstrap_reuses_browser_and_real_csrf_state(self) -> None:
        auth_url = "https://auth.openai.com/authorize?state=fixture"
        with self.browser() as (driver, factory, cookies), mock.patch.object(
            flow.requests.Session, "request", return_value=self.challenge,
        ) as protocol:
            def fetch(script, args):
                if args["method"] == "GET":
                    payload = {"csrfToken": "fixture-csrf"}
                else:
                    self.assertEqual("fixture-csrf", urllib.parse.parse_qs(args["body"])["csrfToken"][0])
                    cookies.append({"name": "__Secure-next-auth.state", "value": "fixture-state", "domain": "chatgpt.com", "path": "/", "secure": True})
                    payload = {"url": auth_url}
                return {"status": 200, "url": args["url"], "headers": {"content-type": "application/json"}, "body": json.dumps(payload)}

            driver.execute_async_script.side_effect = fetch
            result = flow._bootstrap_chatgpt_login_with_redirect(
                session=self.session, device_id="fixture-device", explicit_proxy="http://fixture-proxy:8080",
            )
            self.assertEqual(auth_url, result)
            self.assertEqual(200, self.session.get(result, proxy="http://fixture-proxy:8080").status_code)
            self.assertEqual([mock.call(flow.CHATGPT_LOGIN_URL), mock.call(auth_url)], driver.get.call_args_list)
            factory.assert_called_once_with("http://fixture-proxy:8080", browser_backend="custom")
            protocol.assert_called_once()
            self.assertEqual(2, driver.execute_async_script.call_count)
            self.assertEqual("fixture-state", self.session.cookies.get("__Secure-next-auth.state"))

    def test_native_post_preserves_rejection_and_request_payload(self) -> None:
        url = "https://auth.openai.com/api/accounts/authorize/continue"
        with self.browser() as (driver, _, _), mock.patch.object(
            flow.requests.Session, "request", return_value=self.challenge,
        ):
            driver.execute_async_script.return_value = {
                "status": 400, "url": url, "headers": {"content-type": "application/json"},
                "body": '{"error":"invalid_request"}',
            }
            response = self.session.post(url, json={"screen_hint": "login"}, headers={
                "Accept": "application/json", "Origin": "https://wrong.example", "Cookie": "do-not-copy",
                "User-Agent": "protocol-agent", "openai-sentinel-token": "fixture-sentinel",
            })
        self.assertEqual(400, response.status_code)
        self.assertEqual({"error": "invalid_request"}, response.json())
        self.assertEqual("application/json", response.headers.get("Content-Type"))
        args = driver.execute_async_script.call_args.args[1]
        self.assertEqual({"screen_hint": "login"}, json.loads(args["body"]))
        self.assertEqual("fixture-sentinel", args["headers"]["openai-sentinel-token"])
        self.assertTrue({"origin", "cookie", "user-agent"}.isdisjoint(args["headers"]))

    def test_signup_keeps_browser_transport_from_otp_through_account_creation(self) -> None:
        with self.browser() as (driver, factory, _), mock.patch.object(
            flow.requests.Session, "request", return_value=self.challenge,
        ) as protocol, mock.patch.object(
            signup, "_get_sentinel_header_for_signup", return_value="fixture-sentinel",
        ), mock.patch.object(signup, "_recover_platform_otp_validate_in_browser") as separate_browser:
            self.session._driver = driver
            self.session._browser_proxy = "http://fixture-proxy:8080"
            driver.execute_async_script.side_effect = lambda script, args: {
                "status": 200, "url": args["url"], "headers": {"content-type": "application/json"},
                "body": '{"page":{"type":"about_you"}}',
            }
            otp = signup._submit_email_otp_validate(
                session=self.session, code="123456", device_id="fixture-device",
                sentinel_context=None, explicit_proxy="http://fixture-proxy:8080",
            )
            created = signup._submit_create_account(
                session=self.session, name="Fixture User", birthdate="1990-01-01",
                device_id="fixture-device", sentinel_context=None, explicit_proxy="http://fixture-proxy:8080",
            )
            self.assertEqual(200, otp.status_code)
            self.assertEqual(200, created.status_code)
            protocol.assert_not_called()
            factory.assert_not_called()
            separate_browser.assert_not_called()
            calls = driver.execute_async_script.call_args_list
            self.assertEqual([signup.EMAIL_OTP_VALIDATE_URL, signup.CREATE_ACCOUNT_URL], [call.args[1]["url"] for call in calls])
            self.assertEqual({"name": "Fixture User", "birthdate": "1990-01-01"}, json.loads(calls[1].args[1]["body"]))
            self.assertEqual(signup.ABOUT_YOU_REFERER, calls[1].args[1]["referrer"])
            self.assertEqual(
                [mock.call(signup.EMAIL_VERIFICATION_REFERER)],
                driver.get.call_args_list,
            )

    def test_native_post_waits_for_document_load(self) -> None:
        ready = False
        states = iter(("interactive", "complete"))
        url = "https://auth.openai.com/api/accounts/authorize/continue"
        with self.browser() as (driver, _, _), mock.patch.object(
            flow.requests.Session, "request", return_value=self.challenge,
        ):
            def evaluate(script):
                nonlocal ready
                if script == "return document.readyState":
                    state = next(states)
                    ready = state == "complete"
                    return state
                return 200 if "responseStatus" in script else "fixture-browser"

            def fetch(script, args):
                self.assertTrue(ready)
                return {"status": 400, "url": url, "headers": {}, "body": '{"error":"invalid_request"}'}

            driver.execute_script.side_effect = evaluate
            driver.execute_async_script.side_effect = fetch
            response = self.session.post(url, json={})
        self.assertEqual(400, response.status_code)

    def test_native_post_does_not_submit_from_a_challenge_document(self) -> None:
        with self.browser(status=403) as (driver, _, _), mock.patch.object(
            flow.requests.Session, "request", return_value=self.challenge,
        ):
            driver.title = "Just a moment..."
            response = self.session.post(
                "https://auth.openai.com/api/accounts/create_account",
                json={"name": "Fixture User", "birthdate": "1990-01-01"},
                headers={"Referer": "https://auth.openai.com/about-you"}, timeout=1,
            )
        self.assertIs(self.challenge, response)
        driver.execute_async_script.assert_not_called()
        driver.quit.assert_called_once()

    def test_close_cleans_resources_even_if_driver_quit_fails(self) -> None:
        self.session._driver = driver = mock.Mock()
        driver.quit.side_effect = RuntimeError("fixture failure")
        self.session._profile = tempfile.TemporaryDirectory(prefix="browser-login-test-")
        self.session._proxy_dir = tempfile.mkdtemp(prefix="browser-login-proxy-test-")
        profile = Path(self.session._profile.name)
        proxy = Path(self.session._proxy_dir)
        self.session.close()
        self.session.close()
        driver.quit.assert_called_once()
        self.assertFalse(profile.exists())
        self.assertFalse(proxy.exists())


if __name__ == "__main__":
    unittest.main()
