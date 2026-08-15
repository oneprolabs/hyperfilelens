from __future__ import annotations

import hashlib
import json
import posixpath
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.node.models import NodeTask
from apps.storage.repositories.models import (
    Repository,
    RepositoryLocationClaim,
    RepositoryLocationNamespace,
)
from apps.storage.services.internal.nas_repository import (
    nas_proxy_repository_subdir,
)
from apps.storage.services.internal.repository_endpoints import (
    repository_control_endpoint,
)


ACTIVE_CLAIM_STATES = (
    RepositoryLocationClaim.State.RESERVED,
    RepositoryLocationClaim.State.INITIALIZING,
    RepositoryLocationClaim.State.OWNED,
    RepositoryLocationClaim.State.RESIDUAL,
)


class RepositoryLocationConflict(ValidationError):
    def __init__(
        self, *, repository: Repository, conflicting_claim: RepositoryLocationClaim
    ):
        conflicting_repository = conflicting_claim.repository
        same_organization = int(conflicting_repository.organization_id) == int(
            repository.organization_id
        )
        if same_organization:
            detail = (
                f'This storage location overlaps repository "{conflicting_repository.name}". '
                "Choose a different location."
            )
        else:
            detail = (
                "This storage location is already reserved by another repository. "
                "Choose a different location."
            )
        super().__init__(detail)
        self.conflicting_repository_id = (
            int(conflicting_repository.id) if same_organization else None
        )


@dataclass(frozen=True)
class RepositoryLocationSpec:
    kind: str
    namespace_key: str
    display_hint: str
    root_path: str
    scope: str
    owner_node_id: int | None = None


def reserve_repository_location(
    repository: Repository,
) -> RepositoryLocationClaim | None:
    """Reserve the physical root for a repository initialized at create time."""
    spec = repository_location_spec(repository)
    if spec is None:
        return None
    return _reserve(repository=repository, spec=spec)


def reserve_direct_nas_location(
    *,
    repository: Repository,
    node_id: int,
    repository_subdir: str,
) -> RepositoryLocationClaim:
    spec = repository_location_spec(
        repository,
        owner_node_id=node_id,
        repository_subdir=repository_subdir,
    )
    if spec is None:
        raise ValidationError("Direct NAS repository location is unavailable.")
    return _reserve(repository=repository, spec=spec)


