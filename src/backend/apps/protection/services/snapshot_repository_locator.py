from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, replace
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.protection import conf as protection_conf
from apps.protection.models import BackupSourceSnapshotDirectory
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.nas_repository import (
    nas_agent_repository_subdir,
    nas_proxy_repository_subdir,
)
from apps.storage.services.internal.repository_access import (
    RepositoryAccess,
    resolve_repository_reader,
)


SNAPSHOT_REPOSITORY_LOCATOR_VERSION = 1

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnapshotRepositoryLocator:
    """Immutable, non-secret location of one physical snapshot."""

    version: int
    repository_id: int
    repository_type: str
    repository_subdir: str = ""
    writer_node_id: int | None = None
    access_node_id: int | None = None

    def payload(self) -> dict[str, Any]:
        """Return the JSON-compatible representation stored with the snapshot."""

        return asdict(self)


def build_snapshot_repository_locator(
    *,
    repository: Repository,
    writer_node_id: int | None,
) -> SnapshotRepositoryLocator:
    """Build the physical snapshot location known at backup dispatch time."""

    normalized_writer_node_id = _positive_int(writer_node_id)
    repository_subdir = ""
    access_node_id: int | None = None

    if repository.repo_type == Repository.Type.NAS:
        if repository.bind_node_type == Repository.BindNodeType.PROXY:
            repository_subdir = nas_proxy_repository_subdir(repository)
            access_node_id = _positive_int(repository.bind_node_id)
        else:
            if normalized_writer_node_id is None:
                raise ValidationError(
                    {
                        "repository_id": "Direct NAS snapshot location requires its backup execution node."
                    }
                )
            repository_subdir = nas_agent_repository_subdir(normalized_writer_node_id)
    elif repository.repo_type == Repository.Type.PROXY_FS:
        access_node_id = _positive_int(repository.bind_node_id)
        if access_node_id is None:
            raise ValidationError(
                {
                    "repository_id": "Proxy filesystem snapshot location requires its bound proxy node."
                }
            )

    return SnapshotRepositoryLocator(
        version=SNAPSHOT_REPOSITORY_LOCATOR_VERSION,
        repository_id=int(repository.id),
        repository_type=str(repository.repo_type),
        repository_subdir=repository_subdir,
        writer_node_id=normalized_writer_node_id,
        access_node_id=access_node_id,
    )


def ensure_snapshot_repository_locator(
    *,
    directory: BackupSourceSnapshotDirectory,
    repository: Repository,
    writer_node_id: int | None,
) -> SnapshotRepositoryLocator:
    """Persist the current physical location without storing credentials.

    Dispatch retries may move before a physical snapshot exists. Once Kopia
    has produced the snapshot, its locator is immutable.
    """

    locator = build_snapshot_repository_locator(
        repository=repository,
        writer_node_id=writer_node_id,
    )
    with transaction.atomic():
        locked = BackupSourceSnapshotDirectory.objects.select_for_update().get(
            pk=directory.pk,
        )
        existing = _stored_locator(directory=locked, repository=repository)
        if existing is not None and (
            existing == locator or locked.kopia_snapshot_id
        ):
            resolved = existing
        else:
            locked.repository_locator = locator.payload()
            locked.save(update_fields=["repository_locator", "updated_at"])
            resolved = locator

    # Keep callers that hold an older model instance consistent with the
    # serialized row. In particular, a delayed retry must not carry its stale
    # locator into subsequent work in the same request.
    directory.repository_locator = resolved.payload()
    return resolved


def resolve_snapshot_repository_locator(
    *,
    directory: BackupSourceSnapshotDirectory,
    repository: Repository,
) -> SnapshotRepositoryLocator:
    """Resolve a stored locator or derive one for a legacy snapshot."""

    existing = _stored_locator(directory=directory, repository=repository)
    if existing is not None:
        return existing
    locator = _build_legacy_snapshot_repository_locator(
        directory=directory,
        repository=repository,
    )
    if not directory.kopia_snapshot_id:
        return locator

    stored = BackupSourceSnapshotDirectory.objects.filter(
        pk=directory.pk,
        repository_locator={},
    ).update(repository_locator=locator.payload())
    if stored:
        directory.repository_locator = locator.payload()
        return locator

    directory.refresh_from_db(fields=["repository_locator"])
    concurrent = _stored_locator(directory=directory, repository=repository)
    return concurrent if concurrent is not None else locator


def resolve_snapshot_repository_reader(
    *,
    directory: BackupSourceSnapshotDirectory,
    repository: Repository,
    fallback_node: Node | None,
    source_type: str = "agent",
    source_ref_id: int | None = None,
) -> RepositoryAccess:
    """Resolve repository access while preserving the snapshot's original shard."""

    locator = resolve_snapshot_repository_locator(
        directory=directory,
        repository=repository,
    )
    return resolve_repository_reader(
        repository=repository,
        fallback_node=fallback_node,
        source_type=source_type,
        source_ref_id=source_ref_id,
        repository_subdir=locator.repository_subdir,
        repository_access_node_id=(
            locator.access_node_id
            if locator.repository_type == Repository.Type.PROXY_FS
            else None
        ),
    )


def group_snapshot_directories_by_repository_locator(
    *,
    directories: list[BackupSourceSnapshotDirectory],
    repository: Repository,
) -> list[list[BackupSourceSnapshotDirectory]]:
    """Group physical snapshot rows that share one repository access context."""

    grouped: dict[
        SnapshotRepositoryLocator,
        list[BackupSourceSnapshotDirectory],
    ] = {}
    for directory in directories:
        locator = resolve_snapshot_repository_locator(
            directory=directory,
            repository=repository,
        )
        grouped.setdefault(locator, []).append(directory)
    return list(grouped.values())


