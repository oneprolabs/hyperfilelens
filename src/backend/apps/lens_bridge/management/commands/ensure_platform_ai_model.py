"""Create or update the deployment-managed platform AI model."""

from __future__ import annotations

import json
import sys
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from apps.lens_bridge.services import sl_client
from apps.lens_bridge.services.deployment_ai_model import (
    DeploymentAiModelConfig,
    DeploymentAiModelConfigurationError,
    ensure_platform_ai_model,
    repair_existing_platform_ai_model,
)

MAX_INPUT_BYTES = 16 * 1024


class Command(BaseCommand):
    """Apply one complete AI model configuration supplied through stdin."""

    help = "Create or update the deployment-managed platform AI model from JSON stdin."

    def handle(self, *args: Any, **options: Any) -> None:
        raw = sys.stdin.read(MAX_INPUT_BYTES + 1)
        if len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
            raise CommandError("AI model configuration exceeds the allowed size.")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CommandError("AI model configuration must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise CommandError("AI model configuration must be a JSON object.")

        try:
            role = str(payload.get("role") or "agent").strip().lower()
            if role not in {"agent", "multimodal"}:
                raise DeploymentAiModelConfigurationError(
                    "role must be agent or multimodal"
                )
            if payload.get("repair_existing") is True:
                repaired = repair_existing_platform_ai_model(role=role)
                self.stdout.write(
                    f"HFL_AI_MODEL_REPAIRED={'true' if repaired else 'false'}"
                )
                return
            config = DeploymentAiModelConfig.from_mapping(payload)
            result = ensure_platform_ai_model(config, role=role)
        except DeploymentAiModelConfigurationError as exc:
            raise CommandError(str(exc)) from exc
        except sl_client.LensBridgeError as exc:
            raise CommandError(
                "Unable to apply the deployment-managed AI model in SourceLens."
            ) from exc
        except DatabaseError as exc:
            raise CommandError(
                "Unable to persist the deployment-managed AI model link."
            ) from exc

        if role == "agent" and not result.applied:
            raise CommandError(
                "The required Agent model candidate failed connectivity "
                "validation; the installed model was preserved."
            )

        if result.applied:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Deployment-managed {role} AI model "
                    f"{result.action} successfully."
                )
            )
        else:
            self.stderr.write(
                self.style.WARNING(
                    f"Deployment-managed {role} AI model was not applied; "
                    "the installed default was preserved."
                )
            )
        self.stdout.write(f"HFL_AI_MODEL_STATUS={result.action}")
        self.stdout.write(
            f"HFL_AI_MODEL_APPLIED={'true' if result.applied else 'false'}"
        )
        if not result.connectivity_ok:
            self.stdout.write("HFL_AI_MODEL_CONNECTIVITY=failed")
            self.stderr.write(
                self.style.WARNING(
                    "AI model connectivity test failed; core deployment remains healthy."
                )
            )
        else:
            self.stdout.write("HFL_AI_MODEL_CONNECTIVITY=passed")
