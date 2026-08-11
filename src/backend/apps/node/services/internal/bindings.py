"""Bindings between a Proxy node and storage / source resources.

Used by both the Proxy delete guard and the bindings detail endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import Count, Q

from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.storage.repositories.models import Repository


@dataclass(frozen=True)
class ProxyBindings:
    proxy_id: int
    target_nas_repositories: list[dict[str, Any]]
    standalone_disk_repositories: list[dict[str, Any]]
    source_nas_resources: list[dict[str, Any]]

    @property
    def totals(self) -> dict[str, int]:
        return {
            "target_nas_repositories": len(self.target_nas_repositories),
            "standalone_disk_repositories": len(self.standalone_disk_repositories),
            "source_nas_resources": len(self.source_nas_resources),
        }

    def is_empty(self) -> bool:
        return not (self.target_nas_repositories or self.standalone_disk_repositories or self.source_nas_resources)

    def to_payload(self) -> dict[str, Any]:
        return {
            "proxy_id": self.proxy_id,
            "target_nas_repositories": self.target_nas_repositories,
            "standalone_disk_repositories": self.standalone_disk_repositories,
            "source_nas_resources": self.source_nas_resources,
            "totals": self.totals,
        }


def _serialize_repository(repo: Repository) -> dict[str, Any]:
    config = repo.config if isinstance(repo.config, dict) else {}
    try:
        planned_limit_bytes = max(0, round(float(config.get("quota_gb") or 0) * 1024**3))
    except (TypeError, ValueError, OverflowError):
        planned_limit_bytes = 0
    return {
        "id": repo.id,
        "name": repo.name,
        "repo_type": repo.repo_type,
        "status": repo.status,
        "health": repo.health,
        "config": config,
        "s3_bucket": repo.s3_bucket,
        "s3_platform": repo.s3_platform,
        "nas_protocol": repo.nas_protocol,
        "capacity_bytes": int(repo.capacity_bytes or 0),
        "estimated_usage_bytes": int(repo.estimated_usage_bytes or 0),
        "planned_limit_bytes": planned_limit_bytes,
        "storage_total_bytes": int(repo.storage_total_bytes or 0),
        "storage_used_bytes": int(repo.storage_used_bytes or 0),
        "storage_available_bytes": int(repo.storage_available_bytes or 0),
        "storage_pool_key": str(repo.storage_pool_key or ""),
        "storage_mount_point": str(repo.storage_mount_point or ""),
    }


def _serialize_source(resource: SourceResource) -> dict[str, Any]:
    return {
        "id": resource.id,
        "name": resource.name,
        "resource_type": resource.resource_type,
        "mount_status": resource.mount_status,
        "mount_point": resource.mount_point,
        "status": resource.status,
        "config": resource.config or {},
    }


def collect_proxy_bindings(
    *,
    organization_id: int,
    proxy_id: int,
) -> ProxyBindings:
    """Collect all resources bound to the given Proxy within an organization."""

    target_qs = Repository.objects.filter(
        organization_id=organization_id,
        bind_node_type=Repository.BindNodeType.PROXY,
        bind_node_id=proxy_id,
        repo_type=Repository.Type.NAS,
    ).exclude(status=Repository.Status.REMOVED)
    standalone_qs = Repository.objects.filter(
        organization_id=organization_id,
        bind_node_type=Repository.BindNodeType.PROXY,
        bind_node_id=proxy_id,
        repo_type=Repository.Type.PROXY_FS,
    ).exclude(status=Repository.Status.REMOVED)
    source_qs = SourceResource.objects.filter(
        organization_id=organization_id,
        bound_node_id=proxy_id,
        resource_type__in=(ResourceType.NAS, ResourceType.NFS, ResourceType.CIFS),
        is_deleted=False,
    )

    return ProxyBindings(
        proxy_id=proxy_id,
        target_nas_repositories=[_serialize_repository(r) for r in target_qs.order_by("id")],
        standalone_disk_repositories=[_serialize_repository(r) for r in standalone_qs.order_by("id")],
        source_nas_resources=[_serialize_source(r) for r in source_qs.order_by("id")],
    )


def proxy_has_bindings(
    *,
    organization_id: int,
    proxy_id: int,
) -> bool:
    """Quick check used by the Proxy delete guard."""

    return (
        Repository.objects.filter(
            organization_id=organization_id,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy_id,
            repo_type__in=(Repository.Type.NAS, Repository.Type.PROXY_FS),
        )
        .exclude(status=Repository.Status.REMOVED)
        .exists()
        or SourceResource.objects.filter(
            organization_id=organization_id,
            bound_node_id=proxy_id,
            resource_type__in=(ResourceType.NAS, ResourceType.NFS, ResourceType.CIFS),
            is_deleted=False,
        ).exists()
    )


def proxy_bound_node_qs() -> Q:
    """Reusable Q expression for queries that want to count proxy bindings."""

    return Q(
        bind_node_type=Repository.BindNodeType.PROXY,
        repo_type__in=(Repository.Type.NAS, Repository.Type.PROXY_FS),
    ) & ~Q(status=Repository.Status.REMOVED)


def count_proxy_repository_bindings(
    *,
    organization_id: int,
    proxy_ids: list[int],
) -> dict[int, int]:
    """Return repository counts for Proxy nodes without per-node queries."""

    if not proxy_ids:
        return {}
    rows = (
        Repository.objects.filter(
            organization_id=organization_id,
            bind_node_id__in=proxy_ids,
        )
        .filter(proxy_bound_node_qs())
        .values("bind_node_id")
        .annotate(repository_count=Count("id"))
    )
    return {
        int(row["bind_node_id"]): int(row["repository_count"])
        for row in rows
        if row["bind_node_id"] is not None
    }
