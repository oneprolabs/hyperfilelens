"""Issue or reuse credentials for the installer-managed platform Gateway."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.node.api.views.enrollment_helpers import agent_control_plane_ws_url
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.internal.local_platform_gateway import (
    LOCAL_PLATFORM_GATEWAY_METADATA,
    ensure_local_platform_gateway_token,
    platform_gateway_api_base,
    reconcile_local_platform_gateway_links,
)

OUTPUT_PREFIX = "HFL_LOCAL_PLATFORM_GATEWAY_ENROLLMENT="


class Command(BaseCommand):
    """Emit the local platform Gateway enrollment payload for the host installer."""

    help = "Issue or reuse local platform Gateway enrollment credentials."

    def handle(self, *args, **options):
        try:
            api_base = platform_gateway_api_base()
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        token = ensure_local_platform_gateway_token()
        reconcile_local_platform_gateway_links()
        managed_node_ids = list(
            Node.objects.filter(
                organization=token.organization,
                role=NodeRole.GATEWAY,
                metadata__managed_by=LOCAL_PLATFORM_GATEWAY_METADATA["managed_by"],
                metadata__deployment_mode=LOCAL_PLATFORM_GATEWAY_METADATA[
                    "deployment_mode"
                ],
                metadata__install_key=LOCAL_PLATFORM_GATEWAY_METADATA["install_key"],
            ).values_list("id", flat=True)
        )
        payload = {
            "org_key": token.transport_key,
            "token": token.token,
            "api_base": api_base,
            "wss_url": agent_control_plane_ws_url(api_base),
            "managed_node_ids": managed_node_ids,
        }
        self.stdout.write(f"{OUTPUT_PREFIX}{json.dumps(payload, separators=(',', ':'))}")
