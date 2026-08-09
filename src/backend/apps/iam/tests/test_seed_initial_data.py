from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from apps.iam.models import Membership, Organization
from apps.iam.profile_models import Profile


class SeedInitialDataCommandTests(TestCase):
    def test_fresh_install_creates_single_org_and_membership(self):
        out = StringIO()
        call_command(
            "seed_initial_data",
            admin_email="admin@hyperfilelens.com",
            admin_password="Admin@123",
            org_name="HyperFileLens",
            stdout=out,
        )

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Organization.objects.count(), 1)
        self.assertEqual(Membership.objects.count(), 1)

        user = User.objects.get(email="admin@hyperfilelens.com")
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("Admin@123"))

        membership = Membership.objects.get(user=user)
        from apps.iam.services.membership_service import authoritative_role
        self.assertEqual(authoritative_role(membership), Membership.Role.OWNER)
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.organization.name, "HyperFileLens")

        profile = Profile.objects.get(user=user)
        self.assertTrue(profile.registration_completed)

        # EE AuthZ (when loaded): seed must attach PlatformStaffRole for Console.
        try:
            from apps.membership.models import PlatformStaffRole
            from common.platform_authz import ROLE_PLATFORM_ADMIN

            self.assertEqual(
                PlatformStaffRole.objects.get(user=user).role,
                ROLE_PLATFORM_ADMIN,
            )
        except ImportError:
            pass

    def test_rerun_is_idempotent_when_org_exists(self):
        call_command(
            "seed_initial_data",
            admin_email="admin@hyperfilelens.com",
            admin_password="Admin@123",
        )

        user = User.objects.get(email="admin@hyperfilelens.com")
        user.set_password("OperatorChangedPassword@123")
        user.save(update_fields=["password"])

        call_command(
            "seed_initial_data",
            admin_email="admin@hyperfilelens.com",
            admin_password="Admin@123",
        )

        self.assertEqual(Organization.objects.count(), 1)
        self.assertEqual(Membership.objects.count(), 1)

        user = User.objects.get(email="admin@hyperfilelens.com")
        self.assertTrue(user.check_password("OperatorChangedPassword@123"))
        self.assertFalse(user.check_password("Admin@123"))
