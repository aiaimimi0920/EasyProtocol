from __future__ import annotations

import base64
import json
import itertools
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1] / 'providers' / 'python'
for path in (ROOT / 'src', ROOT / 'python_shared' / 'src'):
    sys.path.insert(0, str(path))

from new_protocol_register import protocol_small_success as flow
from protocol_runtime import protocol_register as browser_runtime


class MinimizedAuthSessionTests(unittest.TestCase):
    email = 'fixture@example.test'

    def payload(self):
        return {
            'session_id': 'fixture-session',
            'auth_session_logging_id': 'fixture-log',
            'openai_client_id': flow._PLATFORM_AUTH0_CLIENT_ID,
            'username': {'value': self.email}, 'email': self.email,
            'original_screen_hint': 'login_or_signup',
        }

    def cookie(self, **changes):
        claims = {key: self.payload()[key] for key in (
            'session_id', 'auth_session_logging_id', 'openai_client_id')}
        claims.update(changes)
        encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
        return encode({'alg': 'ES256', 'typ': 'JWT'}) + '.' + encode(claims) + '.fixture-signature'

    def recover(self, payload=None, status=200, *, real_import=False, loader_delay_reads=0):
        cookie = self.cookie()
        session = flow.requests.Session()
        self.addCleanup(session.close)
        driver = mock.Mock()
        driver.current_url = 'https://auth.openai.com/email-verification'
        driver.page_source = '<html>Check your email</html>'
        driver.get_cookies.return_value = [{
            'name': 'auth-session-minimized', 'value': cookie,
            'domain': 'auth.openai.com', 'path': '/', 'secure': True,
        }]
        driver.execute_cdp_cmd.return_value = {'success': True}
        auth_url = 'https://auth.openai.com/authorize?login_hint=fixture%40example.test'

        def navigate(url):
            driver.current_url = 'https://auth.openai.com/email-verification' if url == auth_url else url

        def execute_script(script):
            nonlocal loader_delay_reads
            if 'performance.getEntriesByType' in script:
                return status
            if '__reactRouterContext' in script:
                if loader_delay_reads:
                    loader_delay_reads -= 1
                    return None
                if driver.current_url != 'https://auth.openai.com/email-verification':
                    return None
                return payload if payload is not None else self.payload()
            return 'fixture-UA'

        driver.get.side_effect = navigate
        driver.execute_script.side_effect = execute_script
        context = SimpleNamespace(device_id='fixture-device', user_agent='fixture-UA')
        original_import = flow._import_browser_driver_cookies_into_session

        def import_cookies(*args, **kwargs):
            if real_import:
                return original_import(*args, **kwargs)
            session.cookies.set('auth-session-minimized', cookie, domain='auth.openai.com')
            return 1

        with mock.patch.object(browser_runtime, '_load_protocol_browser_new_driver',
                               return_value=mock.Mock(return_value=(driver, None))), mock.patch.object(
            flow, '_import_browser_driver_cookies_into_session', side_effect=import_cookies,
        ) as imported, mock.patch.object(flow, '_clone_protocol_sentinel_context', return_value=context), mock.patch.object(
            flow.time, 'sleep',
        ), mock.patch.object(flow.time, 'monotonic', side_effect=itertools.count()):
            result = flow._recover_platform_auth0_authorize_in_browser(
                session=session, auth_url=auth_url,
                device_id='fixture-device', sentinel_context=context, explicit_proxy='http://fixture-proxy:8080',
            )
        driver.quit.assert_called_once()
        if real_import:
            driver.get.assert_called_once_with(auth_url)
        return result, session, imported.call_count

    def test_real_cookie_import_preserves_the_verified_document_and_identity(self):
        result, session, _ = self.recover(real_import=True)
        self.assertIsNotNone(result[1])
        self.assertEqual('https://auth.openai.com/email-verification', result[1].url)
        self.assertEqual(self.email, flow._decode_current_auth_session_payload(session)['email'])

    def test_loader_can_hydrate_after_document_navigation_completes(self):
        result, session, _ = self.recover(real_import=True, loader_delay_reads=2)
        self.assertIsNotNone(result[1])
        self.assertEqual(self.email, flow._decode_current_auth_session_payload(session)['email'])

    def test_missing_loader_remains_a_failure_after_bounded_wait(self):
        result, session, _ = self.recover(real_import=True, loader_delay_reads=100)
        self.assertIsNone(result[1])
        self.assertEqual({}, flow._decode_current_auth_session_payload(session))

    def test_verified_browser_state_supports_minimized_cookie_without_forging_legacy_cookie(self):
        result, session, imports = self.recover()
        self.assertIsNotNone(result[1])
        self.assertEqual(200, result[1].status_code)
        self.assertEqual(self.cookie(), flow._login_session_cookie(session))
        self.assertEqual(self.email, flow._decode_current_auth_session_payload(session)['username']['value'])
        self.assertFalse(flow._get_session_cookie(session, 'login_session'))
        self.assertEqual(1, imports)
        flow._validate_platform_authorize_response(session=session, response=result[1])

    def test_server_state_must_match_cookie_and_requested_identity(self):
        for field, wrong in (
            ('session_id', 'other-session'), ('auth_session_logging_id', 'other-log'),
            ('openai_client_id', 'other-client'), ('email', 'other@example.test'),
            ('username', {'value': 'other@example.test'}), ('original_screen_hint', 'login'),
        ):
            with self.subTest(field=field):
                payload = self.payload()
                payload[field] = wrong
                result, session, _ = self.recover(payload)
                self.assertIsNone(result[1])
                self.assertEqual({}, flow._decode_current_auth_session_payload(session))

    def test_cookie_rotation_invalidates_cached_identity(self):
        _, session, _ = self.recover()
        self.assertTrue(flow._decode_current_auth_session_payload(session))
        session.cookies.set('auth-session-minimized', self.cookie(session_id='new-session'), domain='auth.openai.com')
        self.assertEqual({}, flow._decode_current_auth_session_payload(session))

    def test_challenge_cannot_be_accepted_with_minimized_cookie(self):
        result, _, imports = self.recover(status=403)
        self.assertIsNone(result[1])
        self.assertEqual(0, imports)

    def test_wrong_client_or_malformed_cookie_is_not_a_login_session(self):
        session = flow.requests.Session()
        self.addCleanup(session.close)
        for cookie in (self.cookie(openai_client_id='wrong'), self.cookie(session_id=''), 'invalid', 'e30.e30.signature'):
            with self.subTest(cookie=cookie):
                session.cookies.set('auth-session-minimized', cookie, domain='auth.openai.com')
                self.assertEqual('', flow._login_session_cookie(session))

    def test_legacy_cookie_still_takes_precedence(self):
        session = flow.requests.Session()
        self.addCleanup(session.close)
        session.cookies.set('login_session', 'legacy', domain='auth.openai.com')
        session.cookies.set('auth-session-minimized', self.cookie(), domain='auth.openai.com')
        self.assertEqual('legacy', flow._login_session_cookie(session))


if __name__ == '__main__':
    unittest.main()
