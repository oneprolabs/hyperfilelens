"""Owned temporary Bucket and Kopia round-trip validation."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from apps.storage.conf import provider_validation_region_timeout_seconds
from apps.storage.provider_catalog.credentials import ProviderCredentials
from apps.storage.provider_catalog.models import StorageProviderRegionValidation
from apps.storage.provider_catalog.security import validate_managed_endpoint_network
from apps.storage.s3_compat import (
    is_s3_batch_delete_compatibility_error,
    register_s3_delete_objects_compatibility,
)


OWNERSHIP_KEY = ".hyperfilelens-validation/ownership-v1.json"


class ProviderRegionValidationError(Exception):
    def __init__(self, code: str, message: str, *, cleanup_required: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.cleanup_required = cleanup_required


class ProviderValidationCancelled(ProviderRegionValidationError):
    def __init__(self):
        super().__init__("VALIDATION_CANCELLED", "Validation was cancelled.")


@dataclass(frozen=True)
class RegionValidationContext:
    run_id: UUID
    provider_id: str
    region: dict
    credentials: ProviderCredentials


StepCallback = Callable[[str], None]
BucketCallback = Callable[[str | None], None]
CancelCallback = Callable[[], bool]


def _ownership_bytes(context: RegionValidationContext, bucket_name: str) -> bytes:
    message = (
        f"{context.run_id}:{context.region['id']}:{bucket_name}:ownership-v1"
    ).encode("utf-8")
    signature = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    return json.dumps(
        {
            "version": 1,
            "run_id": str(context.run_id),
            "region_id": context.region["id"],
            "proof": signature,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _bucket_name(context: RegionValidationContext) -> str:
    region = re.sub(r"[^a-z0-9-]", "-", context.region["id"].lower()).strip("-")
    return f"hfl-val-{context.run_id.hex[:10]}-{region[:20]}-{uuid4().hex[:8]}"[:63]


def _s3_client(context: RegionValidationContext):
    style = "virtual" if context.region["s3_url_style"] == "virtual_hosted" else "path"
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{context.region['external_endpoint']}",
        region_name=context.region["id"],
        aws_access_key_id=context.credentials.access_key_id,
        aws_secret_access_key=context.credentials.secret_access_key,
        use_ssl=True,
        verify=True,
        config=Config(
            signature_version="s3v4",
            connect_timeout=15,
            read_timeout=60,
            retries={"max_attempts": 2, "mode": "standard"},
            s3={"addressing_style": style},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )
    register_s3_delete_objects_compatibility(client)
    return client


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code") or "")


def _bucket_exists(client, bucket_name: str) -> bool:
    try:
        client.head_bucket(Bucket=bucket_name)
        return True
    except ClientError as exc:
        if _error_code(exc) in {"404", "NoSuchBucket", "NotFound"}:
            return False
        # Access denied means the globally unique name is already occupied.
        if _error_code(exc) in {"403", "AccessDenied", "Forbidden"}:
            return True
        raise


def _create_bucket_args(
    context: RegionValidationContext,
    bucket_name: str,
) -> dict[str, object]:
    args: dict[str, object] = {"Bucket": bucket_name}
    region = str(context.region.get("id") or "").strip()
    if region and region != "us-east-1":
        args["CreateBucketConfiguration"] = {"LocationConstraint": region}
    return args


def _create_owned_bucket(
    context: RegionValidationContext,
    *,
    client,
    set_bucket: BucketCallback,
) -> str:
    for _attempt in range(5):
        bucket_name = _bucket_name(context)
        set_bucket(bucket_name)
        if _bucket_exists(client, bucket_name):
            set_bucket(None)
            continue
        try:
            client.create_bucket(**_create_bucket_args(context, bucket_name))
        except ClientError as exc:
            if _error_code(exc) in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
                set_bucket(None)
                continue
            set_bucket(None)
            raise
        except BotoCoreError as exc:
            raise ProviderRegionValidationError(
                "BUCKET_CREATE_UNCERTAIN",
                "Temporary Bucket creation result is uncertain.",
                cleanup_required=True,
            ) from exc
        proof = _ownership_bytes(context, bucket_name)
        try:
            client.put_object(
                Bucket=bucket_name,
                Key=OWNERSHIP_KEY,
                Body=proof,
                ContentLength=len(proof),
                ContentType="application/json",
            )
        except Exception as exc:
            raise ProviderRegionValidationError(
                "BUCKET_OWNERSHIP_UNPROVEN",
                "Temporary Bucket ownership proof could not be created.",
                cleanup_required=True,
            ) from exc
        return bucket_name
    raise ProviderRegionValidationError(
        "BUCKET_NAME_EXHAUSTED",
        "Unable to allocate a unique validation Bucket name.",
    )


def _cloud_operation_message(exc: BotoCoreError | ClientError) -> str:
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = str(error.get("Code") or "").strip()
        message = str(error.get("Message") or "").strip()
        detail = ": ".join(part for part in (code, message) if part)
        if detail:
            return f"Cloud storage validation failed: {detail}"
    return "Cloud storage validation failed."


def _verify_ownership(
    context: RegionValidationContext, *, client, bucket_name: str
) -> None:
    try:
        response = client.get_object(Bucket=bucket_name, Key=OWNERSHIP_KEY)
        body = response["Body"].read(4096)
    except Exception as exc:
        raise ProviderRegionValidationError(
            "BUCKET_OWNERSHIP_UNPROVEN",
            "Temporary Bucket ownership proof could not be verified.",
            cleanup_required=True,
        ) from exc
    if not hmac.compare_digest(body, _ownership_bytes(context, bucket_name)):
        raise ProviderRegionValidationError(
            "BUCKET_OWNERSHIP_UNPROVEN",
            "Temporary Bucket ownership proof did not match this validation run.",
            cleanup_required=True,
        )


def _chunks(items: list[dict], size: int = 1000):
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


def _delete_entries(client, bucket_name: str, entries: list[dict]) -> None:
    for batch in _chunks(entries):
        try:
            response = client.delete_objects(
                Bucket=bucket_name,
                Delete={"Objects": batch, "Quiet": True},
            )
        except ClientError as exc:
            if not is_s3_batch_delete_compatibility_error(exc):
                raise
            for entry in batch:
                client.delete_object(Bucket=bucket_name, **entry)
            continue
        if response.get("Errors"):
            raise ProviderRegionValidationError(
                "BUCKET_CLEANUP_FAILED",
                "Temporary Bucket object deletion was incomplete.",
                cleanup_required=True,
            )


def _delete_owned_bucket(
    context: RegionValidationContext,
    *,
    client,
    bucket_name: str,
) -> None:
    _verify_ownership(context, client=client, bucket_name=bucket_name)

    upload_pages = client.get_paginator("list_multipart_uploads")
    for page in upload_pages.paginate(Bucket=bucket_name):
        for upload in page.get("Uploads", []):
            client.abort_multipart_upload(
                Bucket=bucket_name,
                Key=upload["Key"],
                UploadId=upload["UploadId"],
            )

    try:
        version_pages = client.get_paginator("list_object_versions")
        for page in version_pages.paginate(Bucket=bucket_name):
            entries = [
                {"Key": item["Key"], "VersionId": item["VersionId"]}
                for item in [
                    *page.get("Versions", []),
                    *page.get("DeleteMarkers", []),
                ]
                if item.get("Key") and item.get("VersionId")
            ]
            _delete_entries(client, bucket_name, entries)
    except ClientError as exc:
        if _error_code(exc) not in {"NotImplemented", "MethodNotAllowed"}:
            raise

    object_pages = client.get_paginator("list_objects_v2")
    for page in object_pages.paginate(Bucket=bucket_name):
        entries = [
            {"Key": item["Key"]} for item in page.get("Contents", []) if item.get("Key")
        ]
        _delete_entries(client, bucket_name, entries)

    client.delete_bucket(Bucket=bucket_name)
    try:
        client.head_bucket(Bucket=bucket_name)
    except ClientError as exc:
        if _error_code(exc) in {"404", "NoSuchBucket", "NotFound"}:
            return
        raise
    raise ProviderRegionValidationError(
        "BUCKET_CLEANUP_UNCONFIRMED",
        "Temporary Bucket still exists after deletion.",
        cleanup_required=True,
    )


def _kopia_path() -> str:
    configured = os.getenv("HFL_KOPIA_PATH", "").strip()
    result = configured or shutil.which("kopia")
    if not result:
        raise ProviderRegionValidationError(
            "KOPIA_UNAVAILABLE",
            "Kopia CLI is unavailable in the Provider validation Worker.",
        )
    return result


def _run_kopia(
    command: list[str],
    *,
    env: dict[str, str],
    timeout_at: float,
    cancelled: CancelCallback,
) -> str:
    process = subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    while True:
        if cancelled():
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
            raise ProviderValidationCancelled()
        remaining = timeout_at - time.monotonic()
        if remaining <= 0:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
            raise ProviderRegionValidationError(
                "VALIDATION_TIMEOUT",
                "Region validation exceeded its time limit.",
            )
        try:
            stdout, stderr = process.communicate(timeout=min(0.5, remaining))
            break
        except subprocess.TimeoutExpired:
            continue
    if process.returncode != 0:
        output = " ".join(f"{stdout}\n{stderr}".split())[-1000:]
        raise ProviderRegionValidationError(
            "KOPIA_COMMAND_FAILED",
            f"Kopia validation command failed with exit code {process.returncode}: {output}",
        )
    return stdout


def _snapshot_id(value: object) -> str | None:
    if isinstance(value, dict):
        direct = value.get("id")
        if isinstance(direct, str) and direct:
            return direct
        for child in value.values():
            found = _snapshot_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _snapshot_id(child)
            if found:
                return found
    return None


def _validate_kopia(
    context: RegionValidationContext,
    *,
    bucket_name: str,
    step: StepCallback,
    cancelled: CancelCallback,
) -> None:
    timeout_at = time.monotonic() + provider_validation_region_timeout_seconds()
    prefix = f"hfl-validation/{context.run_id}/{context.region['id']}"
    password = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        f"{context.run_id}:{context.region['id']}:kopia-v1".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    with tempfile.TemporaryDirectory(prefix="hfl-provider-validation-") as work:
        root = Path(work)
        config_file = root / "repository.config"
        source_dir = root / "source"
        restore_dir = root / "restore"
        source_dir.mkdir(mode=0o700)
        restore_dir.mkdir(mode=0o700)
        test_file = source_dir / "validation.bin"
        seed = hashlib.sha256(
            f"{context.run_id}:{context.region['id']}".encode("utf-8")
        ).digest()
        content = (seed * ((1024 * 1024 // len(seed)) + 1))[: 1024 * 1024]
        test_file.write_bytes(content)
        expected_hash = hashlib.sha256(content).hexdigest()

        env = os.environ.copy()
        env.update(
            {
                "AWS_ACCESS_KEY_ID": context.credentials.access_key_id,
                "AWS_SECRET_ACCESS_KEY": context.credentials.secret_access_key,
                "AWS_REGION": context.region["id"],
                "AWS_DEFAULT_REGION": context.region["id"],
                "KOPIA_PASSWORD": password,
                "KOPIA_CHECK_FOR_UPDATES": "false",
                "KOPIA_USE_KEYRING": "false",
                "KOPIA_PERSIST_CREDENTIALS_ON_CONNECT": "false",
            }
        )
        base = [
            _kopia_path(),
            f"--config-file={config_file}",
            "--no-persist-credentials",
            "--no-progress",
        ]
        flags = [
            f"--bucket={bucket_name}",
            f"--endpoint={context.region['external_endpoint']}",
            f"--region={context.region['id']}",
            f"--prefix={prefix}",
            "--url-style="
            + (
                "virtual-hosted"
                if context.region["s3_url_style"] == "virtual_hosted"
                else "path"
            ),
        ]

        step(StorageProviderRegionValidation.Step.INITIALIZE_KOPIA)
        _run_kopia(
            [*base, "repository", "create", "s3", *flags],
            env=env,
            timeout_at=timeout_at,
            cancelled=cancelled,
        )
        _run_kopia(
            [*base, "repository", "status"],
            env=env,
            timeout_at=timeout_at,
            cancelled=cancelled,
        )

        step(StorageProviderRegionValidation.Step.BACKUP)
        raw_snapshot = _run_kopia(
            [*base, "snapshot", "create", str(source_dir), "--json"],
            env=env,
            timeout_at=timeout_at,
            cancelled=cancelled,
        )
        try:
            snapshot_id = _snapshot_id(json.loads(raw_snapshot))
        except json.JSONDecodeError as exc:
            raise ProviderRegionValidationError(
                "KOPIA_RESULT_INVALID",
                "Kopia returned an invalid snapshot result.",
            ) from exc
        if not snapshot_id:
            raise ProviderRegionValidationError(
                "KOPIA_SNAPSHOT_MISSING",
                "Kopia did not return a readable snapshot ID.",
            )
        _run_kopia(
            [*base, "snapshot", "list", "--json"],
            env=env,
            timeout_at=timeout_at,
            cancelled=cancelled,
        )

        step(StorageProviderRegionValidation.Step.RESTORE)
        _run_kopia(
            [*base, "snapshot", "restore", snapshot_id, str(restore_dir)],
            env=env,
            timeout_at=timeout_at,
            cancelled=cancelled,
        )
        step(StorageProviderRegionValidation.Step.VERIFY_HASH)
        restored_files = list(restore_dir.rglob("validation.bin"))
        if len(restored_files) != 1:
            raise ProviderRegionValidationError(
                "RESTORE_FILE_MISSING",
                "Kopia restore did not produce the validation file.",
            )
        actual_hash = hashlib.sha256(restored_files[0].read_bytes()).hexdigest()
        if not hmac.compare_digest(expected_hash, actual_hash):
            raise ProviderRegionValidationError(
                "RESTORE_HASH_MISMATCH",
                "Restored validation data did not match the source hash.",
            )

        step(StorageProviderRegionValidation.Step.CLEANUP_REPOSITORY)
        _run_kopia(
            [*base, "snapshot", "delete", snapshot_id, "--delete"],
            env=env,
            timeout_at=timeout_at,
            cancelled=cancelled,
        )


def validate_region(
    context: RegionValidationContext,
    *,
    step: StepCallback,
    set_bucket: BucketCallback,
    cancelled: CancelCallback,
) -> None:
    validate_managed_endpoint_network(f"https://{context.region['external_endpoint']}")
    client = _s3_client(context)
    bucket_name: str | None = None
    error: Exception | None = None
    cleanup_error: Exception | None = None
    try:
        if cancelled():
            raise ProviderValidationCancelled()
        step(StorageProviderRegionValidation.Step.CREATE_BUCKET)
        bucket_name = _create_owned_bucket(
            context, client=client, set_bucket=set_bucket
        )
        _validate_kopia(
            context,
            bucket_name=bucket_name,
            step=step,
            cancelled=cancelled,
        )
    except Exception as exc:
        error = exc
    finally:
        if bucket_name:
            try:
                step(StorageProviderRegionValidation.Step.DELETE_BUCKET)
                _delete_owned_bucket(
                    context,
                    client=client,
                    bucket_name=bucket_name,
                )
                step(StorageProviderRegionValidation.Step.VERIFY_CLEANUP)
                set_bucket(None)
            except Exception as exc:
                cleanup_error = exc

    if cleanup_error is not None:
        if isinstance(cleanup_error, ProviderRegionValidationError):
            raise ProviderRegionValidationError(
                cleanup_error.code,
                cleanup_error.message,
                cleanup_required=True,
            ) from cleanup_error
        raise ProviderRegionValidationError(
            "BUCKET_CLEANUP_FAILED",
            "Temporary Bucket cleanup failed.",
            cleanup_required=True,
        ) from cleanup_error
    if error is not None:
        if isinstance(error, ProviderRegionValidationError):
            raise error
        if isinstance(error, (BotoCoreError, ClientError)):
            raise ProviderRegionValidationError(
                "CLOUD_OPERATION_FAILED",
                _cloud_operation_message(error),
            ) from error
        raise ProviderRegionValidationError(
            "VALIDATION_FAILED",
            "Provider region validation failed.",
        ) from error


def cleanup_region(
    context: RegionValidationContext,
    *,
    bucket_name: str,
    step: StepCallback,
    set_bucket: BucketCallback,
) -> None:
    """Delete only a Bucket whose cryptographic ownership marker still matches."""

    validate_managed_endpoint_network(f"https://{context.region['external_endpoint']}")
    client = _s3_client(context)
    try:
        step(StorageProviderRegionValidation.Step.DELETE_BUCKET)
        _delete_owned_bucket(
            context,
            client=client,
            bucket_name=bucket_name,
        )
        step(StorageProviderRegionValidation.Step.VERIFY_CLEANUP)
        set_bucket(None)
    except ProviderRegionValidationError:
        raise
    except Exception as exc:
        raise ProviderRegionValidationError(
            "BUCKET_CLEANUP_FAILED",
            "Temporary Bucket cleanup failed.",
            cleanup_required=True,
        ) from exc
