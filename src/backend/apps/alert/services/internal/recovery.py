"""Recovery capabilities shared by policy validation and lifecycle handlers."""

from apps.alert.constants import AlertType


def supports_numeric_recovery(alert_type, trigger_rule) -> bool:
    del trigger_rule  # numeric recovery is owned by Metric rules only
    return alert_type == AlertType.METRIC
