from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SourceUnregisterDeferredStateMigrationTests(TransactionTestCase):
    migrate_from = [
        ("source", "0013_source_resource_probing_status"),
        ("task", "0012_task_blocked_idempotency_and_dependency_checks"),
    ]
    migrate_to = [
        ("source", "0013_source_resource_probing_status"),
        ("task", "0013_end_deferred_source_unregister_tasks"),
    ]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self._seed_deferred_tasks(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _seed_deferred_tasks(self, apps):
        Organization = apps.get_model("iam", "Organization")
        SourceResource = apps.get_model("source", "SourceResource")
        Task = apps.get_model("task", "Task")
        TaskDependency = apps.get_model("task", "TaskDependency")
        TaskResource = apps.get_model("task", "TaskResource")
        TaskStep = apps.get_model("task", "TaskStep")

        organization = Organization.objects.create(
            key="source-unregister-migration",
            name="Source unregister migration",
        )
        self.organization_id = organization.id
        self.deferred_nas_id = SourceResource.objects.create(
            organization=organization,
            name="Deferred NAS",
            resource_type="nas",
            status="removing",
        ).id
        self.resumable_nas_id = SourceResource.objects.create(
            organization=organization,
            name="Running NAS",
            resource_type="nas",
            status="removing",
        ).id
        self.shared_active_nas_id = SourceResource.objects.create(
            organization=organization,
            name="Shared active NAS",
            resource_type="nas",
            status="removing",
        ).id
        self.deferred_task_ids = []
        for status in ("waiting", "blocked"):
            task = Task.objects.create(
                organization_id=self.organization_id,
                task_type="source_unregister",
                display_name=f"Legacy {status} deregistration",
                status=status,
                request_payload={"source_ids": ["agent:7"], "force": False},
                result_payload={
                    f"{status}_reasons": [
                        {"code": "running_tasks", "detail": "Backup is active."}
                    ]
                },
            )
            TaskStep.objects.create(
                task=task,
                step_index=1,
                step_name="prepare_source_unregister",
                status="pending",
            )
            TaskDependency.objects.create(
                task=task,
                code="running_tasks",
                detail="Backup is active.",
            )
            TaskResource.objects.create(
                task=task,
                resource_type="backup_source",
                resource_subtype="nas",
                resource_id=self.deferred_nas_id,
                is_primary=True,
            )
            self.deferred_task_ids.append(task.id)

        resumable = Task.objects.create(
            organization_id=self.organization_id,
            task_type="source_unregister",
            display_name="Running deregistration",
            status="running",
            current_step="cleanup_source_endpoint",
            request_payload={"source_ids": ["agent:8"], "force": False},
            result_payload={
                "result": "waiting",
                "accepted": True,
                "waiting_reasons": [
                    {"code": "agent_uninstall", "detail": "Uninstall is in progress."}
                ],
            },
        )
        TaskStep.objects.create(
            task=resumable,
            step_index=1,
            step_name="cleanup_source_endpoint",
            status="running",
        )
        TaskResource.objects.create(
            task=resumable,
            resource_type="backup_source",
            resource_subtype="nas",
            resource_id=self.resumable_nas_id,
            is_primary=True,
        )
        self.resumable_task_id = resumable.id

        not_started = Task.objects.create(
            organization_id=self.organization_id,
            task_type="source_unregister",
            display_name="Just resumed legacy deregistration",
            status="running",
            current_step="prepare_source_unregister",
            request_payload={"source_ids": ["agent:9"], "force": False},
            result_payload={
                "waiting_reasons": [
                    {"code": "running_tasks", "detail": "Backup is active."}
                ]
            },
        )
        TaskStep.objects.create(
            task=not_started,
            step_index=1,
            step_name="prepare_source_unregister",
            status="success",
        )
        TaskResource.objects.create(
            task=not_started,
            resource_type="backup_source",
            resource_subtype="nas",
            resource_id=self.deferred_nas_id,
            is_primary=True,
        )
        self.not_started_task_id = not_started.id

        shared_deferred = Task.objects.create(
            organization_id=self.organization_id,
            task_type="source_unregister",
            display_name="Deferred shared NAS deregistration",
            status="waiting",
            request_payload={"source_ids": [f"nas:{self.shared_active_nas_id}"]},
            result_payload={
                "waiting_reasons": [
                    {"code": "running_tasks", "detail": "Backup is active."}
                ]
            },
        )
        TaskStep.objects.create(
            task=shared_deferred,
            step_index=1,
            step_name="prepare_source_unregister",
            status="pending",
        )
        TaskResource.objects.create(
            task=shared_deferred,
            resource_type="backup_source",
            resource_subtype="nas",
            resource_id=self.shared_active_nas_id,
            is_primary=True,
        )
        self.deferred_task_ids.append(shared_deferred.id)

        shared_active = Task.objects.create(
            organization_id=self.organization_id,
            task_type="source_unregister",
            display_name="Active shared NAS deregistration",
            status="running",
            current_step="cleanup_source_endpoint",
            request_payload={"source_ids": [f"nas:{self.shared_active_nas_id}"]},
            result_payload={"result": "waiting"},
        )
        TaskStep.objects.create(
            task=shared_active,
            step_index=1,
            step_name="cleanup_source_endpoint",
            status="running",
        )
        TaskResource.objects.create(
            task=shared_active,
            resource_type="backup_source",
            resource_subtype="nas",
            resource_id=self.shared_active_nas_id,
            is_primary=True,
        )

    def test_upgrade_ends_only_not_started_deferred_intents(self):
        SourceResource = self.apps.get_model("source", "SourceResource")
        Task = self.apps.get_model("task", "Task")
        TaskDependency = self.apps.get_model("task", "TaskDependency")
        TaskStep = self.apps.get_model("task", "TaskStep")

        ended_task_ids = [*self.deferred_task_ids, self.not_started_task_id]
        for task in Task.objects.filter(id__in=ended_task_ids):
            self.assertEqual(task.status, "failed")
            self.assertEqual(
                task.error_code,
                "SOURCE_UNREGISTER_DEFERRED_CANCELLED",
            )
            self.assertEqual(task.result_payload["result"], "failed")
            self.assertFalse(task.result_payload["accepted"])
            self.assertEqual(task.result_payload["reasons"][0]["code"], "running_tasks")
            self.assertFalse(
                TaskDependency.objects.filter(task_id=task.id, is_active=True).exists()
            )
            self.assertEqual(
                TaskStep.objects.get(
                    task_id=task.id,
                    step_name="prepare_source_unregister",
                ).status,
                "failed",
            )

        resumable = Task.objects.get(pk=self.resumable_task_id)
        self.assertEqual(resumable.status, "running")
        self.assertEqual(resumable.result_payload["result"], "waiting")

        deferred_nas = SourceResource.objects.get(pk=self.deferred_nas_id)
        self.assertEqual(deferred_nas.status, "active")
        self.assertIn("ended during upgrade", deferred_nas.status_message)

        resumable_nas = SourceResource.objects.get(pk=self.resumable_nas_id)
        self.assertEqual(resumable_nas.status, "removing")

        shared_active_nas = SourceResource.objects.get(pk=self.shared_active_nas_id)
        self.assertEqual(shared_active_nas.status, "removing")
