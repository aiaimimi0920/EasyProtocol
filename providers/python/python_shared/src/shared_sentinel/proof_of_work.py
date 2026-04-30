"""
OpenAI Sentinel requirements / enforcement token 生成器。

这一版不再沿用旧的 18 字段 + SHA3-512 简化实现，而是按当前参考实现
对齐到 20260219f9f6 时代的 Sentinel 画像：

- requirements token: 25 字段环境数组，`gAAAAAC...~S`
- enforcement/proof token: 25 字段环境数组 + mixed FNV 求解，`gAAAAAB...`

外部接口保持不变：
- get_pow_token(...)
- generate_proof_token(...)
"""

from __future__ import annotations

import base64
import json
import os
import random
import string
import time
import uuid
from datetime import datetime, timedelta, timezone

from .config import DEFAULT_USER_AGENT, get_data_build

def _local_timezone_offset_minutes() -> int:
    now = datetime.now().astimezone()
    delta = now.utcoffset() or timedelta()
    return int(delta.total_seconds() // 60)


DEFAULT_SENTINEL_SDK_URL = (
    os.environ.get("OPENAI_SENTINEL_SDK_URL")
    or "https://sentinel.openai.com/backend-api/sentinel/sdk.js"
).strip()
DEFAULT_LANGUAGE = (os.environ.get("OPENAI_SENTINEL_LANGUAGE") or "en").strip() or "en"
DEFAULT_LANGUAGES_JOIN = (
    os.environ.get("OPENAI_SENTINEL_LANGUAGES") or "en"
).strip() or "en"
DEFAULT_PLATFORM = (os.environ.get("OPENAI_SENTINEL_PLATFORM") or "Win32").strip() or "Win32"
DEFAULT_VENDOR = (os.environ.get("OPENAI_SENTINEL_VENDOR") or "Google Inc.").strip() or "Google Inc."
DEFAULT_TIMEZONE_OFFSET_MIN = int(
    os.environ.get("OPENAI_SENTINEL_TIMEZONE_OFFSET_MIN") or _local_timezone_offset_minutes()
)
DEFAULT_HEAP_LIMIT = int(os.environ.get("OPENAI_SENTINEL_HEAP_LIMIT") or "4294967296")
MAX_ATTEMPTS = max(1, int(os.environ.get("OPENAI_SENTINEL_MAX_ATTEMPTS") or "500000"))

_NAVIGATOR_PROBE_CHOICES = (
    "canLoadAdAuctionFencedFrame−function canLoadAdAuctionFencedFrame() { [native code] }",
    "hardwareConcurrency−12",
    f"language−{DEFAULT_LANGUAGE}",
    f"languages−{DEFAULT_LANGUAGES_JOIN}",
    f"platform−{DEFAULT_PLATFORM}",
    f"vendor−{DEFAULT_VENDOR}",
)

_DOCUMENT_PROBE_CHOICES = (
    "_reactListeningx9ytk7ovr7",
    "_reactListeningcfilawjnerp",
    "_reactListening9ne2dfo1i47",
    "_reactListening410nzwhan2a",
)

_WINDOW_PROBE_CHOICES = (
    "onmouseover",
    "ondragend",
    "onbeforematch",
    "__next_f",
    "__oai_cached_session",
)

_WINDOW_FLAG_CHOICES = (
    (0, 0, 0, 0, 0, 0, 0),
)


def _timezone_label(offset_minutes: int, language: str) -> str:
    if offset_minutes == 480:
        return "中国标准时间"
    if offset_minutes == 420:
        return "Mountain Standard Time"
    if offset_minutes == 0:
        return "Coordinated Universal Time"
    return ""


def _date_string(*, timezone_offset_min: int, language: str) -> str:
    tz = timezone(timedelta(minutes=timezone_offset_min))
    now = datetime.now(tz)
    label = _timezone_label(timezone_offset_min, language)
    sign = "+" if timezone_offset_min >= 0 else "-"
    abs_minutes = abs(timezone_offset_min)
    hours = abs_minutes // 60
    minutes = abs_minutes % 60
    base = now.strftime("%a %b %d %Y %H:%M:%S")
    if label:
        return f"{base} GMT{sign}{hours:02d}{minutes:02d} ({label})"
    return f"{base} GMT{sign}{hours:02d}{minutes:02d}"


def _default_screen_sum(screen: int | None) -> int:
    return int(screen) if screen is not None else 4000


def _default_core(core: int | None) -> int:
    return int(core) if core is not None else 12


def _navigator_probe(*, language: str, languages_join: str, platform: str, vendor: str, core: int) -> str:
    return "canLoadAdAuctionFencedFrame−function canLoadAdAuctionFencedFrame() { [native code] }"


def _document_probe(session_id: str) -> str:
    suffix = "".join(ch for ch in (session_id or "").lower() if ch.isalnum())
    if not suffix:
        suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10))
    suffix = suffix[:10]
    if suffix:
        return f"_reactListening{suffix}"
    return random.choice(_DOCUMENT_PROBE_CHOICES)


