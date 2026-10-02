from __future__ import annotations

import json
import os
import queue
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1] / "providers" / "python"
for path in (ROOT / "src", ROOT / "python_shared" / "src"):
    sys.path.insert(0, str(path))

import worker_pool
from new_protocol_register import protocol_small_success as flow
from protocol_runtime.errors import ProtocolRuntimeError


class ProtocolErrorBoundaryTests(unittest.TestCase):
    def response(self, status: int, *, challenge: bool = False) -> SimpleNamespace:
        return SimpleNamespace(
            status_code=status,
            headers={"cf-mitigated": "challenge"} if challenge else {},
            text="<html><style>fixture</style>private response content</html>",
        )

    def test_strict_platform_login_rejects_challenge(self) -> None:
        session = SimpleNamespace(headers={})
        with mock.patch.object(
            flow, "_session_request", return_value=self.response(403, challenge=True)
        ) as request, mock.patch.object(
            flow, "_prime_openai_login_session_with_browser"
        ) as recovery:
            with self.assertRaisesRegex(ProtocolRuntimeError, "browser_verification_required") as caught:
                flow._open_platform_login(session=session, explicit_proxy=None)
        self.assertEqual("stage_platform_login", caught.exception.stage)
        self.assertEqual("platform_login", caught.exception.detail)
        self.assertEqual("blocked", caught.exception.category)
        self.assertNotIn("private response content", str(caught.exception))
        self.assertEqual(1, request.call_count)
        recovery.assert_not_called()

    def test_landing_challenge_allows_authoritative_oauth_without_faking_success(self) -> None:
        context = object()
        with mock.patch.object(
            flow, "_session_request", return_value=self.response(403, challenge=True)
        ) as request, mock.patch.object(flow, "_prime_openai_login_session_with_browser") as recovery:
            returned_context, response = flow._open_platform_login_with_browser_retry(
                session=SimpleNamespace(headers={}), sentinel_context=context, explicit_proxy=None,
            )
        self.assertIs(context, returned_context)
        self.assertIsNone(response)
        request.assert_called_once()
        recovery.assert_not_called()

    def test_landing_challenge_does_not_relax_authoritative_oauth_boundary(self) -> None:
        session = SimpleNamespace(headers={})
        with mock.patch.object(
            flow, "_session_request", return_value=self.response(403, challenge=True)
        ) as request, mock.patch.object(flow, "_recover_platform_auth0_authorize_in_browser") as recovery:
            flow._open_platform_login_with_browser_retry(
                session=session, sentinel_context=None, explicit_proxy=None,
            )
            with self.assertRaisesRegex(ProtocolRuntimeError, "browser_verification_required") as caught:
                flow._platform_auth0_authorize_with_browser_retry(
                    session=session, auth_url="https://auth.example.test/authorize",
                    device_id="fixture", sentinel_context=None, explicit_proxy=None,
                    request_label="fixture-authorize",
                )
        self.assertEqual("stage_auth_continue", caught.exception.stage)
        self.assertEqual(2, request.call_count)
        recovery.assert_not_called()

    def test_challenge_header_cannot_be_accepted_as_http_success(self) -> None:
        with self.assertRaisesRegex(ProtocolRuntimeError, "browser_verification_required"):
            flow._raise_if_unexpected_http(
                self.response(200, challenge=True), expected_statuses={200},
                stage="stage_platform_login", detail="platform_login",
            )

    def test_authorize_challenge_does_not_start_browser_or_new_network_attempt(self) -> None:
        with mock.patch.object(
            flow, "_session_request", return_value=self.response(403, challenge=True)
        ), mock.patch.object(flow, "_recover_platform_auth0_authorize_in_browser") as recovery:
            with self.assertRaisesRegex(ProtocolRuntimeError, "browser_verification_required") as caught:
                flow._platform_auth0_authorize_with_browser_retry(
                    session=SimpleNamespace(), auth_url="https://auth.example.test/authorize",
                    device_id="fixture", sentinel_context=None, explicit_proxy=None,
                    request_label="fixture-authorize",
                )
        recovery.assert_not_called()
        self.assertFalse(flow._should_retry_after_authorize_error(
            caught.exception, time_remaining_seconds=300,
        ))

    def test_authorize_continue_challenge_does_not_start_browser_recovery(self) -> None:
        with mock.patch.object(
            flow, "_submit_authorize_continue_login_or_signup",
            return_value=self.response(403, challenge=True),
        ), mock.patch.object(flow, "_prime_openai_login_session_with_browser") as recovery:
            with self.assertRaisesRegex(ProtocolRuntimeError, "browser_verification_required"):
                flow._submit_authorize_continue_with_browser_retry(
                    session=SimpleNamespace(), email="fixture@example.test", device_id="fixture",
                    sentinel_context=None, explicit_proxy=None, use_create_account_referer=True,
                )
        recovery.assert_not_called()

    def test_authorize_challenge_is_not_masked_as_missing_cookie(self) -> None:
        with mock.patch.object(flow, "_login_session_cookie") as cookie:
            with self.assertRaisesRegex(ProtocolRuntimeError, "browser_verification_required"):
                flow._validate_platform_authorize_response(
                    session=SimpleNamespace(), response=self.response(403, challenge=True)
                )
        cookie.assert_not_called()

    def test_authorize_http_failure_is_not_masked_as_missing_cookie(self) -> None:
        with mock.patch.object(flow, "_login_session_cookie") as cookie:
            with self.assertRaisesRegex(ProtocolRuntimeError, "oauth_authorize status=400"):
                flow._validate_platform_authorize_response(
                    session=SimpleNamespace(), response=self.response(400)
                )
        cookie.assert_not_called()

    def test_successful_authorize_response_still_requires_login_cookie(self) -> None:
        with mock.patch.object(flow, "_login_session_cookie", return_value=""):
            with self.assertRaisesRegex(ProtocolRuntimeError, "authorize_init_missing_login_session"):
                flow._validate_platform_authorize_response(
                    session=SimpleNamespace(), response=self.response(200)
                )
        with mock.patch.object(flow, "_login_session_cookie", return_value="fixture-cookie"):
            flow._validate_platform_authorize_response(
                session=SimpleNamespace(), response=self.response(200)
            )

    def worker_error(self, error: RuntimeError) -> dict:
        tasks, results = queue.Queue(), queue.Queue()
        tasks.put({"task_id": "fixture", "step_type": "create_openai_account", "step_input": {}})
        tasks.put(None)
        with mock.patch.dict(os.environ), mock.patch.object(worker_pool, "_dispatch_step", side_effect=error):
            worker_pool._worker_process_main("fixture-worker", tasks, results)
        return json.loads(json.dumps(results.get_nowait()))["error"]

    def test_worker_queue_preserves_protocol_stage_and_detail(self) -> None:
        error = ProtocolRuntimeError(
            "browser_verification_required", stage="stage_platform_login",
            detail="platform_login", category="blocked",
        )
        result = self.worker_error(error)
        self.assertEqual("operation_error", result["category"])
        self.assertEqual(error.to_response_payload(), result["details"]["protocol_error"])
        self.assertEqual("create_openai_account", result["details"]["step_type"])

    def test_unstructured_worker_failure_keeps_its_original_category(self) -> None:
        result = self.worker_error(RuntimeError("fixture failure"))
        self.assertEqual("operation_error", result["category"])
        self.assertEqual("fixture failure", result["message"])
        self.assertNotIn("protocol_error", result["details"])


if __name__ == "__main__":
    unittest.main()
