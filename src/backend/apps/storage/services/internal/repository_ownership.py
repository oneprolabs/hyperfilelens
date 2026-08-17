"""Storage-side ownership protocol for managed repositories."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from django.core.exceptions import ValidationError

from apps.storage.repositories.models import (
    Repository,
    RepositoryDeploymentIdentity,
)
from apps.storage.services.internal.repository_endpoints import (
    repository_control_endpoint,
)
from apps.storage.services.internal.repository_location import (
    mark_repository_location_ownership_verified,
    resolve_s3_repository_namespace,
)
from apps.storage.services.internal.repository_secrets import (
    resolve_repository_secrets,
)
from apps.storage.services.internal.s3_client import (
    identify_s3_namespace,
    list_s3_object_keys,
    put_s3_object_if_absent,
    read_s3_object,
    s3_prefix_has_any_state,
)
from apps.storage.services.internal.s3_url_style import normalize_s3_url_style


OWNERSHIP_FORMAT_VERSION = 1
OWNERSHIP_MARKER_PATH = ".hyperfilelens/repository-owner-v1.json"


class RepositoryOwnershipError(ValidationError):
    """Raised when physical repository ownership is absent or inconsistent."""


class RepositoryOwnershipMarkerMissingError(RepositoryOwnershipError):
    """Raised when destructive ownership proof has no physical marker."""


@dataclass(frozen=True)
class OwnershipMarker:
    deployment_uuid: str
    repository_uuid: str
    location_digest: str
    format_version: int
    signature: str


def repository_deployment_uuid() -> str:
    identity = _deployment_identity()
    return str(identity.deployment_uuid)


def repository_location_digest(
    repository: Repository,
    *,
    repository_subdir: str | None = None,
) -> str:
    config = repository.config if isinstance(repository.config, dict) else {}
    if repository.repo_type == Repository.Type.S3:
        prefix = _normalize_prefix(config.get("prefix"))
        identity = {
            "type": Repository.Type.S3,
            "bucket": str(repository.s3_bucket or "").strip().lower(),
            "root": prefix or "/",
        }
    elif repository.repo_type == Repository.Type.NAS:
        identity = {
            "type": Repository.Type.NAS,
            "protocol": str(repository.nas_protocol or "").strip().lower(),
            "server": str(config.get("server_address") or "")
            .strip()
            .lower()
            .rstrip("."),
            "share": str(config.get("share_path") or "").strip(),
            "root": _normalize_filesystem_root(repository_subdir),
        }
    else:
        identity = {
            "type": repository.repo_type,
            "root": str(config.get("proxy_node_dir") or "").strip(),
        }
    encoded = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ownership_payload(
    repository: Repository,
    *,
    repository_subdir: str | None = None,
) -> dict[str, object]:
    unsigned = {
        "deployment_uuid": repository_deployment_uuid(),
        "repository_uuid": str(repository.repository_uuid),
        "location_digest": repository_location_digest(
            repository,
            repository_subdir=repository_subdir,
        ),
        "format_version": OWNERSHIP_FORMAT_VERSION,
    }
    return {**unsigned, "signature": _sign_marker(unsigned)}


def claim_s3_repository_ownership(repository: Repository) -> None:
    """Resolve the S3 namespace and atomically claim an empty Prefix."""
    s3_args = _s3_args(repository)
    _ensure_s3_namespace_resolved(repository, s3_args=s3_args)
    expected = ownership_payload(repository)
    marker_key = _marker_key(repository)
    _reject_foreign_ancestor_markers(
        repository=repository,
        expected=expected,
        s3_args=s3_args,
    )
    existing = read_s3_object(**s3_args, key=marker_key)
    if existing is not None:
        _require_matching_marker(existing, expected=expected)
        _reject_foreign_descendant_markers(
            repository=repository,
            expected=expected,
            s3_args=s3_args,
        )
        mark_repository_location_ownership_verified(repository)
        return

    prefix = _prefix_with_slash(repository)
    if s3_prefix_has_any_state(**s3_args, prefix=prefix):
        raise RepositoryOwnershipError(
            "The selected object Prefix contains objects, historical versions, "
            "or incomplete uploads and cannot be claimed as a new repository."
        )
    marker_bytes = _encode_marker(expected)
    if not put_s3_object_if_absent(
        **s3_args,
        platform=repository.s3_platform,
        key=marker_key,
        body=marker_bytes,
    ):
        existing = read_s3_object(**s3_args, key=marker_key)
        if existing is None:
            raise RepositoryOwnershipError(
                "Repository ownership changed while the Prefix was being claimed."
            )
        _require_matching_marker(existing, expected=expected)
    persisted = read_s3_object(**s3_args, key=marker_key)
    if persisted is None:
        raise RepositoryOwnershipError(
            "Repository ownership marker was not durable after creation."
        )
    _require_matching_marker(persisted, expected=expected)
    # A database Claim serializes locations that the control plane can identify,
    # but different private-network aliases may reach the same storage. Repeat
    # the physical hierarchy proof after the atomic marker write so neither an
    # ancestor nor a descendant can slip in between the initial probe and init.
    _reject_foreign_ancestor_markers(
        repository=repository,
        expected=expected,
        s3_args=s3_args,
    )
    _reject_foreign_descendant_markers(
        repository=repository,
        expected=expected,
        s3_args=s3_args,
    )
    mark_repository_location_ownership_verified(repository)


def verify_s3_repository_ownership(
    repository: Repository,
    *,
    adopt_legacy: bool = False,
    refresh_namespace: bool = False,
) -> None:
    """Verify the exact Prefix owner, optionally adopting a proven legacy repo."""
    s3_args = _s3_args(repository)
    _ensure_s3_namespace_resolved(
        repository,
        s3_args=s3_args,
        force=refresh_namespace,
    )
    expected = ownership_payload(repository)
    marker_key = _marker_key(repository)
    _reject_foreign_ancestor_markers(
        repository=repository,
        expected=expected,
        s3_args=s3_args,
    )
    marker = read_s3_object(**s3_args, key=marker_key)
    if marker is None:
        if not adopt_legacy:
            raise RepositoryOwnershipError(
                "Repository ownership marker is missing. Physical data was retained."
            )
        if repository.location_claims.filter(
            ownership_verified_at__isnull=False,
        ).exists():
            raise RepositoryOwnershipError(
                "Repository ownership marker disappeared after it was established. "
                "Physical data was retained."
            )
        _reject_foreign_descendant_markers(
            repository=repository,
            expected=expected,
            s3_args=s3_args,
        )
        marker_created = put_s3_object_if_absent(
            **s3_args,
            platform=repository.s3_platform,
            key=marker_key,
            body=_encode_marker(expected),
        )
        marker = read_s3_object(**s3_args, key=marker_key)
        if marker is None:
            message = (
                "Repository ownership marker was not durable after legacy adoption."
                if marker_created
                else "Repository ownership changed during legacy adoption."
            )
            raise RepositoryOwnershipError(message)
        _require_matching_marker(marker, expected=expected)
    else:
        _require_matching_marker(marker, expected=expected)
    if adopt_legacy:
        _reject_foreign_ancestor_markers(
            repository=repository,
            expected=expected,
            s3_args=s3_args,
        )
        _reject_foreign_descendant_markers(
            repository=repository,
            expected=expected,
            s3_args=s3_args,
        )
    mark_repository_location_ownership_verified(repository)


def verify_s3_repository_deletion_ownership(repository: Repository) -> None:
    """Perform the full ownership proof required before destructive cleanup."""
    s3_args = _s3_args(repository)
    _ensure_s3_namespace_resolved(repository, s3_args=s3_args)
    expected = ownership_payload(repository)
    _reject_foreign_ancestor_markers(
        repository=repository,
        expected=expected,
        s3_args=s3_args,
    )
    marker = read_s3_object(**s3_args, key=_marker_key(repository))
    if marker is None:
        raise RepositoryOwnershipMarkerMissingError(
            "Repository ownership marker is missing. Physical data was retained."
        )
    _require_matching_marker(marker, expected=expected)
    _reject_foreign_descendant_markers(
        repository=repository,
        expected=expected,
        s3_args=s3_args,
    )
    mark_repository_location_ownership_verified(repository)


def ownership_payload_for_node(
    repository: Repository,
    *,
    repository_subdir: str | None = None,
) -> dict[str, object]:
    """Return the non-secret marker contract consumed by Agent filesystems."""
    return {
        **ownership_payload(repository, repository_subdir=repository_subdir),
        "marker_path": OWNERSHIP_MARKER_PATH,
    }


def s3_repository_ownership_marker(
    repository: Repository,
) -> tuple[str, dict[str, object]]:
    """Return the exact S3 marker key and trusted payload for cleanup."""
    return _marker_key(repository), ownership_payload(repository)


def _ensure_s3_namespace_resolved(
    repository: Repository,
    *,
    s3_args: dict[str, object],
    force: bool = False,
) -> None:
    already_resolved = repository.location_claims.filter(
        state__in=(
            "reserved",
            "initializing",
            "owned",
            "residual",
        ),
        namespace_resolved_at__isnull=False,
    ).exists()
    if already_resolved and not force:
        return
    owner_id = identify_s3_namespace(
        **{key: value for key, value in s3_args.items() if key != "bucket"}
    )
    resolve_s3_repository_namespace(repository, owner_id=owner_id)


def _reject_foreign_ancestor_markers(
    *,
    repository: Repository,
    expected: dict[str, object],
    s3_args: dict[str, object],
) -> None:
    for key in _ancestor_marker_keys(repository):
        marker = read_s3_object(**s3_args, key=key)
        if marker is None:
            continue
        try:
            _require_matching_marker(marker, expected=expected)
        except RepositoryOwnershipError as exc:
            raise RepositoryOwnershipError(
                "The selected Prefix is nested inside another managed repository."
            ) from exc


def _reject_foreign_descendant_markers(
    *,
    repository: Repository,
    expected: dict[str, object],
    s3_args: dict[str, object],
) -> None:
    own_key = _marker_key(repository)
    suffix = "/" + OWNERSHIP_MARKER_PATH
    for key in list_s3_object_keys(
        **s3_args,
        prefix=_prefix_with_slash(repository),
    ):
        if key == own_key or not key.endswith(suffix):
            continue
        marker = read_s3_object(**s3_args, key=key)
        if marker is None:
            continue
        try:
            _require_matching_marker(marker, expected=expected)
        except RepositoryOwnershipError as exc:
            raise RepositoryOwnershipError(
                "The selected Prefix contains another managed repository."
            ) from exc
        raise RepositoryOwnershipError(
            "The selected Prefix contains a nested repository location."
        )


def _s3_args(repository: Repository) -> dict[str, object]:
    config = repository.config if isinstance(repository.config, dict) else {}
    secrets_payload = resolve_repository_secrets(repository)
    return {
        "endpoint": repository_control_endpoint(config),
        "region": str(config.get("region") or ""),
        "bucket": str(repository.s3_bucket or ""),
        "access_key_id": str(config.get("access_key_id") or ""),
        "secret_access_key": str(secrets_payload.get("secret_access_key") or ""),
        "s3_url_style": normalize_s3_url_style(
            config.get("s3_url_style"), platform=repository.s3_platform
        ),
        "use_tls": config.get("use_tls") is not False,
    }


def _marker_key(repository: Repository) -> str:
    prefix = _normalize_prefix((repository.config or {}).get("prefix"))
    return f"{prefix}/{OWNERSHIP_MARKER_PATH}" if prefix else OWNERSHIP_MARKER_PATH


def _prefix_with_slash(repository: Repository) -> str:
    prefix = _normalize_prefix((repository.config or {}).get("prefix"))
    return f"{prefix}/" if prefix else ""


def _ancestor_marker_keys(repository: Repository) -> list[str]:
    prefix = _normalize_prefix((repository.config or {}).get("prefix"))
    if not prefix:
        return []
    parts = prefix.split("/")
    return [OWNERSHIP_MARKER_PATH] + [
        "/".join([*parts[:index], OWNERSHIP_MARKER_PATH])
        for index in range(1, len(parts))
    ]


def _normalize_prefix(value: object) -> str:
    return "/".join(
        part for part in str(value or "").strip().replace("\\", "/").split("/") if part
    )


def _normalize_filesystem_root(value: object) -> str:
    return "/".join(
        part for part in str(value or "").strip().replace("\\", "/").split("/") if part
    )


def _encode_marker(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _require_matching_marker(
    raw: bytes,
    *,
    expected: dict[str, object],
) -> OwnershipMarker:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RepositoryOwnershipError(
            "Repository ownership marker is invalid. Physical data was retained."
        ) from exc
    if not isinstance(payload, dict):
        raise RepositoryOwnershipError("Repository ownership marker is invalid.")
    try:
        marker = OwnershipMarker(
            deployment_uuid=str(payload.get("deployment_uuid") or ""),
            repository_uuid=str(payload.get("repository_uuid") or ""),
            location_digest=str(payload.get("location_digest") or ""),
            format_version=int(payload.get("format_version") or 0),
            signature=str(payload.get("signature") or ""),
        )
    except (TypeError, ValueError) as exc:
        raise RepositoryOwnershipError(
            "Repository ownership marker is invalid. Physical data was retained."
        ) from exc
    unsigned = {
        "deployment_uuid": marker.deployment_uuid,
        "repository_uuid": marker.repository_uuid,
        "location_digest": marker.location_digest,
        "format_version": marker.format_version,
    }
    if not hmac.compare_digest(marker.signature, _sign_marker(unsigned)):
        raise RepositoryOwnershipError(
            "Repository ownership marker signature is invalid."
        )
    for key in (
        "deployment_uuid",
        "repository_uuid",
        "location_digest",
        "format_version",
    ):
        if unsigned[key] != expected[key]:
            raise RepositoryOwnershipError(
                "Repository ownership belongs to another repository."
            )
    return marker


def _sign_marker(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    secret = _deployment_identity().ownership_signing_key.encode("utf-8")
    return hmac.new(secret, encoded, hashlib.sha256).hexdigest()


def _deployment_identity() -> RepositoryDeploymentIdentity:
    identity, _created = RepositoryDeploymentIdentity.objects.get_or_create(pk=1)
    return identity


__all__ = [
    "OWNERSHIP_MARKER_PATH",
    "RepositoryOwnershipError",
    "RepositoryOwnershipMarkerMissingError",
    "claim_s3_repository_ownership",
    "ownership_payload_for_node",
    "repository_deployment_uuid",
    "repository_location_digest",
    "s3_repository_ownership_marker",
    "verify_s3_repository_deletion_ownership",
    "verify_s3_repository_ownership",
]
