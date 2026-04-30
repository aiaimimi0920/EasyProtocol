from __future__ import annotations

import datetime
import mimetypes
import os
from pathlib import Path
from typing import Any


def _require_text(value: Any, *, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"{name}_required")
    return text


def _optional_text(value: Any) -> str:
    return str(value or "").strip()


def _resolve_input_or_env(step_input: dict[str, Any], input_key: str, env_name: str) -> str:
    direct = _optional_text(step_input.get(input_key))
    if direct:
        return direct
    return _optional_text(os.environ.get(env_name))


def _normalize_target_folder(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").strip().strip("/")
    return "/".join(part for part in normalized.split("/") if part)


def _normalize_object_name(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").strip().strip("/")
    if not normalized:
        return ""
    return "/".join(part for part in normalized.split("/") if part)


def _compose_object_key(*, target_folder: str, object_name: str) -> str:
    folder = _normalize_target_folder(target_folder)
    name = _normalize_object_name(object_name)
    if not name:
        raise RuntimeError("object_name_required")
    if folder:
        return f"{folder}/{name}"
    return name


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _guess_content_type(source_path: Path, explicit_content_type: str) -> str:
    normalized = _optional_text(explicit_content_type)
    if normalized:
        return normalized
    guessed, _ = mimetypes.guess_type(str(source_path))
    return guessed or "application/octet-stream"


def _resolve_endpoint_url(*, step_input: dict[str, Any]) -> str:
    explicit = _resolve_input_or_env(step_input, "endpoint_url", "REGISTER_R2_ENDPOINT_URL")
    if explicit:
        return explicit.rstrip("/")
    account_id = _resolve_input_or_env(step_input, "account_id", "REGISTER_R2_ACCOUNT_ID")
    if not account_id:
        raise RuntimeError("endpoint_url_or_account_id_required")
    return f"https://{account_id}.r2.cloudflarestorage.com"


def _resolve_public_url(*, public_base_url: str, object_key: str) -> str:
    base = _optional_text(public_base_url).rstrip("/")
    if not base:
        return ""
    return f"{base}/{object_key}"


def _build_object_url(*, endpoint_url: str, bucket: str, object_key: str) -> str:
    base = str(endpoint_url or "").rstrip("/")
    return f"{base}/{bucket}/{object_key}"


def _load_boto_modules() -> tuple[Any, Any]:
    try:
        import boto3  # type: ignore
        import botocore.auth  # type: ignore
        import botocore.utils  # type: ignore
        from botocore.exceptions import ClientError  # type: ignore
    except Exception as exc:  # pragma: no cover - import failure path
        raise RuntimeError(f"r2_upload_dependency_missing:{exc}") from exc
    _repair_polluted_botocore(botocore_auth=botocore.auth, botocore_utils=botocore.utils)
    return boto3, ClientError


def _repair_polluted_botocore(*, botocore_auth: Any, botocore_utils: Any) -> None:
    if not getattr(botocore_auth, "_register_r2_repaired", False):
        no_credentials_error = getattr(botocore_auth, "NoCredentialsError")
        sigv4_timestamp = getattr(botocore_auth, "SIGV4_TIMESTAMP")

        def _clean_add_auth(self: Any, request: Any) -> Any:
            if self.credentials is None:
                raise no_credentials_error()
            datetime_now = datetime.datetime.utcnow()
            request.context["timestamp"] = datetime_now.strftime(sigv4_timestamp)
            self._modify_request_before_signing(request)
            canonical_request = self.canonical_request(request)
            string_to_sign = self.string_to_sign(request, canonical_request)
            signature = self.signature(string_to_sign, request)
            self._inject_signature_to_request(request, signature)
            return request

        botocore_auth.SigV4Auth.add_auth = _clean_add_auth
        botocore_auth._register_r2_repaired = True

    if not getattr(botocore_utils, "_register_r2_md5_repaired", False):
        get_md5 = getattr(botocore_utils, "get_md5")

        def _clean_md5_from_bytes(body_bytes: bytes) -> bytes:
            md5 = get_md5(body_bytes)
            return md5.digest()

        def _clean_md5_from_file(fileobj: Any) -> bytes:
            start_position = fileobj.tell()
            md5 = get_md5()
            for chunk in iter(lambda: fileobj.read(1024 * 1024), b""):
                md5.update(chunk)
            fileobj.seek(start_position)
            return md5.digest()

        botocore_utils._calculate_md5_from_bytes = _clean_md5_from_bytes
        botocore_utils._calculate_md5_from_file = _clean_md5_from_file
        botocore_utils._register_r2_md5_repaired = True


def upload_file_to_r2(*, step_input: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(step_input, dict):
        raise RuntimeError("step_input_invalid")

    source_path_text = _require_text(step_input.get("source_path"), name="source_path")
    source_path = Path(source_path_text).expanduser().resolve()
    if not source_path.exists():
        raise RuntimeError(f"source_path_not_found:{source_path}")
    if not source_path.is_file():
        raise RuntimeError(f"source_path_not_file:{source_path}")

    if "target_folder" not in step_input:
        raise RuntimeError("target_folder_required")
    target_folder = _normalize_target_folder(step_input.get("target_folder"))

    bucket = _resolve_input_or_env(step_input, "bucket", "REGISTER_R2_BUCKET")
    bucket = _require_text(bucket, name="bucket")

    access_key_id = _resolve_input_or_env(step_input, "access_key_id", "REGISTER_R2_ACCESS_KEY_ID")
    access_key_id = _require_text(access_key_id, name="access_key_id")

    secret_access_key = _resolve_input_or_env(step_input, "secret_access_key", "REGISTER_R2_SECRET_ACCESS_KEY")
    secret_access_key = _require_text(secret_access_key, name="secret_access_key")

    endpoint_url = _resolve_endpoint_url(step_input=step_input)
    region = _resolve_input_or_env(step_input, "region", "REGISTER_R2_REGION") or "auto"
    object_name = _normalize_object_name(step_input.get("object_name") or source_path.name)
    object_key = _compose_object_key(target_folder=target_folder, object_name=object_name)
    content_type = _guess_content_type(source_path, _optional_text(step_input.get("content_type")))
    overwrite = _coerce_bool(step_input.get("overwrite"), default=True)
    public_base_url = _resolve_input_or_env(step_input, "public_base_url", "REGISTER_R2_PUBLIC_BASE_URL")

    boto3, ClientError = _load_boto_modules()
    client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )

    if not overwrite:
        try:
            client.head_object(Bucket=bucket, Key=object_key)
        except ClientError as exc:
            error_code = str(((exc.response or {}).get("Error") or {}).get("Code") or "").strip()
            if error_code not in {"404", "NoSuchKey", "NotFound"}:
                raise RuntimeError(f"r2_head_object_failed:{exc}") from exc
        else:
            raise RuntimeError(f"object_exists:{object_key}")

    extra_args = {"ContentType": content_type}
    try:
        with source_path.open("rb") as handle:
            response = client.put_object(
                Bucket=bucket,
                Key=object_key,
                Body=handle,
                **extra_args,
            )
    except Exception as exc:
        raise RuntimeError(f"r2_upload_failed:{exc}") from exc

    size = int(source_path.stat().st_size)
    etag = str(response.get("ETag") or "").strip()
    result = {
        "ok": True,
        "provider": "r2",
        "bucket": bucket,
        "target_folder": target_folder,
        "object_name": object_name,
        "object_key": object_key,
        "source_path": str(source_path),
        "endpoint_url": endpoint_url,
        "object_url": _build_object_url(endpoint_url=endpoint_url, bucket=bucket, object_key=object_key),
        "public_url": _resolve_public_url(public_base_url=public_base_url, object_key=object_key),
        "etag": etag,
        "size": size,
        "content_type": content_type,
        "overwrite": overwrite,
        "region": region,
    }
    return result


__all__ = ["upload_file_to_r2"]
