#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3


def load_bootstrap(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Bootstrap file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse bootstrap file {path}: {exc}") from exc


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}


def build_s3_client(bootstrap: dict[str, Any]):
    endpoint = str(bootstrap.get("endpoint") or "").strip()
    account_id = str(bootstrap.get("accountId") or "").strip()
    if not endpoint:
        if not account_id:
            raise SystemExit("Bootstrap file must provide either endpoint or accountId.")
        endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

    access_key_id = str(bootstrap.get("accessKeyId") or "").strip()
    secret_access_key = str(bootstrap.get("secretAccessKey") or "").strip()
    if not access_key_id or not secret_access_key:
        raise SystemExit("Bootstrap file must provide accessKeyId and secretAccessKey.")

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="auto",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )


def hash_hex(content: bytes, algorithm: str) -> str:
    return hashlib.new(algorithm, content).hexdigest()


def download_object(client: Any, *, bucket: str, object_key: str) -> bytes:
    response = client.get_object(Bucket=bucket, Key=object_key)
    return response["Body"].read()


def write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_bytes(content)
    os.replace(temp_path, path)


def resolve_distribution(
    client: Any,
    bootstrap: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    bucket = str(bootstrap.get("bucket") or "").strip()
    if not bucket:
        raise SystemExit("Bootstrap file must provide bucket.")

    manifest_object_key = str(bootstrap.get("manifestObjectKey") or "").strip()
    if manifest_object_key:
        manifest_bytes = download_object(client, bucket=bucket, object_key=manifest_object_key)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        service_base = manifest.get("serviceBase") or {}
        config_entry = service_base.get("config") or {}
        runtime_env_entry = service_base.get("runtimeEnv") or {}
        if not config_entry.get("objectKey"):
            raise SystemExit(f"Manifest {manifest_object_key} does not contain serviceBase.config.objectKey.")
        resolved = {
            "bucket": bucket,
            "configObjectKey": str(config_entry.get("objectKey") or "").strip(),
            "runtimeEnvObjectKey": str(runtime_env_entry.get("objectKey") or "").strip(),
            "expectedConfigSha256": str(config_entry.get("sha256") or bootstrap.get("expectedConfigSha256") or "").strip(),
            "expectedRuntimeEnvSha256": str(runtime_env_entry.get("sha256") or bootstrap.get("expectedRuntimeEnvSha256") or "").strip(),
            "configMd5": str(config_entry.get("md5") or "").strip(),
            "runtimeEnvMd5": str(runtime_env_entry.get("md5") or "").strip(),
            "fingerprint": str(service_base.get("fingerprint") or "").strip(),
            "manifestObjectKey": manifest_object_key,
            "manifestSha256": hash_hex(manifest_bytes, "sha256"),
        }
        return resolved, manifest

    config_object_key = str(bootstrap.get("configObjectKey") or bootstrap.get("objectKey") or "").strip()
    runtime_env_object_key = str(bootstrap.get("runtimeEnvObjectKey") or "").strip()
    if not config_object_key:
        raise SystemExit("Bootstrap file must provide manifestObjectKey or configObjectKey.")

    resolved = {
        "bucket": bucket,
        "configObjectKey": config_object_key,
        "runtimeEnvObjectKey": runtime_env_object_key,
        "expectedConfigSha256": str(bootstrap.get("expectedConfigSha256") or "").strip(),
        "expectedRuntimeEnvSha256": str(bootstrap.get("expectedRuntimeEnvSha256") or "").strip(),
        "configMd5": "",
        "runtimeEnvMd5": "",
        "fingerprint": f"{config_object_key}:{runtime_env_object_key}",
        "manifestObjectKey": "",
        "manifestSha256": "",
    }
    return resolved, None


def save_state(path: Path, *, bootstrap: dict[str, Any], distribution: dict[str, Any]) -> None:
    state = {
        "schemaVersion": 1,
        "lastSyncedAtUtc": datetime.now(timezone.utc).isoformat(),
        "distribution": {
            "accountId": str(bootstrap.get("accountId") or "").strip(),
            "endpoint": str(bootstrap.get("endpoint") or "").strip(),
            "bucket": distribution["bucket"],
            "manifestObjectKey": distribution["manifestObjectKey"],
            "manifestSha256": distribution["manifestSha256"],
            "fingerprint": distribution["fingerprint"],
        },
        "serviceBase": {
            "configObjectKey": distribution["configObjectKey"],
            "configSha256": distribution["expectedConfigSha256"],
            "configMd5": distribution["configMd5"],
            "runtimeEnvObjectKey": distribution["runtimeEnvObjectKey"],
            "runtimeEnvSha256": distribution["expectedRuntimeEnvSha256"],
            "runtimeEnvMd5": distribution["runtimeEnvMd5"],
        },
        "sync": {
            "enabled": bool(bootstrap.get("syncEnabled", True)),
            "intervalSeconds": int(bootstrap.get("syncIntervalSeconds") or 7200),
        },
    }
    write_atomic(path, json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8"))


def maybe_verify_sha256(content: bytes, expected_value: str, object_key: str) -> None:
    expected = expected_value.strip()
    if not expected:
        return
    actual = hash_hex(content, "sha256")
    if actual != expected:
        raise SystemExit(
            f"Downloaded object sha256 mismatch for {object_key}: expected {expected}, got {actual}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch EasyProtocol runtime config artifacts from Cloudflare R2 before container startup.")
    parser.add_argument("--bootstrap-path", required=True)
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--runtime-env-path", required=True)
    parser.add_argument("--state-path", default="")
    parser.add_argument("--mode", choices=["initial", "sync"], default="initial")
    parser.add_argument("--updated-flag-path", default="")
    args = parser.parse_args()

    bootstrap_path = Path(args.bootstrap_path).resolve()
    config_path = Path(args.config_path).resolve()
    runtime_env_path = Path(args.runtime_env_path).resolve()
    state_path = Path(args.state_path).resolve() if args.state_path else config_path.parent / ".import-state.json"
    updated_flag_path = Path(args.updated_flag_path).resolve() if args.updated_flag_path else None

    bootstrap = load_bootstrap(bootstrap_path)
    client = build_s3_client(bootstrap)
    distribution, _ = resolve_distribution(client, bootstrap)
    previous_state = load_json_if_exists(state_path)
    previous_fingerprint = str((previous_state.get("distribution") or {}).get("fingerprint") or "").strip()
    current_fingerprint = str(distribution.get("fingerprint") or "").strip()

    if (
        args.mode == "sync"
        and current_fingerprint
        and current_fingerprint == previous_fingerprint
        and config_path.exists()
        and (not distribution["runtimeEnvObjectKey"] or runtime_env_path.exists())
    ):
        print("[easy-protocol] remote config fingerprint unchanged; skipping sync")
        return 0

    print(
        "[easy-protocol] downloading runtime config from R2 "
        f"bucket={distribution['bucket']} key={distribution['configObjectKey']}"
    )
    config_bytes = download_object(
        client,
        bucket=distribution["bucket"],
        object_key=distribution["configObjectKey"],
    )
    maybe_verify_sha256(config_bytes, distribution["expectedConfigSha256"], distribution["configObjectKey"])
    write_atomic(config_path, config_bytes)

    runtime_env_key = distribution["runtimeEnvObjectKey"]
    if runtime_env_key:
        print(
            "[easy-protocol] downloading runtime env from R2 "
            f"bucket={distribution['bucket']} key={runtime_env_key}"
        )
        runtime_env_bytes = download_object(
            client,
            bucket=distribution["bucket"],
            object_key=runtime_env_key,
        )
        maybe_verify_sha256(runtime_env_bytes, distribution["expectedRuntimeEnvSha256"], runtime_env_key)
        write_atomic(runtime_env_path, runtime_env_bytes)

    save_state(state_path, bootstrap=bootstrap, distribution=distribution)

    if (
        args.mode == "sync"
        and updated_flag_path is not None
        and current_fingerprint
        and current_fingerprint != previous_fingerprint
    ):
        updated_flag_path.parent.mkdir(parents=True, exist_ok=True)
        updated_flag_path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
