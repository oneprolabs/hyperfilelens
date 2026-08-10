from django.db import IntegrityError
from django.http import JsonResponse

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgOperator, IsOrgStaffReader
from apps.source.api.pagination import SourcePagination
from apps.source.api.serializers import (
    SourceResourceListSerializer,
    SourceResourceSerializer,
    SourceResourceUpdateSerializer,
    SourceResourceWriteSerializer,
)
from apps.source.models import SourceResource
from apps.source.selectors.interface import source_resources_queryset
from apps.source.services.internal.agent_host_sync import sync_agent_source_hosts_for_org
from apps.source import services


def health(_request):
    return JsonResponse({"app": "source", "status": "ok"})


class SourceResourceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsOrgStaffReader]
    pagination_class = SourcePagination
    queryset = SourceResource.objects.none()

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAuthenticated(), IsOrgOperator()]
        if self.action in (
            "test_connection",
            "test_draft",
            "mount",
            "unmount",
            "bind_node",
            "unbind_node",
        ):
            return [IsAuthenticated(), IsOrgOperator()]
        return super().get_permissions()

    def get_serializer_class(self):
        if self.action == "list":
            return SourceResourceListSerializer
        if self.action in ("create",):
            return SourceResourceWriteSerializer
        if self.action in ("update", "partial_update"):
            return SourceResourceUpdateSerializer
        return SourceResourceSerializer

    def get_queryset(self):
        org = require_org(self.request)
        params = self.request.query_params
        node_raw = (params.get("bound_node") or params.get("bound_node_id") or "").strip()
        node_id = int(node_raw) if node_raw.isdigit() else None
        return source_resources_queryset(
            organization_id=org.id,
            resource_type=(params.get("resource_type") or "").strip() or None,
            status=(params.get("status") or "").strip() or None,
            mount_status=(params.get("mount_status") or "").strip() or None,
            bound_node_id=node_id,
            search=(params.get("search") or "").strip() or None,
            search_field=(params.get("search_field") or "").strip() or None,
        )

    def get_object(self):
        org = require_org(self.request)
        obj = services.get_resource(organization_id=org.id, resource_id=int(self.kwargs["pk"]))
        if obj is None:
            from rest_framework.exceptions import NotFound

            raise NotFound()
        return obj

    def create(self, request, *args, **kwargs):
        org = require_org(request)
        ser = SourceResourceWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            resource = services.create_source_resource(
                organization=org,
                user=request.user,
                name=data["name"],
                resource_type=data["resource_type"],
                config=data.get("config"),
                credentials=data.get("credentials"),
                bound_node_id=data.get("bound_node_id") or data.get("bound_node"),
                description=data.get("description", ""),
                status=data.get("status") or None,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        except IntegrityError as exc:
            raise ValidationError(
                {"detail": "A source resource with this name already exists."}
            ) from exc
        return Response(
            SourceResourceSerializer(resource).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        resource = self.get_object()
        ser = SourceResourceUpdateSerializer(data=request.data, partial=partial)
        ser.is_valid(raise_exception=True)
        try:
            resource = services.update_source_resource(
                resource=resource,
                user=request.user,
                **ser.validated_data,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(SourceResourceSerializer(resource).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        resource = self.get_object()
        summary = services.delete_source_resource(resource=resource, user=request.user)
        return Response(summary, status=status.HTTP_200_OK)

    def list(self, request, *args, **kwargs):
        org = require_org(request)
        resource_type = (request.query_params.get("resource_type") or "").strip()
        if not resource_type or resource_type == "local":
            sync_agent_source_hosts_for_org(organization_id=org.id)
        return super().list(request, *args, **kwargs)

    @action(detail=False, methods=["get"])
    def statistics(self, request):
        org = require_org(request)
        sync_agent_source_hosts_for_org(organization_id=org.id)
        return Response(services.resource_statistics(organization_id=org.id))

    @action(detail=False, methods=["get"], url_path="production-summary")
    def production_summary(self, request):
        org = require_org(request)
        return Response(services.production_source_summary(organization_id=org.id))

    @action(detail=True, methods=["post"], url_path="test-connection")
    def test_connection(self, request, pk=None):
        resource = self.get_object()
        result = services.test_resource_connection(resource=resource)
        code = status.HTTP_200_OK if result.get("success") else status.HTTP_400_BAD_REQUEST
        return Response(result, status=code)

    @action(detail=False, methods=["post"], url_path="test-draft")
    def test_draft(self, request):
        org = require_org(request)
        node_id = request.data.get("bound_node") or request.data.get("bound_node_id")
        if not node_id:
            return Response(
                {"success": False, "message": "bound_node is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        result = services.test_draft_connection(
            organization_id=org.id,
            bound_node_id=int(node_id),
            resource_type=request.data.get("resource_type", ""),
            config=request.data.get("config"),
            credentials=request.data.get("credentials"),
        )
        code = status.HTTP_200_OK if result.get("success") else status.HTTP_400_BAD_REQUEST
        return Response(result, status=code)

    @action(detail=True, methods=["post"])
    def mount(self, request, pk=None):
        resource = self.get_object()
        result = services.mount_resource(resource=resource)
        code = status.HTTP_200_OK if result.get("success") else status.HTTP_400_BAD_REQUEST
        return Response(result, status=code)

    @action(detail=True, methods=["post"])
    def unmount(self, request, pk=None):
        resource = self.get_object()
        result = services.unmount_resource(resource=resource)
        code = status.HTTP_200_OK if result.get("success") else status.HTTP_400_BAD_REQUEST
        return Response(result, status=code)

    @action(detail=True, methods=["post"], url_path="bind-node")
    def bind_node(self, request, pk=None):
        resource = self.get_object()
        node_id = request.data.get("node_id")
        if not node_id:
            return Response(
                {"success": False, "message": "node_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        result = services.bind_node(resource=resource, node_id=int(node_id))
        code = status.HTTP_200_OK if result.get("success") else status.HTTP_400_BAD_REQUEST
        return Response(result, status=code)

    @action(detail=True, methods=["post"], url_path="unbind-node")
    def unbind_node(self, request, pk=None):
        resource = self.get_object()
        return Response(services.unbind_node(resource=resource))

    @action(detail=True, methods=["get"])
    def scan(self, request, pk=None):
        resource = self.get_object()
        if not resource.bound_node:
            return Response(
                {"success": False, "message": "No bound node configured."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        path = request.query_params.get("path") or "/"
        return Response(
            {
                "success": True,
                "path": path,
                "directories": [],
                "entries": [],
                "message": "Directory scan will be available when proxy channel is connected.",
            }
        )
