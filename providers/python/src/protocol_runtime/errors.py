from __future__ import annotations


class ProtocolRuntimeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        stage: str = "stage_other",
        detail: str = "runtime_error",
        category: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = str(stage or "stage_other").strip() or "stage_other"
        self.detail = str(detail or "runtime_error").strip() or "runtime_error"
        normalized_category = str(category or "").strip().lower()
        self.category = normalized_category or None

    def to_response_payload(self) -> dict[str, str]:
        payload = {
            "error": str(self),
            "stage": self.stage,
            "detail": self.detail,
        }
        if self.category:
            payload["category"] = self.category
        return payload


def _infer_category_from_message(message: str) -> str:
    msg = str(message or "").strip().lower()
    if (
        "registration_disallowed" in msg
        or "terms of use restriction on about-you page" in msg
        or "cannot create your account with the given information" in msg
    ):
        return "blocked"
    if (
        "phone_wall" in msg
        or "phone number required" in msg
        or "add-phone" in msg
        or "cloudflare" in msg
        or "cf-mitigated=challenge" in msg
        or "just a moment" in msg
        or "captcha" in msg
        or "turnstile" in msg
        or "403" in msg
        or "429" in msg
        or "blocked" in msg
    ):
        return "blocked"
    if "otp_timeout" in msg or ("otp" in msg and "timeout" in msg):
        return "otp_timeout"
    if (
        "proxy" in msg
        or "network" in msg
        or "connection" in msg
        or "connect tunnel failed" in msg
        or "response 407" in msg
        or "fetch failed" in msg
        or "econnrefused" in msg
    ):
        return "proxy_error"
    if (
        "token" in msg
        or "callback" in msg
        or "invalid_state" in msg
        or "invalid client. please start over" in msg
        or "authorize_init_missing_did" in msg
        or "invalid_username_or_password" in msg
        or "missing_auth_cookie" in msg
        or "oauth error" in msg
        or "codex_session_handoff" in msg
    ):
        return "auth_error"
    return "flow_error"


def ensure_protocol_runtime_error(
    exc: BaseException,
    *,
    stage: str,
    detail: str,
    category: str | None = None,
) -> ProtocolRuntimeError:
    if isinstance(exc, ProtocolRuntimeError):
        if not exc.stage:
            exc.stage = str(stage or "stage_other").strip() or "stage_other"
        if not exc.detail:
            exc.detail = str(detail or "runtime_error").strip() or "runtime_error"
        if not exc.category:
            normalized_category = str(category or "").strip().lower()
            exc.category = normalized_category or _infer_category_from_message(str(exc))
        return exc

    message = str(exc or detail or "protocol_runtime_error").strip() or "protocol_runtime_error"
    normalized_category = str(category or "").strip().lower()
    final_category = normalized_category or _infer_category_from_message(message)
    return ProtocolRuntimeError(
        message,
        stage=stage,
        detail=detail,
        category=final_category,
    )

