from rest_framework import serializers

from apps.insight.models import InsightFinding, InsightReport
from apps.insight.access import authorized_directories, filter_reports


class _ReportRelationsMixin:
    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        view = self.context.get("view")
        if request is not None and view is not None:
            fields["snapshot"].queryset = authorized_directories(request, view.org.pk)
            if "report" in fields:
                fields["report"].queryset = filter_reports(
                    request, InsightReport.objects.all(), view.org.pk,
                )
        return fields

    def validate(self, attrs):
        attrs = super().validate(attrs)
        org = self.initial_data.get("organization") if hasattr(self, "initial_data") else None
        view = self.context.get("view")
        if org is not None and view is not None and str(org) != str(view.org.pk):
            raise serializers.ValidationError({"organization": "Select the current organization."})
        return attrs


class InsightReportSerializer(_ReportRelationsMixin, serializers.ModelSerializer):
    class Meta:
        model = InsightReport
        fields = (
            "id", "organization", "snapshot", "report_type", "status",
            "summary", "error", "created_at", "updated_at",
        )
        read_only_fields = ("id", "organization", "created_at", "updated_at")


class InsightFindingSerializer(_ReportRelationsMixin, serializers.ModelSerializer):
    class Meta:
        model = InsightFinding
        fields = (
            "id", "organization", "snapshot", "report", "finding_type",
            "severity", "title", "detail", "created_at",
        )
        read_only_fields = ("id", "organization", "created_at")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        report = attrs.get("report") or getattr(self.instance, "report", None)
        snapshot = attrs.get("snapshot") or getattr(self.instance, "snapshot", None)
        if report is not None and snapshot is not None and report.snapshot_id != snapshot.pk:
            raise serializers.ValidationError({"snapshot": "Finding snapshot must match its report."})
        return attrs

