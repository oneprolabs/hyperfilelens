from django.test import TestCase

from apps.alert.constants import AlertType, ResourceType
from apps.alert.models import AlertPolicy, AlertRecord
from apps.alert.services.internal.task_events import handle_task_event
from apps.iam.models import Organization
from apps.storage.repositories.models import Repository, RepositoryExecutionTarget, RepositoryTask
from apps.storage.services.internal.repository_operations import (
    create_repository_operation_task,
    discover_repository_execution_targets,
)
from apps.storage.services.internal.repository_location import (
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    reserve_repository_location,
)
from apps.task.models import Task


class RepositoryTaskAlertTests(TestCase):
    def test_repository_task_alert_policy_filters_operation_type(self):
        org = Organization.objects.create(key="repository-alert-org", name="Repository Alert Org")
        repository = Repository.objects.create(
            organization_id=org.id,
            name="alert-repository",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_bucket="alert-bucket",
        )
        reserve_repository_location(repository)
        mark_repository_location_owned(repository)
        mark_repository_location_ownership_verified(repository)
        discover_repository_execution_targets()
        repository_task = create_repository_operation_task(
            target_id=RepositoryExecutionTarget.objects.get(repository=repository).id,
            operation_type=RepositoryTask.OperationType.MAINTENANCE_FULL,
        )
        repository_task.task.status = Task.Status.FAILED
        repository_task.task.error_message = "maintenance failed"
        repository_task.task.save(update_fields=["status", "error_message", "updated_at"])
        matching = AlertPolicy.objects.create(
            organization=org,
            name="Full maintenance failures",
            type=AlertType.TASK,
            severity="warning",
            enabled=True,
            resource_type=ResourceType.TASK,
            scope="all",
            trigger_rule={
                "task_type": Task.Type.REPOSITORY_OPERATION,
                "operation_type": RepositoryTask.OperationType.MAINTENANCE_FULL,
                "event_type": "task_failed",
            },
        )
        AlertPolicy.objects.create(
            organization=org,
            name="Quick maintenance failures",
            type=AlertType.TASK,
            severity="warning",
            enabled=True,
            resource_type=ResourceType.TASK,
            scope="all",
            trigger_rule={
                "task_type": Task.Type.REPOSITORY_OPERATION,
                "operation_type": RepositoryTask.OperationType.MAINTENANCE_QUICK,
                "event_type": "task_failed",
            },
        )

        handle_task_event(repository_task.task)

        alerts = AlertRecord.objects.filter(organization=org)
        self.assertEqual(alerts.count(), 1)
        self.assertEqual(alerts.get().policy_id, matching.id)
        self.assertEqual(alerts.get().metadata["operation_type"], "maintenance.full")
