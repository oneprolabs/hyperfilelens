from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse
from xml.etree import ElementTree

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError, ParamValidationError
from botocore.regions import EndpointResolverBuiltins

from apps.storage.services.internal.s3_url_style import boto3_s3_addressing_style


DEFAULT_S3_ENDPOINT = "https://s3.amazonaws.com"
BUCKET_REGION_LOOKUP_WORKERS = 10
BUCKET_REGION_LOOKUP_TIMEOUT_SECONDS = 5

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BucketSummary:
    name: str
    region_id: str | None = None


# Keep SDK/API capabilities in backend code. Provider Catalog remains focused
# on user-managed endpoint and Region data.
_PROVIDER_BUCKET_CAPABILITIES = {
    "aws": {"regional_list_buckets": True},
}
_BUCKET_REGION_FIELDS = (
    "BucketRegion",
    "LocationConstraint",
    "Location",
    "Region",
    "region",
    "location",
)
_REGIONAL_LIST_UNSUPPORTED_CODES = {
    "NotImplemented",
    "UnsupportedArgument",
    "UnsupportedOperation",
}


class S3ClientError(Exception):
    pass


def endpoint_for_requests(endpoint: str | None, *, use_tls: bool = True) -> str:
    raw = (endpoint or DEFAULT_S3_ENDPOINT).strip()
    if not raw:
        raw = DEFAULT_S3_ENDPOINT
    if "://" not in raw:
        raw = f"{'https' if use_tls else 'http'}://{raw}"
    parsed = urlparse(raw)
    scheme = parsed.scheme or ("https" if use_tls else "http")
    netloc = parsed.netloc or parsed.path
    path = parsed.path if parsed.netloc else ""
    return urlunparse((scheme, netloc.rstrip("/"), path.rstrip("/"), "", "", ""))


def endpoint_for_kopia(endpoint: str | None) -> str:
    raw = (endpoint or DEFAULT_S3_ENDPOINT).strip()
    if "://" in raw:
        parsed = urlparse(raw)
        return parsed.netloc or parsed.path.strip("/")
    return raw.strip("/")


def list_s3_buckets(
    *,
    endpoint: str | None,
    region: str | None,
    access_key_id: str,
    secret_access_key: str,
    s3_url_style: str | None = None,
    use_tls: bool = True,
    timeout_seconds: float = 15,
) -> list[str]:
    client = _client(
        endpoint=endpoint,
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        s3_url_style=s3_url_style,
        use_tls=use_tls,
        timeout_seconds=timeout_seconds,
    )
    try:
        response = client.list_buckets()
    except (BotoCoreError, ClientError, TypeError) as exc:
        raise S3ClientError(_error_message("Unable to list S3 buckets", exc)) from exc
    return [
        str(bucket.get("Name") or "")
        for bucket in response.get("Buckets", [])
        if bucket.get("Name")
    ]


