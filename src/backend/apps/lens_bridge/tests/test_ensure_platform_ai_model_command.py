from __future__ import annotations

import io
import json
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import SimpleTestCase

from apps.lens_bridge.services.deployment_ai_model import DeploymentAiModelResult


class EnsurePlatformAiModelCommandTests(SimpleTestCase):
    @patch(
        "apps.lens_bridge.management.commands.ensure_platform_ai_model."
        "ensure_platform_ai_model",
        return_value=DeploymentAiModelResult(
            action="updated",
            connectivity_ok=False,
            applied=False,
        ),
    )
    def test_required_agent_validation_failure_is_blocking_without_secret_echo(
        self,
        ensure_model,
    ):
        secret = "never-print-this-api-key"
        payload = json.dumps(
            {
                "provider": "openai_compatible",
                "model_id": "model/one",
                "display_name": "Model One",
                "api_base": "https://models.example/v1",
                "api_key": secret,
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch("sys.stdin", io.StringIO(payload)):
            with self.assertRaises(CommandError):
                call_command(
                    "ensure_platform_ai_model",
                    stdout=stdout,
                    stderr=stderr,
                )

        self.assertNotIn(secret, stdout.getvalue())
        self.assertNotIn(secret, stderr.getvalue())
        passed_config = ensure_model.call_args.args[0]
        self.assertEqual(passed_config.api_key, secret)

    @patch(
        "apps.lens_bridge.management.commands.ensure_platform_ai_model."
        "ensure_platform_ai_model",
        return_value=DeploymentAiModelResult(
            action="created",
            connectivity_ok=True,
        ),
    )
    def test_passes_explicit_multimodal_role(self, ensure_model):
        payload = json.dumps(
            {
                "role": "multimodal",
                "provider": "openai_compatible",
                "model_id": "qwen/qwen3.5-flash/683c8",
                "display_name": "Qwen 3.5 Flash",
                "api_base": "https://models.example/v1",
                "api_key": "deployment-secret",
            }
        )

        with patch("sys.stdin", io.StringIO(payload)):
            call_command(
                "ensure_platform_ai_model",
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

        self.assertEqual(
            ensure_model.call_args.kwargs,
            {"role": "multimodal"},
        )

    @patch(
        "apps.lens_bridge.management.commands.ensure_platform_ai_model."
        "ensure_platform_ai_model",
        return_value=DeploymentAiModelResult(
            action="recreated",
            connectivity_ok=False,
            applied=False,
        ),
    )
    def test_reports_preserved_default_for_rejected_multimodal(self, _ensure_model):
        payload = json.dumps(
            {
                "role": "multimodal",
                "provider": "openai_compatible",
                "model_id": "model/vision",
                "display_name": "Vision Model",
                "api_base": "https://models.example/v1",
                "api_key": "deployment-secret",
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch("sys.stdin", io.StringIO(payload)):
            call_command(
                "ensure_platform_ai_model",
                stdout=stdout,
                stderr=stderr,
            )

        self.assertIn("HFL_AI_MODEL_APPLIED=false", stdout.getvalue())
        self.assertIn("installed default was preserved", stderr.getvalue())

    @patch(
        "apps.lens_bridge.management.commands.ensure_platform_ai_model."
        "repair_existing_platform_ai_model",
        return_value=True,
    )
    def test_repairs_existing_model_without_requiring_credentials(self, repair_model):
        stdout = io.StringIO()

        with patch(
            "sys.stdin",
            io.StringIO(json.dumps({"role": "multimodal", "repair_existing": True})),
        ):
            call_command(
                "ensure_platform_ai_model",
                stdout=stdout,
                stderr=io.StringIO(),
            )

        repair_model.assert_called_once_with(role="multimodal")
        self.assertIn("HFL_AI_MODEL_REPAIRED=true", stdout.getvalue())
