from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Exists, OuterRef, Q
from django.utils.dateparse import parse_datetime
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.iam.org_context import require_org
from apps.iam.resource_access import assert_resource_access, filter_resource_queryset, visible_resource_refs
from apps.iam.permissions_org import IsOrgOperator, IsOrgReader, IsOrgStaffReader
from apps.node.models import Node
from apps.protection.api.pagination import ProtectionPagination
from apps.protection.api.serializers.backup_source_snapshot import (
    BackupSourceSnapshotDetailSerializer,
    BackupSourceSnapshotListSerializer,
)
from apps.protection.models import BackupConfig, BackupSourceSnapshot
from apps.protection.selectors.backup_source_snapshot import (
    backup_source_snapshots_queryset,
    filter_backup_source_snapshots,
    get_backup_source_snapshot,
)
from apps.protection.services.snapshot_delete import create_and_queue_snapshot_delete_task
from apps.source.models import SourceResource
from apps.storage.repositories.models import Repository
from common.extension_spi import get_authz_provider


class BackupSourceSnapshotPagination(ProtectionPagination):
    max_page_size = 200


def picker_source_queryset(*, organization_id: int, search: str = ""):
    """One row per source/config with at least one usable snapshot."""
    available = BackupSourceSnapshot.objects.filter(
        organization_id=organization_id,
        backup_config_id=OuterRef("pk"),
        status=BackupSourceSnapshot.Status.AVAILABLE,
        deleted_at__isnull=True,
    )
    queryset = BackupConfig.objects.filter(organization_id=organization_id).filter(
        Exists(available)
    )
    query = search.strip()
    if query:
        agent_ids = Node.objects.filter(
            organization_id=organization_id
        ).filter(Q(name__icontains=query) | Q(ip_address__icontains=query)).values("id")
        nas_ids = SourceResource.objects.filter(
            organization_id=organization_id
        ).filter(Q(name__icontains=query) | Q(config__server__icontains=query)).values("id")
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(source_type="agent", source_ref_id__in=agent_ids)
            | Q(source_type="nas", source_ref_id__in=nas_ids)
        )
    return queryset.order_by("name", "id")


def picker_source_data(*, organization_id: int, configs: list[BackupConfig]) -> list[dict]:
    agent_ids = [row.source_ref_id for row in configs if row.source_type == "agent"]
    nas_ids = [row.source_ref_id for row in configs if row.source_type == "nas"]
    agents = {
        row["id"]: row
        for row in Node.objects.filter(organization_id=organization_id, id__in=agent_ids)
        .values("id", "name", "ip_address")
    }
    nas = {
        row["id"]: row
        for row in SourceResource.objects.filter(organization_id=organization_id, id__in=nas_ids)
        .values("id", "name", "config")
    }
    result = []
    for config in configs:
        source = (agents if config.source_type == "agent" else nas).get(config.source_ref_id, {})
        nas_config = source.get("config")
        server = nas_config.get("server") if isinstance(nas_config, dict) else None
        address = source.get("ip_address") if config.source_type == "agent" else server
        result.append({
            "backup_config_id": config.id,
            "source_type": config.source_type,
            "source_ref_id": config.source_ref_id,
            "source_display_name": source.get("name") or config.name,
            "source_address": address.strip() if isinstance(address, str) else "",
        })
    return result


