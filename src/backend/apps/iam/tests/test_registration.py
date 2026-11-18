from django.contrib.auth.models import User
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from unittest.mock import patch

from apps.iam.email_verification_models import EmailVerificationCode
from apps.iam.models import Membership, Organization
from apps.iam.services.registration_service import (
    complete_user_registration,
    generate_registration_verification_code,
    provision_registered_user_tenant,
)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    HFL_EMAIL_SIGNUP_ENABLED=True,
)
@patch.dict(
    "os.environ",
    {
        "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
        "HFL_EMAIL_SIGNUP_ENABLED": "true",
        "HFL_ENTERPRISE_REGISTRATION_ENABLED": "true",
        "HFL_PERSONAL_REGISTRATION_ENABLED": "false",
    },
)
class RegistrationApiTests(APITestCase):
    def setUp(self):
        patcher = patch(
            "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _send_code_payload(self, email: str) -> dict:
        return {"email": email}

    def test_send_code_creates_pending_user_and_email(self):
        email = "new-user@example.com"
        response = self.client.post(
            reverse("email_register_send_code"),
            self._send_code_payload(email),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "0000")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(email, mail.outbox[0].to)

        user = User.objects.get(email=email)
        self.assertFalse(user.is_active)
        self.assertEqual(EmailVerificationCode.objects.filter(user=user, is_used=False).count(), 1)

    def test_confirm_registers_user_and_provisions_org(self):
        email = "owner@example.com"
        user = User.objects.create_user(
            username="owner",
            email=email,
            password="unused",
            is_active=False,
        )
        code, error = generate_registration_verification_code(user)
        self.assertIsNone(error)

        response = self.client.post(
            reverse("email_register_confirm"),
            {"email": email, "code": code, "password": "Pass1234"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "0000")
        self.assertIn("organization", response.data["data"])

        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("Pass1234"))

        org_key = response.data["data"]["organization"]["key"]
        org = Organization.objects.get(key=org_key)
        self.assertEqual(org.name, email)
        membership = Membership.objects.get(user=user, organization=org)
        from apps.iam.services.membership_service import authoritative_role
        self.assertEqual(authoritative_role(membership), Membership.Role.OWNER)
        self.assertTrue(membership.is_active)
        self.assertEqual(Membership.objects.filter(user=user).count(), 1)
        self.assertEqual(Organization.objects.filter(memberships__user=user).count(), 1)

    def test_confirm_enterprise_uses_the_requested_organization_name(self):
        email = "named-owner@example.com"
        user = User.objects.create_user(
            username="named-owner",
            email=email,
            password="unused",
            is_active=False,
        )
        code, error = generate_registration_verification_code(user)
        self.assertIsNone(error)

        response = self.client.post(
            reverse("email_register_confirm"),
            {
                "email": email,
                "code": code,
                "password": "Pass1234",
                "registration_mode": "enterprise",
                "organization_name": "Example Inc.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["organization"]["name"], "Example Inc.")

    def test_confirm_rejects_unknown_registration_mode(self):
        response = self.client.post(
            reverse("email_register_confirm"),
            {
                "email": "unknown-mode@example.com",
                "code": "123456",
                "password": "Pass1234",
                "registration_mode": "unknown",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["error"]["error_code"],
            "INVALID_REGISTRATION_MODE",
        )

    def test_confirm_rejects_invalid_organization_names(self):
        for name in (True, {"name": "Example"}, ["Example"], "a" * 201):
            with self.subTest(name=name):
                response = self.client.post(
                    reverse("email_register_confirm"),
                    {
                        "email": "invalid-name@example.com",
                        "code": "123456",
                        "password": "Pass1234",
                        "registration_mode": "enterprise",
                        "organization_name": name,
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("organization_name", response.data["error"]["fields"])

    @patch(
        "apps.iam.auth.views.registration.personal_registration_enabled",
        return_value=True,
    )
    @patch("common.extension_spi.get_registration_provider")
    def test_personal_provisioning_failure_rolls_back_activation_and_code(
        self, get_provider, _personal_enabled,
    ):
        from common.extension_spi import RegistrationUnavailableError

        user = User.objects.create_user(
            username="personal-pending",
            email="personal-pending@example.com",
            is_active=False,
        )
        code, error = generate_registration_verification_code(user)
        self.assertIsNone(error)
        get_provider.return_value.provision_personal_registration.side_effect = (
            RegistrationUnavailableError("Storage unavailable")
        )

        response = self.client.post(
            reverse("email_register_confirm"),
            {
                "email": user.email,
                "code": code,
                "password": "Pass1234",
                "registration_mode": "personal",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["error"]["error_code"], "PERSONAL_REGISTRATION_UNAVAILABLE",
        )
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertTrue(
            EmailVerificationCode.objects.filter(user=user, is_used=False).exists(),
        )
        self.assertFalse(Membership.objects.filter(user=user).exists())

    def test_registration_options_keep_personal_disabled_without_extension(self):
        response = self.client.get("/api/v1/meta/registration-options")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["enterprise"]["enabled"])
        self.assertFalse(response.data["personal"]["available"])

    @patch(
        "apps.configuration.services.runtime_settings.enterprise_registration_enabled",
        return_value=False,
    )
    @patch(
        "apps.configuration.services.runtime_settings.personal_registration_enabled",
        return_value=True,
    )
    def test_registration_options_keep_modes_independent(
        self,
        _personal_enabled,
        _enterprise_enabled,
    ):
        response = self.client.get("/api/v1/meta/registration-options")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["enterprise"]["enabled"])
        self.assertTrue(response.data["personal"]["enabled"])
        self.assertFalse(response.data["personal"]["available"])

    def test_confirm_invalid_code_returns_code_field_error(self):
        email = "invalid-code@example.com"
        User.objects.create_user(
            username="invalid-code",
            email=email,
            password="unused",
            is_active=False,
        )

        response = self.client.post(
            reverse("email_register_confirm"),
            {"email": email, "code": "000000", "password": "Pass1234"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["error_code"], "INVALID_CODE")
        self.assertIn("code", response.data["error"]["fields"])

    def test_confirm_missing_user_returns_email_field_error(self):
        response = self.client.post(
            reverse("email_register_confirm"),
            {"email": "missing@example.com", "code": "123456", "password": "Pass1234"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["error"]["error_code"], "USER_NOT_FOUND")
        self.assertIn("email", response.data["error"]["fields"])

    def test_confirm_active_user_returns_email_field_error(self):
        email = "already-active@example.com"
        User.objects.create_user(
            username="already-active",
            email=email,
            password="Pass1234",
            is_active=True,
        )

        response = self.client.post(
            reverse("email_register_confirm"),
            {"email": email, "code": "123456", "password": "Pass1234"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["error_code"], "ALREADY_ACTIVE")
        self.assertIn("email", response.data["error"]["fields"])

    def test_send_code_rejects_active_email(self):
        email = "active@example.com"
        User.objects.create_user(
            username="active",
            email=email,
            password="Pass1234",
            is_active=True,
        )

        response = self.client.post(
            reverse("email_register_send_code"),
            self._send_code_payload(email),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["error_code"], "EMAIL_EXISTS")

    @patch.dict("os.environ", {"TURNSTILE_ENABLED": ""})
    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="site-key",
        TURNSTILE_SECRET_KEY="secret-key",
    )
    def test_send_code_requires_turnstile_when_enabled(self):
        response = self.client.post(
            reverse("email_register_send_code"),
            {"email": "missing-turnstile@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["error_code"], "VALIDATION_ERROR")
        self.assertIn("turnstile_token", response.data["error"]["fields"])

    @patch.dict("os.environ", {"TURNSTILE_ENABLED": ""})
    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="site-key",
        TURNSTILE_SECRET_KEY="",
    )
    def test_ops_site_registration_bypasses_turnstile(self):
        response = self.client.post(
            reverse("email_register_send_code"),
            {"email": "ops-register@example.com"},
            format="json",
            HTTP_X_HFL_SITE_ROLE="ops",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "0000")


@override_settings(
    HFL_EMAIL_SIGNUP_ENABLED=False,
    HFL_ENTERPRISE_REGISTRATION_ENABLED=False,
    HFL_PERSONAL_REGISTRATION_ENABLED=False,
)
@patch.dict(
    "os.environ",
    {
        "HFL_EMAIL_SIGNUP_ENABLED": "false",
        "HFL_ENTERPRISE_REGISTRATION_ENABLED": "false",
        "HFL_PERSONAL_REGISTRATION_ENABLED": "false",
    },
)
class EmailSignupDisabledTests(APITestCase):
    def test_email_signup_endpoints_are_forbidden(self):
        requests = (
            (
                "email_register_send_code",
                {"email": "disabled@example.com"},
            ),
            (
                "email_register",
                {
                    "first_name": "Disabled",
                    "last_name": "User",
                    "email": "disabled@example.com",
                },
            ),
            (
                "email_register_confirm",
                {
                    "email": "disabled@example.com",
                    "code": "123456",
                    "password": "Pass1234",
                },
            ),
        )

        for route_name, payload in requests:
            with self.subTest(route_name=route_name):
                response = self.client.post(reverse(route_name), payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(
                    response.data["error"]["error_code"],
                    "EMAIL_SIGNUP_DISABLED",
                )

        self.assertFalse(User.objects.filter(email="disabled@example.com").exists())


@override_settings(HFL_EMAIL_SIGNUP_ENABLED=True)
@patch.dict("os.environ", {"HFL_EMAIL_SIGNUP_ENABLED": "true"})
class EmailSignupCommunityEmptySocketTests(APITestCase):
    """Env may enable signup, but empty socket still forbids it."""

    def test_email_signup_stays_off_without_platform_extension(self):
        with patch("apps.configuration.services.runtime_settings.enterprise_identity_enabled", return_value=False):
            response = self.client.post(
                reverse("email_register_send_code"),
                {"email": "community-signup@example.com"},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.data["error"]["error_code"],
            "EMAIL_SIGNUP_DISABLED",
        )


class RegistrationServiceTests(APITestCase):
    def test_provision_registered_user_tenant_is_idempotent(self):
        user = User.objects.create_user(
            username="tenant-user",
            email="tenant-user@example.com",
            password="Pass1234",
            is_active=True,
        )

        org1, membership1 = provision_registered_user_tenant(user)
        org2, membership2 = provision_registered_user_tenant(user)

        self.assertEqual(org1.id, org2.id)
        self.assertEqual(membership1.id, membership2.id)
        from apps.alert.models import AlertPolicy

        self.assertEqual(
            AlertPolicy.objects.filter(
                organization=org1,
                default_key__isnull=False,
            ).count(),
            7,
        )

    def test_complete_user_registration_creates_unique_org_from_email(self):
        email = "unique-org@example.com"
        user = User.objects.create_user(
            username="unique-org",
            email=email,
            password="unused",
            is_active=False,
        )
        code, error = generate_registration_verification_code(user)
        self.assertIsNone(error)

        success, reason, org = complete_user_registration(user, "Pass1234", code)

        self.assertTrue(success)
        self.assertIsNone(reason)
        self.assertIsNotNone(org)
        self.assertTrue(Organization.objects.filter(key=org.key).exists())
        membership = Membership.objects.get(user=user, organization=org, is_active=True)
        from apps.iam.services.membership_service import authoritative_role

        self.assertEqual(authoritative_role(membership), Membership.Role.OWNER)
