from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated
from rest_framework.response import Response

from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgOperator, IsOrgReader
from apps.protection.models import BackupSourceSnapshot
from apps.protection.services.progress.restore_runtime import (
    build_restore_kopia_progress,
    sync_restore_task_progress,
)
from apps.restore.api.pagination import RestorePagination
from apps.restore.api.serializers import (
    RestoreRecordCreateSerializer,
    RestoreRecordSerializer,
)
from apps.restore.models import RestoreRecord
from apps.restore.selectors.interface import (
    filter_restore_records,
    get_restore_record,
    restore_records_queryset,
)
from apps.restore.services import interface as restore_services
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.source.services.internal.nas_share_path import share_root_label
from apps.task.models import Task


RESTORE_RECORD_SEARCH_FIELDS = {"restore_uid", "snapshot_uid", "task_uuid"}


def _datetime_query_param(value: str | None, field_name: str):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValidationError({field_name: "Must be a valid ISO 8601 datetime."})
    return parsed


def _restore_record_search_fields(value: str | None) -> list[str]:
    fields = [
        item.strip() for item in str(value or "restore_uid").split(",") if item.strip()
    ]
    invalid = sorted(set(fields) - RESTORE_RECORD_SEARCH_FIELDS)
    if invalid:
        raise ValidationError(
            {"search_fields": f"Unsupported search fields: {', '.join(invalid)}."}
        )
    if not fields:
        raise ValidationError({"search_fields": "Select at least one search field."})
    return fields


def _validation_error(exc: DjangoValidationError) -> ValidationError:
    if hasattr(exc, "message_dict"):
        return ValidationError(exc.message_dict)
    return ValidationError({"detail": exc.messages})


def _record_result(record: RestoreRecord) -> dict:
    return {
        "restore_record_id": record.id,
        "restore_uid": record.restore_uid,
        "task_id": record.task_id,
        "task_uuid": record.task_uuid,
        "status": "pending",
        "source_snapshot_id": record.source_snapshot_id,
        "item_count": record.items.count(),
        "items": [],
    }


class RestoreRecordViewSet(viewsets.ModelViewSet):
    serializer_class = RestoreRecordSerializer
    permission_classes = [IsAuthenticated, IsOrgOperator]
    pagination_class = RestorePagination
    queryset = RestoreRecord.objects.none()
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated(), IsOrgReader()]
        return super().get_permissions()

    def get_queryset(self):
        org = require_org(self.request)
        params = self.request.query_params
        source_ref_id = params.get("source_ref_id")
        task_status = params.get("status") or None
        if task_status and task_status not in Task.Status.values:
            raise ValidationError({"status": "Unsupported task status."})
        source_mode = params.get("source_mode") or None
        if source_mode and source_mode not in RestoreRecord.SourceMode.values:
            raise ValidationError({"source_mode": "Unsupported restore configuration."})
        return filter_restore_records(
            restore_records_queryset(organization_id=org.id),
            organization_id=org.id,
            source_type=params.get("source_type") or None,
            source_ref_id=int(source_ref_id) if source_ref_id else None,
            task_uuid=params.get("task_uuid") or None,
            search=params.get("search") or None,
            search_fields=_restore_record_search_fields(params.get("search_fields")),
            status=task_status,
            source_mode=source_mode,
            created_from=_datetime_query_param(
                params.get("created_from"), "created_from"
            ),
            created_to=_datetime_query_param(params.get("created_to"), "created_to"),
        )

    def get_object(self):
        org = require_org(self.request)
        record = get_restore_record(organization_id=org.id, record_id=int(self.kwargs["pk"]))
        if record is None:
            raise NotFound("restore record not found")
        return record

    @staticmethod
    def _task_by_uuid(records) -> dict[str, Task]:
        records = list(records)
        if not records:
            return {}
        organization_id = records[0].organization_id
        task_uuids = [record.task_uuid for record in records if record.task_uuid]
        tasks = Task.objects.filter(
            organization_id=organization_id,
            task_uuid__in=task_uuids,
        ).only("task_uuid", "status", "progress", "started_at", "finished_at")
        return {str(task.task_uuid): task for task in tasks}

    @staticmethod
    def _snapshot_uid_by_id(records) -> dict[int, str]:
        records = list(records)
        if not records:
            return {}
        organization_id = records[0].organization_id
        snapshot_ids = {
            record.source_snapshot_id for record in records if record.source_snapshot_id
        }
        rows = BackupSourceSnapshot.objects.filter(
            organization_id=organization_id,
            id__in=snapshot_ids,
        ).values_list("id", "snapshot_uid")
        return {int(snapshot_id): str(snapshot_uid) for snapshot_id, snapshot_uid in rows}

    @staticmethod
    def _nas_share_root_by_record_id(records) -> dict[int, str]:
        records = list(records)
        if not records:
            return {}
        organization_id = records[0].organization_id
        nas_ids = {record.target_ref_id for record in records if record.target_type == RestoreRecord.EndpointType.NAS}
        resources = SourceResource.objects.filter(
            organization_id=organization_id,
            resource_type=ResourceType.NAS,
            id__in=nas_ids,
            is_deleted=False,
        )
        roots = {
            resource.id: share_root_label(
                resource_type=resource.resource_type,
                config=resource.config if isinstance(resource.config, dict) else {},
            )
            for resource in resources
        }
        return {
            record.id: roots.get(record.target_ref_id, "")
            for record in records
            if record.target_type == RestoreRecord.EndpointType.NAS
        }

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        records = list(page if page is not None else queryset)
        context = self.get_serializer_context()
        context["task_by_uuid"] = self._task_by_uuid(records)
        context["snapshot_uid_by_id"] = self._snapshot_uid_by_id(records)
        context["nas_share_root_by_record_id"] = self._nas_share_root_by_record_id(records)
        serializer = self.get_serializer(records, many=True, context=context)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        record = self.get_object()
        context = self.get_serializer_context()
        context["task_by_uuid"] = self._task_by_uuid([record])
        context["snapshot_uid_by_id"] = self._snapshot_uid_by_id([record])
        context["nas_share_root_by_record_id"] = self._nas_share_root_by_record_id([record])
        serializer = self.get_serializer(record, context=context)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        org = require_org(request)
        serializer = RestoreRecordCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            record = restore_services.create_manual_restore_record(
                organization_id=org.id,
                data=serializer.validated_data,
                user_id=getattr(request.user, "id", None),
            )
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        return Response(_record_result(record), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="runtime")
    def runtime(self, request, pk=None):
        record = self.get_object()
        task = Task.objects.filter(
            organization_id=record.organization_id,
            task_uuid=record.task_uuid,
        ).first()
        if task is not None and task.status in {Task.Status.PENDING, Task.Status.RUNNING}:
            payload = sync_restore_task_progress(record=record, task=task)
        else:
            payload = build_restore_kopia_progress(record=record, task=task)
            if task is not None:
                result_payload = task.result_payload if isinstance(task.result_payload, dict) else {}
                transfer = result_payload.get("transfer_progress")
                if isinstance(transfer, dict):
                    derived = payload.get("transfer_progress")
                    payload["transfer_progress"] = {
                        **transfer,
                        **(derived if isinstance(derived, dict) else {}),
                    }
        progress = float(task.progress or 0) if task is not None else 0.0
        return Response({
            "progress": progress,
            "transfer_progress": payload.get("transfer_progress"),
            "kopia_progress": payload,
        })
