"""Authorization for retained reports, inherited from backup configurations."""

from django.db.models import F, QuerySet
from rest_framework import permissions

from apps.iam.permissions_org import IsOrgStaffReader, IsOrgWriter, get_membership
from apps.iam.query_authorization import request_authorization
from apps.protection.models import BackupConfig, BackupSourceSnapshotDirectory
from common.extension_spi import get_authz_provider


def report_action(request) -> str:
    """Resolve only report-record operations, never scanning or Chat execution."""
    verb = (
        "view" if request.method in permissions.SAFE_METHODS
        else "delete" if request.method == "DELETE" else "manage"
    )
    return f"insights.reports.{verb}"


class ReportPermission(permissions.BasePermission):
    """Capability gate; the query separately enforces each configuration scope."""

    def has_permission(self, request, view) -> bool:
        if get_authz_provider() is None:
            legacy = IsOrgStaffReader if request.method in permissions.SAFE_METHODS else IsOrgWriter
            return legacy().has_permission(request, view)
        if get_membership(request) is None:
            return False
        if getattr(request, "hfl_support_readonly", False):
            return request.method in permissions.SAFE_METHODS
        context = request_authorization(
            request, fresh=request.method not in permissions.SAFE_METHODS,
        )
        return bool(context and context.has_action("backup_config", report_action(request)))


def authorized_directories(request, organization_id: int) -> QuerySet:
    """Filter lineage and both capabilities before any count or page is taken."""
    directories = BackupSourceSnapshotDirectory.objects.filter(
        organization_id=organization_id,
        source_snapshot__organization_id=organization_id,
        source_snapshot__backup_config_id=F("backup_config_id"),
    ).exclude(status="deleted").exclude(source_snapshot__status="deleted")
    if get_authz_provider() is None or (
        getattr(request, "hfl_support_readonly", False)
        and request.method in permissions.SAFE_METHODS
    ):
        return directories
    context = request_authorization(
        request, fresh=request.method not in permissions.SAFE_METHODS,
    )
    if context is None or not context.valid:
        return directories.none()
    configs = context.filter_queryset(
        BackupConfig.objects.filter(organization_id=organization_id),
        "backup_config", action=report_action(request),
    )
    # Each action must apply to this same configuration. An unrelated Group's
    # Browse permission cannot authorize the report's actual backup data.
    configs = context.filter_queryset(
        configs, "backup_config", action="protection.backup_data.browse",
    )
    return context.fence_queryset(directories.filter(
        backup_config_id__in=configs.values("pk"),
    ))


def filter_reports(request, queryset, organization_id: int) -> QuerySet:
    return queryset.filter(
        organization_id=organization_id,
        snapshot_id__in=authorized_directories(request, organization_id).values("pk"),
    )
