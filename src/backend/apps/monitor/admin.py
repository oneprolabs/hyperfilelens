from django.contrib import admin

from apps.monitor.models import (
    DeploymentHost,
    RepositoryUsageMetric,
    ResourceMetric,
    SystemMetric,
)


@admin.register(DeploymentHost)
class DeploymentHostAdmin(admin.ModelAdmin):
    list_display = ("hostname", "ip_address", "platform", "app_version", "last_seen_at")
    ordering = ("-last_seen_at",)
    readonly_fields = ("created_at", "last_seen_at")


@admin.register(SystemMetric)
class SystemMetricAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "host", "id")
    ordering = ("-timestamp",)
    readonly_fields = (
        "timestamp",
        "cpu",
        "memory",
        "swap",
        "disks",
        "disk_io",
        "networks",
        "load_average",
        "metadata",
    )


@admin.register(ResourceMetric)
class ResourceMetricAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "organization", "resource_type", "resource_id", "source")
    list_filter = ("resource_type", "source")
    ordering = ("-timestamp",)


@admin.register(RepositoryUsageMetric)
class RepositoryUsageMetricAdmin(admin.ModelAdmin):
    list_display = ("recorded_at", "repository", "usage_bytes", "usage_source")
    list_filter = ("usage_source",)
    ordering = ("-recorded_at",)
    readonly_fields = ("repository", "recorded_at", "usage_bytes", "usage_source", "object_count")
