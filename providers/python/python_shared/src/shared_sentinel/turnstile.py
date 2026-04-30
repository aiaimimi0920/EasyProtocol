"""
OpenAI Turnstile DX VM solver.

这版实现是对旧 Python VM 的增强版：
- 支持 requirements token profile 解析
- 增加更多 opcode
- 提供更接近浏览器的 window / navigator / document / localStorage 环境
"""

from __future__ import annotations

import base64
import json
import math
import os
import random
import re
import shutil
import time
from dataclasses import dataclass
from typing import Any

Q = 9
W = 10
K = 16
OK = 3
ERR = 4
CB = 30
ORDERED = "__ordered_keys__"
PROTO = "__prototype__"
DEFAULT_TURNSTILE_SOLVER_MODE = "go-source-preferred"


def _turnstile_min_token_length() -> int:
    raw = str(os.environ.get("TURNSTILE_MIN_TOKEN_LENGTH") or "64").strip()
    try:
        value = int(raw)
    except Exception:
        value = 64
    return max(1, value)


def _turnstile_decoded_preview(token: str, limit: int = 32) -> str:
    raw = str(token or "").strip()
    if not raw:
        return ""
    try:
        padded = raw + "=" * ((4 - (len(raw) % 4)) % 4)
        decoded = base64.b64decode(padded.encode("ascii"), validate=False)
        return decoded.decode("latin-1", errors="replace")[:limit]
    except Exception:
        return ""


def _normalize_turnstile_token(token: str, *, source: str) -> str:
    raw = str(token or "").strip()
    if not raw:
        return ""
    min_len = _turnstile_min_token_length()
    if len(raw) >= min_len:
        return raw
    decoded_preview = _turnstile_decoded_preview(raw)
    if decoded_preview:
        print(
            f"[turnstile] rejecting short token source={source} "
            f"t_len={len(raw)} min_len={min_len} decoded_preview={decoded_preview!r}"
        )
    else:
        print(
            f"[turnstile] rejecting short token source={source} "
            f"t_len={len(raw)} min_len={min_len} preview={raw[:32]!r}"
        )
    return ""


def _resolve_turnstile_solver_command(solver_bin: str) -> tuple[list[str] | None, str]:
    normalized = str(solver_bin or "").strip()
    if not normalized:
        return None, "missing"
    if os.name == "nt" or not normalized.lower().endswith(".exe"):
        return [normalized], "native"
    allow_wine = str(os.environ.get("TURNSTILE_SOLVER_ALLOW_WINE") or "1").strip().lower()
    if allow_wine in {"0", "false", "no", "off"}:
        return None, "wine-disabled"
    wine_bin = (
        str(os.environ.get("TURNSTILE_WINE_BIN") or "").strip()
        or shutil.which("wine64")
        or shutil.which("wine")
        or ""
    ).strip()
    if not wine_bin:
        return None, "wine-missing"
    return [wine_bin, normalized], "wine"


