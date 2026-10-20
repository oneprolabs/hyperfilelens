import os
from unittest.mock import patch

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class GatewayIdentityMigrationTests(TransactionTestCase):
    migrate_from = [
        ("lens_bridge", "0020_alter_lensgatewaylink_sidecar_status"),
        ("node", "0006_node_network_inventory"),
        ("restore", "0004_alter_restorerecorditem_status"),
    ]
    migrate_to = [
        ("lens_bridge", "0022_workspace_binding"),
        ("node", "0006_node_network_inventory"),
        ("restore", "0004_alter_restorerecorditem_status"),
    ]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self._seed_existing_gateway_data(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    # Only offline test-database restoration may cross the exclusive boundary.
    @patch.dict(os.environ, {"HFL_EXCLUSIVE_PLATFORM_MIGRATION": "1"})
    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _seed_existing_gateway_data(self, apps):
        app_label, model_name = settings.AUTH_USER_MODEL.split(".")
        User = apps.get_model(app_label, model_name)
        Organization = apps.get_model("iam", "Organization")
        LensGatewayLink = apps.get_model("lens_bridge", "LensGatewayLink")
        LensKnowledgeSource = apps.get_model(
            "lens_bridge",
            "LensKnowledgeSource",
        )
        Node = apps.get_model("node", "Node")

        owner = User.objects.create(
            username="migration-owner@example.test",
            email="migration-owner@example.test",
        )
        tenant = Organization.objects.create(
            key="migration-tenant",
            name="Migration tenant",
        )
        platform = Organization.objects.create(
            key="migration-platform",
            name="Migration platform",
        )

        private_gateway = Node.objects.create(
            organization=tenant,
            name="Migration private gateway",
            role="gateway",
        )
        private_link = LensGatewayLink.objects.create(
            organization=tenant,
            gateway=private_gateway,
            scope="user",
            origin="user",
            owner_user=None,
        )
        private_source = LensKnowledgeSource.objects.create(
            organization=tenant,
            gateway=private_gateway,
            name="Migration private source",
            source_path="/data/private",
            created_by=owner,
        )

        platform_gateway = Node.objects.create(
            organization=platform,
            name="Migration platform gateway",
            role="gateway",
        )
        platform_link = LensGatewayLink.objects.create(
            organization=platform,
            gateway=platform_gateway,
            scope="platform",
            origin="platform",
            owner_user=owner,
        )
        platform_source = LensKnowledgeSource.objects.create(
            organization=tenant,
            gateway=platform_gateway,
            name="Migration platform source",
            source_path="/data/platform",
            created_by=owner,
        )

        self.owner_id = owner.id
        self.private_link_id = private_link.id
        self.private_source_id = private_source.id
        self.platform_link_id = platform_link.id
        self.platform_source_id = platform_source.id

    def test_existing_sources_and_gateway_owners_are_backfilled(self):
        LensGatewayLink = self.apps.get_model(
            "lens_bridge",
            "LensGatewayLink",
        )
        LensKnowledgeSource = self.apps.get_model(
            "lens_bridge",
            "LensKnowledgeSource",
        )

        private_source = LensKnowledgeSource.objects.get(pk=self.private_source_id)
        platform_source = LensKnowledgeSource.objects.get(pk=self.platform_source_id)
        private_link = LensGatewayLink.objects.get(pk=self.private_link_id)
        platform_link = LensGatewayLink.objects.get(pk=self.platform_link_id)

        self.assertEqual(private_source.gateway_link_id, self.private_link_id)
        self.assertEqual(platform_source.gateway_link_id, self.platform_link_id)
        self.assertEqual(private_link.owner_user_id, self.owner_id)
        self.assertIsNone(platform_link.owner_user_id)
        self.assertFalse(
            LensKnowledgeSource._meta.get_field("gateway_link").null
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            LensGatewayLink.objects.create(
                organization_id=private_link.organization_id,
                gateway_id=platform_link.gateway_id,
                scope="user",
                origin="user",
                owner_user_id=None,
            )
