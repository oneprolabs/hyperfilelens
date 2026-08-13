from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class InsightWorkspaceRestoreTaskMigrationTests(TransactionTestCase):
    migrate_from = [
        ("restore", "0006_restore_item_terminal_projection"),
        ("task", "0013_end_deferred_source_unregister_tasks"),
    ]
    migrate_to = [
        ("restore", "0006_restore_item_terminal_projection"),
        ("task", "0014_insight_workspace_restore_task_type"),
    ]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self._seed_restore_tasks(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _create_record(self, RestoreRecord, *, task, purpose: str, suffix: str):
        return RestoreRecord.objects.create(
            organization_id=task.organization_id,
            requesting_organization_id=task.organization_id,
            target_execution_organization_id=task.organization_id,
            target_execution_node_id=100,
            purpose=purpose,
            idempotency_key=f"migration-{suffix}" if purpose == "lens_workspace" else "",
            workspace_binding_id=200 if purpose == "lens_workspace" else None,
            restore_uid=f"migration-{suffix}",
            source_mode="manual",
            task_id=task.id,
            task_uuid=task.task_uuid,
            source_type="agent",
            source_ref_id=300,
            source_snapshot_id=400,
            target_type="agent",
            target_ref_id=100,
            target_path=f"/tmp/{suffix}",
            scope="paths",
            conflict_mode="overwrite",
        )

    def _seed_restore_tasks(self, apps):
        Task = apps.get_model("task", "Task")
        RestoreRecord = apps.get_model("restore", "RestoreRecord")

        terminal_lens = Task.objects.create(
            organization_id=10,
            task_type="restore",
            display_name="Failed insight workspace restore",
            status="failed",
        )
        self._create_record(
            RestoreRecord,
            task=terminal_lens,
            purpose="lens_workspace",
            suffix="terminal-lens",
        )
        self.terminal_lens_task_id = terminal_lens.id

        active_lens = Task.objects.create(
            organization_id=10,
            task_type="restore",
            display_name="Running insight workspace restore",
            status="running",
        )
        self._create_record(
            RestoreRecord,
            task=active_lens,
            purpose="lens_workspace",
            suffix="active-lens",
        )
        self.active_lens_task_id = active_lens.id

        user_restore = Task.objects.create(
            organization_id=10,
            task_type="restore",
            display_name="Failed user restore",
            status="failed",
        )
        self._create_record(
            RestoreRecord,
            task=user_restore,
            purpose="user_data",
            suffix="user-data",
        )
        self.user_restore_task_id = user_restore.id

    def test_terminal_lens_tasks_are_classified_without_mutating_active_or_user_restore(self):
        Task = self.apps.get_model("task", "Task")

        self.assertEqual(
            Task.objects.get(id=self.terminal_lens_task_id).task_type,
            "insight_workspace_restore",
        )
        self.assertEqual(
            Task.objects.get(id=self.active_lens_task_id).task_type,
            "restore",
        )
        self.assertEqual(
            Task.objects.get(id=self.user_restore_task_id).task_type,
            "restore",
        )

    def test_reverse_restores_the_legacy_task_type(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        Task = old_apps.get_model("task", "Task")

        self.assertEqual(
            Task.objects.get(id=self.terminal_lens_task_id).task_type,
            "restore",
        )
        self.assertEqual(
            Task.objects.get(id=self.active_lens_task_id).task_type,
            "restore",
        )
        self.assertEqual(
            Task.objects.get(id=self.user_restore_task_id).task_type,
            "restore",
        )
