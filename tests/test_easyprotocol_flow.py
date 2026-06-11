from __future__ import annotations

import contextlib
import datetime as dt
import json
import sys
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "providers" / "python" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

PYTHON_SHARED_ROOT = Path(__file__).resolve().parents[1] / "providers" / "python" / "python_shared" / "src"
if str(PYTHON_SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_SHARED_ROOT))

from new_protocol_register.easyprotocol_flow import _update_team_expand_progress_payload  # noqa: E402
from new_protocol_register import easyprotocol_flow  # noqa: E402
from new_protocol_register.magic import _classify_invite_error  # noqa: E402
from new_protocol_register import protocol_chatgpt_login  # noqa: E402
from new_protocol_register import protocol_oauth  # noqa: E402
from new_protocol_register import protocol_platform_org  # noqa: E402
from new_protocol_register import protocol_phone_verification  # noqa: E402
from new_protocol_register import protocol_small_success  # noqa: E402
from new_protocol_register.others import runtime as protocol_runtime  # noqa: E402
from protocol_runtime import protocol_register  # noqa: E402
from shared_mailbox import easy_email_client  # noqa: E402
from shared_captcha import service_client as captcha_service_client  # noqa: E402
from new_protocol_register.protocol_small_success import (  # noqa: E402
    PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV,
    PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV,
    PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV,
    _protocol_only_env,
)


