from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.dateparse import parse_datetime
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgOperator, IsOrgReader
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


class BackupSourceSnapshotPagination(ProtectionPagination):
    max_page_size = 200


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
) -> dict[str, dict]:
    source_keys = {(snapshot.source_type, snapshot.source_ref_id) for snapshot in snapshots}
    backup_config_ids = {snapshot.backup_config_id for snapshot in snapshots}
    repository_ids = {snapshot.repository_id for snapshot in snapshots}

    source_names: dict[tuple[str, int], str] = {}
    agent_ids = [ref_id for source_type, ref_id in source_keys if source_type == "agent"]
    nas_ids = [ref_id for source_type, ref_id in source_keys if source_type == "nas"]
    if agent_ids:
        for row in Node.objects.filter(
            organization_id=organization_id,
            id__in=agent_ids,
        ).values("id", "name"):
            source_names[("agent", int(row["id"]))] = str(row["name"] or "")
    if nas_ids:
        for row in SourceResource.objects.filter(
            organization_id=organization_id,
            id__in=nas_ids,
        ).values("id", "name"):
            source_names[("nas", int(row["id"]))] = str(row["name"] or "")

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
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), IsOrgReader()]
        return super().get_permissions()

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
        return filter_backup_source_snapshots(
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

    def get_object(self):
        org = require_org(self.request)
        snapshot = get_backup_source_snapshot(
            organization_id=org.id,
            snapshot_id=int(self.kwargs["pk"]),
        )
        if snapshot is None:
            raise NotFound("backup source snapshot not found")
        return snapshot

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        include_directory_snapshots = _truthy_query_param(
            request.query_params.get("include_directory_snapshots")
        )
        if page is not None:
            page_rows = list(page)
            serializer = BackupSourceSnapshotListSerializer(
                page_rows,
                many=True,
                context=_snapshot_context(
                    organization_id=require_org(request).id,
                    snapshots=page_rows,
                    include_directory_snapshots=include_directory_snapshots,
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
