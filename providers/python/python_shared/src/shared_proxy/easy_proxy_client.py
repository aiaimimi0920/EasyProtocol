"""Lightweight client for EasyProxy management API (lease-based proxy rotation)."""
from __future__ import annotations

import json
import os
import secrets
import ipaddress
import time
import urllib.parse
import urllib.request
from typing import Any


EASY_PROXY_BASE_URL = (
    os.environ.get("EASY_PROXY_BASE_URL") or "http://127.0.0.1:29888"
).strip()
EASY_PROXY_API_KEY = (os.environ.get("EASY_PROXY_API_KEY") or "").strip()
EASY_PROXY_HOST_ID = (
    os.environ.get("EASY_PROXY_HOST_ID") or "python-protocol-buy-service"
).strip()
EASY_PROXY_TTL_MINUTES = int(os.environ.get("EASY_PROXY_TTL_MINUTES") or "30")
DEFAULT_EASY_PROXY_READY_TIMEOUT_SECONDS = 90
DEFAULT_EASY_PROXY_READY_PROBE_INTERVAL_SECONDS = 2


def _api_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    base_url: str = "",
    api_key: str = "",
    wait_for_ready: bool = True,
) -> dict[str, Any]:
    effective_base = (base_url or EASY_PROXY_BASE_URL).rstrip("/")
    if wait_for_ready and _should_wait_for_easy_proxy(path):
        _wait_easy_proxy_ready(effective_base, api_key=api_key)
    url = f"{effective_base}{path}"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    effective_key = (api_key or EASY_PROXY_API_KEY).strip()
    if effective_key:
        headers["Authorization"] = f"Bearer {effective_key}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = _build_management_opener(effective_base)
    try:
        return _read_json_response(opener, req)
    except urllib.error.HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        raise RuntimeError(
            f"EasyProxy API {method} {path} returned {exc.code}: {error_body}"
        ) from exc


