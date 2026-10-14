from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import JsonResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.iam.resource_api import GovernedResourceMixin
from apps.iam.resource_access import record_resource_owner
from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgOperator, IsOrgReader
from apps.protection.api.pagination import ProtectionPagination
from apps.protection.api.serializers import (
    BackupPolicySerializer,
    BackupPolicyWriteSerializer,
    BulkDeleteSerializer,
    BulkStateSerializer,
)
from apps.protection.models import BackupPolicy
from apps.protection.selectors import (
    backup_policies_queryset,
    filter_backup_policies,
    get_backup_policy,
)
from apps.protection import services
from apps.protection.api.execution_access import assert_scheduled_policy_write


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Resource is in use."
    default_code = "conflict"


def health(_request):
    return JsonResponse({"app": "protection", "status": "ok"})


def _validation_error(exc: DjangoValidationError) -> ValidationError:
    if hasattr(exc, "message_dict"):
        return ValidationError(exc.message_dict)
    return ValidationError({"detail": exc.messages})


class _BackupPolicyViewSetBase(viewsets.ModelViewSet):
    governance_resource_type = "backup_policy"
    governance_manage_action = "protection.manage"
    serializer_class = BackupPolicySerializer
    permission_classes = [IsAuthenticated, IsOrgOperator]
    pagination_class = ProtectionPagination
    queryset = BackupPolicy.objects.none()

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), IsOrgReader()]
        return super().get_permissions()

    def get_queryset(self):
        org = require_org(self.request)
        params = self.request.query_params
        return filter_backup_policies(
            backup_policies_queryset(organization_id=org.id),
            search=params.get("search") or None,
            search_field=params.get("search_field") or None,
            is_active=params.get("is_active") or None,
            ordering=params.get("ordering") or None,
        )

    def get_object(self):
        org = require_org(self.request)
        policy = get_backup_policy(organization_id=org.id, policy_id=int(self.kwargs["pk"]))
        if policy is None:
            raise NotFound("backup policy not found")
        return policy

    def create(self, request, *args, **kwargs):
        org = require_org(request)
        serializer = BackupPolicyWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            policy = services.create_backup_policy(
                organization_id=org.id,
                data=serializer.validated_data,
            )
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        record_resource_owner(
            organization_id=org.id, resource_type="backup_policy",
            resource_id=policy.id, owner_id=request.user.id,
        )
        return Response(
            BackupPolicySerializer(policy).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        policy = self.get_object()
        serializer = BackupPolicyWriteSerializer(data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        assert_scheduled_policy_write(request, policy, serializer.validated_data)
        try:
            policy = services.update_backup_policy(
                policy=policy,
                data=serializer.validated_data,
            )
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        return Response(BackupPolicySerializer(policy).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        policy = self.get_object()
        try:
            result = services.delete_backup_policy(policy=policy)
        except services.ResourceInUseError as exc:
            raise Conflict(str(exc)) from exc
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-state")
    def bulk_state(self, request):
        org = require_org(request)
        serializer = BulkStateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data["is_active"]:
            for policy in BackupPolicy.objects.filter(
                organization_id=org.id, pk__in=serializer.validated_data["ids"]
            ):
                assert_scheduled_policy_write(request, policy, {"is_active": True})
        try:
            result = services.bulk_set_backup_policy_state(
                organization_id=org.id,
                ids=serializer.validated_data["ids"],
                is_active=serializer.validated_data["is_active"],
            )
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        return Response(result)

    @action(detail=False, methods=["post"], url_path="bulk-delete")
    def bulk_delete(self, request):
        org = require_org(request)
        serializer = BulkDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = services.bulk_delete_backup_policies(
                organization_id=org.id,
                ids=serializer.validated_data["ids"],
            )
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        return Response(result)


class BackupPolicyViewSet(GovernedResourceMixin, _BackupPolicyViewSetBase):
    """Resource-aware adapter over the unchanged business implementation."""

    governance_resource_type = "backup_policy"
    governance_manage_action = "protection.manage"
