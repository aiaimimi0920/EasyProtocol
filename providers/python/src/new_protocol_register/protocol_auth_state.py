"""Bind browser-observed account metadata to the current server session cookie."""

from __future__ import annotations

import base64
import json


def decode_minimized_session(cookie: str, *, client_id: str) -> dict[str, object]:
    parts = str(cookie or '').split('.')
    if len(parts) != 3 or not all(parts):
        return {}
    try:
        header, payload = (
            json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
            for part in parts[:2]
        )
    except (ValueError, TypeError, UnicodeError):
        return {}
    if not isinstance(header, dict) or header.get('alg') != 'ES256' or header.get('typ') != 'JWT':
        return {}
    if not isinstance(payload, dict) or payload.get('openai_client_id') != client_id:
        return {}
    if any(not isinstance(payload.get(key), str) or not payload[key].strip()
           for key in ('session_id', 'auth_session_logging_id')):
        return {}
    # Metadata only: the opaque cookie still goes to the server for validation.
    return payload


def bind_browser_auth_session(
    cookie: str, payload: object, *, email: str, client_id: str,
) -> dict[str, object]:
    claims = decode_minimized_session(cookie, client_id=client_id)
    if not claims or not isinstance(payload, dict):
        return {}
    if any(payload.get(key) != claims[key] for key in (
        'session_id', 'auth_session_logging_id', 'openai_client_id',
    )):
        return {}
    expected = str(email or '').strip().lower()
    username = payload.get('username')
    if (
        not expected or not isinstance(username, dict)
        or str(username.get('value') or '').strip().lower() != expected
        or str(payload.get('email') or '').strip().lower() != expected
        or payload.get('original_screen_hint') != 'login_or_signup'
    ):
        return {}
    return dict(payload)
