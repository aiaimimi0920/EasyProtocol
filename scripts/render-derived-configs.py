from __future__ import annotations

import argparse
import copy
import re
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_TEMPLATE_PATH = REPO_ROOT / "deploy" / "service" / "base" / "config.template.yaml"


class NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data
    return {}


def dump_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(
            payload,
            Dumper=NoAliasDumper,
            sort_keys=False,
            allow_unicode=False,
        ),
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


def normalize_bool(value: Any) -> str:
    return "true" if bool(value) else "false"


def get_dict(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    value = mapping.get(key)
    return value if isinstance(value, dict) else {}


def _first_non_blank(*values: Any) -> str:
    for value in values:
        text = str(value or "")
        if text.strip():
            return text
    return ""


def _normalize_replica_count(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(0, parsed)


def _normalize_duration_string(value: Any, *, default_seconds: int) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            return normalized
    try:
        parsed = int(value)
    except Exception:
        parsed = default_seconds
    return f"{max(0, parsed)}s"


def _replica_suffix(index: int, count: int) -> str:
    width = max(3, len(str(max(1, count))))
    return f"{index:0{width}d}"


def _generate_registry_service_entries(
    *,
    registry: dict[str, Any],
    pool_provider: dict[str, Any],
    default_name: str,
    default_language: str,
    default_endpoint_host: str,
) -> list[dict[str, Any]]:
    if not registry.get("enabled", True):
        return []

    language = str(registry.get("language") or default_language).strip() or default_language
    service_name = str(registry.get("name") or default_name).strip() or default_name
    service_name_prefix = str(
        registry.get("serviceNamePrefix")
        or registry.get("namePrefix")
        or service_name
    ).strip() or service_name
    endpoint_host = str(registry.get("endpointHost") or default_endpoint_host).strip() or default_endpoint_host
    endpoint_host_prefix = str(registry.get("endpointHostPrefix") or "").strip()
    port = int(registry.get("port", 9100) or 9100)
    supported_operations = list(registry.get("supportedOperations") or [])
    warm_replicas = pool_provider.get("warmReplicas") if isinstance(pool_provider, dict) else None
    replicas = _normalize_replica_count(
        warm_replicas if warm_replicas is not None else registry.get("replicas"),
        default=1 if endpoint_host_prefix else 0,
    )

    if endpoint_host_prefix:
        replica_count = max(1, replicas)
        return [
            {
                "name": f"{service_name_prefix}-{_replica_suffix(index, replica_count)}",
                "language": language,
                "endpoint": f"http://{endpoint_host_prefix}-{_replica_suffix(index, replica_count)}:{port}",
                "enabled": True,
                "supported_operations": supported_operations,
            }
            for index in range(1, replica_count + 1)
        ]

    return [
        {
            "name": service_name,
            "language": language,
            "endpoint": f"http://{endpoint_host}:{port}",
            "enabled": True,
            "supported_operations": supported_operations,
        }
    ]


def generate_registry_services(root_config: dict[str, Any]) -> list[dict[str, Any]]:
    providers = get_dict(root_config, "providers")
    service_base = get_dict(root_config, "serviceBase")
    runtime_cfg = get_dict(service_base, "runtime")
    provider_pool = get_dict(runtime_cfg, "provider_pool")
    if not provider_pool:
        provider_pool = get_dict(runtime_cfg, "providerPool")
    pool_providers = get_dict(provider_pool, "providers")
    services: list[dict[str, Any]] = []

    python_provider = get_dict(providers, "python")
    python_registry = get_dict(python_provider, "registry")
    services.extend(
        _generate_registry_service_entries(
            registry=python_registry,
            pool_provider=get_dict(pool_providers, "python"),
            default_name="PythonProtocol",
            default_language="python",
            default_endpoint_host="easy-protocol-python-001",
        )
    )

    for provider_key, defaults in (
        ("go", ("GolangProtocol", "go", "easy-protocol-go-001")),
        ("javascript", ("JSProtocol", "javascript", "easy-protocol-javascript-001")),
        ("rust", ("RustProtocol", "rust", "easy-protocol-rust-001")),
    ):
        provider = get_dict(providers, provider_key)
        registry = get_dict(provider, "registry")
        if not registry:
            continue
        name_default, language_default, endpoint_host_default = defaults
        services.extend(
            _generate_registry_service_entries(
                registry=registry,
                pool_provider=get_dict(pool_providers, provider_key),
                default_name=name_default,
                default_language=language_default,
                default_endpoint_host=endpoint_host_default,
            )
        )

    return services


def _build_managed_provider_host_mounts(provider_key: str, provider: dict[str, Any]) -> list[dict[str, Any]]:
    if provider_key != "python":
        return []
    host_mounts = get_dict(provider, "hostMounts")
    mount_specs = (
        ("registerOutputDirHost", "/shared/register-output", False),
        ("registerTeamAuthDirHost", "/shared/team-auth", True),
        ("registerTeamLocalDirHost", "/shared/local-team-store", False),
    )
    mounts: list[dict[str, Any]] = []
    for source_key, target, read_only in mount_specs:
        source = str(host_mounts.get(source_key) or "").strip()
        if not source:
            continue
        source = _normalize_docker_daemon_host_path(source)
        mounts.append(
            {
                "source": source,
                "target": target,
                "read_only": read_only,
            }
        )
    return mounts


def _normalize_docker_daemon_host_path(path_text: str) -> str:
    normalized = str(path_text or "").strip()
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", normalized)
    if not match:
        return normalized
    drive = match.group(1).lower()
    tail = match.group(2).replace("\\", "/").strip("/")
    return f"/run/desktop/mnt/host/{drive}/{tail}" if tail else f"/run/desktop/mnt/host/{drive}"


def build_managed_provider_runtime(root_config: dict[str, Any]) -> dict[str, Any]:
    providers_cfg = get_dict(root_config, "providers")
    service_base = get_dict(root_config, "serviceBase")
    runtime_cfg = get_dict(service_base, "runtime")
    provider_pool = get_dict(runtime_cfg, "provider_pool")
    if not provider_pool:
        provider_pool = get_dict(runtime_cfg, "providerPool")
    pool_providers = get_dict(provider_pool, "providers")
    stack = get_dict(get_dict(root_config, "stack"), "easyProtocol")
    network_name = str(stack.get("networkName") or "EasyAiMi")
    python_published_port = int(
        stack.get("pythonManagerPublishedPort")
        or stack.get("pythonPrimaryPublishedPort")
        or 11003
    )
    external_dependencies = get_dict(stack, "externalDependencies")
    easy_email = get_dict(external_dependencies, "easyEmail")
    easy_proxy = get_dict(external_dependencies, "easyProxy")

    runtime: dict[str, Any] = {
        "enabled": True,
        "docker_host": "unix:///var/run/docker.sock",
        "compose_project": "easy-protocol",
        "network_name": network_name,
        "providers": {},
    }

    for provider_key, defaults in (
        ("python", ("PythonProtocol", "easy-protocol-python", 9100, python_published_port)),
        ("go", ("GolangProtocol", "easy-protocol-go", 9100, 0)),
        ("javascript", ("JSProtocol", "easy-protocol-javascript", 9100, 0)),
        ("rust", ("RustProtocol", "easy-protocol-rust", 9100, 0)),
    ):
        provider = get_dict(providers_cfg, provider_key)
        registry = get_dict(provider, "registry")
        if not registry or not registry.get("enabled", False):
            continue
        service_name_prefix_default, container_prefix_default, port_default, published_port_default = defaults
        runtime["providers"][provider_key] = {
            "enabled": True,
            "image": str(provider.get("image") or "").strip(),
            "service_name_prefix": str(registry.get("serviceNamePrefix") or service_name_prefix_default).strip() or service_name_prefix_default,
            "container_name_prefix": str(registry.get("endpointHostPrefix") or container_prefix_default).strip() or container_prefix_default,
            "endpoint_host_prefix": str(registry.get("endpointHostPrefix") or container_prefix_default).strip() or container_prefix_default,
            "port": int(registry.get("port", port_default) or port_default),
            "published_port_base": int(registry.get("publishedPortBase", published_port_default) or published_port_default),
            "supported_operations": list(registry.get("supportedOperations") or []),
            "environment": {
                str(key): str(value)
                for key, value in get_dict(provider, "containerEnvironment").items()
            },
            "host_mounts": _build_managed_provider_host_mounts(provider_key, provider),
        }
        if provider_key == "python":
            env_map = runtime["providers"][provider_key]["environment"]
            env_map["MAILBOX_SERVICE_BASE_URL"] = _first_non_blank(
                easy_email.get("baseUrl"), env_map.get("MAILBOX_SERVICE_BASE_URL", "")
            )
            env_map["MAILBOX_SERVICE_API_KEY"] = _first_non_blank(
                easy_email.get("apiKey"), env_map.get("MAILBOX_SERVICE_API_KEY", "")
            )
            env_map["EASY_PROXY_BASE_URL"] = _first_non_blank(
                easy_proxy.get("baseUrl"), env_map.get("EASY_PROXY_BASE_URL", "")
            )
            env_map["EASY_PROXY_API_KEY"] = _first_non_blank(
                easy_proxy.get("apiKey"), env_map.get("EASY_PROXY_API_KEY", "")
            )
            env_map["M2U_EASY_PROXY_MAX_ATTEMPTS"] = str(
                easy_email.get("m2uEasyProxyMaxAttempts", env_map.get("M2U_EASY_PROXY_MAX_ATTEMPTS", 10))
            )
    return runtime


def build_service_base_runtime(root_config: dict[str, Any]) -> dict[str, Any]:
    template = load_yaml(SERVICE_TEMPLATE_PATH)
    service_base = get_dict(root_config, "serviceBase")
    runtime_overlay = get_dict(service_base, "runtime")
    merged = deep_merge(template, runtime_overlay)
    provider_pool = get_dict(merged, "provider_pool")
    if not provider_pool:
        provider_pool = get_dict(merged, "providerPool")
    pool_providers = get_dict(provider_pool, "providers")
    for provider_cfg in pool_providers.values():
        if not isinstance(provider_cfg, dict):
            continue
        provider_cfg["idle_scale_down_seconds"] = _normalize_duration_string(
            provider_cfg.get("idle_scale_down_seconds", provider_cfg.get("idleScaleDownSeconds")),
            default_seconds=120,
        )
        provider_cfg["acquire_timeout"] = _normalize_duration_string(
            provider_cfg.get("acquire_timeout", provider_cfg.get("acquireTimeout")),
            default_seconds=30,
        )
    generated_services = generate_registry_services(root_config)
    if generated_services:
        merged["services"] = generated_services
    merged["managed_provider_runtime"] = build_managed_provider_runtime(root_config)
    return merged


def build_service_base_env(root_config: dict[str, Any]) -> dict[str, str]:
    stack = get_dict(get_dict(root_config, "stack"), "easyProtocol")
    stack_runtime = get_dict(stack, "easyProtocol")
    return {
        "EASY_PROTOCOL_CONFIG_PATH": "/etc/easy-protocol/config.yaml",
        "EASY_PROTOCOL_RUNTIME_ENV_PATH": "/etc/easy-protocol/runtime.env",
        "EASY_PROTOCOL_BOOTSTRAP_PATH": "/etc/easy-protocol/bootstrap/r2-bootstrap.json",
        "EASY_PROTOCOL_STATE_DIR": "/var/lib/easy-protocol",
        "EASY_PROTOCOL_RESET_STORE_ON_BOOT": normalize_bool(stack_runtime.get("resetStoreOnBoot", False)),
    }


def build_easy_stack_env(root_config: dict[str, Any]) -> dict[str, str]:
    providers = get_dict(root_config, "providers")
    python_provider = get_dict(providers, "python")
    python_env = {
        str(key): str(value)
        for key, value in get_dict(python_provider, "containerEnvironment").items()
    }
    host_mounts = get_dict(python_provider, "hostMounts")
    stack = get_dict(get_dict(root_config, "stack"), "easyProtocol")
    stack_runtime = get_dict(stack, "easyProtocol")
    external_dependencies = get_dict(stack, "externalDependencies")
    easy_email = get_dict(external_dependencies, "easyEmail")
    easy_proxy = get_dict(external_dependencies, "easyProxy")

    env: dict[str, str] = dict(python_env)
    env["EASY_PROTOCOL_STACK_NETWORK"] = str(stack.get("networkName") or "EasyAiMi")
    env["EASY_PROTOCOL_GATEWAY_HOST_PORT"] = str(stack_runtime.get("publishedPort") or 19788)
    env["EASY_PROTOCOL_RESET_STORE_ON_BOOT"] = normalize_bool(stack_runtime.get("resetStoreOnBoot", False))
    env["PYTHON_PROTOCOL_MANAGER_HOST_PORT"] = str(
        stack.get("pythonManagerPublishedPort")
        or stack.get("pythonPrimaryPublishedPort")
        or 11003
    )
    env["MAILBOX_SERVICE_BASE_URL"] = _first_non_blank(
        easy_email.get("baseUrl"), env.get("MAILBOX_SERVICE_BASE_URL", "")
    )
    env["MAILBOX_SERVICE_API_KEY"] = _first_non_blank(
        easy_email.get("apiKey"), env.get("MAILBOX_SERVICE_API_KEY", "")
    )
    env["EASY_PROXY_BASE_URL"] = _first_non_blank(
        easy_proxy.get("baseUrl"), env.get("EASY_PROXY_BASE_URL", "")
    )
    env["EASY_PROXY_API_KEY"] = _first_non_blank(
        easy_proxy.get("apiKey"), env.get("EASY_PROXY_API_KEY", "")
    )
    env["EASY_EMAIL_RESET_STORE_ON_BOOT"] = str(easy_email.get("resetStoreOnBoot", False)).lower()
    env["M2U_EASY_PROXY_MAX_ATTEMPTS"] = str(easy_email.get("m2uEasyProxyMaxAttempts", 10))
    env["REGISTER_OUTPUT_DIR_HOST"] = str(host_mounts.get("registerOutputDirHost") or "")
    env["REGISTER_TEAM_AUTH_DIR_HOST"] = str(host_mounts.get("registerTeamAuthDirHost") or "")
    env["REGISTER_TEAM_LOCAL_DIR_HOST"] = str(host_mounts.get("registerTeamLocalDirHost") or "")
    return env


def write_env_file(path: Path, payload: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in payload.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def apply_render_overrides(
    root_config: dict[str, Any],
    *,
    register_output_dir_host: str = "",
    register_team_auth_dir_host: str = "",
    register_team_local_dir_host: str = "",
    python_provider_image: str = "",
) -> dict[str, Any]:
    providers = root_config.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        root_config["providers"] = providers
    python_provider = providers.setdefault("python", {})
    if not isinstance(python_provider, dict):
        python_provider = {}
        providers["python"] = python_provider
    host_mounts = python_provider.setdefault("hostMounts", {})
    if not isinstance(host_mounts, dict):
        host_mounts = {}
        python_provider["hostMounts"] = host_mounts

    overrides = {
        "registerOutputDirHost": register_output_dir_host,
        "registerTeamAuthDirHost": register_team_auth_dir_host,
        "registerTeamLocalDirHost": register_team_local_dir_host,
    }
    for key, value in overrides.items():
        normalized = str(value or "").strip()
        if normalized:
            host_mounts[key] = normalized

    normalized_image = str(python_provider_image or "").strip()
    if normalized_image:
        python_provider["image"] = normalized_image
    return root_config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render derived EasyProtocol config files from the root config.yaml."
    )
    parser.add_argument("--root-config", default=str(REPO_ROOT / "config.yaml"))
    parser.add_argument("--service-output", default="")
    parser.add_argument("--service-env-output", default="")
    parser.add_argument("--stack-config-output", default="")
    parser.add_argument("--stack-env-output", default="")
    parser.add_argument("--register-output-dir-host", default="")
    parser.add_argument("--register-team-auth-dir-host", default="")
    parser.add_argument("--register-team-local-dir-host", default="")
    parser.add_argument("--python-provider-image", default="")
    args = parser.parse_args()

    root_config_path = Path(args.root_config).resolve()
    if not root_config_path.exists():
        raise SystemExit(f"Root config not found: {root_config_path}")

    root_config = apply_render_overrides(
        load_yaml(root_config_path),
        register_output_dir_host=args.register_output_dir_host,
        register_team_auth_dir_host=args.register_team_auth_dir_host,
        register_team_local_dir_host=args.register_team_local_dir_host,
        python_provider_image=args.python_provider_image,
    )
    rendered_runtime = build_service_base_runtime(root_config)

    if args.service_output:
        dump_yaml(Path(args.service_output).resolve(), rendered_runtime)
    if args.service_env_output:
        write_env_file(Path(args.service_env_output).resolve(), build_service_base_env(root_config))

    if args.stack_config_output:
        dump_yaml(Path(args.stack_config_output).resolve(), rendered_runtime)

    if args.stack_env_output:
        write_env_file(Path(args.stack_env_output).resolve(), build_easy_stack_env(root_config))


if __name__ == "__main__":
    main()
