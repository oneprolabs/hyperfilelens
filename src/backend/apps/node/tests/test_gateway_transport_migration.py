"""Preserve existing Gateway credentials and routing during the schema upgrade."""

import os
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class GatewayTransportMigrationTests(TransactionTestCase):
    migrate_from = [("node", "0020_backfill_upgrade_operation_tasks")]
    migrate_to = [("node", "0021_gateway_transport_namespace")]

    def setUp(self):
        super().setUp()
        approval = patch.dict(os.environ, {"HFL_EXCLUSIVE_PLATFORM_MIGRATION": "1"})
        approval.start()
        self.addCleanup(approval.stop)
        executor = MigrationExecutor(connection)
        self.latest_targets = executor.loader.graph.leaf_nodes()
        executor.migrate(self.migrate_from)
        loader = MigrationExecutor(connection).loader
        self.old_apps = loader.project_state(list(loader.applied_migrations)).apps

    def tearDown(self):
        try:
            MigrationExecutor(connection).migrate(self.latest_targets)
        finally:
            super().tearDown()

    def upgrade(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        return executor.loader.project_state(self.migrate_to).apps

    def test_snapshots_only_public_resources_without_rotating_credentials(self):
        Organization = self.old_apps.get_model("iam", "Organization")
        Node = self.old_apps.get_model("node", "Node")
        Token = self.old_apps.get_model("node", "NodeToken")
        Credential = self.old_apps.get_model("node", "NodeCredential")
        Session = self.old_apps.get_model("node", "NodeInstallationSession")
        Link = self.old_apps.get_model("lens_bridge", "LensGatewayLink")
        org = Organization.objects.create(key="__platform_lens__", name="Infrastructure")
        tenant = Organization.objects.create(key="tenant", name="Tenant")
        public = Node.objects.create(
            organization_id=org.pk, name="Public", role="gateway", installation_id="same-id",
        )
        retired = Node.objects.create(
            organization_id=org.pk, name="Retired", role="gateway", is_deleted=True,
        )
        for node in (public, retired):
            Link.objects.create(
                organization_id=org.pk, gateway_id=node.pk, scope="platform",
                is_deleted=node.is_deleted,
            )
        private = Node.objects.create(
            organization_id=tenant.pk, name="Private", role="gateway",
        )
        Link.objects.create(
            organization_id=tenant.pk, gateway_id=private.pk, scope="organization",
        )
        token = Token.objects.create(
            organization_id=org.pk, role="gateway", gateway_scope="platform",
            token=uuid.uuid4().hex, used_at=timezone.now(), is_active=False,
        )
        private_token = Token.objects.create(
            organization_id=tenant.pk, role="gateway", token=uuid.uuid4().hex,
        )
        credential = Credential.objects.create(
            organization_id=org.pk, node_id=public.pk, enrollment_token_id=token.pk,
            role="gateway", installation_id="same-id",
            secret_prefix="existing", secret_hash="a" * 64,
        )
        session = Session.objects.create(
            organization_id=org.pk, enrollment_token_id=token.pk, role="gateway",
            installation_id="resumable-install", secret_prefix="existing-session",
            secret_hash="b" * 64,
            idle_expires_at=timezone.now() + timedelta(hours=1),
            absolute_expires_at=timezone.now() + timedelta(hours=2),
        )

        apps = self.upgrade()

        NewNode = apps.get_model("node", "Node")
        NewToken = apps.get_model("node", "NodeToken")
        for before in (public, retired):
            after = NewNode.objects.get(pk=before.pk)
            self.assertEqual(after.transport_namespace, org.key)
            self.assertEqual(after.organization_id, before.organization_id)
            self.assertEqual(after.installation_id, before.installation_id)
            self.assertEqual(after.updated_at, before.updated_at)
            self.assertEqual(after.is_deleted, before.is_deleted)
        self.assertEqual(NewNode.objects.get(pk=private.pk).transport_namespace, "")
        migrated_token = NewToken.objects.get(pk=token.pk)
        self.assertEqual(migrated_token.transport_namespace, org.key)
        self.assertEqual(migrated_token.token, token.token)
        self.assertEqual(migrated_token.used_at, token.used_at)
        self.assertEqual(migrated_token.updated_at, token.updated_at)
        self.assertFalse(migrated_token.is_active)
        self.assertEqual(NewToken.objects.get(pk=private_token.pk).transport_namespace, "")
        credential.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(credential.secret_hash, "a" * 64)
        self.assertEqual(credential.installation_id, "same-id")
        self.assertEqual(session.secret_hash, "b" * 64)
        self.assertEqual(session.enrollment_token_id, token.pk)
        self.assertEqual(Organization.objects.count(), 2)

    def test_inconsistent_ownership_stops_upgrade_before_rewriting_routing(self):
        Organization = self.old_apps.get_model("iam", "Organization")
        Node = self.old_apps.get_model("node", "Node")
        Link = self.old_apps.get_model("lens_bridge", "LensGatewayLink")
        first = Organization.objects.create(key="first", name="First")
        second = Organization.objects.create(key="second", name="Second")
        node = Node.objects.create(
            organization_id=first.pk, name="Mismatch", role="gateway",
        )
        link = Link.objects.create(
            organization_id=second.pk, gateway_id=node.pk, scope="platform",
        )
        try:
            with self.assertRaisesMessage(RuntimeError, "inconsistent public Gateway"):
                self.upgrade()
            self.assertNotIn(
                self.migrate_to[0], MigrationExecutor(connection).loader.applied_migrations,
            )
            node.refresh_from_db()
            self.assertEqual(node.organization_id, first.pk)
        finally:
            Link.objects.filter(pk=link.pk).update(organization_id=first.pk)
