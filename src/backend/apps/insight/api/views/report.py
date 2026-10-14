"""Retained Insight Report/Finding APIs with configuration-scoped authority."""

from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param

from apps.iam.models import Organization
from apps.insight.access import ReportPermission, filter_reports
from apps.insight.api.serializers import InsightFindingSerializer, InsightReportSerializer
from apps.insight.models import InsightFinding, InsightReport
from common.drf.org_scoped import OrgScopedMixin
from common.drf.query_page import snapshot_page


def health(_request):
    return JsonResponse({"app": "insight", "status": "ok"})


class _ReportAuthorityMixin:
    permission_classes = [IsAuthenticated, ReportPermission]

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        try:
            page = int(request.query_params.get("page", 1))
            size = min(10000, int(request.query_params.get("page_size", 10)))
            if page < 1 or size < 1 or (page - 1) * size > 2**63 - 1:
                raise ValueError
        except (TypeError, ValueError):
            raise ValidationError("Invalid page or page_size.") from None
        refs, total = snapshot_page(queryset, fields=("id",), page=page, page_size=size)
        rows = queryset.filter(pk__in=[row["id"] for row in refs])
        url = request.build_absolute_uri()
        return Response({"data": {
            "list": self.get_serializer(rows, many=True).data,
            "pagination": {
                "total": total, "page": page, "pageSize": size,
                "next": replace_query_param(url, "page", page + 1) if page * size < total else None,
                "previous": replace_query_param(url, "page", page - 1) if page > 1 else None,
            },
        }})

    def _lock_authority(self):
        # Serialize with Role/member/Group changes; recheck after the lock.
        Organization.objects.select_for_update(no_key=True).get(pk=self.org.pk)
        self.check_permissions(self.request)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        self._lock_authority()
        return super().create(request, *args, **kwargs)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        self._lock_authority()
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        self._lock_authority()
        return super().destroy(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(organization=self.org)

    def perform_update(self, serializer):
        serializer.save(organization=self.org)


class InsightReportViewSet(_ReportAuthorityMixin, OrgScopedMixin, viewsets.ModelViewSet):
    queryset = InsightReport.objects.select_related(
        "organization", "snapshot", "snapshot__source_snapshot",
    )
    serializer_class = InsightReportSerializer

    def get_org_scoped_queryset(self):
        return filter_reports(
            self.request, super().get_org_scoped_queryset(), self.org.pk,
        )

    def perform_update(self, serializer):
        snapshot = serializer.validated_data.get("snapshot", serializer.instance.snapshot)
        if (
            snapshot.pk != serializer.instance.snapshot_id
            and serializer.instance.findings.exists()
        ):
            raise ValidationError({"snapshot": "A report with findings cannot change snapshots."})
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        if instance.findings.exclude(
            organization_id=self.org.pk, snapshot_id=instance.snapshot_id,
        ).exists():
            raise ValidationError("Repair inconsistent report findings before deleting the report.")
        super().perform_destroy(instance)


class InsightFindingViewSet(_ReportAuthorityMixin, OrgScopedMixin, viewsets.ModelViewSet):
    queryset = InsightFinding.objects.select_related(
        "organization", "snapshot", "snapshot__source_snapshot", "report",
    )
    serializer_class = InsightFindingSerializer

    def get_org_scoped_queryset(self):
        return filter_reports(
            self.request, super().get_org_scoped_queryset(), self.org.pk,
        ).filter(
            report__organization_id=self.org.pk,
            report__snapshot_id=F("snapshot_id"),
        )
