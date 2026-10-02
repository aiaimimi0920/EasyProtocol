from __future__ import annotations

import contextlib
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1] / "providers" / "python"
for path in (ROOT / "src", ROOT / "python_shared" / "src"):
    sys.path.insert(0, str(path))

from new_protocol_register import protocol_chatgpt_login as login
from new_protocol_register import protocol_small_success as signup
from protocol_runtime import protocol_register as runtime
from protocol_runtime.attempt_limits import RETRY_MAX_ATTEMPTS_ENV, bounded_attempts


class ProtocolAttemptLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = mock.patch.dict(os.environ, {RETRY_MAX_ATTEMPTS_ENV: "1"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_unset_preserves_each_callers_default(self) -> None:
        for value in ("", " "):
            with mock.patch.dict(os.environ, {RETRY_MAX_ATTEMPTS_ENV: value}):
                self.assertEqual(3, bounded_attempts(3))
                self.assertEqual(1, bounded_attempts(1))

    def test_limit_can_only_tighten_not_expand(self) -> None:
        for configured, expected in (("1", 1), (" 2 ", 2), ("30", 3)):
            with mock.patch.dict(os.environ, {RETRY_MAX_ATTEMPTS_ENV: configured}):
                self.assertEqual(expected, bounded_attempts(3))
                self.assertEqual(1, bounded_attempts(1))

    def test_invalid_explicit_limit_fails_closed(self) -> None:
        for value in ("0", "-1", "1.0", "true", "invalid", "１"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {RETRY_MAX_ATTEMPTS_ENV: value}):
                with self.assertRaisesRegex(ValueError, "^invalid_protocol_retry_max_attempts$"):
                    bounded_attempts(2)

    def test_signup_invalid_limit_precedes_mailbox_acquisition(self) -> None:
        with mock.patch.dict(os.environ, {RETRY_MAX_ATTEMPTS_ENV: "invalid"}), mock.patch.object(
            signup, "resolve_mailbox", side_effect=RuntimeError("fixture_mailbox_acquisition"),
        ) as mailbox:
            with self.assertRaisesRegex(ValueError, "invalid_protocol_retry_max_attempts"):
                signup.run_protocol_small_success_once()
        mailbox.assert_not_called()

    def test_signup_one_network_session_even_without_explicit_proxy(self) -> None:
        mailbox = SimpleNamespace(provider="fixture", email="fixture@example.test", session_id="fixture")
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(signup, "resolve_mailbox", return_value=mailbox))
            stack.enter_context(mock.patch.object(signup, "release_mailbox"))
            stack.enter_context(mock.patch.object(signup, "flow_network_env", side_effect=contextlib.nullcontext))
            stack.enter_context(mock.patch.object(signup, "_protocol_only_env", side_effect=contextlib.nullcontext))
            stack.enter_context(mock.patch.object(signup, "lease_flow_proxy", side_effect=lambda **kw: contextlib.nullcontext(SimpleNamespace(proxy_url=None))))
            stack.enter_context(mock.patch.dict(os.environ, {"PROTOCOL_ENABLE_BROWSER_SIGNUP_SESSION": "0"}))
            session = stack.enter_context(mock.patch.object(signup.requests, "Session", side_effect=RuntimeError("curl: (28) fixture timeout")))
            with self.assertRaises(Exception):
                signup.run_protocol_small_success_once()
        self.assertEqual(1, session.call_count)

    def test_one_sentinel_candidate_without_persona_or_email_variations(self) -> None:
        with mock.patch.object(signup, "_get_sentinel_header_for_signup", return_value='{"t":"fixture"}') as token, mock.patch.object(
            signup, "_new_protocol_sentinel_context",
        ) as fresh_context:
            candidates = signup._build_signup_sentinel_candidates(
                session=SimpleNamespace(), email="fixture@example.test", device_id="fixture",
                explicit_proxy=None, sentinel_context=SimpleNamespace(),
            )
        self.assertEqual([("current:with_email", '{"t":"fixture"}')], candidates)
        token.assert_called_once()
        fresh_context.assert_not_called()

    def test_user_register_stops_after_first_failed_submission(self) -> None:
        response = SimpleNamespace(status_code=400, text="fixture rejection")
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(signup, "_build_signup_sentinel_candidates", return_value=[("fixture-a", "{}"), ("fixture-b", "{}")]))
            for name in ("_minimal_user_register_cookie_header", "_deduped_cookie_header_for_request", "_protocol_auth_cookie_summary"):
                stack.enter_context(mock.patch.object(signup, name, return_value="fixture"))
            request = stack.enter_context(mock.patch.object(signup, "_session_request", return_value=response))
            history = []
            returned, winner = signup._submit_user_register_protocol(
                session=SimpleNamespace(), email="fixture@example.test", password="fixture",
                device_id="fixture", sentinel_context=None, explicit_proxy=None,
                network_attempt=1, attempt_history=history,
            )
        self.assertIs(response, returned)
        self.assertIsNone(winner)
        request.assert_called_once()
        self.assertEqual(1, len(history))

    def test_chatgpt_request_does_not_repeat_transient_failure(self) -> None:
        with mock.patch.object(login, "_session_request", side_effect=RuntimeError("curl: (28) fixture")) as request:
            with self.assertRaisesRegex(RuntimeError, "fixture"):
                login._chatgpt_login_request(SimpleNamespace(), "GET", "https://example.test", explicit_proxy=None, request_label="fixture")
        request.assert_called_once()

    def test_chatgpt_recovery_does_not_create_second_session(self) -> None:
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(login, "load_json_payload", return_value={}))
            stack.enter_context(mock.patch.object(login, "_normalize_seed_login_context", return_value={"email": "fixture@example.test", "password": "fixture", "mailboxRef": "fixture", "mailboxSessionId": "fixture"}))
            stack.enter_context(mock.patch.object(login, "flow_network_env", side_effect=contextlib.nullcontext))
            stack.enter_context(mock.patch.object(login, "get_mailbox_latest_message_id", return_value=0))
            stack.enter_context(mock.patch.object(login, "_new_protocol_sentinel_context", return_value=None))
            stack.enter_context(mock.patch.object(login, "_bootstrap_chatgpt_login_with_redirect", side_effect=RuntimeError("curl: (28) fixture")))
            stack.enter_context(mock.patch.dict(os.environ, {"PROTOCOL_ENABLE_BROWSER_CHATGPT_SESSION": "0"}))
            session = stack.enter_context(mock.patch.object(login.requests, "Session", return_value=mock.Mock()))
            with self.assertRaises(Exception):
                login.run_protocol_chatgpt_login_init_from_path(source_path="fixture.json")
        session.assert_called_once()

    def test_challenge_never_qualifies_for_fresh_session_retry(self) -> None:
        self.assertFalse(login._chatgpt_login_step_retryable(RuntimeError("browser_verification_required chatgpt_login_authorize_init_failed curl: (28)")))

    def test_transport_fallback_is_not_a_hidden_second_attempt(self) -> None:
        session = mock.Mock()
        session.request.side_effect = RuntimeError("curl: (35) fixture TLS error")
        with mock.patch.object(runtime, "resolve_system_native_proxy_decision", return_value=None), mock.patch.object(
            runtime, "debug_log_system_native_proxy_decision",
        ), mock.patch.object(runtime, "_forwarded_request_proxy", return_value=None), mock.patch.object(
            runtime, "_session_request_via_urllib",
        ) as fallback:
            with self.assertRaisesRegex(RuntimeError, "fixture TLS"):
                runtime._session_request(session, "POST", "https://example.test", explicit_proxy=None, request_label="fixture")
        session.request.assert_called_once()
        fallback.assert_not_called()

    def test_invalid_limit_precedes_transport_request(self) -> None:
        session = mock.Mock()
        with mock.patch.dict(os.environ, {RETRY_MAX_ATTEMPTS_ENV: "invalid"}), mock.patch.object(
            runtime, "resolve_system_native_proxy_decision", return_value=None,
        ), mock.patch.object(runtime, "debug_log_system_native_proxy_decision"), mock.patch.object(
            runtime, "_forwarded_request_proxy", return_value=None,
        ):
            with self.assertRaisesRegex(ValueError, "invalid_protocol_retry_max_attempts"):
                runtime._session_request(session, "POST", "https://example.test", explicit_proxy=None, request_label="fixture")
        session.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
