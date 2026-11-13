"""REST: control-plane system monitor dashboard."""

from rest_framework.permissions import BasePermission, IsAuthenticated

from common.platform_authz import MONITORING_HOST_MONITORING_VIEW, has_platform_permission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.monitor.services.interface import build_system_monitor_payload, resolve_monitor_time_range


class CanViewControlPlaneMonitor(BasePermission):
    def has_permission(self, request, view):
        return has_platform_permission(request.user, MONITORING_HOST_MONITORING_VIEW)


class SystemMonitorView(APIView):
    """
    GET /api/v1/monitors/system/

    Returns host info, current snapshot, and time series for charts.
    Query: hours (default 24), or start_at + end_at (ISO datetimes).
    """

    permission_classes = [IsAuthenticated, CanViewControlPlaneMonitor]

    def get(self, request):
        since, until, error = resolve_monitor_time_range(
            hours_raw=request.query_params.get("hours"),
            start_at_raw=request.query_params.get("start_at"),
            end_at_raw=request.query_params.get("end_at"),
        )
        if error:
            return Response(error, status=400)
        payload = build_system_monitor_payload(since=since, until=until)
        if payload is None:
            return Response(
                {
                    "host": {},
                    "range": {
                        "start_at": since.isoformat(),
                        "end_at": until.isoformat(),
                        "count": 0,
                    },
                    "current": {},
                    "series": [],
                }
            )
        return Response(payload)
