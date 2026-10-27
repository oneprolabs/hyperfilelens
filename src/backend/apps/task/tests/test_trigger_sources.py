from importlib import import_module

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.storage.repositories.models import Repository, RepositoryTask
from apps.task.api.serializers.task import TaskSerializer
from apps.task.models import Task
from apps.task.selectors.interface import get_task, list_tasks
from apps.task.services.interface import create_task, retry_task
from apps.task.trigger import task_trigger_source


class TaskTriggerSourceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="trigger-sources", name="Triggers")
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="Trigger repository",
            repo_type=Repository.Type.S3,
        )

    def operation(self, *, source="system", parent=None, kind="maintenance.quick"):
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.REPOSITORY_OPERATION,
            display_name="Quick maintenance",
            status=Task.Status.FAILED,
            trigger_type=source,
            replaces_task=parent,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        RepositoryTask.objects.create(
            repository=self.repository,
            task=task,
            operation_type=kind,
            owner_type="controller",
            owner_identity="hfl-maintenance@controller",
        )
        return task

    def test_replacements_inherit_original_source_across_multiple_failures(self):
        for source in ("system", "manual", "api", "hook"):
            with self.subTest(source=source):
                original = self.operation(source=source)
                previous = original
                for attempt in range(1, 4):
                    replacement = create_task(
                        organization_id=self.org.id,
                        task_type=previous.task_type,
                        display_name=previous.display_name,
                        trigger_type=Task.TriggerType.RETRY,
                        normalize_trigger_type=False,
                        replaces_task=previous,
                    )
                    self.assertEqual(replacement.trigger_type, source)
                    self.assertEqual(replacement.recovery_attempt, attempt)
                    previous = replacement
                    previous.status = Task.Status.FAILED
                    previous.save(update_fields=["status"])

    def test_in_place_retry_keeps_source_and_internal_counter(self):
        original = self.operation(source="manual")
        retried = retry_task(
            task_uuid=original.task_uuid, organization_id=self.org.id,
        )
        self.assertEqual(retried.trigger_type, "manual")
        self.assertEqual(retried.retry_count, 1)

    def test_legacy_chain_serializes_root_source_without_mutating_records(self):
        original = self.operation(source="manual")
        retry = self.operation(source="retry", parent=original)
        final = self.operation(source="retry", parent=retry)
        self.assertEqual(task_trigger_source(final), "manual")
        self.assertEqual(TaskSerializer(final).data["trigger_type"], "manual")
        final.refresh_from_db()
        self.assertEqual(final.trigger_type, "retry")

    def test_unlinked_legacy_maintenance_is_system_but_cleanup_is_manual(self):
        maintenance = self.operation(source="retry")
        cleanup = self.operation(source="retry", kind="cleanup.repository")
        self.assertEqual(TaskSerializer(maintenance).data["trigger_type"], "system")
        self.assertEqual(TaskSerializer(cleanup).data["trigger_type"], "manual")
        self.assertIsNone(maintenance.replaces_task_id)

    def test_create_never_persists_retry_as_a_trigger_even_without_normalization(self):
        for normalize in (True, False):
            task = create_task(
                organization_id=self.org.id,
                task_type=Task.Type.BACKUP,
                display_name="Legacy caller",
                trigger_type=Task.TriggerType.RETRY,
                normalize_trigger_type=normalize,
            )
            self.assertEqual(task.trigger_type, "manual")

    def test_repository_lists_fold_only_explicit_replacement_chains(self):
        original = self.operation()
        replacement = self.operation(parent=original)
        unrelated = self.operation()
        backup = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Unrelated failed backup",
            status=Task.Status.FAILED,
        )
        Task.objects.create(
            organization_id=self.org.id,
            task_type=backup.task_type,
            display_name="Backup replacement",
            replaces_task=backup,
        )
        ids = set(list_tasks(organization_id=self.org.id).values_list("id", flat=True))
        self.assertNotIn(original.id, ids)
        self.assertIn(replacement.id, ids)
        self.assertIn(unrelated.id, ids)
        self.assertIn(backup.id, ids)
        self.assertIsNotNone(get_task(
            organization_id=self.org.id, task_uuid=original.task_uuid,
        ))

    def test_detail_history_has_only_linked_time_and_status_records(self):
        original = self.operation()
        middle = self.operation(parent=original)
        latest = self.operation(parent=middle)
        self.operation()  # A separate scheduled run must not enter the history.
        for task in (original, middle, latest):
            data = TaskSerializer(
                task, context={"include_execution_history": True},
            ).data
            self.assertEqual(
                [row["task_uuid"] for row in data["execution_history"]],
                [str(row.task_uuid) for row in (original, middle, latest)],
            )
            self.assertEqual(
                set(data["execution_history"][0]),
                {"task_uuid", "status", "started_at", "finished_at", "created_at"},
            )
        self.assertEqual(TaskSerializer(latest).data["execution_history"], [])

    def test_data_migration_preserves_results_and_links_and_is_idempotent(self):
        original = self.operation(source="api")
        retry = self.operation(source="retry", parent=original)
        unlinked = self.operation(source="retry")
        cleanup = self.operation(source="retry", kind="cleanup.repository")
        snapshots = {
            task.id: (task.status, task.started_at, task.finished_at, task.updated_at)
            for task in (original, retry, unlinked, cleanup)
        }
        migration = import_module(
            "apps.task.migrations.0016_normalize_retry_trigger_sources"
        )
        with connection.schema_editor(atomic=False) as editor:
            migration.normalize_retry_sources(apps, editor)
            migration.normalize_retry_sources(apps, editor)
        for task, expected in (
            (original, "api"), (retry, "api"), (unlinked, "system"), (cleanup, "manual"),
        ):
            task.refresh_from_db()
            self.assertEqual(task.trigger_type, expected)
            self.assertEqual(
                (task.status, task.started_at, task.finished_at, task.updated_at),
                snapshots[task.id],
            )
        self.assertEqual(retry.replaces_task_id, original.id)
        self.assertIsNone(unlinked.replaces_task_id)
