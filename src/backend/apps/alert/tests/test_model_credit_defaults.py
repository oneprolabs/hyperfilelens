"""Existing installations receive credit defaults once, preserving preferences."""

from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TestCase

from apps.alert.models import AlertPolicy
from apps.alert.services.defaults import (
    create_missing_default_alert_rules,
    default_catalog,
    ensure_default_organization_alert_rules,
    restore_default_alert_rules,
)
from apps.iam.models import Organization

seed_credit_rules = import_module(
    "apps.alert.migrations.0010_seed_ai_model_credit_rule",
).seed_credit_rules
CREDIT_KEY = "ai_model.insufficient_credit"


class ModelCreditDefaultTests(TestCase):
    def test_upgrade_seeds_existing_organizations_and_platform_only_once(self):
        org = Organization.objects.create(name="Existing", key="credit-existing")
        other = Organization.objects.create(name="Other", key="credit-existing-other")
        create_missing_default_alert_rules(
            owner_scope="organization", organization=org, default_keys={CREDIT_KEY},
        )
        AlertPolicy.objects.filter(organization=org).update(
            enabled=False, name="Custom credit rule", notification_channel_ids=[73],
        )
        for _ in range(2):
            seed_credit_rules(apps, SimpleNamespace(connection=connection))
        self.assertEqual(AlertPolicy.objects.count(), 3)
        preserved = AlertPolicy.objects.get(organization=org)
        self.assertFalse(preserved.enabled)
        self.assertEqual(preserved.name, "Custom credit rule")
        self.assertEqual(preserved.notification_channel_ids, [73])
        self.assertTrue(AlertPolicy.objects.get(organization=other).enabled)
        platform = AlertPolicy.objects.get(owner_scope="platform")
        expected = next(
            rule for rule in default_catalog(owner_scope="platform")
            if rule["default_key"] == CREDIT_KEY
        )
        for field, value in expected.items():
            self.assertEqual(getattr(platform, field), value)

    def test_new_organizations_still_receive_credit_rule_and_can_restore_it(self):
        org = Organization.objects.create(name="New", key="credit-new")
        ensure_default_organization_alert_rules(org)
        rule = AlertPolicy.objects.get(organization=org, default_key=CREDIT_KEY)
        rule.delete()
        self.assertFalse(AlertPolicy.objects.filter(
            organization=org, default_key=CREDIT_KEY,
        ).exists())
        restore_default_alert_rules(
            owner_scope="organization", organization=org, default_keys={CREDIT_KEY},
        )
        self.assertTrue(AlertPolicy.objects.get(
            organization=org, default_key=CREDIT_KEY,
        ).enabled)
