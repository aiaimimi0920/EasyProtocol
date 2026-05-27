from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


SRC_ROOT = Path(__file__).resolve().parents[1] / "providers" / "python" / "src"
PYTHON_SHARED_SRC_ROOT = (
    Path(__file__).resolve().parents[1] / "providers" / "python" / "python_shared" / "src"
)
for candidate in (SRC_ROOT, PYTHON_SHARED_SRC_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from shared_proxy import easy_proxy_client  # noqa: E402


class EasyProxyClientTests(unittest.TestCase):
    def test_list_available_nodes_falls_back_to_unfiltered_payload(self) -> None:
        fallback_payload = {
            "available_nodes": 2,
            "nodes": [
                {"tag": "node-down", "available": False, "availability_score": 90},
                {"tag": "node-up-low", "available": True, "availability_score": 10},
                {"tag": "node-up-high", "effective_available": True, "availability_score": 80},
            ],
        }

        with mock.patch.object(
            easy_proxy_client,
            "_api_request",
            side_effect=[
                RuntimeError("INITIAL_PROXY_PROBE_PENDING"),
                fallback_payload,
            ],
        ) as api_request:
            nodes = easy_proxy_client.list_available_nodes(
                base_url="http://easy-proxy-monorepo-service:29888",
                only_available=True,
                prefer_available=True,
            )

        self.assertEqual(["node-up-high", "node-up-low"], [node["tag"] for node in nodes])
        self.assertEqual(2, api_request.call_count)
        self.assertEqual(
            mock.call(
                "GET",
                "/api/nodes?only_available=1&prefer_available=1",
                base_url="http://easy-proxy-monorepo-service:29888",
                api_key="",
            ),
            api_request.call_args_list[0],
        )
        self.assertEqual(
            mock.call(
                "GET",
                "/api/nodes",
                base_url="http://easy-proxy-monorepo-service:29888",
                api_key="",
                wait_for_ready=False,
            ),
            api_request.call_args_list[1],
        )

    def test_wait_ready_accepts_unfiltered_available_nodes(self) -> None:
        with mock.patch.object(easy_proxy_client, "_build_management_opener", return_value=object()):
            with mock.patch.object(
                easy_proxy_client,
                "_read_json_response",
                side_effect=[
                    RuntimeError("INITIAL_PROXY_PROBE_PENDING"),
                    {
                        "available_nodes": 0,
                        "nodes": [{"tag": "node-up", "available": True}],
                    },
                ],
            ):
                with mock.patch("shared_proxy.easy_proxy_client.time.sleep", return_value=None):
                    easy_proxy_client._wait_easy_proxy_ready("http://easy-proxy-monorepo-service:29888")


if __name__ == "__main__":
    unittest.main()
