"""Seed multi-tenant test data (roles across organizations, no MFA)."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.iam.models import Membership, Organization


class Command(BaseCommand):
    help = "Seed multi-tenant test users and organizations for manual QA"

    @transaction.atomic
    def handle(self, *args, **opts):
        User = get_user_model()

        org_acme, _ = Organization.objects.get_or_create(
            key="acme",
            defaults={"name": "Acme Corp", "is_active": True},
        )
        org_beta, _ = Organization.objects.get_or_create(
            key="beta-co",
            defaults={"name": "Beta Co", "is_active": True},
        )
        from apps.subscription.services.interface import initialize_organization_quota

        initialize_organization_quota(org_acme)
        initialize_organization_quota(org_beta)
        self.stdout.write(self.style.SUCCESS(f"Organizations: {org_acme.key}, {org_beta.key}"))

        def create_user(email: str, password: str):
            user, created = User.objects.get_or_create(
                username=email,
                defaults={"email": email, "is_staff": False, "is_superuser": False},
            )
            if created:
                user.set_password(password)
                user.save(update_fields=["password"])
                self.stdout.write(self.style.SUCCESS(f"  Created user: {email}"))
            else:
                self.stdout.write(f"  User exists: {email}")
            return user

        def add_membership(user, org, role):
            membership, created = Membership.objects.get_or_create(
                user=user,
                organization=org,
                defaults={"is_active": True},
            )
            if not created and not membership.is_active:
                membership.is_active = True
                membership.save(update_fields=["is_active"])
            # Role authority is EE MemberRole (or community default).
            from apps.iam.services.membership_service import sync_member_role

            sync_member_role(user_id=user.id, organization_id=org.id, role=role)
            self.stdout.write(f"    -> {org.key} as {role}")

        owner = create_user("owner@hyperfilelens.com", "Owner@12345")
        add_membership(owner, org_acme, Membership.Role.OWNER)

        admin = create_user("admin@hyperfilelens.com", "Admin@12345")
        add_membership(admin, org_acme, Membership.Role.ADMIN)

        operator = create_user("operator@hyperfilelens.com", "Operator@12345")
        add_membership(operator, org_acme, Membership.Role.OPERATOR)

        auditor = create_user("auditor@hyperfilelens.com", "Auditor@12345")
        add_membership(auditor, org_acme, Membership.Role.AUDITOR)

        multi = create_user("multi@hyperfilelens.com", "Multi@12345")
        add_membership(multi, org_acme, Membership.Role.OPERATOR)
        add_membership(multi, org_beta, Membership.Role.ADMIN)

        staff, _ = User.objects.get_or_create(
            username="staff@hyperfilelens.com",
            defaults={"email": "staff@hyperfilelens.com", "is_staff": True, "is_superuser": True},
        )
        staff.set_password("Staff@12345")
        staff.is_staff = True
        staff.is_superuser = True
        staff.save(update_fields=["password", "is_staff", "is_superuser"])
        add_membership(staff, org_acme, Membership.Role.ADMIN)

        self.stdout.write(self.style.SUCCESS("\nTest accounts:"))
        for email, password, note in [
            ("owner@hyperfilelens.com", "Owner@12345", "Acme owner"),
            ("admin@hyperfilelens.com", "Admin@12345", "Acme admin"),
            ("operator@hyperfilelens.com", "Operator@12345", "Acme operator"),
            ("auditor@hyperfilelens.com", "Auditor@12345", "Acme auditor (read-only)"),
            ("multi@hyperfilelens.com", "Multi@12345", "Acme operator + Beta admin"),
            ("staff@hyperfilelens.com", "Staff@12345", "Platform staff + Acme admin"),
        ]:
            self.stdout.write(f"  {email} / {password} — {note}")
