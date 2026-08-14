"""Installer-managed local platform Gateway enrollment helpers."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.iam.models import Organization
from apps.lens_bridge.models import LensGatewayLink
from apps.lens_bridge.services import platform_lens
from apps.node.models import NodeToken
from apps.node.models.base import NodeRole
from common.deploy.site import enrollment_tls_verify, tenant_public_url

LOCAL_PLATFORM_GATEWAY_TOKEN_NOTE = "deploy:local-platform-gateway"
LOCAL_PLATFORM_GATEWAY_INSTALL_KEY = "local-platform-gateway"
LOCAL_PLATFORM_GATEWAY_METADATA = {
    "managed_by": "hfl-installer",
    "deployment_mode": "local-platform",
    "install_key": LOCAL_PLATFORM_GATEWAY_INSTALL_KEY,
}


def platform_gateway_api_base(*, require_remote: bool = False) -> str:
    """Return the configured tenant origin used by platform Data Gateways.

    Args:
        require_remote: Reject loopback hosts reserved for the installer-managed
            local platform Gateway.

    Raises:
        ValueError: If ``FRONTEND_URL`` is not an absolute HTTP(S) origin.
    """
    api_base = tenant_public_url()
    parsed = urlsplit(api_base)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(
            "FRONTEND_URL must be an absolute HTTP(S) origin "
            "for Data Gateway enrollment."
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (enrollment_tls_verify() and parsed.scheme != "https")
    ):
        raise ValueError(
            "FRONTEND_URL must be an absolute HTTP(S) origin for Data Gateway "
            "enrollment and must use HTTPS when TLS verification is enabled."
        )
    hostname = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(hostname)
        non_routable = address.is_loopback or address.is_unspecified
    except ValueError:
        non_routable = hostname == "localhost" or hostname.endswith(".localhost")
    if require_remote and non_routable:
        raise ValueError(
            "FRONTEND_URL must use a network-reachable host for remote Public "
            "Data Gateway enrollment. Configure the deployment public URL first."
        )
    return api_base.rstrip("/")


@transaction.atomic
def ensure_local_platform_gateway_token() -> NodeToken:
    """Return a reusable enrollment token for the installer-managed Gateway."""
    org = platform_lens.get_or_create_platform_org()
    Organization.objects.select_for_update().get(pk=org.pk)
    now = timezone.now()
    token = (
        NodeToken.objects.select_for_update()
        .filter(
            organization=org,
            role=NodeRole.GATEWAY,
            note=LOCAL_PLATFORM_GATEWAY_TOKEN_NOTE,
            gateway_scope=LensGatewayLink.GatewayScope.PLATFORM,
            is_active=True,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .order_by("-created_at", "-id")
        .first()
    )
    if token is not None:
        return token
    return NodeToken.objects.create(
        organization=org,
        role=NodeRole.GATEWAY,
        note=LOCAL_PLATFORM_GATEWAY_TOKEN_NOTE,
        gateway_scope=LensGatewayLink.GatewayScope.PLATFORM,
    )


def is_local_platform_gateway_metadata(metadata: object) -> bool:
    """Return whether node metadata identifies the installer-managed Gateway."""
    return bool(
        isinstance(metadata, dict)
        and metadata.get("managed_by") == "hfl-installer"
        and metadata.get("deployment_mode") == "local-platform"
        and metadata.get("install_key") == LOCAL_PLATFORM_GATEWAY_INSTALL_KEY
    )


def registration_metadata(
    payload_metadata: object,
    *,
    token_note: str = "",
    existing_metadata: object = None,
) -> dict:
    """Preserve trusted installer ownership across Agent heartbeats."""
    metadata = dict(payload_metadata) if isinstance(payload_metadata, dict) else {}
    for key in LOCAL_PLATFORM_GATEWAY_METADATA:
        metadata.pop(key, None)
    installer_managed = (
        token_note == LOCAL_PLATFORM_GATEWAY_TOKEN_NOTE
        or is_local_platform_gateway_metadata(existing_metadata)
    )
    if installer_managed:
        metadata.update(LOCAL_PLATFORM_GATEWAY_METADATA)
    return metadata


@transaction.atomic
def reconcile_local_platform_gateway_links() -> int:
    """Repair installer-managed Gateway links without changing user Gateways."""
    from apps.node.models import Node

    org = platform_lens.get_or_create_platform_org()
    Organization.objects.select_for_update().get(pk=org.pk)
    managed_gateway_ids = list(
        Node.objects.filter(
            organization=org,
            role=NodeRole.GATEWAY,
            is_deleted=False,
            metadata__managed_by=LOCAL_PLATFORM_GATEWAY_METADATA["managed_by"],
            metadata__deployment_mode=LOCAL_PLATFORM_GATEWAY_METADATA[
                "deployment_mode"
            ],
            metadata__install_key=LOCAL_PLATFORM_GATEWAY_METADATA["install_key"],
        )
        .order_by("id")
        .values_list("id", flat=True)
    )
    if not managed_gateway_ids:
        return 0

    links = list(
        LensGatewayLink.objects.select_for_update()
        .filter(
            organization=org,
            gateway_id__in=managed_gateway_ids,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            is_deleted=False,
        )
        .order_by("id")
    )
    if not links:
        return 0

    changed = 0
    for link in links:
        update_fields = []
        if link.origin != LensGatewayLink.Origin.PLATFORM:
            link.origin = LensGatewayLink.Origin.PLATFORM
            update_fields.append("origin")
        if link.owner_user_id is not None:
            link.owner_user = None
            update_fields.append("owner_user")
        if update_fields:
            link.save(update_fields=[*update_fields, "updated_at"])
            changed += 1

    other_default_exists = LensGatewayLink.objects.filter(
        organization=org,
        scope=LensGatewayLink.GatewayScope.PLATFORM,
        is_platform_default=True,
        is_deleted=False,
    ).exclude(gateway_id__in=managed_gateway_ids).exists()
    preferred_id = None
    if not other_default_exists:
        preferred_id = next(
            (link.id for link in links if link.is_platform_default),
            links[0].id,
        )

    for link in links:
        should_be_default = link.id == preferred_id
        if link.is_platform_default != should_be_default:
            link.is_platform_default = should_be_default
            link.save(update_fields=["is_platform_default", "updated_at"])
            changed += 1

    return changed
