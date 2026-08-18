"""License API tests."""

import base64
import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.subscription.models import License, LicenseHistory, MachineCode
from apps.subscription.services.internal.crypto import (
    LICENSE_SECRET_KEY,
    generate_activation_code,
)
from apps.subscription.services.internal.license_ops import _determine_change_type
from apps.subscription.services.interface import get_or_create_machine_code
from common.extension_spi import get_authz_provider
from common.platform_authz import ensure_platform_role


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


def _signed_payload(payload: dict) -> str:
    data = dict(payload)
    canonical = json.dumps(data, sort_keys=True)
    data["signature"] = hashlib.sha256(
        (canonical + LICENSE_SECRET_KEY).encode()
    ).hexdigest()
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    return f"HFL-ACT-{encoded}"


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
        if get_authz_provider() is not None:
            self.user.is_staff = True
            self.user.save(update_fields=["is_staff"])
            ensure_platform_role(self.user)
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
        self.assertIn("machine_code", resp.data)
        self.assertIn("limits", resp.data)
        self.assertIn("usage", resp.data)
        provider = get_quota_provider()
        if provider is None:
            self.assertFalse(resp.data["is_valid"])
            # Community: informational DEFAULT_LIMITS, no hard enforcement.
            self.assertEqual(resp.data["limits"]["max_users"], 500)
            self.assertFalse(resp.data.get("enforcement_enabled", True))
        else:
            # EE: built-in entitlement + default organization plan are unlimited.
            self.assertTrue(resp.data["is_valid"])
            self.assertEqual(resp.data.get("entitlement_source"), "builtin_unlimited")
            self.assertEqual(resp.data["limits"]["max_users"], -1)
            self.assertTrue(resp.data.get("enforcement_enabled"))

    def test_change_type_treats_unlimited_as_highest_limit(self):
        existing = License(max_users=100)

        upgraded, _reason = _determine_change_type(
            existing,
            {"max_users": -1},
            None,
        )
        existing.max_users = -1
        downgraded, _reason = _determine_change_type(
            existing,
            {"max_users": 100},
            None,
        )

        self.assertEqual(upgraded, License.ChangeType.UPGRADE)
        self.assertEqual(downgraded, License.ChangeType.DOWNGRADE)

    def test_change_type_includes_feature_entitlement_changes(self):
        existing = License(max_users=100, features=["quota_governance"])

        upgraded, _reason = _determine_change_type(
            existing,
            {"max_users": 100},
            None,
            ["ai_insights", "quota_governance"],
        )
        downgraded, _reason = _determine_change_type(
            existing,
            {"max_users": 100},
            None,
            [],
        )

        self.assertEqual(upgraded, License.ChangeType.UPGRADE)
        self.assertEqual(downgraded, License.ChangeType.DOWNGRADE)

    def test_entitlement_downgrade_takes_priority_over_expiry_extension(self):
        existing = License(
            max_users=100,
            features=["*"],
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )

        change_type, _reason = _determine_change_type(
            existing,
            {"max_users": 50},
            datetime(2028, 1, 1, tzinfo=timezone.utc),
            ["quota_governance"],
        )

        self.assertEqual(change_type, License.ChangeType.DOWNGRADE)

    def test_change_type_treats_perpetual_validity_as_highest(self):
        existing = License(
            max_users=100,
            features=["quota_governance"],
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
        upgraded, _reason = _determine_change_type(
            existing,
            {"max_users": 100},
            None,
            ["quota_governance"],
        )
        existing.expires_at = None
        downgraded, _reason = _determine_change_type(
            existing,
            {"max_users": 100},
            datetime(2028, 1, 1, tzinfo=timezone.utc),
            ["quota_governance"],
        )

        self.assertEqual(upgraded, License.ChangeType.UPGRADE)
        self.assertEqual(downgraded, License.ChangeType.DOWNGRADE)

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
        license_obj = License.objects.get(organization=self.org)
        self.assertEqual(license_obj.features, ["*"])

    @override_settings(DEBUG=True)
    @patch(
        "apps.subscription.api.views.license.write_audit_log",
        side_effect=RuntimeError("audit unavailable"),
    )
    def test_activate_rolls_back_when_audit_write_fails(self, _audit):
        with self.assertRaises(RuntimeError):
            self.client.post(
                "/api/v1/subscription/licenses/activate/",
                {"activation_code": "DEV-UNLIMITED"},
                format="json",
                **self._headers(),
            )

        self.assertFalse(License.objects.filter(organization=self.org).exists())

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
        self.assertEqual(current.data.get("entitlement_source"), "license")
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
                "max_public_gateway_capacity_bytes": 500 * 1024**2,
                "max_source_nas": 11,
                "max_object_storage": 12,
                "max_target_nas": 13,
                "max_standalone_disk": 14,
                "max_protected_sources": 15,
            },
            features=["quota_governance"],
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
        self.assertEqual(
            lic.max_public_gateway_capacity_bytes,
            500 * 1024**2,
        )
        self.assertEqual(lic.max_source_nas, 11)
        self.assertEqual(lic.max_object_storage, 12)
        self.assertEqual(lic.max_target_nas, 13)
        self.assertEqual(lic.max_standalone_disk, 14)
        self.assertEqual(lic.max_protected_sources, 15)
        self.assertEqual(lic.features, ["quota_governance"])
        self.assertEqual(resp.data["license"]["features"], ["quota_governance"])
        self.assertEqual(lic.get_limits()["max_protected_sources"], 15)
        self.assertTrue(lic.signature)

    def test_signed_license_rejects_sub_mib_capacity_increment(self):
        machine_code = get_or_create_machine_code(
            organization=self.org,
            user=self.user,
        )
        code = generate_activation_code(
            license_key="INVALID-CAPACITY-INCREMENT",
            machine_code=machine_code,
            limits={"max_public_gateway_capacity_bytes": 1},
        )

        response = self.client.post(
            "/api/v1/subscription/licenses/activate/",
            {"activation_code": code},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("whole MiB increments", response.data["message"])
        self.assertFalse(License.objects.filter(organization=self.org).exists())

    def test_signed_license_renewal_replaces_persisted_signature(self):
        machine_code = get_or_create_machine_code(organization=self.org, user=self.user)
        first_code = generate_activation_code(
            license_key="SIGNATURE-KEY-001",
            machine_code=machine_code,
            limits={"max_users": 10},
        )
        second_code = generate_activation_code(
            license_key="SIGNATURE-KEY-002",
            machine_code=machine_code,
            limits={"max_users": 20},
        )

        first = self.client.post(
            "/api/v1/subscription/licenses/activate/",
            {"activation_code": first_code},
            format="json",
            **self._headers(),
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        first_signature = License.objects.get(organization=self.org).signature

        second = self.client.post(
            "/api/v1/subscription/licenses/activate/",
            {"activation_code": second_code},
            format="json",
            **self._headers(),
        )

        self.assertEqual(second.status_code, status.HTTP_200_OK)
        renewed = License.objects.get(organization=self.org)
        self.assertTrue(first_signature)
        self.assertNotEqual(renewed.signature, first_signature)
        self.assertEqual(renewed.max_users, 20)

    def test_malformed_activation_payloads_return_bad_request(self):
        machine_code = get_or_create_machine_code(organization=self.org, user=self.user)
        now = datetime.now(timezone.utc)
        malformed_codes = (
            "HFL-ACT-" + base64.b64encode(b"[]").decode(),
            _signed_payload(
                {
                    "license_key": "MISSING-ISSUED-AT",
                    "machine_code": machine_code,
                    "limits": {},
                    "expires_at": None,
                }
            ),
            _signed_payload(
                {
                    "license_key": "NAIVE-ISSUED-AT",
                    "machine_code": machine_code,
                    "limits": {},
                    "issued_at": now.replace(tzinfo=None).isoformat(),
                    "expires_at": None,
                }
            ),
        )

        for activation_code in malformed_codes:
            with self.subTest(activation_code=activation_code[:40]):
                response = self.client.post(
                    "/api/v1/subscription/licenses/activate/",
                    {"activation_code": activation_code},
                    format="json",
                    **self._headers(),
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertFalse(License.objects.filter(organization=self.org).exists())

    def test_signed_ai_token_limit_uses_standard_meter_key(self):
        machine_code = get_or_create_machine_code(organization=self.org, user=self.user)
        code = generate_activation_code(
            license_key="AI-TOKEN-KEY-001",
            machine_code=machine_code,
            limits={"ai_tokens": 123},
        )

        response = self.client.post(
            "/api/v1/subscription/licenses/activate/",
            {"activation_code": code},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            License.objects.get(organization=self.org).ai_insights_quota,
            123,
        )

    def test_signed_license_rejects_invalid_limit_values(self):
        machine_code = get_or_create_machine_code(
            organization=self.org,
            user=self.user,
        )
        invalid_limits = (
            ("max_users", -2),
            ("max_users", 1.5),
            ("max_users", 2**31),
            ("max_users", True),
            ("max_tasks", 1.5),
        )

        for index, (field, invalid_limit) in enumerate(invalid_limits):
            with self.subTest(field=field, limit=invalid_limit):
                code = generate_activation_code(
                    license_key=f"INVALID-LIMIT-{index}",
                    machine_code=machine_code,
                    limits={field: invalid_limit},
                )
                response = self.client.post(
                    "/api/v1/subscription/licenses/activate/",
                    {"activation_code": code},
                    format="json",
                    **self._headers(),
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertFalse(License.objects.filter(organization=self.org).exists())

        invalid_shape = generate_activation_code(
            license_key="INVALID-LIMITS-SHAPE",
            machine_code=machine_code,
            limits=[],
        )
        response = self.client.post(
            "/api/v1/subscription/licenses/activate/",
            {"activation_code": invalid_shape},
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(License.objects.filter(organization=self.org).exists())

    def test_enterprise_org_admin_cannot_activate_instance_license(self):
        if get_authz_provider() is None:
            self.skipTest("Enterprise AuthzProvider is not loaded")
        user_model = get_user_model()
        org_admin = user_model.objects.create_user(
            username="org-admin-license@test.local",
            email="org-admin-license@test.local",
            password="test-pass",
        )
        Membership.objects.create(
            user=org_admin,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        client = APIClient()
        client.force_authenticate(user=org_admin)

        response = client.post(
            "/api/v1/subscription/licenses/activate/",
            {"activation_code": "DEV-UNLIMITED"},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(License.objects.filter(organization=self.org).exists())

    @override_settings(DEBUG=True)
    def test_enterprise_org_admin_cannot_read_instance_license_secrets(self):
        if get_authz_provider() is None:
            self.skipTest("Enterprise AuthzProvider is not loaded")
        activation = self.client.post(
            "/api/v1/subscription/licenses/activate/",
            {"activation_code": "DEV-UNLIMITED"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(activation.status_code, status.HTTP_200_OK)

        user_model = get_user_model()
        org_admin = user_model.objects.create_user(
            username="org-admin-license-read@test.local",
            email="org-admin-license-read@test.local",
            password="test-pass",
        )
        Membership.objects.create(
            user=org_admin,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        client = APIClient()
        client.force_authenticate(user=org_admin)

        current = client.get(
            "/api/v1/subscription/licenses/current/",
            **self._headers(),
        )
        machine_code = client.get(
            "/api/v1/subscription/licenses/machine_code/",
            **self._headers(),
        )
        history = client.get(
            "/api/v1/subscription/licenses/history/",
            **self._headers(),
        )

        self.assertEqual(current.status_code, status.HTTP_200_OK)
        self.assertFalse(current.data["can_manage_instance_license"])
        self.assertIsNone(current.data["machine_code"])
        self.assertNotIn("license_key", current.data["license"])
        self.assertNotIn("machine_code", current.data["license"])
        self.assertNotIn("features", current.data["license"])
        self.assertEqual(machine_code.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(history.status_code, status.HTTP_200_OK)
        self.assertEqual(history.data, {"count": 0, "results": []})

    def test_legacy_signed_code_without_features_keeps_all_enterprise_features(self):
        machine_code = get_or_create_machine_code(organization=self.org, user=self.user)
        code = generate_activation_code(
            license_key="LEGACY-KEY-001",
            machine_code=machine_code,
            limits={"max_users": 100},
        )

        response = self.client.post(
            "/api/v1/subscription/licenses/activate/",
            {"activation_code": code},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(License.objects.get(organization=self.org).features, ["*"])

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
        # EE enforces both boundaries; current built-in entitlement and default
        # organization plan are unlimited.
        self.assertTrue(payload["is_valid"])
        self.assertTrue(payload.get("enforcement_enabled"))

    def test_validate_rejects_non_numeric_amount(self):
        response = self.client.get(
            "/api/v1/subscription/licenses/validate/",
            {"quota_type": "users", "amount": "not-a-number"},
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("amount", str(response.data))

    def test_validate_rejects_negative_amount(self):
        response = self.client.get(
            "/api/v1/subscription/licenses/validate/",
            {"quota_type": "users", "amount": "-1"},
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("amount", str(response.data))

    def test_history_empty(self):
        resp = self.client.get(
            "/api/v1/subscription/licenses/history/",
            **self._headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 0)