def _default_turnstile_solver_path(solver_dir: str) -> str:
    normalized_dir = str(solver_dir or "").strip()
    if not normalized_dir:
        return ""
    candidates: list[str]
    if os.name == "nt":
        candidates = [
            os.path.join(normalized_dir, "turnstile_solver.exe"),
            os.path.join(normalized_dir, "turnstile_solver"),
            os.path.join(normalized_dir, "turnstile_solver_linux_amd64"),
        ]
    else:
        candidates = [
            os.path.join(normalized_dir, "turnstile_solver"),
            os.path.join(normalized_dir, "turnstile_solver_linux_amd64"),
            os.path.join(normalized_dir, "turnstile_solver.exe"),
        ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return candidates[0]


def _default_turnstile_solver_source_dir(solver_dir: str) -> str:
    normalized_dir = str(solver_dir or "").strip()
    if not normalized_dir:
        return ""
    return os.path.join(os.path.dirname(normalized_dir), "turnstile_solver_go")


def _resolve_turnstile_solver_source_command(source_dir: str) -> tuple[list[str] | None, str]:
    normalized = str(source_dir or "").strip()
    if not normalized:
        return None, "missing"
    if not os.path.isdir(normalized):
        return None, "source-dir-missing"
    if not os.path.isfile(os.path.join(normalized, "go.mod")):
        return None, "go-mod-missing"
    if not os.path.isfile(os.path.join(normalized, "main.go")):
        return None, "main-go-missing"
    go_bin = (
        str(os.environ.get("TURNSTILE_GO_BIN") or "").strip()
        or shutil.which("go")
        or ""
    ).strip()
    if not go_bin:
        return None, "go-missing"
    return [go_bin, "run", "."], "go-source"


def _parse_turnstile_solver_stdout(stdout_text: str) -> dict[str, Any]:
    raw = str(stdout_text or "").strip()
    if not raw:
        return {}
    candidates = [line.strip() for line in raw.splitlines() if line.strip()]
    if raw not in candidates:
        candidates.append(raw)
    for candidate in reversed(candidates):
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _profile_screen_size(profile: "Profile") -> tuple[int, int]:
    width = 2048
    height = 1152
    try:
        screen_sum = int(getattr(profile, "screen_sum", 0) or 0)
    except Exception:
        screen_sum = 0
    if screen_sum > 2000:
        width = int(round(screen_sum * 0.64))
        height = max(1, int(screen_sum - width))
    return width, height


def _go_solver_session_payload(profile: "Profile") -> dict[str, Any]:
    width, height = _profile_screen_size(profile)
    session_id = str(profile.session_id or "").strip()
    navigator_probe = str(profile.navigator_probe or "").strip()
    window_probe = str(profile.window_probe or "").strip()
    script_url = str(profile.script_url or "").strip()
    if "backend-api/sentinel/sdk.js" in script_url and "canLoadAdAuctionFencedFrame" in navigator_probe and window_probe == "onmouseover":
        math_random_sequence = [
            0.4605362141104189,
            0.028267996208479085,
            0.8557543243431462,
            0.23349777990427056,
            0.4226912173790277,
            0.09626857573723901,
            0.9509451133394776,
            0.441050832097446,
            0.5674010844293004,
            0.797939797522652,
            0.6922512921331013,
            0.7554222222130249,
            0.4801700821850855,
            0.25881034541941506,
            0.548530546438854,
            0.3397034305336585,
            0.039451389721142704,
            0.06545064347719454,
            0.3831767605442624,
            0.04572066729066315,
            0.0131306381659555,
            0.46710616352376055,
            0.29021624758778375,
            0.22499199438187312,
            0.529372166999655,
            0.5743375773817718,
            0.8970422893157702,
            0.35654187744850285,
            0.6874367886224536,
            0.5438569977181711,
            0.366475404911224,
            0.8641317127616611,
            0.9238979318726256,
            0.4630274441079013,
            0.9816207377132188,
            0.4305906409725031,
            0.4553676854639087,
            0.5236864682690924,
            0.9364020963085581,
            0.305148107948769,
            0.3171335091661196,
            0.24332860241554233,
            0.6197534569599558,
            0.3977806509796905,
            0.6763085136237856,
            0.2991222090740826,
            0.9024926701254776,
            0.6468956716350268,
            0.5305781696246805,
            0.16420051836770833,
            0.6170716304559821,
            0.35561095404008214,
            0.7194541707092491,
            0.2700468338968742,
            0.059337199554503894,
            0.04791591952615237,
            0.09212266536976166,
            0.9500980274617389,
            0.05248293595820408,
            0.41902152498228395,
            0.5091779098944226,
            0.6260262071413268,
            0.6754439105299099,
            0.7773240550449021,
            0.23173942265871872,
            0.12332056265174773,
            0.8820871126701837,
            0.7253293732302817,
            0.8429261445770918,
            0.8255014983977457,
            0.6563957238095114,
            0.49831522493039504,
            0.466077894602943,
            0.8357769342068614,
            0.8745401495855023,
            0.2729181368748985,
            0.33955168121417745,
            0.18283833644932168,
            0.7761922100086608,
            0.6955995960689297,
        ]
    elif "productSub" in navigator_probe and window_probe == "close":
        math_random_sequence = [
            0.6320530382018646,
            0.7755427670893457,
            0.3978074664520963,
            0.3069699707704624,
        ]
    else:
        math_random_sequence = [
            0.5948783272252895,
            0.1623084345896082,
            0.3978074664520963,
            0.3069699707704624,
        ]
    return {
        "deviceID": session_id or "bb13486d-db99-4547-81a4-a8f2a6351be9",
        "userAgent": str(profile.user_agent or "").strip(),
        "screenWidth": width,
        "screenHeight": height,
        "heapLimit": int(profile.heap_limit or 4294967296),
        "hardwareConcurrency": int(profile.hardware_concurrency or 12),
        "language": str(profile.language or "en").strip() or "en",
        "languagesJoin": str(profile.languages_join or "en").strip() or "en",
        "persona": {
            "platform": "Win32",
            "vendor": "Google Inc.",
            "sessionID": session_id,
            "timeOrigin": float(profile.time_origin or 0.0),
            "windowFlags": [0, 0, 0, 0, 0, 0, 0],
            "windowFlagsSet": True,
            "mathRandomSequence": math_random_sequence,
            "requirementsScriptURL": str(profile.script_url or "").strip(),
            "navigatorProbe": str(profile.navigator_probe or "").strip(),
            "documentProbe": str(profile.document_probe or "").strip(),
            "windowProbe": str(profile.window_probe or "").strip(),
            "performanceNow": float(profile.performance_now or 9272.400000000373),
            "requirementsElapsed": 0.0,
        },
    }


def _b64d(value: str) -> str:
    return base64.b64decode(value).decode("latin-1")


def _b64e(value: str) -> str:
    return base64.b64encode(value.encode("latin-1", errors="ignore")).decode("ascii")


def _xor(data: str, key: str) -> str:
    if not key:
        return data
    db = data.encode("latin-1", errors="ignore")
    kb = key.encode("latin-1", errors="ignore")
    out = bytearray(len(db))
    for idx, byte in enumerate(db):
        out[idx] = byte ^ kb[idx % len(kb)]
    return out.decode("latin-1", errors="ignore")


def _reg(value: Any) -> str:
    if value is None:
        return "nil"
    if isinstance(value, str):
        return f"s:{value}"
    if isinstance(value, bool):
        return f"n:{1 if value else 0}"
    if isinstance(value, int):
        return f"n:{value}"
    if isinstance(value, float):
        return f"n:{int(value) if math.trunc(value) == value else value:g}"
    return f"x:{value}"


def _idx(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.trunc(value) == value:
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except Exception:
            return -1
    return -1


def _ordered_map(value: dict[str, Any] | None, keys: list[str]) -> dict[str, Any]:
    out = dict(value or {})
    ordered: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key not in seen:
            ordered.append(key)
            seen.add(key)
    for key in sorted(k for k in out.keys() if k not in seen and not k.startswith("__")):
        ordered.append(key)
    out[ORDERED] = ordered
    return out


def _keys_of(value: dict[str, Any]) -> list[str]:
    ordered = value.get(ORDERED)
    if isinstance(ordered, list):
        return [str(item) for item in ordered]
    return sorted(key for key in value.keys() if not str(key).startswith("__"))


def _json_stringify(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return "null"
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(_json_stringify(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False) + ":" + _json_stringify(value.get(key))
            for key in _keys_of(value)
            if not str(key).startswith("__")
        ) + "}"
    return json.dumps(value, ensure_ascii=False)


@dataclass(frozen=True)
class Profile:
    screen_sum: int = 4000
    heap_limit: int = 4294967296
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    )
    script_url: str = "https://sentinel.openai.com/backend-api/sentinel/sdk.js"
    language: str = "en"
    languages_join: str = "en"
    navigator_probe: str = "canLoadAdAuctionFencedFrame−function canLoadAdAuctionFencedFrame() { [native code] }"
    document_probe: str = "onvisibilitychange"
    window_probe: str = "onmouseover"
    performance_now: float = 9272.400000000373
    session_id: str = ""
    hardware_concurrency: int = 12
    time_origin: float = 0.0


def _parse_profile(requirements_token: str) -> Profile:
    token = str(requirements_token or "").strip().removeprefix("gAAAAAC").removesuffix("~S")
    if not token:
        return Profile()
    try:
        fields = json.loads(base64.b64decode(token).decode("utf-8"))
    except Exception:
        return Profile()
    if not isinstance(fields, list) or len(fields) < 18:
        return Profile()
    return Profile(
        screen_sum=int(float(fields[0] or 4000)),
        heap_limit=int(float(fields[2] or 4294967296)),
        user_agent=str(fields[4] or Profile.user_agent),
        script_url=str(fields[5] or Profile.script_url),
        language=str(fields[7] or "en"),
        languages_join=str(fields[8] or "en"),
        navigator_probe=str(fields[10] or Profile.navigator_probe),
        document_probe=str(fields[11] or "onvisibilitychange"),
        window_probe=str(fields[12] or "onmouseover"),
        performance_now=float(fields[13] or 9272.400000000373),
        session_id=str(fields[14] or ""),
        hardware_concurrency=int(float(fields[16] or 12)),
        time_origin=float(fields[17] or 0.0),
    )


def _chrome_version_hints(user_agent: str) -> tuple[str, str]:
    normalized = str(user_agent or "").strip()
    if not normalized:
        return "147", "147.0.0.0"
    match = re.search(r"Chrome/([0-9]+(?:\.[0-9]+){0,3})", normalized)
    if not match:
        return "147", "147.0.0.0"
    full = str(match.group(1) or "").strip() or "147.0.0.0"
    major = full.split(".", 1)[0].strip() or "147"
    if "." not in full:
        full = f"{full}.0.0.0"
    return major, full


class _RegRef:
    def __init__(self, solver: "_Solver") -> None:
        self.solver = solver


class _Solver:
    def __init__(self, requirements_token: str) -> None:
        self.profile = _parse_profile(requirements_token)
        self.regs: dict[str, Any] = {}
        self.done = False
        self.resolved = ""
        self.rejected = ""
        self.steps = 0
        self.window = self._build_window()

    def get(self, key: Any) -> Any:
        return self.regs.get(_reg(key))

    def set(self, key: Any, value: Any) -> None:
        self.regs[_reg(key)] = value

    def as_number(self, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip()) if value.strip() else 0.0
            except Exception:
                return None
        return None

    def js_str(self, value: Any) -> str:
        if value is None:
            return "undefined"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            if math.isnan(value):
                return "NaN"
            if math.isinf(value):
                return "Infinity" if value > 0 else "-Infinity"
            return str(int(value)) if math.trunc(value) == value else f"{value:f}".rstrip("0").rstrip(".")
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return ",".join("" if item is None else self.js_str(item) for item in value)
        if isinstance(value, dict):
            if isinstance(value.get("href"), str) and "search" in value:
                return str(value.get("href"))
            return "[object Object]"
        return str(value)

    def get_prop(self, obj: Any, prop: Any) -> Any:
        if obj is None:
            return None
        if isinstance(obj, _RegRef):
            return obj.solver.get(prop)
        if isinstance(obj, dict):
            key = self.js_str(prop)
            storage = obj.get("__storage_data__")
            if isinstance(storage, dict):
                if key in {"__storage_data__", "__storage_keys__", "length", "key", "getItem", "setItem", "removeItem", "clear"}:
                    return obj.get(key)
                return obj.get(key, storage.get(key))
            return obj.get(key)
        if isinstance(obj, list):
            if self.js_str(prop) == "length":
                return float(len(obj))
            index = _idx(prop)
            return obj[index] if 0 <= index < len(obj) else None
        if isinstance(obj, str):
            if self.js_str(prop) == "length":
                return float(len(obj))
            index = _idx(prop)
            return obj[index] if 0 <= index < len(obj) else None
        return None

    def set_prop(self, obj: Any, prop: Any, value: Any) -> bool:
        if isinstance(obj, _RegRef):
            obj.solver.set(prop, value)
            return True
        if isinstance(obj, dict):
            key = self.js_str(prop)
            storage = obj.get("__storage_data__")
            if isinstance(storage, dict) and key not in {"__storage_data__", "__storage_keys__", "length", "key", "getItem", "setItem", "removeItem", "clear"}:
                storage[key] = value
                obj[key] = value
                obj["__storage_keys__"] = _keys_of(storage)
                obj["length"] = float(len(_keys_of(storage)))
            else:
                obj[key] = value
            return True
        return False

    def _call_builtin(self, name: str, args: list[Any]) -> Any:
        if name == "window.performance.now":
            return self.profile.performance_now + ((time.perf_counter() * 1000) % 1000)
        if name == "window.Object.create":
            return _ordered_map({}, [])
        if name == "window.Object.keys":
            if args and isinstance(args[0], str) and args[0] == "window.localStorage":
                return [
                    "STATSIG_LOCAL_STORAGE_INTERNAL_STORE_V4",
                    "STATSIG_LOCAL_STORAGE_STABLE_ID",
                    "client-correlated-secret",
                    "oai/apps/capExpiresAt",
                    "oai-did",
                    "STATSIG_LOCAL_STORAGE_LOGGING_REQUEST",
                    "UiState.isNavigationCollapsed.1",
                ]
            if args and isinstance(args[0], dict):
                return [key for key in _keys_of(args[0]) if not str(key).startswith("__")]
            return []
        if name == "window.Math.random":
            return random.random()
        if name == "window.Reflect.set":
            return self.set_prop(args[0], args[1], args[2]) if len(args) >= 3 else True
        return None

    def call(self, value: Any, args: list[Any]) -> Any:
        if isinstance(value, str):
            return self._call_builtin(value, args)
        if callable(value):
            return value(*args)
        return None

    def _queue(self) -> list[Any]:
        queue = self.get(Q)
        return list(queue) if isinstance(queue, list) else []

    def _build_window(self) -> dict[str, Any]:
        width = 2048
        height = 1152
        if self.profile.screen_sum > 2000:
            width = int(round(self.profile.screen_sum * 0.64))
            height = self.profile.screen_sum - width

        ua = self.profile.user_agent
        script_url = str(self.profile.script_url or "").strip() or "https://sentinel.openai.com/backend-api/sentinel/sdk.js"
        lang = self.profile.language
        languages_join = self.profile.languages_join
        hw_concurrency = self.profile.hardware_concurrency
        heap_limit = int(self.profile.heap_limit or 4294967296)
        time_origin = float(self.profile.time_origin or (int(time.time() * 1000) - 10000))
        perf_now = self.profile.performance_now
        doc_probe = self.profile.document_probe
        win_probe = self.profile.window_probe
        device_id = self.profile.session_id or "bb13486d-db99-4547-81a4-a8f2a6351be9"
        product_sub = "20030107"
        if "productSub" in str(self.profile.navigator_probe or ""):
            parts = str(self.profile.navigator_probe).split("−", 1)
            if len(parts) == 2 and str(parts[1]).strip():
                product_sub = str(parts[1]).strip()
        chrome_major, chrome_full = _chrome_version_hints(ua)

        location = _ordered_map({"href": "https://auth.openai.com/create-account/password", "search": ""}, [])
        scripts: list[dict[str, Any]] = []
        seen_scripts: set[str] = set()
        for candidate in [
            script_url,
            "https://sentinel.openai.com/backend-api/sentinel/sdk.js",
            "https://sentinel.openai.com/sentinel/20260219f9f6/sdk.js",
        ]:
            normalized_candidate = str(candidate or "").strip()
            if not normalized_candidate or normalized_candidate in seen_scripts:
                continue
            seen_scripts.add(normalized_candidate)
            scripts.append(_ordered_map({"src": normalized_candidate}, []))

        # --- localStorage matching Go reference statsig keys ---
        storage_data = _ordered_map({
            "statsig.stable_id.444584300": '"' + device_id + '"',
            "statsig.session_id.444584300": '{"sessionID":"acba2013-7acb-405b-8064-7917fe2d8b4d","startTime":1775195126921,"lastUpdate":1775195156096}',
            "statsig.network_fallback.2742193661": '{"initialize":{"urlConfigChecksum":"3392903","url":"https://assetsconfigcdn.org/v1/initialize","expiryTime":1775799927542,"previous":[]}}',
        }, ["statsig.stable_id.444584300", "statsig.session_id.444584300", "statsig.network_fallback.2742193661"])
        storage_keys = list(_keys_of(storage_data))
        local_storage = _ordered_map({
            "__storage_data__": storage_data,
            "__storage_keys__": list(storage_keys),
            "length": float(len(storage_keys)),
        }, [])
        def _refresh_storage():
            nonlocal storage_keys
            storage_keys = list(_keys_of(storage_data))
            local_storage["__storage_keys__"] = list(storage_keys)
            local_storage["length"] = float(len(storage_keys))
        local_storage["key"] = lambda *args: storage_keys[_idx(args[0])] if args and 0 <= _idx(args[0]) < len(storage_keys) else None
        local_storage["getItem"] = lambda *args: storage_data.get(self.js_str(args[0])) if args else None
        def _set_item(*args):
            if len(args) >= 2:
                storage_data[self.js_str(args[0])] = self.js_str(args[1])
                _refresh_storage()
        def _remove_item(*args):
            if args:
                storage_data.pop(self.js_str(args[0]), None)
                _refresh_storage()
        def _clear_storage(*args):
            for k in list(storage_data.keys()):
                if not k.startswith("__"):
                    storage_data.pop(k, None)
            _refresh_storage()
        local_storage["setItem"] = _set_item
        local_storage["removeItem"] = _remove_item
        local_storage["clear"] = _clear_storage

        # --- screen ---
        screen = _ordered_map({
            "availWidth": float(width), "availHeight": float(height),
            "availLeft": 0.0, "availTop": 0.0,
            "colorDepth": 24.0, "pixelDepth": 24.0,
            "width": float(width), "height": float(height),
        }, [])

        # --- document with createElement ---
        document = _ordered_map({
            "scripts": scripts, "location": location,
            "documentElement": _ordered_map({"getAttribute": lambda *args: None}, []),
        }, ["location", doc_probe, "_reactListeningj3rmi50kcy", "closure_lm_184788"])
        document["body"] = _ordered_map({"getBoundingClientRect": lambda *args: _ordered_map({
            "x": 0.0, "y": 0.0, "width": 800.0, "height": 346.0,
            "top": 0.0, "left": 0.0, "right": 800.0, "bottom": 346.0,
        }, [])}, [])
        document["getElementById"] = lambda *args: document["body"]
        document["querySelector"] = lambda *args: document["body"]
        def _create_element(*args):
            tag = self.js_str(args[0]).lower() if args else ""
            el = _ordered_map({
                "tagName": tag.upper(),
                "style": _ordered_map({}, []),
                "appendChild": lambda *a: a[0] if a else None,
                "removeChild": lambda *a: a[0] if a else None,
                "remove": lambda *a: None,
            }, [])
            if tag == "canvas":
                el["getContext"] = lambda *a: _ordered_map({
                    "getExtension": lambda *ea: _ordered_map({
                        "UNMASKED_VENDOR_WEBGL": 37445.0,
                        "UNMASKED_RENDERER_WEBGL": 37446.0,
                    }, []) if ea and self.js_str(ea[0]) == "WEBGL_debug_renderer_info" else None,
                    "getParameter": lambda *pa: {
                        37445: "Google Inc. (NVIDIA)", 7936: "Google Inc. (NVIDIA)",
                        37446: "ANGLE (NVIDIA, NVIDIA GeForce RTX 5080 Laptop GPU Direct3D11 vs_5_0 ps_5_0, D3D11)",
                        7937: "ANGLE (NVIDIA, NVIDIA GeForce RTX 5080 Laptop GPU Direct3D11 vs_5_0 ps_5_0, D3D11)",
                    }.get(_idx(pa[0]), None) if pa else None,
                }, [])
            return el
        document["createElement"] = _create_element

        # --- navigator with full prototype keys (matching Go authNavigatorPrototypeKeys) ---
        nav_proto_keys = [
            "vendorSub", "productSub", "vendor", "maxTouchPoints", "scheduling", "userActivation",
            "geolocation", "doNotTrack", "connection", "plugins", "mimeTypes", "pdfViewerEnabled",
            "webkitTemporaryStorage", "webkitPersistentStorage", "hardwareConcurrency", "cookieEnabled",
            "appCodeName", "appName", "appVersion", "platform", "product", "userAgent", "language",
            "languages", "onLine", "webdriver", "getGamepads", "javaEnabled", "sendBeacon", "vibrate",
            "windowControlsOverlay", "deprecatedRunAdAuctionEnforcesKAnonymity", "protectedAudience",
            "bluetooth", "storageBuckets", "clipboard", "credentials", "keyboard", "managed",
            "mediaDevices", "storage", "serviceWorker", "virtualKeyboard", "wakeLock", "deviceMemory",
            "userAgentData", "login", "ink", "mediaCapabilities", "devicePosture", "hid", "locks",
            "gpu", "mediaSession", "permissions", "presentation", "serial", "usb", "xr",
            "adAuctionComponents", "runAdAuction", "canLoadAdAuctionFencedFrame", "canShare", "share",
            "clearAppBadge", "getBattery", "getUserMedia", "requestMIDIAccess",
            "requestMediaKeySystemAccess", "setAppBadge", "webkitGetUserMedia",
            "clearOriginJoinedAdInterestGroups", "createAuctionNonce", "joinAdInterestGroup",
            "leaveAdInterestGroup", "updateAdInterestGroups", "deprecatedReplaceInURN",
            "deprecatedURNToURL", "getInstalledRelatedApps", "getInterestGroupAdAuctionData",
            "registerProtocolHandler", "unregisterProtocolHandler",
        ]
        navigator = _ordered_map({
            "vendorSub": "",
            "productSub": product_sub,
            "userAgent": ua, "vendor": "Google Inc.", "platform": "Win32",
            "hardwareConcurrency": float(hw_concurrency), "deviceMemory": 8.0,
            "maxTouchPoints": 10.0, "language": lang,
            "appCodeName": "Mozilla",
            "appName": "Netscape",
            "appVersion": ua.removeprefix("Mozilla/5.0 "),
            "languages": [item for item in languages_join.split(",") if item],
            "webdriver": False,
        }, [])
        navigator[PROTO] = _ordered_map({key: None for key in nav_proto_keys}, nav_proto_keys)
        navigator["clipboard"] = _ordered_map({}, [])
        navigator["xr"] = _ordered_map({}, [])
        navigator["storage"] = _ordered_map({"estimate": lambda *args: _ordered_map({"quota": 306461727129.0, "usage": 0.0, "usageDetails": _ordered_map({}, [])}, [])}, [])
        navigator["userAgentData"] = _ordered_map({
            "brands": [
                _ordered_map({"brand": "Chromium", "version": chrome_major}, []),
                _ordered_map({"brand": "Google Chrome", "version": chrome_major}, []),
                _ordered_map({"brand": "Not_A Brand", "version": "99"}, []),
            ],
            "mobile": False,
            "platform": "Windows",
            "getHighEntropyValues": lambda *args: _ordered_map({
                "platform": "Windows",
                "platformVersion": "10.0.0",
                "architecture": "x86",
                "model": "",
                "uaFullVersion": chrome_full,
            }, []),
        }, [])
        navigator["canLoadAdAuctionFencedFrame"] = lambda *args: True

        # --- Full authWindowKeyOrder matching Go reference (200+ keys) ---
        auth_window_key_order = [
            "0", "window", "self", "document", "name", "location", "customElements", "history",
            "navigation", "locationbar", "menubar", "personalbar", "scrollbars", "statusbar",
            "toolbar", "status", "closed", "frames", "length", "top", "opener", "parent",
            "frameElement", "navigator", "origin", "external", "screen", "innerWidth", "innerHeight",
            "scrollX", "pageXOffset", "scrollY", "pageYOffset", "visualViewport", "screenX", "screenY",
            "outerWidth", "outerHeight", "devicePixelRatio", "event", "clientInformation", "screenLeft",
            "screenTop", "styleMedia", "onsearch", "trustedTypes", "performance",
            "onappinstalled", "onbeforeinstallprompt", "crypto", "indexedDB", "sessionStorage",
            "localStorage", "onbeforexrselect", "onabort", "onbeforeinput", "onbeforematch",
            "onbeforetoggle", "onblur", "oncancel", "oncanplay", "oncanplaythrough", "onchange",
            "onclick", "onclose", "oncommand", "oncontentvisibilityautostatechange", "oncontextlost",
            "oncontextmenu", "oncontextrestored", "oncuechange", "ondblclick", "ondrag", "ondragend",
            "ondragenter", "ondragleave", "ondragover", "ondragstart", "ondrop", "ondurationchange",
            "onemptied", "onended", "onerror", "onfocus", "onformdata", "oninput", "oninvalid",
            "onkeydown", "onkeypress", "onkeyup", "onload", "onloadeddata", "onloadedmetadata",
            "onloadstart", "onmousedown", "onmouseenter", "onmouseleave", "onmousemove", "onmouseout",
            "onmouseover", "onmouseup", "onmousewheel", "onpause", "onplay", "onplaying", "onprogress",
            "onratechange", "onreset", "onresize", "onscroll", "onscrollend",
            "onsecuritypolicyviolation", "onseeked", "onseeking", "onselect", "onslotchange",
            "onstalled", "onsubmit", "onsuspend", "ontimeupdate", "ontoggle", "onvolumechange",
            "onwaiting", "onwebkitanimationend", "onwebkitanimationiteration", "onwebkitanimationstart",
            "onwebkittransitionend", "onwheel", "onauxclick", "ongotpointercapture",
            "onlostpointercapture", "onpointerdown", "onpointermove", "onpointerup", "onpointercancel",
            "onpointerover", "onpointerout", "onpointerenter", "onpointerleave", "onselectstart",
            "onselectionchange", "onanimationend", "onanimationiteration", "onanimationstart",
            "ontransitionrun", "ontransitionstart", "ontransitionend", "ontransitioncancel",
            "onafterprint", "onbeforeprint", "onbeforeunload", "onhashchange", "onlanguagechange",
            "onmessage", "onmessageerror", "onoffline", "ononline", "onpagehide", "onpageshow",
            "onpopstate", "onrejectionhandled", "onstorage", "onunhandledrejection", "onunload",
            "isSecureContext", "crossOriginIsolated", "scheduler", "alert", "atob", "blur", "btoa",
            "cancelAnimationFrame", "cancelIdleCallback", "captureEvents", "clearInterval",
            "clearTimeout", "close", "confirm", "createImageBitmap", "fetch", "find", "focus",
            "getComputedStyle", "getSelection", "matchMedia", "moveBy", "moveTo", "open", "postMessage",
            "print", "prompt", "queueMicrotask", "releaseEvents", "reportError",
            "requestAnimationFrame", "requestIdleCallback", "resizeBy", "resizeTo", "scroll",
            "scrollBy", "scrollTo", "setInterval", "setTimeout", "stop", "structuredClone",
            "webkitCancelAnimationFrame", "webkitRequestAnimationFrame", "chrome", "caches",
            "cookieStore", "ondevicemotion", "ondeviceorientation", "ondeviceorientationabsolute",
            "onpointerrawupdate", "documentPictureInPicture", "sharedStorage", "fetchLater",
            "getScreenDetails", "queryLocalFonts", "showDirectoryPicker", "showOpenFilePicker",
            "showSaveFilePicker", "originAgentCluster", "viewport", "onpageswap", "onpagereveal",
            "credentialless", "fence", "launchQueue", "speechSynthesis", "onscrollsnapchange",
            "onscrollsnapchanging", "webkitRequestFileSystem", "webkitResolveLocalFileSystemURL",
            "__reactRouterContext", "$RB", "$RV", "$RC", "$RT", "__reactRouterManifest",
            "__STATSIG__", "__reactRouterVersion", "__REACT_INTL_CONTEXT__", "DD_RUM",
            "__SEGMENT_INSPECTOR__", "__reactRouterRouteModules", "__reactRouterDataRouter",
            "__sentinel_token_pending", "__sentinel_init_pending", "SentinelSDK", "rwha4gh7no",
        ]

        window = _ordered_map({
            "document": document,
            "navigator": navigator,
            "location": location,
            "screen": screen,
            "localStorage": local_storage,
            "performance": _ordered_map({
                "now": lambda *args: perf_now + ((time.perf_counter() * 1000) % 1000),
                "timeOrigin": time_origin,
                "memory": _ordered_map({"jsHeapSizeLimit": float(heap_limit)}, []),
            }, []),
            "Math": _ordered_map({
                "random": lambda *args: random.random(),
                "abs": lambda *args: abs(self.as_number(args[0]) or 0.0) if args else 0.0,
            }, []),
            "Object": _ordered_map({
                "create": lambda *args: _ordered_map({}, []),
                "keys": lambda *args: [key for key in _keys_of(args[0]) if not str(key).startswith("__")] if args and isinstance(args[0], dict) else [],
                "getPrototypeOf": lambda *args: args[0].get(PROTO) if args and isinstance(args[0], dict) else None,
            }, []),
            "Reflect": _ordered_map({
                "set": lambda *args: self.set_prop(args[0], args[1], args[2]) if len(args) >= 3 else True,
            }, []),

            "JSON": _ordered_map({"parse": lambda *args: json.loads(self.js_str(args[0])) if args else None, "stringify": lambda *args: _json_stringify(args[0]) if args else "null"}, []),
            "atob": lambda *args: _b64d(self.js_str(args[0])) if args else "",
            "btoa": lambda *args: _b64e(self.js_str(args[0])) if args else "",
            "chrome": _ordered_map({"runtime": _ordered_map({}, [])}, []),
            "close": lambda *args: None,
            "__reactRouterContext": _ordered_map({"ssr": True, "isSpaMode": True}, []),
            "__reactRouterManifest": _ordered_map({}, []),
            "__STATSIG__": _ordered_map({}, []),
            "__reactRouterVersion": "7.9.3",
            "__REACT_INTL_CONTEXT__": _ordered_map({}, []),
            "DD_RUM": _ordered_map({}, []),
            "__SEGMENT_INSPECTOR__": _ordered_map({}, []),
            "__reactRouterRouteModules": _ordered_map({}, []),
            "__reactRouterDataRouter": _ordered_map({}, []),
            "__sentinel_token_pending": _ordered_map({}, []),
            "__sentinel_init_pending": _ordered_map({}, []),
            "SentinelSDK": _ordered_map({}, []),
            "rwha4gh7no": lambda *args: None,
            win_probe: None,
        }, auth_window_key_order)
        window["window"] = window
        window["self"] = window
        window["globalThis"] = window
        window["0"] = window
        window["innerWidth"] = 800.0
        window["innerHeight"] = 600.0
        window["outerWidth"] = 160.0
        window["outerHeight"] = 28.0
        window["screenX"] = -25600.0
        window["screenY"] = -25600.0
        window["screenLeft"] = -25600.0
        window["screenTop"] = -25600.0
        window["devicePixelRatio"] = 1.0000000149011612
        window["clientInformation"] = navigator
        window["event"] = None
        window["history"] = _ordered_map({"length": 3.0}, [])
        window["isSecureContext"] = True
        window["crossOriginIsolated"] = False
        window["hardwareConcurrency"] = float(hw_concurrency)
        document[doc_probe] = True
        return window


    def run(self, queue: list[Any]) -> None:
        self.set(Q, queue)
        while not self.done:
            current = self._queue()
            if not current:
                return
            ins = current[0]
            self.set(Q, current[1:])
            if not isinstance(ins, list) or not ins:
                continue
            fn = self.get(ins[0])
            if not callable(fn):
                raise RuntimeError(f"vm opcode not callable: {ins[0]}")
            fn(*ins[1:])
            self.steps += 1
            if self.steps > 50000:
                raise RuntimeError("turnstile vm step overflow")


def process_turnstile(dx: str, p: str) -> str:
    """Solve a Turnstile DX challenge using the Go VM binary.

    The Go binary is a compiled version of the proven reference Turnstile VM
    solver that produces valid 900+ char tokens. Falls back to the legacy
    Python VM if the Go binary is not available.
    """
    import subprocess

    solver_mode = str(os.environ.get("TURNSTILE_SOLVER_MODE") or DEFAULT_TURNSTILE_SOLVER_MODE).strip().lower() or DEFAULT_TURNSTILE_SOLVER_MODE
    if solver_mode == "python":
        print("[turnstile] solver mode forced to python script")
        return _normalize_turnstile_token(
            _process_turnstile_python(dx, p),
            source="python-script",
        )

    # Locate Go solver source and binary next to this Python file
    solver_dir = os.path.dirname(os.path.abspath(__file__))
    solver_source_dir = str(os.environ.get("TURNSTILE_SOLVER_SOURCE_DIR") or "").strip()
    if not solver_source_dir:
        solver_source_dir = _default_turnstile_solver_source_dir(solver_dir)
    solver_bin = str(os.environ.get("TURNSTILE_SOLVER_PATH") or "").strip()
    if not solver_bin:
        solver_bin = _default_turnstile_solver_path(solver_dir)

    profile = _parse_profile(p)
    source_cmd, source_transport = _resolve_turnstile_solver_source_command(solver_source_dir)
    solver_cmd, solver_transport = _resolve_turnstile_solver_command(solver_bin)
    prefer_source = solver_mode in {"go", "go-source", "source", "source-preferred", "go-source-preferred"}

    if solver_mode in {"go", "go-source", "source"} and not source_cmd:
        raise RuntimeError(
            f"go source solver requested but unavailable path={solver_source_dir} reason={source_transport}"
        )
    if solver_mode == "binary" and not solver_cmd:
        raise RuntimeError(
            f"binary solver requested but unavailable path={solver_bin} reason={solver_transport}"
        )

    def _run_solver_command(
        *,
        command: list[str],
        transport: str,
        cwd: str | None = None,
        use_stdin: bool = False,
    ) -> str:
        payload = json.dumps(
            {
                "dx": str(dx or ""),
                "p": str(p or ""),
                "session": _go_solver_session_payload(profile),
            }
        )
        run_env = os.environ.copy()
        if transport == "wine":
            run_env.setdefault("WINEDEBUG", "-all")
            run_env.setdefault("WINEARCH", "win64")
        final_command = [*command]
        run_kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": 30,
            "env": run_env,
            "cwd": cwd,
        }
        if use_stdin:
            run_kwargs["input"] = payload
        else:
            final_command.append(payload)
        result = subprocess.run(
            final_command,
            **run_kwargs,
        )
        if str(os.environ.get("TURNSTILE_VM_TRACE") or "").strip() == "1":
            stderr_preview = str(result.stderr or "").strip()
            if stderr_preview:
                print(f"[turnstile-trace] transport={transport} stderr={stderr_preview}")
        if result.returncode == 0 and result.stdout.strip():
            resp = _parse_turnstile_solver_stdout(result.stdout)
            if resp.get("error"):
                raise RuntimeError(f"turnstile solver error: {resp['error']}")
            token = _normalize_turnstile_token(
                str(resp.get("token", "") or ""),
                source=transport,
            )
            if token:
                print(
                    f"[turnstile] solver success transport={transport} "
                    f"t_len={len(token)}"
                )
            return token
        stderr_preview = str(result.stderr or "").strip()[:200]
        if stderr_preview:
            print(
                f"[turnstile] solver stderr transport={transport} "
                f"preview={stderr_preview!r}"
            )
        print(
            f"[turnstile] solver returned short/empty token "
            f"transport={transport}, falling back"
        )
        return ""

    if prefer_source or (solver_mode == "auto" and source_cmd):
        try:
            token = _run_solver_command(
                command=source_cmd,
                transport=source_transport,
                cwd=solver_source_dir,
                use_stdin=True,
            )
            if token:
                return token
        except FileNotFoundError:
            print(
                f"[turnstile] go source solver command missing transport={source_transport} "
                f"path={solver_source_dir}, falling back"
            )
        except subprocess.TimeoutExpired:
            print(
                f"[turnstile] go source solver timed out transport={source_transport}, "
                "falling back"
            )
        except Exception as exc:
            print(
                f"[turnstile] go source solver failed transport={source_transport}: "
                f"{exc}, falling back"
            )

    if not solver_cmd:
        print(
            f"[turnstile] skipping binary solver path={solver_bin} "
            f"reason={solver_transport}"
        )
        return _normalize_turnstile_token(
            _process_turnstile_python(dx, p),
            source="python-script",
        )

    if os.path.isfile(solver_bin):
        try:
            token = _run_solver_command(
                command=solver_cmd,
                transport=solver_transport,
            )
            if token:
                return token
        except FileNotFoundError:
            print(
                f"[turnstile] binary solver command missing transport={solver_transport} "
                f"path={solver_bin}, using Python VM"
            )
        except subprocess.TimeoutExpired:
            print(
                f"[turnstile] binary solver timed out transport={solver_transport}, "
                "using Python VM"
            )
        except Exception as exc:
            print(
                f"[turnstile] binary solver failed transport={solver_transport}: "
                f"{exc}, using Python VM"
            )

    # Fallback: Python VM (may produce short tokens)
    return _normalize_turnstile_token(
        _process_turnstile_python(dx, p),
        source="python-script",
    )


def _process_turnstile_python(dx: str, p: str) -> str:
    """Legacy Python Turnstile VM solver (fallback)."""
    solver = _Solver(str(p or ""))
    solver.set(OK, lambda *args: setattr(solver, "resolved", _b64e(solver.js_str(args[0] if args else None))) or setattr(solver, "done", True))
    solver.set(ERR, lambda *args: setattr(solver, "rejected", _b64e(solver.js_str(args[0] if args else None))) or setattr(solver, "done", True))

    # CB handler (opcode 30)
    def _cb_handler(*args: Any) -> None:
        if len(args) < 3:
            return
        target_reg = args[0]
        return_reg = args[1]
        mapped_arg_regs: list[Any] = []
        inner_queue = args[2] if isinstance(args[2], list) else []
        if len(args) >= 4:
            mapped_arg_regs = list(args[2]) if isinstance(args[2], list) else []
            inner_queue = list(args[3]) if isinstance(args[3], list) else []

        def _closure(*call_args: Any) -> Any:
            if solver.done:
                return None
            prev_queue = solver._queue()
            for i, reg_id in enumerate(mapped_arg_regs):
                solver.set(reg_id, call_args[i] if i < len(call_args) else None)
            solver.set(Q, list(inner_queue))
            try:
                solver.run(solver._queue())
            except Exception:
                pass
            result = solver.get(return_reg)
            solver.set(Q, prev_queue)
            return result

        solver.set(target_reg, _closure)

    solver.set(CB, _cb_handler)
    solver.set(K, str(p or ""))
    solver.set(W, solver.window)

    def op0(*args: Any) -> str | None:
        if not args:
            return None
        return _process_turnstile_python(solver.js_str(args[0]), solver.js_str(solver.get(K)))
    def op1(*args: Any) -> None:
        if len(args) >= 2:
            solver.set(args[0], _xor(solver.js_str(solver.get(args[0])), solver.js_str(solver.get(args[1]))))
    def op2(*args: Any) -> None:
        if len(args) >= 2:
            solver.set(args[0], args[1])
    def op5(*args: Any) -> None:
        if len(args) < 2: return
        left = solver.get(args[0]); right = solver.get(args[1])
        if isinstance(left, list):
            solver.set(args[0], left + ([right] if right is not None else [])); return
        lnum = solver.as_number(left); rnum = solver.as_number(right)
        solver.set(args[0], lnum + rnum if lnum is not None and rnum is not None else solver.js_str(left) + solver.js_str(right))
    def op6(*args: Any) -> None:
        if len(args) >= 3:
            solver.set(args[0], solver.get_prop(solver.get(args[1]), solver.get(args[2])))
    def op7(*args: Any) -> None:
        if args: solver.call(solver.get(args[0]), [solver.get(arg) for arg in args[1:]])
    def op8(*args: Any) -> None:
        if len(args) >= 2: solver.set(args[0], solver.get(args[1]))
    def op11(*args: Any) -> None:
        if len(args) < 2: return
        try: rx = re.compile(solver.js_str(solver.get(args[1])))
        except Exception: solver.set(args[0], None); return
        scripts = solver.get_prop(solver.get_prop(solver.window, "document"), "scripts")
        for item in scripts if isinstance(scripts, list) else []:
            src = solver.js_str(solver.get_prop(item, "src"))
            if src and rx.search(src): solver.set(args[0], src); return
        solver.set(args[0], None)
    def op12(*args: Any) -> None:
        if args: solver.set(args[0], _RegRef(solver))
    def op13(*args: Any) -> None:
        if len(args) < 2: return
        try: solver.call(solver.get(args[1]), list(args[2:]))
        except Exception as exc: solver.set(args[0], str(exc))
    def op14(*args: Any) -> None:
        if len(args) >= 2: solver.set(args[0], json.loads(solver.js_str(solver.get(args[1]))))
    def op15(*args: Any) -> None:
        if len(args) >= 2: solver.set(args[0], _json_stringify(solver.get(args[1])))
    def op17(*args: Any) -> None:
        if len(args) >= 2: solver.set(args[0], solver.call(solver.get(args[1]), [solver.get(arg) for arg in args[2:]]))
    def op18(*args: Any) -> None:
        if args: solver.set(args[0], _b64d(solver.js_str(solver.get(args[0]))))
    def op19(*args: Any) -> None:
        if args: solver.set(args[0], _b64e(solver.js_str(solver.get(args[0]))))
    def op20(*args: Any) -> None:
        if len(args) >= 3 and solver.get(args[0]) == solver.get(args[1]): solver.call(solver.get(args[2]), list(args[3:]))
    def op21(*args: Any) -> None:
        if len(args) < 4: return
        left = solver.as_number(solver.get(args[0])) or 0.0
        right = solver.as_number(solver.get(args[1])) or 0.0
        threshold = solver.as_number(solver.get(args[2])) or 0.0
        if abs(left - right) > threshold: solver.call(solver.get(args[3]), list(args[4:]))
    def op22(*args: Any) -> None:
        if len(args) < 2: return
        prev = solver._queue(); solver.set(Q, list(args[1]) if isinstance(args[1], list) else [])
        try: solver.run(solver._queue())
        except Exception as exc: solver.set(args[0], str(exc))
        finally: solver.set(Q, prev)
    def op23(*args: Any) -> None:
        if len(args) >= 2 and solver.get(args[0]) is not None: solver.call(solver.get(args[1]), list(args[2:]))
    def op24(*args: Any) -> None:
        if len(args) >= 3:
            method = solver.get_prop(solver.get(args[1]), solver.get(args[2]))
            solver.set(args[0], method if callable(method) or isinstance(method, str) else None)
    def op27(*args: Any) -> None:
        if len(args) < 2: return
        left = solver.get(args[0]); right = solver.get(args[1])
        if isinstance(left, list): solver.set(args[0], [item for item in left if item != right]); return
        lnum = solver.as_number(left); rnum = solver.as_number(right)
        if lnum is not None and rnum is not None: solver.set(args[0], lnum - rnum)
    def op29(*args: Any) -> None:
        if len(args) >= 3: solver.set(args[0], (solver.as_number(solver.get(args[1])) or 0.0) < (solver.as_number(solver.get(args[2])) or 0.0))
    def op33(*args: Any) -> None:
        if len(args) >= 3: solver.set(args[0], (solver.as_number(solver.get(args[1])) or 0.0) * (solver.as_number(solver.get(args[2])) or 0.0))
    def op34(*args: Any) -> None:
        if len(args) >= 2: solver.set(args[0], solver.get(args[1]))
    def op35(*args: Any) -> None:
        if len(args) < 3: return
        left = solver.as_number(solver.get(args[1])) or 0.0
        right = solver.as_number(solver.get(args[2])) or 0.0
        solver.set(args[0], 0.0 if right == 0 else left / right)

    for code, fn in {0: op0, 1: op1, 2: op2, 5: op5, 6: op6, 7: op7, 8: op8, 11: op11, 12: op12, 13: op13, 14: op14, 15: op15, 17: op17, 18: op18, 19: op19, 20: op20, 21: op21, 22: op22, 23: op23, 24: op24, 25: lambda *args: None, 26: lambda *args: None, 27: op27, 28: lambda *args: None, 29: op29, 33: op33, 34: op34, 35: op35}.items():
        solver.set(code, fn)

    decoded = _b64d(str(dx or ""))
    plain = _xor(decoded, str(p or ""))
    queue = json.loads(plain)
    try:
        solver.run(queue if isinstance(queue, list) else [])
    except Exception:
        if not solver.done:
            solver.resolved = _b64e(f"{solver.steps}: turnstile vm execution failed")
            solver.done = True
    if solver.rejected:
        raise RuntimeError(solver.rejected)
    return solver.resolved

