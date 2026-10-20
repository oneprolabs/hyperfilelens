import os
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class AvailabilityMigrationTests(TransactionTestCase):
    migrate_from = [
        ("node", "0008_nodetask_requesting_organization"),
        ("source", "0009_repository_purge_pending_idempotency_key"),
    ]
    migrate_to = [
        ("node", "0009_node_availability"),
        ("source", "0010_source_resource_availability"),
    ]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self._seed_history(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    # Only offline test-database restoration may cross the exclusive boundary.
    @patch.dict(os.environ, {"HFL_EXCLUSIVE_PLATFORM_MIGRATION": "1"})
    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _seed_history(self, apps):
        Organization = apps.get_model("iam", "Organization")
        Node = apps.get_model("node", "Node")
        SourceResource = apps.get_model("source", "SourceResource")
        org = Organization.objects.create(
            key="availability-migration",
            name="Availability migration",
        )
        now = timezone.now()
        agent = Node.objects.create(
            organization=org,
            name="fresh-agent",
            role="agent",
            status="online",
            last_seen_at=now,
        )
        stale_agent = Node.objects.create(
            organization=org,
            name="stale-agent",
            role="agent",
            status="online",
            last_seen_at=now - timedelta(minutes=5),
        )
        proxy = Node.objects.create(
            organization=org,
            name="fresh-proxy",
            role="proxy",
            status="online",
            last_seen_at=now,
        )
        SourceResource.objects.create(
            organization=org,
            name="local-fresh",
            resource_type="local",
            bound_node=agent,
        )
        SourceResource.objects.create(
            organization=org,
            name="local-stale",
            resource_type="local",
            bound_node=stale_agent,
        )
        SourceResource.objects.create(
            organization=org,
            name="nas-proven",
            resource_type="nas",
            bound_node=proxy,
            connection_test_status="success",
            last_connection_test=now,
        )
        SourceResource.objects.create(
            organization=org,
            name="nas-unproven",
            resource_type="nas",
            bound_node=proxy,
        )

    def test_backfills_only_persisted_fresh_evidence_online(self):
        Node = self.apps.get_model("node", "Node")
        SourceResource = self.apps.get_model("source", "SourceResource")

        self.assertEqual(
            Node.objects.get(name="fresh-agent").availability,
            "online",
        )
        self.assertEqual(
            Node.objects.get(name="stale-agent").availability,
            "offline",
        )
        self.assertEqual(
            SourceResource.objects.get(name="local-fresh").availability,
            "online",
        )
        self.assertEqual(
            SourceResource.objects.get(name="local-stale").availability,
            "offline",
        )
        self.assertEqual(
            SourceResource.objects.get(name="nas-proven").availability,
            "online",
        )
        self.assertEqual(
            SourceResource.objects.get(name="nas-unproven").availability,
            "offline",
        )


class CompleteSourceBackupPipelineMigrationTests(TransactionTestCase):
    migrate_from = [
        ("node", "0009_node_availability"),
        ("source", "0010_source_resource_availability"),
    ]
    migrate_to = [("source", "0012_backup_pipeline_query_indexes")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self._seed_history(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    # Only offline test-database restoration may cross the exclusive boundary.
    @patch.dict(os.environ, {"HFL_EXCLUSIVE_PLATFORM_MIGRATION": "1"})
    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _seed_history(self, apps):
        Organization = apps.get_model("iam", "Organization")
        Node = apps.get_model("node", "Node")
        Pipeline = apps.get_model("source", "SourceBackupPipelineEntry")
        org = Organization.objects.create(
            key="complete-pipeline-migration",
            name="Complete pipeline migration",
        )
        active_agent = Node.objects.create(
            organization=org,
            name="active-agent",
            role="agent",
            status="online",
        )
        deleted_agent = Node.objects.create(
            organization=org,
            name="deleted-pipeline-agent",
            role="agent",
            status="online",
        )
        Node.objects.create(
            organization=org,
            name="missing-pipeline-agent",
            role="agent",
            status="online",
        )
        Pipeline.objects.create(
            organization=org,
            source_kind="agent",
            ref_id=active_agent.id,
            step=2,
        )
        Pipeline.objects.create(
            organization=org,
            source_kind="agent",
            ref_id=deleted_agent.id,
            step=2,
            is_deleted=True,
            deleted_at=timezone.now(),
        )

    def test_upgrade_completes_and_restores_existing_pipeline_rows(self):
        Pipeline = self.apps.get_model("source", "SourceBackupPipelineEntry")
        rows = {
            row.source_name: row
            for row in Pipeline._base_manager.filter(source_kind="agent")
        }

        self.assertEqual(set(rows), {
            "active-agent",
            "deleted-pipeline-agent",
            "missing-pipeline-agent",
        })
        self.assertEqual(rows["active-agent"].step, 2)
        self.assertEqual(rows["deleted-pipeline-agent"].step, 1)
        self.assertFalse(rows["deleted-pipeline-agent"].is_deleted)
        self.assertIsNone(rows["deleted-pipeline-agent"].deleted_at)
        self.assertEqual(rows["missing-pipeline-agent"].step, 1)