def list_s3_buckets_by_region(
    *,
    platform: str,
    endpoint: str | None,
    region: str,
    access_key_id: str,
    secret_access_key: str,
    s3_url_style: str | None = None,
    use_tls: bool = True,
    timeout_seconds: float = BUCKET_REGION_LOOKUP_TIMEOUT_SECONDS,
) -> list[str]:
    """List buckets confirmed to belong to ``region``.

    Providers with an explicitly declared native Region filter use that first.
    Other providers are filtered from ordinary ListBuckets metadata, with
    GetBucketLocation used only for buckets whose ListBuckets item has no
    Region metadata.
    """
    normalized_platform = str(platform or "").strip().lower()
    normalized_region = str(region or "").strip()
    if not normalized_region:
        raise S3ClientError("Region is required to list S3 buckets by Region.")

    client = _client(
        endpoint=endpoint,
        region=normalized_region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        s3_url_style=s3_url_style,
        use_tls=use_tls,
        timeout_seconds=timeout_seconds,
    )
    _register_list_buckets_region_parser(client)
    if normalized_platform == "huaweicloud":
        _register_huawei_bucket_location_compatibility(client)
    capabilities = _PROVIDER_BUCKET_CAPABILITIES.get(normalized_platform, {})
    if capabilities.get("regional_list_buckets"):
        try:
            summaries = _list_bucket_summaries(
                client,
                request_args={"BucketRegion": normalized_region, "MaxBuckets": 1000},
            )
        except ParamValidationError as exc:
            if "BucketRegion" not in str(exc):
                raise S3ClientError(
                    _error_message("Unable to list S3 buckets by Region", exc)
                ) from exc
        except ClientError as exc:
            if _client_error_code(exc) not in _REGIONAL_LIST_UNSUPPORTED_CODES:
                raise S3ClientError(
                    _error_message("Unable to list S3 buckets by Region", exc)
                ) from exc
        except BotoCoreError as exc:
            raise S3ClientError(
                _error_message("Unable to list S3 buckets by Region", exc)
            ) from exc
        else:
            return _sorted_bucket_names(
                summary
                for summary in summaries
                if not summary.region_id
                or _bucket_region_matches(
                    platform=normalized_platform,
                    expected=normalized_region,
                    actual=summary.region_id,
                )
            )

    try:
        summaries = _list_bucket_summaries(client)
    except (BotoCoreError, ClientError) as exc:
        raise S3ClientError(_error_message("Unable to list S3 buckets", exc)) from exc

    matched = [
        summary
        for summary in summaries
        if summary.region_id
        and _bucket_region_matches(
            platform=normalized_platform,
            expected=normalized_region,
            actual=summary.region_id,
        )
    ]
    unresolved = [summary for summary in summaries if not summary.region_id]
    if unresolved:
        matched.extend(
            _resolve_matching_bucket_regions(
                client=client,
                platform=normalized_platform,
                expected_region=normalized_region,
                buckets=unresolved,
            )
        )
    return _sorted_bucket_names(matched)


def ensure_s3_bucket(
    *,
    endpoint: str | None,
    region: str | None,
    bucket: str,
    access_key_id: str,
    secret_access_key: str,
    s3_url_style: str | None = None,
    use_tls: bool = True,
    timeout_seconds: float = 15,
) -> None:
    client = _client(
        endpoint=endpoint,
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        s3_url_style=s3_url_style,
        use_tls=use_tls,
        timeout_seconds=timeout_seconds,
    )
    try:
        response = client.list_buckets()
    except (BotoCoreError, ClientError) as exc:
        raise S3ClientError(_error_message("Unable to list S3 buckets", exc)) from exc

    existing_names = {
        str(bucket_info.get("Name") or "")
        for bucket_info in response.get("Buckets", [])
        if bucket_info.get("Name")
    }
    if bucket in existing_names:
        return

    try:
        create_args = {"Bucket": bucket}
        create_bucket_configuration = _create_bucket_configuration(region)
        if create_bucket_configuration:
            create_args["CreateBucketConfiguration"] = create_bucket_configuration
        client.create_bucket(**create_args)
    except ClientError as exc:
        if _client_error_code(exc) in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            return
        raise S3ClientError(_error_message(f"Unable to create S3 bucket {bucket}", exc)) from exc
    except BotoCoreError as exc:
        raise S3ClientError(_error_message(f"Unable to create S3 bucket {bucket}", exc)) from exc


