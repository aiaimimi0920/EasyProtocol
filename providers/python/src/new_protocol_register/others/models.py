from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


PLATFORM_LOGIN_URL = "https://platform.openai.com/login"


@dataclass(frozen=True)
class PlatformProtocolRegistrationResult:
    outcome: str
    email: str
    password: str
    email_service_provider: str
    mailbox_provider: str
    mailbox_access_key: str
    mailbox_ref: str
    mailbox_session_id: str
    first_name: str
    last_name: str
    birthdate: str
    page_type: str
    final_url: str
    storage_path: str
    platform_url: str = PLATFORM_LOGIN_URL
    final_stage: str = ""
    account_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SecondOAuthResult:
    email: str
    account_id: str
    storage_path: str
    auth: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProtocolOAuthResult:
    email: str
    account_id: str
    storage_path: str
    auth: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