def _stored_locator(
    *,
    directory: BackupSourceSnapshotDirectory,
    repository: Repository,
) -> SnapshotRepositoryLocator | None:
    raw = directory.repository_locator
    if raw == {}:
        return None
    if not isinstance(raw, dict):
        raise ValidationError(
            {"repository_locator": "Snapshot repository locator must be an object."}
        )
    try:
        locator = SnapshotRepositoryLocator(
            version=int(raw.get("version") or 0),
            repository_id=int(raw.get("repository_id") or 0),
            repository_type=str(raw.get("repository_type") or "").strip(),
            repository_subdir=str(raw.get("repository_subdir") or "").strip(),
            writer_node_id=_positive_int(raw.get("writer_node_id")),
            access_node_id=_positive_int(raw.get("access_node_id")),
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            {"repository_locator": "Snapshot repository locator is invalid."}
        ) from exc
    if locator.version != SNAPSHOT_REPOSITORY_LOCATOR_VERSION:
        raise ValidationError(
            {"repository_locator": "Snapshot repository locator version is not supported."}
        )
    if (
        locator.repository_id != int(repository.id)
        or locator.repository_type != str(repository.repo_type)
    ):
        raise ValidationError(
            {"repository_locator": "Snapshot repository locator does not match its repository."}
        )
    if repository.repo_type == Repository.Type.NAS and not locator.repository_subdir:
        raise ValidationError(
            {"repository_locator": "NAS snapshot repository locator has no subdirectory."}
        )
    if (
        repository.repo_type == Repository.Type.PROXY_FS
        and locator.access_node_id is None
    ):
        raise ValidationError(
            {"repository_locator": "Proxy filesystem snapshot locator has no access node."}
        )
    return locator


def _build_legacy_snapshot_repository_locator(
    *,
    directory: BackupSourceSnapshotDirectory,
    repository: Repository,
) -> SnapshotRepositoryLocator:
    locator = build_snapshot_repository_locator(
        repository=repository,
        writer_node_id=_legacy_writer_node_id(directory),
    )
    if repository.repo_type != Repository.Type.PROXY_FS:
        return locator

    historical_access_node_id = _legacy_proxy_fs_access_node_id(directory)
    if historical_access_node_id is not None:
        return replace(locator, access_node_id=historical_access_node_id)

    logger.warning(
        "Legacy ProxyFS snapshot locator fell back to current repository binding "
        "directory_id=%s repository_id=%s bind_node_id=%s",
        directory.id,
        repository.id,
        repository.bind_node_id,
    )
    return locator


def _legacy_proxy_fs_access_node_id(
    directory: BackupSourceSnapshotDirectory,
) -> int | None:
    """Recover the Proxy that exposed a legacy ProxyFS snapshot to its writer."""

    snapshot = directory.source_snapshot
    repository_server_tasks = (
        NodeTask.objects.filter(
            organization_id=directory.organization_id,
            kind="repository.server.start",
            correlation_type=protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE,
            correlation_id=str(snapshot.task_uuid),
            status=NodeTask.Status.SUCCESS,
        )
        .order_by("-created_at", "-id")
        .values("node_id", "payload")
    )
    legacy_node_id: int | None = None
    for task in repository_server_tasks:
        payload = task.get("payload")
        repository_payload = (
            payload.get("repository") if isinstance(payload, dict) else None
        )
        task_repository_id = (
            _positive_int(repository_payload.get("id"))
            if isinstance(repository_payload, dict)
            else None
        )
        if task_repository_id == int(snapshot.repository_id):
            return _positive_int(task.get("node_id"))
        if task_repository_id is None and legacy_node_id is None:
            legacy_node_id = _positive_int(task.get("node_id"))
    if legacy_node_id is not None:
        return legacy_node_id

    if not directory.node_task_id:
        return None
    backup_proxy_node_id = (
        NodeTask.objects.filter(
            organization_id=directory.organization_id,
            id=directory.node_task_id,
            kind="backup.run",
            node__role=NodeRole.PROXY,
        )
        .values_list("node_id", flat=True)
        .first()
    )
    return _positive_int(backup_proxy_node_id)


def _legacy_writer_node_id(directory: BackupSourceSnapshotDirectory) -> int | None:
    if directory.node_task_id:
        node_id = (
            NodeTask.objects.filter(
                organization_id=directory.organization_id,
                id=directory.node_task_id,
            )
            .values_list("node_id", flat=True)
            .first()
        )
        if node_id:
            return int(node_id)

    snapshot = directory.source_snapshot
    if snapshot.source_type == "agent":
        return _positive_int(snapshot.source_ref_id)
    if snapshot.source_type != "nas":
        return None
    node_id = (
        SourceResource.objects.filter(
            organization_id=directory.organization_id,
            id=snapshot.source_ref_id,
            resource_type=ResourceType.NAS,
            is_deleted=False,
        )
        .values_list("bound_node_id", flat=True)
        .first()
    )
    return _positive_int(node_id)


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


__all__ = [
    "SnapshotRepositoryLocator",
    "build_snapshot_repository_locator",
    "ensure_snapshot_repository_locator",
    "group_snapshot_directories_by_repository_locator",
    "resolve_snapshot_repository_locator",
    "resolve_snapshot_repository_reader",
]
