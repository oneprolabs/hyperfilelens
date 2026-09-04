"""Tenant-scoped operational event feed."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgReader
from apps.monitor.models import OperationalEvent


PERIODS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def _serialize_event(event: OperationalEvent) -> dict[str, object]:
    return {
        "id": str(event.id),
        "event_type": event.event_type,
        "category": event.category,
        "severity": event.severity,
        "title": event.title,
        "details": event.details,
        "occurred_at": event.occurred_at.isoformat(),
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "resource_name": event.resource_name,
        "source": event.source,
        "target_path": event.target_path,
        "correlation_id": event.correlation_id,
    }


class EventView(APIView):
    """Return filtered events and matching severity totals for one organization."""

    permission_classes = [IsAuthenticated, IsOrgReader]

    def get(self, request):
        org = require_org(request)
        category = str(request.query_params.get("category") or "").strip()
        severity = str(request.query_params.get("severity") or "").strip()
        period = str(request.query_params.get("period") or "24h").strip()
        search = str(request.query_params.get("search") or "").strip()[:200]

        if category and category not in OperationalEvent.Category.values:
            raise ValidationError({"category": "invalid event category"})
        if severity and severity not in OperationalEvent.Severity.values:
            raise ValidationError({"severity": "invalid event severity"})
        if period not in {*PERIODS, "all"}:
            raise ValidationError({"period": "period must be 24h, 7d, 30d, or all"})
        try:
            page = max(1, int(request.query_params.get("page") or 1))
            page_size = min(
                100, max(1, int(request.query_params.get("page_size") or 20))
            )
        except ValueError as exc:
            raise ValidationError(
                {"page": "page and page_size must be integers"}
            ) from exc

        queryset = OperationalEvent.objects.filter(organization=org)
        if period != "all":
            queryset = queryset.filter(
                occurred_at__gte=timezone.now() - PERIODS[period]
            )
        if category:
            queryset = queryset.filter(category=category)
        if severity:
            queryset = queryset.filter(severity=severity)
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search)
                | Q(details__icontains=search)
                | Q(resource_name__icontains=search)
            )

        stats = {
            "total": queryset.count(),
            "critical": queryset.filter(
                severity=OperationalEvent.Severity.CRITICAL
            ).count(),
            "warning": queryset.filter(
                severity=OperationalEvent.Severity.WARNING
            ).count(),
            "information": queryset.filter(
                severity=OperationalEvent.Severity.INFORMATION
            ).count(),
        }
        start = (page - 1) * page_size
        rows = queryset[start : start + page_size]
        return Response(
            {
                "count": stats["total"],
                "stats": stats,
                "results": [_serialize_event(event) for event in rows],
            }
        )