def create_s3_bucket(
    *,
    endpoint: str | None,
    region: str | None,
    bucket: str,
    access_key_id: str,
    secret_access_key: str,
    s3_url_style: str | None = None,
    use_tls: bool = True,
    timeout_seconds: float = 15,
) -> None:
    """Create a bucket and reject any existing-name collision."""
    if not str(bucket or "").strip():
        raise S3ClientError("Bucket name is required.")
    client = _client(
        endpoint=endpoint,
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        s3_url_style=s3_url_style,
        use_tls=use_tls,
        timeout_seconds=timeout_seconds,
    )
    try:
        create_args = {"Bucket": bucket}
        create_bucket_configuration = _create_bucket_configuration(region)
        if create_bucket_configuration:
            create_args["CreateBucketConfiguration"] = create_bucket_configuration
        client.create_bucket(**create_args)
    except ClientError as exc:
        if _client_error_code(exc) in {
            "BucketAlreadyOwnedByYou",
            "BucketAlreadyExists",
            "OperationAborted",
        }:
            raise S3ClientError(
                f"Unable to create S3 bucket {bucket}: bucket already exists."
            ) from exc
        raise S3ClientError(_error_message(f"Unable to create S3 bucket {bucket}", exc)) from exc
    except BotoCoreError as exc:
        raise S3ClientError(_error_message(f"Unable to create S3 bucket {bucket}", exc)) from exc


def check_s3_bucket_readable(
    *,
    endpoint: str | None,
    region: str | None,
    bucket: str,
    access_key_id: str,
    secret_access_key: str,
    s3_url_style: str | None = None,
    use_tls: bool = True,
    timeout_seconds: float = 15,
) -> None:
    """Check an existing S3 bucket without creating or modifying objects."""
    if not str(bucket or "").strip():
        raise S3ClientError("Bucket name is required.")
    client = _client(
        endpoint=endpoint,
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        s3_url_style=s3_url_style,
        use_tls=use_tls,
        timeout_seconds=timeout_seconds,
    )
    try:
        client.head_bucket(Bucket=bucket)
    except (BotoCoreError, ClientError) as exc:
        raise S3ClientError(
            _error_message(f"Unable to access bucket {bucket}", exc)
        ) from exc


def verify_s3_bucket_rw(
    *,
    endpoint: str | None,
    region: str | None,
    bucket: str,
    access_key_id: str,
    secret_access_key: str,
    s3_url_style: str | None = None,
    use_tls: bool = True,
    timeout_seconds: float = 15,
) -> dict:
    """Verify that the given bucket is readable and writable by performing
    head_bucket + put_object + delete_object using a probe key under
    ``.hfl-verify/<uuid>.tmp``.

    Returns a dict with ``bucket``, ``probe_key``, ``wrote`` and ``deleted`` on
    success. Raises :class:`S3ClientError` on any failure.
    """
    if not str(bucket or "").strip():
        raise S3ClientError("Bucket name is required for verification.")
    client = _client(
        endpoint=endpoint,
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        s3_url_style=s3_url_style,
        use_tls=use_tls,
        timeout_seconds=timeout_seconds,
    )
    probe_key = f".hfl-verify/{uuid.uuid4().hex}.tmp"
    body = b"hyperfilelens-verify"
    try:
        try:
            client.head_bucket(Bucket=bucket)
        except (BotoCoreError, ClientError) as exc:
            raise S3ClientError(_error_message(f"Unable to access bucket {bucket}", exc)) from exc
        try:
            client.put_object(Bucket=bucket, Key=probe_key, Body=body, ContentLength=len(body))
        except (BotoCoreError, ClientError) as exc:
            raise S3ClientError(_error_message(f"Unable to write to bucket {bucket}", exc)) from exc
        try:
            client.delete_object(Bucket=bucket, Key=probe_key)
        except (BotoCoreError, ClientError) as exc:
            raise S3ClientError(_error_message(f"Unable to clean up probe object in {bucket}", exc)) from exc
    except S3ClientError:
        # best-effort cleanup; ignore failures
        try:
            client.delete_object(Bucket=bucket, Key=probe_key)
        except Exception:
            pass
        raise
    return {"bucket": bucket, "probe_key": probe_key, "wrote": True, "deleted": True}


