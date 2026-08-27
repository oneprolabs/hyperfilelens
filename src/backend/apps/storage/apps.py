"""
Storage app config.
"""

from django.apps import AppConfig


class StorageConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.storage"
    verbose_name = "Storage"

    def ready(self) -> None:
        # Fail fast on invalid storage task configuration.
        from apps.storage.conf import (
            background_storage_concurrency,
            kopia_config_lock_timeout_seconds,
            repository_health_interval_seconds,
        )
        from apps.storage.services.internal.repository_operations import (
            maintenance_settings,
        )

        maintenance_settings()
        background_storage_concurrency()
        kopia_config_lock_timeout_seconds()
        repository_health_interval_seconds()

        # The release baseline must be valid before this process serves requests.
        from apps.storage.provider_catalog.catalog import load_default_catalog
        load_default_catalog()
