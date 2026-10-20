from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command, CommandError
from django.test import TestCase
from django.db import IntegrityError, transaction

from apps.iam.models import Membership, Organization
from apps.iam.profile_models import Profile


class SeedInitialDataCommandTests(TestCase):
    def test_renamed_installation_identity_is_reused_without_resetting_profile(self):
        call_command("seed_initial_data", stdout=StringIO())
        user = User.objects.get(email="admin@hyperfilelens.com")
        user.email = user.username = "renamed@example.test"
        user.set_password("ChangedPassword@123")
        user.save()
        Profile.objects.filter(user=user).update(language="en", timezone="Asia/Shanghai")
        call_command("seed_initial_data", stdout=StringIO())
        user.refresh_from_db()
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(user.email, "renamed@example.test")
        self.assertTrue(user.check_password("ChangedPassword@123"))
        self.assertEqual(Profile.objects.get(user=user).timezone, "Asia/Shanghai")

    def test_only_one_persistent_installation_identity_is_allowed(self):
        first = User.objects.create_user(username="root-one")
        second = User.objects.create_user(username="root-two")
        Profile.objects.update_or_create(user=first, defaults={"is_installation_admin": True})
        with self.assertRaises(IntegrityError), transaction.atomic():
            Profile.objects.update_or_create(user=second, defaults={"is_installation_admin": True})

    def test_existing_staff_requires_explicit_offline_adoption(self):
        user = User.objects.create_user(
            username="admin@hyperfilelens.com", email="admin@hyperfilelens.com",
            is_staff=True, password="KeepThisPassword@123",
        )
        with self.assertRaises(CommandError):
            call_command("seed_initial_data", stdout=StringIO())
        call_command("seed_initial_data", adopt_existing_user_id=user.pk, stdout=StringIO())
        self.assertTrue(Profile.objects.get(user=user).is_installation_admin)
        user.refresh_from_db()
        self.assertTrue(user.check_password("KeepThisPassword@123"))

    def test_seed_does_not_adopt_existing_organizations_or_create_another_identity(self):
        tenant = User.objects.create_user(username="existing-tenant")
        org = Organization.objects.create(key="existing", name="Existing tenant")
        Membership.objects.create(user=tenant, organization=org, role="owner")
        call_command("seed_initial_data", stdout=StringIO())
        root = User.objects.get(username="admin@hyperfilelens.com")
        self.assertFalse(Membership.objects.filter(user=root).exists())
        self.assertEqual(Membership.objects.count(), 1)
        with self.assertRaises(CommandError):
            call_command("seed_initial_data", adopt_existing_user_id=tenant.pk, stdout=StringIO())

    def test_seed_does_not_reactivate_or_promote_a_damaged_installation_account(self):
        call_command("seed_initial_data", stdout=StringIO())
        user = User.objects.get(username="admin@hyperfilelens.com")
        User.objects.filter(pk=user.pk).update(is_active=False)
        with self.assertRaises(CommandError):
            call_command("seed_initial_data", stdout=StringIO())
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_existing_ordinary_account_cannot_be_promoted_by_bootstrap_email(self):
        account = User.objects.create_user(
            username="admin@hyperfilelens.com", email="admin@hyperfilelens.com",
        )
        with self.assertRaises(CommandError):
            call_command("seed_initial_data", stdout=StringIO())
        account.refresh_from_db()
        self.assertFalse(account.is_staff)
        self.assertFalse(account.is_superuser)
        self.assertFalse(Profile.objects.filter(user=account, is_installation_admin=True).exists())

    @patch("apps.subscription.services.interface.enforce_license_quota")
    @patch(
        "apps.subscription.services.internal.organization_count.assert_organization_count_available"
    )
    def test_fresh_install_counts_initial_org_and_owner_against_instance_pool(
        self,
        organization_check,
        user_check,
    ):
        call_command(
            "seed_initial_data",
            admin_email="quota-admin@hyperfilelens.com",
            admin_password="Admin@123",
            org_name="Quota Bootstrap",
        )

        org = Membership.objects.select_related("organization").get().organization
        organization_check.assert_called_once_with(additional=1)
        user_check.assert_called_once_with(org, "max_users", additional=1)

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

        # EE installation authority is intrinsic; no ordinary preset is seeded.
        try:
            from apps.membership.models import PlatformRole, PlatformStaffRole
            from common.platform_authz import ADMIN_USERS_MANAGE, has_platform_permission

            self.assertFalse(PlatformStaffRole.objects.filter(user=user).exists())
            self.assertFalse(PlatformRole.objects.exists())
            self.assertTrue(has_platform_permission(user, ADMIN_USERS_MANAGE))
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
