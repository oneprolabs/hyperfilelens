from __future__ import annotations

import queue
import threading
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node.models import Node
from apps.protection.models import (
    BackupConfig,
    BackupConfigDirectory,
    BackupSourceSnapshot,
)
from apps.protection.services.backup_task import start_backup_tasks
from apps.protection.services.backup_config_reset import create_reset_tasks_for_sources
from apps.source.models import SourceBackupPipelineEntry
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_location import (
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    reserve_repository_location,
)
from apps.task.models import Task, TaskResource
from common.errors import AppError


class BackupTaskConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if not connection.features.has_select_for_update:
            self.skipTest("Database backend does not support row-level locking.")
        self.org = Organization.objects.create(
            key="backup-concurrency-org",
            name="Backup Concurrency Org",
        )
        self.agent = Node.objects.create(
            organization=self.org,
            name="backup-concurrency-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.71",
        )
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="backup-concurrency-repo",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="backup-concurrency-bucket",
            config={
                "endpoint": "s3.example.internal:9000",
                "region": "cn-test-1",
                "prefix": "kopia/concurrency",
                "access_key_id": "ak-test",
                "secret_access_key": "sk-test",
                "kopia_password": "repo-password",
                "use_tls": False,
            },
        )
        reserve_repository_location(self.repository)
        mark_repository_location_owned(self.repository)
        mark_repository_location_ownership_verified(self.repository)
        self.config = BackupConfig.objects.create(
            organization_id=self.org.id,
            name="Concurrent backup config",
            source_type="agent",
            source_ref_id=self.agent.id,
            repository_id=self.repository.id,
            compression_level=BackupConfig.CompressionLevel.BALANCED,
        )
        BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data/concurrent",
            sort_order=0,
        )
        SourceBackupPipelineEntry.objects.create(
            organization=self.org,
            source_kind="agent",
            ref_id=self.agent.id,
            step=3,
        )

    def test_simultaneous_starts_create_one_active_backup(self):
        barrier = threading.Barrier(2)
        outcomes: queue.Queue[dict] = queue.Queue()
        errors: queue.Queue[BaseException] = queue.Queue()

        def start(idempotency_key: str) -> None:
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                outcomes.put(
                    start_backup_tasks(
                        organization_id=self.org.id,
                        source_ids=[f"agent:{self.agent.id}"],
                        trigger_type="manual",
                        idempotency_key=idempotency_key,
                    )
                )
            except (
                BaseException
            ) as exc:  # pragma: no cover - surfaced in the main test thread
                errors.put(exc)
            finally:
                close_old_connections()

        with patch("apps.protection.services.backup_task._queue_backup_execution"):
            threads = [
                threading.Thread(target=start, args=("concurrent-a",)),
                threading.Thread(target=start, args=("concurrent-b",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        if not errors.empty():
            raise errors.get()
        self.assertEqual(outcomes.qsize(), 2)
        statuses = sorted(outcomes.get()["results"][0]["status"] for _ in range(2))
        self.assertEqual(statuses, ["conflict", "created"])
        self.assertEqual(Task.objects.filter(task_type=Task.Type.BACKUP).count(), 1)
        self.assertEqual(
            BackupSourceSnapshot.objects.filter(
                status=BackupSourceSnapshot.Status.CREATING
            ).count(),
            1,
        )

    def test_simultaneous_starts_both_observe_full_repository_quota(self):
        self.repository.config = {**self.repository.config, "quota_gb": 1}
        self.repository.estimated_usage_bytes = 1024**3
        self.repository.usage_probe_status = Repository.MetricProbeStatus.SUCCESS
        self.repository.usage_last_success_at = timezone.now()
        self.repository.save(
            update_fields=[
                "config",
                "estimated_usage_bytes",
                "usage_probe_status",
                "usage_last_success_at",
                "updated_at",
            ]
        )
        barrier = threading.Barrier(2)
        outcomes: queue.Queue[str] = queue.Queue()
        errors: queue.Queue[BaseException] = queue.Queue()

        def start(idempotency_key: str) -> None:
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                start_backup_tasks(
                    organization_id=self.org.id,
                    source_ids=[f"agent:{self.agent.id}"],
                    trigger_type="manual",
                    idempotency_key=idempotency_key,
                )
            except AppError as exc:
                outcomes.put(exc.code)
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.put(exc)
            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=start, args=("quota-full-a",)),
            threading.Thread(target=start, args=("quota-full-b",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        if not errors.empty():
            raise errors.get()
        self.assertEqual(outcomes.qsize(), 2)
        self.assertEqual(
            [outcomes.get(), outcomes.get()],
            [
                "BACKUP.REPOSITORY_QUOTA_EXCEEDED",
                "BACKUP.REPOSITORY_QUOTA_EXCEEDED",
            ],
        )
        self.assertFalse(Task.objects.filter(task_type=Task.Type.BACKUP).exists())

    def test_backup_start_is_blocked_by_active_source_unregister(self):
        unregister_task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.SOURCE_UNREGISTER,
            display_name="Unregister backup source",
            status=Task.Status.RUNNING,
        )
        TaskResource.objects.create(
            task=unregister_task,
            resource_type=TaskResource.Type.BACKUP_SOURCE,
            resource_subtype="agent",
            resource_id=self.agent.id,
            is_primary=True,
        )

        result = start_backup_tasks(
            organization_id=self.org.id,
            source_ids=[f"agent:{self.agent.id}"],
            trigger_type="manual",
            idempotency_key="blocked-by-unregister",
        )

        self.assertEqual(result["results"][0]["status"], "failed")
        self.assertIn(
            "active Reset or Deregistration operation",
            result["results"][0]["message"],
        )
        self.assertFalse(Task.objects.filter(task_type=Task.Type.BACKUP).exists())

    def test_simultaneous_backup_and_reset_create_one_operation(self):
        barrier = threading.Barrier(2)
        outcomes: queue.Queue[str] = queue.Queue()
        errors: queue.Queue[BaseException] = queue.Queue()

        def start_backup() -> None:
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                result = start_backup_tasks(
                    organization_id=self.org.id,
                    source_ids=[f"agent:{self.agent.id}"],
                    trigger_type="manual",
                    idempotency_key="backup-reset-race",
                )
                status_value = result["results"][0]["status"]
                outcomes.put("created" if status_value == "created" else "conflict")
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.put(exc)
            finally:
                close_old_connections()

        def reset_config() -> None:
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                result = create_reset_tasks_for_sources(
                    organization_id=self.org.id,
                    source_ids=[f"agent:{self.agent.id}"],
                    confirmation="RESET",
                )
                outcomes.put("created" if result["created_count"] == 1 else "conflict")
            except AppError as exc:
                if exc.code != "BACKUP.ALREADY_RUNNING":
                    errors.put(exc)
                else:
                    outcomes.put("conflict")
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.put(exc)
            finally:
                close_old_connections()

        with (
            patch("apps.protection.services.backup_task._queue_backup_execution"),
            patch(
                "apps.protection.services.backup_config_reset.queue_backup_config_reset_task"
            ),
        ):
            threads = [
                threading.Thread(target=start_backup),
                threading.Thread(target=reset_config),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        if not errors.empty():
            raise errors.get()
        self.assertEqual(outcomes.qsize(), 2)
        self.assertEqual(sorted(outcomes.get() for _ in range(2)), ["conflict", "created"])
        self.assertEqual(
            Task.objects.filter(
                task_type__in=(Task.Type.BACKUP, Task.Type.BACKUP_CONFIG_RESET)
            ).count(),
            1,
        )

    def test_backup_start_revalidates_repository_before_task_creation(self):
        self.repository.status = Repository.Status.REMOVING
        self.repository.save(update_fields=["status", "updated_at"])

        with patch(
            "apps.protection.services.backup_task.validate_backup_repository_compatible",
            return_value=self.repository,
        ):
            result = start_backup_tasks(
                organization_id=self.org.id,
                source_ids=[f"agent:{self.agent.id}"],
                trigger_type="manual",
                idempotency_key="repository-became-unavailable",
            )

        self.assertEqual(result["results"][0]["status"], "failed")
        self.assertIn(
            "no longer available",
            result["results"][0]["message"],
        )
        self.assertFalse(Task.objects.filter(task_type=Task.Type.BACKUP).exists())
