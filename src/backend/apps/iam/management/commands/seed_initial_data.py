"""Seed default organization, admin user, and global configuration templates."""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.iam.models import Membership, Organization
from apps.iam.profile_models import Profile
from apps.iam.services.registration_service import unique_org_key_for_email
from apps.storage.config import seed_global_config
from common.platform_authz import ensure_platform_role
from common.platform_staff import apply_platform_staff


class Command(BaseCommand):
    help = "Seed initial tenant/admin/templates (one org + one admin membership on fresh install)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--org-name",
            default=os.environ.get("SEED_ORG_NAME", "HyperFileLens"),
        )
        parser.add_argument(
            "--admin-email",
            default=os.environ.get("SEED_ADMIN_EMAIL", "admin@hyperfilelens.com"),
        )
        parser.add_argument(
            "--admin-password",
            default=os.environ.get("SEED_ADMIN_PASSWORD", "Admin@123"),
        )
        parser.add_argument(
            "--adopt-existing-user-id",
            type=int,
            help=(
                "Offline recovery only: explicitly establish an existing active staff "
                "account as the installation identity when no trusted identity exists. "
                "Does not change its email or password."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        org_name = str(opts["org_name"]).strip()
        admin_email = str(opts["admin_email"]).strip().lower()
        admin_password = str(opts["admin_password"])

        User = get_user_model()
        from apps.iam.services.member_onboarding import lock_identity_provisioning

        lock_identity_provisioning()
        identities = list(Profile.objects.select_for_update().filter(
            is_installation_admin=True,
        ).select_related("user"))
        if len(identities) > 1:
            raise CommandError("Multiple installation identities exist; resolve them offline.")
        adopt_id = opts.get("adopt_existing_user_id")
        if identities:
            user = identities[0].user
            if adopt_id is not None and adopt_id != user.pk:
                raise CommandError("An installation identity already exists; it cannot be replaced by seeding.")
        elif adopt_id is not None:
            user = User.objects.select_for_update().filter(pk=adopt_id).first()
            if user is None:
                raise CommandError("The explicitly selected recovery account does not exist.")
        else:
            if User.objects.filter(username=admin_email).exists() or User.objects.filter(
                email__iexact=admin_email,
            ).exists():
                raise CommandError(
                    "The bootstrap account already exists without a trusted identity. "
                    "For offline recovery, explicitly select an active staff account "
                    "with --adopt-existing-user-id."
                )
            user = User(
                username=admin_email, email=admin_email, is_active=True,
            )
            apply_platform_staff(user, True)
            user.set_password(admin_password)
            user.save()
        if not user.is_active or not user.is_staff:
            raise CommandError(
                "The installation identity must be an active staff account; "
                "repair its account state offline, not through initialization."
            )
        profile, _ = Profile.objects.get_or_create(user=user, defaults={
            "registration_completed": True, "registered_at": timezone.now(),
        })
        if not profile.is_installation_admin:
            profile.is_installation_admin = True
            profile.save(update_fields=["is_installation_admin"])
        # Establish intrinsic installation authority before resolving EE Roles.
        # A fresh installation must not create an ordinary preset Role.
        ensure_platform_role(user)

        if Organization.objects.exists():
            # Existing organizations are not adopted: global authority does not
            # make this account their member or Owner.
            seed_global_config()
            org = (
                Membership.objects.filter(user=user, is_active=True)
                .select_related("organization")
                .order_by("id")
                .values_list("organization__key", flat=True)
                .first()
            )
            self.stdout.write(
                self.style.WARNING(
                    f"Organizations already exist; ensured admin={user.email} org={org}"
                )
            )
            return

        from apps.subscription.services.internal.organization_count import (
            assert_organization_count_available,
        )

        assert_organization_count_available(additional=1)
        org = Organization.objects.create(
            key=unique_org_key_for_email(admin_email),
            name=org_name,
            is_active=True,
        )
        from apps.subscription.services.interface import (
            enforce_license_quota,
            initialize_organization_quota,
        )

        initialize_organization_quota(org)
        enforce_license_quota(org, "max_users", additional=1)
        Membership.objects.create(
            user=user,
            organization=org,
            role=Membership.Role.OWNER,
            is_active=True,
        )

        from apps.iam.services.role_seeding import seed_organization_roles

        seed_organization_roles(org)
        seed_global_config()

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded admin={user.email} org={org.key} "
                f"(organizations={Organization.objects.count()}, "
                f"memberships={Membership.objects.count()})"
            )
        )
