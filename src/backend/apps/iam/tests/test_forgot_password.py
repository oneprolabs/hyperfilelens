from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.iam.profile_models import Profile
from apps.iam.email_verification_models import EmailVerificationCode


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ForgotPasswordCommunityDisabledTests(APITestCase):
    """Community empty socket: self-serve reset stays off even with SMTP."""

    def test_password_reset_is_forbidden_without_extension(self):
        response = self.client.post(
            reverse("forgot_password"),
            {"email": "community@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.data["error"]["error_code"],
            "PASSWORD_RESET_DISABLED",
        )

    def test_password_reset_confirm_is_forbidden_without_extension(self):
        response = self.client.post(
            reverse("forgot_password_confirm"),
            {
                "email": "community@example.com",
                "code": "123456",
                "password": "NewPass123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.data["error"]["error_code"],
            "PASSWORD_RESET_DISABLED",
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ForgotPasswordApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        patcher = patch(
            "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _payload(self, email: str) -> dict:
        return {"email": email}

    def test_active_user_receives_reset_email(self):
        email = "active-reset@example.com"
        User.objects.create_user(
            username="active-reset",
            email=email,
            password="Pass1234",
            is_active=True,
        )

        response = self.client.post(
            reverse("forgot_password"),
            self._payload(email),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "0000")
        self.assertFalse(response.data["data"].get("pending_registration"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("password reset", mail.outbox[0].subject.lower())

    def test_pending_registration_user_does_not_resume_when_signup_is_disabled(self):
        email = "pending-reset@example.com"
        user = User.objects.create_user(
            username="pending-reset",
            email=email,
            password="unused",
            is_active=False,
        )
        Profile.objects.create(user=user, registration_completed=False)

        response = self.client.post(
            reverse("forgot_password"),
            self._payload(email),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "0000")
        self.assertFalse(response.data["data"].get("pending_registration"))
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(HFL_EMAIL_SIGNUP_ENABLED=True)
    @patch.dict("os.environ", {"HFL_EMAIL_SIGNUP_ENABLED": "true"})
    def test_pending_registration_user_resumes_when_signup_is_enabled(self):
        email = "pending-signup-enabled@example.com"
        user = User.objects.create_user(
            username="pending-signup-enabled",
            email=email,
            password="unused",
            is_active=False,
        )
        Profile.objects.create(user=user, registration_completed=False)

        response = self.client.post(
            reverse("forgot_password"),
            self._payload(email),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["data"]["pending_registration"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("verification code", mail.outbox[0].subject.lower())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_console_backend_reports_email_service_unavailable(self):
        email = "console-reset@example.com"
        user = User.objects.create_user(
            username="console-reset",
            email=email,
            password="Pass1234",
            is_active=True,
        )

        response = self.client.post(
            reverse("forgot_password"),
            self._payload(email),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(
            response.data["error"]["error_code"],
            "EMAIL_SERVICE_UNAVAILABLE",
        )
        self.assertFalse(EmailVerificationCode.objects.filter(user=user).exists())

    @patch(
        "apps.iam.services.registration_service._send_password_reset_email",
        side_effect=RuntimeError("sensitive provider failure"),
    )
    def test_smtp_failure_deletes_code_and_returns_stable_error(self, _send_email):
        email = "failed-reset@example.com"
        user = User.objects.create_user(
            username="failed-reset",
            email=email,
            password="Pass1234",
            is_active=True,
        )

        response = self.client.post(
            reverse("forgot_password"),
            self._payload(email),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(
            response.data["error"]["error_code"],
            "EMAIL_SERVICE_UNAVAILABLE",
        )
        self.assertNotIn("sensitive provider failure", str(response.data))
        self.assertFalse(EmailVerificationCode.objects.filter(user=user).exists())

    def test_unknown_email_returns_not_registered_error(self):
        response = self.client.post(
            reverse("forgot_password"),
            self._payload("missing@example.com"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "1001")
        self.assertEqual(response.data["error"]["error_code"], "EMAIL_NOT_REGISTERED")
        self.assertIn("email", response.data["error"]["fields"])
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="site-key",
        TURNSTILE_SECRET_KEY="",
    )
    def test_ops_site_password_reset_bypasses_turnstile(self):
        email = "ops-reset@example.com"
        User.objects.create_user(
            username=email,
            email=email,
            password="Pass1234",
            is_active=True,
        )

        response = self.client.post(
            reverse("forgot_password"),
            self._payload(email),
            format="json",
            HTTP_X_HFL_SITE_ROLE="ops",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "0000")
