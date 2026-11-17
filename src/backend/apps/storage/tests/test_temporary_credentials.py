"""Non-database checks for temporary credential handling on the Host."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.core.exceptions import ValidationError

from apps.storage.services.internal.kopia_cli import _environment
from apps.storage.services.internal.repository_provider import managed_repository_provider
from apps.storage.services.internal.repository_secrets import (
    sanitize_repository_config, scrub_secrets,
)


class TemporaryCredentialHandlingTests(SimpleTestCase):
    def test_session_tokens_are_not_public_configuration(self):
        config = sanitize_repository_config({
            "bucket": "test-bucket", "session_token": "test-session-token",
            "secret_access_key": "test-secret",
        })
        self.assertEqual(config, {"bucket": "test-bucket"})
        value = scrub_secrets({
            "session_token": "test-session-token",
            "securitytoken": "test-huawei-token",
            "credential_lease": {"token": "test-execution-capability"},
            "detail": "session_token=test-session-token securitytoken=test-huawei-token",
        })
        self.assertNotIn("test-session-token", str(value))
        self.assertNotIn("test-huawei-token", str(value))
        self.assertNotIn("test-execution-capability", str(value))

    def test_kopia_does_not_inherit_another_repository_session(self):
        repository = SimpleNamespace(config={"access_key_id": "test-access"})
        with (
            patch.dict("os.environ", {
                "AWS_SESSION_TOKEN": "unrelated-session",
                "KOPIA_S3_CREDENTIALS_FILE": "/unrelated/identity.json",
            }),
            patch(
                "apps.storage.services.internal.kopia_cli.resolve_repository_secrets",
                return_value={"secret_access_key": "test-secret", "kopia_password": "test-password"},
            ),
        ):
            env = _environment(repository)
        self.assertEqual(env["AWS_SESSION_TOKEN"], "")
        self.assertEqual(env["AWS_SECRET_ACCESS_KEY"], "test-secret")
        self.assertNotIn("KOPIA_S3_CREDENTIALS_FILE", env)

    def test_unsupported_managed_credential_mode_never_falls_back_to_legacy(self):
        with self.assertRaises(ValidationError):
            managed_repository_provider(SimpleNamespace(config={"credential_mode": "unknown-mode"}))
