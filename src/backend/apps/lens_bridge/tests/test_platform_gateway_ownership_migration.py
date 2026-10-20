"""Exercise the actual platform ownership/data migration, not just fresh models."""

import os
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class PlatformGatewayOwnershipMigrationTests(TransactionTestCase):
    before = [
        ("lens_bridge", "0047_platform_gateway_schema"),
        ("node", "0022_platform_node_ownership"),
        ("restore", "0009_platform_restore_execution"),
        ("audit", "0006_platform_audit_scope"),
        ("monitor", "0008_platform_telemetry_scope"),
    ]
    after = [("lens_bridge", "0048_platform_gateway_ownership")]

    def setUp(self):
        super().setUp()
        approval = patch.dict(os.environ, {"HFL_EXCLUSIVE_PLATFORM_MIGRATION": "1"})
        approval.start()
        self.addCleanup(approval.stop)
        executor = MigrationExecutor(connection)
        self.latest = executor.loader.graph.leaf_nodes()
        executor.migrate(self.before)
        loader = MigrationExecutor(connection).loader
        self.old = loader.project_state(list(loader.applied_migrations)).apps
        Organization = self.old.get_model("iam", "Organization")
        self.legacy = Organization.objects.create(key="__platform_lens__", name="Platform Lens")
        self.tenant = Organization.objects.create(key="business-tenant", name="Business Tenant")
        self.gateway = self.old.get_model("node", "Node").objects.create(
            organization_id=self.legacy.pk, role="gateway", name="Existing Gateway",
            installation_id="installed-once",
        )
        self.token = self.old.get_model("node", "NodeToken").objects.create(
            organization_id=self.legacy.pk, role="gateway", gateway_scope="platform",
            token=uuid.uuid4().hex, used_at=timezone.now() - timedelta(days=2),
        )
        self.credential = self.old.get_model("node", "NodeCredential").objects.create(
            organization_id=self.legacy.pk, node_id=self.gateway.pk,
            enrollment_token_id=self.token.pk, role="gateway",
            secret_prefix="existing", secret_hash="a" * 64,
            installation_id="installed-once",
        )
        self.link = self.old.get_model("lens_bridge", "LensGatewayLink").objects.create(
            organization_id=self.legacy.pk, gateway_id=self.gateway.pk, scope="platform",
            sl_lensnode_uuid=uuid.uuid4(), config_json={"lensnode_token": "unchanged"},
            workspace_root="", capacity_bytes=123456789,
            chat_prepare_concurrency=3, chat_queue_capacity=17,
        )
        self.source = self.old.get_model("lens_bridge", "LensKnowledgeSource").objects.create(
            organization_id=self.tenant.pk, gateway_id=self.gateway.pk,
            gateway_link_id=self.link.pk, name="Existing prepared data",
            source_path="/original/data", backup_source_snapshot_id=7,
        )
        self.root = f"/workspace/org-{self.legacy.pk}/data"
        self.binding = self.old.get_model("lens_bridge", "LensWorkspaceBinding").objects.create(
            organization_id=self.tenant.pk, knowledge_source_id=self.source.pk,
            gateway_link_id=self.link.pk, execution_organization_id=self.legacy.pk,
            execution_node_id=self.gateway.pk, workspace_kind="managed_restore",
            workspace_root=self.root, relative_path="existing-workspace",
            state="ready", identity_status="ready", capacity_accounted_bytes=987654,
            capacity_accounting_status="exact",
        )
        self.task = self.old.get_model("task", "Task").objects.create(
            organization_id=self.tenant.pk, task_type="insight_workspace_restore",
            display_name="Existing restore", trigger_type="system",
        )
        self.node_task = self.old.get_model("node", "NodeTask").objects.create(
            organization_id=self.legacy.pk, requesting_organization_id=self.legacy.pk,
            node_id=self.gateway.pk, parent_task_id=self.task.pk,
            kind="lens.ks.prepare", watchdog_deadline_at=timezone.now() + timedelta(hours=1),
            payload={"workspace_uid": str(self.binding.workspace_uid)},
        )

    def tearDown(self):
        try:
            MigrationExecutor(connection).migrate(self.latest)
        finally:
            super().tearDown()

    def upgrade(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        return executor.loader.project_state(self.after).apps

    def test_partially_upgraded_database_still_requires_exclusive_approval(self):
        with patch.dict(os.environ, {"HFL_EXCLUSIVE_PLATFORM_MIGRATION": "0"}):
            with self.assertRaisesMessage(RuntimeError, "exclusive migration window"):
                self.upgrade()
        self.gateway.refresh_from_db()
        self.credential.refresh_from_db()
        self.assertEqual(self.gateway.organization_id, self.legacy.pk)
        self.assertEqual(self.credential.organization_id, self.legacy.pk)
        self.assertNotIn(self.after[0], MigrationExecutor(connection).loader.applied_migrations)

    def test_moves_in_place_preserving_credentials_paths_capacity_and_requester(self):
        event = self.old.get_model("monitor", "OperationalEvent").objects.create(
            organization_id=self.legacy.pk, event_type="node.offline",
            category="infrastructure", severity="warning", title="Existing event",
            resource_type="gateway", resource_id=str(self.gateway.pk), dedup_key="existing-event",
        )
        audit = self.old.get_model("audit", "AuditLog").objects.create(
            organization_id=self.legacy.pk, action="node.lifecycle.upgrade",
        )

        apps = self.upgrade()

        Organization = apps.get_model("iam", "Organization")
        self.assertFalse(Organization.objects.filter(pk=self.legacy.pk).exists())
        self.assertTrue(Organization.objects.filter(pk=self.tenant.pk).exists())
        node = apps.get_model("node", "Node").objects.get(pk=self.gateway.pk)
        self.assertIsNone(node.organization_id)
        self.assertEqual(node.scope, "platform")
        self.assertEqual(node.installation_id, "installed-once")
        self.assertEqual(node.transport_namespace, "__platform_lens__")
        self.assertEqual(node.updated_at, self.gateway.updated_at)
        credential = apps.get_model("node", "NodeCredential").objects.get(pk=self.credential.pk)
        self.assertIsNone(credential.organization_id)
        self.assertEqual(credential.secret_hash, self.credential.secret_hash)
        self.assertEqual(credential.updated_at, self.credential.updated_at)
        token = apps.get_model("node", "NodeToken").objects.get(pk=self.token.pk)
        self.assertIsNone(token.organization_id)
        self.assertEqual(token.transport_namespace, "__platform_lens__")
        self.assertEqual(token.token, self.token.token)
        self.assertEqual(token.used_at, self.token.used_at)
        link = apps.get_model("lens_bridge", "LensGatewayLink").objects.get(pk=self.link.pk)
        self.assertIsNone(link.organization_id)
        self.assertEqual(link.sl_lensnode_uuid, self.link.sl_lensnode_uuid)
        self.assertEqual(link.config_json, self.link.config_json)
        self.assertEqual(link.workspace_root, self.root)
        self.assertEqual(link.capacity_bytes, 123456789)
        self.assertEqual((link.chat_prepare_concurrency, link.chat_queue_capacity), (3, 17))
        binding = apps.get_model("lens_bridge", "LensWorkspaceBinding").objects.get(pk=self.binding.pk)
        self.assertEqual(binding.organization_id, self.tenant.pk)
        self.assertIsNone(binding.execution_organization_id)
        self.assertEqual(binding.workspace_root, self.root)
        self.assertEqual(binding.relative_path, "existing-workspace")
        self.assertEqual(binding.workspace_uid, self.binding.workspace_uid)
        self.assertEqual(binding.capacity_accounted_bytes, 987654)
        node_task = apps.get_model("node", "NodeTask").objects.get(pk=self.node_task.pk)
        self.assertIsNone(node_task.organization_id)
        self.assertEqual(node_task.requesting_organization_id, self.tenant.pk)
        self.assertEqual(node_task.parent_task_id, self.task.pk)
        audit.refresh_from_db()
        event.refresh_from_db()
        self.assertIsNone(audit.organization_id)
        self.assertIsNone(event.organization_id)

    def test_ambiguous_business_ownership_rolls_back_the_entire_data_migration(self):
        Source = self.old.get_model("lens_bridge", "LensKnowledgeSource")
        ambiguous = Source.objects.create(
            organization_id=self.legacy.pk, gateway_id=self.gateway.pk,
            gateway_link_id=self.link.pk, name="Needs an explicit tenant",
            source_path="/ambiguous",
        )
        try:
            with self.assertRaisesMessage(RuntimeError, "still has records"):
                self.upgrade()
            self.gateway.refresh_from_db()
            self.credential.refresh_from_db()
            self.assertEqual(self.gateway.organization_id, self.legacy.pk)
            self.assertEqual(self.gateway.scope, "organization")
            self.assertEqual(self.credential.organization_id, self.legacy.pk)
            self.assertEqual(self.credential.secret_hash, "a" * 64)
            self.assertNotIn(self.after[0], MigrationExecutor(connection).loader.applied_migrations)
        finally:
            Source.objects.filter(pk=ambiguous.pk).delete()

    def test_unknown_task_requester_is_not_assigned_to_the_default_organization(self):
        NodeTask = self.old.get_model("node", "NodeTask")
        unknown = NodeTask.objects.create(
            organization_id=self.legacy.pk, requesting_organization_id=self.legacy.pk,
            node_id=self.gateway.pk, kind="unrecognized.business.work",
            watchdog_deadline_at=timezone.now() + timedelta(hours=1),
        )
        try:
            with self.assertRaisesMessage(RuntimeError, "ambiguous requesting tenant"):
                self.upgrade()
            self.gateway.refresh_from_db()
            self.assertEqual(self.gateway.organization_id, self.legacy.pk)
        finally:
            NodeTask.objects.filter(pk=unknown.pk).update(requesting_organization_id=self.tenant.pk)

    def test_private_installation_with_the_same_installation_id_remains_private(self):
        private = self.old.get_model("node", "Node").objects.create(
            organization_id=self.tenant.pk, role="gateway", name="Private",
            installation_id="installed-once",
        )
        private_token = self.old.get_model("node", "NodeToken").objects.create(
            organization_id=self.tenant.pk, role="gateway", token=uuid.uuid4().hex,
        )

        apps = self.upgrade()

        private.refresh_from_db()
        private_token.refresh_from_db()
        self.assertEqual(private.organization_id, self.tenant.pk)
        self.assertEqual(private.scope, "organization")
        self.assertEqual(private_token.organization_id, self.tenant.pk)
        public = apps.get_model("node", "Node").objects.get(pk=self.gateway.pk)
        self.assertIsNone(public.organization_id)
        self.assertNotEqual(public.pk, private.pk)
