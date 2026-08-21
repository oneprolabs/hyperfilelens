from django.contrib import admin

from apps.node.models import Node, NodeTask, NodeToken


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    readonly_fields = ("installation_mode",)
    list_display = (
        "name",
        "organization",
        "role",
        "installation_mode",
        "status",
        "version",
        "ip_address",
        "last_seen_at",
    )
    list_filter = ("role", "installation_mode", "status", "organization")
    search_fields = ("name", "ip_address", "organization__key")
    ordering = ("organization", "name", "id")


@admin.register(NodeTask)
class NodeTaskAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "organization",
        "node",
        "kind",
        "status",
        "watchdog_deadline_at",
        "last_progress_at",
        "dispatched_at",
    )
    list_filter = ("status", "kind", "organization")
    search_fields = ("kind", "correlation_id", "correlation_type", "id")


@admin.register(NodeToken)
class NodeTokenAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "role",
        "installation_mode",
        "is_active",
        "note",
        "created_at",
        "expires_at",
        "used_at",
    )
    list_filter = ("role", "installation_mode", "is_active", "organization")
    search_fields = ("token", "note", "organization__key")
    ordering = ("-created_at", "-id")

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        return ("organization", "token", "role", "installation_mode", "expires_at")
