from __future__ import annotations

import io
from unittest.mock import patch

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.configuration.models import PlatformRuntimeSetting
from apps.configuration.services.runtime_settings import (
    KEY_EMAIL_HOST,
    KEY_IDENTITY_EMAIL_SIGNUP,
    KEY_IDENTITY_GOOGLE_CLIENT_ID,
    KEY_IDENTITY_GOOGLE_OAUTH,
    KEY_IDENTITY_TURNSTILE_SITE,
    SECRET_KEY_EMAIL_PASSWORD,
    SECRET_KEY_GOOGLE,
    SECRET_KEY_TURNSTILE,
    email_connection_kwargs,
    get_secret,
    google_client_id,
    google_client_secret,
    google_oauth_enabled,
    invalidate_runtime_settings_cache,
    turnstile_secret_key,
    turnstile_site_key,
)


@override_settings(
    FRONTEND_URL="https://127.0.0.1:11443",
    SITE_ID=1,
    # Keep deployment SMTP empty so the command's runtime rows become effective.
    EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
    EMAIL_HOST="",
    EMAIL_PORT=587,
    EMAIL_HOST_USER="",
    EMAIL_HOST_PASSWORD="",
    EMAIL_USE_TLS=False,
    EMAIL_USE_SSL=False,
    DEFAULT_FROM_EMAIL="HyperFileLens <noreply@hyperfilelens.local>",
)
class EnsureDeploymentIdentitySettingsCommandTests(TestCase):
    def setUp(self):
        invalidate_runtime_settings_cache()

    def tearDown(self):
        invalidate_runtime_settings_cache()

    @staticmethod
    def complete_env(**overrides: str) -> dict[str, str]:
        env = {
            "HFL_EMAIL_SIGNUP_ENABLED": "true",
            "HFL_GOOGLE_OAUTH_ENABLED": "true",
            "GOOGLE_CLIENT_ID": "123-example.apps.googleusercontent.com",
            "GOOGLE_CLIENT_SECRET": "google-test-secret",
            "TURNSTILE_ENABLED": "false",
            "TURNSTILE_SITE_KEY": "turnstile-test-site",
            "TURNSTILE_SECRET_KEY": "turnstile-test-secret",
            "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "EMAIL_HOST": "smtp.example.com",
            "EMAIL_PORT": "465",
            "EMAIL_HOST_USER": "mailer@example.com",
            "EMAIL_HOST_PASSWORD": "smtp-test-secret",
            "EMAIL_USE_TLS": "false",
            "EMAIL_USE_SSL": "true",
            "DEFAULT_FROM_EMAIL": "HyperFileLens <mailer@example.com>",
        }
        env.update(overrides)
        return env

    def run_command(self, env: dict[str, str]):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.dict("os.environ", env, clear=False):
            call_command(
                "ensure_deployment_identity_settings",
                stdout=stdout,
                stderr=stderr,
            )
        invalidate_runtime_settings_cache()
        return stdout.getvalue(), stderr.getvalue()

    def test_applies_configuration_idempotently_without_echoing_secret(self):
        env = self.complete_env(
            GOOGLE_CLIENT_SECRET="never-print-google-secret",
            TURNSTILE_SECRET_KEY="never-print-turnstile-secret",
            EMAIL_HOST_PASSWORD="never-print-smtp-secret",
        )

        first_stdout, first_stderr = self.run_command(env)
        second_stdout, second_stderr = self.run_command(env)

        self.assertEqual(
            PlatformRuntimeSetting.objects.filter(
                key__in=(
                    KEY_IDENTITY_EMAIL_SIGNUP,
                    KEY_IDENTITY_GOOGLE_CLIENT_ID,
                    KEY_IDENTITY_GOOGLE_OAUTH,
                    KEY_IDENTITY_TURNSTILE_SITE,
                    SECRET_KEY_GOOGLE,
                    SECRET_KEY_TURNSTILE,
                )
            ).count(),
            6,
        )
        # Community empty socket: stored credentials stay off until EE loads.
        with patch(
            "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
            return_value=False,
        ):
            self.assertFalse(google_oauth_enabled())
        with patch(
            "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
            return_value=True,
        ):
            self.assertTrue(google_oauth_enabled())
        self.assertEqual(google_client_id(), env["GOOGLE_CLIENT_ID"])
        self.assertEqual(google_client_secret(), env["GOOGLE_CLIENT_SECRET"])
        self.assertEqual(turnstile_site_key(), env["TURNSTILE_SITE_KEY"])
        self.assertEqual(turnstile_secret_key(), env["TURNSTILE_SECRET_KEY"])
        email = email_connection_kwargs()
        self.assertEqual(email["host"], env["EMAIL_HOST"])
        self.assertEqual(email["password"], env["EMAIL_HOST_PASSWORD"])
        self.assertEqual(email["source"], "runtime")
        output = first_stdout + first_stderr + second_stdout + second_stderr
        for secret_name in (
            "GOOGLE_CLIENT_SECRET",
            "TURNSTILE_SECRET_KEY",
            "EMAIL_HOST_PASSWORD",
        ):
            self.assertNotIn(env[secret_name], output)
        self.assertIn("HFL_IDENTITY_STATUS=applied", second_stdout)
        self.assertEqual(SocialApp.objects.filter(provider="google").count(), 1)
        app = SocialApp.objects.get(provider="google")
        self.assertEqual(app.client_id, env["GOOGLE_CLIENT_ID"])
        self.assertEqual(list(app.sites.values_list("pk", flat=True)), [1])
        site = Site.objects.get(pk=1)
        self.assertEqual(site.domain, "127.0.0.1:11443")
        self.assertEqual(site.name, "127.0.0.1:11443")

    def test_invalid_google_configuration_warns_and_preserves_existing_values(self):
        existing = self.complete_env(
            HFL_EMAIL_SIGNUP_ENABLED="false",
            GOOGLE_CLIENT_ID="123-existing.apps.googleusercontent.com",
            GOOGLE_CLIENT_SECRET="existing-secret",
        )
        self.run_command(existing)

        stdout, stderr = self.run_command(
            self.complete_env(
                GOOGLE_CLIENT_ID="invalid-client",
                GOOGLE_CLIENT_SECRET="replacement-secret",
            )
        )

        self.assertEqual(google_client_id(), existing["GOOGLE_CLIENT_ID"])
        self.assertEqual(google_client_secret(), existing["GOOGLE_CLIENT_SECRET"])
        self.assertIn("HFL_IDENTITY_STATUS=warning", stdout)
        self.assertIn("preserved the installed Google settings", stderr)
        self.assertEqual(
            SocialApp.objects.get(provider="google").client_id,
            existing["GOOGLE_CLIENT_ID"],
        )

    def test_google_sync_failure_warns_and_preserves_existing_values(self):
        existing = self.complete_env(
            GOOGLE_CLIENT_ID="123-existing.apps.googleusercontent.com",
            GOOGLE_CLIENT_SECRET="existing-secret",
        )
        self.run_command(existing)

        with patch(
            "apps.instance_settings.management.commands."
            "ensure_deployment_identity_settings.sync_google_social_app",
            side_effect=ValueError("invalid public URL"),
        ):
            stdout, stderr = self.run_command(
                self.complete_env(
                    GOOGLE_CLIENT_ID="123-replacement.apps.googleusercontent.com",
                    GOOGLE_CLIENT_SECRET="replacement-secret",
                )
            )

        self.assertEqual(google_client_id(), existing["GOOGLE_CLIENT_ID"])
        self.assertEqual(google_client_secret(), existing["GOOGLE_CLIENT_SECRET"])
        self.assertEqual(
            SocialApp.objects.get(provider="google").client_id,
            existing["GOOGLE_CLIENT_ID"],
        )
        self.assertIn("HFL_IDENTITY_STATUS=warning", stdout)
        self.assertIn("Google OAuth synchronization failed (ValueError)", stderr)
        self.assertNotIn("replacement-secret", stdout + stderr)

    def test_production_policy_disables_email_signup_and_enables_google(self):
        stdout, _stderr = self.run_command(
            self.complete_env(
                HFL_EMAIL_SIGNUP_ENABLED="false",
                GOOGLE_CLIENT_ID="6436338978-prod.apps.googleusercontent.com",
                GOOGLE_CLIENT_SECRET="production-secret",
            )
        )

        signup = PlatformRuntimeSetting.objects.get(key=KEY_IDENTITY_EMAIL_SIGNUP)
        self.assertEqual(signup.value_text, "false")
        self.assertIn("Email sign-up: disabled", stdout)
        with patch(
            "apps.configuration.services.runtime_settings.enterprise_identity_enabled",
            return_value=True,
        ):
            self.assertTrue(google_oauth_enabled())

    def test_invalid_turnstile_preserves_existing_credentials_without_failing(self):
        existing = self.complete_env(
            TURNSTILE_ENABLED="true",
            TURNSTILE_SITE_KEY="existing-turnstile-site",
            TURNSTILE_SECRET_KEY="existing-turnstile-secret",
        )
        self.run_command(existing)

        stdout, stderr = self.run_command(
            self.complete_env(
                TURNSTILE_ENABLED="true",
                TURNSTILE_SITE_KEY="replacement-site",
                TURNSTILE_SECRET_KEY="invalid secret",
            )
        )

        self.assertEqual(turnstile_site_key(), existing["TURNSTILE_SITE_KEY"])
        self.assertEqual(turnstile_secret_key(), existing["TURNSTILE_SECRET_KEY"])
        self.assertIn("HFL_IDENTITY_STATUS=warning", stdout)
        self.assertIn("preserved the installed Turnstile settings", stderr)
        self.assertNotIn("invalid secret", stdout + stderr)

    def test_invalid_smtp_preserves_existing_configuration_without_failing(self):
        existing = self.complete_env(
            EMAIL_HOST="smtp.existing.example.com",
            EMAIL_HOST_PASSWORD="existing-smtp-secret",
        )
        self.run_command(existing)

        stdout, stderr = self.run_command(
            self.complete_env(
                EMAIL_HOST="smtp.replacement.example.com",
                EMAIL_PORT="invalid",
                EMAIL_HOST_PASSWORD="replacement-smtp-secret",
            )
        )

        self.assertEqual(
            PlatformRuntimeSetting.objects.get(key=KEY_EMAIL_HOST).value_text,
            existing["EMAIL_HOST"],
        )
        self.assertEqual(
            get_secret(SECRET_KEY_EMAIL_PASSWORD),
            existing["EMAIL_HOST_PASSWORD"],
        )
        self.assertIn("HFL_IDENTITY_STATUS=warning", stdout)
        self.assertIn("preserved the installed email configuration", stderr)
        self.assertNotIn("replacement-smtp-secret", stdout + stderr)

    def test_converges_legacy_google_duplicates_to_matching_application(self):
        site = Site.objects.get_current()
        legacy = SocialApp.objects.create(
            provider="google",
            name="Legacy Google",
            client_id="123-legacy.apps.googleusercontent.com",
            secret="legacy-secret",
        )
        desired = SocialApp.objects.create(
            provider="google",
            name="Desired Google",
            client_id="123-example.apps.googleusercontent.com",
            secret="stale-secret",
        )
        legacy.sites.add(site)
        desired.sites.add(site)

        stdout, stderr = self.run_command(self.complete_env())

        self.assertEqual(stderr, "")
        self.assertEqual(SocialApp.objects.filter(provider="google").count(), 1)
        remaining = SocialApp.objects.get(provider="google")
        self.assertEqual(remaining.pk, desired.pk)
        self.assertEqual(remaining.name, "Google")
        self.assertEqual(remaining.secret, "google-test-secret")
        self.assertEqual(list(remaining.sites.values_list("pk", flat=True)), [1])
        self.assertIn("Google OAuth duplicate applications removed: 1", stdout)