def _int_query_param(value: str | None, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({field_name: "Must be an integer."}) from exc


def _csv_query_param(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _truthy_query_param(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _datetime_query_param(value: str | None, field_name: str):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValidationError({field_name: "Must be a valid ISO 8601 datetime."})
    return parsed


def _snapshot_context(
    *,
    organization_id: int,
    snapshots: list[BackupSourceSnapshot],
    include_directory_snapshots: bool = False,
    include_source_addresses: bool = True,
) -> dict[str, dict]:
    source_keys = {(snapshot.source_type, snapshot.source_ref_id) for snapshot in snapshots}
    backup_config_ids = {snapshot.backup_config_id for snapshot in snapshots}
    repository_ids = {snapshot.repository_id for snapshot in snapshots}

    source_names: dict[tuple[str, int], str] = {}
    source_addresses: dict[tuple[str, int], str] = {}
    agent_ids = [ref_id for source_type, ref_id in source_keys if source_type == "agent"]
    nas_ids = [ref_id for source_type, ref_id in source_keys if source_type == "nas"]
    if agent_ids:
        for row in Node.objects.filter(
            organization_id=organization_id,
            id__in=agent_ids,
        ).values("id", "name", "ip_address"):
            key = ("agent", int(row["id"]))
            source_names[key] = str(row["name"] or "")
            if include_source_addresses:
                source_addresses[key] = str(row["ip_address"] or "").strip()
    if nas_ids:
        for row in SourceResource.objects.filter(
            organization_id=organization_id,
            id__in=nas_ids,
        ).values("id", "name", "config"):
            key = ("nas", int(row["id"]))
            source_names[key] = str(row["name"] or "")
            if include_source_addresses:
                config = row["config"] if isinstance(row["config"], dict) else {}
                server = config.get("server")
                source_addresses[key] = server.strip() if isinstance(server, str) else ""

    backup_config_names = {
        int(row["id"]): str(row["name"] or "")
        for row in BackupConfig.objects.filter(
            organization_id=organization_id,
            id__in=backup_config_ids,
        ).values("id", "name")
    }
    repository_names = {
        int(row["id"]): str(row["name"] or "")
        for row in Repository.objects.filter(
            organization_id=organization_id,
            id__in=repository_ids,
        ).values("id", "name")
    }
    return {
        "source_names": source_names,
        "source_addresses": source_addresses,
        "backup_config_names": backup_config_names,
        "repository_names": repository_names,
        "include_directory_snapshots": include_directory_snapshots,
    }


class BackupSourceSnapshotViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated, IsOrgOperator]
    pagination_class = BackupSourceSnapshotPagination
    queryset = BackupSourceSnapshot.objects.none()
    serializer_class = BackupSourceSnapshotListSerializer

    def get_permissions(self):
        if self.action == "picker_sources":
            return [IsAuthenticated(), IsOrgStaffReader()]
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), IsOrgReader()]
        return super().get_permissions()

    @action(detail=False, methods=["get"], url_path="picker-sources")
    def picker_sources(self, request):
        org = require_org(request)
        queryset = picker_source_queryset(
            organization_id=org.id,
            search=request.query_params.get("search") or "",
        )
        if get_authz_provider() is not None:
            queryset = filter_resource_queryset(request, queryset, "backup_config")
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(
                picker_source_data(organization_id=org.id, configs=list(page))
            )
        return Response(picker_source_data(organization_id=org.id, configs=list(queryset)))

    def get_queryset(self):
        org = require_org(self.request)
        params = self.request.query_params
        requested_statuses = _csv_query_param(params.get("status"))
        invalid_statuses = sorted(set(requested_statuses) - set(BackupSourceSnapshot.Status.values))
        if invalid_statuses:
            raise ValidationError({"status": f"Unsupported snapshot statuses: {', '.join(invalid_statuses)}."})
        requested_status = requested_statuses[0] if len(requested_statuses) == 1 else None
        source_ref_id = _int_query_param(params.get("source_ref_id"), "source_ref_id")
        backup_config_id = _int_query_param(params.get("backup_config_id"), "backup_config_id")
        repository_id = _int_query_param(params.get("repository_id"), "repository_id")
        created_from = parse_datetime(params.get("created_from", "")) if params.get("created_from") else None
        created_to = parse_datetime(params.get("created_to", "")) if params.get("created_to") else None
        started_from = _datetime_query_param(params.get("started_from"), "started_from")
        started_to = _datetime_query_param(params.get("started_to"), "started_to")
        exclude_statuses = _csv_query_param(params.get("exclude_status"))
        queryset = filter_backup_source_snapshots(
            backup_source_snapshots_queryset(
                organization_id=org.id,
                include_deleted=bool(
                    set(requested_statuses)
                    & {
                        BackupSourceSnapshot.Status.DELETING,
                        BackupSourceSnapshot.Status.DELETE_FAILED,
                        BackupSourceSnapshot.Status.DELETED,
                    }
                ),
            ),
            organization_id=org.id,
            source_type=params.get("source_type") or None,
            source_ref_id=source_ref_id,
            backup_config_id=backup_config_id,
            repository_id=repository_id,
            status=requested_status,
            statuses=requested_statuses,
            exclude_statuses=exclude_statuses,
            created_from=created_from,
            created_to=created_to,
            started_from=started_from,
            started_to=started_to,
            snapshot_uid=params.get("snapshot_uid") or None,
            search=params.get("search") or None,
            ordering=params.get("ordering") or None,
        )
        snapshot_id = _int_query_param(params.get("snapshot_id"), "snapshot_id")
        if snapshot_id is not None:
            queryset = queryset.filter(id=snapshot_id)
        if get_authz_provider() is not None:
            config_ids = list(queryset.order_by().values_list("backup_config_id", flat=True).distinct())
            visible = visible_resource_refs(
                self.request,
                [("backup_config", int(config_id)) for config_id in config_ids],
            )
            if visible is not None:
                allowed_config_ids = {
                    resource_id
                    for resource_type, resource_id in visible
                    if resource_type == "backup_config"
                }
                queryset = queryset.filter(backup_config_id__in=allowed_config_ids)
        return queryset

    def get_object(self):
        org = require_org(self.request)
        snapshot = get_backup_source_snapshot(
            organization_id=org.id,
            snapshot_id=int(self.kwargs["pk"]),
        )
        if snapshot is None:
            raise NotFound("backup source snapshot not found")
        assert_resource_access(
            self.request,
            "backup_config",
            snapshot.backup_config_id,
            action=("resources.manage" if self.action == "destroy" else "resources.view"),
        )
        return snapshot

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        include_directory_snapshots = _truthy_query_param(
            request.query_params.get("include_directory_snapshots")
        )
        include_source_addresses = IsOrgStaffReader().has_permission(request, self)
        if page is not None:
            page_rows = list(page)
            serializer = BackupSourceSnapshotListSerializer(
                page_rows,
                many=True,
                context=_snapshot_context(
                    organization_id=require_org(request).id,
                    snapshots=page_rows,
                    include_directory_snapshots=include_directory_snapshots,
                    include_source_addresses=include_source_addresses,
                ),
            )
            return self.get_paginated_response(serializer.data)
        rows = list(queryset)
        serializer = BackupSourceSnapshotListSerializer(
            rows,
            many=True,
            context=_snapshot_context(
                organization_id=require_org(request).id,
                snapshots=rows,
                include_directory_snapshots=include_directory_snapshots,
                include_source_addresses=include_source_addresses,
            ),
        )
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        snapshot = self.get_object()
        serializer = BackupSourceSnapshotDetailSerializer(
            snapshot,
            context=_snapshot_context(
                organization_id=require_org(request).id,
                snapshots=[snapshot],
                include_source_addresses=IsOrgStaffReader().has_permission(request, self),
            ),
        )
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        snapshot = self.get_object()
        try:
            task = create_and_queue_snapshot_delete_task(source_snapshot=snapshot)
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            raise ValidationError(detail=detail) from exc
        return Response(
            {
                "deleted": False,
                "id": snapshot.id,
                "task_id": task.id,
                "task_uuid": str(task.task_uuid),
            },
            status=status.HTTP_202_ACCEPTED,
        )
