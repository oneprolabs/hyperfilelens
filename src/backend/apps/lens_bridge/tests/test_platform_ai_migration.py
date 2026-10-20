"""Exercise schema and data upgrades using the historical model registry."""

import os
import uuid
from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PlatformAIMigrationTests(TransactionTestCase):
    migrate_from = [("lens_bridge", "0045_remove_deployment_model_metadata")]
    migrate_to = [("lens_bridge", "0046_platform_ai_ownership")]

    def setUp(self):
        super().setUp()
        approval = patch.dict(os.environ, {"HFL_EXCLUSIVE_PLATFORM_MIGRATION": "1"})
        approval.start()
        self.addCleanup(approval.stop)
        executor = MigrationExecutor(connection)
        self.latest_targets = executor.loader.graph.leaf_nodes()
        executor.migrate(self.migrate_from)
        applied = MigrationExecutor(connection).loader
        self.old_apps = applied.project_state(list(applied.applied_migrations)).apps

    def tearDown(self):
        try:
            MigrationExecutor(connection).migrate(self.latest_targets)
        finally:
            super().tearDown()

    def upgrade(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        return executor.loader.project_state(self.migrate_to).apps

    def test_empty_install_does_not_create_a_platform_organization(self):
        with patch.dict(os.environ, {"HFL_EXCLUSIVE_PLATFORM_MIGRATION": "0"}):
            apps = self.upgrade()

        self.assertEqual(apps.get_model("iam", "Organization").objects.count(), 0)
        self.assertEqual(
            apps.get_model("lens_bridge", "PlatformAISettings").objects.count(), 0,
        )

    def test_old_installer_is_rejected_before_changing_an_initialized_database(self):
        org = self.old_apps.get_model("iam", "Organization").objects.create(
            key="existing", name="Existing tenant",
        )
        with patch.dict(os.environ, {"HFL_EXCLUSIVE_PLATFORM_MIGRATION": "0"}):
            with self.assertRaisesMessage(RuntimeError, "exclusive migration window"):
                self.upgrade()
        self.assertTrue(self.old_apps.get_model("iam", "Organization").objects.filter(pk=org.pk).exists())
        self.assertNotIn(
            self.migrate_to[0], MigrationExecutor(connection).loader.applied_migrations,
        )

    def test_existing_account_without_an_organization_also_requires_exclusivity(self):
        membership = self.old_apps.get_model("iam", "Membership")
        user = membership._meta.get_field("user").remote_field.model
        user.objects.create(username="existing-account")
        with patch.dict(os.environ, {"HFL_EXCLUSIVE_PLATFORM_MIGRATION": "0"}):
            with self.assertRaisesMessage(RuntimeError, "TARGET release"):
                self.upgrade()

    def test_upgrade_preserves_ids_defaults_and_tenant_models_and_can_reverse(self):
        Organization = self.old_apps.get_model("iam", "Organization")
        ModelLink = self.old_apps.get_model("lens_bridge", "LensOrgModelLink")
        OrgSettings = self.old_apps.get_model("lens_bridge", "LensOrgLink")
        Node = self.old_apps.get_model("node", "Node")
        LensGatewayLink = self.old_apps.get_model("lens_bridge", "LensGatewayLink")
        platform = Organization.objects.create(
            key="__platform_lens__", name="Platform Lens",
        )
        tenant = Organization.objects.create(key="tenant", name="Tenant")
        gateway = Node.objects.create(
            organization_id=platform.pk, name="Existing Public Gateway", role="gateway",
        )
        gateway_link = LensGatewayLink.objects.create(
            organization_id=platform.pk, gateway=gateway, scope="platform",
        )
        agent = ModelLink.objects.create(
            organization_id=platform.pk, sl_config_uuid=uuid.uuid4(), display_name="Agent",
        )
        multimodal = ModelLink.objects.create(
            organization_id=platform.pk, sl_config_uuid=uuid.uuid4(), display_name="Vision",
        )
        retired = ModelLink.objects.create(
            organization_id=platform.pk, sl_config_uuid=uuid.uuid4(), is_deleted=True,
        )
        private = ModelLink.objects.create(
            organization_id=tenant.pk, sl_config_uuid=uuid.uuid4(),
        )
        OrgSettings.objects.create(
            organization_id=platform.pk,
            default_agent_model_ref=agent.sl_config_uuid,
            default_multimodal_model_ref=multimodal.sl_config_uuid,
            assistant_name_prefix="existing-prefix",
        )
        OrgSettings.objects.create(
            organization_id=tenant.pk, default_agent_model_ref=private.sl_config_uuid,
        )

        apps = self.upgrade()

        NewModel = apps.get_model("lens_bridge", "LensOrgModelLink")
        for previous in (agent, multimodal, retired):
            current = NewModel.objects.get(pk=previous.pk)
            self.assertEqual(current.scope, "platform")
            self.assertIsNone(current.organization_id)
            self.assertEqual(current.sl_config_uuid, previous.sl_config_uuid)
            self.assertEqual(current.created_at, previous.created_at)
            self.assertEqual(current.updated_at, previous.updated_at)
            self.assertEqual(current.is_deleted, previous.is_deleted)
            self.assertEqual(current.display_name, previous.display_name)
        current_private = NewModel.objects.get(pk=private.pk)
        self.assertEqual(current_private.scope, "organization")
        self.assertEqual(current_private.organization_id, tenant.pk)
        settings = apps.get_model("lens_bridge", "PlatformAISettings").objects.get(pk=1)
        self.assertEqual(settings.default_agent_model_ref, agent.sl_config_uuid)
        self.assertEqual(settings.default_multimodal_model_ref, multimodal.sl_config_uuid)
        remaining = apps.get_model("lens_bridge", "LensOrgLink").objects.get(
            organization_id=platform.pk,
        )
        self.assertIsNone(remaining.default_agent_model_ref)
        self.assertIsNone(remaining.default_multimodal_model_ref)
        self.assertEqual(remaining.assistant_name_prefix, "existing-prefix")
        self.assertEqual(Organization.objects.count(), 2)
        gateway.refresh_from_db()
        gateway_link.refresh_from_db()
        self.assertEqual(gateway.organization_id, platform.pk)
        self.assertEqual(gateway_link.gateway_id, gateway.pk)
        self.assertEqual(gateway_link.organization_id, platform.pk)

        MigrationExecutor(connection).migrate(self.migrate_from)

        for previous in (agent, multimodal, retired):
            restored = ModelLink.objects.get(pk=previous.pk)
            self.assertEqual(restored.organization_id, platform.pk)
            self.assertEqual(restored.sl_config_uuid, previous.sl_config_uuid)
        restored_settings = OrgSettings.objects.get(organization_id=platform.pk)
        self.assertEqual(restored_settings.default_agent_model_ref, agent.sl_config_uuid)
        self.assertEqual(
            restored_settings.default_multimodal_model_ref, multimodal.sl_config_uuid,
        )
        self.assertEqual(
            OrgSettings.objects.get(
                organization_id=tenant.pk,
            ).default_agent_model_ref,
            private.sl_config_uuid,
        )

    def test_foreign_default_blocks_upgrade_without_reassigning_tenant_model(self):
        Organization = self.old_apps.get_model("iam", "Organization")
        ModelLink = self.old_apps.get_model("lens_bridge", "LensOrgModelLink")
        OrgSettings = self.old_apps.get_model("lens_bridge", "LensOrgLink")
        platform = Organization.objects.create(
            key="__platform_lens__", name="Platform Lens",
        )
        tenant = Organization.objects.create(key="tenant", name="Tenant")
        private = ModelLink.objects.create(
            organization_id=tenant.pk, sl_config_uuid=uuid.uuid4(),
        )
        defaults = OrgSettings.objects.create(
            organization_id=platform.pk, default_agent_model_ref=private.sl_config_uuid,
        )
        try:
            with self.assertRaisesMessage(RuntimeError, "platform-owned model"):
                self.upgrade()
            self.assertEqual(
                ModelLink.objects.get(pk=private.pk).organization_id, tenant.pk,
            )
            self.assertNotIn(
                self.migrate_to[0], MigrationExecutor(connection).loader.applied_migrations,
            )
        finally:
            OrgSettings.objects.filter(pk=defaults.pk).update(default_agent_model_ref=None)