class EasyProtocolFlowTests(unittest.TestCase):
    def test_get_mailbox_latest_message_id_uses_wall_clock_when_existing_code_has_no_marker(self) -> None:
        with mock.patch.object(
            easy_email_client,
            "_get_json",
            return_value={"code": {"code": "123456"}},
        ), mock.patch.object(easy_email_client.time, "time", return_value=1779975000.9):
            marker = easy_email_client.get_mailbox_latest_message_id(session_id="mailbox_123")

        self.assertEqual(1779975000, marker)

    def test_wait_openai_code_resolves_default_floor_before_polling(self) -> None:
        calls: list[str] = []

        def _fake_get_json(path: str) -> dict:
            calls.append(path)
            if path == "/mail/mailboxes/mailbox_123/code":
                return {
                    "code": {
                        "code": "111111",
                        "receivedAt": "2026-06-06T10:00:00Z",
                    }
                }
            if path == "/mail/snapshot":
                return {
                    "snapshot": {
                        "messages": [
                            {
                                "sessionId": "mailbox_123",
                                "extractedCode": "222222",
                                "receivedAt": "2026-06-06T10:00:05Z",
                            }
                        ]
                    }
                }
            raise AssertionError(f"unexpected path: {path}")

        with mock.patch.object(easy_email_client, "_get_json", side_effect=_fake_get_json):
            code = easy_email_client.wait_openai_code(
                mailbox_ref="moemail:mailbox_123",
                session_id="mailbox_123",
                timeout_seconds=5,
            )

        self.assertEqual("222222", code)
        self.assertEqual(
            [
                "/mail/mailboxes/mailbox_123/code",
                "/mail/mailboxes/mailbox_123/code",
                "/mail/snapshot",
            ],
            calls,
        )

    def test_wait_openai_code_uses_snapshot_after_transient_code_endpoint_error(self) -> None:
        calls: list[str] = []

        def _fake_get_json(path: str) -> dict:
            calls.append(path)
            if path == "/mail/mailboxes/mailbox_123/code":
                if calls.count(path) == 1:
                    return {
                        "code": {
                            "code": "111111",
                            "receivedAt": "2026-06-06T10:00:00Z",
                        }
                    }
                raise RuntimeError("mail service GET /mail/mailboxes/mailbox_123/code failed: HTTP 502")
            if path == "/mail/snapshot":
                return {
                    "snapshot": {
                        "messages": [
                            {
                                "sessionId": "mailbox_123",
                                "extractedCode": "222222",
                                "receivedAt": "2026-06-06T10:00:05Z",
                            }
                        ]
                    }
                }
            raise AssertionError(f"unexpected path: {path}")

        with mock.patch.object(easy_email_client, "_get_json", side_effect=_fake_get_json):
            code = easy_email_client.wait_openai_code(
                mailbox_ref="moemail:mailbox_123",
                session_id="mailbox_123",
                timeout_seconds=5,
            )

        self.assertEqual("222222", code)
        self.assertIn("/mail/snapshot", calls)

    def test_wait_openai_code_accepts_code_equal_to_auto_floor(self) -> None:
        with mock.patch.object(
            easy_email_client,
            "_resolve_openai_code_floor",
            return_value=123,
        ), mock.patch.object(
            easy_email_client,
            "_get_json",
            return_value={
                "code": {
                    "code": "654321",
                    "receivedAt": "1970-01-01T00:02:03+00:00",
                }
            },
        ), mock.patch.object(
            easy_email_client,
            "_snapshot_session_openai_code",
            return_value=("", 0),
        ), mock.patch.object(
            easy_email_client.time,
            "time",
            side_effect=[0, 0, 0, 11],
        ), mock.patch.object(
            easy_email_client.time,
            "sleep",
            return_value=None,
        ):
            code = easy_email_client.wait_openai_code(
                mailbox_ref="moemail:mailbox_123",
                session_id="mailbox_123",
                timeout_seconds=10,
            )

        self.assertEqual("654321", code)

    def test_update_team_expand_progress_payload_sets_last_updated_at(self) -> None:
        payload = {
            "teamFlow": {
                "teamExpandProgress": {
                    "targetCount": 1,
                    "successfulMemberEmails": [],
                    "successfulArtifacts": [],
                    "successCount": 0,
                    "remainingCount": 1,
                    "readyForMotherCollection": False,
                }
            }
        }

        updated = _update_team_expand_progress_payload(
            payload,
            success_email="member@example.com",
            success_path="/tmp/member.json",
            account_id="acct_12345678",
        )

        progress = updated["teamFlow"]["teamExpandProgress"]
        self.assertEqual(["member@example.com"], progress["successfulMemberEmails"])
        self.assertEqual(1, progress["successCount"])
        self.assertEqual(0, progress["remainingCount"])
        self.assertTrue(progress["readyForMotherCollection"])
        self.assertTrue(str(progress.get("lastUpdatedAt") or "").endswith("Z"))

    def test_protocol_only_env_disables_browser_bootstrap_and_sentinel(self) -> None:
        original_bootstrap = os.environ.get(PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV)
        original_sentinel = os.environ.get(PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV)
        original_stage2 = os.environ.get(PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV)
        try:
            os.environ[PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV] = "1"
            os.environ[PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV] = "1"
            os.environ[PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV] = "1"
            with _protocol_only_env():
                self.assertEqual("0", os.environ.get(PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV))
                self.assertEqual("0", os.environ.get(PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV))
                self.assertEqual("0", os.environ.get(PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV))
        finally:
            if original_bootstrap is None:
                os.environ.pop(PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV, None)
            else:
                os.environ[PROTOCOL_ENABLE_BROWSER_BOOTSTRAP_FALLBACK_ENV] = original_bootstrap
            if original_sentinel is None:
                os.environ.pop(PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV, None)
            else:
                os.environ[PROTOCOL_ENABLE_BROWSER_SENTINEL_ENV] = original_sentinel
            if original_stage2 is None:
                os.environ.pop(PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV, None)
            else:
                os.environ[PROTOCOL_ENABLE_BROWSER_STAGE2_HANDOFF_ENV] = original_stage2

    def test_classify_invite_error_detects_deactivated_workspace(self) -> None:
        payload = {
            "detail": {
                "code": "deactivated_workspace",
            },
            "status_code": 402,
        }
        self.assertEqual("deactivated_workspace", _classify_invite_error(402, payload))

    def test_build_signup_sentinel_candidates_keeps_trying_other_personas(self) -> None:
        session = mock.Mock()
        sentinel_context = SimpleNamespace(user_agent="ua")
        with mock.patch.object(
            protocol_small_success,
            "_get_sentinel_header_for_signup",
            side_effect=[
                "token-current-with-email",
                "token-current-without-email",
                "token-har1-with-email",
                "token-har1-without-email",
                "token-har2-with-email",
                "token-har2-without-email",
            ],
        ) as get_sentinel, mock.patch.object(
            protocol_small_success,
            "_sentinel_token_lengths",
            side_effect=[
                (1312, 665, True),
                (1336, 665, True),
                (1290, 620, True),
                (1400, 700, True),
                (1280, 610, True),
                (1390, 690, True),
            ],
        ):
            candidates = protocol_small_success._build_signup_sentinel_candidates(
                session=session,
                email="demo@example.com",
                device_id="device-id",
                explicit_proxy="http://proxy:8080",
                sentinel_context=sentinel_context,
                network_attempt=1,
            )
        self.assertIn(("har1:without_email", "token-har1-without-email"), candidates)
        self.assertIn(("har2:without_email", "token-har2-without-email"), candidates)
        self.assertEqual(6, get_sentinel.call_count)

    def test_captcha_service_client_rejects_easybrowser_base_url(self) -> None:
        original_base_url = os.environ.get("CAPTCHA_SERVICE_BASE_URL")
        try:
            os.environ["CAPTCHA_SERVICE_BASE_URL"] = "http://easy-browser:18080"
            with self.assertRaises(RuntimeError) as ctx:
                captcha_service_client._post_json("/createTask", {"task": {"type": "Demo"}})
            self.assertIn("EasyBrowser attach service", str(ctx.exception))
        finally:
            if original_base_url is None:
                os.environ.pop("CAPTCHA_SERVICE_BASE_URL", None)
            else:
                os.environ["CAPTCHA_SERVICE_BASE_URL"] = original_base_url

    def test_chatgpt_login_request_retries_transient_network_error(self) -> None:
        session = mock.Mock()
        response = SimpleNamespace(status_code=200, url="https://chatgpt.com/auth/login_with")
        with mock.patch.object(
            protocol_chatgpt_login,
            "_session_request",
            side_effect=[RuntimeError("curl: (7) Connection closed abruptly"), response],
        ) as session_request:
            result = protocol_chatgpt_login._chatgpt_login_request(
                session,
                "GET",
                "https://chatgpt.com/auth/login_with",
                explicit_proxy="http://proxy:8080",
                request_label="chatgpt-login",
                timeout=20,
            )
        self.assertIs(result, response)
        self.assertEqual(2, session_request.call_count)

    def test_send_email_otp_retries_transient_network_error(self) -> None:
        session = mock.Mock()
        response = SimpleNamespace(status_code=200, url="https://auth.openai.com/api/accounts/email-otp/send")
        with mock.patch.object(
            protocol_register,
            "_build_protocol_headers",
            return_value={},
        ), mock.patch.object(
            protocol_register,
            "_session_request",
            side_effect=[RuntimeError("curl: (28) Operation timed out"), response],
        ) as session_request:
            result = protocol_register._send_email_otp(
                session,
                explicit_proxy="http://proxy:8080",
                header_builder=None,
            )
        self.assertIs(result, response)
        self.assertEqual(2, session_request.call_count)

    def test_submit_phone_number_retries_transient_network_error(self) -> None:
        session = mock.Mock()
        response = SimpleNamespace(status_code=200, url="https://auth.openai.com/api/accounts/add-phone/send")
        with mock.patch.object(
            protocol_register,
            "_phone_resume_sentinel_context",
            return_value=mock.Mock(),
        ), mock.patch.object(
            protocol_register,
            "_build_protocol_headers",
            return_value={"referer": "https://auth.openai.com/add-phone"},
        ), mock.patch.object(
            protocol_register,
            "_session_request",
            side_effect=[RuntimeError("curl: (28) Operation timed out"), response],
        ) as session_request, mock.patch.object(
            protocol_register,
            "_extract_page_type",
            return_value="sms_verification",
        ):
            result = protocol_register._submit_phone_number_via_protocol_session(
                resume_context={"continueUrl": "https://auth.openai.com/add-phone"},
                session=session,
                phone_number="+15551234567",
                explicit_proxy="http://proxy:8080",
            )

        self.assertEqual("phone_number_submitted", result["status"])
        self.assertEqual("sms_verification", result["pageType"])
        self.assertEqual(2, session_request.call_count)

    def test_extract_chatgpt_client_bootstrap_reads_access_token(self) -> None:
        html = """
        <html>
          <body>
            <script id="client-bootstrap" type="application/json">
              {"authStatus":"logged_in","session":{"accessToken":"tok_demo","account":{"id":"acct_1","planType":"free","structure":"personal"},"user":{"id":"user_1","email":"demo@example.com"}}}
            </script>
          </body>
        </html>
        """
        payload = protocol_chatgpt_login._extract_chatgpt_client_bootstrap(html)
        self.assertEqual("logged_in", payload.get("authStatus"))
        self.assertEqual("tok_demo", (payload.get("session") or {}).get("accessToken"))

    def test_platform_org_init_persists_oauth_refresh_material_without_returning_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            seed_path = Path(tmp_dir) / "small-success.json"
            organization_update_payloads: list[dict[str, object]] = []
            seed_path.write_text(
                json.dumps(
                    {
                        "email": "user@example.com",
                        "mailboxRef": "mailbox-ref",
                        "mailboxSessionId": "mailbox-session",
                        "finalUrl": "https://platform.openai.com/auth/callback?code=code_123&state=state_123",
                        "platformAuth": {
                            "codeVerifier": "verifier_123",
                            "state": "state_123",
                            "deviceId": "device_123",
                        },
                    }
                ),
                encoding="utf-8",
            )

            def request_side_effect(*_args: object, **kwargs: object) -> SimpleNamespace:
                request_label = str(kwargs.get("request_label") or "")
                if request_label == "platform-oauth-token":
                    return SimpleNamespace(
                        status_code=200,
                        json=lambda: {
                            "access_token": "access.demo",
                            "refresh_token": "refresh.demo",
                            "id_token": "id.demo",
                            "expires_in": 3600,
                            "token_type": "Bearer",
                        },
                    )
                if request_label == "platform-onboarding-login":
                    return SimpleNamespace(
                        status_code=200,
                        json=lambda: {
                            "user": {
                                "id": "user_123",
                                "session": {"sensitive_id": "session_token_123"},
                                "orgs": {
                                    "data": [
                                        {
                                            "id": "org_123",
                                            "title": "personal",
                                            "name": "personal",
                                            "settings": {"completed_platform_onboarding": False},
                                            "projects": {"data": [{"id": "proj_123", "title": "Default"}]},
                                        }
                                    ]
                                },
                            }
                        },
                    )
                if request_label == "platform-organization-update":
                    request_json = kwargs.get("json")
                    self.assertIsInstance(request_json, dict)
                    organization_update_payloads.append(dict(request_json))
                    return SimpleNamespace(status_code=200, json=lambda: {"ok": True})
                if request_label == "platform-organization-user-update":
                    return SimpleNamespace(status_code=200, json=lambda: {"ok": True})
                raise AssertionError(f"unexpected request label: {request_label}")

            with mock.patch.object(protocol_platform_org, "flow_network_env", return_value=contextlib.nullcontext()), mock.patch.object(
                protocol_platform_org,
                "_session_request",
                side_effect=request_side_effect,
            ), mock.patch.object(
                protocol_platform_org,
                "_build_platform_headers",
                return_value={},
            ), mock.patch.object(protocol_platform_org, "_best_effort_warm_platform_permissions"):
                result = protocol_platform_org.run_protocol_platform_organization_init_from_path(
                    source_path=seed_path,
                    explicit_proxy="http://proxy:8080",
                    organization_name="Personal",
                    organization_title="Personal",
                )

            self.assertTrue(result["ok"])
            self.assertEqual("completed", result["status"])
            self.assertNotIn("refreshToken", result)
            self.assertNotIn("refresh_token", result)

            persisted = json.loads(seed_path.read_text(encoding="utf-8"))
            self.assertEqual("access.demo", persisted["accessToken"])
            self.assertEqual("refresh.demo", persisted["refreshToken"])
            self.assertEqual("id.demo", persisted["idToken"])
            self.assertTrue(str(persisted["expiresAt"]).endswith("Z"))
            self.assertEqual(protocol_platform_org._PLATFORM_AUTH0_CLIENT_ID, persisted["oauthClientId"])
            self.assertEqual(protocol_platform_org._PLATFORM_AUTH0_TOKEN_URL, persisted["oauthTokenEndpoint"])
            self.assertEqual("oauth_token", persisted["refreshStrategy"])
            oauth_tokens = persisted["chatgptLoginDetails"]["oauthTokens"]
            self.assertEqual("access.demo", oauth_tokens["access_token"])
            self.assertEqual("refresh.demo", oauth_tokens["refresh_token"])
            self.assertEqual("id.demo", oauth_tokens["id_token"])
            self.assertEqual(3600, oauth_tokens["expires_in"])
            self.assertEqual("Bearer", oauth_tokens["token_type"])
            self.assertTrue(str(oauth_tokens["exchanged_at"]).endswith("Z"))
            self.assertEqual(1, len(organization_update_payloads))
            self.assertEqual("personal", organization_update_payloads[0]["name"])
            self.assertEqual("Personal", organization_update_payloads[0]["title"])

    def test_initialize_platform_organization_dispatch_defaults_to_personal_title(self) -> None:
        with mock.patch.object(
            easyprotocol_flow,
            "run_protocol_platform_organization_init_from_path",
            return_value={"ok": True, "status": "completed"},
        ) as platform_org_init:
            result = easyprotocol_flow.dispatch_easyprotocol_step(
                step_type="initialize_platform_organization",
                step_input={"source_path": "/tmp/source.json", "proxy_url": "http://proxy.local:8080"},
            )

        self.assertTrue(result["ok"])
        platform_org_init.assert_called_once_with(
            source_path="/tmp/source.json",
            explicit_proxy="http://proxy.local:8080",
            organization_name="Personal",
            organization_title="Personal",
            developer_persona="student",
        )

    def test_chatgpt_login_details_merge_preserves_existing_oauth_tokens(self) -> None:
        details = protocol_chatgpt_login._merge_chatgpt_login_details(
            seed_payload={
                "chatgptLoginDetails": {
                    "oauthTokens": {
                        "access_token": "access.demo",
                        "refresh_token": "refresh.demo",
                        "id_token": "id.demo",
                    }
                }
            },
            account_entries=[{"id": "acct_1"}],
            client_bootstrap={
                "authStatus": "logged_in",
                "accountId": "acct_1",
                "planType": "free",
                "structure": "personal",
                "accessTokenPresent": True,
                "accessToken": "bootstrap.demo",
                "userId": "user_1",
                "email": "user@example.com",
            },
            page_type="chatgpt_logged_in",
            network_attempt=2,
        )

        self.assertEqual("refresh.demo", details["oauthTokens"]["refresh_token"])
        self.assertEqual("bootstrap.demo", details["clientBootstrap"]["accessToken"])
        self.assertEqual([{"id": "acct_1"}], details["accounts"])
        self.assertEqual("chatgpt_logged_in", details["pageType"])
        self.assertEqual(2, details["networkAttempt"])

    def test_obtain_team_mother_oauth_force_email_auth_skips_refresh(self) -> None:
        with mock.patch.object(
            easyprotocol_flow,
            "load_json_payload",
            return_value={
                "email": "mother@example.com",
                "refresh_token": "rt_demo",
            },
        ), mock.patch.object(
            easyprotocol_flow,
            "refresh_team_auth_once",
        ) as refresh_team_auth_once, mock.patch.object(
            easyprotocol_flow,
            "run_protocol_oauth_from_path",
            return_value=SimpleNamespace(
                auth={"email": "mother@example.com", "user_id": "user_123"},
                email="mother@example.com",
                account_id="acct_123",
                storage_path="/tmp/codex-123.json",
            ),
        ) as run_protocol_oauth_from_path:
            result = easyprotocol_flow.dispatch_easyprotocol_step(
                step_type="obtain_team_mother_oauth",
                step_input={
                    "source_path": "C:/tmp/mother.json",
                    "output_dir": "C:/tmp/out",
                    "force_email_auth": True,
                },
            )
        refresh_team_auth_once.assert_not_called()
        run_protocol_oauth_from_path.assert_called_once()
        self.assertEqual("email", result["authMode"])
        self.assertFalse(bool(result.get("refreshOnly")))

    def test_run_protocol_oauth_once_returns_phone_verification_required_result(self) -> None:
        seed_payload = {
            "email": "user@example.com",
            "password": "pw",
            "mailboxRef": "mailtm:test",
            "mailboxSessionId": "mailbox_123",
            "firstName": "User",
            "lastName": "Example",
            "birthdate": "2000-01-01",
        }

        class _FlowProxy:
            proxy_url = "http://easy-proxy:25000"

        with mock.patch.object(
            protocol_oauth,
            "_refresh_seed_mailbox_binding",
            return_value=(
                {
                    "email": "user@example.com",
                    "password": "pw",
                    "mailbox_ref": "mailtm:test",
                    "session_id": "mailbox_123",
                    "first_name": "User",
                    "last_name": "Example",
                    "birthdate": "2000-01-01",
                },
                {},
            ),
        ), mock.patch.object(
            protocol_oauth,
            "_ensure_protocol_oauth_easy_runtime_defaults",
        ), mock.patch.object(
            protocol_oauth,
            "flow_network_env",
            return_value=contextlib.nullcontext(),
        ), mock.patch.object(
            protocol_oauth,
            "lease_flow_proxy",
            return_value=contextlib.nullcontext(_FlowProxy()),
        ), mock.patch.object(
            protocol_oauth,
            "run_protocol_repair_once",
            return_value=protocol_register.ProtocolRegistrationResult(
                email="user@example.com",
                auth={"mailboxRef": "mailtm:test"},
                phone_verification_required=True,
                page_type="add_phone",
                final_url="https://auth.openai.com/add-phone",
                resume_context={"continueUrl": "https://auth.openai.com/add-phone", "token": "resume_123"},
            ),
        ), mock.patch.object(
            protocol_oauth,
            "persist_first_phone_record",
            return_value="C:/tmp/first-phone.json",
        ) as persist_first_phone_record, mock.patch.object(
            protocol_oauth,
            "persist_success_auth_json",
        ) as persist_success_auth_json, mock.patch.object(
            protocol_oauth,
            "release_mailbox_sessions_by_email",
            return_value=[],
        ):
            result = protocol_oauth.run_protocol_oauth_once(seed_payload=seed_payload, output_dir="C:/tmp/out")

        self.assertTrue(result.phone_verification_required)
        self.assertEqual("add_phone", result.page_type)
        self.assertEqual("https://auth.openai.com/add-phone", result.final_url)
        self.assertEqual("resume_123", result.resume_context["token"])
        self.assertEqual("C:/tmp/first-phone.json", result.storage_path)
        persist_first_phone_record.assert_called_once()
        persist_success_auth_json.assert_not_called()

    def test_refresh_seed_mailbox_binding_reuses_recent_existing_binding(self) -> None:
        created_at = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=2)
        ).isoformat().replace("+00:00", "Z")
        auth_obj = {
            "email": "prudence96e088@pek.blaizesmp.net",
            "mailbox_ref": "tempmail-lol:tempmail_lol_shared_default:%7Bdemo%7D",
            "session_id": "mailbox_20260527131801_1762",
            "created_at": created_at,
        }

        with mock.patch.object(
            protocol_oauth,
            "release_mailbox_sessions_by_email",
        ) as release_mailbox_sessions_by_email, mock.patch.object(
            protocol_oauth,
            "resolve_mailbox",
        ) as resolve_mailbox:
            updated_auth, refresh = protocol_oauth._refresh_seed_mailbox_binding(auth_obj)

        release_mailbox_sessions_by_email.assert_not_called()
        resolve_mailbox.assert_not_called()
        self.assertEqual(auth_obj["mailbox_ref"], updated_auth["mailbox_ref"])
        self.assertEqual(auth_obj["session_id"], updated_auth["session_id"])
        self.assertEqual("reuse_existing", refresh["strategy"])

    def test_refresh_seed_mailbox_binding_reuses_stale_existing_binding(self) -> None:
        created_at = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
        ).isoformat().replace("+00:00", "Z")
        auth_obj = {
            "email": "user@example.com",
            "mailbox_ref": "moemail:old-ref",
            "session_id": "mailbox_old",
            "created_at": created_at,
        }
        with mock.patch.object(
            protocol_oauth,
            "release_mailbox_sessions_by_email",
        ) as release_mailbox_sessions_by_email, mock.patch.object(
            protocol_oauth,
            "resolve_mailbox",
        ) as resolve_mailbox:
            updated_auth, refresh = protocol_oauth._refresh_seed_mailbox_binding(auth_obj)

        release_mailbox_sessions_by_email.assert_not_called()
        resolve_mailbox.assert_not_called()
        self.assertEqual("mailbox_old", updated_auth["session_id"])
        self.assertEqual("moemail:old-ref", updated_auth["mailbox_ref"])
        self.assertEqual("reuse_existing", refresh["strategy"])

    def test_refresh_seed_mailbox_binding_recreates_when_session_binding_missing(self) -> None:
        auth_obj = {
            "email": "user@example.com",
            "mailbox_ref": "",
            "session_id": "",
            "created_at": "",
        }
        resolved_mailbox = protocol_runtime.Mailbox(
            provider="moemail",
            email="user@example.com",
            ref="moemail:new-ref",
            session_id="mailbox_new",
        )

        with mock.patch.object(
            protocol_oauth,
            "release_mailbox_sessions_by_email",
            return_value=[],
        ) as release_mailbox_sessions_by_email, mock.patch.object(
            protocol_oauth,
            "resolve_mailbox",
            return_value=resolved_mailbox,
        ) as resolve_mailbox:
            updated_auth, refresh = protocol_oauth._refresh_seed_mailbox_binding(auth_obj)

        release_mailbox_sessions_by_email.assert_called_once()
        resolve_mailbox.assert_called_once()
        self.assertEqual("mailbox_new", updated_auth["session_id"])
        self.assertEqual("moemail:new-ref", updated_auth["mailbox_ref"])
        self.assertEqual("recreate_existing", refresh["strategy"])

    def test_obtain_codex_oauth_phone_wall_result_contains_resume_context(self) -> None:
        with mock.patch.object(
            easyprotocol_flow,
            "run_protocol_oauth_from_path",
            return_value=SimpleNamespace(
                phone_verification_required=True,
                page_type="add_phone",
                final_url="https://chatgpt.com/auth/add-phone",
                resume_context={"flow": "oauth", "token": "resume_123"},
                storage_path="C:/tmp/first-phone.json",
            ),
        ):
            result = easyprotocol_flow.dispatch_easyprotocol_step(
                step_type="obtain_codex_oauth",
                step_input={"source_path": "C:/tmp/small.json", "output_dir": "C:/tmp/out"},
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["phoneVerificationRequired"])
        self.assertEqual("add_phone", result["pageType"])
        self.assertEqual("resume_123", result["resumeContext"]["token"])

    def test_dispatch_submit_phone_verification_code_returns_oauth_payload(self) -> None:
        with mock.patch.object(
            easyprotocol_flow,
            "submit_phone_verification_code_from_path",
            return_value={
                "ok": True,
                "status": "completed",
                "successPath": "C:/tmp/codex-free.json",
                "userId": "user_123",
            },
        ):
            result = easyprotocol_flow.dispatch_easyprotocol_step(
                step_type="submit_phone_verification_code",
                step_input={
                    "source_path": "C:/tmp/small.json",
                    "resume_context": {"token": "resume_123"},
                    "sms_code": "123456",
                },
            )

        self.assertEqual("completed", result["status"])
        self.assertEqual("user_123", result["userId"])

    def test_submit_phone_number_for_resume_updates_resume_context_after_browser_step(self) -> None:
        class _FakeDriver:
            def __init__(self) -> None:
                self.current_url = "https://auth.openai.com/add-phone"

            def get(self, url: str) -> None:
                self.current_url = "https://auth.openai.com/sms-verification"

            def quit(self) -> None:
                return None

        with mock.patch.object(
            protocol_register,
            "_load_protocol_browser_new_driver",
            return_value=lambda explicit_proxy, browser_backend=None: (_FakeDriver(), None),
        ), mock.patch.object(
            protocol_register,
            "_hydrate_browser_driver_with_protocol_session_cookies",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_import_browser_driver_cookies_into_session",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_browser_try_submit_phone_number",
            return_value=True,
        ), mock.patch.object(
            protocol_register,
            "_export_protocol_session_cookies",
            return_value=[{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
        ), mock.patch.object(
            protocol_register,
            "_extract_page_type",
            return_value="sms_verification",
        ):
            result = protocol_register.submit_phone_number_for_resume(
                source_payload={"email": "user@example.com"},
                resume_context={
                    "continueUrl": "https://auth.openai.com/add-phone",
                    "sessionCookies": [{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
                    "oauth": {
                        "authUrl": "https://auth.openai.com/api/accounts/authorize?x=1",
                        "state": "state_123",
                        "codeVerifier": "verifier_123",
                        "redirectUri": "https://chatgpt.com/api/auth/callback/openai",
                    },
                },
                phone_number="+15551234567",
                explicit_proxy=None,
            )

        self.assertEqual("sms_verification", result["pageType"])
        self.assertEqual("https://auth.openai.com/sms-verification", result["resumeContext"]["continueUrl"])
        self.assertEqual(1, len(result["resumeContext"]["sessionCookies"]))

    def test_submit_phone_verification_code_for_resume_can_finish_matching_callback_url(self) -> None:
        class _FakeDriver:
            def __init__(self) -> None:
                self.current_url = "http://localhost:1455/auth/callback?code=abc&state=state_123"

            def get(self, url: str) -> None:
                self.current_url = "http://localhost:1455/auth/callback?code=abc&state=state_123"

            def quit(self) -> None:
                return None

        with mock.patch.object(
            protocol_register,
            "_load_protocol_browser_new_driver",
            return_value=lambda explicit_proxy, browser_backend=None: (_FakeDriver(), None),
        ), mock.patch.object(
            protocol_register,
            "_hydrate_browser_driver_with_protocol_session_cookies",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_import_browser_driver_cookies_into_session",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_browser_try_submit_phone_code",
            return_value=True,
        ), mock.patch.object(
            protocol_register,
            "_callback_result_from_url",
            return_value=protocol_register.ProtocolRegistrationResult(
                email="user@example.com",
                auth={"user_id": "user_123"},
            ),
        ) as callback_result_from_url, mock.patch.object(
            protocol_register,
            "_continue_authenticated_codex_oauth",
        ) as continue_authenticated_codex_oauth:
            result = protocol_register.submit_phone_verification_code_for_resume(
                source_payload={
                    "email": "user@example.com",
                    "password": "pw",
                    "mailboxRef": "mailtm:test",
                    "firstName": "User",
                    "lastName": "Example",
                    "birthdate": "2000-01-01",
                },
                resume_context={
                    "continueUrl": "https://auth.openai.com/sms-verification",
                    "sessionCookies": [{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
                    "oauth": {
                        "authUrl": "https://auth.openai.com/oauth/authorize?x=1",
                        "state": "state_123",
                        "codeVerifier": "verifier_123",
                        "redirectUri": "http://localhost:1455/auth/callback",
                    },
                },
                sms_code="123456",
                explicit_proxy=None,
            )

        self.assertEqual("user@example.com", result["email"])
        self.assertEqual("user_123", result["auth"]["user_id"])
        callback_result_from_url.assert_called_once()
        continue_authenticated_codex_oauth.assert_not_called()

    def test_submit_phone_verification_code_replays_codex_oauth_after_chatgpt_web_callback(self) -> None:
        class _FakeDriver:
            def __init__(self) -> None:
                self.current_url = "https://chatgpt.com/api/auth/callback/openai?code=abc&state=state_123"

            def get(self, url: str) -> None:
                self.current_url = "https://chatgpt.com/api/auth/callback/openai?code=abc&state=state_123"

            def quit(self) -> None:
                return None

        completed = protocol_register.ProtocolRegistrationResult(
            email="user@example.com",
            auth={
                "account_id": "acct_codex",
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "acct_codex",
                    "chatgpt_plan_type": "free",
                    "organizations": [{"title": "personal", "role": "owner"}],
                },
            },
        )
        chatgpt_web_result = protocol_register.ProtocolRegistrationResult(
            email="user@example.com",
            auth={
                "account_id": "acct_web",
                "https://api.openai.com/auth": {"user_id": "user_web"},
            },
        )

        with mock.patch.object(
            protocol_register,
            "_load_protocol_browser_new_driver",
            return_value=lambda explicit_proxy, browser_backend=None: (_FakeDriver(), None),
        ), mock.patch.object(
            protocol_register,
            "_hydrate_browser_driver_with_protocol_session_cookies",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_import_browser_driver_cookies_into_session",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_browser_try_submit_phone_code",
            return_value=True,
        ), mock.patch.object(
            protocol_register,
            "_callback_result_from_url",
            return_value=chatgpt_web_result,
        ) as callback_result_from_url, mock.patch.object(
            protocol_register,
            "_continue_authenticated_codex_oauth",
            return_value=completed,
        ) as continue_authenticated_codex_oauth:
            result = protocol_register.submit_phone_verification_code_for_resume(
                source_payload={
                    "email": "user@example.com",
                    "password": "pw",
                    "mailboxRef": "mailtm:test",
                    "firstName": "User",
                    "lastName": "Example",
                    "birthdate": "2000-01-01",
                },
                resume_context={
                    "continueUrl": "https://auth.openai.com/sms-verification",
                    "sessionCookies": [{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
                    "oauth": {
                        "authUrl": "https://auth.openai.com/oauth/authorize?x=1",
                        "state": "state_123",
                        "codeVerifier": "verifier_123",
                        "redirectUri": "http://localhost:1455/auth/callback",
                    },
                },
                sms_code="123456",
                explicit_proxy=None,
            )

        self.assertEqual("acct_codex", result["accountId"])
        self.assertEqual("free", result["auth"]["https://api.openai.com/auth"]["chatgpt_plan_type"])
        callback_result_from_url.assert_not_called()
        continue_authenticated_codex_oauth.assert_called_once()

    def test_browser_submit_phone_code_skips_hidden_otp_inputs(self) -> None:
        class _FakeElement:
            def __init__(self, *, displayed: bool, label: str) -> None:
                self.displayed = displayed
                self.label = label
                self.sent: list[str] = []

            def is_displayed(self) -> bool:
                return self.displayed

            def is_enabled(self) -> bool:
                return True

            @property
            def rect(self) -> dict[str, int]:
                return {"width": 24 if self.displayed else 0, "height": 24 if self.displayed else 0}

            def click(self) -> None:
                if not self.displayed:
                    raise RuntimeError(f"{self.label} hidden")

            def send_keys(self, *values: object) -> None:
                if not self.displayed:
                    raise RuntimeError(f"{self.label} hidden")
                self.sent.extend(str(value) for value in values)

        class _FakeSwitch:
            def default_content(self) -> None:
                return None

        class _FakeDriver:
            def __init__(self) -> None:
                self.switch_to = _FakeSwitch()
                self.hidden = _FakeElement(displayed=False, label="hidden")
                self.visible = [_FakeElement(displayed=True, label=f"digit-{index}") for index in range(4)]

            def find_elements(self, by: object, selector: str) -> list[object]:
                if selector == 'input[maxlength="1"], input[inputmode="numeric"], input[autocomplete="one-time-code"]':
                    return [self.hidden, *self.visible]
                if selector == "button":
                    return []
                return []

        driver = _FakeDriver()

        self.assertTrue(protocol_register._browser_try_submit_phone_code(driver, sms_code="1234"))
        self.assertEqual(["1"], driver.visible[0].sent[-1:])
        self.assertEqual(["4"], driver.visible[3].sent[-1:])

    def test_browser_submit_phone_code_skips_non_interactable_visible_otp_inputs(self) -> None:
        class _FakeElement:
            def __init__(self, *, label: str, interactable: bool = True) -> None:
                self.label = label
                self.interactable = interactable
                self.sent: list[str] = []

            def is_displayed(self) -> bool:
                return True

            def is_enabled(self) -> bool:
                return True

            @property
            def rect(self) -> dict[str, int]:
                return {"width": 24, "height": 24}

            def click(self) -> None:
                if not self.interactable:
                    raise RuntimeError(f"{self.label} not interactable")

            def send_keys(self, *values: object) -> None:
                if not self.interactable:
                    raise RuntimeError(f"{self.label} not interactable")
                self.sent.extend(str(value) for value in values)

        class _FakeSwitch:
            def default_content(self) -> None:
                return None

        class _FakeDriver:
            def __init__(self) -> None:
                self.switch_to = _FakeSwitch()
                self.bad = _FakeElement(label="bad", interactable=False)
                self.visible = [_FakeElement(label=f"digit-{index}") for index in range(4)]

            def find_elements(self, by: object, selector: str) -> list[object]:
                if selector == 'input[maxlength="1"], input[inputmode="numeric"], input[autocomplete="one-time-code"]':
                    return [self.bad, *self.visible]
                if selector == "button":
                    return []
                return []

        driver = _FakeDriver()

        self.assertTrue(protocol_register._browser_try_submit_phone_code(driver, sms_code="1234"))
        self.assertEqual(["1"], driver.visible[0].sent[-1:])
        self.assertEqual(["4"], driver.visible[3].sent[-1:])

    def test_browser_submit_phone_code_skips_non_interactable_text_code_input(self) -> None:
        class _FakeElement:
            def __init__(self, *, label: str, interactable: bool = True) -> None:
                self.label = label
                self.interactable = interactable
                self.sent: list[str] = []

            def is_displayed(self) -> bool:
                return True

            def is_enabled(self) -> bool:
                return True

            @property
            def rect(self) -> dict[str, int]:
                return {"width": 120, "height": 24}

            def click(self) -> None:
                if not self.interactable:
                    raise RuntimeError(f"{self.label} not interactable")

            def send_keys(self, *values: object) -> None:
                if not self.interactable:
                    raise RuntimeError(f"{self.label} not interactable")
                self.sent.extend(str(value) for value in values)

        class _FakeSwitch:
            def default_content(self) -> None:
                return None

        class _FakeDriver:
            def __init__(self) -> None:
                self.switch_to = _FakeSwitch()
                self.bad = _FakeElement(label="bad", interactable=False)
                self.good = _FakeElement(label="good")

            def find_elements(self, by: object, selector: str) -> list[object]:
                if selector == 'input[maxlength="1"], input[inputmode="numeric"], input[autocomplete="one-time-code"]':
                    return []
                if selector == 'input[name*="code"]':
                    return [self.bad, self.good]
                if selector == "button":
                    return []
                return []

            def find_element(self, by: object, selector: str) -> object:
                elements = self.find_elements(by, selector)
                if not elements:
                    raise RuntimeError(f"not found: {selector}")
                return elements[0]

        driver = _FakeDriver()

        self.assertTrue(protocol_register._browser_try_submit_phone_code(driver, sms_code="1234"))
        self.assertEqual(["1234"], driver.good.sent[-1:])

    def test_browser_submit_phone_code_returns_false_when_submit_button_is_not_clickable(self) -> None:
        class _FakeInput:
            def is_displayed(self) -> bool:
                return True

            def is_enabled(self) -> bool:
                return True

            @property
            def rect(self) -> dict[str, int]:
                return {"width": 120, "height": 24}

            def click(self) -> None:
                return None

            def send_keys(self, *values: object) -> None:
                return None

        class _FakeButton:
            text = "Continue"

            def is_displayed(self) -> bool:
                return True

            def is_enabled(self) -> bool:
                return True

            @property
            def rect(self) -> dict[str, int]:
                return {"width": 80, "height": 24}

            def click(self) -> None:
                raise RuntimeError("button not interactable")

        class _FakeSwitch:
            def default_content(self) -> None:
                return None

        class _FakeDriver:
            def __init__(self) -> None:
                self.switch_to = _FakeSwitch()
                self.text_input = _FakeInput()
                self.button = _FakeButton()

            def find_elements(self, by: object, selector: str) -> list[object]:
                if selector == 'input[maxlength="1"], input[inputmode="numeric"], input[autocomplete="one-time-code"]':
                    return []
                if selector == 'input[name*="code"]':
                    return [self.text_input]
                if selector == "button":
                    return [self.button]
                return []

            def find_element(self, by: object, selector: str) -> object:
                elements = self.find_elements(by, selector)
                if not elements:
                    raise RuntimeError(f"not found: {selector}")
                return elements[0]

        self.assertFalse(protocol_register._browser_try_submit_phone_code(_FakeDriver(), sms_code="1234"))

    def test_submit_phone_number_for_resume_waits_for_phone_surface_before_failing(self) -> None:
        class _FakeDriver:
            def __init__(self) -> None:
                self.current_url = "https://auth.openai.com/sms-verification"
                self.title = "Phone number required"

            def get(self, url: str) -> None:
                self.current_url = "https://auth.openai.com/sms-verification"

            def quit(self) -> None:
                return None

        with mock.patch.object(
            protocol_register,
            "_load_protocol_browser_new_driver",
            return_value=lambda explicit_proxy, browser_backend=None: (_FakeDriver(), None),
        ), mock.patch.object(
            protocol_register,
            "_submit_phone_number_via_protocol_session",
            side_effect=RuntimeError("phone_number_send_unavailable"),
        ), mock.patch.object(
            protocol_register,
            "_hydrate_browser_driver_with_protocol_session_cookies",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_import_browser_driver_cookies_into_session",
            return_value=2,
        ), mock.patch.object(
            protocol_register,
            "_browser_try_submit_phone_number",
            side_effect=[False, False, True],
        ) as submit_phone_number, mock.patch.object(
            protocol_register,
            "_export_protocol_session_cookies",
            return_value=[{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
        ), mock.patch.object(
            protocol_register.time,
            "sleep",
            return_value=None,
        ), mock.patch.object(
            protocol_register.time,
            "monotonic",
            side_effect=[0.0, 0.1, 0.2, 0.3],
        ):
            result = protocol_register.submit_phone_number_for_resume(
                source_payload={"email": "user@example.com"},
                resume_context={
                    "continueUrl": "https://auth.openai.com/add-phone",
                    "sessionCookies": [{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
                    "oauth": {
                        "authUrl": "https://auth.openai.com/api/accounts/authorize?x=1",
                        "state": "state_123",
                        "codeVerifier": "verifier_123",
                        "redirectUri": "https://chatgpt.com/api/auth/callback/openai",
                    },
                },
                phone_number="+15551234567",
                explicit_proxy=None,
            )

        self.assertEqual(3, submit_phone_number.call_count)
        self.assertEqual("sms_verification", result["pageType"])

    def test_submit_phone_number_for_resume_returns_terminal_phone_result_without_browser(self) -> None:
        with mock.patch.object(
            protocol_register,
            "_submit_phone_number_via_protocol_session",
            return_value={
                "status": "phone_verification_terminal",
                "pageType": "add_phone",
                "resumeContext": {"continueUrl": "https://auth.openai.com/add-phone"},
                "phoneVerificationAttempted": True,
                "phoneVerificationTerminal": True,
                "phoneVerificationTerminalCode": "phone_number_in_use",
                "phoneVerificationTerminalMessage": "Phone number already in use.",
                "phoneVerificationTerminalStatusCode": 403,
            },
        ), mock.patch.object(
            protocol_register,
            "_load_protocol_browser_new_driver",
        ) as new_driver:
            result = protocol_register.submit_phone_number_for_resume(
                source_payload={"email": "user@example.com"},
                resume_context={
                    "continueUrl": "https://auth.openai.com/add-phone",
                    "sessionCookies": [{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
                    "oauth": {
                        "authUrl": "https://auth.openai.com/api/accounts/authorize?x=1",
                        "state": "state_123",
                        "codeVerifier": "verifier_123",
                        "redirectUri": "https://chatgpt.com/api/auth/callback/openai",
                    },
                },
                phone_number="+15551234567",
                explicit_proxy=None,
            )

        self.assertTrue(result["phoneVerificationAttempted"])
        self.assertTrue(result["phoneVerificationTerminal"])
        self.assertEqual("phone_number_in_use", result["phoneVerificationTerminalCode"])
        new_driver.assert_not_called()

    def test_submit_phone_verification_number_from_path_passes_through_terminal_phone_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = Path(tmp_dir) / "small-success.json"
            source_path.write_text('{"email":"user@example.com"}', encoding="utf-8")
            with mock.patch.object(
                protocol_phone_verification.protocol_register,
                "submit_phone_number_for_resume",
                return_value={
                    "status": "phone_verification_terminal",
                    "pageType": "add_phone",
                    "resumeContext": {"continueUrl": "https://auth.openai.com/add-phone"},
                    "phoneVerificationAttempted": True,
                    "phoneVerificationTerminal": True,
                    "phoneVerificationTerminalCode": "rate_limit_exceeded",
                    "phoneVerificationTerminalMessage": "Too many requests.",
                    "phoneVerificationTerminalStatusCode": 403,
                },
            ):
                result = protocol_phone_verification.submit_phone_verification_number_from_path(
                    source_path=str(source_path),
                    resume_context={"continueUrl": "https://auth.openai.com/add-phone"},
                    phone_number="+15551234567",
                )

        self.assertEqual("phone_verification_terminal", result["status"])
        self.assertTrue(result["phoneVerificationTerminal"])
        self.assertEqual("rate_limit_exceeded", result["phoneVerificationTerminalCode"])

    def test_requested_email_candidates_prefer_cloudflare_for_mail_aiaimimi(self) -> None:
        with mock.patch.object(
            protocol_runtime,
            "resolve_mailbox_provider_order",
            return_value=("moemail", "m2u"),
        ):
            candidates = protocol_runtime._requested_email_provider_candidates(
                "",
                "ambervoyage217803@mail.aiaimimi.com",
            )
        self.assertEqual(("cloudflare_temp_email", "moemail", "m2u"), candidates)

    def test_resolve_mailbox_recreates_same_cloudflare_address_when_recovery_not_supported(self) -> None:
        expected_mailbox = protocol_runtime.Mailbox(
            provider="cloudflare_temp_email",
            email="ambervoyage217803@mail.aiaimimi.com",
            ref="cloudflare_temp_email:cloudflare_temp_email_shared_default:demo",
            session_id="mailbox_123",
        )
        with mock.patch.object(protocol_runtime, "ensure_easy_email_env_defaults"), mock.patch.object(
            protocol_runtime,
            "_resolve_mailbox_ttl_seconds",
            return_value=90,
        ), mock.patch.object(
            protocol_runtime,
            "_requested_email_provider_candidates",
            return_value=("cloudflare_temp_email", "moemail"),
        ) as provider_candidates, mock.patch.object(
            protocol_runtime,
            "recover_mailbox_by_email",
            return_value={
                "recovered": False,
                "strategy": "not_supported",
                "detail": "provider_recovery_not_supported",
            },
        ) as recover_mailbox_by_email, mock.patch.object(
            protocol_runtime,
            "create_mailbox",
            return_value=expected_mailbox,
        ) as create_mailbox:
            mailbox = protocol_runtime.resolve_mailbox(
                preallocated_email="ambervoyage217803@mail.aiaimimi.com",
                preallocated_session_id=None,
                preallocated_mailbox_ref=None,
                recreate_preallocated_email=True,
            )
        provider_candidates.assert_called_once()
        recover_mailbox_by_email.assert_called_once()
        create_mailbox.assert_called_once()
        self.assertEqual("cloudflare_temp_email", create_mailbox.call_args.kwargs["provider"])
        self.assertEqual(expected_mailbox, mailbox)

    def test_ensure_easy_email_env_defaults_uses_docker_alias_inside_docker(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            protocol_runtime, "_running_in_docker", return_value=True
        ):
            protocol_runtime.ensure_easy_email_env_defaults()
            self.assertEqual("http://easy-email:8080", os.environ.get("MAILBOX_SERVICE_BASE_URL"))

    def test_protocol_oauth_defaults_mailbox_base_url_to_easy_email_inside_docker(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            protocol_oauth, "_default_easyemail_base_url", return_value="http://easy-email:8080"
        ), mock.patch.object(
            protocol_oauth, "_read_easyemail_server_api_key", return_value=""
        ):
            protocol_oauth._ensure_protocol_oauth_easy_runtime_defaults()
            self.assertEqual("http://easy-email:8080", os.environ.get("MAILBOX_SERVICE_BASE_URL"))

    def test_send_passwordless_login_otp_posts_authapi_login_endpoint(self) -> None:
        response = SimpleNamespace(status_code=200)
        with mock.patch.object(
            protocol_register,
            "_build_protocol_headers",
            return_value={"referer": protocol_register.LOGIN_PASSWORD_REFERER},
        ) as build_headers, mock.patch.object(
            protocol_register,
            "_session_request",
            return_value=response,
        ) as session_request, mock.patch.object(
            protocol_register,
            "_extract_page_type",
            return_value="email_otp_verification",
        ):
            result = protocol_register._send_passwordless_login_otp(
                mock.Mock(),
                explicit_proxy="http://proxy:8080",
                header_builder=SimpleNamespace(),
            )
        build_headers.assert_called_once_with(
            request_kind="",
            referer=protocol_register.LOGIN_PASSWORD_REFERER,
            sentinel_context=mock.ANY,
        )
        session_request.assert_called_once_with(
            mock.ANY,
            "POST",
            protocol_register.PASSWORDLESS_SEND_OTP_URL,
            explicit_proxy="http://proxy:8080",
            request_label="passwordless-login-send-otp",
            headers={"referer": protocol_register.LOGIN_PASSWORD_REFERER},
            timeout=45,
        )
        self.assertIs(result, response)

    def test_resolve_repair_oauth_entry_uses_passwordless_send_otp_fallback_when_password_missing(self) -> None:
        signup_response = SimpleNamespace()
        otp_response = SimpleNamespace()
        with mock.patch.object(
            protocol_register,
            "_extract_page_type",
            side_effect=["login_password", "email_otp_verification"],
        ), mock.patch.object(
            protocol_register,
            "_send_passwordless_login_otp",
            return_value=otp_response,
        ) as send_passwordless_login_otp, mock.patch.object(
            protocol_register,
            "_verify_login_password",
        ) as verify_login_password:
            oauth_entry_response, page_type, oauth_entry_referer = protocol_register._resolve_repair_oauth_entry(
                mock.Mock(),
                signup_response=signup_response,
                password="",
                mailbox_ref="cloudflare_temp_email:mailbox_123",
                explicit_proxy="http://proxy:8080",
                header_builder=SimpleNamespace(),
            )
        verify_login_password.assert_not_called()
        send_passwordless_login_otp.assert_called_once()
        self.assertIs(oauth_entry_response, otp_response)
        self.assertEqual("email_otp_verification", page_type)
        self.assertEqual(protocol_register.EMAIL_VERIFICATION_REFERER, oauth_entry_referer)

    def test_run_protocol_repair_once_returns_phone_verification_required_when_password_verify_hits_add_phone(self) -> None:
        session = mock.Mock()
        session.headers = {}
        oauth = SimpleNamespace(
            auth_url="https://auth.openai.com/api/accounts/authorize?x=1",
            state="state_123",
            code_verifier="verifier",
            redirect_uri="https://chatgpt.com/api/auth/callback/openai",
        )
        signup_response = SimpleNamespace(status_code=200)
        phone_response = SimpleNamespace(
            status_code=200,
            url="https://auth.openai.com/add-phone",
        )
        phone_response.json = lambda: {
            "page": {"type": "add_phone"},
            "continue_url": "https://auth.openai.com/add-phone",
        }

        with mock.patch.object(
            protocol_register,
            "generate_oauth_url",
            return_value=oauth,
        ), mock.patch.object(
            protocol_register,
            "get_mailbox_latest_message_id",
            return_value=0,
        ), mock.patch.object(
            protocol_register,
            "_session_request",
            side_effect=[SimpleNamespace(status_code=200), signup_response],
        ), mock.patch.object(
            protocol_register,
            "_get_session_cookie",
            return_value="did_123",
        ), mock.patch.object(
            protocol_register,
            "_build_protocol_headers",
            return_value={},
        ), mock.patch.object(
            protocol_register,
            "_resolve_repair_oauth_entry",
            return_value=(phone_response, "add_phone", protocol_register.LOGIN_PASSWORD_REFERER),
        ), mock.patch.object(
            protocol_register,
            "_export_protocol_session_cookies",
            return_value=[{"name": "a", "value": "b", "domain": ".openai.com", "path": "/"}],
        ):
            result = protocol_register.run_protocol_repair_once(
                auth_obj={
                    "email": "user@example.com",
                    "password": "pw",
                    "mailbox_ref": "mailtm:test",
                    "session_id": "mailbox_123",
                },
                existing_session=session,
                existing_sentinel_context=SimpleNamespace(user_agent="ua", device_id="device"),
            )

        self.assertTrue(result.phone_verification_required)
        self.assertEqual("add_phone", result.page_type)
        self.assertEqual("https://auth.openai.com/add-phone", result.final_url)
        self.assertEqual("repair_page_type", result.resume_context["context"])
        self.assertEqual("https://auth.openai.com/add-phone", result.resume_context["continueUrl"])
        self.assertEqual("device", result.resume_context["browser"]["deviceId"])
        self.assertEqual("state_123", result.resume_context["oauth"]["state"])


if __name__ == "__main__":
    unittest.main()
