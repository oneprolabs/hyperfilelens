"""Celery tasks for user-scoped in-app notifications."""

from celery import shared_task

from common.observability.celery_context import logged_celery_task

from apps.notification.services.internal.in_app import publish_to_org_members


@shared_task(name="apps.notification.tasks.in_app.publish_user_notifications")
@logged_celery_task(
    name="apps.notification.tasks.in_app.publish_user_notifications",
    trace_keys=("organization_id", "event_type", "source_type", "source_id"),
)
def publish_user_notifications(
    *,
    organization_id: int,
    event_type: str,
    source_type: str,
    source_id: str,
    title: str,
    summary: str = "",
    severity: str = "info",
    target_url: str = "",
) -> dict[str, int]:
    """Fan out one product event to active members' inboxes."""
    published = publish_to_org_members(
        organization_id=organization_id,
        event_type=event_type,
        source_type=source_type,
        source_id=source_id,
        title=title,
        summary=summary,
        severity=severity,
        target_url=target_url,
    )
    return {"published": published}
