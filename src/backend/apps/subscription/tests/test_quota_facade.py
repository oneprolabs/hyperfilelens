"""Host smoke: create-path quota helpers + QuotaProvider SPI contract."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.iam.models import Membership, Organization
from apps.subscription.services.interface import (
    enforce_license_quota,
    enforce_node_role_quota,
    enforce_repository_type_quota,
)
from apps.subscription.services.quota import validate_quota
from common.extension_spi import (
    clear_providers_for_tests,
    register_quota_provider,
    restore_providers_for_tests,
)


class _BlockingProvider:
    """Minimal QuotaProvider stub matching the formal SPI."""

    def check_quota(self, organization, resource_type, additional=1):
        from common.errors import AppError

        raise AppError(
            code="SUBSCRIPTION.QUOTA_EXCEEDED",
            status=403,
            title="blocked",
            diagnostic="blocked",
            meta={"quota_type": resource_type},
        )

    def get_limits(self, organization):
        return {
            "max_users": 7,
            "max_gateways": 2,
            "gateway_select_max_files": 1,
            "gateway_select_max_bytes": -1,
        }

    def validate_quota(self, organization, quota_type, amount=1):
        return {
            "is_valid": False,
            "quota_type": quota_type,
            "limit": 7,
            "used": 7,
            "message": "blocked",
            "enforcement_enabled": True,
        }

    def on_license_activated(self, organization, license_obj):
        return None


class HostQuotaFacadeTests(TestCase):
    def setUp(self):
        self._spi_previous = clear_providers_for_tests()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="hq@test.local",
            email="hq@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(key="hq-org", name="HQ Org")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.OWNER,
        )

    def tearDown(self):
        restore_providers_for_tests(self._spi_previous)

    def test_helpers_noop_without_provider(self):
        self.assertIsNone(enforce_license_quota(self.org, "max_users", additional=1))
        self.assertIsNone(enforce_node_role_quota(organization=self.org, role="agent"))
        self.assertIsNone(enforce_repository_type_quota(organization=self.org, repo_type="s3"))

    def test_validate_quota_informational_without_provider(self):
        result = validate_quota(self.org, "max_users", amount=1)
        self.assertTrue(result["is_valid"])
        self.assertFalse(result["enforcement_enabled"])

    def test_helpers_delegate_to_provider(self):
        register_quota_provider(_BlockingProvider())
        from common.errors import AppError

        with self.assertRaises(AppError) as ctx:
            enforce_license_quota(self.org, "max_users", additional=1)
        self.assertEqual(ctx.exception.code, "SUBSCRIPTION.QUOTA_EXCEEDED")

    def test_validate_and_limits_delegate_to_provider(self):
        register_quota_provider(_BlockingProvider())
        from common.errors import AppError
        from apps.subscription.services.quota import assert_gateway_select_within_limits

        result = validate_quota(self.org, "max_users", amount=1)
        self.assertFalse(result["is_valid"])
        self.assertTrue(result["enforcement_enabled"])
        with self.assertRaises(AppError) as ctx:
            assert_gateway_select_within_limits(
                organization=self.org,
                file_count=10,
                size_bytes=0,
            )
        self.assertEqual(ctx.exception.code, "SUBSCRIPTION.QUOTA_EXCEEDED")