def delete_s3_prefix(
    *,
    endpoint: str | None,
    region: str | None,
    bucket: str,
    prefix: str,
    access_key_id: str,
    secret_access_key: str,
    s3_url_style: str | None = None,
    use_tls: bool = True,
    timeout_seconds: float = 30,
) -> dict[str, int | str]:
    """Delete one managed repository prefix without deleting its bucket."""

    normalized_prefix = str(prefix or "").strip().replace("\\", "/").strip("/")
    if not normalized_prefix:
        raise S3ClientError("Repository object prefix is required for cleanup.")
    normalized_prefix += "/"
    client = _client(
        endpoint=endpoint,
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        s3_url_style=s3_url_style,
        use_tls=use_tls,
        timeout_seconds=timeout_seconds,
    )
    deleted_objects = 0
    deleted_versions = 0
    deleted_markers = 0
    aborted_uploads = 0
    try:
        upload_paginator = client.get_paginator("list_multipart_uploads")
        for page in upload_paginator.paginate(Bucket=bucket, Prefix=normalized_prefix):
            for upload in page.get("Uploads", []):
                key = str(upload.get("Key") or "")
                upload_id = str(upload.get("UploadId") or "")
                if not key or not upload_id:
                    continue
                client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
                aborted_uploads += 1

        try:
            version_paginator = client.get_paginator("list_object_versions")
            for page in version_paginator.paginate(Bucket=bucket, Prefix=normalized_prefix):
                version_entries = [
                    {"Key": item["Key"], "VersionId": item["VersionId"]}
                    for item in page.get("Versions", [])
                    if item.get("Key") and item.get("VersionId")
                ]
                marker_entries = [
                    {"Key": item["Key"], "VersionId": item["VersionId"]}
                    for item in page.get("DeleteMarkers", [])
                    if item.get("Key") and item.get("VersionId")
                ]
                for batch in _chunks(version_entries + marker_entries, 1000):
                    _delete_s3_entries(client=client, bucket=bucket, entries=batch)
                deleted_versions += len(version_entries)
                deleted_markers += len(marker_entries)
        except ClientError as exc:
            if not _is_unsupported_s3_header_error(exc):
                raise

        object_paginator = client.get_paginator("list_objects_v2")
        for page in object_paginator.paginate(Bucket=bucket, Prefix=normalized_prefix):
            entries = [
                {"Key": item["Key"]}
                for item in page.get("Contents", [])
                if item.get("Key")
            ]
            for batch in _chunks(entries, 1000):
                _delete_s3_entries(client=client, bucket=bucket, entries=batch)
            deleted_objects += len(entries)

        _verify_s3_prefix_empty(client=client, bucket=bucket, prefix=normalized_prefix)
    except (BotoCoreError, ClientError) as exc:
        raise S3ClientError(_error_message(f"Unable to delete repository prefix {normalized_prefix}", exc)) from exc
    return {
        "bucket": bucket,
        "prefix": normalized_prefix,
        "deleted_objects": deleted_objects,
        "deleted_versions": deleted_versions,
        "deleted_markers": deleted_markers,
        "aborted_uploads": aborted_uploads,
    }


def delete_s3_bucket_if_empty(
    *,
    endpoint: str | None,
    region: str | None,
    bucket: str,
    access_key_id: str,
    secret_access_key: str,
    s3_url_style: str | None = None,
    use_tls: bool = True,
    timeout_seconds: float = 30,
) -> dict[str, str]:
    """Best-effort deletion of an empty repository-owned bucket.

    This helper never raises: prefix cleanup remains the mandatory operation,
    while bucket cleanup is recorded as a secondary outcome on the task.
    """
    try:
        client = _client(
            endpoint=endpoint,
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            s3_url_style=s3_url_style,
            use_tls=use_tls,
            timeout_seconds=timeout_seconds,
        )
        nonempty_reason = _bucket_nonempty_reason(client=client, bucket=bucket)
        if nonempty_reason:
            return {
                "bucket": bucket,
                "status": "skipped_not_empty",
                "reason": nonempty_reason,
            }
        client.delete_bucket(Bucket=bucket)
        return {"bucket": bucket, "status": "deleted", "reason": "bucket_empty"}
    except ClientError as exc:
        if _client_error_code(exc) in {"BucketNotEmpty", "BucketNotEmptyException"}:
            return {
                "bucket": bucket,
                "status": "skipped_not_empty",
                "reason": "bucket_became_non_empty",
            }
        return {
            "bucket": bucket,
            "status": "failed",
            "reason": _error_message(f"Unable to delete S3 bucket {bucket}", exc),
        }
    except BotoCoreError as exc:
        return {
            "bucket": bucket,
            "status": "failed",
            "reason": _error_message(f"Unable to delete S3 bucket {bucket}", exc),
        }


