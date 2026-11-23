"""Previous API writers must remain compatible until blue/green cutover."""

from uuid import uuid4

from django.core.exceptions import FieldDoesNotExist
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class ProviderErrorMigrationTests(TransactionTestCase):
    migrate_from = [("lens_bridge", "0051_usage_billing_user")]
    migrate_to = [("lens_bridge", "0052_usage_provider_error_state")]

    def setUp(self):
        super().setUp()
        self.addCleanup(self.restore_schema)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        organization = old_apps.get_model("iam", "Organization")
        self.old_ledger = old_apps.get_model("lens_bridge", "LensUsageLedger")
        self.org = organization.objects.create(
            name="Upgrade compatibility", key="provider-error-upgrade",
        )
        self.existing = self.old_api_insert()

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        self.new_ledger = new_apps.get_model("lens_bridge", "LensUsageLedger")

    def restore_schema(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def old_api_insert(self):
        # The historical ORM omits the new column, exactly as the old API does.
        return self.old_ledger.objects.create(
            organization_id=self.org.pk, sl_user_id=7,
            sl_run_uuid=uuid4(), occurred_at=timezone.now(),
        )

    def test_migration_backfills_existing_rows_without_making_field_nullable(self):
        existing = self.new_ledger.objects.get(pk=self.existing.pk)
        self.assertEqual(existing.provider_error_state_json, {})
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.new_ledger.objects.filter(pk=existing.pk).update(
                provider_error_state_json=None,
            )

    def test_previous_api_can_insert_after_migration_using_database_default(self):
        with self.assertRaises(FieldDoesNotExist):
            self.old_ledger._meta.get_field("provider_error_state_json")
        for _ in range(2):
            inserted = self.old_api_insert()
            current = self.new_ledger.objects.get(pk=inserted.pk)
            self.assertEqual(current.provider_error_state_json, {})

        field = self.new_ledger._meta.get_field("provider_error_state_json")
        self.assertEqual(field.db_default, {})
