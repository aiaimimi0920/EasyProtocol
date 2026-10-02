from __future__ import annotations

import contextlib
import io
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

from new_protocol_register import protocol_small_success as signup
from protocol_runtime import protocol_register as runtime


class PlatformAuthorizeBrowserDiagnosticsTests(unittest.TestCase):
    def summary(self, status=200, path="/log-in-or-create-account", body="<input type=email>"):
        return signup._platform_authorize_browser_rejection_summary(
            status=status, current_url="https://auth.openai.com" + path, body=body,
        )

    def test_email_entry_is_not_reported_as_challenge_or_authorized(self):
        value = self.summary()
        self.assertEqual("email_entry_requires_interaction", value["reason"])
        self.assertEqual("email_entry", value["page"])
        self.assertFalse(value["challenge"])

    def test_challenge_has_distinct_reason_even_on_http_200(self):
        for status in (200, 403):
            with self.subTest(status=status):
                value = self.summary(status=status, body="Verify you are human")
                self.assertEqual("challenge_page", value["reason"])
                self.assertEqual(status, value["status"])
                self.assertTrue(value["challenge"])

    def test_missing_navigation_status_is_not_called_http_403(self):
        for status in (0, None):
            value = self.summary(status=status)
            self.assertEqual("navigation_status_unavailable", value["reason"])
            self.assertEqual(0, value["status"])

    def test_non_challenge_http_error_stays_separate(self):
        self.assertEqual("unexpected_http_status", self.summary(status=403)["reason"])

    def test_origin_and_page_mismatch_remain_rejections(self):
        value = signup._platform_authorize_browser_rejection_summary(
            status=200, current_url="https://example.test/log-in-or-create-account", body="",
        )
        self.assertEqual("unexpected_origin", value["reason"])
        self.assertEqual("other", value["page"])
        self.assertEqual("unexpected_page", self.summary(path="/unexpected")["reason"])

    def test_valid_account_document_can_still_lack_auth_cookie(self):
        self.assertEqual("missing_auth_cookie", self.summary(path="/create-account/password")["reason"])

    def test_diagnostics_never_include_url_query_body_or_identifiers(self):
        value = self.summary(
            path="/secret-path?login_hint=private@example.test&state=private-state#private-fragment",
            body="private-password private-cookie private-session-id",
        )
        serialized = json.dumps(value)
        for secret in ("secret-path", "private", "example.test", "login_hint", "https://"):
            self.assertNotIn(secret, serialized)
        self.assertEqual({"reason", "status", "page", "challenge"}, set(value))

    def test_real_recovery_branch_still_rejects_email_entry_once(self):
        driver = mock.Mock()
        driver.current_url = "https://auth.openai.com/log-in-or-create-account?state=private-state"
        driver.page_source = "<input type=email>"
        driver.execute_cdp_cmd.return_value = {"success": True}
        driver.execute_script.return_value = 200
        factory = mock.Mock(return_value=(driver, None))
        auth_url = "https://auth.openai.com/authorize?login_hint=fixture%40example.test&state=private-state"
        sentinel = object()
        log = io.StringIO()
        with mock.patch.object(runtime, "_load_protocol_browser_new_driver", return_value=factory), mock.patch.object(
            signup, "_import_browser_driver_cookies_into_session",
        ) as cookie_import, contextlib.redirect_stdout(log):
            result = signup._recover_platform_auth0_authorize_in_browser(
                session=SimpleNamespace(), auth_url=auth_url, device_id="private-device-id",
                sentinel_context=sentinel, explicit_proxy=None,
            )
        self.assertEqual((sentinel, None, auth_url, "private-device-id"), result)
        driver.get.assert_called_once_with(auth_url)
        driver.get_cookies.assert_not_called()
        driver.quit.assert_called_once()
        cookie_import.assert_not_called()
        self.assertIn('"reason": "email_entry_requires_interaction"', log.getvalue())
        self.assertNotIn("private", log.getvalue())
        self.assertNotIn("example.test", log.getvalue())

    def test_failed_browser_recovery_preserves_original_http_challenge(self):
        response = SimpleNamespace(status_code=403, headers={"cf-mitigated": "challenge"}, text="", url="https://auth.openai.com/authorize")
        sentinel = object()
        auth_url = response.url + "?login_hint=fixture%40example.test"
        with mock.patch.dict(os.environ, {"PROTOCOL_ENABLE_BROWSER_AUTHORIZE_FALLBACK": "1"}), mock.patch.object(
            signup, "_session_request", return_value=response,
        ) as request, mock.patch.object(signup, "_recover_platform_auth0_authorize_in_browser", return_value=(sentinel, None, auth_url, "fixture")):
            with self.assertRaisesRegex(Exception, "browser_verification_required"):
                signup._platform_auth0_authorize_with_browser_retry(
                    session=SimpleNamespace(), auth_url=auth_url, device_id="fixture",
                    sentinel_context=sentinel, explicit_proxy=None, request_label="fixture",
                )
        request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
