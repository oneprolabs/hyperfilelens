"""Seed default organization, admin user, and global configuration templates."""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
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

    @transaction.atomic
    def handle(self, *args, **opts):
        org_name = str(opts["org_name"]).strip()
        admin_email = str(opts["admin_email"]).strip().lower()
        admin_password = str(opts["admin_password"])

        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=admin_email,
            defaults={
                "email": admin_email,
                "is_active": True,
                "is_staff": False,
                "is_superuser": False,
            },
        )
        user.email = admin_email
        user.is_active = True
        apply_platform_staff(user, True)
        if created:
            user.set_password(admin_password)
        user.save()
        # EE AuthZ: bare is_staff is not enough for Admin Console / smoke verify.
        ensure_platform_role(user)

        Profile.objects.update_or_create(
            user=user,
            defaults={
                "registration_completed": True,
                "registered_at": timezone.now(),
                "language": "en",
                "timezone": "UTC",
            },
        )

        if Organization.objects.exists():
            membership = (
                Membership.objects.filter(user=user, is_active=True)
                .select_related("organization")
                .order_by("id")
                .first()
            )
            if membership is None:
                org = Organization.objects.order_by("id").first()
                from apps.subscription.services.interface import enforce_license_quota

                enforce_license_quota(org, "max_users", additional=1)
                Membership.objects.create(
                    user=user,
                    organization=org,
                    role=Membership.Role.OWNER,
                    is_active=True,
                )
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

        seed_global_config()

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded admin={user.email} org={org.key} "
                f"(organizations={Organization.objects.count()}, "
                f"memberships={Membership.objects.count()})"
            )
        )
