from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1] / "providers" / "python"
for path in (ROOT / "src", ROOT / "python_shared" / "src"):
    sys.path.insert(0, str(path))

from new_protocol_register import protocol_small_success as flow
from protocol_runtime import protocol_register as runtime
from protocol_runtime.errors import ProtocolRuntimeError
from shared_mailbox import easy_email_client as mail


class Clock:
    def __init__(self) -> None:
        self.now = 1_780_000_000.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class OtpResendRecoveryTests(unittest.TestCase):
    def wait(
        self, clock: Clock, poll: object, resend: object, *, timeout: int = 10,
        snapshot: tuple = ("", 0), snapshot_error: Exception | None = None,
    ) -> str:
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"MAILBOX_POLL_INTERVAL_SECONDS": "1"}))
            stack.enter_context(mock.patch.object(mail.time, "time", side_effect=lambda: clock.now))
            stack.enter_context(mock.patch.object(mail.time, "sleep", side_effect=clock.sleep))
            stack.enter_context(mock.patch.object(mail, "_mail_service_base_url", return_value="http://mail.example.test"))
            stack.enter_context(mock.patch.object(mail, "_probe_mail_service", return_value="ok:200"))
            stack.enter_context(mock.patch.object(
                mail, "_snapshot_session_openai_code", return_value=snapshot, side_effect=snapshot_error,
            ))
            stack.enter_context(mock.patch.object(mail, "_get_json", side_effect=poll))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            return mail.wait_openai_code(
                mailbox_ref="fixture-ref", session_id="fixture-session", timeout_seconds=timeout,
                min_mail_id=1_779_999_999, resend_callback=resend, resend_after_seconds=3,
            )

    def code(self, clock: Clock) -> dict:
        return {"code": {"code": "123456", "receivedAt": datetime.fromtimestamp(clock.now, timezone.utc).isoformat()}}

    def test_existing_code_is_returned_without_resending(self) -> None:
        clock = Clock()
        resend = mock.Mock(return_value=True)
        result = self.wait(clock, lambda _: self.code(clock), resend)
        self.assertEqual("123456", result)
        resend.assert_not_called()

    def test_snapshot_code_is_used_before_resending(self) -> None:
        clock = Clock()
        resend = mock.Mock(return_value=True)
        result = self.wait(clock, lambda _: {}, resend, snapshot=("654321", int(clock.now)))
        self.assertEqual("654321", result)
        resend.assert_not_called()

    def test_empty_inbox_gets_one_resend_and_then_returns_the_code(self) -> None:
        clock = Clock()
        sent = False

        def resend(remaining: int) -> bool:
            nonlocal sent
            self.assertEqual(7, remaining)
            sent = True
            return True

        callback = mock.Mock(side_effect=resend)
        result = self.wait(clock, lambda _: self.code(clock) if sent else {}, callback)
        self.assertEqual("123456", result)
        callback.assert_called_once()
        self.assertEqual(1_780_000_004, clock.now)

    def test_failed_mailbox_reads_do_not_trigger_resending(self) -> None:
        clock = Clock()
        resend = mock.Mock(return_value=True)
        with self.assertRaisesRegex(RuntimeError, "timeout waiting"):
            self.wait(clock, mock.Mock(side_effect=RuntimeError("mail transport unavailable")), resend)
        resend.assert_not_called()

    def test_resend_does_not_restart_the_wait_budget(self) -> None:
        clock = Clock()

        def resend(_: int) -> bool:
            clock.sleep(2)
            return True

        callback = mock.Mock(side_effect=resend)
        with self.assertRaisesRegex(RuntimeError, "timeout waiting"):
            self.wait(clock, lambda _: {}, callback)
        callback.assert_called_once_with(7)
        self.assertEqual(1_780_000_010, clock.now)

    def test_failed_snapshot_reads_do_not_trigger_resending(self) -> None:
        clock = Clock()
        resend = mock.Mock(return_value=True)
        with self.assertRaisesRegex(RuntimeError, "timeout waiting"):
            self.wait(
                clock, lambda _: {}, resend, snapshot_error=RuntimeError("snapshot unavailable"),
            )
        resend.assert_not_called()

    def test_resend_finishing_at_deadline_does_not_add_another_poll_delay(self) -> None:
        clock = Clock()

        def resend(remaining: int) -> bool:
            clock.sleep(remaining)
            return True

        callback = mock.Mock(side_effect=resend)
        with self.assertRaisesRegex(RuntimeError, "timeout waiting"):
            self.wait(clock, lambda _: {}, callback)
        callback.assert_called_once_with(7)
        self.assertEqual(1_780_000_010, clock.now)

    def test_declined_resend_is_not_repeated(self) -> None:
        clock = Clock()
        resend = mock.Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError, "timeout waiting"):
            self.wait(clock, lambda _: {}, resend)
        resend.assert_called_once_with(7)

    def test_resend_rejection_is_not_swallowed_as_a_mail_poll_error(self) -> None:
        clock = Clock()
        error = ProtocolRuntimeError("browser_verification_required", stage="stage_otp_send", detail="email_otp_resend")
        resend = mock.Mock(side_effect=error)
        with self.assertRaises(ProtocolRuntimeError) as caught:
            self.wait(clock, lambda _: {}, resend)
        self.assertIs(error, caught.exception)
        self.assertEqual(1_780_000_003, clock.now)

    def test_resend_uses_same_session_proxy_and_no_transport_replay(self) -> None:
        session = mock.Mock()
        response = SimpleNamespace(status_code=200, headers={})
        with mock.patch.dict(os.environ, {"PROTOCOL_ENABLE_EMAIL_OTP_SEND": "1"}), mock.patch.object(
            flow, "_session_request", return_value=response
        ) as request:
            result = flow._resend_passwordless_email_otp(
                session=session, explicit_proxy="http://proxy.example.test:8080",
                sentinel_context=SimpleNamespace(user_agent="fixture"), remaining_seconds=100,
            )
        self.assertTrue(result)
        request.assert_called_once()
        self.assertIs(session, request.call_args.args[0])
        self.assertEqual(flow.EMAIL_OTP_SEND_URL, request.call_args.args[2])
        self.assertEqual("http://proxy.example.test:8080", request.call_args.kwargs["explicit_proxy"])
        self.assertFalse(request.call_args.kwargs["allow_transport_fallback"])
        self.assertEqual(30, request.call_args.kwargs["timeout"])
        self.assertNotIn("openai-sentinel-token", request.call_args.kwargs["headers"])

    def test_resend_respects_disabled_setting_and_insufficient_budget(self) -> None:
        for enabled, remaining in (("0", 100), ("1", 9)):
            with self.subTest(enabled=enabled, remaining=remaining), mock.patch.dict(
                os.environ, {"PROTOCOL_ENABLE_EMAIL_OTP_SEND": enabled}
            ), mock.patch.object(flow, "_session_request") as request:
                self.assertFalse(flow._resend_passwordless_email_otp(
                    session=mock.Mock(), explicit_proxy=None, sentinel_context=None, remaining_seconds=remaining,
                ))
                request.assert_not_called()

    def test_challenge_and_rate_limit_responses_are_fatal(self) -> None:
        for status, headers in ((403, {"cf-mitigated": "challenge"}), (429, {})):
            with self.subTest(status=status), mock.patch.dict(os.environ, {"PROTOCOL_ENABLE_EMAIL_OTP_SEND": "1"}), mock.patch.object(
                flow, "_session_request", return_value=SimpleNamespace(status_code=status, headers=headers, text="rejected")
            ) as request:
                with self.assertRaises(ProtocolRuntimeError) as caught:
                    flow._resend_passwordless_email_otp(
                        session=mock.Mock(), explicit_proxy=None, sentinel_context=None, remaining_seconds=100,
                    )
                self.assertEqual("email_otp_resend", caught.exception.detail)
                request.assert_called_once()

    def test_no_replay_flag_prevents_urllib_retry_after_ambiguous_send_error(self) -> None:
        session = mock.Mock()
        session.request.side_effect = RuntimeError("curl: (55) send failure")
        with mock.patch.object(runtime, "resolve_system_native_proxy_decision", return_value=object()), mock.patch.object(
            runtime, "debug_log_system_native_proxy_decision"
        ), mock.patch.object(runtime, "_forwarded_request_proxy", return_value=None), mock.patch.object(
            runtime, "_session_request_via_urllib"
        ) as fallback:
            with self.assertRaisesRegex(RuntimeError, "send failure"):
                runtime._session_request(
                    session, "GET", "https://auth.example.test/otp/send", explicit_proxy=None,
                    request_label="fixture", allow_transport_fallback=False,
                )
        session.request.assert_called_once()
        self.assertNotIn("allow_transport_fallback", session.request.call_args.kwargs)
        fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
