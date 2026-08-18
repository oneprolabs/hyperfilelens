from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PolicyStartSecondsMigrationTests(TransactionTestCase):
    migrate_from = [("protection", "0018_backup_directory_repository_locator")]
    migrate_to = [("protection", "0019_normalize_policy_start_seconds")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        BackupPolicy = old_apps.get_model("protection", "BackupPolicy")
        Organization = old_apps.get_model("iam", "Organization")
        organization = Organization.objects.create(
            key="policy-start-seconds-migration",
            name="Policy start seconds migration",
        )

        common = {
            "organization_id": organization.id,
            "is_active": True,
            "retention": {},
            "throttling": {},
            "error_handling": {},
        }
        self.minute_id = BackupPolicy.objects.create(
            name="Minute precision",
            schedule={"enabled": True, "mode": "interval", "starts_at": "2026-08-18T09:30"},
            **common,
        ).id
        self.second_id = BackupPolicy.objects.create(
            name="Second precision",
            schedule={"enabled": True, "mode": "interval", "starts_at": "2026-08-18T09:30:45"},
            **common,
        ).id
        self.cron_id = BackupPolicy.objects.create(
            name="Legacy cron",
            schedule={"enabled": True, "cron_expr": "0 2 * * *", "starts_at": "2026-08-18T09:30"},
            **common,
        ).id
        self.malformed_id = BackupPolicy.objects.create(
            name="Malformed start",
            schedule={"enabled": True, "mode": "interval", "starts_at": "2026-02-31T09:30"},
            **common,
        ).id

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_only_structured_minute_precision_starts_are_normalized(self):
        BackupPolicy = self.apps.get_model("protection", "BackupPolicy")

        self.assertEqual(
            BackupPolicy.objects.get(pk=self.minute_id).schedule["starts_at"],
            "2026-08-18T09:30:00",
        )
        self.assertEqual(
            BackupPolicy.objects.get(pk=self.second_id).schedule["starts_at"],
            "2026-08-18T09:30:45",
        )
        self.assertEqual(
            BackupPolicy.objects.get(pk=self.cron_id).schedule["starts_at"],
            "2026-08-18T09:30",
        )
        self.assertEqual(
            BackupPolicy.objects.get(pk=self.malformed_id).schedule["starts_at"],
            "2026-02-31T09:30",
        )