def repository_location_spec(
    repository: Repository,
    *,
    owner_node_id: int | None = None,
    repository_subdir: str = "",
    s3_owner_id: str | None = None,
    s3_namespace_resolved: bool = False,
) -> RepositoryLocationSpec | None:
    config = repository.config if isinstance(repository.config, dict) else {}
    if repository.repo_type == Repository.Type.S3:
        endpoint = _normalize_host(repository_control_endpoint(config))
        bucket = str(repository.s3_bucket or "").strip().lower()
        platform = str(repository.s3_platform or "custom").strip().lower()
        if s3_namespace_resolved:
            owner = str(s3_owner_id or "").strip()
            if owner:
                identity = {
                    "kind": RepositoryLocationNamespace.Kind.S3,
                    "platform": platform,
                    "owner": owner,
                    "bucket": bucket,
                }
                if platform == Repository.S3Platform.CUSTOM:
                    identity["endpoint"] = endpoint
            else:
                # Bucket-scoped credentials may not expose an account ID. In
                # that case use the conservative endpoint namespace instead
                # of treating a credential string as storage identity.
                identity = {
                    "kind": RepositoryLocationNamespace.Kind.S3,
                    "endpoint": endpoint,
                    "bucket": bucket,
                }
        else:
            # This is only a short-lived reservation key. Initialization must
            # resolve it to a storage-derived namespace before writing data.
            identity = {
                "kind": RepositoryLocationNamespace.Kind.S3,
                "endpoint": endpoint,
                "bucket": bucket,
                "provisional_credential": _digest(
                    str(config.get("access_key_id") or "").strip()
                ),
            }
        return RepositoryLocationSpec(
            kind=RepositoryLocationNamespace.Kind.S3,
            namespace_key=_namespace_key(identity),
            display_hint=_join_hint(endpoint, bucket),
            root_path=_normalize_s3_root(config.get("prefix")),
            scope=RepositoryLocationClaim.Scope.REPOSITORY,
        )

    if repository.repo_type == Repository.Type.NAS:
        if repository.bind_node_id is None and owner_node_id is None:
            # Direct NAS is a logical shared configuration. Its physical root
            # is known only after a backup source selects an execution Agent.
            return None
        protocol = str(repository.nas_protocol or "").strip().lower()
        share = _normalize_share(config.get("share_path"))
        if protocol == Repository.NasProtocol.SMB:
            share = share.lower()
        direct = repository.bind_node_id is None
        node_id = (
            int(owner_node_id)
            if owner_node_id is not None
            else int(repository.bind_node_id or 0) or None
        )
        identity = {
            "kind": RepositoryLocationNamespace.Kind.NAS,
            # Private NAS addresses are only meaningful inside the Agent or
            # Proxy network that can reach them. Two customers may both use
            # 192.168.1.10:/backup without referring to the same appliance.
            # The database Claim therefore coordinates one execution boundary;
            # the marker on the mounted storage remains the physical authority.
            "execution_node_id": node_id,
            "protocol": protocol,
            "server": _normalize_host(config.get("server_address")),
            "share": share,
        }
        root_path = (
            repository_subdir if direct else nas_proxy_repository_subdir(repository)
        )
        return RepositoryLocationSpec(
            kind=RepositoryLocationNamespace.Kind.NAS,
            namespace_key=_namespace_key(identity),
            display_hint=_join_hint(
                f"Node #{node_id}" if node_id is not None else "",
                identity["server"],
                identity["share"],
            ),
            root_path=_normalize_root(root_path),
            scope=(
                RepositoryLocationClaim.Scope.DIRECT_NAS_AGENT
                if direct
                else RepositoryLocationClaim.Scope.REPOSITORY
            ),
            owner_node_id=node_id,
        )

    if repository.repo_type == Repository.Type.PROXY_FS:
        base_path = _normalize_absolute_path(
            config.get("proxy_node_base_dir") or config.get("proxy_node_dir")
        )
        identity = {
            "kind": RepositoryLocationNamespace.Kind.PROXY_FS,
            "node_id": int(repository.bind_node_id or 0),
        }
        return RepositoryLocationSpec(
            kind=RepositoryLocationNamespace.Kind.PROXY_FS,
            namespace_key=_namespace_key(identity),
            display_hint=f"Proxy #{identity['node_id']}: {base_path}",
            root_path=_normalize_absolute_path(config.get("proxy_node_dir")),
            scope=RepositoryLocationClaim.Scope.REPOSITORY,
            owner_node_id=int(repository.bind_node_id or 0) or None,
        )
    return None


@transaction.atomic
def resolve_s3_repository_namespace(
    repository: Repository,
    *,
    owner_id: str | None,
) -> RepositoryLocationClaim:
    """Replace a credential-scoped reservation with a storage namespace."""
    locked_repository = Repository.objects.select_for_update().get(
        pk=repository.id,
        organization_id=repository.organization_id,
    )
    # Prefer the storage-provider account identity when it is available. Some
    # bucket-scoped credentials cannot call ListBuckets and therefore expose no
    # owner ID. Keep their credential-scoped reservation in that case: two
    # independent cloud accounts may legitimately use the same Endpoint,
    # Bucket name and Prefix. The ownership marker remains the physical
    # authority, so credential rotation or aliases cannot authorize access to
    # another repository.
    spec = repository_location_spec(
        locked_repository,
        s3_owner_id=owner_id,
        s3_namespace_resolved=bool(str(owner_id or "").strip()),
    )
    if spec is None:
        raise ValidationError("S3 repository location is unavailable.")
    target_namespace, _created = RepositoryLocationNamespace.objects.get_or_create(
        namespace_key=spec.namespace_key,
        defaults={"kind": spec.kind, "display_hint": spec.display_hint[:700]},
    )
    conservative_spec = repository_location_spec(
        locked_repository,
        s3_namespace_resolved=True,
    )
    conservative_namespace = (
        RepositoryLocationNamespace.objects.filter(
            namespace_key=conservative_spec.namespace_key,
        ).first()
        if conservative_spec is not None
        else None
    )
    current_claim = (
        locked_repository.location_claims.filter(
            scope=RepositoryLocationClaim.Scope.REPOSITORY,
            state__in=ACTIVE_CLAIM_STATES,
        )
        .order_by("id")
        .first()
    )
    namespace_ids = {target_namespace.id}
    if current_claim is not None:
        namespace_ids.add(current_claim.namespace_id)
    if conservative_namespace is not None:
        namespace_ids.add(conservative_namespace.id)
    list(
        RepositoryLocationNamespace.objects.select_for_update()
        .filter(id__in=sorted(namespace_ids))
        .order_by("id")
    )
    conflicts = (
        RepositoryLocationClaim.objects.select_related("repository")
        .filter(namespace_id__in=namespace_ids, state__in=ACTIVE_CLAIM_STATES)
        .exclude(repository=locked_repository)
        .order_by("id")
    )
    for claim in conflicts:
        if _roots_overlap(spec.root_path, claim.root_path):
            raise RepositoryLocationConflict(
                repository=locked_repository,
                conflicting_claim=claim,
            )
    if current_claim is None:
        return RepositoryLocationClaim.objects.create(
            organization_id=locked_repository.organization_id,
            repository=locked_repository,
            namespace=target_namespace,
            scope=spec.scope,
            root_path=spec.root_path,
            state=RepositoryLocationClaim.State.RESERVED,
            namespace_resolved_at=timezone.now(),
        )
    current_claim.namespace = target_namespace
    current_claim.root_path = spec.root_path
    current_claim.namespace_resolved_at = timezone.now()
    current_claim.save(
        update_fields=[
            "namespace",
            "root_path",
            "namespace_resolved_at",
            "updated_at",
        ]
    )
    return current_claim


