"""Tenant-scoped resource metrics API."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgAuthorizedMember
from apps.iam.resource_access import assert_resource_access
from apps.monitor.models import ResourceMetric
from apps.monitor.services.internal.time_range import resolve_time_range


class ResourceMonitorView(APIView):
    """
    GET /api/v1/monitors/resources/

    Query: resource_type, resource_id (required), hours or start_at/end_at.
    """

    permission_classes = [IsAuthenticated, IsOrgAuthorizedMember]

    def get(self, request):
        org = require_org(request)
        resource_type = str(request.query_params.get("resource_type") or "").strip()
        resource_id = str(request.query_params.get("resource_id") or "").strip()
        if not resource_type or not resource_id:
            return Response(
                {"error": "resource_type and resource_id are required"},
                status=400,
            )
        kind = {
            "agent": "node", "proxy": "node", "gateway": "node", "node_agent": "node",
            "node_proxy": "node", "node_gateway": "node", "node": "node",
            "repository": "repository", "backup_repository": "repository",
        }.get(resource_type)
        if (
            kind is None or not resource_id.isascii() or not resource_id.isdigit()
            or not 0 < int(resource_id) <= 2**63 - 1
        ):
            return Response({"error": "Unsupported resource reference"}, status=400)
        assert_resource_access(request, kind, int(resource_id), action="operations.host_monitoring.view")

        since, until, error = resolve_time_range(
            hours_raw=request.query_params.get("hours"),
            start_at_raw=request.query_params.get("start_at"),
            end_at_raw=request.query_params.get("end_at"),
        )
        if error:
            return Response(error, status=400)

        metrics = (
            ResourceMetric.objects.filter(
                organization_id=org.id,
                resource_type=resource_type,
                resource_id=resource_id,
                timestamp__gte=since,
                timestamp__lte=until,
            )
            .order_by("timestamp")[:2000]
        )
        series = [
            {
                "timestamp": m.timestamp.isoformat(),
                "metrics": m.metrics,
                "source": m.source,
                "resource_name": m.resource_name,
            }
            for m in metrics
        ]
        latest = series[-1] if series else {}
        return Response(
            {
                "resource_type": resource_type,
                "resource_id": resource_id,
                "range": {"start_at": since.isoformat(), "end_at": until.isoformat()},
                "current": latest,
                "series": series,
            }
        )
