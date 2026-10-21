"""Concurrent instance-license activation (advisory lock)."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings

from apps.iam.models import Membership, Organization
from apps.subscription.models import License, LicenseHistory, MachineCode
from apps.subscription.services.interface import activate_license
from common.extension_spi import get_authz_provider


@override_settings(DEBUG=True)
class ConcurrentLicenseActivateTests(TransactionTestCase):
    """Uses real commits so advisory locks serialize across threads.

    Avoid Django's full flush teardown — keepdb + leftover ledger FKs break
    TRUNCATE. Clean only rows created by this test.
    """

    def setUp(self):
        self.suffix = uuid.uuid4().hex[:8]
        LicenseHistory.objects.all().delete()
        License.objects.all().delete()
        MachineCode.objects.all().delete()
        try:
            from apps.subscription_gov.models import Quota

            Quota.objects.all().delete()
        except Exception:  # pragma: no cover
            pass
        User = get_user_model()
        self.user_a = User.objects.create_user(
            username=f"lic-race-a-{self.suffix}@test.local",
            email=f"lic-race-a-{self.suffix}@test.local",
            password="test-pass",
        )
        self.user_b = User.objects.create_user(
            username=f"lic-race-b-{self.suffix}@test.local",
            email=f"lic-race-b-{self.suffix}@test.local",
            password="test-pass",
        )
        self.org_a = Organization.objects.create(
            key=f"lic-race-a-{self.suffix}", name="Race A"
        )
        self.org_b = Organization.objects.create(
            key=f"lic-race-b-{self.suffix}", name="Race B"
        )
        if get_authz_provider() is not None:
            from apps.iam.permission_catalog import PERMISSION_KEYS
            from apps.membership.models import CustomOrganizationRole

            for organization in (self.org_a, self.org_b):
                CustomOrganizationRole.objects.create(
                    organization=organization, key="admin", name="Administrator",
                    permissions=sorted(PERMISSION_KEYS),
                )
        Membership.objects.create(
            user=self.user_a,
            organization=self.org_a,
            role=Membership.Role.ADMIN,
        )
        Membership.objects.create(
            user=self.user_b,
            organization=self.org_b,
            role=Membership.Role.ADMIN,
        )

    def _fixture_teardown(self):
        try:
            from apps.subscription_gov.models import Quota

            Quota.objects.filter(
                organization_id__in=[self.org_a.id, self.org_b.id]
            ).delete()
        except Exception:  # pragma: no cover
            pass
        LicenseHistory.objects.filter(organization_id__in=[self.org_a.id, self.org_b.id]).delete()
        License.objects.filter(organization_id__in=[self.org_a.id, self.org_b.id]).delete()
        MachineCode.objects.filter(organization_id__in=[self.org_a.id, self.org_b.id]).delete()
        Membership.objects.filter(organization_id__in=[self.org_a.id, self.org_b.id]).delete()
        Organization.objects.filter(id__in=[self.org_a.id, self.org_b.id]).delete()
        get_user_model().objects.filter(
            id__in=[self.user_a.id, self.user_b.id]
        ).delete()

    def test_two_orgs_race_yields_single_customer_license(self):
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def _activate(org, user):
            barrier.wait(timeout=5)
            try:
                return activate_license(
                    organization=org,
                    user=user,
                    activation_code="DEV-UNLIMITED",
                )
            except Exception as exc:  # noqa: BLE001 — collect per-thread outcomes
                errors.append(exc)
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(_activate, self.org_a, self.user_a),
                pool.submit(_activate, self.org_b, self.user_b),
            ]
            results = [f.result(timeout=30) for f in as_completed(futures)]

        successes = [r for r in results if r is not None]
        self.assertEqual(len(successes), 1, f"successes={successes!r} errors={errors!r}")
        self.assertEqual(
            License.objects.filter(
                organization_id__in=[self.org_a.id, self.org_b.id]
            ).count(),
            1,
        )
        self.assertTrue(
            any("already active" in str(e).lower() for e in errors),
            errors,
        )