@transaction.atomic
def _reserve(
    *,
    repository: Repository,
    spec: RepositoryLocationSpec,
) -> RepositoryLocationClaim:
    namespace, _created = RepositoryLocationNamespace.objects.get_or_create(
        namespace_key=spec.namespace_key,
        defaults={"kind": spec.kind, "display_hint": spec.display_hint[:700]},
    )
    namespace = RepositoryLocationNamespace.objects.select_for_update().get(
        pk=namespace.pk
    )
    claims = list(
        RepositoryLocationClaim.objects.select_related("repository")
        .filter(namespace=namespace, state__in=ACTIVE_CLAIM_STATES)
        .exclude(repository=repository)
        .order_by("id")
    )
    for claim in claims:
        if _roots_overlap(spec.root_path, claim.root_path):
            raise RepositoryLocationConflict(
                repository=repository,
                conflicting_claim=claim,
            )

    existing = (
        RepositoryLocationClaim.objects.filter(
            repository=repository,
            namespace=namespace,
            scope=spec.scope,
            root_path=spec.root_path,
            owner_node_id=spec.owner_node_id,
            state__in=ACTIVE_CLAIM_STATES,
        )
        .order_by("-id")
        .first()
    )
    if existing is not None:
        return existing
    return RepositoryLocationClaim.objects.create(
        organization_id=repository.organization_id,
        repository=repository,
        namespace=namespace,
        scope=spec.scope,
        root_path=spec.root_path,
        owner_node_id=spec.owner_node_id,
        state=RepositoryLocationClaim.State.RESERVED,
    )


def mark_repository_location_owned(
    repository: Repository,
    *,
    owner_node_id: int | None = None,
    repository_subdir: str | None = None,
) -> None:
    now = timezone.now()
    query = repository.location_claims.filter(
        state__in=[
            RepositoryLocationClaim.State.RESERVED,
            RepositoryLocationClaim.State.INITIALIZING,
            RepositoryLocationClaim.State.RESIDUAL,
        ]
    )
    if owner_node_id is not None:
        query = query.filter(owner_node_id=owner_node_id)
    if repository_subdir is not None:
        query = query.filter(root_path=_normalize_root(repository_subdir))
    query.update(
        state=RepositoryLocationClaim.State.OWNED,
        initialized_at=now,
        last_verified_at=now,
        released_at=None,
    )


def mark_repository_location_ownership_verified(
    repository: Repository,
    *,
    owner_node_id: int | None = None,
    repository_subdir: str | None = None,
) -> None:
    query = repository.location_claims.filter(state__in=ACTIVE_CLAIM_STATES)
    if owner_node_id is not None:
        query = query.filter(owner_node_id=owner_node_id)
    if repository_subdir is not None:
        query = query.filter(root_path=_normalize_root(repository_subdir))
    now = timezone.now()
    query.update(
        state=RepositoryLocationClaim.State.OWNED,
        last_verified_at=now,
        ownership_verified_at=now,
        legacy_adoption_required=False,
        released_at=None,
    )


