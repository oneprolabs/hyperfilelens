"""Read only the two bounded metadata objects; never connect or adopt a repo."""

import json

from apps.storage.services.internal.repository_initializer import (
    RepositoryInitializationError,
)
from apps.storage.services.internal.repository_ownership import (
    RepositoryOwnershipError,
    _marker_key,
    _prefix_with_slash,
    _require_matching_marker,
    _s3_args,
    ownership_payload,
)
from apps.storage.services.internal.s3_client import S3ClientError, read_s3_object

METADATA_MAX_BYTES = 1024 * 1024


def check_s3_repository_lightweight(repository):
    args = _s3_args(repository)
    try:
        raw = read_s3_object(
            **args,
            key=f"{_prefix_with_slash(repository)}kopia.repository",
            max_bytes=METADATA_MAX_BYTES,
        )
        if raw is None:
            raise RepositoryInitializationError("Repository format file is missing.")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise RepositoryInitializationError(
                "Repository format file is invalid."
            ) from exc
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("uniqueID"), str)
            or not value["uniqueID"]
            or not isinstance(value.get("keyAlgo"), str)
            or not value["keyAlgo"]
        ):
            raise RepositoryInitializationError("Repository format file is invalid.")
        marker = read_s3_object(
            **args,
            key=_marker_key(repository),
            max_bytes=METADATA_MAX_BYTES,
        )
        if marker is None:
            raise RepositoryOwnershipError("Repository ownership marker is missing.")
        _require_matching_marker(marker, expected=ownership_payload(repository))
    except S3ClientError as exc:
        raise RepositoryInitializationError(
            "Repository metadata could not be read."
        ) from exc
