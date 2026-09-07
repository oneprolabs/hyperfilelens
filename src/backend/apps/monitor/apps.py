"""Control-plane host monitoring."""

from django.apps import AppConfig


class MonitorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.monitor"
    verbose_name = "Monitor"

    def ready(self) -> None:
        import apps.monitor.signals  # noqa: F401
