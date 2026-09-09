"""Prepare S3 storage before persisting a repository or its credentials."""

from __future__ import annotations

import hashlib
from functools import wraps

from botocore.exceptions import BotoCoreError, ClientError
from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_endpoints import (
    repository_control_endpoint,
)
from apps.storage.services.internal.repository_execution_lock import (
    repository_execution_lock,
)
from apps.storage.services.internal.repository_location import (
    repository_location_spec,
    _roots_overlap,
)
from apps.storage.services.internal.repository_secrets import (
    build_secret_payload,
    scrub_secrets,
)
from apps.storage.services.internal.s3_client import (
    S3ClientError,
    _client,
    create_s3_bucket,
    _s3_atomic_create_strategy,
    _ATOMIC_CREATE_ALIYUN_FORBID_OVERWRITE,
    _ATOMIC_CREATE_HUAWEI_FORBID_OVERWRITE,
    _add_aliyun_forbid_overwrite_header,
    _add_huawei_forbid_overwrite_header,
    delete_s3_bucket_if_empty,
)
from apps.storage.services.internal.s3_url_style import normalize_s3_url_style

PROBE_NAME = ".hfl-repository-probe-550e8400-e29b-41d4-a716-446655440000.tmp"
PROBE_METADATA = {"hfl-purpose": "repository-preflight", "hfl-probe-version": "1"}
PROBE_BODY = b"HyperFileLens repository preflight v1\n"


class S3BucketAlreadyExists(S3ClientError):
    pass


def ensure_s3_bucket_absent(**bucket_args) -> None:
    """Some providers return success for CreateBucket on an owned bucket."""
    client = _client(
        **{k: v for k, v in bucket_args.items() if k != "bucket"}, timeout_seconds=15
    )
    try:
        client.head_bucket(Bucket=bucket_args["bucket"])
    except ClientError as exc:
        if str(exc.response.get("Error", {}).get("Code")) in {
            "404",
            "NoSuchBucket",
            "NotFound",
        }:
            return
        raise S3ClientError(
            "Unable to confirm that the Bucket name is available. Check permissions and the endpoint."
        ) from exc
    except BotoCoreError as exc:
        raise S3ClientError(
            "Unable to check whether the Bucket already exists."
        ) from exc
    raise S3BucketAlreadyExists(
        "Bucket already exists. Choose another name or select Existing Bucket."
    )


def verify_empty_prefix(
    *, prefix: str, platform: str | None = None, **bucket_args
) -> None:
    """List, PUT and DELETE only; HEAD verifies residue metadata without GET."""
    client = _client(
        **{k: v for k, v in bucket_args.items() if k != "bucket"}, timeout_seconds=15
    )
    bucket = bucket_args["bucket"]
    key = f"{prefix}{PROBE_NAME}"

    def remove_probe():
        # Delete explicit versions, never create a delete marker. Repeated HEAD
        # also exposes older probe versions left by interrupted attempts.
        for _ in range(100):
            try:
                head = client.head_object(Bucket=bucket, Key=key)
            except ClientError as exc:
                if str(exc.response.get("Error", {}).get("Code")) in {
                    "404",
                    "NoSuchKey",
                    "NotFound",
                }:
                    return
                raise
            if head.get("Metadata") != PROBE_METADATA or head.get(
                "ContentLength"
            ) != len(PROBE_BODY):
                raise S3ClientError(
                    f"The reserved probe object has unrecognized metadata: {key}. It was not deleted."
                )
            args = {"Bucket": bucket, "Key": key}
            if head.get("VersionId") is not None:
                args["VersionId"] = head["VersionId"]
            client.delete_object(**args)
        raise S3ClientError(f"Unable to finish cleaning probe object: {key}.")

    def ensure_empty():
        result = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
        if result.get("Contents") or result.get("IsTruncated"):
            raise S3ClientError(
                "The selected object Prefix already contains data. Choose an empty Prefix."
            )

    try:
        # List before HEAD so missing List permission cannot be treated as empty.
        client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
        remove_probe()
        ensure_empty()
        args = dict(
            Bucket=bucket,
            Key=key,
            Body=PROBE_BODY,
            ContentLength=len(PROBE_BODY),
            Metadata=PROBE_METADATA,
        )
        strategy = _s3_atomic_create_strategy(
            platform=platform, endpoint=bucket_args.get("endpoint")
        )
        if strategy == _ATOMIC_CREATE_ALIYUN_FORBID_OVERWRITE:
            client.meta.events.register_first(
                "before-sign.s3.PutObject",
                _add_aliyun_forbid_overwrite_header,
                unique_id="hfl-preflight-no-overwrite",
            )
        elif strategy == _ATOMIC_CREATE_HUAWEI_FORBID_OVERWRITE:
            client.meta.events.register_first(
                "before-sign.s3.PutObject",
                _add_huawei_forbid_overwrite_header,
                unique_id="hfl-preflight-no-overwrite",
            )
        else:
            args["IfNoneMatch"] = "*"
        uploaded = client.put_object(**args)
        # Use the returned version even if a concurrent writer changes the key.
        delete_args = {"Bucket": bucket, "Key": key}
        if uploaded.get("VersionId") is not None:
            delete_args["VersionId"] = uploaded["VersionId"]
        client.delete_object(**delete_args)
        ensure_empty()
    except (ClientError, BotoCoreError) as exc:
        raise S3ClientError(
            f"Object Prefix verification failed (probe: {key}): {exc}"
        ) from exc


