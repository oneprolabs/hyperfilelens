"""The cache business rule must not rewrite existing alert/notification settings."""

from importlib import import_module

from django.apps import apps
from django.db import connection
from django.test import TestCase

from apps.alert.models import AlertPolicy
from apps.alert.services.defaults import default_catalog
from apps.iam.models import Organization


class CacheWarningRuleMigrationTests(TestCase):
    def test_seed_is_idempotent_and_preserves_existing_rules_and_custom_warning(self):
        migration = import_module("apps.alert.migrations.0009_cache_space_warning_rule")
        organization = Organization.objects.create(key="cache-rule-migration", name="Migration")
        missing = Organization.objects.create(key="cache-rule-missing", name="Missing")
        existing = AlertPolicy.objects.create(
            organization=organization,
            default_key="backup.task_failed",
            name="Customized backup failures",
            type="task",
            trigger_rule={"task_type": "backup", "event_type": "task_failed", "repeat_interval_seconds": 1234,
                          "merge_enabled": True, "merge_key": "resource_id", "merge_window_seconds": 600},
            notification_channel_ids=[23],
        )
        warning = AlertPolicy.objects.create(
            organization=organization,
            default_key="backup.space_check_warning",
            name="Customized space warning",
            enabled=False,
            type="event",
            trigger_rule={"event_types": ["backup_space_check_warning"], "repeat_interval_seconds": 99},
            notification_channel_ids=[24],
        )
        before = list(AlertPolicy.objects.filter(pk__in=[existing.pk, warning.pk]).order_by("pk").values())
        with connection.schema_editor() as editor:
            migration.add_cache_warning_rule(apps, editor)
            migration.add_cache_warning_rule(apps, editor)
        self.assertEqual(list(AlertPolicy.objects.filter(pk__in=[existing.pk, warning.pk]).order_by("pk").values()), before)
        seeded = AlertPolicy.objects.get(organization=missing, default_key="backup.space_check_warning")
        for field, value in migration.WARNING_RULE.items():
            self.assertEqual(getattr(seeded, field), value)
        self.assertEqual(seeded.notification_channel_ids, [])
        self.assertEqual(seeded.trigger_rule, {
            "event_category": "protection",
            "event_types": ["backup_space_check_warning"],
            "repeat_interval_seconds": 7200,
            "merge_enabled": True,
            "merge_window_seconds": 300,
            "merge_key": "",
        })
        definition = next(row for row in default_catalog(owner_scope="organization") if row["default_key"] == seeded.default_key)
        self.assertEqual({key: value for key, value in definition.items() if key != "default_key"}, migration.WARNING_RULE)

    def test_existing_product_defaults_keep_their_previous_window_configuration(self):
        organization_rules = default_catalog(owner_scope="organization")
        backup = next(row for row in organization_rules if row["default_key"] == "backup.task_failed")
        self.assertEqual(backup["trigger_rule"]["repeat_interval_seconds"], 3600)
        for rule in (*organization_rules, *default_catalog(owner_scope="platform")):
            if rule["default_key"] == "backup.space_check_warning":
                continue
            self.assertNotIn("merge_enabled", rule["trigger_rule"])
            self.assertNotIn("merge_window_seconds", rule["trigger_rule"])
            self.assertNotIn("merge_key", rule["trigger_rule"])
