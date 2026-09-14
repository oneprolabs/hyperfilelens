from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class GatewayOrganizationOwnershipMigrationTests(TransactionTestCase):
    migrate_from = [
        ("lens_bridge", "0040_session_analysis_type"),
        ("node", "0020_backfill_upgrade_operation_tasks"),
    ]
    migrate_to = [
        ("lens_bridge", "0041_gateway_organization_ownership"),
        ("node", "0020_backfill_upgrade_operation_tasks"),
    ]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self._seed_legacy_gateway(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _seed_legacy_gateway(self, apps):
        app_label, model_name = settings.AUTH_USER_MODEL.split(".")
        User = apps.get_model(app_label, model_name)
        Organization = apps.get_model("iam", "Organization")
        Node = apps.get_model("node", "Node")
        LensGatewayLink = apps.get_model("lens_bridge", "LensGatewayLink")

        creator = User.objects.create(
            username="legacy-gateway-creator@example.test",
            email="legacy-gateway-creator@example.test",
        )
        organization = Organization.objects.create(
            key="legacy-gateway-organization",
            name="Legacy gateway organization",
        )
        gateway = Node.objects.create(
            organization=organization,
            name="Legacy private gateway",
            role="gateway",
        )
        link = LensGatewayLink.objects.create(
            organization=organization,
            gateway=gateway,
            scope="user",
            origin="user",
            owner_user=creator,
        )
        self.creator_id = creator.id
        self.organization_id = organization.id
        self.gateway_id = gateway.id
        self.link_id = link.id

    def test_installer_is_backfilled_without_rewriting_legacy_scope(self):
        LensGatewayLink = self.apps.get_model("lens_bridge", "LensGatewayLink")

        link = LensGatewayLink.objects.get(pk=self.link_id)

        self.assertEqual(link.created_by_id, self.creator_id)
        self.assertEqual(link.owner_user_id, self.creator_id)
        self.assertEqual(link.scope, "user")

    def test_schema_accepts_ownerless_organization_scope(self):
        LensGatewayLink = self.apps.get_model("lens_bridge", "LensGatewayLink")
        Node = self.apps.get_model("node", "Node")
        gateway = Node.objects.create(
            organization_id=self.organization_id,
            name="Organization-scope private gateway",
            role="gateway",
        )

        link = LensGatewayLink.objects.create(
            organization_id=self.organization_id,
            gateway_id=gateway.id,
            scope="organization",
            origin="user",
            owner_user_id=None,
            created_by_id=None,
        )

        self.assertEqual(link.scope, "organization")
