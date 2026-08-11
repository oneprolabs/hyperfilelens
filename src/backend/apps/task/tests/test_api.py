from decimal import Decimal
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.task.api.serializers.task import TaskStepInputSerializer
from apps.task.models import Task, TaskDependency, TaskEvent, TaskResource, TaskStep
from apps.task.services.interface import complete_task, create_task


class TaskStepInputSerializerTests(SimpleTestCase):
    def test_progress_uses_decimal_bounds_and_default(self):
        default_serializer = TaskStepInputSerializer(data={"step_name": "snapshot"})
        self.assertTrue(default_serializer.is_valid(), default_serializer.errors)
        self.assertEqual(default_serializer.validated_data["progress"], Decimal("0.00"))

        for value, expected in (("0", Decimal("0.00")), ("100", Decimal("100.00"))):
            with self.subTest(value=value):
                serializer = TaskStepInputSerializer(
                    data={"step_name": "snapshot", "progress": value}
                )
                self.assertTrue(serializer.is_valid(), serializer.errors)
                self.assertEqual(serializer.validated_data["progress"], expected)

    def test_progress_rejects_values_outside_decimal_bounds(self):
        for value in ("-0.01", "100.01"):
            with self.subTest(value=value):
                serializer = TaskStepInputSerializer(
                    data={"step_name": "snapshot", "progress": value}
                )
                self.assertFalse(serializer.is_valid())
                self.assertIn("progress", serializer.errors)


class TaskApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="task-api@test.local",
            email="task-api@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(key="task-test-org", name="Task Test Org")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.client.force_authenticate(user=self.user)

    def _headers(self, org: Organization | None = None):
        return {"HTTP_X_ORG_KEY": (org or self.org).key}

    def test_statistics_honors_task_type_and_created_range(self):
        now = timezone.now()
        recent_restore = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.RESTORE,
            display_name="Recent restore",
            status=Task.Status.SUCCESS,
        )
        recent_failure = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.RESTORE,
            display_name="Recent failed restore",
            status=Task.Status.FAILED,
        )
        old_restore = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.RESTORE,
            display_name="Old restore",
            status=Task.Status.SUCCESS,
        )
        Task.objects.filter(pk=recent_restore.pk).update(created_at=now - timedelta(hours=1))
        Task.objects.filter(pk=recent_failure.pk).update(created_at=now - timedelta(hours=2))
        Task.objects.filter(pk=old_restore.pk).update(created_at=now - timedelta(days=2))
        Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Recent backup",
            status=Task.Status.SUCCESS,
        )

        response = self.client.get(
            "/api/v1/tasks/statistics/",
            {
                "task_type": Task.Type.RESTORE,
                "created_after": (now - timedelta(hours=24)).isoformat(),
            },
            follow=True,
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["total"], 2)
        self.assertEqual(response.data["success"], 1)
        self.assertEqual(response.data["failed"], 1)
        self.assertEqual(response.data["by_task_type"], {Task.Type.RESTORE: 2})

    def test_list_and_statistics_honor_finished_range_and_terminal_only(self):
        now = timezone.now()
        completed_success = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Completed successfully",
            status=Task.Status.SUCCESS,
            finished_at=now - timedelta(hours=2),
        )
        completed_timeout = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Completed with timeout",
            status=Task.Status.TIMEOUT,
            finished_at=now - timedelta(hours=3),
        )
        Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Still running",
            status=Task.Status.RUNNING,
        )
        Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Completed yesterday",
            status=Task.Status.SUCCESS,
            finished_at=now - timedelta(days=2),
        )

        params = {
            "finished_after": (now - timedelta(days=1)).isoformat(),
            "finished_before": now.isoformat(),
            "terminal_only": "true",
        }
        listing = self.client.get("/api/v1/tasks/", params, follow=True, **self._headers())
        self.assertEqual(listing.status_code, status.HTTP_200_OK, listing.content)
        self.assertEqual(listing.data["data"]["pagination"]["total"], 2)
        self.assertEqual(
            {row["task_uuid"] for row in listing.data["data"]["list"]},
            {str(completed_success.task_uuid), str(completed_timeout.task_uuid)},
        )

        statistics = self.client.get(
            "/api/v1/tasks/statistics/",
            params,
            follow=True,
            **self._headers(),
        )
        self.assertEqual(statistics.status_code, status.HTTP_200_OK, statistics.content)
        self.assertEqual(statistics.data["total"], 2)
        self.assertEqual(statistics.data["success"], 1)
        self.assertEqual(statistics.data["timeout"], 1)
        self.assertEqual(statistics.data["running"], 0)

    def test_statistics_reports_all_task_statuses(self):
        for task_status in (
            Task.Status.PENDING,
            Task.Status.RUNNING,
            Task.Status.SUCCESS,
            Task.Status.FAILED,
            Task.Status.CANCELLED,
            Task.Status.TIMEOUT,
        ):
            Task.objects.create(
                organization_id=self.org.id,
                task_type=Task.Type.BACKUP,
                display_name=f"{task_status} task",
                status=task_status,
            )

        response = self.client.get(
            "/api/v1/tasks/statistics/",
            follow=True,
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["total"], 6)
        self.assertEqual(response.data["running"], 1)
        self.assertEqual(response.data["success"], 1)
        self.assertEqual(response.data["failed"], 1)
        self.assertEqual(response.data["cancelled"], 1)
        self.assertEqual(response.data["timeout"], 1)
        self.assertEqual(response.data["by_status"][Task.Status.PENDING], 1)

    def test_create_task_persists_after_listing_refresh(self):
        response = self.client.post(
            "/api/v1/tasks/",
            {
                "task_type": Task.Type.BACKUP,
                "display_name": "Daily backup",
                "trigger_type": Task.TriggerType.MANUAL,
                "request_payload": {"source": "backup-source-1"},
                "resources": [
                    {
                        "resource_type": TaskResource.Type.BACKUP_SOURCE,
                        "resource_subtype": "agent",
                        "resource_id": 1,
                        "is_primary": True,
                    },
                    {
                        "resource_type": TaskResource.Type.REPOSITORY,
                        "resource_id": 2,
                    },
                ],
                "steps": [
                    {"step_name": "snapshot"},
                    {"step_name": "upload"},
                    {"step_name": "finalize"},
                ],
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        task_uuid = response.data["task_uuid"]

        listing = self.client.get("/api/v1/tasks/", **self._headers())
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        rows = listing.data["data"]["list"]
        self.assertIn(task_uuid, [row["task_uuid"] for row in rows])

        detail = self.client.get(f"/api/v1/tasks/{task_uuid}/", **self._headers())
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["display_name"], "Daily backup")
        self.assertEqual(len(detail.data["resources"]), 1)
        source_resource = next(
            row for row in detail.data["resources"] if row["resource_type"] == TaskResource.Type.BACKUP_SOURCE
        )
        self.assertEqual(source_resource["resource_subtype"], "agent")
        self.assertTrue(source_resource["is_primary"])
        self.assertEqual(detail.data["primary_resource"]["resource_id"], 1)
        self.assertEqual(len(detail.data["steps"]), 3)
        task = Task.objects.get(task_uuid=task_uuid)
        first_step = task.steps.get(step_name="snapshot")
        self.assertEqual(
            TaskEvent.objects.get(task=task, message="Task created").step_id,
            first_step.id,
        )

        filtered = self.client.get(
            "/api/v1/tasks/?resource_type=backup_source&resource_subtype=agent&resource_id=1",
            **self._headers(),
        )
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertIn(task_uuid, [row["task_uuid"] for row in filtered.data["data"]["list"]])

    def test_create_task_rejects_multiple_primary_resources(self):
        response = self.client.post(
            "/api/v1/tasks/",
            {
                "task_type": Task.Type.BACKUP,
                "display_name": "Invalid task",
                "resources": [
                    {"resource_type": TaskResource.Type.BACKUP_SOURCE, "resource_id": 1, "is_primary": True},
                    {"resource_type": TaskResource.Type.REPOSITORY, "resource_id": 2, "is_primary": True},
                ],
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["data"]["errors"][0]["field"], "resources")

    def test_create_task_rejects_system_managed_node_lifecycle_type(self):
        response = self.client.post(
            "/api/v1/tasks/",
            {
                "task_type": Task.Type.NODE_LIFECYCLE,
                "display_name": "Forged node removal",
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_new_source_scoped_tasks_only_persist_backup_source_resources(self):
        source_scoped_types = (
            Task.Type.BACKUP,
            Task.Type.RESTORE,
            Task.Type.SNAPSHOT_DOWNLOAD,
            Task.Type.SNAPSHOT_DELETE,
            Task.Type.BACKUP_CONFIG_RESET,
            Task.Type.SOURCE_UNREGISTER,
        )

        for task_type in source_scoped_types:
            with self.subTest(task_type=task_type):
                task = create_task(
                    organization_id=self.org.id,
                    task_type=task_type,
                    display_name=f"{task_type} task",
                    resources=[
                        {
                            "resource_type": TaskResource.Type.BACKUP_SOURCE,
                            "resource_subtype": "agent",
                            "resource_id": 1,
                            "is_primary": True,
                        },
                        {
                            "resource_type": TaskResource.Type.REPOSITORY,
                            "resource_id": 2,
                        },
                        {
                            "resource_type": TaskResource.Type.SNAPSHOT,
                            "resource_id": 3,
                        },
                    ],
                )

                self.assertEqual(
                    list(task.resources.values_list("resource_type", flat=True)),
                    [TaskResource.Type.BACKUP_SOURCE],
                )

    def test_new_task_creation_does_not_rewrite_existing_resource_associations(self):
        existing_task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Existing backup",
        )
        TaskResource.objects.create(
            task=existing_task,
            resource_type=TaskResource.Type.REPOSITORY,
            resource_id=2,
        )

        create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="New backup",
            resources=[
                {
                    "resource_type": TaskResource.Type.BACKUP_SOURCE,
                    "resource_subtype": "agent",
                    "resource_id": 1,
                }
            ],
        )

        self.assertTrue(
            TaskResource.objects.filter(
                task=existing_task,
                resource_type=TaskResource.Type.REPOSITORY,
                resource_id=2,
            ).exists()
        )

    def test_repository_operation_keeps_repository_resource_association(self):
        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.REPOSITORY_OPERATION,
            display_name="Repository maintenance",
            resources=[
                {
                    "resource_type": TaskResource.Type.REPOSITORY,
                    "resource_id": 2,
                    "is_primary": True,
                }
            ],
        )

        self.assertEqual(
            list(task.resources.values_list("resource_type", flat=True)),
            [TaskResource.Type.REPOSITORY],
        )

    def test_task_list_is_tenant_scoped(self):
        other_user = get_user_model().objects.create_user(
            username="task-other@test.local",
            password="test-pass",
        )
        other_org = Organization.objects.create(key="task-other-org", name="Task Other Org")
        Membership.objects.create(
            user=other_user,
            organization=other_org,
            role=Membership.Role.ADMIN,
        )
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Org task",
            status=Task.Status.PENDING,
            trigger_type=Task.TriggerType.MANUAL,
        )

        self.client.force_authenticate(user=other_user)
        response = self.client.get("/api/v1/tasks/", **self._headers(other_org))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["data"]["list"]
        self.assertNotIn(str(task.task_uuid), [row["task_uuid"] for row in rows])

    def test_task_list_search_field_filters_name_and_uuid(self):
        named_task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Nightly database backup",
            status=Task.Status.PENDING,
            trigger_type=Task.TriggerType.MANUAL,
        )
        other_task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name=str(named_task.task_uuid),
            status=Task.Status.PENDING,
            trigger_type=Task.TriggerType.MANUAL,
        )

        name_response = self.client.get(
            "/api/v1/tasks/?search=database&search_field=name",
            **self._headers(),
        )
        self.assertEqual(name_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [row["task_uuid"] for row in name_response.data["data"]["list"]],
            [str(named_task.task_uuid)],
        )

        uuid_response = self.client.get(
            f"/api/v1/tasks/?search={named_task.task_uuid}&search_field=uuid",
            **self._headers(),
        )
        self.assertEqual(uuid_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [row["task_uuid"] for row in uuid_response.data["data"]["list"]],
            [str(named_task.task_uuid)],
        )
        self.assertNotIn(str(other_task.task_uuid), [row["task_uuid"] for row in uuid_response.data["data"]["list"]])

    def test_cancel_and_retry_task(self):
        created = self.client.post(
            "/api/v1/tasks/",
            {
                "task_type": Task.Type.RESTORE,
                "display_name": "Restore files",
                "trigger_type": Task.TriggerType.API,
            },
            format="json",
            **self._headers(),
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.content)
        self.assertEqual(created.data["trigger_type"], Task.TriggerType.MANUAL)
        task_uuid = created.data["task_uuid"]

        cancelled = self.client.post(
            f"/api/v1/tasks/{task_uuid}/cancel/",
            {"reason": "no longer needed"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(cancelled.status_code, status.HTTP_200_OK)
        self.assertEqual(cancelled.data["status"], Task.Status.CANCELLED)

        retried = self.client.post(
            f"/api/v1/tasks/{task_uuid}/retry/",
            {"reason": "run again"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(retried.status_code, status.HTTP_200_OK)
        self.assertEqual(retried.data["status"], Task.Status.PENDING)
        self.assertEqual(retried.data["retry_count"], 1)
        task = Task.objects.get(task_uuid=task_uuid)
        first_step = task.steps.order_by("step_index").first()
        self.assertIsNotNone(first_step)
        self.assertEqual(
            TaskEvent.objects.get(task=task, message="Task cancelled").step_id,
            first_step.id,
        )
        self.assertEqual(
            TaskEvent.objects.get(task=task, message="Task queued for retry").step_id,
            first_step.id,
        )

    def test_source_unregister_task_cannot_be_cancelled_generically(self):
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.SOURCE_UNREGISTER,
            display_name="Unregister backup source",
            status=Task.Status.RUNNING,
            trigger_type=Task.TriggerType.MANUAL,
        )

        response = self.client.post(
            f"/api/v1/tasks/{task.task_uuid}/cancel/",
            {"reason": "cancel cleanup"},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cannot be cancelled", str(response.data))
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.RUNNING)

    def test_waiting_source_unregister_task_can_be_cancelled(self):
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.SOURCE_UNREGISTER,
            display_name="Unregister backup source",
            status=Task.Status.WAITING,
            trigger_type=Task.TriggerType.MANUAL,
        )
        dependency = TaskDependency.objects.create(
            task=task,
            code="running_tasks",
            detail="A backup task is still active.",
        )

        response = self.client.post(
            f"/api/v1/tasks/{task.task_uuid}/cancel/",
            {"reason": "keep the source"},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        task.refresh_from_db()
        dependency.refresh_from_db()
        self.assertEqual(task.status, Task.Status.CANCELLED)
        self.assertFalse(dependency.is_active)
        self.assertIsNotNone(dependency.resolved_at)

    def test_blocked_source_unregister_exposes_recheck_and_cancel_actions(self):
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.SOURCE_UNREGISTER,
            display_name="Blocked source deregistration",
            status=Task.Status.BLOCKED,
            trigger_type=Task.TriggerType.MANUAL,
        )
        dependency = TaskDependency.objects.create(
            task=task,
            code="agent_offline",
            detail="Agent is offline.",
            auto_resumable=False,
        )

        detail = self.client.get(
            f"/api/v1/tasks/{task.task_uuid}/",
            **self._headers(),
        )
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertTrue(detail.data["actions"]["can_cancel"])
        self.assertTrue(detail.data["actions"]["can_recheck"])

        cancelled = self.client.post(
            f"/api/v1/tasks/{task.task_uuid}/cancel/",
            {"reason": "keep source"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(cancelled.status_code, status.HTTP_200_OK)
        dependency.refresh_from_db()
        self.assertFalse(dependency.is_active)

    def test_node_lifecycle_task_cannot_be_cancelled_or_retried_generically(self):
        running = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.NODE_LIFECYCLE,
            display_name="Delete proxy host",
            status=Task.Status.RUNNING,
            trigger_type=Task.TriggerType.MANUAL,
        )

        cancelled = self.client.post(
            f"/api/v1/tasks/{running.task_uuid}/cancel/",
            {"reason": "cancel projected task"},
            format="json",
            **self._headers(),
        )

        self.assertEqual(cancelled.status_code, status.HTTP_400_BAD_REQUEST)
        running.refresh_from_db()
        self.assertEqual(running.status, Task.Status.RUNNING)

        failed = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.NODE_LIFECYCLE,
            display_name="Failed proxy removal",
            status=Task.Status.FAILED,
            trigger_type=Task.TriggerType.MANUAL,
        )
        retried = self.client.post(
            f"/api/v1/tasks/{failed.task_uuid}/retry/",
            {"reason": "retry projected task"},
            format="json",
            **self._headers(),
        )

        self.assertEqual(retried.status_code, status.HTTP_400_BAD_REQUEST)
        failed.refresh_from_db()
        self.assertEqual(failed.status, Task.Status.FAILED)

    def test_failed_complete_task_stores_explicit_progress(self):
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Failed backup",
            status=Task.Status.RUNNING,
            progress=87,
            trigger_type=Task.TriggerType.MANUAL,
        )
        step = TaskStep.objects.create(task=task, step_index=1, step_name="snapshot", status=TaskStep.Status.RUNNING)
        task.current_step = "snapshot"
        task.save(update_fields=["current_step", "updated_at"])

        updated = complete_task(
            task_uuid=task.task_uuid,
            organization_id=self.org.id,
            status=Task.Status.FAILED,
            progress=45,
            error_code="FAILED",
            error_message="failed",
        )

        self.assertEqual(updated.status, Task.Status.FAILED)
        self.assertEqual(float(updated.progress), 45)
        self.assertEqual(
            TaskEvent.objects.get(task=task, message="Task finished with status failed").step_id,
            step.id,
        )
