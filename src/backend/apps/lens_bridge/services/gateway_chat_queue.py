"""Durable per-Data-Gateway admission and scheduling for heavy Chat preparation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from common.errors import AppError
from apps.lens_bridge.models import (
    LensGatewayChatSlot,
    LensGatewayLink,
    LensSessionLink,
)

DEFAULT_CHAT_PREPARE_CONCURRENCY = 1
DEFAULT_CHAT_QUEUE_CAPACITY = 10
MAX_CHAT_PREPARE_CONCURRENCY = 32
MAX_CHAT_QUEUE_CAPACITY = 1000
# Slot release/configuration changes wake eligible sessions immediately. This
# is the normal delayed retry cadence; the periodic reconciler remains the
# durable fallback. Keep it slow enough that a large waiting queue cannot
# flood the worker broker.
QUEUE_RETRY_SECONDS = 60


@dataclass(frozen=True)
class ChatSlotResult:
    acquired: bool
    position: int
    retry_after_seconds: int = QUEUE_RETRY_SECONDS


def _strict_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped.lstrip("-").isdigit():
            return int(stripped)
    raise ValueError(f"{field} must be an integer")


def normalize_chat_workload_settings(
    *,
    chat_prepare_concurrency: Any,
    chat_queue_capacity: Any,
) -> tuple[int, int]:
    concurrency = _strict_int(
        chat_prepare_concurrency,
        field="chat_prepare_concurrency",
    )
    queue_capacity = _strict_int(
        chat_queue_capacity,
        field="chat_queue_capacity",
    )
    if not 1 <= concurrency <= MAX_CHAT_PREPARE_CONCURRENCY:
        raise ValueError(
            "chat_prepare_concurrency must be between 1 and "
            f"{MAX_CHAT_PREPARE_CONCURRENCY}"
        )
    if not 0 <= queue_capacity <= MAX_CHAT_QUEUE_CAPACITY:
        raise ValueError(
            "chat_queue_capacity must be between 0 and "
            f"{MAX_CHAT_QUEUE_CAPACITY}"
        )
    return concurrency, queue_capacity


def _queued_sessions(gateway_link_id: int):
    return LensSessionLink.objects.filter(
        gateway_link_id=gateway_link_id,
        lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
        gateway_chat_slot__isnull=True,
    ).annotate(
        queue_order_at=Coalesce("gateway_queue_entered_at", "created_at"),
    )


def _ordered_queued_sessions(gateway_link_id: int):
    return _queued_sessions(gateway_link_id).order_by("queue_order_at", "id")


def _occupied_slots(gateway_link_id: int):
    return LensGatewayChatSlot.objects.filter(
        gateway_link_id=gateway_link_id,
    ).exclude(
        Q(session_link__lifecycle_status=LensSessionLink.LifecycleStatus.READY)
        | Q(session_link__lifecycle_status=LensSessionLink.LifecycleStatus.DELETED)
        | Q(
            session_link__lifecycle_status=LensSessionLink.LifecycleStatus.FAILED,
            session_link__cleanup_status=LensSessionLink.CleanupStatus.COMPLETE,
        )
    )


def active_chat_prepare_count(*, gateway_link_id: int) -> int:
    return _occupied_slots(gateway_link_id).count()


def chat_queue_positions(*, gateway_link_id: int) -> dict[int, int]:
    """Return one privacy-safe FIFO position map with a single queue query."""

    session_ids = _ordered_queued_sessions(gateway_link_id).values_list(
        "id",
        flat=True,
    )
    return {
        int(session_id): position
        for position, session_id in enumerate(session_ids, start=1)
    }


def chat_queue_position(*, session: LensSessionLink) -> int:
    if (
        session.gateway_link_id is None
        or session.lifecycle_status != LensSessionLink.LifecycleStatus.PROVISIONING
        or LensGatewayChatSlot.objects.filter(session_link_id=session.id).exists()
    ):
        return 0
    entered_at = session.gateway_queue_entered_at or session.created_at
    return _queued_sessions(session.gateway_link_id).filter(
        Q(queue_order_at__lt=entered_at)
        | Q(queue_order_at=entered_at, id__lt=session.id)
    ).count() + 1


def chat_queue_ahead(
    *,
    session: LensSessionLink,
    queue_position: int | None = None,
    active_count: int | None = None,
) -> int:
    position = (
        chat_queue_position(session=session)
        if queue_position is None
        else int(queue_position)
    )
    if position <= 0 or session.gateway_link_id is None:
        return 0
    active = (
        active_chat_prepare_count(gateway_link_id=session.gateway_link_id)
        if active_count is None
        else int(active_count)
    )
    return max(0, active + position - 1)


def chat_workload_payload(*, gateway_link: LensGatewayLink) -> dict[str, Any]:
    active_count = active_chat_prepare_count(gateway_link_id=gateway_link.id)
    queued = _queued_sessions(gateway_link.id)
    oldest_at = (
        _ordered_queued_sessions(gateway_link.id)
        .values_list("queue_order_at", flat=True)
        .first()
    )
    return {
        "gateway_link_id": gateway_link.id,
        "gateway_id": gateway_link.gateway_id,
        "gateway_name": gateway_link.gateway.name,
        "gateway_scope": gateway_link.scope,
        "chat_prepare_concurrency": int(gateway_link.chat_prepare_concurrency),
        "chat_queue_capacity": int(gateway_link.chat_queue_capacity),
        "active_chat_preparations": active_count,
        "queued_chat_preparations": queued.count(),
        "oldest_queued_at": oldest_at,
    }


@transaction.atomic
def set_chat_workload_settings(
    *,
    gateway_link: LensGatewayLink,
    chat_prepare_concurrency: Any,
    chat_queue_capacity: Any,
) -> LensGatewayLink:
    concurrency, queue_capacity = normalize_chat_workload_settings(
        chat_prepare_concurrency=chat_prepare_concurrency,
        chat_queue_capacity=chat_queue_capacity,
    )
    locked = (
        LensGatewayLink.objects.select_for_update()
        .select_related("gateway")
        .get(pk=gateway_link.pk)
    )
    _cleanup_releasable_slots(gateway_link_id=locked.id)
    update_fields: list[str] = []
    if locked.chat_prepare_concurrency != concurrency:
        locked.chat_prepare_concurrency = concurrency
        update_fields.append("chat_prepare_concurrency")
    if locked.chat_queue_capacity != queue_capacity:
        locked.chat_queue_capacity = queue_capacity
        update_fields.append("chat_queue_capacity")
    if update_fields:
        locked.save(update_fields=[*update_fields, "updated_at"])
        transaction.on_commit(lambda: wake_gateway_queue(locked.id))
    return locked


def assert_chat_queue_admission(*, gateway_link: LensGatewayLink) -> LensGatewayLink:
    """Lock one Gateway and reject only when its projected waiting queue is full."""

    locked = (
        LensGatewayLink.objects.select_for_update()
        .select_related("gateway")
        .get(pk=gateway_link.pk)
    )
    _cleanup_releasable_slots(gateway_link_id=locked.id)
    active = active_chat_prepare_count(gateway_link_id=locked.id)
    free_slots = max(0, int(locked.chat_prepare_concurrency) - active)
    pending_without_slot = _queued_sessions(locked.id).count()
    projected_waiting = max(0, pending_without_slot + 1 - free_slots)
    if projected_waiting > int(locked.chat_queue_capacity):
        raise AppError(
            code="INSIGHT.GATEWAY_CHAT_QUEUE_FULL",
            status=429,
            title="Data Gateway Chat queue is full",
            diagnostic=(
                "The selected Data Gateway has reached its Chat waiting queue "
                "capacity. Try again later or choose another Data Gateway."
            ),
            retryable=True,
            meta={
                "gateway_id": locked.gateway_id,
                "gateway_link_id": locked.id,
                "chat_prepare_concurrency": int(locked.chat_prepare_concurrency),
                "chat_queue_capacity": int(locked.chat_queue_capacity),
                "active_chat_preparations": active,
                "queued_chat_preparations": pending_without_slot,
            },
        )
    return locked


def _cleanup_releasable_slots(*, gateway_link_id: int) -> None:
    """Recover slots whose owning lifecycle has durably finished.

    A deleting or failed Chat may still be waiting for SourceLens to confirm
    conversion shutdown. Those slots intentionally remain occupied until the
    teardown path marks cleanup complete and releases them explicitly.
    """

    LensGatewayChatSlot.objects.filter(gateway_link_id=gateway_link_id).filter(
        Q(session_link__lifecycle_status=LensSessionLink.LifecycleStatus.READY)
        | Q(session_link__lifecycle_status=LensSessionLink.LifecycleStatus.DELETED)
        | Q(
            session_link__lifecycle_status=LensSessionLink.LifecycleStatus.FAILED,
            session_link__cleanup_status=LensSessionLink.CleanupStatus.COMPLETE,
        )
    ).delete()


@transaction.atomic
def try_acquire_chat_prepare_slot(
    *,
    session_link_id: int,
    expected_generation: int,
) -> ChatSlotResult:
    session = (
        LensSessionLink.objects.select_for_update()
        .filter(pk=session_link_id)
        .first()
    )
    if (
        session is None
        or session.gateway_link_id is None
        or session.lifecycle_status != LensSessionLink.LifecycleStatus.PROVISIONING
        or session.provision_generation != int(expected_generation)
    ):
        return ChatSlotResult(acquired=False, position=0)

    gateway = LensGatewayLink.objects.select_for_update().get(
        pk=session.gateway_link_id
    )
    _cleanup_releasable_slots(gateway_link_id=gateway.id)
    now = timezone.now()
    existing = LensGatewayChatSlot.objects.filter(session_link_id=session.id).first()
    if existing is not None:
        if existing.session_generation != session.provision_generation:
            raise AppError(
                code="INSIGHT.GATEWAY_CHAT_SLOT_CONFLICT",
                status=409,
                title="Data Gateway Chat slot is still in use",
                diagnostic=(
                    "A previous Chat preparation generation still owns this Data "
                    "Gateway slot. Cleanup must finish before retrying."
                ),
                retryable=True,
                meta={
                    "gateway_link_id": gateway.id,
                    "session_link_id": session.id,
                    "slot_generation": int(existing.session_generation),
                    "session_generation": int(session.provision_generation),
                },
            )
        existing.heartbeat_at = now
        existing.save(update_fields=["heartbeat_at", "updated_at"])
        return ChatSlotResult(acquired=True, position=0)

    head = _ordered_queued_sessions(gateway.id).first()
    position = chat_queue_position(session=session)
    if head is None or head.id != session.id:
        return ChatSlotResult(acquired=False, position=position)

    active_slots = list(
        LensGatewayChatSlot.objects.select_for_update()
        .filter(gateway_link_id=gateway.id)
        .order_by("slot_number")
    )
    concurrency = int(gateway.chat_prepare_concurrency)
    if len(active_slots) >= concurrency:
        return ChatSlotResult(acquired=False, position=position)
    occupied = {slot.slot_number for slot in active_slots}
    slot_number = next(
        number for number in range(1, concurrency + 1) if number not in occupied
    )
    LensGatewayChatSlot.objects.create(
        gateway_link=gateway,
        slot_number=slot_number,
        session_link=session,
        session_generation=session.provision_generation,
        lease_token=uuid.uuid4(),
        acquired_at=now,
        heartbeat_at=now,
    )
    return ChatSlotResult(acquired=True, position=0)


def heartbeat_chat_prepare_slot(*, session_link_id: int, generation: int) -> bool:
    return bool(
        LensGatewayChatSlot.objects.filter(
            session_link_id=session_link_id,
            session_generation=generation,
        ).update(heartbeat_at=timezone.now(), updated_at=timezone.now())
    )


@transaction.atomic
def release_chat_prepare_slot(
    *,
    session_link_id: int,
    expected_generation: int,
) -> int | None:
    """Release only the slot owned by the expected provisioning generation."""

    slot = (
        LensGatewayChatSlot.objects.select_for_update()
        .filter(
            session_link_id=session_link_id,
            session_generation=int(expected_generation),
        )
        .first()
    )
    if slot is None:
        return None
    gateway_link_id = slot.gateway_link_id
    slot.delete()
    transaction.on_commit(lambda: wake_gateway_queue(gateway_link_id))
    return gateway_link_id


def wake_gateway_queue(gateway_link_id: int) -> None:
    gateway = LensGatewayLink.objects.filter(pk=gateway_link_id).first()
    if gateway is None:
        return
    active = active_chat_prepare_count(gateway_link_id=gateway.id)
    available = max(0, int(gateway.chat_prepare_concurrency) - active)
    if available <= 0:
        return
    candidates = list(
        _ordered_queued_sessions(gateway.id)
        .values_list("id", flat=True)[:available]
    )
    if not candidates:
        return
    from apps.lens_bridge.services.chat_lifecycle import (
        _queue_provision_or_mark_failed,
    )

    LensSessionLink.objects.filter(id__in=candidates).update(
        provision_next_retry_at=timezone.now(),
        updated_at=timezone.now(),
    )
    for session_id in candidates:
        _queue_provision_or_mark_failed(session_id)


__all__ = [
    "ChatSlotResult",
    "active_chat_prepare_count",
    "assert_chat_queue_admission",
    "chat_queue_ahead",
    "chat_queue_position",
    "chat_queue_positions",
    "chat_workload_payload",
    "heartbeat_chat_prepare_slot",
    "normalize_chat_workload_settings",
    "release_chat_prepare_slot",
    "set_chat_workload_settings",
    "try_acquire_chat_prepare_slot",
    "wake_gateway_queue",
]
