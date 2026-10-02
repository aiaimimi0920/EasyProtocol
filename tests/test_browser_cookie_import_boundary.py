from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1] / "providers" / "python"
for path in (ROOT / "src", ROOT / "python_shared" / "src"):
    sys.path.insert(0, str(path))

from protocol_runtime import protocol_register as runtime


class BrowserCookieImportBoundaryTests(unittest.TestCase):
    def session_and_driver(self):
        cookies = []
        session = SimpleNamespace(cookies=SimpleNamespace(set=lambda name, value, **kwargs: cookies.append((name, value))))
        driver = mock.Mock()
        driver.current_url = "https://auth.openai.com/email-verification"
        driver.get_cookies.return_value = [
            {"name": "login_session", "value": "fixture-login", "domain": "auth.openai.com"},
            {"name": "auth-session-minimized", "value": "fixture-metadata", "domain": "auth.openai.com"},
            {"name": "cf_clearance", "value": "fixture-edge", "domain": "auth.openai.com"},
            {"name": "__cf_bm", "value": "fixture-edge", "domain": "auth.openai.com"},
            {"name": "_cfuvid", "value": "fixture-edge", "domain": "auth.openai.com"},
        ]
        return session, driver, cookies

    def test_verified_document_import_excludes_edge_clearance_cookies(self):
        session, driver, cookies = self.session_and_driver()
        imported = runtime._import_browser_driver_cookies_into_session(session, driver=driver, navigate=False)
        self.assertEqual(2, imported)
        self.assertEqual(["login_session", "auth-session-minimized"], [name for name, _ in cookies])
        driver.get.assert_not_called()
        driver.get_cookies.assert_called_once()

    def test_verified_document_import_does_not_navigate(self):
        session, driver, _ = self.session_and_driver()
        driver.get_cookies.return_value = [driver.get_cookies.return_value[0]]
        self.assertEqual(1, runtime._import_browser_driver_cookies_into_session(session, driver=driver, navigate=False))
        driver.get.assert_not_called()
        self.assertEqual("https://auth.openai.com/email-verification", driver.current_url)

    def test_legacy_default_import_retains_navigation_and_deduplication(self):
        session, driver, cookies = self.session_and_driver()
        driver.get_cookies.return_value = [driver.get_cookies.return_value[0]]
        self.assertEqual(1, runtime._import_browser_driver_cookies_into_session(session, driver=driver))
        self.assertEqual(10, driver.get.call_count)
        self.assertEqual([("login_session", "fixture-login")], cookies)


if __name__ == "__main__":
    unittest.main()
