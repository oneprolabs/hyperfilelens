"""Register periodic tasks for protection recovery."""

from apps.protection import conf as protection_conf
from common.scheduling.registry import TASK_REGISTRY


def register_periodic_tasks() -> None:
    reconcile_interval = max(
        15,
        int(protection_conf.PROTECTION_BACKUP_RECONCILE_INTERVAL_SECONDS),
    )
    TASK_REGISTRY.add(
        name="protection_reconcile_backup_tasks",
        task="apps.protection.tasks.backup.reconcile_backup_tasks",
        schedule=reconcile_interval,
        args=(),
        kwargs={"limit": 100},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="protection_reconcile_interrupted_backup_tasks",
        task="apps.protection.tasks.backup.reconcile_interrupted_backup_tasks",
        schedule=reconcile_interval,
        args=(),
        kwargs={"limit": 100},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="protection_backup_policy_maintenance",
        task="apps.protection.tasks.policy_execution.run_backup_policy_maintenance",
        schedule=60,
        args=(),
        kwargs={"retention_limit": 100},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="protection_reconcile_stuck_backup_config_reset_tasks",
        task="apps.protection.tasks.backup_config_reset.reconcile_stuck_backup_config_reset_tasks_task",
        schedule=60,
        args=(),
        kwargs={"limit": 50},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="protection_reconcile_snapshot_delete_tasks",
        task="apps.protection.tasks.snapshot_delete.reconcile_snapshot_delete_tasks",
        schedule=60,
        args=(),
        kwargs={"limit": 100},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="protection_reconcile_backup_config_provision_tasks",
        task="apps.protection.tasks.backup_config_provision.reconcile_backup_config_provision_tasks",
        schedule=60,
        args=(),
        kwargs={"limit": 100},
        queue=None,
        enabled=True,
    )
