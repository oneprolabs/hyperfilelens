import uuid

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.services.interface import write_audit_log
from apps.iam.permissions_org import IsOrgReader, IsOrgWriter

from apps.alert.api.pagination import AlertPagination
from apps.alert.api.serializers import (
    AlertPolicySerializer,
    BulkPolicyDeleteSerializer,
    BulkPolicyStateSerializer,
)
from apps.alert.api.views._org import require_org
from apps.alert.selectors.interface import filter_policies, policies_for_org
from apps.alert.selectors.stats import policy_statistics
from apps.alert.services.internal.policy_bulk import bulk_delete_policies, bulk_set_policy_state


class AlertPolicyViewSet(viewsets.ModelViewSet):
    serializer_class = AlertPolicySerializer
    permission_classes = [IsAuthenticated, IsOrgWriter]
    pagination_class = AlertPagination

    def get_permissions(self):
        if self.action in ("list", "retrieve", "stats"):
            return [IsAuthenticated(), IsOrgReader()]
        return super().get_permissions()

    def get_queryset(self):
        org = require_org(self.request)
        qs = policies_for_org(organization_id=org.id)
        params = self.request.query_params
        return filter_policies(
            qs,
            search=params.get("search", ""),
            alert_type=params.get("type", ""),
            severity=params.get("severity", ""),
            resource_type=params.get("resource_type", ""),
            enabled=params.get("enabled", ""),
        )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        try:
            ctx["organization"] = require_org(self.request)
        except Exception:
            pass
        return ctx

    def perform_create(self, serializer):
        org = require_org(self.request)
        from apps.subscription.services.interface import enforce_license_quota

        enforce_license_quota(org, "max_alert_policies", additional=1)
        policy = serializer.save(organization=org)
        write_audit_log(
            organization=org,
            user=self.request.user,
            action="alert.policy.create",
            target_type="alert_policy",
            target_id=str(policy.id),
            ip_address=self.request.META.get("REMOTE_ADDR"),
            user_agent=str(self.request.META.get("HTTP_USER_AGENT", "") or ""),
            metadata={"name": policy.name, "type": policy.type},
        )

    def perform_update(self, serializer):
        policy = serializer.save()
        write_audit_log(
            organization=policy.organization,
            user=self.request.user,
            action="alert.policy.update",
            target_type="alert_policy",
            target_id=str(policy.id),
            ip_address=self.request.META.get("REMOTE_ADDR"),
            user_agent=str(self.request.META.get("HTTP_USER_AGENT", "") or ""),
            metadata={"name": policy.name},
        )

    def perform_destroy(self, instance):
        org = instance.organization
        policy_id = str(instance.id)
        name = instance.name
        instance.delete()
        write_audit_log(
            organization=org,
            user=self.request.user,
            action="alert.policy.delete",
            target_type="alert_policy",
            target_id=policy_id,
            ip_address=self.request.META.get("REMOTE_ADDR"),
            user_agent=str(self.request.META.get("HTTP_USER_AGENT", "") or ""),
            metadata={"name": name},
        )

    @action(detail=True, methods=["post"])
    def enable(self, request, pk=None):
        policy = self.get_object()
        policy.enabled = True
        policy.save(update_fields=["enabled", "updated_at"])
        return Response(self.get_serializer(policy).data)

    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        policy = self.get_object()
        policy.enabled = False
        policy.save(update_fields=["enabled", "updated_at"])
        return Response(self.get_serializer(policy).data)

    @action(detail=False, methods=["post"], url_path="bulk-state")
    def bulk_state(self, request):
        org = require_org(request)
        serializer = BulkPolicyStateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = bulk_set_policy_state(
            organization_id=org.id,
            ids=list(serializer.validated_data["ids"]),
            enabled=bool(serializer.validated_data["enabled"]),
        )
        return Response(result)

    @action(detail=False, methods=["post"], url_path="bulk-delete")
    def bulk_delete(self, request):
        org = require_org(request)
        serializer = BulkPolicyDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = bulk_delete_policies(
            organization_id=org.id,
            ids=list(serializer.validated_data["ids"]),
        )
        return Response(result)

    @action(detail=True, methods=["post"])
    def duplicate(self, request, pk=None):
        policy = self.get_object()
        policy.pk = None
        policy.id = uuid.uuid4()
        policy.name = f"{policy.name} Copy"
        if request.user.is_authenticated:
            policy.created_by = request.user.id
        policy.save()
        return Response(self.get_serializer(policy).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        org = require_org(request)
        params = request.query_params
        return Response(
            policy_statistics(
                organization_id=org.id,
                search=params.get("search", ""),
                alert_type=params.get("type", ""),
                severity=params.get("severity", ""),
                resource_type=params.get("resource_type", ""),
                enabled=params.get("enabled", ""),
            )
        )
