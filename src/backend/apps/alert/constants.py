"""Shared constants for the alert center."""

from django.db import models


class AlertType(models.TextChoices):
    METRIC = "metric", "Metric Alert"
    HEALTH = "health", "Health Alert"
    TASK = "task", "Task Alert"
    EVENT = "event", "Event Alert"


class AlertSeverity(models.TextChoices):
    CRITICAL = "critical", "Critical"
    WARNING = "warning", "Warning"
    INFO = "info", "Info"


class AlertStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    FIRING = "firing", "Firing"
    ACKNOWLEDGED = "acknowledged", "Acknowledged"
    RESOLVED = "resolved", "Resolved"


class ResourceType(models.TextChoices):
    SYSTEM = "system", "Control Plane"
    AGENT_PROXY = "agent_proxy", "Source Host"
    SYNC_PROXY = "sync_proxy", "Proxy Host"
    GATEWAY = "gateway", "Data Gateway"
    SOURCE_RESOURCE = "source_resource", "Source Resource"
    BACKUP_REPOSITORY = "backup_repository", "Backup Repository"
    TARGET_STORAGE = "target_storage", "Target Storage"
    TASK = "task", "Task"
    SYSTEM_SERVICE = "system_service", "System Service"
    LICENSE = "license", "License"
    USER = "user", "User"


class PolicyScope(models.TextChoices):
    ALL = "all", "All"
    SELECTED = "selected", "Selected"


class NotificationLogStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"


class NotificationLogType(models.TextChoices):
    FIRING = "firing", "Firing"
    RESOLVED = "resolved", "Resolved"


METRICS_BY_RESOURCE_TYPE = {
    ResourceType.SYSTEM: [
        "cpu_usage",
        "load_1m",
        "load_5m",
        "load_15m",
        "memory_usage",
        "swap_usage",
        "disk_usage",
        "disk_read_bytes",
        "disk_write_bytes",
        "network_rx",
        "network_tx",
    ],
    ResourceType.SYNC_PROXY: [
        "cpu_usage",
        "memory_usage",
        "disk_usage",
        "network_rx",
        "network_tx",
    ],
    ResourceType.GATEWAY: [
        "cpu_usage",
        "memory_usage",
        "disk_usage",
        "network_rx",
        "network_tx",
    ],
    ResourceType.AGENT_PROXY: [
        "cpu_usage",
        "memory_usage",
        "disk_usage",
        "network_rx",
        "network_tx",
    ],
    ResourceType.BACKUP_REPOSITORY: ["capacity_usage", "used_size", "free_size"],
    ResourceType.SOURCE_RESOURCE: ["capacity_usage", "data_size", "file_count"],
    ResourceType.TARGET_STORAGE: ["capacity_usage", "used_size", "free_size"],
}

# Org-level Health checks (tenant Alert Rules).
HEALTH_CHECK_TYPES = ["heartbeat"]
# Health evaluator targets node heartbeats only. Control Plane and service
# resources do not expose a node heartbeat and must not produce no-op rules.
HEALTH_RESOURCE_TYPES = [
    ResourceType.AGENT_PROXY,
    ResourceType.SYNC_PROXY,
    ResourceType.GATEWAY,
]
# Only checks with an evaluator are exposed. Platform service probes are not
# yet safe to configure as alerts because their evaluator has no durable
# service-health source (and must not silently create rules that never fire).
PLATFORM_HEALTH_CHECK_TYPES = ["heartbeat"]
# Backward-compatible aliases for older imports / metadata clients.
AVAILABILITY_CHECK_TYPES = list(HEALTH_CHECK_TYPES)
SYSTEM_CHECK_TYPES = [c for c in PLATFORM_HEALTH_CHECK_TYPES if c != "heartbeat"]
TASK_TYPES = ["backup", "restore", "repository_operation"]
TASK_EVENT_TYPES = ["task_failed", "task_timeout", "retry_exceeded", "partial_success"]
EVENT_CATEGORIES = ["user", "license", "repository", "configuration", "security"]
EVENT_TYPES = {
    "user": [
        "user_created",
        "user_deleted",
        "user_disabled",
        "user_enabled",
        "password_changed",
        "user_role_changed",
        "login_success",
        "login_failed",
        "logout",
    ],
    "license": [
        "license_added",
        "license_updated",
        "license_expired",
        "license_near_expiration",
        "license_capacity_exceeded",
    ],
    "repository": [
        "repository_created",
        "repository_deleted",
        "repository_updated",
        "repository_unreachable",
        "repository_readonly",
        "repository_capacity_low",
    ],
    "configuration": [
        "configuration_changed",
        "notification_channel_changed",
        "alert_policy_changed",
        "repository_config_changed",
        "proxy_config_changed",
    ],
    "security": [
        "multiple_login_failures",
        "api_token_created",
        "api_token_deleted",
        "permission_changed",
    ],
}