def _bucket_nonempty_reason(*, client, bucket: str) -> str:
    objects = client.list_objects_v2(Bucket=bucket, MaxKeys=1)
    if objects.get("Contents"):
        return "objects_present"
    try:
        versions = client.list_object_versions(Bucket=bucket, MaxKeys=1)
        if versions.get("Versions"):
            return "object_versions_present"
        if versions.get("DeleteMarkers"):
            return "delete_markers_present"
    except ClientError as exc:
        if not _is_unsupported_s3_header_error(exc):
            raise
    uploads = client.list_multipart_uploads(Bucket=bucket, MaxUploads=1)
    if uploads.get("Uploads"):
        return "multipart_uploads_present"
    return ""


def _chunks(items: list[dict], size: int):
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


def _delete_s3_entries(*, client, bucket: str, entries: list[dict]) -> None:
    try:
        response = client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": entries, "Quiet": True},
        )
    except ClientError as exc:
        if not _should_fallback_from_batch_delete(exc):
            raise
        for entry in entries:
            client.delete_object(Bucket=bucket, **entry)
        return
    errors = response.get("Errors") if isinstance(response, dict) else None
    if errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        code = str(first.get("Code") or "DeleteFailed")
        message = str(first.get("Message") or "S3 rejected one or more object deletions.")
        raise S3ClientError(f"Unable to delete repository objects: {code}: {message}")


def _is_unsupported_s3_header_error(exc: ClientError) -> bool:
    if _client_error_code(exc) != "NotImplemented":
        return False
    message = str(exc.response.get("Error", {}).get("Message") or "").lower()
    return "header you provided implies functionality" in message


def _should_fallback_from_batch_delete(exc: ClientError) -> bool:
    return _is_unsupported_s3_header_error(exc) or _client_error_code(exc) == "MissingContentMD5"


def _verify_s3_prefix_empty(*, client, bucket: str, prefix: str) -> None:
    try:
        versions = client.list_object_versions(Bucket=bucket, Prefix=prefix, MaxKeys=1)
        if versions.get("Versions") or versions.get("DeleteMarkers"):
            raise S3ClientError("Repository object prefix still contains object versions after cleanup.")
    except ClientError as exc:
        if not _is_unsupported_s3_header_error(exc):
            raise
    objects = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
    if objects.get("Contents"):
        raise S3ClientError("Repository object prefix still contains objects after cleanup.")
    uploads = client.list_multipart_uploads(Bucket=bucket, Prefix=prefix, MaxUploads=1)
    if uploads.get("Uploads"):
        raise S3ClientError("Repository object prefix still contains multipart uploads after cleanup.")


def _register_list_buckets_region_parser(client) -> None:
    client.meta.events.register(
        "after-call.s3.ListBuckets",
        _merge_list_buckets_regions,
        unique_id="hfl-list-buckets-regions",
    )


