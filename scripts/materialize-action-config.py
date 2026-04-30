from __future__ import annotations

import argparse
import copy
import os
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data
    return {}


def dump_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )


def deep_merge(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = {key: copy.deepcopy(value) for key, value in base.items()}
        for key, value in overlay.items():
            if key in merged:
                merged[key] = deep_merge(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged
    if isinstance(overlay, list):
        return copy.deepcopy(overlay)
    if overlay is None:
        return copy.deepcopy(base)
    return copy.deepcopy(overlay)


def get_secret(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def set_if_present(mapping: dict[str, Any], key: str, value: Any) -> None:
    if value not in ("", None):
        mapping[key] = value


def build_overlay() -> dict[str, Any]:
    overlay: dict[str, Any] = {}

    service_runtime = {
        "serviceBase": {
            "runtime": {
                "unified_api": {},
                "control_plane": {},
            }
        }
    }
    unified_api = service_runtime["serviceBase"]["runtime"]["unified_api"]
    control_plane = service_runtime["serviceBase"]["runtime"]["control_plane"]

    set_if_present(
        unified_api,
        "password",
        get_secret("EASYPROTOCOL_SERVICE_UNIFIED_API_PASSWORD"),
    )
    set_if_present(
        control_plane,
        "read_token",
        get_secret("EASYPROTOCOL_SERVICE_CONTROL_PLANE_READ_TOKEN"),
    )
    set_if_present(
        control_plane,
        "mutate_token",
        get_secret("EASYPROTOCOL_SERVICE_CONTROL_PLANE_MUTATE_TOKEN"),
    )

    if unified_api or control_plane:
        overlay = deep_merge(overlay, service_runtime)

    providers_overlay = {
        "providers": {
            "python": {
                "containerEnvironment": {},
                "hostMounts": {},
            }
        }
    }
    python_env = providers_overlay["providers"]["python"]["containerEnvironment"]
    python_mounts = providers_overlay["providers"]["python"]["hostMounts"]

    secret_to_python_env = {
        "EASYPROTOCOL_STACK_MAILBOX_SERVICE_API_KEY": "MAILBOX_SERVICE_API_KEY",
        "EASYPROTOCOL_STACK_EASY_PROXY_API_KEY": "EASY_PROXY_API_KEY",
        "EASYPROTOCOL_PROVIDER_REGISTER_R2_ACCESS_KEY_ID": "REGISTER_R2_ACCESS_KEY_ID",
        "EASYPROTOCOL_PROVIDER_REGISTER_R2_SECRET_ACCESS_KEY": "REGISTER_R2_SECRET_ACCESS_KEY",
        "EASYPROTOCOL_PROVIDER_REGISTER_R2_ACCOUNT_ID": "REGISTER_R2_ACCOUNT_ID",
        "EASYPROTOCOL_PROVIDER_REGISTER_R2_BUCKET": "REGISTER_R2_BUCKET",
        "EASYPROTOCOL_PROVIDER_REGISTER_R2_ENDPOINT_URL": "REGISTER_R2_ENDPOINT_URL",
        "EASYPROTOCOL_PROVIDER_REGISTER_R2_REGION": "REGISTER_R2_REGION",
        "EASYPROTOCOL_PROVIDER_REGISTER_R2_PUBLIC_BASE_URL": "REGISTER_R2_PUBLIC_BASE_URL",
    }
    for secret_name, env_key in secret_to_python_env.items():
        set_if_present(python_env, env_key, get_secret(secret_name))

    set_if_present(
        python_mounts,
        "registerOutputDirHost",
        get_secret("EASYPROTOCOL_PROVIDER_REGISTER_OUTPUT_DIR_HOST"),
    )
    set_if_present(
        python_mounts,
        "registerTeamAuthDirHost",
        get_secret("EASYPROTOCOL_PROVIDER_REGISTER_TEAM_AUTH_DIR_HOST"),
    )
    set_if_present(
        python_mounts,
        "registerTeamLocalDirHost",
        get_secret("EASYPROTOCOL_PROVIDER_REGISTER_TEAM_LOCAL_DIR_HOST"),
    )

    if python_env or python_mounts:
        overlay = deep_merge(overlay, providers_overlay)

    publishing_overlay = {"publishing": {"ghcr": {}}}
    ghcr = publishing_overlay["publishing"]["ghcr"]
    set_if_present(ghcr, "registry", get_secret("EASYPROTOCOL_PUBLISH_GHCR_REGISTRY"))
    set_if_present(ghcr, "owner", get_secret("EASYPROTOCOL_PUBLISH_GHCR_OWNER"))
    set_if_present(ghcr, "username", get_secret("EASYPROTOCOL_PUBLISH_GHCR_USERNAME"))
    set_if_present(ghcr, "token", get_secret("EASYPROTOCOL_PUBLISH_GHCR_TOKEN"))

    if ghcr:
        overlay = deep_merge(overlay, publishing_overlay)

    return overlay


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize a deployable EasyProtocol config from GitHub Actions secrets."
    )
    parser.add_argument("--base-config", required=True, help="Path to the base config YAML to merge onto.")
    parser.add_argument("--output", required=True, help="Path to the generated root config.yaml.")
    args = parser.parse_args()

    base_config_path = Path(args.base_config).resolve()
    output_path = Path(args.output).resolve()

    base_config = load_yaml(base_config_path)
    overlay = build_overlay()
    merged = deep_merge(base_config, overlay)
    dump_yaml(output_path, merged)


if __name__ == "__main__":
    main()