def prepare_s3_creation(create):
    @wraps(create)
    def wrapped(**kwargs):
        if kwargs.get("repo_type") != Repository.Type.S3:
            return create(**kwargs)
        from apps.storage.services.internal.repository_initializer import (
            resolve_s3_url_style,
            S3UrlStyleProbeError,
        )

        config = dict(kwargs.get("config") or {})
        config.pop("s3_bucket_prepared", None)
        secrets = build_secret_payload(
            repository_type=Repository.Type.S3,
            config=config,
            credential_payload=kwargs.get("credential_payload"),
        )
        candidate = Repository(
            organization_id=kwargs["organization_id"],
            repo_type=Repository.Type.S3,
            s3_bucket=kwargs.get("s3_bucket"),
            s3_platform=kwargs.get("s3_platform"),
            config=config,
        )
        spec = repository_location_spec(candidate, s3_namespace_resolved=True)
        lock_id = int(hashlib.sha256(spec.namespace_key.encode()).hexdigest()[:15], 16)
        try:
            with repository_execution_lock(
                operation="s3-preflight", operation_id=lock_id
            ) as acquired:
                if not acquired:
                    raise S3ClientError(
                        "Another repository creation is preparing this Bucket. Retry when it finishes."
                    )
                from apps.iam.models import Organization
                from apps.subscription.services.interface import (
                    enforce_repository_type_quota,
                )

                with transaction.atomic():
                    org = Organization.objects.filter(
                        pk=kwargs["organization_id"]
                    ).first()
                    if org is not None:
                        enforce_repository_type_quota(
                            organization=org, repo_type=Repository.Type.S3
                        )
                for existing in Repository.objects.filter(
                    repo_type=Repository.Type.S3, s3_bucket=candidate.s3_bucket
                ):
                    other = repository_location_spec(
                        existing, s3_namespace_resolved=True
                    )
                    if other.namespace_key == spec.namespace_key and _roots_overlap(
                        other.root_path, spec.root_path
                    ):
                        raise S3ClientError(
                            "This storage location overlaps an existing repository. Choose a different Prefix."
                        )
                bucket_args = dict(
                    endpoint=repository_control_endpoint(config),
                    region=str(config.get("region") or ""),
                    bucket=str(candidate.s3_bucket or ""),
                    access_key_id=str(
                        (kwargs.get("credential_payload") or {}).get(
                            "access_key_id", config.get("access_key_id", "")
                        )
                    ),
                    secret_access_key=str(secrets.get("secret_access_key") or ""),
                    s3_url_style=normalize_s3_url_style(
                        config.get("s3_url_style"), platform=candidate.s3_platform
                    ),
                    use_tls=config.get("use_tls") is not False,
                )
                bucket_created = False
                if kwargs.get("s3_bucket_mode") == Repository.S3BucketMode.NEW:
                    ensure_s3_bucket_absent(**bucket_args)
                    try:
                        bucket_created = create_s3_bucket(**bucket_args)
                    except S3ClientError as exc:
                        if "already exists" in str(exc).lower():
                            raise S3BucketAlreadyExists(
                                "Bucket already exists. Choose another name or select Existing Bucket."
                            ) from exc
                        raise
                    if not bucket_created:
                        raise S3BucketAlreadyExists(
                            "Bucket already exists. Choose another name or select Existing Bucket."
                        )
                try:
                    bucket_args["s3_url_style"] = resolve_s3_url_style(**bucket_args)
                    prefix = spec.root_path.strip("/")
                    prefix = f"{prefix}/" if prefix else ""
                    verify_empty_prefix(
                        prefix=prefix, platform=candidate.s3_platform, **bucket_args
                    )
                    config.update(
                        prefix=prefix,
                        s3_url_style=bucket_args["s3_url_style"],
                        s3_bucket_prepared=True,
                    )
                    kwargs["config"] = config
                    result = create(**kwargs)
                    return result
                except Exception as exc:
                    # A dispatch error can occur after the creation transaction
                    # commits. Never roll back storage already tracked by a row.
                    if (
                        bucket_created
                        and not Repository.objects.filter(
                            organization_id=kwargs["organization_id"],
                            repo_type=Repository.Type.S3,
                            s3_bucket=candidate.s3_bucket,
                            name=kwargs["name"],
                        ).exists()
                    ):
                        rollback = delete_s3_bucket_if_empty(**bucket_args)
                        if rollback.get("status") != "deleted":
                            raise S3ClientError(
                                f"{exc} The newly created Bucket could not be rolled back: "
                                f"{rollback.get('reason', 'unknown error')}. Check the Bucket before retrying."
                            ) from exc
                    raise
        except S3BucketAlreadyExists as exc:
            raise ValidationError({"s3_bucket": str(exc)}) from exc
        except (S3ClientError, S3UrlStyleProbeError) as exc:
            raise ValidationError(
                {
                    "detail": scrub_secrets(
                        str(exc),
                        extra_values=[
                            str(secrets.get("secret_access_key") or ""),
                            str(config.get("access_key_id") or ""),
                        ],
                    )
                }
            ) from exc

    return wrapped
