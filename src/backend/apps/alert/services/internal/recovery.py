"""Recovery capabilities shared by policy validation and lifecycle handlers."""

from apps.alert.constants import AlertType


def supports_numeric_recovery(alert_type, trigger_rule) -> bool:
    return alert_type == AlertType.METRIC or (
        alert_type == AlertType.SYSTEM
        and (
            trigger_rule.get("check_type") == "disk_space_low"
            or bool(trigger_rule.get("metric_key"))
        )
    )
