"""Platform-owned Gateway selection and runtime defaults."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Count, Q

from apps.iam.models import Organization
from apps.lens_bridge.models import LensGatewayChatSlot, LensGatewayLink, LensSessionLink
from apps.lens_bridge.services.gateway_readiness import (
    gateway_runtime_state,
    require_hfl_usable_gateway,
)
from apps.lens_bridge.services.gateway_ownership import organization_gateway_links

NO_PUBLIC_DATA_GATEWAY_AVAILABLE = (
    "No public Data Gateway is available. Select a private Data Gateway "
    "or contact your administrator."
)


def platform_gateway_links():
    return LensGatewayLink.objects.filter(
        organization__isnull=True,
        scope=LensGatewayLink.GatewayScope.PLATFORM,
        gateway__scope="platform",
        gateway__organization__isnull=True,
    ).select_related("gateway")


def _first_eligible(links) -> LensGatewayLink | None:
    for link in links:
        if gateway_runtime_state(link)["copilot_eligible"]:
            return link
    return None


def _platform_gateway_links(*, require_lensnode: bool = False):
    links = (
        LensGatewayLink.objects.filter(
            organization__isnull=True,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            gateway__scope="platform",
            gateway__organization__isnull=True,
            is_deleted=False,
        )
        .select_related("gateway")
        .order_by("id")
    )
    if require_lensnode:
        links = links.filter(sl_lensnode_uuid__isnull=False)
    return links


def _bulk_chat_load(link_ids: list[int]) -> dict[int, int]:
    """Return active prepare slots + queued preparations per gateway link."""
    id_list = [int(x) for x in link_ids]
    if not id_list:
        return {}
    active = {
        int(row["gateway_link_id"]): int(row["count"])
        for row in (
            LensGatewayChatSlot.objects.filter(gateway_link_id__in=id_list)
            .exclude(
                Q(session_link__lifecycle_status=LensSessionLink.LifecycleStatus.READY)
                | Q(
                    session_link__lifecycle_status=LensSessionLink.LifecycleStatus.DELETED
                )
                | Q(
                    session_link__lifecycle_status=LensSessionLink.LifecycleStatus.FAILED,
                    session_link__cleanup_status=LensSessionLink.CleanupStatus.COMPLETE,
                )
            )
            .values("gateway_link_id")
            .annotate(count=Count("id"))
        )
    }
    queued = {
        int(row["gateway_link_id"]): int(row["count"])
        for row in (
            LensSessionLink.objects.filter(
                gateway_link_id__in=id_list,
                lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
                gateway_chat_slot__isnull=True,
            )
            .values("gateway_link_id")
            .annotate(count=Count("id"))
        )
    }
    return {
        link_id: int(active.get(link_id, 0)) + int(queued.get(link_id, 0))
        for link_id in id_list
    }


def _capacity_load_key(link: LensGatewayLink, used_bytes: int) -> tuple[float, int]:
    """Lower is better: ratio for capped gateways, absolute use for unlimited."""
    from apps.lens_bridge.services.public_gateway_capacity import (
        get_public_gateway_capacity_bytes,
    )

    capacity = get_public_gateway_capacity_bytes(gateway_link=link)
    used = max(0, int(used_bytes))
    if capacity < 0:
        return (0.0, used)
    if capacity == 0:
        return (1.0, used)
    return (min(1.0, used / float(capacity)), used)


def _health_gateway_score(link: LensGatewayLink, state: dict) -> tuple:
    """Prefer healthier platform Gateways. Public Default is ignored."""
    return (
        int(bool(state.get("copilot_eligible"))),
        int(bool(state.get("hfl_usable"))),
        int(bool(state.get("hfl_agent_online") and state.get("hfl_sidecar_online"))),
        int(bool(state.get("hfl_agent_online"))),
        link.gateway.last_seen_at.timestamp() if link.gateway.last_seen_at else 0.0,
        -link.id,
    )


def _pool_gateway_score(
    link: LensGatewayLink,
    state: dict,
    *,
    chat_load: int,
    used_bytes: int,
) -> tuple:
    """Among healthy Gateways prefer lower chat load then lower capacity use."""
    capacity_ratio, capacity_used = _capacity_load_key(link, used_bytes)
    return (
        int(bool(state.get("copilot_eligible"))),
        int(bool(state.get("hfl_usable"))),
        int(bool(state.get("hfl_agent_online") and state.get("hfl_sidecar_online"))),
        -int(chat_load),
        -float(capacity_ratio),
        -int(capacity_used),
        link.gateway.last_seen_at.timestamp() if link.gateway.last_seen_at else 0.0,
        -link.id,
    )


def resolve_platform_runtime_gateway_link() -> LensGatewayLink | None:
    """Resolve the platform Gateway that should represent runtime state.

    Prefer readiness over a stale default marker so Runtime Environment stays
    aligned with Copilot when an old local Gateway remains mis-marked.
    """
    candidates = list(_platform_gateway_links())
    if not candidates:
        return None
    scored = [
        (link, gateway_runtime_state(link))
        for link in candidates
    ]
    return max(
        scored,
        key=lambda item: _health_gateway_score(item[0], item[1]),
    )[0]


def resolve_platform_default_gateway_link() -> LensGatewayLink | None:
    """Resolve Auto Chat to a healthy, lower-load platform Gateway from the pool."""
    from apps.lens_bridge.services.public_gateway_capacity import (
        bulk_public_gateway_used_bytes,
    )

    candidates = list(_platform_gateway_links(require_lensnode=True))
    if not candidates:
        return None
    eligible = [
        (link, gateway_runtime_state(link))
        for link in candidates
    ]
    eligible = [
        (link, state)
        for link, state in eligible
        if state.get("copilot_eligible")
    ]
    if not eligible:
        return None
    link_ids = [int(link.id) for link, _state in eligible]
    chat_loads = _bulk_chat_load(link_ids)
    used_map = bulk_public_gateway_used_bytes(link_ids)
    return max(
        eligible,
        key=lambda item: _pool_gateway_score(
            item[0],
            item[1],
            chat_load=chat_loads.get(int(item[0].id), 0),
            used_bytes=used_map.get(int(item[0].id), (0, False))[0],
        ),
    )[0]


def resolve_organization_default_gateway_link(
    *, organization: Organization
) -> LensGatewayLink | None:
    """Resolve the first HFL-ready Private Gateway in an organization."""
    return _first_eligible(
        organization_gateway_links(organization=organization)
        .filter(sl_lensnode_uuid__isnull=False)
        .order_by("created_at", "id")
    )


def resolve_auto_gateway_link_for_copilot(*, user) -> LensGatewayLink | None:
    """Resolve Chat Auto to an HFL-ready platform gateway only."""
    return resolve_platform_default_gateway_link()


def resolve_gateway_link_for_copilot(
    org: Organization,
    *,
    user,
    gateway_link_id: int | None = None,
) -> LensGatewayLink:
    """Resolve DG for Copilot. Prefer platform pool; Auto → healthy/low-load pick.

    Never creates or deletes a DG — only selects an existing SL-admin gateway link.
    """
    from rest_framework.exceptions import ValidationError

    if gateway_link_id:
        link = (
            platform_gateway_links()
            .filter(pk=gateway_link_id, sl_lensnode_uuid__isnull=False)
            .first()
        )
        if link is None:
            link = (
                organization_gateway_links(organization=org)
                .filter(pk=gateway_link_id, sl_lensnode_uuid__isnull=False)
                .first()
            )
        if link is None:
            raise ValidationError({"gateway_link_id": "Data gateway is not available."})
        from apps.lens_bridge.services.gateway_execution import context_for_gateway_link

        context_for_gateway_link(
            tenant_organization=org,
            gateway_link=link,
        )
        return link

    platform_pick = resolve_auto_gateway_link_for_copilot(user=user)
    if platform_pick is not None:
        return platform_pick

    raise ValidationError(
        {"gateway_link_id": NO_PUBLIC_DATA_GATEWAY_AVAILABLE}
    )


@transaction.atomic
def set_platform_default_gateway(*, gateway_link_id: int) -> LensGatewayLink:
    """Legacy marker writer kept for installer/migrations; Auto selection ignores it."""
    from apps.node.services.internal.ownership import lock_platform_infrastructure

    lock_platform_infrastructure()
    link = LensGatewayLink.objects.select_for_update().get(
        pk=gateway_link_id,
        organization__isnull=True,
        scope=LensGatewayLink.GatewayScope.PLATFORM,
    )
    require_hfl_usable_gateway(link)
    LensGatewayLink.objects.filter(
        organization__isnull=True,
        scope=LensGatewayLink.GatewayScope.PLATFORM,
        is_platform_default=True,
    ).exclude(pk=link.pk).update(is_platform_default=False)
    link.is_platform_default = True
    link.save(update_fields=["is_platform_default", "updated_at"])
    return link
