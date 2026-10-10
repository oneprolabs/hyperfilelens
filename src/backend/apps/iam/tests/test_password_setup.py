from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.iam.auth.serializers import UserDetailsSerializer
from apps.iam.email_verification_models import EmailVerificationCode


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordSetupTests(APITestCase):
    def setUp(self):
        cache.clear()
        patcher = patch(
            "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = User.objects.create_user(
            username="setup@example.com", email="setup@example.com", password=None,
        )
        self.url = reverse("forgot_password_confirm")

    def code(self, purpose=EmailVerificationCode.Purpose.PASSWORD_RESET):
        return EmailVerificationCode.objects.create(
            user=self.user, purpose=purpose,
            code_hash=EmailVerificationCode.hash_code(
                "123456", user_id=self.user.pk, purpose=purpose,
                recipient_email=self.user.email if purpose == EmailVerificationCode.Purpose.PASSWORD_RESET else None,
            ),
            expires_at=timezone.now() + timedelta(minutes=5),
        )

    def confirm(self, code="123456"):
        return self.client.post(self.url, {
            "email": "SETUP@example.com", "code": code, "password": "NewPass123",
        }, format="json")

    def test_passwordless_user_sets_password_and_revokes_codes(self):
        self.code()
        login_code = self.code(EmailVerificationCode.Purpose.LOGIN)
        response = self.confirm()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["data"]["requires_relogin"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewPass123"))
        self.assertEqual(cache.get(f"user_token_invalid_reason:{self.user.pk}"), "password_changed")
        login_code.refresh_from_db()
        self.assertTrue(login_code.is_used)
        self.assertEqual(self.confirm().status_code, 400)

    def test_login_and_legacy_codes_cannot_set_password(self):
        for purpose in (EmailVerificationCode.Purpose.LOGIN, EmailVerificationCode.Purpose.LEGACY):
            with self.subTest(purpose=purpose):
                self.code(purpose)
                self.assertEqual(self.confirm().status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())

    def test_wrong_code_locks_out_after_five_attempts(self):
        challenge = self.code()
        for _ in range(5):
            self.assertEqual(self.confirm("654321").status_code, 400)
        challenge.refresh_from_db()
        self.assertEqual(challenge.failed_attempts, 5)
        self.assertIsNotNone(challenge.invalidated_at)
        self.assertEqual(self.confirm().status_code, 400)

    def test_password_save_failure_does_not_consume_code(self):
        challenge = self.code()
        with patch(
            "apps.iam.auth.views.registration.blacklist_all_user_tokens",
            side_effect=RuntimeError("cache unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                self.confirm()
        challenge.refresh_from_db()
        self.user.refresh_from_db()
        self.assertFalse(challenge.is_used)
        self.assertFalse(self.user.has_usable_password())
        self.assertEqual(self.confirm().status_code, 200)

    def test_duplicate_case_insensitive_email_cannot_reset(self):
        self.code()
        User.objects.create_user(username="duplicate", email="SETUP@example.com")
        self.assertEqual(self.confirm().status_code, 404)

    def test_expired_code_cannot_set_password(self):
        challenge = self.code()
        challenge.expires_at = timezone.now() - timedelta(seconds=1)
        challenge.save()
        self.assertEqual(self.confirm().status_code, 400)

    def test_reset_send_has_server_side_cooldown(self):
        payload = {"email": self.user.email}
        self.assertEqual(self.client.post(reverse("forgot_password"), payload, format="json").status_code, 200)
        self.assertEqual(self.client.post(reverse("forgot_password"), payload, format="json").status_code, 429)

    def test_profile_exposes_password_state_but_rejects_email_change(self):
        serializer = UserDetailsSerializer(self.user)
        self.assertFalse(serializer.data["has_usable_password"])
        self.assertTrue(serializer.data["password_reset_available"])
        update = UserDetailsSerializer(self.user, data={"email": "attacker@example.com"}, partial=True)
        self.assertFalse(update.is_valid())
        self.assertIn("email", update.errors)

    def test_reset_invalidates_existing_access_and_refresh_tokens(self):
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken
        from apps.iam.services.token_service import (
            generate_token_family_id, store_refresh_token_family,
        )
        family_id = generate_token_family_id()
        refresh = RefreshToken.for_user(self.user)
        refresh["family_id"] = family_id
        refresh["token_version"] = store_refresh_token_family(
            self.user.pk, family_id, refresh.payload["jti"],
        )
        old_client = APIClient()
        old_client.cookies["access_token"] = str(refresh.access_token)
        old_client.cookies["refresh_token"] = str(refresh)
        self.code()
        self.assertEqual(self.confirm().status_code, 200)
        self.assertEqual(old_client.get(reverse("rest_user_details")).status_code, 401)
        self.assertEqual(old_client.post(reverse("token_refresh")).status_code, 401)

    def test_password_update_invalidates_pending_org_selection(self):
        import time
        from rest_framework.test import APIClient
        from apps.iam.models import Membership, Organization
        org = Organization.objects.create(key="setup-org", name="Setup Org")
        Membership.objects.create(user=self.user, organization=org, is_active=True)
        pending_client = APIClient()
        session = pending_client.session
        session["pending_user_id"] = self.user.pk
        session["pending_password_fingerprint"] = self.user.get_session_auth_hash()
        session["pending_login_at"] = time.time()
        session.save()
        self.code()
        self.assertEqual(self.confirm().status_code, 200)
        response = pending_client.post(reverse("org_select"), {"org_key": org.key}, format="json")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access_token", response.cookies)

    def test_reset_code_is_bound_to_original_email(self):
        self.code()
        self.user.email = "new@example.com"
        self.user.save(update_fields=["email"])
        response = self.client.post(self.url, {
            "email": self.user.email, "code": "123456", "password": "NewPass123",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())