def _register_huawei_bucket_location_compatibility(client) -> None:
    # Botocore forces path-style specifically for GetBucketLocation because
    # that is safer for AWS when the Bucket Region is unknown. Huawei OBS
    # requires virtual-hosted addressing for this operation instead.
    client.meta.events.register_last(
        "before-endpoint-resolution.s3",
        _force_virtual_hosted_bucket_location,
        unique_id="hfl-huawei-bucket-location-addressing",
    )
    # Botocore's built-in handler only reads the XML root text. Huawei may
    # wrap the value in a LocationConstraint child, so restore it from the
    # raw response after Botocore has run its parser.
    client.meta.events.register_last(
        "after-call.s3.GetBucketLocation",
        _merge_huawei_bucket_location,
        unique_id="hfl-huawei-bucket-location-response",
    )


def _force_virtual_hosted_bucket_location(builtins, model, **_kwargs) -> None:
    if model.name == "GetBucketLocation":
        builtins[EndpointResolverBuiltins.AWS_S3_FORCE_PATH_STYLE] = False


def _merge_huawei_bucket_location(parsed, http_response, **_kwargs) -> None:
    if not isinstance(parsed, dict):
        return
    if int(getattr(http_response, "status_code", 0) or 0) >= 300:
        return
    response_body = getattr(http_response, "content", b"")
    if not response_body:
        return
    try:
        region = _bucket_location_from_xml(response_body)
    except (ElementTree.ParseError, TypeError, ValueError):
        logger.warning("Unable to parse Huawei Bucket Location response XML")
        return
    if region:
        parsed["LocationConstraint"] = region


def _bucket_location_from_xml(response_body: bytes | str) -> str | None:
    root = ElementTree.fromstring(response_body)
    for element in root.iter():
        if _xml_local_name(element.tag) not in {
            "LocationConstraint",
            "Location",
            "Region",
        }:
            continue
        region = str(element.text or "").strip()
        if region:
            return region
    return str(root.text or "").strip() or None


def _merge_list_buckets_regions(parsed, http_response, **_kwargs) -> None:
    """Restore vendor ListBuckets Region fields discarded by Botocore."""
    if not isinstance(parsed, dict):
        return
    if int(getattr(http_response, "status_code", 0) or 0) >= 300:
        return
    response_body = getattr(http_response, "content", b"")
    if not response_body:
        return
    try:
        raw_regions = _list_buckets_regions_from_xml(response_body)
    except (ElementTree.ParseError, TypeError, ValueError):
        logger.warning("Unable to parse vendor Region fields from ListBuckets XML")
        return
    for item in parsed.get("Buckets", []):
        if not isinstance(item, dict) or _bucket_region_value(item):
            continue
        name = str(item.get("Name") or "").strip()
        field_and_region = raw_regions.get(name)
        if field_and_region:
            field, region = field_and_region
            item[field] = region


def _list_buckets_regions_from_xml(
    response_body: bytes | str,
) -> dict[str, tuple[str, str]]:
    root = ElementTree.fromstring(response_body)
    regions: dict[str, tuple[str, str]] = {}
    for bucket_element in root.iter():
        if _xml_local_name(bucket_element.tag) != "Bucket":
            continue
        values: dict[str, str] = {}
        for child in bucket_element:
            field = _xml_local_name(child.tag)
            if field not in {"Name", "BucketRegion", "Location", "Region"}:
                continue
            value = str(child.text or "").strip()
            if value:
                values[field] = value
        name = values.get("Name", "")
        if not name:
            continue
        for field in ("BucketRegion", "Location", "Region"):
            region = values.get(field)
            if region:
                regions[name] = (field, region)
                break
    return regions


