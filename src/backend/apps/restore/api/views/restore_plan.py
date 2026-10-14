from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated
from rest_framework.response import Response

from apps.iam.resource_api import GovernedResourceMixin
from apps.iam.permission_catalog import resource_action_permission
from apps.iam.org_context import require_org
from apps.iam.resource_access import (
    assert_resource_access,
    filter_resource_queryset,
    record_resource_owner,
)
from apps.protection.models import BackupSourceSnapshot
from apps.protection.api.execution_access import assert_backup_config_execution
from apps.iam.permissions_org import IsOrgOperator, IsOrgReader
from apps.restore.api.pagination import RestorePagination
from apps.restore.api.serializers import (
    RestorePlanBatchRunSerializer,
    RestorePlanPatchSerializer,
    RestorePlanRunSerializer,
    RestorePlanSerializer,
    RestorePlanSourceRunSerializer,
    RestorePlanWriteSerializer,
)
from apps.restore.models import RestorePlan, RestoreRecord
from apps.restore.selectors.interface import (
    filter_restore_plans,
    get_restore_plan,
    restore_plans_queryset,
)
from apps.restore.services import interface as restore_services
from common.extension_spi import get_authz_provider


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


def _source_run_result(records: list[RestoreRecord]) -> dict:
    return {
        "status": "pending",
        "record_count": len(records),
        "task_count": len(records),
        "records": [_record_result(record) for record in records],
    }


def _assert_endpoint_access(
    request, endpoint_type: str, endpoint_id: int, *, action: str = "resource.view"
) -> None:
    assert_resource_access(
        request,
        "node" if endpoint_type == RestorePlan.EndpointType.AGENT else "source_resource",
        endpoint_id,
        action=action,
    )


def _assert_snapshot_access(
    request, snapshot_id: int | None, *, action: str = "protection.restore_plans.run"
) -> None:
    if not snapshot_id:
        return
    config_id = BackupSourceSnapshot.objects.filter(
        organization_id=require_org(request).id, pk=snapshot_id,
    ).values_list("backup_config_id", flat=True).first()
    if config_id:
        assert_resource_access(request, "backup_config", config_id, action=action)
    elif get_authz_provider() is not None:
        raise NotFound("Snapshot not found.")


def _assert_plan_execution(request, plan: RestorePlan) -> None:
    action_name = "protection.restore_plans.run"
    assert_resource_access(request, "restore_plan", plan.pk, action=action_name)
    assert_backup_config_execution(request, plan.backup_config_id, action=action_name)
    _assert_endpoint_access(
        request, plan.source_type, plan.source_ref_id, action=action_name
    )
    _assert_endpoint_access(
        request, plan.target_type, plan.target_ref_id, action=action_name
    )


