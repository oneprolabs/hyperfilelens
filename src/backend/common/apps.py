"""Django AppConfig for the platform application."""

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class PlatformConfig(AppConfig):
    """Platform app: one-time hooks for observability and shared infrastructure."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "common"
    verbose_name = "Platform"

    def ready(self) -> None:
        """Initialize optional integrations after apps and models are loaded."""
        # Import registers Celery task lifecycle hooks that release coalesced
        # periodic wake-ups when execution finishes or a message is revoked.
        from common.scheduling import periodic_wakeup  # noqa: F401

        try:
            from common.observability.sentry import init_sentry

            init_sentry()
        except Exception:
            logger.exception(
                "Failed to initialize observability; continuing startup",
            )
