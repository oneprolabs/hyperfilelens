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
from apps.iam.activity_access import filter_activity_queryset
from apps.monitor.models import OperationalEvent
from apps.monitor.services.event_targets import repository_event_target, source_event_target
from apps.source.models import SourceResource
from apps.storage.models import Repository


PERIODS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def _serialize_event(
    event: OperationalEvent,
    source_types: dict[str, str] | None = None,
    repository_types: dict[str, str] | None = None,
) -> dict[str, object]:
    target_path = event.target_path
    if event.source == "source" and event.resource_id and target_path in {
        "", "/protection/backup-sources", "/protection/backup-sources?tab=host",
    }:
        source_type = (source_types or {}).get(
            event.resource_id, event.metadata.get("source_type", ""),
        )
        target_path = source_event_target(event.resource_id, source_type)
    elif event.resource_type == "repository" and event.resource_id and target_path in {
        "", "/node/repositories",
    }:
        repo_type = (repository_types or {}).get(
            event.resource_id, event.metadata.get("repo_type", ""),
        )
        target_path = repository_event_target(event.resource_id, repo_type)
    # Availability events written before the node-route split still point all
    # non-gateway nodes at the Proxy Hosts page. Resolve those legacy links at
    # read time so existing events remain actionable after the route fix.
    if event.source == "node" and event.resource_id:
        if event.resource_type == "agent" and (
            not target_path or target_path.startswith("/node/agents")
        ):
            target_path = (
                "/protection/backup-sources?tab=host&openNode="
                f"{event.resource_id}"
            )
        elif event.resource_type == "proxy" and target_path == "/node/agents":
            target_path = f"/node/agents?openNode={event.resource_id}"
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
        "target_path": target_path,
        "correlation_id": event.correlation_id,
    }


class EventView(APIView):
    """Return filtered events and matching severity totals for one organization."""

    permission_classes = [IsAuthenticated, IsOrgReader]
    governance_read_action = "operations.events.view"

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

        queryset = filter_activity_queryset(
            request, OperationalEvent.objects.filter(organization=org),
        )
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
        rows = list(queryset[start : start + page_size])
        # Resolve only this page, scoped to the current tenant, without N+1 reads.
        source_ids = {
            int(event.resource_id) for event in rows
            if event.source == "source" and event.resource_id.isdecimal()
            and 0 < int(event.resource_id) <= 2**63 - 1
        }
        repository_ids = {
            int(event.resource_id) for event in rows
            if event.resource_type == "repository" and event.resource_id.isdecimal()
            and 0 < int(event.resource_id) <= 2**63 - 1
        }
        source_types = {
            str(pk): kind for pk, kind in SourceResource.objects.filter(
                organization_id=org.id, pk__in=source_ids, is_deleted=False,
            ).values_list("pk", "resource_type")
        }
        repository_types = {
            str(pk): kind for pk, kind in Repository.objects.filter(
                organization_id=org.id, pk__in=repository_ids,
            ).values_list("pk", "repo_type")
        }
        return Response(
            {
                "count": stats["total"],
                "stats": stats,
                "results": [
                    _serialize_event(event, source_types, repository_types)
                    for event in rows
                ],
            }
        )
