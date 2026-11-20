"""Deployments must not import environment defaults into runtime overrides."""

from __future__ import annotations

import io
from contextlib import contextmanager
from unittest.mock import patch

from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.db import DatabaseError
from django.test import TestCase, override_settings

from apps.configuration.models import PlatformRuntimeSetting
from apps.configuration.services import runtime_settings as runtime


@override_settings(
    FRONTEND_URL="https://127.0.0.1:11443",
    SITE_ID=1,
    HFL_ENTERPRISE_REGISTRATION_ENABLED=None,
    HFL_PERSONAL_REGISTRATION_ENABLED=False,
    HFL_EMAIL_SIGNUP_ENABLED=False,
    HFL_GOOGLE_OAUTH_ENABLED=False,
    GOOGLE_CLIENT_ID="",
    GOOGLE_CLIENT_SECRET="",
    TURNSTILE_ENABLED=False,
)
class EnsureDeploymentIdentitySettingsCommandTests(TestCase):
    @contextmanager
    def deployment(self, **overrides: str):
        env = {
            "HFL_EMAIL_SIGNUP_ENABLED": "true",
            "HFL_ENTERPRISE_REGISTRATION_ENABLED": "",
            "HFL_PERSONAL_REGISTRATION_ENABLED": "false",
            "HFL_GOOGLE_OAUTH_ENABLED": "true",
            "GOOGLE_CLIENT_ID": "123-example.apps.googleusercontent.com",
            "GOOGLE_CLIENT_SECRET": "never-print-google-secret",
            "TURNSTILE_ENABLED": "false",
            "TURNSTILE_SITE_KEY": "turnstile-test-site",
            "TURNSTILE_SECRET_KEY": "never-print-turnstile-secret",
            "EMAIL_BACKEND": runtime.SMTP_EMAIL_BACKEND,
            "EMAIL_HOST": "smtp.example.com",
            "EMAIL_PORT": "465",
            "EMAIL_HOST_USER": "mailer@example.com",
            "EMAIL_HOST_PASSWORD": "never-print-smtp-secret",
            "EMAIL_USE_TLS": "false",
            "EMAIL_USE_SSL": "true",
            "DEFAULT_FROM_EMAIL": "HyperFileLens <mailer@example.com>",
        }
        env.update(overrides)
        # SMTP uses Django settings populated at process startup, unlike the
        # identity getters which can read the environment directly.
        email_settings = {
            key: value
            for key, value in env.items()
            if key.startswith("EMAIL_") or key == "DEFAULT_FROM_EMAIL"
        }
        email_settings["EMAIL_PORT"] = int(env["EMAIL_PORT"])
        for key in ("EMAIL_USE_TLS", "EMAIL_USE_SSL"):
            email_settings[key] = env[key].lower() == "true"
        with (
            patch.dict("os.environ", env),
            override_settings(**email_settings),
            patch.object(runtime, "enterprise_identity_enabled", return_value=True),
        ):
            yield env

    def run_command(self) -> tuple[str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        call_command(
            "ensure_deployment_identity_settings",
            stdout=stdout,
            stderr=stderr,
        )
        return stdout.getvalue(), stderr.getvalue()

    def runtime_snapshot(self) -> list[dict]:
        return list(PlatformRuntimeSetting.objects.order_by("key").values())

    def test_deployment_uses_environment_without_materializing_overrides(self):
        with self.deployment() as env:
            first_stdout, first_stderr = self.run_command()
            second_stdout, second_stderr = self.run_command()
            self.assertFalse(PlatformRuntimeSetting.objects.exists())
            self.assertTrue(runtime.enterprise_registration_enabled())
            self.assertEqual(runtime.google_client_id(), env["GOOGLE_CLIENT_ID"])
            email = runtime.email_connection_kwargs()
            self.assertEqual(email["password"], env["EMAIL_HOST_PASSWORD"])
            self.assertEqual(email["source"], "deployment")
            self.assertIn(
                "Enterprise registration: enabled (deployment)", second_stdout
            )
            self.assertIn("Personal registration: disabled (deployment)", second_stdout)
            self.assertIn("SMTP: configured (deployment)", second_stdout)
            self.assertIn("HFL_IDENTITY_STATUS=applied", second_stdout)
            self.assertEqual(first_stderr + second_stderr, "")
            for key in (
                "GOOGLE_CLIENT_SECRET",
                "TURNSTILE_SECRET_KEY",
                "EMAIL_HOST_PASSWORD",
            ):
                self.assertNotIn(env[key], first_stdout + second_stdout)

        app = SocialApp.objects.get(provider="google")
        self.assertEqual(app.client_id, env["GOOGLE_CLIENT_ID"])
        self.assertEqual(list(app.sites.values_list("pk", flat=True)), [1])
        site = Site.objects.get(pk=1)
        self.assertEqual(site.domain, "127.0.0.1:11443")
        self.assertEqual(site.name, "127.0.0.1:11443")

    def test_registration_runtime_true_and_false_survive_redeployment(self):
        user = get_user_model().objects.create_user(username="settings-admin")
        flags = (
            (
                runtime.KEY_IDENTITY_PERSONAL_REGISTRATION,
                "HFL_PERSONAL_REGISTRATION_ENABLED",
            ),
            (
                runtime.KEY_IDENTITY_ENTERPRISE_REGISTRATION,
                "HFL_ENTERPRISE_REGISTRATION_ENABLED",
            ),
            (runtime.KEY_IDENTITY_EMAIL_SIGNUP, "HFL_EMAIL_SIGNUP_ENABLED"),
        )
        for enabled in (True, False):
            for key, env_name in flags:
                with self.subTest(key=key, enabled=enabled):
                    PlatformRuntimeSetting.objects.all().delete()
                    runtime.set_bool(key, enabled, user=user)
                    before = self.runtime_snapshot()
                    with self.deployment(**{env_name: str(not enabled).lower()}):
                        self.run_command()
                        self.run_command()
                        self.assertEqual(runtime.get_bool(key), enabled)
                    self.assertEqual(self.runtime_snapshot(), before)

    def test_environment_remains_live_without_database_override(self):
        for enabled in (True, False):
            with self.deployment(
                HFL_PERSONAL_REGISTRATION_ENABLED=str(enabled).lower()
            ):
                self.run_command()
                self.assertEqual(runtime.personal_registration_enabled(), enabled)
        self.assertFalse(PlatformRuntimeSetting.objects.exists())

    def test_explicit_modes_and_legacy_fallback_keep_existing_semantics(self):
        with self.deployment(
            HFL_EMAIL_SIGNUP_ENABLED="true",
            HFL_ENTERPRISE_REGISTRATION_ENABLED="false",
            HFL_PERSONAL_REGISTRATION_ENABLED="true",
        ):
            self.run_command()
            self.assertFalse(runtime.enterprise_registration_enabled())
            self.assertTrue(runtime.personal_registration_enabled())
        with self.deployment(HFL_ENTERPRISE_REGISTRATION_ENABLED=""):
            self.run_command()
            self.assertTrue(runtime.enterprise_registration_enabled())
        self.assertFalse(PlatformRuntimeSetting.objects.exists())

    def test_missing_environment_uses_default_without_creating_rows(self):
        with self.deployment(
            HFL_EMAIL_SIGNUP_ENABLED="",
            HFL_PERSONAL_REGISTRATION_ENABLED="",
            HFL_GOOGLE_OAUTH_ENABLED="",
            TURNSTILE_ENABLED="",
        ):
            stdout, _ = self.run_command()
            self.assertFalse(runtime.personal_registration_enabled())
            self.assertIn("Personal registration: disabled (default)", stdout)
        self.assertFalse(PlatformRuntimeSetting.objects.exists())

    def test_blank_or_invalid_environment_cannot_replace_runtime_flag(self):
        runtime.set_bool(runtime.KEY_IDENTITY_PERSONAL_REGISTRATION, True)
        before = self.runtime_snapshot()
        for value in ("", "not-a-boolean"):
            with self.deployment(HFL_PERSONAL_REGISTRATION_ENABLED=value):
                stdout, stderr = self.run_command()
                self.assertTrue(runtime.personal_registration_enabled())
                self.assertIn("Personal registration: enabled (runtime)", stdout)
                self.assertNotIn("Invalid deployment boolean", stderr)
        self.assertEqual(self.runtime_snapshot(), before)

    def test_invalid_deployment_boolean_warns_without_creating_override(self):
        with self.deployment(HFL_PERSONAL_REGISTRATION_ENABLED="not-a-boolean"):
            stdout, stderr = self.run_command()
            self.assertIn(
                "Invalid deployment boolean for HFL_PERSONAL_REGISTRATION_ENABLED",
                stderr,
            )
            self.assertIn("HFL_IDENTITY_STATUS=warning", stdout)
        self.assertFalse(PlatformRuntimeSetting.objects.exists())

    def test_google_runtime_credentials_win_even_when_deployment_disables_google(self):
        runtime.set_bool(runtime.KEY_IDENTITY_GOOGLE_OAUTH, True)
        runtime.set_value(
            key=runtime.KEY_IDENTITY_GOOGLE_CLIENT_ID,
            value="123-runtime.apps.googleusercontent.com",
        )
        runtime.set_value(key=runtime.SECRET_KEY_GOOGLE, secret="runtime-google-secret")
        before = self.runtime_snapshot()
        with self.deployment(
            HFL_GOOGLE_OAUTH_ENABLED="false", GOOGLE_CLIENT_ID="invalid"
        ):
            stdout, stderr = self.run_command()
            self.assertTrue(runtime.google_oauth_enabled())
            self.assertIn("Google OAuth: enabled (runtime)", stdout)
        self.assertEqual(stderr, "")
        self.assertEqual(self.runtime_snapshot(), before)
        app = SocialApp.objects.get(provider="google")
        self.assertEqual(app.client_id, "123-runtime.apps.googleusercontent.com")
        self.assertEqual(app.secret, "runtime-google-secret")
        self.assertNotIn(app.secret, stdout + stderr)

    def test_google_runtime_disabled_is_not_reenabled(self):
        runtime.set_bool(runtime.KEY_IDENTITY_GOOGLE_OAUTH, False)
        before = self.runtime_snapshot()
        with self.deployment():
            stdout, _ = self.run_command()
            self.assertFalse(runtime.google_oauth_enabled())
            self.assertIn("Google OAuth: disabled (runtime)", stdout)
        self.assertFalse(SocialApp.objects.filter(provider="google").exists())
        self.assertEqual(self.runtime_snapshot(), before)

    def test_explicit_empty_runtime_value_is_not_replaced_by_environment(self):
        runtime.set_bool(runtime.KEY_IDENTITY_GOOGLE_OAUTH, True)
        runtime.set_value(key=runtime.KEY_IDENTITY_GOOGLE_CLIENT_ID, value="")
        runtime.set_value(key=runtime.KEY_EMAIL_HOST, value="")
        before = self.runtime_snapshot()
        with self.deployment():
            stdout, stderr = self.run_command()
            self.assertFalse(runtime.google_oauth_enabled())
            self.assertEqual(runtime.google_client_id(), "")
            self.assertEqual(runtime.email_connection_kwargs()["host"], "")
        self.assertIn("Invalid or missing effective Google OAuth client ID", stderr)
        self.assertIn("SMTP: unavailable (runtime)", stdout)
        self.assertEqual(self.runtime_snapshot(), before)

    def test_invalid_google_config_does_not_replace_existing_social_app(self):
        with self.deployment():
            self.run_command()
        for overrides in (
            {"GOOGLE_CLIENT_ID": "invalid-client"},
            {"GOOGLE_CLIENT_SECRET": "invalid\nsecret"},
            {"GOOGLE_CLIENT_SECRET": ""},
        ):
            with self.subTest(overrides=overrides), self.deployment(**overrides):
                stdout, stderr = self.run_command()
                self.assertIn("Invalid or missing effective Google OAuth", stderr)
                self.assertIn("HFL_IDENTITY_STATUS=warning", stdout)
                app = SocialApp.objects.get(provider="google")
                self.assertEqual(
                    app.client_id, "123-example.apps.googleusercontent.com"
                )
                self.assertEqual(app.secret, "never-print-google-secret")
        self.assertFalse(PlatformRuntimeSetting.objects.exists())

    def test_google_sync_failure_does_not_leak_or_modify_configuration(self):
        before = self.runtime_snapshot()
        for failure in (ValueError("private-secret"), DatabaseError("private-secret")):
            with (
                self.subTest(failure=type(failure)),
                self.deployment(),
                patch.object(
                    runtime,
                    "sync_google_social_app",
                    side_effect=failure,
                ),
            ):
                stdout, stderr = self.run_command()
                self.assertIn("Google OAuth synchronization failed", stderr)
                self.assertIn("HFL_IDENTITY_STATUS=warning", stdout)
                self.assertNotIn("private-secret", stdout + stderr)
        self.assertEqual(self.runtime_snapshot(), before)

    def test_turnstile_runtime_keys_and_policy_survive_deployment(self):
        runtime.set_bool(runtime.KEY_IDENTITY_TURNSTILE_ENABLED, True)
        runtime.set_value(key=runtime.KEY_IDENTITY_TURNSTILE_SITE, value="runtime-site")
        runtime.set_value(key=runtime.SECRET_KEY_TURNSTILE, secret="runtime-secret")
        before = self.runtime_snapshot()
        with self.deployment(
            TURNSTILE_ENABLED="false", TURNSTILE_SECRET_KEY="invalid secret"
        ):
            stdout, stderr = self.run_command()
            self.assertTrue(runtime.turnstile_enabled())
            self.assertEqual(runtime.turnstile_secret_key(), "runtime-secret")
        self.assertEqual(stderr, "")
        self.assertNotIn("runtime-secret", stdout)
        self.assertEqual(self.runtime_snapshot(), before)

    def test_invalid_turnstile_warns_without_disabling_policy(self):
        with self.deployment(
            TURNSTILE_ENABLED="true", TURNSTILE_SECRET_KEY="invalid secret"
        ):
            stdout, stderr = self.run_command()
            self.assertTrue(runtime.turnstile_enabled())
        self.assertIn("Invalid or missing effective Turnstile credentials", stderr)
        self.assertNotIn("invalid secret", stdout + stderr)
        self.assertFalse(PlatformRuntimeSetting.objects.exists())

    def test_smtp_runtime_values_and_partial_fallback_are_preserved(self):
        runtime.set_value(key=runtime.KEY_EMAIL_HOST, value="runtime.smtp.example.com")
        runtime.set_value(
            key=runtime.SECRET_KEY_EMAIL_PASSWORD, secret="runtime-mail-secret"
        )
        runtime.set_bool(runtime.KEY_EMAIL_USE_SSL, False)
        before = self.runtime_snapshot()
        with self.deployment():
            stdout, stderr = self.run_command()
            email = runtime.email_connection_kwargs()
            self.assertEqual(email["host"], "runtime.smtp.example.com")
            self.assertEqual(email["password"], "runtime-mail-secret")
            self.assertFalse(email["use_ssl"])
            self.assertEqual(email["username"], "mailer@example.com")
            self.assertIn("SMTP: configured (runtime)", stdout)
        self.assertEqual(stderr, "")
        self.assertNotIn("runtime-mail-secret", stdout)
        self.assertEqual(self.runtime_snapshot(), before)

    def test_invalid_smtp_warns_without_importing_credentials(self):
        with self.deployment(EMAIL_USE_TLS="true", EMAIL_USE_SSL="true") as env:
            stdout, stderr = self.run_command()
        self.assertIn("SMTP configuration is unavailable or invalid", stderr)
        self.assertIn("SMTP: unavailable (deployment)", stdout)
        self.assertNotIn(env["EMAIL_HOST_PASSWORD"], stdout + stderr)
        self.assertFalse(PlatformRuntimeSetting.objects.exists())

    def test_clearing_overrides_restores_environment_without_reseeding(self):
        runtime.set_bool(runtime.KEY_IDENTITY_PERSONAL_REGISTRATION, False)
        runtime.set_value(key=runtime.KEY_EMAIL_HOST, value="runtime.smtp.example.com")
        runtime.clear_identity_runtime_settings()
        runtime.clear_email_runtime_settings()
        with self.deployment(HFL_PERSONAL_REGISTRATION_ENABLED="true"):
            self.run_command()
            self.assertTrue(runtime.personal_registration_enabled())
            self.assertEqual(runtime.email_connection_kwargs()["source"], "deployment")
        self.assertFalse(PlatformRuntimeSetting.objects.exists())

    def test_community_does_not_enable_enterprise_identity_features(self):
        with (
            self.deployment(),
            patch.object(runtime, "enterprise_identity_enabled", return_value=False),
        ):
            stdout, _ = self.run_command()
            self.assertFalse(runtime.google_oauth_enabled())
            self.assertFalse(runtime.personal_registration_enabled())
            self.assertIn("Google OAuth: disabled", stdout)
        self.assertFalse(SocialApp.objects.filter(provider="google").exists())
        self.assertFalse(PlatformRuntimeSetting.objects.exists())

    def test_converges_legacy_google_duplicates_to_effective_application(self):
        site = Site.objects.get_current()
        for client_id in (
            "123-legacy.apps.googleusercontent.com",
            "123-example.apps.googleusercontent.com",
        ):
            app = SocialApp.objects.create(
                provider="google",
                name="Legacy",
                client_id=client_id,
                secret="stale-secret",
            )
            app.sites.add(site)
        with self.deployment():
            stdout, stderr = self.run_command()
        self.assertEqual(stderr, "")
        self.assertEqual(SocialApp.objects.filter(provider="google").count(), 1)
        app = SocialApp.objects.get(provider="google")
        self.assertEqual(app.client_id, "123-example.apps.googleusercontent.com")
        self.assertEqual(app.secret, "never-print-google-secret")
        self.assertEqual(list(app.sites.values_list("pk", flat=True)), [1])
        self.assertIn("Google OAuth duplicate applications removed: 1", stdout)
