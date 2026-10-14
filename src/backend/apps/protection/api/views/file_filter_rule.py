from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.iam.resource_api import GovernedResourceMixin
from apps.iam.resource_access import record_resource_owner
from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgStaffReader, IsOrgWriter
from apps.protection import services
from apps.protection.api.pagination import ProtectionPagination
from apps.protection.api.serializers import (
    BulkDeleteSerializer,
    BulkStateSerializer,
    FileFilterRuleSerializer,
    FileFilterRuleWriteSerializer,
)
from apps.protection.api.views.policy import Conflict, _validation_error
from apps.protection.models import FileFilterRule
from apps.protection.selectors import (
    file_filter_rules_queryset,
    filter_file_filter_rules,
    get_file_filter_rule,
)


class _FileFilterRuleViewSetBase(viewsets.ModelViewSet):
    governance_resource_type = "file_filter_rule"
    governance_manage_action = "protection.manage"
    serializer_class = FileFilterRuleSerializer
    permission_classes = [IsAuthenticated, IsOrgWriter]
    pagination_class = ProtectionPagination
    queryset = FileFilterRule.objects.none()

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), IsOrgStaffReader()]
        return super().get_permissions()

    def get_queryset(self):
        org = require_org(self.request)
        params = self.request.query_params
        return filter_file_filter_rules(
            file_filter_rules_queryset(organization_id=org.id),
            search=params.get("search") or None,
            search_field=params.get("search_field") or None,
            is_active=params.get("is_active") or None,
            ordering=params.get("ordering") or None,
        )

    def get_object(self):
        org = require_org(self.request)
        rule = get_file_filter_rule(organization_id=org.id, rule_id=int(self.kwargs["pk"]))
        if rule is None:
            raise NotFound("file filter rule not found")
        return rule

    def create(self, request, *args, **kwargs):
        org = require_org(request)
        serializer = FileFilterRuleWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            rule = services.create_file_filter_rule(
                organization_id=org.id,
                data=serializer.validated_data,
            )
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        record_resource_owner(
            organization_id=org.id, resource_type="file_filter_rule",
            resource_id=rule.id, owner_id=request.user.id,
        )
        return Response(
            FileFilterRuleSerializer(rule).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        rule = self.get_object()
        serializer = FileFilterRuleWriteSerializer(data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        try:
            rule = services.update_file_filter_rule(
                rule=rule,
                data=serializer.validated_data,
            )
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        return Response(FileFilterRuleSerializer(rule).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        rule = self.get_object()
        try:
            result = services.delete_file_filter_rule(rule=rule)
        except services.ResourceInUseError as exc:
            raise Conflict(str(exc)) from exc
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-state")
    def bulk_state(self, request):
        org = require_org(request)
        serializer = BulkStateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = services.bulk_set_file_filter_state(
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
            result = services.bulk_delete_file_filters(
                organization_id=org.id,
                ids=serializer.validated_data["ids"],
            )
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        return Response(result)


class FileFilterRuleViewSet(GovernedResourceMixin, _FileFilterRuleViewSetBase):
    """Resource-aware adapter over the unchanged business implementation."""

    governance_resource_type = "file_filter_rule"
    governance_manage_action = "protection.manage"
