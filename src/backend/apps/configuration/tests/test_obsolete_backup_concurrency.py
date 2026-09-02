"""Regression coverage for the removed backup concurrency setting."""

from django.test import SimpleTestCase
from django.urls import Resolver404, resolve

from apps.configuration.services.internal.registry import registry_by_key


class ObsoleteBackupConcurrencyTests(SimpleTestCase):
    def test_removed_org_settings_route_is_not_registered(self):
        with self.assertRaises(Resolver404):
            resolve("/api/v1/configuration/org-settings/")

    def test_removed_setting_is_not_registered(self):
        self.assertNotIn("file_dr.dr_task_concurrency", registry_by_key())
