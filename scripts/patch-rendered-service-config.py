#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import yaml


class NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def dump_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        yaml.dump(payload, Dumper=NoAliasDumper, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )


def normalize_docker_daemon_host_path(path_text: str) -> str:
    normalized = str(path_text or "").strip()
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", normalized)
    if not match:
        return normalized
    drive = match.group(1).lower()
    tail = match.group(2).replace("\\", "/").strip("/")
    return f"/run/desktop/mnt/host/{drive}/{tail}" if tail else f"/run/desktop/mnt/host/{drive}"


def ensure_dict(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


def upsert_mount(mounts: list[Any], *, source: str, target: str, read_only: bool) -> None:
    normalized_source = normalize_docker_daemon_host_path(source)
    for index, item in enumerate(mounts):
        if isinstance(item, dict) and item.get("target") == target:
            mounts[index] = {
                **item,
                "source": normalized_source,
                "target": target,
                "read_only": read_only,
            }
            return
    mounts.append({"source": normalized_source, "target": target, "read_only": read_only})


def patch_config(
    config: dict[str, Any],
    *,
    python_provider_image: str,
    register_output_dir_host: str,
    register_team_auth_dir_host: str,
    register_team_local_dir_host: str,
    mailbox_service_api_key: str,
    easy_proxy_api_key: str,
) -> bool:
    changed = False
    managed = ensure_dict(config, "managed_provider_runtime")
    providers = ensure_dict(managed, "providers")
    python_provider = ensure_dict(providers, "python")

    if python_provider_image:
        if python_provider.get("image") != python_provider_image:
            python_provider["image"] = python_provider_image
            changed = True

    environment = ensure_dict(python_provider, "environment")
    env_overrides = {
        "MAILBOX_SERVICE_API_KEY": mailbox_service_api_key,
        "EASY_PROXY_API_KEY": easy_proxy_api_key,
    }
    for key, value in env_overrides.items():
        if value and environment.get(key) != value:
            environment[key] = value
            changed = True

    mounts = python_provider.get("host_mounts")
    if not isinstance(mounts, list):
        mounts = []
        python_provider["host_mounts"] = mounts
        changed = True

    mount_overrides = (
        (register_output_dir_host, "/shared/register-output", False),
        (register_team_auth_dir_host, "/shared/team-auth", True),
        (register_team_local_dir_host, "/shared/local-team-store", False),
    )
    before_mounts = yaml.safe_dump(mounts, sort_keys=False)
    for source, target, read_only in mount_overrides:
        if source:
            upsert_mount(mounts, source=source, target=target, read_only=read_only)
    if yaml.safe_dump(mounts, sort_keys=False) != before_mounts:
        changed = True

    return changed


def parse_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key] = value
    return result


def write_env_file(path: Path, payload: dict[str, str]) -> None:
    path.write_text("\n".join(f"{key}={value}" for key, value in payload.items()) + "\n", encoding="utf-8")


def patch_env(
    env_path: Path,
    *,
    python_provider_image: str,
    register_output_dir_host: str,
    register_team_auth_dir_host: str,
    register_team_local_dir_host: str,
    mailbox_service_api_key: str,
    easy_proxy_api_key: str,
) -> bool:
    env = parse_env_file(env_path)
    changed = False
    overrides = {
        "EASY_PROTOCOL_PYTHON_PROVIDER_IMAGE": python_provider_image,
        "MAILBOX_SERVICE_API_KEY": mailbox_service_api_key,
        "EASY_PROXY_API_KEY": easy_proxy_api_key,
        "REGISTER_OUTPUT_DIR_HOST": normalize_docker_daemon_host_path(register_output_dir_host),
        "REGISTER_TEAM_AUTH_DIR_HOST": normalize_docker_daemon_host_path(register_team_auth_dir_host),
        "REGISTER_TEAM_LOCAL_DIR_HOST": normalize_docker_daemon_host_path(register_team_local_dir_host),
    }
    for key, value in overrides.items():
        if value and env.get(key) != value:
            env[key] = value
            changed = True
    if changed:
        write_env_file(env_path, env)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch a rendered service/base config without re-rendering root config.")
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--runtime-env-path", required=True)
    parser.add_argument("--python-provider-image", default="")
    parser.add_argument("--register-output-dir-host", default="")
    parser.add_argument("--register-team-auth-dir-host", default="")
    parser.add_argument("--register-team-local-dir-host", default="")
    parser.add_argument("--mailbox-service-api-key", default="")
    parser.add_argument("--easy-proxy-api-key", default="")
    args = parser.parse_args()

    config_path = Path(args.config_path).resolve()
    runtime_env_path = Path(args.runtime_env_path).resolve()
    if not config_path.exists():
        raise SystemExit(f"Rendered service config not found: {config_path}")
    if not runtime_env_path.exists():
        raise SystemExit(f"Rendered runtime env not found: {runtime_env_path}")

    config = load_yaml(config_path)
    config_changed = patch_config(
        config,
        python_provider_image=args.python_provider_image.strip(),
        register_output_dir_host=args.register_output_dir_host.strip(),
        register_team_auth_dir_host=args.register_team_auth_dir_host.strip(),
        register_team_local_dir_host=args.register_team_local_dir_host.strip(),
        mailbox_service_api_key=args.mailbox_service_api_key.strip(),
        easy_proxy_api_key=args.easy_proxy_api_key.strip(),
    )
    if config_changed:
        dump_yaml(config_path, config)

    env_changed = patch_env(
        runtime_env_path,
        python_provider_image=args.python_provider_image.strip(),
        register_output_dir_host=args.register_output_dir_host.strip(),
        register_team_auth_dir_host=args.register_team_auth_dir_host.strip(),
        register_team_local_dir_host=args.register_team_local_dir_host.strip(),
        mailbox_service_api_key=args.mailbox_service_api_key.strip(),
        easy_proxy_api_key=args.easy_proxy_api_key.strip(),
    )

    status = "patched" if config_changed or env_changed else "unchanged"
    print(f"Rendered service config patch status: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
