from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any


def _clean_optional(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _base_url() -> str | None:
    return _clean_optional(os.environ.get("CAPTCHA_SERVICE_BASE_URL"))


def _api_key() -> str | None:
    return _clean_optional(os.environ.get("CAPTCHA_SERVICE_API_KEY"))


def _client_key() -> str | None:
    return _clean_optional(os.environ.get("CAPTCHA_SERVICE_CLIENT_KEY"))


def _provider_kind(default: str = "turnstile-solver-camoufox") -> str:
    return _clean_optional(os.environ.get("DEFAULT_CAPTCHA_PROVIDER")) or default


def _poll_interval_seconds() -> float:
    try:
        return max(0.25, float(_clean_optional(os.environ.get("CAPTCHA_SERVICE_POLL_INTERVAL_SECONDS")) or "2.5"))
    except Exception:
        return 2.5


def _max_wait_seconds() -> int:
    try:
        return max(5, int(_clean_optional(os.environ.get("CAPTCHA_SERVICE_MAX_WAIT_SECONDS")) or "120"))
    except Exception:
        return 120


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    api_key = _api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    base_url = _base_url()
    if not base_url:
        raise RuntimeError("captcha service base url is not configured")
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def solve_turnstile_token(
    *,
    website_url: str,
    website_key: str,
    proxy: str | None = None,
    action: str | None = None,
    c_data: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    task: dict[str, Any] = {
        "type": "TurnstileTaskProxyless",
        "websiteURL": website_url,
        "websiteKey": website_key,
    }
    if action:
        task["action"] = action
    if c_data:
        task["cData"] = c_data
    if user_agent:
        task["userAgent"] = user_agent
    if proxy:
        task["proxy"] = proxy

    create_payload: dict[str, Any] = {"task": task, "provider": _provider_kind()}
    client_key = _client_key()
    if client_key:
        create_payload["clientKey"] = client_key

    created = _post_json("/createTask", create_payload)
    if int(created.get("errorId") or 0) != 0:
        raise RuntimeError(f"captcha createTask failed: {created}")
    task_id = created.get("taskId")
    if not task_id:
        raise RuntimeError(f"captcha createTask missing taskId: {created}")

    deadline = time.time() + _max_wait_seconds()
    while time.time() < deadline:
        time.sleep(_poll_interval_seconds())
        result_payload: dict[str, Any] = {"taskId": task_id}
        if client_key:
            result_payload["clientKey"] = client_key
        result = _post_json("/getTaskResult", result_payload)
        if int(result.get("errorId") or 0) != 0:
            raise RuntimeError(f"captcha getTaskResult failed: {result}")
        if str(result.get("status") or "").strip().lower() == "ready":
            solution = result.get("solution")
            if not isinstance(solution, dict):
                raise RuntimeError(f"captcha ready response missing solution: {result}")
            token = _clean_optional(
                str(
                    solution.get("token")
                    or solution.get("gRecaptchaResponse")
                    or solution.get("cf-turnstile-response")
                    or ""
                )
            )
            if not token:
                raise RuntimeError(f"captcha ready response missing token field: {result}")
            return {
                "taskId": task_id,
                "solution": solution,
                "token": token,
            }

    raise RuntimeError(f"captcha task timeout taskId={task_id}")


def solve_turnstile_vm_token(
    *,
    dx: str,
    proof_token: str,
) -> dict[str, Any]:
    create_payload: dict[str, Any] = {
        "task": {
            "type": "TurnstileVmTask",
            "dx": dx,
            "proofToken": proof_token,
        },
        "provider": _provider_kind("turnstile-solver-camoufox"),
    }
    client_key = _client_key()
    if client_key:
        create_payload["clientKey"] = client_key

    created = _post_json("/createTask", create_payload)
    if int(created.get("errorId") or 0) != 0:
        raise RuntimeError(f"captcha createTask failed: {created}")
    task_id = created.get("taskId")
    if not task_id:
        raise RuntimeError(f"captcha createTask missing taskId: {created}")

    deadline = time.time() + _max_wait_seconds()
    while time.time() < deadline:
        time.sleep(_poll_interval_seconds())
        result_payload: dict[str, Any] = {"taskId": task_id}
        if client_key:
            result_payload["clientKey"] = client_key
        result = _post_json("/getTaskResult", result_payload)
        if int(result.get("errorId") or 0) != 0:
            raise RuntimeError(f"captcha getTaskResult failed: {result}")
        if str(result.get("status") or "").strip().lower() == "ready":
            solution = result.get("solution")
            if not isinstance(solution, dict):
                raise RuntimeError(f"captcha ready response missing solution: {result}")
            token = _clean_optional(
                str(
                    solution.get("token")
                    or solution.get("cf-turnstile-response")
                    or solution.get("gRecaptchaResponse")
                    or ""
                )
            )
            if not token:
                raise RuntimeError(f"captcha ready response missing token field: {result}")
            return {
                "taskId": task_id,
                "solution": solution,
                "token": token,
            }

    raise RuntimeError(f"captcha vm task timeout taskId={task_id}")


def solve_cloudflare_clearance(
    *,
    website_url: str,
    proxy: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    task: dict[str, Any] = {
        "type": "CloudflareClearanceTask",
        "websiteURL": website_url,
    }
    if proxy:
        task["proxy"] = proxy
    if user_agent:
        task["userAgent"] = user_agent

    create_payload: dict[str, Any] = {"task": task, "provider": _provider_kind("turnstile-solver-camoufox")}
    client_key = _client_key()
    if client_key:
        create_payload["clientKey"] = client_key

    created = _post_json("/createTask", create_payload)
    if int(created.get("errorId") or 0) != 0:
        raise RuntimeError(f"captcha createTask failed: {created}")
    task_id = created.get("taskId")
    if not task_id:
        raise RuntimeError(f"captcha createTask missing taskId: {created}")

    deadline = time.time() + _max_wait_seconds()
    while time.time() < deadline:
        time.sleep(_poll_interval_seconds())
        result_payload: dict[str, Any] = {"taskId": task_id}
        if client_key:
            result_payload["clientKey"] = client_key
        result = _post_json("/getTaskResult", result_payload)
        if int(result.get("errorId") or 0) != 0:
            raise RuntimeError(f"captcha getTaskResult failed: {result}")
        if str(result.get("status") or "").strip().lower() == "ready":
            solution = result.get("solution")
            if not isinstance(solution, dict):
                raise RuntimeError(f"captcha ready response missing solution: {result}")
            token = _clean_optional(
                str(
                    solution.get("cf_clearance")
                    or solution.get("token")
                    or ""
                )
            )
            if not token:
                raise RuntimeError(f"captcha ready response missing clearance token: {result}")
            return {
                "taskId": task_id,
                "solution": solution,
                "token": token,
                "cf_clearance": token,
                "cookies": solution.get("cookies") if isinstance(solution.get("cookies"), list) else [],
            }

    raise RuntimeError(f"captcha clearance task timeout taskId={task_id}")


def solve_browser_sentinel_token(
    *,
    flow: str,
    website_url: str,
    proxy: str | None = None,
    user_agent: str | None = None,
    cookies: list[dict[str, Any]] | None = None,
    frame_url: str | None = None,
) -> dict[str, Any]:
    task: dict[str, Any] = {
        "type": "BrowserSentinelTokenTask",
        "flow": flow,
        "websiteURL": website_url,
    }
    if proxy:
        task["proxy"] = proxy
    if user_agent:
        task["userAgent"] = user_agent
    if isinstance(cookies, list) and cookies:
        task["cookies"] = cookies
    if frame_url:
        task["frameURL"] = frame_url

    create_payload: dict[str, Any] = {"task": task, "provider": _provider_kind("turnstile-solver-camoufox")}
    client_key = _client_key()
    if client_key:
        create_payload["clientKey"] = client_key

    created = _post_json("/createTask", create_payload)
    if int(created.get("errorId") or 0) != 0:
        raise RuntimeError(f"captcha createTask failed: {created}")
    task_id = created.get("taskId")
    if not task_id:
        raise RuntimeError(f"captcha createTask missing taskId: {created}")

    deadline = time.time() + _max_wait_seconds()
    while time.time() < deadline:
        time.sleep(_poll_interval_seconds())
        result_payload: dict[str, Any] = {"taskId": task_id}
        if client_key:
            result_payload["clientKey"] = client_key
        result = _post_json("/getTaskResult", result_payload)
        if int(result.get("errorId") or 0) != 0:
            raise RuntimeError(f"captcha getTaskResult failed: {result}")
        if str(result.get("status") or "").strip().lower() == "ready":
            solution = result.get("solution")
            if not isinstance(solution, dict):
                raise RuntimeError(f"captcha ready response missing solution: {result}")
            token_payload = solution.get("tokenPayload")
            if not isinstance(token_payload, dict):
                raise RuntimeError(f"captcha ready response missing tokenPayload: {result}")
            token_p = _clean_optional(str(token_payload.get("p") or ""))
            token_t = _clean_optional(str(token_payload.get("t") or ""))
            if not token_p or not token_t:
                raise RuntimeError(f"captcha ready response missing sentinel p/t: {result}")
            return {
                "taskId": task_id,
                "solution": solution,
                "tokenPayload": token_payload,
                "token": solution.get("token"),
                "cookies": solution.get("cookies") if isinstance(solution.get("cookies"), list) else [],
                "passkeyCapabilities": solution.get("passkeyCapabilities") if isinstance(solution.get("passkeyCapabilities"), dict) else None,
                "deviceId": _clean_optional(str(solution.get("deviceId") or "")),
                "userAgent": _clean_optional(str(solution.get("userAgent") or "")),
                "currentUrl": _clean_optional(str(solution.get("currentUrl") or "")),
                "sessionObserverToken": solution.get("sessionObserverToken") if isinstance(solution.get("sessionObserverToken"), dict) else None,
            }

    raise RuntimeError(f"captcha browser sentinel task timeout taskId={task_id}")


def solve_browser_auth_bootstrap(
    *,
    website_url: str,
    proxy: str | None = None,
    user_agent: str | None = None,
    cookies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    task: dict[str, Any] = {
        "type": "BrowserAuthBootstrapTask",
        "websiteURL": website_url,
    }
    if proxy:
        task["proxy"] = proxy
    if user_agent:
        task["userAgent"] = user_agent
    if isinstance(cookies, list) and cookies:
        task["cookies"] = cookies

    create_payload: dict[str, Any] = {"task": task, "provider": _provider_kind("turnstile-solver-camoufox")}
    client_key = _client_key()
    if client_key:
        create_payload["clientKey"] = client_key

    created = _post_json("/createTask", create_payload)
    if int(created.get("errorId") or 0) != 0:
        raise RuntimeError(f"captcha createTask failed: {created}")
    task_id = created.get("taskId")
    if not task_id:
        raise RuntimeError(f"captcha createTask missing taskId: {created}")

    deadline = time.time() + _max_wait_seconds()
    while time.time() < deadline:
        time.sleep(_poll_interval_seconds())
        result_payload: dict[str, Any] = {"taskId": task_id}
        if client_key:
            result_payload["clientKey"] = client_key
        result = _post_json("/getTaskResult", result_payload)
        if int(result.get("errorId") or 0) != 0:
            raise RuntimeError(f"captcha getTaskResult failed: {result}")
        if str(result.get("status") or "").strip().lower() == "ready":
            solution = result.get("solution")
            if not isinstance(solution, dict):
                raise RuntimeError(f"captcha ready response missing solution: {result}")
            auth_url = _clean_optional(str(solution.get("authUrl") or ""))
            auth_state = _clean_optional(str(solution.get("authState") or ""))
            if not auth_url or not auth_state:
                raise RuntimeError(f"captcha ready response missing auth bootstrap fields: {result}")
            return {
                "taskId": task_id,
                "solution": solution,
                "authUrl": auth_url,
                "authState": auth_state,
                "cookies": solution.get("cookies") if isinstance(solution.get("cookies"), list) else [],
                "deviceId": _clean_optional(str(solution.get("deviceId") or "")),
                "userAgent": _clean_optional(str(solution.get("userAgent") or "")),
                "currentUrl": _clean_optional(str(solution.get("currentUrl") or "")),
            }

    raise RuntimeError(f"captcha browser auth bootstrap task timeout taskId={task_id}")
