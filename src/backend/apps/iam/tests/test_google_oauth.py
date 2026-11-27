"""Tests for Google OAuth login and social registration."""

from datetime import timedelta
from io import StringIO
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.iam.models import Membership, OAuthErrorEvent, Organization
from apps.iam.services.oauth_error_events import (
    build_oauth_error_redirect,
    consume_oauth_error_event,
    create_oauth_error_event,
)
from apps.iam.services.registration_service import complete_social_user_registration
from apps.configuration.services.runtime_settings import sync_google_social_app


@override_settings(
    GOOGLE_CLIENT_ID="test-client-id",
    GOOGLE_CLIENT_SECRET="test-client-secret",
    HFL_GOOGLE_OAUTH_ENABLED=True,
    FRONTEND_URL="https://app.example.com",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    },
)
@patch.dict("os.environ", {"HFL_GOOGLE_OAUTH_ENABLED": "true"})
class GoogleOAuthConfigTests(APITestCase):
    def setUp(self):
        super().setUp()
        patcher = patch(
            "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        sync_google_social_app()

    def test_google_config_enabled(self):
        response = self.client.get(reverse("google_oauth_config"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "0000")
        self.assertTrue(response.data["data"]["enabled"])
        self.assertEqual(
            response.data["data"]["login_url"],
            "https://app.example.com/accounts/google/login/",
        )

    def test_google_provider_has_no_second_settings_application(self):
        self.assertNotIn("APP", settings.SOCIALACCOUNT_PROVIDERS["google"])
        self.assertEqual(SocialApp.objects.filter(provider="google").count(), 1)

    def test_google_login_redirects_with_the_canonical_callback(self):
        response = self.client.get(
            reverse("google_login"),
            secure=True,
            HTTP_HOST="app.example.com",
            HTTP_X_FORWARDED_PROTO="https",
        )

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        location = urlparse(response["Location"])
        self.assertEqual(location.hostname, "accounts.google.com")
        self.assertEqual(
            parse_qs(location.query)["redirect_uri"],
            ["https://app.example.com/accounts/google/login/callback/"],
        )

    def test_legacy_duplicate_is_reported_as_unavailable_instead_of_500(self):
        duplicate = SocialApp.objects.create(
            provider="google",
            name="Duplicate Google",
            client_id="duplicate-client",
            secret="duplicate-secret",
        )
        duplicate.sites.add(Site.objects.get_current())

        config = self.client.get(reverse("google_oauth_config"))
        login = self.client.get(reverse("google_login"))

        self.assertEqual(config.status_code, status.HTTP_200_OK)
        self.assertFalse(config.data["data"]["enabled"])
        self.assertEqual(login.status_code, status.HTTP_403_FORBIDDEN)

    def test_stale_database_credentials_are_reported_as_unavailable(self):
        app = SocialApp.objects.get(provider="google")
        app.client_id = "stale-client-id"
        app.secret = "stale-client-secret"
        app.save(update_fields=["client_id", "secret"])

        config = self.client.get(reverse("google_oauth_config"))
        login = self.client.get(reverse("google_login"))

        self.assertEqual(config.status_code, status.HTTP_200_OK)
        self.assertFalse(config.data["data"]["enabled"])
        self.assertEqual(login.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="")
    @patch.dict("os.environ", {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""})
    def test_google_config_requires_credentials(self):
        response = self.client.get(reverse("google_oauth_config"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["data"]["enabled"])


@override_settings(
    GOOGLE_CLIENT_ID="test-client-id",
    GOOGLE_CLIENT_SECRET="test-client-secret",
    HFL_GOOGLE_OAUTH_ENABLED=True,
    FRONTEND_URL="https://127.0.0.1:11443",
)
@patch.dict("os.environ", {"HFL_GOOGLE_OAUTH_ENABLED": "true"})
class GoogleOAuthReadinessCommandTests(APITestCase):
    def setUp(self):
        super().setUp()
        patcher = patch(
            "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        sync_google_social_app()

    def test_local_callback_is_ready(self):
        stdout = StringIO()

        call_command("check_google_oauth_readiness", stdout=stdout)

        self.assertIn(
            "Google OAuth callback: "
            "https://127.0.0.1:11443/accounts/google/login/callback/",
            stdout.getvalue(),
        )
        self.assertIn("HFL_GOOGLE_OAUTH_STATUS=ready", stdout.getvalue())

    @override_settings(FRONTEND_URL="https://app.hyperfilelens.com")
    def test_production_callback_is_ready(self):
        sync_google_social_app()
        stdout = StringIO()

        call_command("check_google_oauth_readiness", stdout=stdout)

        self.assertIn(
            "Google OAuth callback: "
            "https://app.hyperfilelens.com/accounts/google/login/callback/",
            stdout.getvalue(),
        )

    def test_duplicate_application_fails_readiness_without_exposing_secret(self):
        duplicate = SocialApp.objects.create(
            provider="google",
            name="Duplicate Google",
            client_id="duplicate-client",
            secret="must-not-be-printed",
        )
        duplicate.sites.add(Site.objects.get_current())
        stderr = StringIO()

        with self.assertRaises(CommandError):
            call_command(
                "check_google_oauth_readiness",
                stdout=StringIO(),
                stderr=stderr,
            )

        self.assertNotIn("must-not-be-printed", stderr.getvalue())


@override_settings(
    GOOGLE_CLIENT_ID="test-client-id",
    GOOGLE_CLIENT_SECRET="test-client-secret",
    HFL_GOOGLE_OAUTH_ENABLED=False,
    FRONTEND_URL="https://app.example.com",
)
@patch.dict("os.environ", {"HFL_GOOGLE_OAUTH_ENABLED": "false"})
class GoogleOAuthDisabledTests(APITestCase):
    def test_google_config_disabled(self):
        response = self.client.get(reverse("google_oauth_config"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["data"]["enabled"])

    def test_google_login_endpoint_is_forbidden(self):
        response = self.client.get(reverse("google_login"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


@override_settings(
    GOOGLE_CLIENT_ID="test-client-id",
    GOOGLE_CLIENT_SECRET="test-client-secret",
    HFL_GOOGLE_OAUTH_ENABLED=True,
    FRONTEND_URL="https://app.example.com",
)
@patch.dict(
    "os.environ",
    {
        "HFL_GOOGLE_OAUTH_ENABLED": "true",
        "GOOGLE_CLIENT_ID": "test-client-id",
        "GOOGLE_CLIENT_SECRET": "test-client-secret",
    },
)
class GoogleOAuthCommunityEmptySocketTests(APITestCase):
    """Credentials may be present; empty socket still keeps OAuth off."""

    def test_google_config_stays_disabled_without_extension(self):
        response = self.client.get(reverse("google_oauth_config"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["data"]["enabled"])

    def test_google_login_stays_forbidden_without_extension(self):
        response = self.client.get(reverse("google_login"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class SocialRegistrationServiceTests(APITestCase):
    @patch(
        "apps.lens_bridge.services.chat_user_provisioning."
        "enqueue_sl_chat_user_provision"
    )
    def test_complete_social_user_registration_provisions_org(self, _enqueue):
        user = User.objects.create_user(
            username="google-user",
            email="google-user@example.com",
            password="unused",
            is_active=False,
        )

        org = complete_social_user_registration(user)

        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertTrue(Organization.objects.filter(pk=org.pk).exists())
        membership = Membership.objects.get(user=user, organization=org)
        from apps.iam.services.membership_service import authoritative_role
        self.assertEqual(authoritative_role(membership), Membership.Role.OWNER)
        self.assertEqual(Membership.objects.filter(user=user).count(), 1)


class OAuthErrorEventTests(APITestCase):
    def test_event_is_hashed_and_consumed_once(self):
        event_id = create_oauth_error_event("account_disabled")
        event = OAuthErrorEvent.objects.get()

        self.assertNotEqual(event.token_hash, event_id)
        self.assertEqual(len(event.token_hash), 64)
        self.assertEqual(consume_oauth_error_event(event_id), "account_disabled")
        self.assertIsNone(consume_oauth_error_event(event_id))
        self.assertFalse(OAuthErrorEvent.objects.exists())

    def test_expired_unknown_and_oversized_events_fail_closed(self):
        event_id = create_oauth_error_event("invalid_grant")
        OAuthErrorEvent.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        self.assertIsNone(consume_oauth_error_event(event_id))
        self.assertIsNone(consume_oauth_error_event("unknown"))
        self.assertIsNone(consume_oauth_error_event("x" * 257))
        self.assertFalse(OAuthErrorEvent.objects.exists())

    def test_unknown_reason_is_reduced_to_generic(self):
        event_id = create_oauth_error_event("attacker_controlled")

        self.assertEqual(consume_oauth_error_event(event_id), "oauth_failed")

    def test_consume_endpoint_returns_verified_reason_then_generic_replay(self):
        event_id = create_oauth_error_event("state_lost")
        url = reverse("google_oauth_error_event_consume")

        verified = self.client.post(url, {"event_id": event_id}, format="json")
        replay = self.client.post(url, {"event_id": event_id}, format="json")

        self.assertEqual(verified.status_code, status.HTTP_200_OK)
        self.assertEqual(
            verified.data["data"],
            {"verified": True, "reason": "state_lost"},
        )
        self.assertEqual(verified["Cache-Control"], "no-store")
        self.assertEqual(
            replay.data["data"],
            {"verified": False, "reason": "oauth_failed"},
        )

    def test_redirect_contains_only_an_opaque_event_id(self):
        redirect_url = build_oauth_error_redirect(
            "https://app.example.com/",
            "no_email",
        )
        parsed = urlparse(redirect_url)
        query = parse_qs(parsed.query)

        self.assertEqual(parsed.path, "/auth/oauth/error")
        self.assertNotIn("reason", query)
        self.assertEqual(len(query["event_id"]), 1)
        self.assertEqual(
            consume_oauth_error_event(query["event_id"][0]),
            "no_email",
        )

    @patch(
        "apps.iam.services.oauth_error_events.create_oauth_error_event",
        side_effect=RuntimeError("database unavailable"),
    )
    def test_redirect_falls_back_to_generic_page_when_storage_fails(self, _create):
        self.assertEqual(
            build_oauth_error_redirect(
                "https://app.example.com/",
                "account_disabled",
            ),
            "https://app.example.com/auth/oauth/error",
        )


@override_settings(
    GOOGLE_CLIENT_ID="test-client-id",
    GOOGLE_CLIENT_SECRET="test-client-secret",
    HFL_GOOGLE_OAUTH_ENABLED=True,
    FRONTEND_URL="https://app.example.com",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    },
)
@patch.dict("os.environ", {"HFL_GOOGLE_OAUTH_ENABLED": "true"})
class GoogleOAuthCallbackTests(APITestCase):
    def setUp(self):
        super().setUp()
        patcher = patch(
            "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        sync_google_social_app()

    def test_incomplete_oauth_redirects_with_a_consumable_event(self):
        response = self.client.get(reverse("oauth_callback"))
        parsed = urlparse(response["Location"])
        query = parse_qs(parsed.query)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(parsed.path, "/auth/oauth/error")
        self.assertNotIn("reason", query)
        self.assertEqual(
            consume_oauth_error_event(query["event_id"][0]),
            "not_authenticated",
        )

    @patch(
        "apps.lens_bridge.services.chat_user_provisioning."
        "enqueue_sl_chat_user_provision"
    )
    def test_oauth_callback_issues_cookies_and_redirects(self, _enqueue):
        user = User.objects.create_user(
            username="oauth-callback",
            email="oauth-callback@example.com",
            password="Pass1234",
            is_active=True,
        )
        org = Organization.objects.create(key="oauth-org", name="OAuth Org", is_active=True)
        Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER, is_active=True)

        client = Client()
        client.force_login(user)
        response = client.get(reverse("oauth_callback"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("https://app.example.com/auth/oauth/callback", response["Location"])
        self.assertIn("org_key=oauth-org", response["Location"])
        self.assertIn("access_token", response.cookies)
        self.assertIn("refresh_token", response.cookies)
        self.assertIn("hfl_logout", response.cookies)
