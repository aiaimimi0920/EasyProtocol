from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import traceback
import urllib.parse
import uuid
from collections.abc import Mapping
from http.cookiejar import Cookie
from pathlib import Path

from curl_cffi import requests
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait


_BROWSER_HOSTS = {"auth.openai.com", "chatgpt.com", "www.chatgpt.com"}
_COOKIE_DOMAINS = _BROWSER_HOSTS | {"openai.com"}


def _edge_cookie(name: str) -> bool:
    return name == "cf_clearance" or name.startswith(("_cf", "__cf"))


def _browser_origin(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in _BROWSER_HOSTS or parsed.port not in (None, 443):
        return ""
    return "https://" + str(parsed.hostname)


def _navigate_html(driver: WebDriver, url: str, seconds: float) -> None:
    previous_document = driver.execute_script("return performance.timeOrigin")
    try:
        driver.get(url)
    except TimeoutException:
        current_document = driver.execute_script("return performance.timeOrigin")
        if (
            not isinstance(previous_document, (int, float))
            or not isinstance(current_document, (int, float))
            or current_document <= previous_document
            or driver.execute_script("return document.readyState") not in {"interactive", "complete"}
        ):
            raise
    WebDriverWait(driver, seconds).until(
        lambda current: current.execute_script("return document.readyState") in {"interactive", "complete"},
    )


class BrowserDocumentChallenge(RuntimeError):
    """The browser is still on an observed challenge; no API request was sent."""


class BrowserLoginSession(requests.Session):
    """Keep a challenged login on one real browser transport until close()."""

    def __init__(self, *, recover_challenges: bool = True, **kwargs: object) -> None:
        self._recover_challenges = recover_challenges
        self._driver: WebDriver | None = None
        self._profile: tempfile.TemporaryDirectory[str] | None = None
        self._proxy_dir: str | None = None
        self._browser_proxy: str | None = None
        self._adopted_cleanup: contextlib.ExitStack | None = None
        super().__init__(**kwargs)

    def request(self, method: str, url: str, **kwargs: object) -> requests.Response:
        supported = (
            bool(_browser_origin(url)) and method.upper() in {"GET", "POST"}
            and kwargs.get("allow_redirects", True) is not False
            and not kwargs.get("stream") and not kwargs.get("files")
        )
        if self._driver is not None and supported:
            return self._native_request(method, url, kwargs)
        response = super().request(method, url, **kwargs)
        # An OTP response must not trigger a second submission on another transport.
        if method.upper() == "POST" and urllib.parse.urlsplit(url).path == "/api/accounts/email-otp/validate":
            return response
        if not self._recover_challenges or not supported or str(response.headers.get("cf-mitigated") or "").lower() != "challenge":
            return response
        try:
            return self._native_request(method, url, kwargs)
        except Exception as exc:
            print(f"[protocol-browser-session] recovery failed exception_type={type(exc).__name__}", flush=True)
            if self._driver is not None:
                self._capture_failure(self._driver, method, url, kwargs, {
                    "status": 0, "errorType": type(exc).__name__, "traceback": traceback.format_exc(),
                })
            self._close_browser()
            return response

    def _ensure_browser(self, proxy: str | None) -> WebDriver:
        if self._driver is not None:
            if proxy != self._browser_proxy:
                raise RuntimeError("browser_login_proxy_changed")
            return self._driver
        from protocol_runtime import protocol_register as runtime

        self._profile = tempfile.TemporaryDirectory(prefix="chatgpt-login-browser-")
        previous = os.environ.get("BROWSER_USER_DATA_DIR")
        try:
            os.environ["BROWSER_USER_DATA_DIR"] = self._profile.name
            self._driver, self._proxy_dir = runtime._load_protocol_browser_new_driver()(
                proxy, browser_backend="custom",
            )
        finally:
            if previous is None:
                os.environ.pop("BROWSER_USER_DATA_DIR", None)
            else:
                os.environ["BROWSER_USER_DATA_DIR"] = previous
        self._browser_proxy = proxy
        return self._driver

    def _seed_browser_cookies(self, driver: WebDriver) -> None:
        cookies = []
        for cookie in self.cookies.jar:
            if cookie.domain.lstrip(".") not in _COOKIE_DOMAINS or _edge_cookie(cookie.name):
                continue
            item = {
                "name": cookie.name, "value": cookie.value, "domain": cookie.domain,
                "path": cookie.path or "/", "secure": cookie.secure,
                "httpOnly": cookie.has_nonstandard_attr("HttpOnly"),
            }
            if cookie.expires is not None and cookie.expires > 0:
                item["expires"] = cookie.expires
            same_site = cookie.get_nonstandard_attr("SameSite")
            if same_site in {"Strict", "Lax", "None"}:
                item["sameSite"] = same_site
            cookies.append(item)
        if cookies:
            driver.execute_cdp_cmd("Network.setCookies", {"cookies": cookies})

    def adopt_browser(
        self, driver: WebDriver, *, proxy: str | None, proxy_dir: str | None,
        cleanup: contextlib.ExitStack,
    ) -> None:
        """Keep the browser that actually completed an external auth recovery."""
        self._sync_application_cookies(driver)
        self._close_browser()
        self._driver = driver
        self._proxy_dir = proxy_dir
        self._browser_proxy = proxy
        self._adopted_cleanup = cleanup.pop_all()

    def _sync_application_cookies(self, driver: WebDriver) -> None:
        snapshot = driver.execute_cdp_cmd("Network.getAllCookies", {})
        browser_cookies = snapshot.get("cookies")
        if not isinstance(browser_cookies, list):
            raise RuntimeError("browser_login_cookie_snapshot_missing")
        for cookie in list(self.cookies.jar):
            if cookie.domain.lstrip(".") in _COOKIE_DOMAINS:
                self.cookies.jar.clear(cookie.domain, cookie.path, cookie.name)
        for cookie in browser_cookies:
            if not isinstance(cookie, dict):
                continue
            name = str(cookie.get("name") or "")
            domain = str(cookie.get("domain") or "")
            if name and domain.lstrip(".") in _COOKIE_DOMAINS and not _edge_cookie(name):
                raw_expiry = cookie.get("expires")
                expiry = int(raw_expiry) if isinstance(raw_expiry, (int, float)) and raw_expiry > 0 else None
                attributes = {}
                if cookie.get("httpOnly"):
                    attributes["HttpOnly"] = None
                if cookie.get("sameSite") in {"Strict", "Lax", "None"}:
                    attributes["SameSite"] = cookie["sameSite"]
                self.cookies.jar.set_cookie(Cookie(
                    version=0, name=name, value=str(cookie.get("value") or ""),
                    port=None, port_specified=False, domain=domain,
                    domain_specified=domain.startswith("."), domain_initial_dot=domain.startswith("."),
                    path=str(cookie.get("path") or "/"), path_specified=True,
                    secure=bool(cookie.get("secure")), expires=expiry, discard=expiry is None,
                    comment=None, comment_url=None, rest=attributes, rfc2109=False,
                ))

    def _native_request(self, method: str, url: str, kwargs: Mapping[str, object]) -> requests.Response:
        proxy = kwargs.get("proxy")
        proxies = kwargs.get("proxies")
        if isinstance(proxies, Mapping):
            proxy = proxies.get("https") or proxies.get("all") or proxy
        if proxy is None:
            proxy = self._browser_proxy
        if proxy is not None and not isinstance(proxy, str):
            raise RuntimeError("browser_login_proxy_invalid")
        driver = self._ensure_browser(proxy)
        timeout = kwargs.get("timeout", 30)
        seconds = min(30.0, max(1.0, float(timeout))) if isinstance(timeout, (int, float)) else 30.0
        driver.set_page_load_timeout(seconds)
        driver.set_script_timeout(seconds)
        self._seed_browser_cookies(driver)

        headers = {key.lower(): value for key, value in self.headers.items()}
        provided_headers = kwargs.get("headers")
        if isinstance(provided_headers, Mapping):
            headers.update({str(key).lower(): str(value) for key, value in provided_headers.items()})
        referer = headers.get("referer")
        headers = {
            key.lower(): value for key, value in headers.items()
            if key.lower() not in {"cookie", "host", "content-length", "user-agent", "origin", "referer", "accept-encoding"}
            and not key.lower().startswith("sec-")
        }
        params = kwargs.get("params")
        if isinstance(params, Mapping):
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        method = method.upper()
        json_request = "application/json" in headers.get("accept", "").lower()
        if method == "GET" and not json_request:
            _navigate_html(driver, url, seconds)
            observed = {
                "status": driver.execute_script("return performance.getEntriesByType('navigation')[0]?.responseStatus || 0"),
                "url": driver.current_url, "body": driver.page_source, "headers": {},
            }
        else:
            origin = _browser_origin(url)
            if referer and _browser_origin(referer) != origin:
                raise RuntimeError("browser_login_origin_mismatch")
            current_url = str(driver.current_url or "")
            # Keep the verified document for same-origin API requests.
            if _browser_origin(current_url) != origin:
                landing = str(referer or origin + "/")
                if _browser_origin(landing) != origin:
                    raise RuntimeError("browser_login_origin_mismatch")
                _navigate_html(driver, landing, seconds)
            try:
                WebDriverWait(driver, seconds).until(
                    lambda current: current.execute_script("return document.readyState") == "complete"
                    and _browser_origin(str(current.current_url or "")) == origin
                    and current.execute_script("return performance.getEntriesByType('navigation')[0]?.responseStatus || 0") == 200
                    and not any(marker in str(current.title or "").lower() for marker in (
                        "just a moment", "verify you are human", "attention required", "cloudflare",
                    )),
                )
            except TimeoutException as exc:
                document_status = driver.execute_script(
                    "return performance.getEntriesByType('navigation')[0]?.responseStatus || 0",
                )
                self._capture_failure(driver, method, url, kwargs, {
                    "status": 0, "errorType": "BrowserDocumentNotReady", "requestSubmitted": False,
                    "documentStatus": document_status,
                })
                if document_status == 403 and any(marker in str(driver.page_source or "").lower() for marker in (
                    "cf-chl-", "_cf_chl_opt", "just a moment", "attention required! | cloudflare",
                )):
                    raise BrowserDocumentChallenge("browser_document_challenged") from exc
                raise
            body = kwargs.get("data")
            if "json" in kwargs:
                body = json.dumps(kwargs["json"])
                headers.setdefault("content-type", "application/json")
            elif isinstance(body, Mapping):
                body = urllib.parse.urlencode(body, doseq=True)
                headers.setdefault("content-type", "application/x-www-form-urlencoded")
            elif isinstance(body, bytes):
                body = body.decode("utf-8")
            if body is not None and not isinstance(body, str):
                raise RuntimeError("browser_login_body_not_supported")
            observed = driver.execute_async_script("""
                const args = arguments[0], done = arguments[arguments.length - 1];
                fetch(args.url, {method: args.method, credentials: 'include', redirect: 'error',
                    headers: args.headers, body: args.body, referrer: args.referrer || undefined,
                    signal: AbortSignal.timeout(args.timeoutMs)})
                .then(async response => done({status: response.status, url: response.url,
                    headers: Object.fromEntries(response.headers), body: await response.text()}))
                .catch(error => done({status: 0, errorType: error.name, errorMessage: String(error.message)}));
            """, {"url": url, "method": method, "headers": headers, "body": body, "referrer": referer,
                  "timeoutMs": max(1, int(seconds * 1000) - 500)})
        if (
            not isinstance(observed, dict) or not isinstance(observed.get("status"), int)
            or not 100 <= observed["status"] <= 599
            or not _browser_origin(str(observed.get("url") or ""))
            or not isinstance(observed.get("body"), str) or not isinstance(observed.get("headers"), dict)
        ):
            self._capture_failure(driver, method, url, kwargs, observed)
            raise RuntimeError("browser_login_response_not_observed")
        response = requests.Response()
        response.status_code = observed["status"]
        response.url = observed["url"]
        response.headers = requests.Headers(observed["headers"])
        response.content = observed["body"].encode("utf-8")
        self._sync_application_cookies(driver)
        if response.status_code >= 400:
            self._capture_failure(driver, method, url, kwargs, observed)
        user_agent = driver.execute_script("return navigator.userAgent")
        if isinstance(user_agent, str) and user_agent:
            self.headers["user-agent"] = user_agent
        target = urllib.parse.urlsplit(url)
        print(f"[protocol-browser-session] method={method} host={target.hostname} path={target.path} status={response.status_code}", flush=True)
        return response

    def _capture_failure(self, driver, method, url, kwargs, observed) -> None:
        root_value = os.environ.get("PROTOCOL_BROWSER_SESSION_DIAGNOSTICS_DIR", "").strip()
        if not root_value:
            return
        with contextlib.suppress(Exception):
            root = Path(root_value).resolve()
            if not root.is_relative_to(Path("/shared/register-output")):
                return
            folder = root / str(uuid.uuid4())
            folder.mkdir(mode=0o700, parents=True)
            fd = os.open(folder / "private-failure.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({
                    "method": method, "url": url, "request": dict(kwargs), "response": observed,
                    "documentUrl": driver.current_url, "documentHtml": driver.page_source,
                    "cookies": driver.execute_cdp_cmd("Network.getAllCookies", {}).get("cookies", []),
                }, stream)

    def _close_browser(self) -> None:
        if self._driver is not None:
            with contextlib.suppress(Exception):
                self._driver.quit()
            self._driver = None
        if self._profile is not None:
            with contextlib.suppress(Exception):
                self._profile.cleanup()
            self._profile = None
        if self._proxy_dir:
            path = Path(self._proxy_dir).resolve()
            root = Path(tempfile.gettempdir()).resolve()
            if path != root and path.is_relative_to(root):
                shutil.rmtree(path, ignore_errors=True)
            self._proxy_dir = None
        if self._adopted_cleanup is not None:
            with contextlib.suppress(Exception):
                self._adopted_cleanup.close()
            self._adopted_cleanup = None
        self._browser_proxy = None

    def close(self) -> None:
        self._close_browser()
        super().close()
