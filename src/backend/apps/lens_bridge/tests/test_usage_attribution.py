"""Historical consumption remains charged to its original organization-user."""

from importlib import import_module
from types import SimpleNamespace
from uuid import uuid4

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.lens_bridge.models import LensSessionLink, LensSlUserLink, LensUsageLedger
from apps.lens_bridge.services.usage import register_usage_run


class UsageAttributionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="billing-attribution", name="Billing attribution")
        self.user = get_user_model().objects.create_user(username="billing-original")
        self.other = get_user_model().objects.create_user(username="billing-other")

    def ledger(self, user, **extra):
        return LensUsageLedger.objects.create(
            organization=self.org, hfl_user=user, sl_user_id=1, sl_run_uuid=uuid4(),
            occurred_at=timezone.now(), total_tokens=10, **extra,
        )

    def test_snapshot_survives_user_deletion_and_metrics_updates(self):
        row = self.ledger(self.user)
        original_id = self.user.pk
        self.assertEqual(row.billing_user_id, original_id)
        self.user.delete()
        row.refresh_from_db()
        self.assertIsNone(row.hfl_user_id)
        self.assertEqual(row.billing_user_id, original_id)
        row.total_tokens = 20
        row.save(update_fields=["total_tokens"])
        row.refresh_from_db()
        self.assertEqual(row.billing_user_id, original_id)

    def test_pre_delete_preserves_known_rows_created_without_model_save(self):
        row = self.ledger(self.user)
        LensUsageLedger.objects.filter(pk=row.pk).update(billing_user_id=None)
        original_id = self.user.pk
        self.user.delete()
        row.refresh_from_db()
        self.assertEqual(row.billing_user_id, original_id)
        self.assertIsNone(row.hfl_user_id)

    def test_model_and_registration_reject_reassignment_of_existing_run(self):
        row = self.ledger(self.user)
        row.hfl_user = self.other
        with self.assertRaises(ValueError):
            row.save()
        row.billing_user_id = self.other.pk
        with self.assertRaises(ValueError):
            row.save()
        LensSlUserLink.objects.create(
            hfl_user=self.other, sl_user_id=2, sl_username="other",
            provision_status=LensSlUserLink.ProvisionStatus.READY,
        )
        session = LensSessionLink.objects.create(organization=self.org, hfl_user=self.other)
        with self.assertRaises(ValueError):
            register_usage_run(session, run_uuid=row.sl_run_uuid, question="Rebind", status="done")
        row.refresh_from_db()
        self.assertEqual(row.billing_user_id, self.user.pk)
        self.assertEqual(row.hfl_user_id, self.user.pk)

    def test_migration_backfills_known_fk_but_does_not_guess_orphan_history(self):
        known = self.ledger(self.user)
        unknown = self.ledger(None)
        LensUsageLedger.objects.filter(pk=known.pk).update(billing_user_id=None)
        migration = import_module("apps.lens_bridge.migrations.0051_usage_billing_user")
        migration.preserve_known_users(apps, SimpleNamespace(connection=connection))
        known.refresh_from_db()
        unknown.refresh_from_db()
        self.assertEqual(known.billing_user_id, self.user.pk)
        self.assertIsNone(unknown.billing_user_id)
