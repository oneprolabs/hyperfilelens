"""Host platform AuthZ facade (staff bootstrap without AuthzProvider)."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from common.extension_spi import (
    clear_providers_for_tests,
    register_authz_provider,
    restore_providers_for_tests,
)
from common.platform_authz import (
    ADMIN_INSTANCE_LICENSE_ACTIVATE,
    COMMERCE_ORG_QUOTA_MANAGE,
    INFRA_AI_MODELS_MANAGE,
    ROLE_BILLING_OPERATOR,
    ROLE_INFRA_OPERATOR,
    ROLE_PLATFORM_ADMIN,
    authorize_platform,
    has_platform_permission,
    list_platform_permissions,
    permissions_for_role,
)
from common.errors import AppError


class PlatformAuthzCatalogTest(SimpleTestCase):
    def test_role_permission_map(self):
        admin = permissions_for_role(ROLE_PLATFORM_ADMIN)
        infra = permissions_for_role(ROLE_INFRA_OPERATOR)
        billing = permissions_for_role(ROLE_BILLING_OPERATOR)
        self.assertIn(INFRA_AI_MODELS_MANAGE, infra)
        self.assertNotIn(COMMERCE_ORG_QUOTA_MANAGE, infra)
        self.assertIn(COMMERCE_ORG_QUOTA_MANAGE, billing)
        self.assertNotIn(INFRA_AI_MODELS_MANAGE, billing)
        self.assertIn(ADMIN_INSTANCE_LICENSE_ACTIVATE, admin)
        self.assertNotIn(ADMIN_INSTANCE_LICENSE_ACTIVATE, infra)
        self.assertNotIn(ADMIN_INSTANCE_LICENSE_ACTIVATE, billing)


class PlatformAuthzFacadeTest(TestCase):
    def setUp(self):
        self._prev = clear_providers_for_tests()
        self.staff = User.objects.create_user(
            username="plat-staff@test.com",
            email="plat-staff@test.com",
            password="Pass1234",
            is_staff=True,
        )
        self.user = User.objects.create_user(
            username="plat-user@test.com",
            email="plat-user@test.com",
            password="Pass1234",
            is_staff=False,
        )

    def tearDown(self):
        restore_providers_for_tests(self._prev)

    def test_staff_bootstrap_without_provider(self):
        self.assertTrue(has_platform_permission(self.staff, COMMERCE_ORG_QUOTA_MANAGE))
        self.assertFalse(has_platform_permission(self.user, COMMERCE_ORG_QUOTA_MANAGE))
        self.assertIn(COMMERCE_ORG_QUOTA_MANAGE, list_platform_permissions(self.staff))

    def test_provider_overrides_staff_bootstrap(self):
        class _Stub:
            def has_platform_permission(self, user, action):
                return action == INFRA_AI_MODELS_MANAGE

            def list_platform_permissions(self, user):
                return [INFRA_AI_MODELS_MANAGE]

            def get_platform_role(self, user):
                return ROLE_INFRA_OPERATOR

        register_authz_provider(_Stub())
        self.assertTrue(has_platform_permission(self.staff, INFRA_AI_MODELS_MANAGE))
        self.assertFalse(has_platform_permission(self.staff, COMMERCE_ORG_QUOTA_MANAGE))
        with self.assertRaises(AppError) as ctx:
            authorize_platform(self.staff, COMMERCE_ORG_QUOTA_MANAGE)
        self.assertEqual(ctx.exception.code, "AUTH.FORBIDDEN")

    def test_provider_without_platform_methods_fails_closed(self):
        class _Partial:
            """Org-only stub; missing platform AuthZ methods must not fall open."""

        register_authz_provider(_Partial())
        self.assertFalse(has_platform_permission(self.staff, COMMERCE_ORG_QUOTA_MANAGE))
        self.assertEqual(list_platform_permissions(self.staff), [])
        from common.platform_authz import get_platform_role

        self.assertIsNone(get_platform_role(self.staff))