def _read_json_response(opener: urllib.request.OpenerDirector, req: urllib.request.Request) -> dict[str, Any]:
    with opener.open(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _should_wait_for_easy_proxy(path: str) -> bool:
    normalized = str(path or "").strip().lower()
    return normalized.startswith("/api/nodes") or normalized.startswith("/proxy/leases/checkout")


def _resolve_easy_proxy_ready_timeout_seconds() -> int:
    raw = str(os.environ.get("EASY_PROXY_READY_TIMEOUT_SECONDS") or "").strip()
    try:
        return max(1, int(raw or DEFAULT_EASY_PROXY_READY_TIMEOUT_SECONDS))
    except Exception:
        return DEFAULT_EASY_PROXY_READY_TIMEOUT_SECONDS


def _resolve_easy_proxy_ready_probe_interval_seconds() -> int:
    raw = str(os.environ.get("EASY_PROXY_READY_PROBE_INTERVAL_SECONDS") or "").strip()
    try:
        return max(1, int(raw or DEFAULT_EASY_PROXY_READY_PROBE_INTERVAL_SECONDS))
    except Exception:
        return DEFAULT_EASY_PROXY_READY_PROBE_INTERVAL_SECONDS


def _wait_easy_proxy_ready(base_url: str, *, api_key: str = "") -> None:
    deadline = time.time() + _resolve_easy_proxy_ready_timeout_seconds()
    interval = _resolve_easy_proxy_ready_probe_interval_seconds()
    last_error: Exception | None = None
    opener = _build_management_opener(base_url)
    effective_key = (api_key or EASY_PROXY_API_KEY).strip()
    headers: dict[str, str] = {}
    if effective_key:
        headers["Authorization"] = f"Bearer {effective_key}"

    filtered_probe_url = f"{base_url.rstrip('/')}/api/nodes?only_available=1&prefer_available=1"
    fallback_probe_url = f"{base_url.rstrip('/')}/api/nodes"
    while time.time() < deadline:
        for probe_url, allow_local_filter in (
            (filtered_probe_url, False),
            (fallback_probe_url, True),
        ):
            try:
                req = urllib.request.Request(probe_url, headers=headers, method="GET")
                payload = _read_json_response(opener, req)
                available_nodes = int(payload.get("available_nodes") or 0)
                if allow_local_filter and available_nodes <= 0:
                    available_nodes = len(
                        _normalize_node_list(
                            payload,
                            only_available=True,
                            prefer_available=True,
                        )
                    )
                if available_nodes > 0:
                    return
                last_error = RuntimeError(
                    f"EasyProxy not ready: available_nodes={available_nodes}"
                )
                if allow_local_filter:
                    break
            except Exception as exc:
                last_error = exc
                if allow_local_filter:
                    break
        time.sleep(interval)
    raise RuntimeError(f"EasyProxy not ready after wait: {last_error}") from last_error


def _build_management_opener(base_url: str) -> urllib.request.OpenerDirector:
    parsed = urllib.parse.urlsplit(str(base_url or "").strip())
    host = str(parsed.hostname or "").strip()
    should_bypass_proxy = host in (
        "127.0.0.1",
        "localhost",
        "::1",
        "0.0.0.0",
        "easy-proxy",
        "easy-proxy-service",
    )
    if not should_bypass_proxy and host:
        try:
            ip = ipaddress.ip_address(host)
            should_bypass_proxy = bool(ip.is_loopback or ip.is_private or ip.is_link_local)
        except ValueError:
            should_bypass_proxy = False
    if should_bypass_proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _node_marked_available(node: dict[str, Any]) -> bool:
    return bool(node.get("effective_available") or node.get("available"))


def _node_sort_key(node: dict[str, Any]) -> tuple[int, int]:
    try:
        score = int(node.get("availability_score") or 0)
    except Exception:
        score = 0
    return (0 if _node_marked_available(node) else 1, -score)


def _normalize_node_list(
    payload: dict[str, Any],
    *,
    only_available: bool,
    prefer_available: bool,
) -> list[dict[str, Any]]:
    nodes = payload.get("nodes") or []
    normalized = [node for node in nodes if isinstance(node, dict)]
    if only_available:
        normalized = [node for node in normalized if _node_marked_available(node)]
    if prefer_available:
        normalized.sort(key=_node_sort_key)
    return normalized


def checkout_proxy(
    *,
    host_id: str = "",
    ttl_minutes: int = 0,
    base_url: str = "",
    api_key: str = "",
    metadata: dict[str, str] | None = None,
    require_dedicated_node: bool = False,
) -> dict[str, Any]:
    body = {
        "hostId": host_id or EASY_PROXY_HOST_ID,
        "providerTypeKey": "easy-proxies",
        "provisionMode": "reuse-only",
        "bindingMode": "shared-instance",
        "protocol": "http",
        "ttlMinutes": ttl_minutes or EASY_PROXY_TTL_MINUTES,
        "metadata": metadata or {"source": "python-protocol-buy-service"},
    }
    result = _api_request("POST", "/proxy/leases/checkout", body, base_url=base_url, api_key=api_key)
    lease = (result.get("result") or {}).get("lease") or {}
    _validate_checkout_lease(
        lease,
        result=result,
        require_dedicated_node=require_dedicated_node,
    )
    return lease


def get_settings(
    *,
    base_url: str = "",
    api_key: str = "",
) -> dict[str, Any]:
    return _api_request("GET", "/api/settings", base_url=base_url, api_key=api_key)


def list_available_nodes(
    *,
    base_url: str = "",
    api_key: str = "",
    only_available: bool = True,
    prefer_available: bool = True,
) -> list[dict[str, Any]]:
    query = []
    if only_available:
        query.append("only_available=1")
    if prefer_available:
        query.append("prefer_available=1")
    suffix = f"?{'&'.join(query)}" if query else ""
    path = f"/api/nodes{suffix}"
    fallback_used = False
    try:
        payload = _api_request("GET", path, base_url=base_url, api_key=api_key)
    except Exception:
        if not suffix:
            raise
        payload = _api_request(
            "GET",
            "/api/nodes",
            base_url=base_url,
            api_key=api_key,
            wait_for_ready=False,
        )
        fallback_used = True
    if fallback_used:
        return _normalize_node_list(
            payload,
            only_available=only_available,
            prefer_available=prefer_available,
        )
    nodes = payload.get("nodes") or []
    if not isinstance(nodes, list):
        return []
    return [node for node in nodes if isinstance(node, dict)]


def checkout_random_node_proxy(
    *,
    base_url: str = "",
    api_key: str = "",
    runtime_host: str = "",
    excluded_proxy_urls: set[str] | None = None,
) -> dict[str, Any]:
    settings = get_settings(base_url=base_url, api_key=api_key)
    nodes = list_available_nodes(base_url=base_url, api_key=api_key, only_available=True, prefer_available=True)
    if not nodes:
        raise RuntimeError("EasyProxy random node checkout found no available nodes")

    rng = secrets.SystemRandom()
    candidates = list(nodes)
    rng.shuffle(candidates)
    excluded = {str(item).strip().lower() for item in (excluded_proxy_urls or set()) if str(item).strip()}

    protocol = str(
        settings.get("multi_port_protocol")
        or settings.get("listener_protocol")
        or "http"
    ).strip() or "http"
    username = str(
        settings.get("multi_port_username")
        or settings.get("listener_username")
        or ""
    ).strip()
    password = str(
        settings.get("multi_port_password")
        or settings.get("listener_password")
        or ""
    ).strip()
    host = _resolve_runtime_host(base_url=base_url, runtime_host=runtime_host)

    for node in candidates:
        try:
            port = int(node.get("port") or 0)
        except Exception:
            port = 0
        if port <= 0:
            continue
        proxy_url = _build_proxy_url(
            protocol=protocol,
            host=host,
            port=port,
            username=username,
            password=password,
        )
        if proxy_url.lower() in excluded:
            continue
        return {
            "id": "",
            "proxyUrl": proxy_url,
            "host": host,
            "port": port,
            "protocol": protocol,
            "username": username,
            "password": password,
            "metadata": {
                "selectedNodeTag": str(node.get("tag") or "").strip(),
                "selectedNodeName": str(node.get("name") or "").strip(),
                "selectedNodePort": str(port),
                "selectedNodeMode": "dedicated-node",
                "selectedNodeAvailability": str(bool(node.get("available"))).lower(),
                "selectedNodeAvailabilityScore": str(node.get("availability_score") or ""),
                "selectedNodeRegion": str(node.get("region") or "").strip(),
                "selectedNodeCountry": str(node.get("country") or "").strip(),
                "selectedNodeProtocolFamily": str(node.get("protocol_family") or "").strip(),
                "selectedNodeDomainFamily": str(node.get("domain_family") or "").strip(),
                "selectedNodeSourceRef": str(node.get("source_ref") or "").strip(),
                "selectedNodeSelectionTier": "random-node",
            },
        }

    raise RuntimeError("EasyProxy random node checkout exhausted available nodes")


def release_lease(
    lease_id: str,
    *,
    base_url: str = "",
    api_key: str = "",
) -> None:
    if not lease_id:
        return
    try:
        _api_request("POST", f"/proxy/leases/{lease_id}/release", {}, base_url=base_url, api_key=api_key)
    except Exception:
        pass


def report_usage(
    lease_id: str,
    *,
    success: bool,
    latency_ms: int = 0,
    error_code: str = "",
    service_key: str = "",
    stage: str = "",
    failure_class: str = "",
    route_confidence: str = "",
    base_url: str = "",
    api_key: str = "",
) -> None:
    if not lease_id:
        return
    try:
        _api_request(
            "POST",
            "/proxy/leases/report",
            {
                "leaseId": lease_id,
                "success": success,
                "latencyMs": latency_ms,
                "errorCode": error_code,
                "serviceKey": service_key,
                "stage": stage,
                "failureClass": failure_class,
                "routeConfidence": route_confidence,
            },
            base_url=base_url,
            api_key=api_key,
        )
    except Exception:
        pass


def _resolve_runtime_host(*, base_url: str, runtime_host: str) -> str:
    value = str(runtime_host or "").strip()
    if value:
        return value
    parsed = urllib.parse.urlsplit((base_url or EASY_PROXY_BASE_URL).strip())
    host = str(parsed.hostname or "127.0.0.1").strip()
    if host in ("", "0.0.0.0", "::", "[::]", "localhost"):
        return "127.0.0.1"
    return host


def _build_proxy_url(
    *,
    protocol: str,
    host: str,
    port: int,
    username: str,
    password: str,
) -> str:
    scheme = str(protocol or "http").strip() or "http"
    if username:
        quoted_user = urllib.parse.quote(username, safe="")
        quoted_password = urllib.parse.quote(password, safe="")
        return f"{scheme}://{quoted_user}:{quoted_password}@{host}:{port}"
    return f"{scheme}://{host}:{port}"


def _coerce_port(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _validate_checkout_lease(
    lease: dict[str, Any],
    *,
    result: dict[str, Any],
    require_dedicated_node: bool,
) -> None:
    if not lease.get("proxyUrl"):
        raise RuntimeError(f"EasyProxy checkout returned no proxyUrl: {result}")
    if not require_dedicated_node:
        return

    metadata = lease.get("metadata") or {}
    selected_mode = str(metadata.get("selectedNodeMode") or "").strip().lower()
    selected_port = _coerce_port(metadata.get("selectedNodePort") or lease.get("port"))
    lease_port = _coerce_port(lease.get("port"))
    if selected_mode and selected_mode != "dedicated-node":
        raise RuntimeError(
            f"EasyProxy checkout returned non-dedicated route: {selected_mode or 'unknown'}"
        )
    if selected_port <= 0 or lease_port <= 0:
        raise RuntimeError(
            f"EasyProxy checkout returned invalid dedicated port selected={selected_port} lease={lease_port}"
        )
    if selected_port == 2323 or lease_port == 2323:
        raise RuntimeError(
            f"EasyProxy checkout returned shared listener port selected={selected_port} lease={lease_port}"
        )
