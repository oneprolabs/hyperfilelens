"""Collect license usage statistics for an organization."""

from __future__ import annotations

from apps.iam.models import Membership, Organization
from apps.subscription.constants import UNLIMITED


class IncompleteUsageMeasurementError(RuntimeError):
    """A quota meter has a known lower bound but is not fully measurable."""

    def __init__(self, message: str, *, measured_value: int | float = 0):
        super().__init__(message)
        self.measured_value = measured_value


_INSTANCE_USAGE_KEYS = (
    "organizations_count",
    "users_count",
    "nodes_count",
    "agents_count",
    "proxies_count",
    "gateways_count",
    "source_nas_count",
    "object_storage_count",
    "target_nas_count",
    "standalone_disk_count",
    "protected_sources_count",
    "storage_used_gb",
    "public_gateway_capacity_used_bytes",
    "ai_tokens_used",
    "alert_policies_count",
)


def _org_public_gateway_capacity_used_bytes(organization_id: int) -> int:
    try:
        from apps.lens_bridge.services.public_gateway_capacity import (
            org_public_gateway_capacity_used_bytes,
        )

        return int(
            org_public_gateway_capacity_used_bytes(organization_id=organization_id)
        )
    except Exception:
        return 0


def collect_usage_stats(*, organization_id: int) -> dict:
    org = Organization.objects.filter(id=organization_id).first()
    if org is None:
        return _empty_usage()

    users_count = Membership.objects.filter(
        organization_id=organization_id, is_active=True
    ).count()
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
                resource_type__in=(
                    ResourceType.NAS,
                    ResourceType.NFS,
                    ResourceType.CIFS,
                ),
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
        standalone_disk_count = repo_qs.filter(
            repo_type=Repository.Type.PROXY_FS
        ).count()
    except Exception:
        pass
    try:
        from apps.alert.models import AlertPolicy

        alert_policies_count = AlertPolicy.objects.filter(
            organization_id=organization_id
        ).count()
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
        "public_gateway_capacity_used_bytes": _org_public_gateway_capacity_used_bytes(
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
        "public_gateway_capacity_used_bytes": 0,
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


def _storage_usage_gb(repositories) -> float:
    """Return measured storage usage or raise when any active probe is unknown."""
    from django.db.models import Sum, Value
    from django.db.models.functions import Coalesce

    facts = repositories.aggregate(
        total=Sum(
            Coalesce(
                "physical_usage_bytes",
                "estimated_usage_bytes",
                Value(0),
            )
        ),
    )
    measured_gb = float(facts.get("total") or 0) / float(1024**3)
    if _storage_usage_unknown_repositories(repositories).exists():
        raise IncompleteUsageMeasurementError(
            "Storage usage cannot be measured completely",
            measured_value=measured_gb,
        )
    return measured_gb


def _storage_usage_unknown_repositories(repositories):
    """Return repositories whose managed usage cannot yet be trusted.

    A newly configured Direct NAS target has no physical location until its
    first backup configuration selects an execution node. Before that point it
    is a known zero, even if its asynchronous probe is still pending. Once a
    config or location claim exists, normal fail-closed measurement applies.
    """
    from django.db.models import Exists, OuterRef, Q

    from apps.protection.models.backup_config import BackupConfig
    from apps.storage.repositories.models import Repository, RepositoryLocationClaim

    unknown = repositories.exclude(
        usage_probe_status=Repository.MetricProbeStatus.SUCCESS,
    ).annotate(
        has_backup_config=Exists(
            BackupConfig.objects.filter(repository_id=OuterRef("pk"))
        ),
        has_location_claim=Exists(
            RepositoryLocationClaim.objects.filter(
                repository_id=OuterRef("pk")
            ).exclude(
                state=RepositoryLocationClaim.State.RELEASED,
            )
        ),
    )
    pristine_direct_nas = (
        Q(repo_type=Repository.Type.NAS)
        & (Q(bind_node_type__isnull=True) | Q(bind_node_type=""))
        & Q(bind_node_id__isnull=True)
        & Q(has_backup_config=False)
        & Q(has_location_claim=False)
        & Q(estimated_usage_bytes=0)
        & (Q(physical_usage_bytes__isnull=True) | Q(physical_usage_bytes=0))
    )
    return unknown.exclude(pristine_direct_nas)


def collect_meter_usage(*, organization_id: int, usage_key: str) -> int | float:
    """Read one organization meter directly; errors propagate to quota callers."""
    org_id = int(organization_id)
    if usage_key == "organizations_count":
        from apps.subscription.services.internal.organization_count import (
            count_customer_organizations,
        )

        return count_customer_organizations()
    if usage_key == "users_count":
        return Membership.objects.filter(
            organization_id=org_id,
            is_active=True,
        ).count()
    if usage_key in {"nodes_count", "agents_count", "proxies_count", "gateways_count"}:
        from apps.node.models import Node
        from apps.node.models.base import NodeRole

        nodes = Node.objects.filter(organization_id=org_id)
        roles = {
            "agents_count": NodeRole.AGENT,
            "proxies_count": NodeRole.PROXY,
            "gateways_count": NodeRole.GATEWAY,
        }
        return (
            nodes.filter(role=roles[usage_key]).count()
            if usage_key in roles
            else nodes.count()
        )
    if usage_key == "source_nas_count":
        from apps.source.constants import ResourceStatus, ResourceType
        from apps.source.models import SourceResource

        return (
            SourceResource.objects.filter(
                organization_id=org_id,
                resource_type__in=(
                    ResourceType.NAS,
                    ResourceType.NFS,
                    ResourceType.CIFS,
                ),
            )
            .exclude(status__in=(ResourceStatus.REMOVING, ResourceStatus.REMOVED))
            .count()
        )
    if usage_key in {
        "object_storage_count",
        "target_nas_count",
        "standalone_disk_count",
    }:
        from apps.storage.repositories.models import Repository

        repository_types = {
            "object_storage_count": Repository.Type.S3,
            "target_nas_count": Repository.Type.NAS,
            "standalone_disk_count": Repository.Type.PROXY_FS,
        }
        return (
            Repository.objects.filter(
                organization_id=org_id,
                repo_type=repository_types[usage_key],
            )
            .exclude(status=Repository.Status.REMOVED)
            .count()
        )
    if usage_key == "protected_sources_count":
        from apps.protection.models.backup_config import BackupConfig

        return BackupConfig.objects.filter(organization_id=org_id).count()
    if usage_key == "storage_used_gb":
        from apps.storage.repositories.models import Repository

        repositories = Repository.objects.filter(organization_id=org_id).exclude(
            status=Repository.Status.REMOVED
        )
        return _storage_usage_gb(repositories)
    if usage_key == "public_gateway_capacity_used_bytes":
        from apps.lens_bridge.services.public_gateway_capacity import (
            org_public_gateway_used_bytes,
        )

        used_bytes, incomplete = org_public_gateway_used_bytes(organization_id=org_id)
        if incomplete:
            raise IncompleteUsageMeasurementError(
                "Public Gateway usage cannot be measured completely",
                measured_value=used_bytes,
            )
        return used_bytes
    if usage_key in {"ai_tokens_used", "ai_insights_used", "ai_requests_used"}:
        from django.db.models import Sum

        from apps.lens_bridge.models import LensUsageLedger

        return int(
            LensUsageLedger.objects.filter(organization_id=org_id)
            .aggregate(total=Sum("total_tokens"))
            .get("total")
            or 0
        )
    if usage_key == "alert_policies_count":
        from apps.alert.models import AlertPolicy

        return AlertPolicy.objects.filter(organization_id=org_id).count()
    raise ValueError(f"Unsupported quota usage meter: {usage_key}")


def collect_instance_usage_stats() -> dict[str, int | float]:
    """Read each instance meter directly instead of rescanning every organization."""
    totals = {
        key: collect_instance_meter_usage(usage_key=key) for key in _INSTANCE_USAGE_KEYS
    }
    totals["ai_insights_used"] = totals["ai_tokens_used"]
    totals["ai_requests_used"] = totals["ai_tokens_used"]
    return totals


def collect_instance_usage_stats_with_status() -> tuple[
    dict[str, int | float], set[str]
]:
    """Read instance meters while preserving incomplete measurements for reports."""
    totals: dict[str, int | float] = {}
    incomplete: set[str] = set()
    for key in _INSTANCE_USAGE_KEYS:
        try:
            totals[key] = collect_instance_meter_usage(usage_key=key)
        except IncompleteUsageMeasurementError as exc:
            totals[key] = exc.measured_value
            incomplete.add(key)
    totals["ai_insights_used"] = totals["ai_tokens_used"]
    totals["ai_requests_used"] = totals["ai_tokens_used"]
    return totals, incomplete


def _exclude_platform_organization(queryset, *, platform_org_key: str):
    """Exclude the internal organization without requiring a model relation."""
    platform_org_ids = Organization.objects.filter(key=platform_org_key).values("id")
    return queryset.exclude(organization_id__in=platform_org_ids)


def collect_instance_meter_usage(*, usage_key: str) -> float:
    """
    Sum a usage meter across all customer organizations (excludes platform org).

    Used to enforce the instance entitlement from live consumption. Organization
    plans and overrides are evaluated independently.
    """
    try:
        from apps.lens_bridge.services.platform_lens import PLATFORM_ORG_KEY
    except Exception:  # pragma: no cover
        PLATFORM_ORG_KEY = "__platform_lens__"

    if usage_key == "organizations_count":
        from apps.subscription.services.internal.organization_count import (
            count_customer_organizations,
        )

        return float(count_customer_organizations())
    if usage_key == "users_count":
        return float(
            _exclude_platform_organization(
                Membership.objects.all(),
                platform_org_key=PLATFORM_ORG_KEY,
            )
            .filter(is_active=True)
            .count()
        )
    if usage_key in {"nodes_count", "agents_count", "proxies_count", "gateways_count"}:
        from apps.node.models import Node
        from apps.node.models.base import NodeRole

        nodes = _exclude_platform_organization(
            Node.objects.all(),
            platform_org_key=PLATFORM_ORG_KEY,
        )
        roles = {
            "agents_count": NodeRole.AGENT,
            "proxies_count": NodeRole.PROXY,
            "gateways_count": NodeRole.GATEWAY,
        }
        return float(
            nodes.filter(role=roles[usage_key]).count()
            if usage_key in roles
            else nodes.count()
        )
    if usage_key == "source_nas_count":
        from apps.source.constants import ResourceStatus, ResourceType
        from apps.source.models import SourceResource

        return float(
            _exclude_platform_organization(
                SourceResource.objects.all(),
                platform_org_key=PLATFORM_ORG_KEY,
            )
            .filter(
                resource_type__in=(
                    ResourceType.NAS,
                    ResourceType.NFS,
                    ResourceType.CIFS,
                ),
            )
            .exclude(status__in=(ResourceStatus.REMOVING, ResourceStatus.REMOVED))
            .count()
        )
    if usage_key in {
        "object_storage_count",
        "target_nas_count",
        "standalone_disk_count",
    }:
        from apps.storage.repositories.models import Repository

        repository_types = {
            "object_storage_count": Repository.Type.S3,
            "target_nas_count": Repository.Type.NAS,
            "standalone_disk_count": Repository.Type.PROXY_FS,
        }
        return float(
            _exclude_platform_organization(
                Repository.objects.all(),
                platform_org_key=PLATFORM_ORG_KEY,
            )
            .filter(repo_type=repository_types[usage_key])
            .exclude(status=Repository.Status.REMOVED)
            .count()
        )
    if usage_key == "protected_sources_count":
        from apps.protection.models.backup_config import BackupConfig

        return float(
            _exclude_platform_organization(
                BackupConfig.objects.all(),
                platform_org_key=PLATFORM_ORG_KEY,
            ).count()
        )
    if usage_key == "storage_used_gb":
        from apps.storage.repositories.models import Repository

        repositories = _exclude_platform_organization(
            Repository.objects.all(),
            platform_org_key=PLATFORM_ORG_KEY,
        ).exclude(status=Repository.Status.REMOVED)
        return _storage_usage_gb(repositories)
    if usage_key == "public_gateway_capacity_used_bytes":
        from apps.lens_bridge.services.public_gateway_capacity import (
            bulk_public_gateway_used_bytes,
        )

        usage = bulk_public_gateway_used_bytes()
        if any(incomplete for _used, incomplete in usage.values()):
            measured_bytes = sum(used for used, _incomplete in usage.values())
            raise IncompleteUsageMeasurementError(
                "Public Gateway usage cannot be measured completely",
                measured_value=measured_bytes,
            )
        return sum(used for used, _incomplete in usage.values())
    if usage_key in {"ai_tokens_used", "ai_requests_used", "ai_insights_used"}:
        from django.db.models import Sum

        from apps.lens_bridge.models import LensUsageLedger

        return float(
            _exclude_platform_organization(
                LensUsageLedger.objects.all(),
                platform_org_key=PLATFORM_ORG_KEY,
            )
            .aggregate(total=Sum("total_tokens"))
            .get("total")
            or 0
        )
    if usage_key == "alert_policies_count":
        from apps.alert.models import AlertPolicy

        return float(
            _exclude_platform_organization(
                AlertPolicy.objects.all(),
                platform_org_key=PLATFORM_ORG_KEY,
            ).count()
        )
    raise ValueError(f"Unsupported instance quota usage meter: {usage_key}")


def collect_instance_node_pool_usage() -> int:
    """Sum agents + proxies across customer orgs (shared max_nodes pool)."""
    from apps.lens_bridge.services.platform_lens import PLATFORM_ORG_KEY
    from apps.node.models import Node
    from apps.node.models.base import NodeRole

    return int(
        _exclude_platform_organization(
            Node.objects.all(),
            platform_org_key=PLATFORM_ORG_KEY,
        )
        .filter(role__in=(NodeRole.AGENT, NodeRole.PROXY))
        .count()
    )


def collect_usage_stats_by_organization(
    *,
    organization_ids: list[int] | tuple[int, ...],
) -> tuple[dict[int, dict], dict[int, set[str]]]:
    """Collect organization quota meters with a fixed number of aggregate queries."""
    from django.db.models import Count, Q, Sum, Value
    from django.db.models.functions import Coalesce

    org_ids = sorted({int(value) for value in organization_ids})
    usage_by_org = {organization_id: _empty_usage() for organization_id in org_ids}
    incomplete_by_org: dict[int, set[str]] = {
        organization_id: set() for organization_id in org_ids
    }
    if not org_ids:
        return usage_by_org, incomplete_by_org

    for row in (
        Membership.objects.filter(organization_id__in=org_ids, is_active=True)
        .values("organization_id")
        .annotate(users_count=Count("id"))
    ):
        usage_by_org[int(row["organization_id"])]["users_count"] = int(
            row["users_count"] or 0
        )

    from apps.node.models import Node
    from apps.node.models.base import NodeRole

    for row in (
        Node.objects.filter(organization_id__in=org_ids)
        .values("organization_id")
        .annotate(
            nodes_count=Count("id"),
            agents_count=Count("id", filter=Q(role=NodeRole.AGENT)),
            proxies_count=Count("id", filter=Q(role=NodeRole.PROXY)),
            gateways_count=Count("id", filter=Q(role=NodeRole.GATEWAY)),
        )
    ):
        organization_usage = usage_by_org[int(row["organization_id"])]
        for key in ("nodes_count", "agents_count", "proxies_count", "gateways_count"):
            organization_usage[key] = int(row[key] or 0)

    from apps.source.constants import ResourceStatus, ResourceType
    from apps.source.models import SourceResource

    for row in (
        SourceResource.objects.filter(
            organization_id__in=org_ids,
            resource_type__in=(ResourceType.NAS, ResourceType.NFS, ResourceType.CIFS),
        )
        .exclude(status__in=(ResourceStatus.REMOVING, ResourceStatus.REMOVED))
        .values("organization_id")
        .annotate(source_nas_count=Count("id"))
    ):
        usage_by_org[int(row["organization_id"])]["source_nas_count"] = int(
            row["source_nas_count"] or 0
        )

    from apps.storage.repositories.models import Repository

    active_repositories = Repository.objects.filter(
        organization_id__in=org_ids,
    ).exclude(status=Repository.Status.REMOVED)
    for row in active_repositories.values("organization_id").annotate(
        object_storage_count=Count("id", filter=Q(repo_type=Repository.Type.S3)),
        target_nas_count=Count("id", filter=Q(repo_type=Repository.Type.NAS)),
        standalone_disk_count=Count(
            "id",
            filter=Q(repo_type=Repository.Type.PROXY_FS),
        ),
        storage_bytes=Sum(
            Coalesce(
                "physical_usage_bytes",
                "estimated_usage_bytes",
                Value(0),
            )
        ),
    ):
        organization_usage = usage_by_org[int(row["organization_id"])]
        for key in (
            "object_storage_count",
            "target_nas_count",
            "standalone_disk_count",
        ):
            organization_usage[key] = int(row[key] or 0)
        organization_usage["storage_used_gb"] = float(
            row["storage_bytes"] or 0
        ) / float(1024**3)
    incomplete_storage_org_ids = set(
        _storage_usage_unknown_repositories(active_repositories)
        .values_list("organization_id", flat=True)
        .distinct()
    )
    for organization_id in incomplete_storage_org_ids:
        incomplete_by_org[int(organization_id)].add("storage_used_gb")

    from apps.protection.models.backup_config import BackupConfig

    for row in (
        BackupConfig.objects.filter(organization_id__in=org_ids)
        .values("organization_id")
        .annotate(protected_sources_count=Count("id"))
    ):
        usage_by_org[int(row["organization_id"])]["protected_sources_count"] = int(
            row["protected_sources_count"] or 0
        )

    from apps.lens_bridge.models import LensUsageLedger

    for row in (
        LensUsageLedger.objects.filter(organization_id__in=org_ids)
        .values("organization_id")
        .annotate(ai_tokens_used=Sum("total_tokens"))
    ):
        organization_usage = usage_by_org[int(row["organization_id"])]
        tokens = int(row["ai_tokens_used"] or 0)
        organization_usage["ai_tokens_used"] = tokens
        organization_usage["ai_insights_used"] = tokens
        organization_usage["ai_requests_used"] = tokens

    from apps.alert.models import AlertPolicy

    for row in (
        AlertPolicy.objects.filter(organization_id__in=org_ids)
        .values("organization_id")
        .annotate(alert_policies_count=Count("id"))
    ):
        usage_by_org[int(row["organization_id"])]["alert_policies_count"] = int(
            row["alert_policies_count"] or 0
        )

    from apps.lens_bridge.services.public_gateway_capacity import (
        bulk_org_public_gateway_used_bytes,
    )

    public_usage = bulk_org_public_gateway_used_bytes(org_ids)
    for organization_id, (used_bytes, incomplete) in public_usage.items():
        usage_by_org[organization_id]["public_gateway_capacity_used_bytes"] = used_bytes
        if incomplete:
            incomplete_by_org[organization_id].add(
                "public_gateway_capacity_used_bytes"
            )

    return usage_by_org, incomplete_by_org
