from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


ROOT = Path(__file__).resolve().parents[1] / "providers" / "python"
for path in (ROOT / "src", ROOT / "python_shared" / "src"):
    sys.path.insert(0, str(path))

from new_protocol_register.protocol_browser_session import _navigate_html


@unittest.skipUnless(os.environ.get("RUN_BROWSER_INTEGRATION") == "1", "requires real Chromium")
class BrowserNavigationTimeoutIntegrationTests(unittest.TestCase):
    def test_committed_document_survives_navigation_timeout(self) -> None:
        release = threading.Event()
        requested: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                requested.append(self.path)
                if self.path.startswith("/deferred.js"):
                    release.wait(30)
                    body = b""
                    content_type = "text/javascript"
                else:
                    body = (
                        '<!doctype html><title>committed document</title>'
                        '<script defer src="/deferred.js?' + self.path[1:] + '"></script>'
                        '<main id="proof">real document</main>'
                    ).encode("ascii")
                    content_type = "text/html"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(body)

            def log_message(self, _format: str, *args: object) -> None:
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as profile:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            driver = None
            try:
                chrome = shutil.which("chromium") or shutil.which("google-chrome")
                chromedriver = shutil.which("chromedriver")
                self.assertTrue(chrome and chromedriver, "Chromium and ChromeDriver must be installed")
                options = Options()
                options.binary_location = chrome
                options.page_load_strategy = "normal"
                for argument in ("--headless=new", "--no-sandbox", "--no-proxy-server", "--user-data-dir=" + profile):
                    options.add_argument(argument)
                driver = webdriver.Chrome(service=Service(chromedriver), options=options)
                driver.set_page_load_timeout(2)
                base = f"http://127.0.0.1:{server.server_port}"
                timed_out = False
                try:
                    driver.get(base + "/baseline")
                except TimeoutException:
                    timed_out = True
                self.assertTrue(timed_out, {
                    "pageLoadStrategy": driver.capabilities.get("pageLoadStrategy"),
                    "requested": requested, "title": driver.title,
                    "readyState": driver.execute_script("return document.readyState"),
                })
                before = driver.execute_script("return performance.timeOrigin")

                _navigate_html(driver, base + "/fixed", 2)

                self.assertGreater(driver.execute_script("return performance.timeOrigin"), before)
                self.assertEqual(base + "/fixed", driver.current_url)
                self.assertIn(driver.execute_script("return document.readyState"), {"interactive", "complete"})
                self.assertEqual(200, driver.execute_script(
                    "return performance.getEntriesByType('navigation')[0].responseStatus",
                ))
                self.assertEqual("real document", driver.find_element("id", "proof").text)
            finally:
                release.set()
                if driver is not None:
                    with contextlib.suppress(Exception):
                        driver.quit()
                server.shutdown()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
