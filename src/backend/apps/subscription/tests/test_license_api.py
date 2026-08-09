"""License API tests."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.subscription.models import License, LicenseHistory, MachineCode
from apps.subscription.services.internal.crypto import generate_activation_code
from apps.subscription.services.interface import get_or_create_machine_code


def _clear_license_state() -> None:
    """Reset Host licenses and any EE EffectiveQuota rows left by prior activates."""
    LicenseHistory.objects.all().delete()
    License.objects.all().delete()
    MachineCode.objects.all().delete()
    try:
        from apps.subscription_gov.models import Quota

        Quota.objects.all().delete()
    except Exception:  # pragma: no cover — community Host has no ee ledger
        pass


class LicenseApiTests(TestCase):
    def setUp(self):
        # keepdb can leave licenses/quotas from earlier runs; pin-to-host must start clean.
        _clear_license_state()
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="license-api@test.local",
            email="license-api@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(key="license-test-org", name="License Test Org")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.client.force_authenticate(user=self.user)

    def _headers(self):
        return {"HTTP_X_ORG_KEY": self.org.key}

    def test_current_without_license(self):
        from common.extension_spi import get_quota_provider

        resp = self.client.get(
            "/api/v1/subscription/licenses/current/",
            **self._headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data["is_valid"])
        self.assertIn("machine_code", resp.data)
        self.assertIn("limits", resp.data)
        self.assertIn("usage", resp.data)
        provider = get_quota_provider()
        if provider is None:
            # Community: informational DEFAULT_LIMITS, no hard enforcement.
            self.assertEqual(resp.data["limits"]["max_users"], 500)
            self.assertFalse(resp.data.get("enforcement_enabled", True))
        else:
            # EE: missing org Quota → share instance default pool + hard enforce.
            from apps.subscription.constants import DEFAULT_LIMITS

            self.assertEqual(resp.data["limits"]["max_users"], DEFAULT_LIMITS["max_users"])
            self.assertTrue(resp.data.get("enforcement_enabled"))

    @override_settings(DEBUG=True)
    def test_activate_dev_license(self):
        resp = self.client.post(
            "/api/v1/subscription/licenses/activate/",
            {"activation_code": "DEV-UNLIMITED"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["success"])
        self.assertTrue(License.objects.filter(organization=self.org).exists())

    @override_settings(DEBUG=True)
    def test_secondary_org_instance_license_and_activate_pin(self):
        """Secondary tenants share the host grant; only the host may activate."""
        activate = self.client.post(
            "/api/v1/subscription/licenses/activate/",
            {"activation_code": "DEV-UNLIMITED"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(activate.status_code, status.HTTP_200_OK)

        org2 = Organization.objects.create(key="license-secondary-org", name="Secondary Org")
        Membership.objects.create(
            user=self.user,
            organization=org2,
            role=Membership.Role.ADMIN,
        )
        headers2 = {"HTTP_X_ORG_KEY": org2.key}

        current = self.client.get(
            "/api/v1/subscription/licenses/current/",
            **headers2,
        )
        self.assertEqual(current.status_code, status.HTTP_200_OK)
        self.assertTrue(current.data["is_valid"])
        self.assertTrue(current.data.get("instance_shared"))
        self.assertIn("license", current.data)
        self.assertNotIn("license_key", current.data["license"] or {})
        self.assertNotIn("organization_key", current.data["license"] or {})
        self.assertEqual(current.data.get("organization_name"), org2.name)

        blocked = self.client.post(
            "/api/v1/subscription/licenses/activate/",
            {"activation_code": "DEV-UNLIMITED"},
            format="json",
            **headers2,
        )
        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already active", (blocked.data.get("message") or "").lower())
        self.assertFalse(License.objects.filter(organization=org2).exists())

        renew = self.client.post(
            "/api/v1/subscription/licenses/activate/",
            {"activation_code": "DEV-UNLIMITED"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(renew.status_code, status.HTTP_200_OK)

    def test_activate_with_signed_code(self):
        machine_code = get_or_create_machine_code(organization=self.org, user=self.user)
        code = generate_activation_code(
            license_key="TEST-KEY-001",
            machine_code=machine_code,
            limits={
                "max_users": 100,
                "max_nodes": 10,
                "max_storage_gb": 200,
            },
        )
        resp = self.client.post(
            "/api/v1/subscription/licenses/activate/",
            {"activation_code": code},
            format="json",
            **self._headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        lic = License.objects.get(organization=self.org)
        self.assertEqual(lic.max_users, 100)

    def test_validate_always_allows_in_dev(self):
        from common.extension_spi import get_quota_provider

        resp = self.client.get(
            "/api/v1/subscription/licenses/validate/",
            {"quota_type": "users", "amount": "9999"},
            **self._headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        payload = resp.data.get("data", resp.data) if isinstance(resp.data, dict) else resp.data
        provider = get_quota_provider()
        if provider is None:
            self.assertTrue(payload["is_valid"])
            return
        # EE always enforces EffectiveQuota (unsigned default pool or signed license).
        # Unallocated org meters are 0 → large request is denied.
        self.assertFalse(payload["is_valid"])
        self.assertTrue(payload.get("enforcement_enabled"))

    def test_history_empty(self):
        resp = self.client.get(
            "/api/v1/subscription/licenses/history/",
            **self._headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 0)
