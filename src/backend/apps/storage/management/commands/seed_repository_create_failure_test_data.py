"""Seed a failed repository-create task for browser verification."""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from uuid import uuid4

from apps.iam.models import Membership
from apps.storage.repositories.models import Repository, RepositoryTask
from apps.task.models.task import Task
from apps.task.models.task_step import TaskStep
from apps.task.services.interface import create_task


class Command(BaseCommand):
    help = "Create an idempotent failed repository creation task for manual UI testing"

    @transaction.atomic
    def handle(self, *args, **options):
        membership = Membership.objects.filter(is_active=True).first()
        if not membership:
            self.stderr.write(self.style.ERROR("No active organization membership found."))
            return
        org_id = membership.organization_id
        suffix = uuid4().hex[:8]
        name = f"[TEST] Failed repository creation {suffix}"
        repository = Repository.objects.create(
            organization_id=org_id,
            name=name,
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATE_FAILED,
            health=Repository.Health.OFFLINE,
            config={"endpoint": "https://invalid.test", "bucket": f"test-failure-{suffix}"},
        )
        task = create_task(
            organization_id=org_id,
            task_type=Task.Type.REPOSITORY_OPERATION,
            display_name=f"Create repository: {name}",
            trigger_type=Task.TriggerType.MANUAL,
            request_payload={"repository_id": repository.id, "repo_type": "s3"},
            resources=[{"resource_type": "repository", "resource_id": repository.id, "is_primary": True}],
            steps=[
                {"step_name": "validate_configuration", "status": "success", "progress": 100},
                {"step_name": "initialize_repository", "status": "failed", "progress": 100},
            ],
        )
        RepositoryTask.objects.create(
            task=task,
            repository=repository,
            operation_type=RepositoryTask.OperationType.CREATE_REPOSITORY,
            owner_type="controller",
            owner_identity="controller",
        )
        Task.objects.filter(pk=task.pk).update(
            status=Task.Status.FAILED,
            progress=100,
            current_step="initialize_repository",
            error_code="REPOSITORY_CREATE_AUTH_FAILED",
            error_message="The storage service rejected the configured credentials.",
            result_payload={
                "summary": "Repository creation failed",
                "reasons": ["The test credentials are intentionally invalid."],
                "resolutions": ["Open the repository form and update credentials, then retry."],
                "technical_detail": {"provider": "s3", "endpoint": "https://invalid.test"},
            },
            finished_at=timezone.now(),
        )
        TaskStep.objects.filter(task=task, step_name="validate_configuration").update(
            status=TaskStep.Status.SUCCESS, progress=100,
        )
        TaskStep.objects.filter(task=task, step_name="initialize_repository").update(
            status=TaskStep.Status.FAILED, progress=100,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Failed repository task ready: repository_id={repository.id}, task_uuid={task.task_uuid}"
        ))