def _xml_local_name(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _list_bucket_summaries(
    client,
    *,
    request_args: dict | None = None,
) -> list[BucketSummary]:
    args = dict(request_args or {})
    summaries: list[BucketSummary] = []
    seen_tokens: set[str] = set()
    while True:
        response = client.list_buckets(**args)
        for item in response.get("Buckets", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("Name") or "").strip()
            if not name:
                continue
            summaries.append(
                BucketSummary(name=name, region_id=_bucket_region_value(item))
            )
        continuation_token = str(
            response.get("ContinuationToken")
            or response.get("NextContinuationToken")
            or ""
        ).strip()
        if not continuation_token or continuation_token in seen_tokens:
            return summaries
        seen_tokens.add(continuation_token)
        args["ContinuationToken"] = continuation_token


def _resolve_matching_bucket_regions(
    *,
    client,
    platform: str,
    expected_region: str,
    buckets: list[BucketSummary],
) -> list[BucketSummary]:
    matched: list[BucketSummary] = []
    worker_count = min(BUCKET_REGION_LOOKUP_WORKERS, len(buckets))
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="s3-bucket-region",
    ) as executor:
        pending = {
            executor.submit(
                _get_bucket_region,
                client=client,
                platform=platform,
                bucket=summary.name,
            ): summary
            for summary in buckets
        }
        for future in as_completed(pending):
            summary = pending[future]
            try:
                actual_region = future.result()
            except (BotoCoreError, ClientError) as exc:
                error_code, http_status, request_id = _bucket_region_error_details(exc)
                logger.warning(
                    "Unable to resolve S3 bucket Region; bucket excluded "
                    "platform=%s bucket=%s error_code=%s http_status=%s request_id=%s",
                    platform,
                    summary.name,
                    error_code,
                    http_status,
                    request_id,
                )
                continue
            if not actual_region:
                logger.warning(
                    "S3 bucket Location response has no Region; bucket excluded "
                    "platform=%s bucket=%s",
                    platform,
                    summary.name,
                )
                continue
            if _bucket_region_matches(
                platform=platform,
                expected=expected_region,
                actual=actual_region,
            ):
                matched.append(
                    BucketSummary(name=summary.name, region_id=actual_region)
                )
    return matched


def _get_bucket_region(*, client, platform: str, bucket: str) -> str | None:
    try:
        response = client.get_bucket_location(Bucket=bucket)
    except ClientError as exc:
        response_region = _bucket_region_from_error(exc)
        if response_region:
            return response_region
        raise
    return _bucket_region_value(response, platform=platform)


def _bucket_region_value(
    payload: dict,
    *,
    platform: str = "",
) -> str | None:
    for field in _BUCKET_REGION_FIELDS:
        if field not in payload:
            continue
        value = str(payload.get(field) or "").strip()
        if value:
            return value
        if platform == "aws" and field == "LocationConstraint":
            return "us-east-1"
    return None


def _bucket_region_from_error(exc: ClientError) -> str | None:
    response = exc.response if isinstance(exc.response, dict) else {}
    error = response.get("Error") if isinstance(response.get("Error"), dict) else {}
    region = _bucket_region_value(error)
    if region:
        return region
    for field in ("Endpoint", "endpoint"):
        endpoint = str(error.get(field) or "").strip()
        if endpoint:
            return endpoint
    metadata = (
        response.get("ResponseMetadata")
        if isinstance(response.get("ResponseMetadata"), dict)
        else {}
    )
    headers = (
        metadata.get("HTTPHeaders")
        if isinstance(metadata.get("HTTPHeaders"), dict)
        else {}
    )
    normalized_headers = {str(key).lower(): value for key, value in headers.items()}
    for key in (
        "x-amz-bucket-region",
        "x-oss-bucket-region",
        "x-obs-bucket-region",
        "x-obs-bucket-location",
    ):
        value = str(normalized_headers.get(key) or "").strip()
        if value:
            return value
    return None


def _bucket_region_matches(*, platform: str, expected: str, actual: str) -> bool:
    return _canonical_bucket_region(platform, expected) == _canonical_bucket_region(
        platform, actual
    )


