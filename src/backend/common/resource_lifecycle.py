"""Optional extension notification for lifecycle changes that bypass model saves."""

from django.dispatch import Signal


resource_lifecycle_changed = Signal()


def notify_resource_lifecycle_changed(
    *, organization_id: int, resource_type: str, resource_id: int,
) -> None:
    """Notify listeners; Community has no governance cleanup receiver."""
    resource_lifecycle_changed.send(
        sender=None, organization_id=organization_id,
        resource_type=resource_type, resource_id=resource_id,
    )
