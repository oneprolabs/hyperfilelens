"""
Storage read facade — other apps should import this module only.
"""

from __future__ import annotations

from django.db.models import Prefetch, Q, QuerySet

from apps.storage.repositories.models import Credential, Repository, RepositoryTask
from apps.task.models import Task
from apps.storage.provider_catalog.catalog import (
    effective_catalog as _effective_provider_catalog,
    effective_provider as _effective_provider,
    effective_provider_records as _effective_provider_records,
)


def get_effective_provider_catalog() -> dict:
    return _effective_provider_catalog()


def get_effective_storage_provider(provider_id: str) -> dict | None:
    return _effective_provider(provider_id)


def list_effective_storage_providers() -> list[dict]:
    records = _effective_provider_records()
    return [
        {
            **record["config"],
            "source": record["source"],
            "checksum": record["checksum"],
            "updated_at": record["updated_at"],
            "region_count": len(record["config"]["regions"]),
        }
        for record in records.values()
    ]


def list_repositories(
    *,
    organization_id: int,
    repo_type: str | None = None,
    status: str | None = None,
    health: str | None = None,
    search: str | None = None,
    search_field: str | None = None,
) -> QuerySet[Repository]:
    qs = Repository.objects.filter(organization_id=organization_id)
    if repo_type:
        qs = qs.filter(repo_type=repo_type)
    if status:
        qs = qs.filter(status=status)
    if health:
        qs = qs.filter(health=health)
    if search:
        term = search.strip()
        if term:
            field_q = (
                _repository_field_search_q(search_field or "", term)
                if search_field
                else None
            )
            qs = qs.filter(
                field_q
                or (
                    Q(name__icontains=term)
                    | Q(s3_bucket__icontains=term)
                    | Q(nas_protocol__icontains=term)
                    | Q(bind_node_type__icontains=term)
                )
            )
    active_lifecycle_operations = (
        RepositoryTask.objects.filter(
            task__status__in=(Task.Status.PENDING, Task.Status.RUNNING),
        )
        .select_related("task")
        .order_by("-created_at", "-id")
    )
    return qs.prefetch_related(
        "location_claims",
        Prefetch(
            "repository_tasks",
            queryset=active_lifecycle_operations,
            to_attr="active_lifecycle_operations",
        ),
    ).order_by("name", "id")


def _repository_field_search_q(field: str, term: str) -> Q | None:
    normalized = (field or "").strip().lower()
    if normalized == "name":
        return Q(name__icontains=term)
    if normalized == "bucket":
        return Q(s3_bucket__icontains=term) | Q(config__bucket__icontains=term)
    if normalized == "server":
        return (
            Q(config__server_address__icontains=term)
            | Q(config__smb_server__icontains=term)
            | Q(config__nfs_host__icontains=term)
        )
    return None


def get_credential(*, organization_id: int, credential_id: int) -> Credential | None:
    return Credential.objects.filter(
        organization_id=organization_id,
        id=credential_id,
    ).first()


def repository_display_name(
    *, repository_id: int, organization_id: int | None = None
) -> str:
    qs = Repository.objects.filter(pk=repository_id)
    if organization_id:
        qs = qs.filter(organization_id=organization_id)
    return str(qs.values_list("name", flat=True).first() or "").strip()
