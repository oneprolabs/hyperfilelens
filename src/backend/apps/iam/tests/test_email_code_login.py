import hashlib
import re
from datetime import timedelta
from unittest.mock import patch

import requests
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.iam.email_verification_models import EmailVerificationCode
from apps.iam.models import Membership, Organization


@override_settings(
    HFL_EMAIL_CODE_LOGIN_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
@patch.dict(
    "os.environ",
    {
        "HFL_EMAIL_CODE_LOGIN_ENABLED": "true",
        "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    },
)
class EmailCodeLoginApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        patcher = patch(
            "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.email = "member-code-login@example.com"
        self.user = User.objects.create_user(
            username=self.email,
            email=self.email,
            password="Password123",
            is_active=True,
        )
        self.organization = Organization.objects.create(
            key="email-code-org",
            name="Email Code Org",
        )
        Membership.objects.create(
            user=self.user,
            organization=self.organization,
            role=Membership.Role.OWNER,
            is_active=True,
        )

    def tearDown(self):
        cache.clear()

    def _send(
        self, email: str | None = None, *, site_role: str = "tenant",
        token: str | None = None,
    ):
        data = {"email": email or self.email}
        if token is not None:
            data["turnstile_token"] = token
        return self.client.post(
            reverse("email_code_login_send"),
            data,
            format="json",
            HTTP_X_HFL_SITE_ROLE=site_role,
            HTTP_X_FORWARDED_PROTO="https",
            REMOTE_ADDR="192.0.2.10",
            secure=True,
        )

    def _verify(self, code: str, email: str | None = None):
        return self.client.post(
            reverse("email_code_login_verify"),
            {"email": email or self.email, "code": code},
            format="json",
            HTTP_X_HFL_SITE_ROLE="tenant",
            HTTP_X_FORWARDED_PROTO="https",
            REMOTE_ADDR="192.0.2.10",
            secure=True,
        )

    @staticmethod
    def _outbox_code() -> str:
        match = re.search(r"\b(\d{6})\b", mail.outbox[-1].body)
        assert match is not None
        return match.group(1)

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="test-site-key",
        TURNSTILE_SECRET_KEY="test-secret-key",
        FRONTEND_URL="https://testserver",
    )
    @patch("apps.iam.services.turnstile_verification.validate_turnstile")
    def test_send_requires_turnstile_without_consuming_cooldown_on_rejection(self, verify):
        response = self._send()
        self.assertEqual(response.status_code, 400)
        self.assertIn("turnstile_token", response.data["error"]["fields"])
        verify.assert_not_called()

        verify.return_value = False
        response = self._send(token="invalid-token")
        self.assertEqual(response.data["error"]["error_code"], "TURNSTILE_INVALID")
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EmailVerificationCode.objects.exists())

        verify.return_value = True
        response = self._send(token="valid-token")
        self.assertEqual(response.status_code, 200)
        verify.assert_called_with(
            "valid-token", "192.0.2.10",
            expected_action="email_login_send_code",
            expected_hostname="testserver",
        )
        self.assertEqual(len(mail.outbox), 1)

        # The email code is the credential; no second Turnstile token is needed.
        verify.reset_mock()
        response = self._verify(self._outbox_code())
        self.assertEqual(response.status_code, 200)
        verify.assert_not_called()

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="test-site-key",
        TURNSTILE_SECRET_KEY="",
    )
    def test_send_fails_closed_when_turnstile_is_misconfigured(self):
        response = self._send(token="token")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["error"]["error_code"], "TURNSTILE_MISCONFIGURED")
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EmailVerificationCode.objects.exists())

    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="test-site-key",
        TURNSTILE_SECRET_KEY="test-secret-key",
        FRONTEND_URL="https://testserver",
    )
    @patch("apps.iam.services.turnstile_service._get_http_session")
    def test_siteverify_failure_action_and_hostname_never_issue_codes(self, session):
        response = session.return_value.post.return_value
        for result in [
            {"success": False, "error-codes": ["timeout-or-duplicate"]},
            {"success": True, "action": "login", "hostname": "testserver"},
            {"success": True, "action": "email_login_send_code", "hostname": "other.example"},
        ]:
            with self.subTest(result=result):
                response.json.return_value = result
                rejected = self._send(token="token")
                self.assertEqual(rejected.data["error"]["error_code"], "TURNSTILE_INVALID")
                self.assertEqual(len(mail.outbox), 0)
                self.assertFalse(EmailVerificationCode.objects.exists())

        session.return_value.post.side_effect = requests.Timeout()
        rejected = self._send(token="token")
        self.assertEqual(rejected.data["error"]["error_code"], "TURNSTILE_INVALID")
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EmailVerificationCode.objects.exists())

    @override_settings(TURNSTILE_ENABLED=False)
    @patch("apps.iam.services.turnstile_verification.validate_turnstile")
    def test_disabled_turnstile_does_not_verify(self, verify):
        self.assertEqual(self._send().status_code, 200)
        verify.assert_not_called()

    def test_send_and_verify_hands_off_to_existing_org_selection(self):
        response = self._send()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "0000")
        self.assertEqual(response.data["data"]["retry_after"], 60)
        self.assertEqual(response.data["data"]["expires_in"], 600)
        self.assertIn("Request received", str(response.data["data"]["message"]))
        self.assertEqual(len(mail.outbox), 1)
        code = self._outbox_code()

        response = self._verify(code)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["data"]["available_orgs"][0]["org_key"],
            self.organization.key,
        )
        self.assertEqual(self.client.session["pending_user_id"], self.user.id)

        response = self.client.post(
            reverse("org_select"),
            {"org_key": self.organization.key},
            format="json",
            HTTP_X_HFL_SITE_ROLE="tenant",
            HTTP_X_FORWARDED_PROTO="https",
            secure=True,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access_token", response.cookies)
        self.assertIn("refresh_token", response.cookies)

    def test_unknown_email_uses_same_success_message_without_sending(self):
        known = self._send()
        cache.clear()
        mail.outbox.clear()

        unknown = self._send("missing-code-login@example.com")

        self.assertEqual(unknown.status_code, status.HTTP_200_OK)
        self.assertEqual(
            unknown.data["data"]["message"],
            known.data["data"]["message"],
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_email_is_normalized_before_lookup(self):
        response = self._send(f"  {self.email.upper()}  ")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)

    def test_duplicate_email_is_not_eligible_and_is_not_disclosed(self):
        User.objects.create_user(
            username="duplicate-code-login@example.com",
            email=self.email.upper(),
            password="Password123",
            is_active=True,
        )

        duplicate = self._send()
        cache.clear()
        unknown = self._send("missing-code-login@example.com")

        self.assertEqual(duplicate.status_code, status.HTTP_200_OK)
        self.assertEqual(
            duplicate.data["data"]["message"],
            unknown.data["data"]["message"],
        )
        self.assertEqual(len(mail.outbox), 0)

        response = self._verify("123456")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["error"]["error_code"],
            "INVALID_OR_EXPIRED_CODE",
        )

    def test_user_without_active_organization_is_not_eligible(self):
        self.organization.is_active = False
        self.organization.save(update_fields=["is_active"])

        response = self._send()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)

    def test_user_without_active_membership_is_not_eligible(self):
        Membership.objects.filter(
            user=self.user,
            organization=self.organization,
        ).update(is_active=False)

        response = self._send()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)

    def test_platform_staff_is_not_eligible_and_is_not_disclosed(self):
        staff = User.objects.create_user(
            username="staff-code-login@example.com",
            email="staff-code-login@example.com",
            password="Password123",
            is_active=True,
            is_staff=True,
        )

        response = self._send(staff.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)

    def test_ops_site_is_rejected_even_when_feature_is_enabled(self):
        response = self._send(site_role="ops")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.data["error"]["error_code"],
            "EMAIL_CODE_LOGIN_DISABLED",
        )

    def test_resend_cooldown_is_enforced_by_server(self):
        self.assertEqual(self._send().status_code, status.HTTP_200_OK)

        response = self._send()

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(
            response.data["error"]["error_code"],
            "EMAIL_CODE_RATE_LIMITED",
        )
        self.assertGreater(int(response["Retry-After"]), 0)
        self.assertEqual(len(mail.outbox), 1)

    @patch(
        "apps.iam.auth.views.email_code_login.issue_login_code",
        side_effect=RuntimeError("sensitive SMTP provider response"),
    )
    def test_delivery_failure_returns_stable_redacted_error(self, _issue):
        response = self._send()

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(
            response.data["error"]["error_code"],
            "EMAIL_SERVICE_UNAVAILABLE",
        )
        self.assertNotIn("sensitive SMTP provider response", str(response.data))

    def test_wrong_code_is_capped_and_valid_code_cannot_be_replayed(self):
        self._send()
        code = self._outbox_code()

        for _attempt in range(4):
            response = self._verify("000000")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(
                response.data["error"]["error_code"],
                "INVALID_OR_EXPIRED_CODE",
            )

        response = self._verify("000000")
        self.assertEqual(
            response.data["error"]["error_code"],
            "EMAIL_CODE_ATTEMPTS_EXCEEDED",
        )
        self.assertEqual(self._verify(code).status_code, status.HTTP_400_BAD_REQUEST)

    def test_password_reset_code_cannot_be_used_for_login(self):
        purpose = EmailVerificationCode.Purpose.PASSWORD_RESET
        plain_code = "123456"
        EmailVerificationCode.objects.create(
            user=self.user,
            purpose=purpose,
            code_hash=EmailVerificationCode.hash_code(
                plain_code,
                user_id=self.user.id,
                purpose=purpose,
            ),
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        response = self._verify(plain_code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["error"]["error_code"],
            "INVALID_OR_EXPIRED_CODE",
        )

    def test_legacy_code_is_never_accepted_for_login(self):
        plain_code = "654321"
        EmailVerificationCode.objects.create(
            user=self.user,
            purpose=EmailVerificationCode.Purpose.LEGACY,
            code_hash=hashlib.sha256(plain_code.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        response = self._verify(plain_code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["error"]["error_code"],
            "INVALID_OR_EXPIRED_CODE",
        )

    @override_settings(HFL_EMAIL_CODE_LOGIN_ENABLED=False)
    @patch.dict("os.environ", {"HFL_EMAIL_CODE_LOGIN_ENABLED": "false"})
    def test_feature_is_off_by_default_policy(self):
        response = self._send()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(len(mail.outbox), 0)


@override_settings(
    HFL_EMAIL_CODE_LOGIN_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
@patch.dict(
    "os.environ",
    {
        "HFL_EMAIL_CODE_LOGIN_ENABLED": "true",
        "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    },
)
class EmailCodeLoginCommunityEmptySocketTests(APITestCase):
    """Env may enable code login; empty socket still forbids it."""

    def test_send_code_stays_off_without_platform_extension(self):
        response = self.client.post(
            reverse("email_code_login_send"),
            {"email": "community-code@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.data["error"]["error_code"],
            "EMAIL_CODE_LOGIN_DISABLED",
        )
        self.assertEqual(len(mail.outbox), 0)