def invalidate_repository_location_ownership(
    repository: Repository,
    *,
    owner_node_id: int | None = None,
    repository_subdir: str | None = None,
) -> None:
    """Fail closed when the physical ownership marker is absent or changed."""
    query = repository.location_claims.filter(state__in=ACTIVE_CLAIM_STATES)
    if owner_node_id is not None:
        query = query.filter(owner_node_id=owner_node_id)
    if repository_subdir is not None:
        query = query.filter(root_path=_normalize_root(repository_subdir))
    query.update(
        state=RepositoryLocationClaim.State.RESIDUAL,
        released_at=None,
    )


def mark_repository_location_initializing(
    repository: Repository,
    *,
    owner_node_id: int | None = None,
    repository_subdir: str | None = None,
    include_residual: bool = False,
) -> None:
    """Record that physical initialization may already have changed storage."""
    states = [RepositoryLocationClaim.State.RESERVED]
    if include_residual:
        states.append(RepositoryLocationClaim.State.RESIDUAL)
    query = repository.location_claims.filter(
        state__in=states,
    )
    if owner_node_id is not None:
        query = query.filter(owner_node_id=owner_node_id)
    if repository_subdir is not None:
        query = query.filter(root_path=_normalize_root(repository_subdir))
    query.update(
        state=RepositoryLocationClaim.State.INITIALIZING,
        released_at=None,
    )


def mark_repository_location_residual(
    repository: Repository,
    *,
    owner_node_id: int | None = None,
    repository_subdir: str | None = None,
) -> None:
    query = repository.location_claims.filter(state__in=ACTIVE_CLAIM_STATES)
    if owner_node_id is not None:
        query = query.filter(owner_node_id=owner_node_id)
    if repository_subdir is not None:
        query = query.filter(root_path=_normalize_root(repository_subdir))
    query.update(state=RepositoryLocationClaim.State.RESIDUAL, released_at=None)


def mark_repository_location_initialization_failed(
    repository: Repository,
    *,
    owner_node_id: int | None = None,
    repository_subdir: str | None = None,
) -> None:
    """Retain an uncertain initialization without overriding a newer success."""
    query = repository.location_claims.filter(
        state__in=[
            RepositoryLocationClaim.State.RESERVED,
            RepositoryLocationClaim.State.INITIALIZING,
            RepositoryLocationClaim.State.RESIDUAL,
        ]
    )
    if owner_node_id is not None:
        query = query.filter(owner_node_id=owner_node_id)
    if repository_subdir is not None:
        query = query.filter(root_path=_normalize_root(repository_subdir))
    query.update(state=RepositoryLocationClaim.State.RESIDUAL, released_at=None)


def release_repository_location(
    repository: Repository,
    *,
    owner_node_id: int | None = None,
    repository_subdir: str | None = None,
) -> None:
    query = repository.location_claims.filter(state__in=ACTIVE_CLAIM_STATES)
    if owner_node_id is not None:
        query = query.filter(owner_node_id=owner_node_id)
    if repository_subdir is not None:
        query = query.filter(root_path=_normalize_root(repository_subdir))
    query.update(
        state=RepositoryLocationClaim.State.RELEASED, released_at=timezone.now()
    )