class _RestorePlanViewSetBase(viewsets.ModelViewSet):
    governance_resource_type = "restore_plan"
    governance_manage_action = "protection.manage"
    serializer_class = RestorePlanSerializer
    permission_classes = [IsAuthenticated, IsOrgOperator]
    pagination_class = RestorePagination
    queryset = RestorePlan.objects.none()

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated(), IsOrgReader()]
        return super().get_permissions()

    def get_queryset(self):
        org = require_org(self.request)
        params = self.request.query_params
        backup_config_id = params.get("backup_config_id")
        source_ref_id = params.get("source_ref_id")
        enabled = params.get("enabled")
        enabled_bool = None
        if enabled not in (None, ""):
            enabled_bool = str(enabled).lower() in {"1", "true", "yes"}
        queryset = filter_restore_plans(
            restore_plans_queryset(organization_id=org.id),
            backup_config_id=int(backup_config_id) if backup_config_id else None,
            source_type=params.get("source_type") or None,
            source_ref_id=int(source_ref_id) if source_ref_id else None,
            enabled=enabled_bool,
        )
        return filter_resource_queryset(
            self.request,
            queryset,
            "restore_plan",
            action=self._resource_action(),
        )

    def get_object(self):
        org = require_org(self.request)
        plan = get_restore_plan(organization_id=org.id, plan_id=int(self.kwargs["pk"]))
        if plan is None:
            raise NotFound("restore plan not found")
        assert_resource_access(
            self.request,
            "restore_plan",
            plan.id,
            action=self._resource_action(),
        )
        return plan

    def create(self, request, *args, **kwargs):
        org = require_org(request)
        serializer = RestorePlanWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        assert_resource_access(
            request,
            "backup_config",
            data["backup_config_id"],
            action="resource.view",
        )
        _assert_endpoint_access(request, data["source_type"], data["source_ref_id"])
        _assert_endpoint_access(request, data["target_type"], data["target_ref_id"])
        try:
            plan = restore_services.create_restore_plan(
                organization_id=org.id,
                data=serializer.validated_data,
            )
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        record_resource_owner(
            organization_id=org.id, resource_type="restore_plan", resource_id=plan.id,
            owner_id=request.user.id,
        )
        return Response(RestorePlanSerializer(plan).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        plan = self.get_object()
        serializer = RestorePlanPatchSerializer(data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        assert_resource_access(
            request,
            "backup_config",
            data.get("backup_config_id", plan.backup_config_id),
            action="resource.view",
        )
        _assert_endpoint_access(
            request,
            data.get("source_type", plan.source_type),
            data.get("source_ref_id", plan.source_ref_id),
        )
        _assert_endpoint_access(
            request,
            data.get("target_type", plan.target_type),
            data.get("target_ref_id", plan.target_ref_id),
        )
        try:
            plan = restore_services.update_restore_plan(plan=plan, data=serializer.validated_data)
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        return Response(RestorePlanSerializer(plan).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        plan = self.get_object()
        return Response(restore_services.delete_restore_plan(plan=plan), status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="run")
    def run(self, request, pk=None):
        org = require_org(request)
        plan = self.get_object()
        _assert_plan_execution(request, plan)
        serializer = RestorePlanRunSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _assert_snapshot_access(
            request,
            serializer.validated_data.get("source_snapshot_id"),
        )
        try:
            record = restore_services.run_restore_plan(
                organization_id=org.id,
                plan=plan,
                user_id=getattr(request.user, "id", None),
                idempotency_key=serializer.validated_data.get("idempotency_key") or None,
                source_snapshot_id=serializer.validated_data.get("source_snapshot_id"),
            )
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        return Response(_record_result(record), status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="run-batch")
    def run_batch(self, request):
        org = require_org(request)
        serializer = RestorePlanBatchRunSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assert_backup_config_execution(
            request, serializer.validated_data["backup_config_id"],
            action="protection.restore_plans.run",
        )
        _assert_snapshot_access(
            request,
            serializer.validated_data.get("source_snapshot_id"),
        )
        _assert_endpoint_access(
            request,
            serializer.validated_data["target_type"],
            serializer.validated_data["target_ref_id"],
            action="protection.restore_plans.run",
        )
        try:
            record = restore_services.run_restore_plan_batch(
                organization_id=org.id,
                data=serializer.validated_data,
                user_id=getattr(request.user, "id", None),
            )
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        return Response(_record_result(record), status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="run-source")
    def run_source(self, request):
        org = require_org(request)
        serializer = RestorePlanSourceRunSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _assert_endpoint_access(
            request,
            serializer.validated_data["source_type"],
            serializer.validated_data["source_ref_id"],
            action="protection.restore_plans.run",
        )
        plans = RestorePlan.objects.filter(
            organization_id=org.id,
            source_type=serializer.validated_data["source_type"],
            source_ref_id=serializer.validated_data["source_ref_id"],
            enabled=True,
        )
        for plan in plans:
            _assert_plan_execution(request, plan)
        _assert_snapshot_access(
            request,
            serializer.validated_data.get("source_snapshot_id"),
        )
        try:
            records = restore_services.run_restore_plans_for_source(
                organization_id=org.id,
                source_type=serializer.validated_data["source_type"],
                source_ref_id=serializer.validated_data["source_ref_id"],
                user_id=getattr(request.user, "id", None),
                idempotency_key=serializer.validated_data.get("idempotency_key") or None,
                source_snapshot_id=serializer.validated_data.get("source_snapshot_id"),
            )
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        return Response(_source_run_result(records), status=status.HTTP_201_CREATED)


class RestorePlanViewSet(GovernedResourceMixin, _RestorePlanViewSetBase):
    """Resource-aware adapter over the unchanged business implementation."""

    governance_resource_type = "restore_plan"
    governance_manage_action = "protection.manage"

    def _resource_action(self) -> str:
        # Keep the same Restore Plan intent at the coarse entry gate, direct
        # scope check and inherited parent scope check.
        action = super()._resource_action()
        return resource_action_permission("restore_plan", action) or action