def _canonical_bucket_region(platform: str, value: object) -> str:
    normalized_platform = str(platform or "").strip().lower()
    raw = str(value or "").strip().lower().rstrip(".")
    if normalized_platform == "aws":
        if not raw or raw == "us":
            return "us-east-1"
        if raw == "eu":
            return "eu-west-1"
        return raw
    if normalized_platform == "aliyun":
        host = urlparse(raw if "://" in raw else f"https://{raw}").hostname or raw
        labels = host.split(".")
        if "aliyuncs" in labels and labels.index("aliyuncs") > 0:
            region_label = labels[labels.index("aliyuncs") - 1]
        else:
            region_label = next(
                (label for label in labels if label.startswith("oss-")),
                labels[0],
            )
        if region_label.endswith("-internal"):
            region_label = region_label.removesuffix("-internal")
        return (
            region_label
            if region_label.startswith("oss-")
            else f"oss-{region_label}"
        )
    if normalized_platform == "huaweicloud":
        host = urlparse(raw if "://" in raw else f"https://{raw}").hostname or raw
        parts = host.split(".")
        if "obs" in parts:
            obs_index = len(parts) - 1 - parts[::-1].index("obs")
            if len(parts) > obs_index + 1:
                return parts[obs_index + 1]
    return raw


def _sorted_bucket_names(summaries) -> list[str]:
    return sorted({summary.name for summary in summaries if summary.name})


def _bucket_region_error_details(exc: Exception) -> tuple[str, str, str]:
    if isinstance(exc, ClientError):
        response = exc.response if isinstance(exc.response, dict) else {}
        metadata = (
            response.get("ResponseMetadata")
            if isinstance(response.get("ResponseMetadata"), dict)
            else {}
        )
        headers = (
            metadata.get("HTTPHeaders")
            if isinstance(metadata.get("HTTPHeaders"), dict)
            else {}
        )
        normalized_headers = {
            str(key).lower(): value for key, value in headers.items()
        }
        request_id = str(
            metadata.get("RequestId")
            or normalized_headers.get("x-amz-request-id")
            or normalized_headers.get("x-oss-request-id")
            or normalized_headers.get("x-obs-request-id")
            or "-"
        )
        return (
            _client_error_code(exc) or "ClientError",
            str(metadata.get("HTTPStatusCode") or "-"),
            request_id,
        )
    return type(exc).__name__, "-", "-"


def _client(
    *,
    endpoint: str | None,
    region: str | None,
    access_key_id: str,
    secret_access_key: str,
    s3_url_style: str | None,
    use_tls: bool,
    timeout_seconds: float,
):
    endpoint_url = endpoint_for_requests(endpoint, use_tls=use_tls)
    normalized_region = (region or "").strip() or "us-east-1"
    try:
        address_style = boto3_s3_addressing_style(s3_url_style)
    except ValueError as exc:
        raise S3ClientError(str(exc)) from exc
    config = Config(
        signature_version="s3v4",
        connect_timeout=timeout_seconds,
        read_timeout=timeout_seconds,
        max_pool_connections=BUCKET_REGION_LOOKUP_WORKERS,
        retries={"max_attempts": 1},
        s3={"addressing_style": address_style},
        # Some S3-compatible endpoints reject botocore's optional checksum
        # headers on multi-object deletion. Keep checksums for operations whose
        # protocol requires them, without sending optional SDK checksum headers.
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=normalized_region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        use_ssl=endpoint_url.startswith("https://"),
        verify=endpoint_url.startswith("https://"),
        config=config,
    )


def _create_bucket_configuration(region: str | None) -> dict[str, str] | None:
    normalized = (region or "").strip()
    if not normalized or normalized == "us-east-1":
        return None
    return {"LocationConstraint": normalized}


def _client_error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code") or "")


def _error_message(prefix: str, exc: Exception) -> str:
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = str(error.get("Code") or "")
        message = str(error.get("Message") or "")
        suffix = ": ".join(part for part in [code, message] if part)
        if suffix:
            return f"{prefix}: {suffix}"
    return f"{prefix}: {exc}"
