"""Publish user-scoped in-app notifications."""

from __future__ import annotations

import logging
from functools import partial

from django.db import transaction
from django.utils import timezone

from apps.iam.models import Membership
from apps.notification.models import UserNotification

logger = logging.getLogger(__name__)


def publish_to_org_members(
    *,
    organization_id: int,
    event_type: str,
    source_type: str,
    source_id: str,
    title: str,
    summary: str = "",
    severity: str = "info",
    target_url: str = "",
) -> int:
    """Create or refresh an inbox item for every active organization member."""
    user_ids = list(
        Membership.objects.filter(
            organization_id=organization_id,
            is_active=True,
            user__is_active=True,
        ).values_list("user_id", flat=True)
    )
    if not user_ids:
        return 0

    now = timezone.now()
    notifications = [
        UserNotification(
            user_id=user_id,
            organization_id=organization_id,
            event_type=event_type,
            source_type=source_type,
            source_id=str(source_id),
            title=title,
            summary=summary,
            severity=severity,
            target_url=target_url,
            read_at=None,
            created_at=now,
            updated_at=now,
        )
        for user_id in user_ids
    ]
    UserNotification.objects.bulk_create(
        notifications,
        update_conflicts=True,
        update_fields=[
            "title",
            "summary",
            "severity",
            "target_url",
            "read_at",
            "updated_at",
        ],
        unique_fields=[
            "user",
            "organization",
            "event_type",
            "source_type",
            "source_id",
        ],
    )
    return len(notifications)


def _enqueue_user_notifications(payload: dict[str, object]) -> None:
    """Queue inbox fan-out without allowing broker errors to affect callers."""
    from apps.notification.tasks.in_app import publish_user_notifications

    try:
        publish_user_notifications.delay(**payload)
    except Exception:
        logger.exception(
            "Failed to enqueue user notifications for organization_id=%s event_type=%s",
            payload["organization_id"],
            payload["event_type"],
        )


def publish_to_org_members_after_commit(
    *,
    organization_id: int,
    event_type: str,
    source_type: str,
    source_id: str,
    title: str,
    summary: str = "",
    severity: str = "info",
    target_url: str = "",
) -> None:
    """Schedule inbox fan-out after the surrounding transaction commits."""
    payload: dict[str, object] = {
        "organization_id": organization_id,
        "event_type": event_type,
        "source_type": source_type,
        "source_id": str(source_id),
        "title": title,
        "summary": summary,
        "severity": severity,
        "target_url": target_url,
    }
    transaction.on_commit(partial(_enqueue_user_notifications, payload))
