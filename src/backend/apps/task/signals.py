"""Product task lifecycle signals."""

from django.db.models.signals import pre_save
from django.dispatch import Signal, receiver

task_cancelled = Signal()
task_failed = Signal()
task_succeeded = Signal()
task_timed_out = Signal()
task_updated = Signal()


@receiver(pre_save, sender="task.Task", dispatch_uid="task.impersonation_attribution")
def bind_task_initiator(sender, instance, **kwargs):
    """Persist initiator provenance, not executable credentials, on new tasks."""
    from common.impersonation import attribution

    if instance._state.adding and attribution.get():
        instance.initiator_context = {"impersonation": dict(attribution.get())}


@receiver(pre_save, sender="task.TaskEvent", dispatch_uid="task.event_impersonation_attribution")
def bind_task_event_initiator(sender, instance, **kwargs):
    """Record each delegated operation without rewriting the task's creator."""
    from common.impersonation import attribution

    delegated = attribution.get()
    if instance._state.adding and delegated:
        instance.metadata = {
            **(instance.metadata or {}),
            "impersonation": dict(delegated),
        }
