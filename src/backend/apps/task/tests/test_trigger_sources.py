from importlib import import_module

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.iam.models import Organization
from apps.storage.repositories.models import Repository, RepositoryTask
from apps.task.api.serializers.task import TaskSerializer
from apps.task.models import Task
from apps.task.selectors.interface import (
    EXECUTION_HISTORY_PAGE_SIZE,
    get_task,
    list_tasks,
    repository_execution_history,
)
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
        ids = set(list_tasks(
            organization_id=self.org.id, collapse_replacements=True,
        ).values_list("id", flat=True))
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

    def test_exact_uuid_search_keeps_replaced_executions_discoverable(self):
        original = self.operation()
        self.operation(parent=original)
        for search_field in ("uuid", None):
            rows = list_tasks(
                organization_id=self.org.id, collapse_replacements=True,
                search=str(original.task_uuid), search_field=search_field,
            )
            self.assertEqual(list(rows.values_list("id", flat=True)), [original.id])
        self.assertIn(original.id, list_tasks(
            organization_id=self.org.id,
        ).values_list("id", flat=True))

    def test_history_paginates_long_chains_with_one_query_per_page(self):
        current = None
        expected = set()
        for _ in range(105):
            current = self.operation(parent=current)
            expected.add(current.id)
        _ = current.repository_operation
        cursor = None
        seen = set()
        pages = 0
        while True:
            with self.assertNumQueries(1):
                page = repository_execution_history(task=current, before_id=cursor)
            ids = [task.id for task in page.records]
            self.assertEqual(ids, sorted(ids))
            self.assertLessEqual(len(ids), EXECUTION_HISTORY_PAGE_SIZE)
            self.assertFalse(seen.intersection(ids))
            seen.update(ids)
            pages += 1
            if page.next_cursor is None:
                break
            self.assertEqual(page.next_cursor, min(ids))
            if cursor is not None:
                self.assertLess(page.next_cursor, cursor)
            cursor = page.next_cursor
        self.assertEqual(seen, expected)
        self.assertEqual(pages, 6)
        serializer = TaskSerializer(current, context={"include_execution_history": True})
        with self.assertNumQueries(1):
            self.assertEqual(
                len(serializer.get_execution_history(current)), EXECUTION_HISTORY_PAGE_SIZE,
            )
            self.assertIsNotNone(serializer.get_execution_history_next_cursor(current))

    def test_history_terminates_cycles_and_fences_foreign_chain_members(self):
        root = self.operation()
        last = self.operation(parent=root)
        Task.objects.filter(pk=root.pk).update(replaces_task=last)
        with self.assertNumQueries(1):
            page = repository_execution_history(task=last)
        self.assertEqual([task.id for task in page.records], [root.id, last.id])

        root = self.operation()
        foreign = self.operation(parent=root)
        Task.objects.filter(pk=foreign.pk).update(organization_id=self.org.id + 1000)
        self.operation(parent=foreign)
        self.assertEqual(
            [task.id for task in repository_execution_history(task=root).records],
            [root.id],
        )
        other_repository = Repository.objects.create(
            organization_id=self.org.id, name="Other history", repo_type=Repository.Type.S3,
        )
        root = self.operation()
        foreign = self.operation(parent=root)
        RepositoryTask.objects.filter(task=foreign).update(repository=other_repository)
        self.operation(parent=foreign)
        self.assertEqual(
            [task.id for task in repository_execution_history(task=root).records],
            [root.id],
        )

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

    def test_data_migration_bounds_queries_for_long_chains_and_batches_updates(self):
        migration = import_module(
            "apps.task.migrations.0016_normalize_retry_trigger_sources"
        )
        for count in (105, 1105):
            with self.subTest(count=count):
                original = self.operation(source="hook")
                retries = Task.objects.bulk_create([
                    Task(
                        organization_id=self.org.id,
                        task_type=original.task_type, display_name="Legacy recovery",
                        status=Task.Status.FAILED, trigger_type="retry",
                    )
                    for _ in range(count)
                ])
                for previous, current in zip([original] + retries, retries):
                    current.replaces_task_id = previous.id
                Task.objects.bulk_update(retries, ["replaces_task"], batch_size=500)
                # Imported chains need not have parents with smaller IDs.
                root = self.operation(source="api")
                reversed_chain = [self.operation(source="retry") for _ in range(3)]
                for child, parent in zip(reversed_chain, reversed_chain[1:] + [root]):
                    Task.objects.filter(pk=child.pk).update(replaces_task_id=parent.pk)
                with connection.schema_editor(atomic=False) as editor:
                    with CaptureQueriesContext(connection) as queries:
                        migration.normalize_retry_sources(apps, editor)
                self.assertLessEqual(len(queries), 8)
                updates = [
                    query["sql"] for query in queries
                    if query["sql"].lstrip().upper().startswith("UPDATE")
                ]
                self.assertEqual(len(updates), (count + 499) // 500 + 1)
                self.assertEqual(Task.objects.filter(
                    pk__in=[row.pk for row in retries], trigger_type="hook",
                ).count(), count)
                self.assertEqual(Task.objects.filter(
                    pk__in=[row.pk for row in reversed_chain], trigger_type="api",
                ).count(), 3)
                with connection.schema_editor(atomic=False) as editor:
                    with self.assertNumQueries(1):
                        migration.normalize_retry_sources(apps, editor)

    def test_data_migration_terminates_cycles_and_respects_org_and_type_boundaries(self):
        cycle_start = self.operation(source="retry")
        cycle_end = self.operation(source="retry", parent=cycle_start)
        Task.objects.filter(pk=cycle_start.pk).update(replaces_task=cycle_end)

        foreign_root = self.operation(source="api")
        Task.objects.filter(pk=foreign_root.pk).update(organization_id=self.org.id + 1000)
        foreign_child = self.operation(
            source="retry", parent=foreign_root, kind="cleanup.repository",
        )
        different_type = Task.objects.create(
            organization_id=self.org.id, task_type=Task.Type.BACKUP,
            display_name="Foreign type", trigger_type="hook",
        )
        type_child = self.operation(source="retry", parent=different_type)
        null_root = self.operation(source="hook")
        null_child = self.operation(source="retry", parent=null_root)
        Task.objects.filter(pk__in=[null_root.pk, null_child.pk]).update(organization_id=None)
        migration = import_module(
            "apps.task.migrations.0016_normalize_retry_trigger_sources"
        )
        with connection.schema_editor(atomic=False) as editor:
            migration.normalize_retry_sources(apps, editor)
        for task, expected in (
            (cycle_start, "system"), (cycle_end, "system"),
            (foreign_child, "manual"), (type_child, "system"), (null_child, "hook"),
        ):
            task.refresh_from_db()
            self.assertEqual(task.trigger_type, expected)
