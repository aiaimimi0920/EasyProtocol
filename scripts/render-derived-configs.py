from __future__ import annotations

import argparse
import copy
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


def _normalize_replica_count(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(0, parsed)


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


def build_service_base_runtime(root_config: dict[str, Any]) -> dict[str, Any]:
    template = load_yaml(SERVICE_TEMPLATE_PATH)
    service_base = get_dict(root_config, "serviceBase")
    runtime_overlay = get_dict(service_base, "runtime")
    merged = deep_merge(template, runtime_overlay)
    generated_services = generate_registry_services(root_config)
    if generated_services:
        merged["services"] = generated_services
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
    env["MAILBOX_SERVICE_API_KEY"] = str(easy_email.get("apiKey", env.get("MAILBOX_SERVICE_API_KEY", "")) or "")
    env["EASY_PROXY_API_KEY"] = str(easy_proxy.get("apiKey", env.get("EASY_PROXY_API_KEY", "")) or "")
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render derived EasyProtocol config files from the root config.yaml."
    )
    parser.add_argument("--root-config", default=str(REPO_ROOT / "config.yaml"))
    parser.add_argument("--service-output", default="")
    parser.add_argument("--service-env-output", default="")
    parser.add_argument("--stack-config-output", default="")
    parser.add_argument("--stack-env-output", default="")
    args = parser.parse_args()

    root_config_path = Path(args.root_config).resolve()
    if not root_config_path.exists():
        raise SystemExit(f"Root config not found: {root_config_path}")

    root_config = load_yaml(root_config_path)
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