def _window_probe() -> str:
    return "onmouseover"


def _random_entropy() -> float:
    return random.randint(10000, 99999) / 100000


def _random_time_origin_ms() -> float:
    delta_ms = random.randint(3000, 15000)
    return int(time.time() * 1000) - delta_ms


def _random_window_flags() -> tuple[int, int, int, int, int, int, int]:
    return random.choice(_WINDOW_FLAG_CHOICES)


def _random_performance_now() -> float:
    base = random.uniform(8500.0, 10500.0)
    return round(base + random.random(), 12)


def _must_b64_json(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(body).decode("ascii")


def _mixed_fnv(text: str) -> str:
    e = 2166136261
    for ch in text:
        e ^= ord(ch)
        e = (e * 16777619) & 0xFFFFFFFF
    e ^= (e >> 16)
    e = (e * 2246822507) & 0xFFFFFFFF
    e ^= (e >> 13)
    e = (e * 3266489909) & 0xFFFFFFFF
    e ^= (e >> 16)
    return f"{e:08x}"


def build_config(
    user_agent: str | None = None,
    core: int | None = None,
    screen: int | None = None,
    data_build: str | None = None,
    *,
    for_pow: bool = False,
    nonce: int = 1,
    elapsed_ms: int = 0,
    session_id: str | None = None,
    language: str | None = None,
    languages_join: str | None = None,
    timezone_offset_min: int | None = None,
    performance_now: float | None = None,
    time_origin: float | None = None,
    script_url: str | None = None,
    navigator_probe: str | None = None,
    document_probe: str | None = None,
    window_probe: str | None = None,
    window_flags: tuple[int, int, int, int, int, int, int] | None = None,
) -> list[object]:
    actual_user_agent = (user_agent or DEFAULT_USER_AGENT).strip()
    actual_core = _default_core(core)
    actual_screen = _default_screen_sum(screen)
    actual_language = (language or DEFAULT_LANGUAGE).strip() or DEFAULT_LANGUAGE
    actual_languages_join = (languages_join or DEFAULT_LANGUAGES_JOIN).strip() or DEFAULT_LANGUAGES_JOIN
    actual_timezone_offset_min = (
        DEFAULT_TIMEZONE_OFFSET_MIN if timezone_offset_min is None else int(timezone_offset_min)
    )
    actual_session_id = (session_id or "").strip() or str(uuid.uuid4())
    actual_time_origin = float(time_origin) if time_origin is not None else _random_time_origin_ms()
    actual_performance_now = float(performance_now) if performance_now is not None else _random_performance_now()
    resolved_navigator_probe = str(navigator_probe or "").strip() or _navigator_probe(
        language=actual_language,
        languages_join=actual_languages_join,
        platform=DEFAULT_PLATFORM,
        vendor=DEFAULT_VENDOR,
        core=actual_core,
    )
    resolved_document_probe = str(document_probe or "").strip() or _document_probe(actual_session_id)
    resolved_window_probe = str(window_probe or "").strip() or _window_probe()
    resolved_window_flags = window_flags if window_flags is not None else _random_window_flags()
    resolved_script_url = str(script_url or "").strip() or DEFAULT_SENTINEL_SDK_URL

    field3: object = _random_entropy()
    field9: object = _random_entropy()
    if for_pow:
        field3 = int(nonce)
        field9 = int(elapsed_ms)

    # `data_build` 保留在参数中只是为了兼容现有调用方签名；新版 token 画像里并不把它
    # 直接放进数组，而是把 SDK URL 放在 [5]，这与参考实现保持一致。
    _ = data_build or get_data_build()

    return [
        actual_screen,
        _date_string(timezone_offset_min=actual_timezone_offset_min, language=actual_language),
        DEFAULT_HEAP_LIMIT,
        field3,
        actual_user_agent,
        resolved_script_url,
        None,
        actual_language,
        actual_languages_join,
        field9,
        resolved_navigator_probe,
        resolved_document_probe,
        resolved_window_probe,
        actual_performance_now,
        actual_session_id,
        "",
        actual_core,
        actual_time_origin,
        *resolved_window_flags,
    ]


def solve_challenge(seed: str, difficulty: str, config: list[object]) -> tuple[str, bool]:
    diff = (difficulty or "0").strip() or "0"
    started_at = time.perf_counter()
    for nonce in range(MAX_ATTEMPTS):
        working = list(config)
        working[3] = nonce
        working[9] = int((time.perf_counter() - started_at) * 1000)
        answer = _must_b64_json(working)
        if _mixed_fnv((seed or "") + answer)[: len(diff)] <= diff:
            return answer + "~S", True
    return "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4Dtimeout", False


def generate_proof_token(
    required: bool,
    seed: str = "",
    difficulty: str = "",
    user_agent: str | None = None,
    core: int | None = None,
    screen: int | None = None,
    data_build: str | None = None,
    session_id: str | None = None,
    language: str | None = None,
    languages_join: str | None = None,
    timezone_offset_min: int | None = None,
    performance_now: float | None = None,
    time_origin: float | None = None,
    script_url: str | None = None,
    navigator_probe: str | None = None,
    document_probe: str | None = None,
    window_probe: str | None = None,
    window_flags: tuple[int, int, int, int, int, int, int] | None = None,
) -> str:
    if required:
        config = build_config(
            user_agent=user_agent,
            core=core,
            screen=screen,
            data_build=data_build,
            for_pow=True,
            session_id=session_id,
            language=language,
            languages_join=languages_join,
            timezone_offset_min=timezone_offset_min,
            performance_now=performance_now,
            time_origin=time_origin,
            script_url=script_url,
            navigator_probe=navigator_probe,
            document_probe=document_probe,
            window_probe=window_probe,
            window_flags=window_flags,
        )
        answer, solved = solve_challenge(seed, difficulty, config)
        if not solved:
            print(
                f"[shared_sentinel] WARNING: PoW solve failed after {MAX_ATTEMPTS} attempts "
                f"seed_len={len(seed or '')} difficulty={difficulty} — "
                f"returning best-effort token but server will likely reject it"
            )
        return "gAAAAAB" + answer

    config = build_config(
        user_agent=user_agent,
        core=core,
        screen=screen,
        data_build=data_build,
        for_pow=False,
        nonce=0,
        elapsed_ms=0,
        session_id=session_id,
        language=language,
        languages_join=languages_join,
        timezone_offset_min=timezone_offset_min,
        performance_now=performance_now,
        time_origin=time_origin,
        script_url=script_url,
        navigator_probe=navigator_probe,
        document_probe=document_probe,
        window_probe=window_probe,
        window_flags=window_flags,
    )
    return "gAAAAAB" + _must_b64_json(config) + "~S"


def generate_requirements_token(
    user_agent: str | None = None,
    core: int | None = None,
    screen: int | None = None,
    data_build: str | None = None,
    session_id: str | None = None,
    language: str | None = None,
    languages_join: str | None = None,
    timezone_offset_min: int | None = None,
    performance_now: float | None = None,
    time_origin: float | None = None,
    script_url: str | None = None,
    navigator_probe: str | None = None,
    document_probe: str | None = None,
    window_probe: str | None = None,
    window_flags: tuple[int, int, int, int, int, int, int] | None = None,
) -> str:
    config = build_config(
        user_agent=user_agent,
        core=core,
        screen=screen,
        data_build=data_build,
        for_pow=False,
        nonce=1,
        elapsed_ms=random.randint(5, 50),
        session_id=session_id,
        language=language,
        languages_join=languages_join,
        timezone_offset_min=timezone_offset_min,
        performance_now=performance_now,
        time_origin=time_origin,
        script_url=script_url,
        navigator_probe=navigator_probe,
        document_probe=document_probe,
        window_probe=window_probe,
        window_flags=window_flags,
    )
    config[3] = 1
    config[9] = random.randint(5, 50)
    return "gAAAAAC" + _must_b64_json(config) + "~S"


def get_pow_token(
    user_agent: str | None = None,
    core: int | None = None,
    screen: int | None = None,
    data_build: str | None = None,
    session_id: str | None = None,
    language: str | None = None,
    languages_join: str | None = None,
    timezone_offset_min: int | None = None,
    performance_now: float | None = None,
    time_origin: float | None = None,
    script_url: str | None = None,
    navigator_probe: str | None = None,
    document_probe: str | None = None,
    window_probe: str | None = None,
    window_flags: tuple[int, int, int, int, int, int, int] | None = None,
) -> str:
    return generate_requirements_token(
        user_agent=user_agent,
        core=core,
        screen=screen,
        data_build=data_build,
        session_id=session_id,
        language=language,
        languages_join=languages_join,
        timezone_offset_min=timezone_offset_min,
        performance_now=performance_now,
        time_origin=time_origin,
        script_url=script_url,
        navigator_probe=navigator_probe,
        document_probe=document_probe,
        window_probe=window_probe,
        window_flags=window_flags,
    )


if __name__ == "__main__":
    print("=" * 60)
    print("OpenAI Sentinel Proof Token Generator")
    print("=" * 60)
    requirements = generate_requirements_token()
    proof = generate_proof_token(True, "test_seed_value", "0fffff")
    print(f"requirements: {requirements[:100]}...")
    print(f"proof: {proof[:100]}...")
