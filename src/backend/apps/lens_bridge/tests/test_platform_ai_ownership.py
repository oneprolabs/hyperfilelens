"""Platform AI resources must not acquire a synthetic tenant identity."""

import uuid
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.exceptions import NotFound, ValidationError

from apps.iam.models import Membership, Organization
from apps.iam.services.membership_service import authoritative_role
from apps.lens_bridge.models import LensOrgLink, LensOrgModelLink, PlatformAISettings
from apps.lens_bridge.services import org_models, provisioning
from apps.lens_bridge.services.model_defaults import get_model_defaults


class PlatformAIOwnershipTests(TestCase):
    def test_platform_model_and_defaults_do_not_create_an_organization(self):
        reference = uuid.uuid4()

        link = org_models.register_org_model(org=None, sl_config_uuid=reference)
        defaults = get_model_defaults(None)

        self.assertEqual(link.scope, LensOrgModelLink.Scope.PLATFORM)
        self.assertIsNone(link.organization_id)
        self.assertIsInstance(defaults, PlatformAISettings)
        self.assertEqual(defaults.default_agent_model_ref, reference)
        self.assertEqual(Organization.objects.count(), 0)
        self.assertEqual(LensOrgLink.objects.count(), 0)

    def test_platform_ai_initialization_does_not_prevent_default_org_seeding(self):
        org_models.register_org_model(org=None, sl_config_uuid=uuid.uuid4())

        call_command("seed_initial_data", stdout=StringIO())

        self.assertEqual(Organization.objects.count(), 1)
        organization = Organization.objects.get()
        self.assertEqual(organization.name, "HyperFileLens")
        self.assertEqual(get_user_model().objects.count(), 1)
        membership = Membership.objects.get(
            organization=organization,
            user__profile__is_installation_admin=True,
            is_active=True,
        )
        self.assertEqual(authoritative_role(membership), Membership.Role.OWNER)

    def test_database_enforces_both_scope_and_organization(self):
        org = Organization.objects.create(key="tenant", name="Tenant")
        for scope, organization in (
            (LensOrgModelLink.Scope.PLATFORM, org),
            (LensOrgModelLink.Scope.ORGANIZATION, None),
            ("unknown", None),
            ("unknown", org),
        ):
            with self.subTest(scope=scope, organization=organization):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    LensOrgModelLink.objects.create(
                        scope=scope, organization=organization, sl_config_uuid=uuid.uuid4(),
                    )

    def test_platform_settings_are_a_database_singleton(self):
        self.assertEqual(get_model_defaults(None).pk, 1)
        self.assertEqual(get_model_defaults(None).pk, 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PlatformAISettings.objects.create(pk=2)
        self.assertEqual(PlatformAISettings.objects.count(), 1)

    def test_deleting_a_tenant_does_not_delete_platform_ai(self):
        tenant = Organization.objects.create(key="tenant", name="Tenant")
        platform_model = org_models.register_org_model(
            org=None, sl_config_uuid=uuid.uuid4(),
        )
        tenant_model = org_models.register_org_model(
            org=tenant, sl_config_uuid=uuid.uuid4(),
        )

        tenant.delete()

        self.assertTrue(LensOrgModelLink.objects.filter(pk=platform_model.pk).exists())
        self.assertFalse(LensOrgModelLink.objects.filter(pk=tenant_model.pk).exists())
        self.assertEqual(
            get_model_defaults(None).default_agent_model_ref,
            platform_model.sl_config_uuid,
        )

    def test_platform_and_tenant_management_selectors_are_disjoint(self):
        tenant = Organization.objects.create(key="tenant", name="Tenant")
        platform_model = org_models.register_org_model(
            org=None, sl_config_uuid=uuid.uuid4(),
        )
        tenant_model = org_models.register_org_model(
            org=tenant, sl_config_uuid=uuid.uuid4(),
        )

        with self.assertRaises(NotFound):
            org_models.require_org_model(None, tenant_model.sl_config_uuid)
        with self.assertRaises(NotFound):
            org_models.require_org_model(tenant, platform_model.sl_config_uuid)
        with self.assertRaises(ValidationError):
            org_models.validate_default_model_ref(None, tenant_model.sl_config_uuid)
        with self.assertRaises(ValidationError):
            org_models.validate_default_model_ref(tenant, platform_model.sl_config_uuid)

    def test_selected_models_can_be_platform_or_own_but_not_another_tenants(self):
        tenant = Organization.objects.create(key="tenant", name="Tenant")
        other = Organization.objects.create(key="other", name="Other")
        platform_model = org_models.register_org_model(
            org=None, sl_config_uuid=uuid.uuid4(),
        )
        own_model = org_models.register_org_model(org=tenant, sl_config_uuid=uuid.uuid4())
        foreign_model = org_models.register_org_model(org=other, sl_config_uuid=uuid.uuid4())

        org_models.validate_agent_model_ref(tenant, platform_model.sl_config_uuid)
        org_models.validate_agent_model_ref(tenant, own_model.sl_config_uuid)
        with self.assertRaises(ValidationError):
            org_models.validate_agent_model_ref(tenant, foreign_model.sl_config_uuid)

    def test_configured_defaults_use_platform_without_creating_an_extra_org(self):
        tenant = Organization.objects.create(key="tenant", name="Tenant")
        platform_model = org_models.register_org_model(
            org=None, sl_config_uuid=uuid.uuid4(),
        )

        self.assertEqual(
            provisioning.configured_default_model_refs_for_org(tenant),
            (str(platform_model.sl_config_uuid), None),
        )
        self.assertEqual(Organization.objects.count(), 1)

    @patch("apps.lens_bridge.services.org_models.sl_client.request_json")
    def test_every_tenant_can_use_active_platform_models_without_a_grant(self, request_json):
        first = Organization.objects.create(key="first", name="First")
        second = Organization.objects.create(key="second", name="Second")
        active = org_models.register_org_model(org=None, sl_config_uuid=uuid.uuid4())
        inactive = org_models.register_org_model(org=None, sl_config_uuid=uuid.uuid4())
        private = org_models.register_org_model(org=first, sl_config_uuid=uuid.uuid4())
        request_json.return_value = [
            {"uuid": str(active.sl_config_uuid), "is_active": True},
            {"uuid": str(inactive.sl_config_uuid), "is_active": False},
            {"uuid": str(private.sl_config_uuid), "is_active": True},
        ]

        for tenant, expected in (
            (first, {str(active.sl_config_uuid), str(private.sl_config_uuid)}),
            (second, {str(active.sl_config_uuid)}),
        ):
            with self.subTest(tenant=tenant.key):
                rows = org_models.active_llm_configs_available_to_org(tenant)
                self.assertEqual({row["uuid"] for row in rows}, expected)
        self.assertEqual(Organization.objects.count(), 2)

    def test_model_uuid_cannot_be_reassigned_by_creating_another_scope(self):
        tenant = Organization.objects.create(key="tenant", name="Tenant")
        platform_model = org_models.register_org_model(
            org=None, sl_config_uuid=uuid.uuid4(),
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            org_models.register_org_model(
                org=tenant, sl_config_uuid=platform_model.sl_config_uuid,
            )
        platform_model.refresh_from_db()
        self.assertIsNone(platform_model.organization_id)
