import os
from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.storage.services.internal.repository_operations import maintenance_settings


class RepositoryMaintenanceSettingsTests(SimpleTestCase):
    def test_quick_maintenance_defaults_to_six_hours(self):
        with patch.dict(os.environ):
            os.environ.pop("STORAGE_MAINTENANCE_QUICK_INTERVAL_SECONDS", None)

            settings = maintenance_settings()

        self.assertEqual(settings.quick_interval, timedelta(hours=6))

    def test_quick_maintenance_interval_remains_configurable(self):
        with patch.dict(
            os.environ,
            {"STORAGE_MAINTENANCE_QUICK_INTERVAL_SECONDS": "7200"},
        ):
            settings = maintenance_settings()

        self.assertEqual(settings.quick_interval, timedelta(hours=2))