@transaction.atomic
def release_repository_residual_locations(repository: Repository) -> int:
    """Release residual claims after an operator confirms external cleanup."""
    from apps.task.models import Task

    locked = Repository.objects.select_for_update().get(
        pk=repository.id,
        organization_id=repository.organization_id,
    )
    if locked.status != Repository.Status.REMOVED:
        raise ValidationError(
            "Residual locations can only be released for a removed repository."
        )
    if Task.objects.filter(
        repository_operation__repository=locked,
        status__in=[Task.Status.PENDING, Task.Status.RUNNING],
    ).exists():
        raise ValidationError("Repository still has an active operation.")
    if (
        NodeTask.objects.filter(
            organization_id=locked.organization_id,
            status__in=[NodeTask.Status.PENDING, NodeTask.Status.RUNNING],
        )
        .filter(
            # Persisted Agent tasks use a top-level repository_id; retain the
            # nested form for legacy/direct task rows as well. Releasing a
            # residual claim while either form is still running could allow a
            # new repository to claim the same physical location.
            Q(payload__repository_id=locked.id)
            | Q(payload__repository_id=str(locked.id))
            | Q(payload__repository__id=locked.id)
            | Q(payload__repository__id=str(locked.id))
        )
        .exists()
    ):
        raise ValidationError("Repository still has an active Agent operation.")
    claims = RepositoryLocationClaim.objects.select_for_update().filter(
        repository=locked,
        state=RepositoryLocationClaim.State.RESIDUAL,
    )
    claim_ids = list(claims.values_list("id", flat=True))
    if not claim_ids:
        raise ValidationError("Repository has no residual storage locations.")
    return RepositoryLocationClaim.objects.filter(id__in=claim_ids).update(
        state=RepositoryLocationClaim.State.RELEASED,
        released_at=timezone.now(),
    )


def repository_has_owned_location(
    repository: Repository,
    *,
    owner_node_id: int | None = None,
    repository_subdir: str | None = None,
) -> bool:
    query = repository.location_claims.filter(
        state=RepositoryLocationClaim.State.OWNED,
        ownership_verified_at__isnull=False,
    )
    if owner_node_id is not None:
        query = query.filter(owner_node_id=owner_node_id)
    if repository_subdir is not None:
        query = query.filter(root_path=_normalize_root(repository_subdir))
    return query.exists()


def repository_has_legacy_location(
    repository: Repository,
    *,
    owner_node_id: int | None = None,
    repository_subdir: str | None = None,
) -> bool:
    """Return whether a migrated location may use non-destructive workloads."""
    query = repository.location_claims.filter(
        state=RepositoryLocationClaim.State.OWNED,
        ownership_verified_at__isnull=True,
        legacy_adoption_required=True,
    )
    if owner_node_id is not None:
        query = query.filter(owner_node_id=owner_node_id)
    if repository_subdir is not None:
        query = query.filter(root_path=_normalize_root(repository_subdir))
    return query.exists()


def _roots_overlap(left: str, right: str) -> bool:
    left_parts = _path_parts(left)
    right_parts = _path_parts(right)
    shorter = min(len(left_parts), len(right_parts))
    return left_parts[:shorter] == right_parts[:shorter]


def _path_parts(value: str) -> tuple[str, ...]:
    return tuple(
        part for part in str(value or "").replace("\\", "/").split("/") if part
    )


def _normalize_root(value: object) -> str:
    parts = _path_parts(str(value or "").strip())
    if not parts:
        raise ValidationError("Repository root path is required.")
    return "/".join(parts)


def _normalize_s3_root(value: object) -> str:
    """Return the canonical Claim root for an S3 Prefix or Bucket root."""
    parts = _path_parts(str(value or "").strip())
    return "/".join(parts) if parts else "/"


def _normalize_absolute_path(value: object) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    normalized = posixpath.normpath(raw)
    if not normalized.startswith("/") or normalized == "/":
        raise ValidationError(
            "Repository filesystem path must be an absolute child path."
        )
    return normalized


def _normalize_share(value: object) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    normalized = "/" + "/".join(_path_parts(raw))
    return normalized.rstrip("/") or "/"


def _normalize_host(value: object) -> str:
    return str(value or "").strip().rstrip("/").lower().rstrip(".")


def _namespace_key(identity: dict[str, object]) -> str:
    encoded = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _join_hint(*parts: object) -> str:
    return "/".join(str(part or "").strip().strip("/") for part in parts if part)


__all__ = [
    "ACTIVE_CLAIM_STATES",
    "RepositoryLocationConflict",
    "mark_repository_location_initialization_failed",
    "mark_repository_location_initializing",
    "invalidate_repository_location_ownership",
    "mark_repository_location_owned",
    "mark_repository_location_ownership_verified",
    "mark_repository_location_residual",
    "release_repository_location",
    "release_repository_residual_locations",
    "repository_has_owned_location",
    "repository_has_legacy_location",
    "repository_location_spec",
    "resolve_s3_repository_namespace",
    "reserve_direct_nas_location",
    "reserve_repository_location",
]
