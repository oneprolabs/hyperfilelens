"""Collect license usage statistics for an organization."""

from __future__ import annotations

from apps.iam.models import Membership, Organization
from apps.subscription.constants import UNLIMITED


def _org_public_gateway_capacity_used_gb(organization_id: int) -> float:
    try:
        from apps.lens_bridge.services.public_gateway_capacity import (
            org_public_gateway_capacity_used_gb,
        )

        return float(org_public_gateway_capacity_used_gb(organization_id=organization_id))
    except Exception:
        return 0.0


def collect_usage_stats(*, organization_id: int) -> dict:
    org = Organization.objects.filter(id=organization_id).first()
    if org is None:
        return _empty_usage()

    users_count = Membership.objects.filter(organization_id=organization_id, is_active=True).count()
    nodes_count = 0
    agents_count = 0
    proxies_count = 0
    gateways_count = 0
    source_nas_count = 0
    object_storage_count = 0
    target_nas_count = 0
    standalone_disk_count = 0
    alert_policies_count = 0
    protected_sources_count = 0
    ai_tokens_used = 0
    try:
        from apps.node.models import Node
        from apps.node.models.base import NodeRole

        node_qs = Node.objects.filter(organization_id=organization_id)
        nodes_count = node_qs.count()
        agents_count = node_qs.filter(role=NodeRole.AGENT).count()
        proxies_count = node_qs.filter(role=NodeRole.PROXY).count()
        gateways_count = node_qs.filter(role=NodeRole.GATEWAY).count()
    except Exception:
        pass
    try:
        from apps.source.constants import ResourceStatus, ResourceType
        from apps.source.models import SourceResource

        source_nas_count = (
            SourceResource.objects.filter(
                organization_id=organization_id,
                resource_type__in=(ResourceType.NAS, ResourceType.NFS, ResourceType.CIFS),
            )
            .exclude(
                status__in=(ResourceStatus.REMOVING, ResourceStatus.REMOVED),
            )
            .count()
        )
    except Exception:
        pass
    try:
        from apps.storage.repositories.models import Repository

        repo_qs = Repository.objects.filter(organization_id=organization_id).exclude(
            status=Repository.Status.REMOVED
        )
        object_storage_count = repo_qs.filter(repo_type=Repository.Type.S3).count()
        target_nas_count = repo_qs.filter(repo_type=Repository.Type.NAS).count()
        standalone_disk_count = repo_qs.filter(repo_type=Repository.Type.PROXY_FS).count()
    except Exception:
        pass
    try:
        from apps.alert.models import AlertPolicy

        alert_policies_count = AlertPolicy.objects.filter(organization_id=organization_id).count()
    except Exception:
        pass
    try:
        from apps.protection.models.backup_config import BackupConfig

        protected_sources_count = BackupConfig.objects.filter(
            organization_id=organization_id,
        ).count()
    except Exception:
        pass
    try:
        from django.db.models import Sum

        from apps.lens_bridge.models import LensUsageLedger

        ai_tokens_used = int(
            LensUsageLedger.objects.filter(organization_id=organization_id)
            .aggregate(total=Sum("total_tokens"))
            .get("total")
            or 0
        )
    except Exception:
        ai_tokens_used = 0

    try:
        from django.db.models import Sum, Value
        from django.db.models.functions import Coalesce

        from apps.storage.repositories.models import Repository

        # Prefer physical probe when present; else estimated usage (Host domain facts).
        # Exclude REMOVED tombstones so deleted repos do not inflate quota.
        total_bytes = (
            Repository.objects.filter(organization_id=organization_id)
            .exclude(status=Repository.Status.REMOVED)
            .aggregate(
                total=Sum(
                    Coalesce(
                        "physical_usage_bytes",
                        "estimated_usage_bytes",
                        Value(0),
                    )
                )
            )
            .get("total")
        )
        storage_used_gb = float(total_bytes or 0) / float(1024**3)
    except Exception:
        storage_used_gb = 0.0

    return {
        "organizations_count": Organization.objects.filter(is_active=True).count(),
        "users_count": users_count,
        "nodes_count": nodes_count,
        "agents_count": agents_count,
        "proxies_count": proxies_count,
        "gateways_count": gateways_count,
        "source_nas_count": source_nas_count,
        "object_storage_count": object_storage_count,
        "target_nas_count": target_nas_count,
        "standalone_disk_count": standalone_disk_count,
        "protected_sources_count": protected_sources_count,
        "storage_used_gb": storage_used_gb,
        "public_gateway_capacity_used_gb": _org_public_gateway_capacity_used_gb(
            organization_id
        ),
        "ai_tokens_used": ai_tokens_used,
        # Legacy aliases (same lifetime token total).
        "ai_insights_used": ai_tokens_used,
        "ai_requests_used": ai_tokens_used,
        "alert_policies_count": alert_policies_count,
    }


def _empty_usage() -> dict:
    return {
        "organizations_count": 0,
        "users_count": 0,
        "nodes_count": 0,
        "agents_count": 0,
        "proxies_count": 0,
        "gateways_count": 0,
        "source_nas_count": 0,
        "object_storage_count": 0,
        "target_nas_count": 0,
        "standalone_disk_count": 0,
        "protected_sources_count": 0,
        "storage_used_gb": 0.0,
        "public_gateway_capacity_used_gb": 0.0,
        "ai_tokens_used": 0,
        "ai_insights_used": 0,
        "ai_requests_used": 0,
        "alert_policies_count": 0,
    }


def check_quota_available(*, limit: int, current: int, additional: int = 1) -> bool:
    """Return True if within quota. UNLIMITED (-1) always allowed."""
    if limit == UNLIMITED or limit < 0:
        return True
    return (current + additional) <= limit


def collect_instance_meter_usage(*, usage_key: str) -> float:
    """
    Sum a usage meter across all customer organizations (excludes platform org).

    Used for shared instance-pool enforcement when an org has no explicit Quota row.
    """
    try:
        from apps.lens_bridge.services.platform_lens import PLATFORM_ORG_KEY
    except Exception:  # pragma: no cover
        PLATFORM_ORG_KEY = "__platform_lens__"

    if usage_key in ("ai_tokens_used", "ai_requests_used", "ai_insights_used"):
        try:
            from django.db.models import Sum

            from apps.lens_bridge.models import LensUsageLedger

            return float(
                LensUsageLedger.objects.exclude(organization__key=PLATFORM_ORG_KEY)
                .aggregate(total=Sum("total_tokens"))
                .get("total")
                or 0
            )
        except Exception:
            return 0.0

    total = 0.0
    org_ids = Organization.objects.exclude(key=PLATFORM_ORG_KEY).values_list("id", flat=True)
    for org_id in org_ids:
        stats = collect_usage_stats(organization_id=int(org_id))
        total += float(stats.get(usage_key, 0) or 0)
    return total


def collect_instance_node_pool_usage() -> int:
    """Sum agents + proxies across customer orgs (shared max_nodes pool)."""
    try:
        from apps.lens_bridge.services.platform_lens import PLATFORM_ORG_KEY
        from apps.node.models import Node
        from apps.node.models.base import NodeRole

        return int(
            Node.objects.exclude(organization__key=PLATFORM_ORG_KEY)
            .filter(role__in=(NodeRole.AGENT, NodeRole.PROXY))
            .count()
        )
    except Exception:
        return 0
