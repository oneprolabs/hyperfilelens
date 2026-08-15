from __future__ import annotations

from collections.abc import Iterable

from django.core.exceptions import ValidationError

from apps.storage.repositories.models import Repository, RepositoryLocationClaim
from apps.storage.services.internal.repository_location import (
    repository_has_legacy_location,
)


class RepositoryWorkload:
    BACKUP_WRITE = "backup_write"
    RESTORE_READ = "restore_read"
    SNAPSHOT_DELETE = "snapshot_delete"

    VALUES = {BACKUP_WRITE, RESTORE_READ, SNAPSHOT_DELETE}


def lock_repositories_for_workload(
    *,
    organization_id: int,
    repository_ids: Iterable[int],
    workload: str = RepositoryWorkload.RESTORE_READ,
) -> list[Repository]:
    """Lock and revalidate repositories before accepting data work."""
    if workload not in RepositoryWorkload.VALUES:
        raise ValueError(f"Unsupported repository workload: {workload}")
    ordered_ids = sorted({int(repository_id) for repository_id in repository_ids})
    if not ordered_ids:
        raise ValueError("At least one repository is required for data work.")

    repositories = list(
        Repository.objects.select_for_update()
        .filter(
            organization_id=organization_id,
            id__in=ordered_ids,
        )
        .order_by("id")
    )
    by_id = {repository.id: repository for repository in repositories}
    if any(repository_id not in by_id for repository_id in ordered_ids):
        raise ValidationError(
            {"repository_id": "Repository is no longer available for this operation."}
        )
    ordered = [by_id[repository_id] for repository_id in ordered_ids]
    for repository in ordered:
        _require_repository_capability(repository, workload=workload)
    return ordered


def _require_repository_capability(repository: Repository, *, workload: str) -> None:
    if repository.status != Repository.Status.CREATED:
        raise ValidationError(
            {"repository_id": "Repository is no longer available for this operation."}
        )

    direct_nas = (
        repository.repo_type == Repository.Type.NAS and repository.bind_node_id is None
    )
    ownership_verified = RepositoryLocationClaim.objects.filter(
        repository=repository,
        scope=RepositoryLocationClaim.Scope.REPOSITORY,
        state=RepositoryLocationClaim.State.OWNED,
        ownership_verified_at__isnull=False,
    ).exists()
    legacy_non_destructive = workload in {
        RepositoryWorkload.BACKUP_WRITE,
        RepositoryWorkload.RESTORE_READ,
    } and repository_has_legacy_location(repository)
    if not direct_nas and not ownership_verified and not legacy_non_destructive:
        raise ValidationError(
            {
                "repository_id": (
                    "Repository ownership has not been verified. Run a health "
                    "check before using it."
                )
            }
        )

    if (
        workload
        in {RepositoryWorkload.RESTORE_READ, RepositoryWorkload.SNAPSHOT_DELETE}
        and repository.health != Repository.Health.ONLINE
    ):
        raise ValidationError(
            {
                "repository_id": (
                    "Repository is not currently available for read operations."
                )
            }
        )


__all__ = ["RepositoryWorkload", "lock_repositories_for_workload"]
