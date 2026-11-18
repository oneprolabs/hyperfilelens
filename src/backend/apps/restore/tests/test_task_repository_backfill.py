"""Regression coverage for historical restore task repository associations."""

from importlib import import_module
from types import SimpleNamespace

from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from apps.restore.models import RestoreRecord, RestoreRecordItem
from apps.task.models import Task, TaskResource


class RestoreTaskRepositoryBackfillTests(TestCase):
    def test_backfill_preserves_sources_and_is_idempotent_for_both_restore_types(self):
        expected = set()
        for task_type in (Task.Type.RESTORE, Task.Type.INSIGHT_WORKSPACE_RESTORE):
            task = Task.objects.create(
                organization_id=801,
                task_type=task_type,
                display_name="Historical restore",
            )
            TaskResource.objects.create(
                task=task,
                resource_type="backup_source",
                resource_subtype="agent",
                resource_id=11,
                is_primary=True,
            )
            record = RestoreRecord.objects.create(
                organization_id=801,
                requesting_organization_id=801,
                target_execution_organization_id=801,
                target_execution_node_id=51,
                restore_uid=f"historical-{task.pk}",
                source_mode="manual",
                task_id=task.pk,
                task_uuid=task.task_uuid,
                source_type="agent",
                source_ref_id=11,
                backup_config_id=21,
                source_snapshot_id=41,
                target_type="agent",
                target_ref_id=61,
                target_path="/restore",
                scope="snapshot",
                conflict_mode="skip",
            )
            for directory_id, repository_id in ((1, 31), (2, 31), (3, 32)):
                RestoreRecordItem.objects.create(
                    organization_id=801,
                    restore_record=record,
                    source_snapshot_directory_id=directory_id,
                    backup_config_dir_id=directory_id,
                    repository_id=repository_id,
                    kopia_snapshot_id=f"snapshot-{directory_id}",
                    source_path="/data",
                    target_path="/restore",
                    conflict_mode="skip",
                )
                expected.add((task.pk, repository_id))
            TaskResource.objects.create(
                task=task, resource_type="repository", resource_id=31,
            )

        migration_name = "0010_backfill_task_repository_resources"
        historical_apps = MigrationLoader(connection).project_state(
            [("restore", migration_name)],
        ).apps
        backfill = import_module(
            f"apps.restore.migrations.{migration_name}",
        ).backfill_task_repository_resources
        for _ in range(2):
            backfill(historical_apps, SimpleNamespace(connection=connection))
            repositories = TaskResource.objects.filter(resource_type="repository")
            self.assertCountEqual(
                repositories.values_list("task_id", "resource_id"), expected,
            )
        self.assertEqual(
            TaskResource.objects.filter(
                resource_type="backup_source", is_primary=True,
            ).count(),
            2,
        )
