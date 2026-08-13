from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SessionCapacityMigrationTests(TransactionTestCase):
    migrate_from = [
        ("lens_bridge", "0030_lens_run_submission"),
    ]
    migrate_to = [
        ("lens_bridge", "0031_session_create_and_capacity_state"),
    ]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self._seed_legacy_sessions(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _seed_legacy_sessions(self, apps):
        app_label, model_name = settings.AUTH_USER_MODEL.split(".")
        User = apps.get_model(app_label, model_name)
        Organization = apps.get_model("iam", "Organization")
        LensSessionLink = apps.get_model("lens_bridge", "LensSessionLink")

        self.user_id = User.objects.create(
            username="session-migration@example.test",
            email="session-migration@example.test",
        ).id
        self.organization_id = Organization.objects.create(
            key="session-capacity-migration",
            name="Session capacity migration",
        ).id

        common = {
            "organization_id": self.organization_id,
            "hfl_user_id": self.user_id,
        }
        self.ready_id = LensSessionLink.objects.create(
            **common,
            lifecycle_status="ready",
            source_scopes_json=[],
        ).id
        self.resolved_id = LensSessionLink.objects.create(
            **common,
            lifecycle_status="provisioning",
            source_scopes_json=[
                {"path_type": "file", "file_count": 1, "size_bytes": 42},
            ],
        ).id
        self.pending_id = LensSessionLink.objects.create(
            **common,
            lifecycle_status="failed",
            source_scopes_json=[
                {"path_type": "file", "file_count": 2, "size_bytes": 42},
            ],
        ).id

    def test_schema_and_legacy_capacity_state_are_migrated(self):
        LensSessionLink = self.apps.get_model(
            "lens_bridge",
            "LensSessionLink",
        )

        ready = LensSessionLink.objects.get(pk=self.ready_id)
        resolved = LensSessionLink.objects.get(pk=self.resolved_id)
        pending = LensSessionLink.objects.get(pk=self.pending_id)

        self.assertEqual(ready.scope_resolution_status, "resolved")
        self.assertEqual(ready.capacity_reservation_status, "released")
        self.assertEqual(resolved.scope_resolution_status, "resolved")
        self.assertEqual(resolved.capacity_reservation_status, "pending")
        self.assertEqual(pending.scope_resolution_status, "pending")
        self.assertEqual(pending.capacity_reservation_status, "pending")
        self.assertEqual(pending.capacity_reserved_bytes, 0)
        self.assertIsNone(pending.capacity_reserved_at)

    def test_active_create_idempotency_key_is_unique_per_user_and_org(self):
        LensSessionLink = self.apps.get_model(
            "lens_bridge",
            "LensSessionLink",
        )
        common = {
            "organization_id": self.organization_id,
            "hfl_user_id": self.user_id,
            "create_idempotency_key": "same-create-request",
        }

        LensSessionLink.objects.create(**common)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LensSessionLink.objects.create(**common)
