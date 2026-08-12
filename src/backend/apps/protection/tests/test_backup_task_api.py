from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node.models import Node, NodeTask
from apps.protection import conf as protection_conf
from apps.protection.models import (
    BackupConfig,
    BackupConfigDirectory,
    BackupPolicy,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
)
from apps.protection.services.backup_source_snapshot import create_source_snapshot
from apps.node.services.internal.agent_task import AgentTaskHandle
from apps.protection.services.backup_orchestrator import (
    _reconcile_pending_backup_queue,
    _repository_public_host,
    cancel_backup,
    project_backup_node_task_result,
    queue_backup_result_projection,
    reconcile_backup_tasks,
)
from apps.protection.services.backup_task import (
    ExecutionTarget,
    _queue_backup_execution,
    _repository_runtime_payload,
    reconcile_interrupted_backup_tasks,
    run_backup_task,
)
from apps.protection.services.progress.orchestrated_progress import BACKUP_TRANSFER_END, BACKUP_TRANSFER_START
from apps.source.models import SourceBackupPipelineEntry
from apps.storage.repositories.models import Repository
from apps.task.models import Task, TaskEvent, TaskResource, TaskStep
from apps.task.services.interface import create_task


class ProtectionBackupTaskApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="backup-task-api@test.local",
            email="backup-task-api@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(key="backup-task-test-org", name="Backup Task Test Org")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.agent = Node.objects.create(
            organization=self.org,
            name="agent-backup-task-1",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.61",
        )
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="backup-task-s3-repo",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="backup-task-bucket",
            config={
                "endpoint": "s3.example.internal:9000",
                "region": "cn-test-1",
                "prefix": "kopia/team-a",
                "access_key_id": "ak-test",
                "secret_access_key": "sk-test",
                "kopia_password": "123456",
                "use_tls": False,
            },
        )
        self.config = BackupConfig.objects.create(
            organization_id=self.org.id,
            name="Agent backup config",
            source_type="agent",
            source_ref_id=self.agent.id,
            repository_id=self.repository.id,
            compression_level="balanced",
        )
        BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data/projects",
            display_name="projects",
            sort_order=0,
            estimated_size_bytes=2048,
        )
        SourceBackupPipelineEntry.objects.create(
            organization=self.org,
            source_kind="agent",
            ref_id=self.agent.id,
            step=3,
        )
        self.client.force_authenticate(user=self.user)

    def _headers(self):
        return {"HTTP_X_ORG_KEY": self.org.key}

    @patch("apps.protection.tasks.backup.advance_backup_task.delay")
    def test_policy_prepare_result_immediately_queues_backup_advance(self, mock_delay):
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-policy-result-followup",
        )
        node_task = NodeTask.objects.create(
            organization_id=self.org.id,
            node=self.agent,
            kind="repository.policy.apply",
            status=NodeTask.Status.SUCCESS,
            correlation_type=(
                protection_conf.PROTECTION_BACKUP_POLICY_PREPARE_CORRELATION_TYPE
            ),
            correlation_id=str(task.task_uuid),
            payload={"backup_config_dir_id": self.config.directories.first().id},
            result={},
            watchdog_deadline_at=timezone.now(),
        )

        self.assertTrue(queue_backup_result_projection(node_task=node_task))
        mock_delay.assert_called_once_with(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=snapshot.id,
        )

    def _async_outcome(
        self,
        *,
        status: str = NodeTask.Status.SUCCESS,
        kopia_snapshot_id: str = "kopia-snap-1",
        result: dict | None = None,
        last_error: str = "",
    ) -> dict:
        payload = dict(result or {})
        if kopia_snapshot_id and "kopia_snapshot_id" not in payload:
            payload["kopia_snapshot_id"] = kopia_snapshot_id
        if kopia_snapshot_id and "snapshot" not in payload:
            payload["snapshot"] = {
                "rootEntry": {
                    "summ": {
                        "size": 2048,
                        "files": 32,
                        "dirs": 4,
                    },
                },
            }
        return {
            "status": status,
            "result": payload,
            "last_error": last_error,
        }

    def _mock_run_agent_task_async(self, outcomes: list[dict]):
        counter = {"index": 0}

        def factory(**kwargs):
            outcome = outcomes[min(counter["index"], len(outcomes) - 1)]
            counter["index"] += 1
            node = Node.objects.get(id=kwargs.get("node_id", self.agent.id))
            node_task = NodeTask.objects.create(
                organization=self.org,
                node=node,
                kind=kwargs.get("kind", "backup.run"),
                correlation_type=kwargs.get("correlation_type", "protection.backup"),
                correlation_id=kwargs.get("correlation_id", ""),
                payload=kwargs.get("payload") or {},
                status=outcome.get("status", NodeTask.Status.SUCCESS),
                result=outcome.get("result") or {},
                last_error=outcome.get("last_error", ""),
                watchdog_deadline_at=timezone.now(),
            )
            return AgentTaskHandle(task_id=str(node_task.id), task=node_task)

        return factory

    def _run_orchestrated_backup(self, *, task, snapshot):
        with patch(
            "apps.protection.services.backup_orchestrator.effective_agent_node_status",
            return_value=Node.Availability.ONLINE,
        ):
            return run_backup_task(
                organization_id=self.org.id,
                task_uuid=str(task.task_uuid),
                source_snapshot_id=snapshot.id,
            )

    def _create_backup_task_and_snapshot(self, *, idempotency_key: str):
        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Backup Agent backup config",
            trigger_type=Task.TriggerType.MANUAL,
            request_payload={
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "backup_config_id": self.config.id,
                "repository_id": self.repository.id,
            },
            resources=[
                {"resource_type": "backup_source", "resource_id": self.agent.id},
                {"resource_type": "repository", "resource_id": self.repository.id},
            ],
            steps=[
                {"step_name": "create_logic_snapshot"},
                {"step_name": "kopia_snapshot"},
                {"step_name": "finalize_snapshot"},
            ],
        )
        snapshot = create_source_snapshot(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=task.id,
            task_uuid=task.task_uuid,
            idempotency_key=idempotency_key,
            directory_count=1,
        )
        return task, snapshot

    @override_settings(PROTECTION_BACKUP_EXECUTION_BACKEND="celery")
    @patch("apps.protection.tasks.backup.execute_backup_source_task.delay")
    def test_queue_backup_execution_defaults_to_celery(self, mock_delay):
        _queue_backup_execution(
            organization_id=self.org.id,
            task_uuid="task-uuid-1",
            source_snapshot_id=123,
        )

        mock_delay.assert_called_once_with(
            organization_id=self.org.id,
            task_uuid="task-uuid-1",
            source_snapshot_id=123,
        )

    @patch("apps.protection.services.backup_task.cache.add", return_value=False)
    def test_run_backup_task_returns_when_execution_lock_exists(self, mock_cache_add):
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-run-backup-task-lock",
        )

        result = run_backup_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=snapshot.id,
        )

        self.assertEqual(result["status"], "already_running")
        mock_cache_add.assert_called_once()

    @patch("apps.protection.services.backup_task._queue_backup_execution")
    @patch(
        "apps.protection.tasks.directory_size_estimate."
        "refresh_backup_config_directory_estimates_task.delay"
    )
    def test_start_backup_task_api_creates_task_and_source_snapshot(
        self,
        mock_size_refresh,
        mock_queue,
    ):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/v1/protection/backup-tasks/",
                {
                    "source_ids": [f"agent:{self.agent.id}"],
                    "trigger_type": "manual",
                },
                format="json",
                **self._headers(),
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(response.data["created_count"], 1)
        result = response.data["results"][0]
        self.assertEqual(result["status"], "created")
        task = Task.objects.get(id=result["task_id"])
        snapshot = BackupSourceSnapshot.objects.get(id=result["source_snapshot_id"])
        self.assertEqual(task.task_type, Task.Type.BACKUP)
        self.assertEqual(task.display_name, f"Backup {self.agent.name}")
        self.assertEqual(task.trigger_type, Task.TriggerType.MANUAL)
        self.assertEqual(task.status, Task.Status.PENDING)
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.CREATING)
        self.assertEqual(snapshot.trigger_type, BackupSourceSnapshot.TriggerType.MANUAL)
        self.assertEqual(snapshot.task_id, task.id)
        self.assertEqual(snapshot.backup_config_id, self.config.id)
        source_resource = TaskResource.objects.get(task=task, resource_type=TaskResource.Type.BACKUP_SOURCE)
        self.assertEqual(source_resource.resource_subtype, "agent")
        self.assertEqual(source_resource.resource_id, self.agent.id)
        mock_queue.assert_called_once()
        mock_size_refresh.assert_called_once_with(
            config_id=self.config.id,
            force_refresh=True,
            task_uuid=str(task.task_uuid),
        )
        self.assertTrue(task.request_payload["directory_size_refresh_required"])

    @patch("apps.protection.services.directory_size_estimate.run_agent_task_sync")
    @patch("apps.protection.services.backup_task._queue_backup_execution")
    @patch(
        "apps.protection.tasks.directory_size_estimate."
        "refresh_backup_config_directory_estimates_task.delay"
    )
    def test_start_backup_task_does_not_sync_directory_size_estimate(
        self,
        mock_size_refresh,
        mock_queue,
        mock_path_size,
    ):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/v1/protection/backup-tasks/",
                {
                    "source_ids": [f"agent:{self.agent.id}"],
                    "trigger_type": "manual",
                },
                format="json",
                **self._headers(),
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(response.data["created_count"], 1)
        mock_queue.assert_called_once()
        mock_size_refresh.assert_called_once()
        mock_path_size.assert_not_called()

    @patch("apps.protection.services.backup_task._queue_backup_execution")
    def test_start_backup_task_api_accepts_backup_config_ids_without_source_ids(self, mock_queue):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/v1/protection/backup-tasks/",
                {
                    "backup_config_ids": [self.config.id],
                    "trigger_type": "manual",
                },
                format="json",
                **self._headers(),
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(response.data["created_count"], 1)
        result = response.data["results"][0]
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["source_type"], "agent")
        self.assertEqual(result["source_ref_id"], self.agent.id)
        self.assertEqual(result["backup_config_id"], self.config.id)
        mock_queue.assert_called_once()

    def test_cancel_backup_finalizes_creating_snapshot_as_failed(self):
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-cancel-backup-failed-snapshot",
        )

        result = cancel_backup(organization_id=self.org.id, task_uuid=str(task.task_uuid))

        task.refresh_from_db()
        snapshot.refresh_from_db()
        directory = BackupSourceSnapshotDirectory.objects.get(source_snapshot=snapshot)
        self.assertEqual(result["status"], Task.Status.CANCELLED)
        self.assertEqual(result["source_snapshot_status"], BackupSourceSnapshot.Status.FAILED)
        self.assertEqual(task.status, Task.Status.CANCELLED)
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.FAILED)
        self.assertEqual(snapshot.successful_directory_count, 0)
        self.assertEqual(snapshot.failed_directory_count, 1)
        self.assertEqual(snapshot.error_code, "TASK_CANCELLED")
        self.assertEqual(directory.status, BackupSourceSnapshotDirectory.Status.CANCELLED)
        self.assertEqual(directory.error_code, "TASK_CANCELLED")
        self.assertEqual(directory.error_message, "Task cancelled by user")

    def test_cancel_backup_finalizes_creating_snapshot_as_partial_when_some_directories_succeeded(self):
        extra_directory = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data/archive",
            display_name="archive",
            sort_order=1,
        )
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-cancel-backup-partial-snapshot",
        )
        snapshot.directory_count = 2
        snapshot.save(update_fields=["directory_count", "updated_at"])
        success_directory = BackupConfigDirectory.objects.get(backup_config=self.config, path="/data/projects")
        BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=snapshot,
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=success_directory.id,
            source_path=success_directory.path,
            path_type=success_directory.path_type,
            display_name=success_directory.display_name,
            repository_id=self.repository.id,
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
            kopia_snapshot_id="kopia-success-before-cancel",
            size_bytes=2048,
            file_count=12,
            dir_count=3,
        )

        result = cancel_backup(organization_id=self.org.id, task_uuid=str(task.task_uuid))

        task.refresh_from_db()
        snapshot.refresh_from_db()
        success_row = BackupSourceSnapshotDirectory.objects.get(
            source_snapshot=snapshot,
            backup_config_dir_id=success_directory.id,
        )
        cancelled_row = BackupSourceSnapshotDirectory.objects.get(
            source_snapshot=snapshot,
            backup_config_dir_id=extra_directory.id,
        )
        self.assertEqual(result["source_snapshot_status"], BackupSourceSnapshot.Status.PARTIAL)
        self.assertEqual(task.status, Task.Status.CANCELLED)
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.PARTIAL)
        self.assertEqual(snapshot.successful_directory_count, 1)
        self.assertEqual(snapshot.failed_directory_count, 1)
        self.assertEqual(success_row.status, BackupSourceSnapshotDirectory.Status.AVAILABLE)
        self.assertEqual(cancelled_row.status, BackupSourceSnapshotDirectory.Status.CANCELLED)
        self.assertEqual(cancelled_row.error_code, "TASK_CANCELLED")

    @patch("apps.protection.services.backup_task._queue_backup_execution")
    def test_start_backup_task_allows_agent_source_with_proxy_fs_repository(self, mock_queue):
        proxy = Node.objects.create(
            organization=self.org,
            name="proxy-backup-task-incompatible",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.63",
        )
        proxy_repo = Repository.objects.create(
            organization_id=self.org.id,
            name="backup-task-proxy-fs-repo",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={"proxy_node_dir": "/repo"},
        )
        self.config.repository_id = proxy_repo.id
        self.config.save(update_fields=["repository_id", "updated_at"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/v1/protection/backup-tasks/",
                {
                    "source_ids": [f"agent:{self.agent.id}"],
                    "trigger_type": "manual",
                },
                format="json",
                **self._headers(),
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(response.data["created_count"], 1)
        result = response.data["results"][0]
        self.assertEqual(result["status"], "created")
        task = Task.objects.get(id=result["task_id"])
        self.assertEqual(task.request_payload["repository_id"], proxy_repo.id)

    @patch("apps.protection.services.backup_task._queue_backup_execution")
    def test_start_backup_task_api_normalizes_legacy_api_trigger_to_manual(self, mock_queue):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/v1/protection/backup-tasks/",
                {
                    "source_ids": [f"agent:{self.agent.id}"],
                    "trigger_type": "api",
                },
                format="json",
                **self._headers(),
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        result = response.data["results"][0]
        task = Task.objects.get(id=result["task_id"])
        snapshot = BackupSourceSnapshot.objects.get(id=result["source_snapshot_id"])
        self.assertEqual(task.trigger_type, Task.TriggerType.MANUAL)
        self.assertEqual(snapshot.trigger_type, BackupSourceSnapshot.TriggerType.MANUAL)
        mock_queue.assert_called_once()

    @patch("apps.protection.services.backup_task._queue_backup_execution")
    def test_start_backup_task_allows_agent_source_with_proxy_bound_nas_repository(self, mock_queue):
        proxy = Node.objects.create(
            organization=self.org,
            name="proxy-backup-task-nas",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.64",
        )
        proxy_repo = Repository.objects.create(
            organization_id=self.org.id,
            name="backup-task-proxy-nas-repo",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            nas_protocol=Repository.NasProtocol.SMB,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={
                "server_address": "10.0.0.20",
                "share_path": "/backup",
                "smb_username": "backup_user",
            },
        )
        self.config.repository_id = proxy_repo.id
        self.config.save(update_fields=["repository_id", "updated_at"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/v1/protection/backup-tasks/",
                {
                    "source_ids": [f"agent:{self.agent.id}"],
                    "trigger_type": "manual",
                },
                format="json",
                **self._headers(),
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(response.data["created_count"], 1)
        result = response.data["results"][0]
        self.assertEqual(result["status"], "created")
        task = Task.objects.get(id=result["task_id"])
        self.assertEqual(task.request_payload["repository_id"], proxy_repo.id)
        mock_queue.assert_called_once()

    @patch("apps.protection.services.backup_task._queue_backup_execution")
    @patch(
        "apps.protection.tasks.directory_size_estimate."
        "refresh_backup_config_directory_estimates_task.delay"
    )
    def test_start_backup_task_api_reuses_item_when_idempotency_key_repeated(
        self,
        mock_size_refresh,
        mock_queue,
    ):
        payload = {
            "source_ids": [f"agent:{self.agent.id}"],
            "trigger_type": "manual",
            "idempotency_key": "batch-001",
        }
        with self.captureOnCommitCallbacks(execute=True):
            first = self.client.post(
                "/api/v1/protection/backup-tasks/",
                payload,
                format="json",
                **self._headers(),
            )
        with self.captureOnCommitCallbacks(execute=True):
            second = self.client.post(
                "/api/v1/protection/backup-tasks/",
                payload,
                format="json",
                **self._headers(),
            )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.content)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED, second.content)
        self.assertEqual(first.data["created_count"], 1)
        self.assertEqual(second.data["created_count"], 0)
        self.assertEqual(second.data["skipped_count"], 1)
        self.assertEqual(second.data["results"][0]["status"], "skipped")
        self.assertEqual(Task.objects.count(), 1)
        self.assertEqual(BackupSourceSnapshot.objects.count(), 1)
        mock_queue.assert_called_once()
        mock_size_refresh.assert_called_once()

    def test_start_backup_task_api_rejects_running_duplicate(self):
        Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Running backup",
            status=Task.Status.RUNNING,
            trigger_type=Task.TriggerType.MANUAL,
            request_payload={
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "backup_config_id": self.config.id,
                "repository_id": self.repository.id,
            },
        )

        response = self.client.post(
            "/api/v1/protection/backup-tasks/",
            {
                "source_ids": [f"agent:{self.agent.id}"],
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(response.data["created_count"], 0)
        self.assertEqual(response.data["results"][0]["status"], "conflict")

    @patch("apps.protection.services.backup_orchestrator.enqueue_repository_usage_refresh")
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_run_backup_task_records_directory_snapshot_and_completes_task(self, mock_run_agent_task_async, mock_sync_repository_usage):
        mock_sync_repository_usage.return_value = {"queued": True, "task_id": "usage-refresh"}
        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Backup Agent backup config",
            trigger_type=Task.TriggerType.MANUAL,
            request_payload={
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "backup_config_id": self.config.id,
                "repository_id": self.repository.id,
            },
            resources=[
                {"resource_type": "backup_source", "resource_id": self.agent.id},
                {"resource_type": "repository", "resource_id": self.repository.id},
            ],
            steps=[
                {"step_name": "create_logic_snapshot"},
                {"step_name": "kopia_snapshot"},
                {"step_name": "finalize_snapshot"},
            ],
        )
        snapshot = create_source_snapshot(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=task.id,
            task_uuid=task.task_uuid,
            idempotency_key="test-run-backup-task",
            directory_count=1,
        )
        mock_run_agent_task_async.side_effect = self._mock_run_agent_task_async(
            [self._async_outcome(kopia_snapshot_id="kopia-snap-1")]
        )

        result = self._run_orchestrated_backup(task=task, snapshot=snapshot)

        task.refresh_from_db()
        snapshot.refresh_from_db()
        directory = BackupSourceSnapshotDirectory.objects.get(source_snapshot=snapshot)
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.AVAILABLE)
        self.assertEqual(directory.kopia_snapshot_id, "kopia-snap-1")
        self.assertEqual(directory.status, BackupSourceSnapshotDirectory.Status.AVAILABLE)
        self.assertEqual(directory.size_bytes, 2048)
        self.assertEqual(directory.file_count, 32)
        self.assertEqual(directory.dir_count, 4)
        self.assertEqual(snapshot.successful_directory_count, 1)
        self.assertEqual(snapshot.total_size_bytes, 2048)
        self.assertEqual(snapshot.file_count, 32)
        self.assertEqual(snapshot.dir_count, 4)
        self.assertEqual(result["total_size_bytes"], 2048)
        self.assertEqual(result["file_count"], 32)

    @patch("apps.protection.services.backup_orchestrator.enqueue_repository_usage_refresh")
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_backup_uses_configured_internal_endpoint_while_default_is_external(
        self,
        mock_run_agent_task_async,
        mock_sync_repository_usage,
    ):
        external = "oss-cn-hangzhou.aliyuncs.com"
        internal = "oss-cn-hangzhou-internal.aliyuncs.com"
        self.repository.config = {
            **self.repository.config,
            "endpoint": external,
            "external_endpoint": external,
            "internal_endpoint": internal,
            "s3_url_style": "virtual_hosted",
            "use_tls": True,
        }
        self.repository.save(update_fields=["config", "updated_at"])
        self.config.repository_endpoint_type = "internal"
        self.config.save(update_fields=["repository_endpoint_type", "updated_at"])
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-configured-internal-endpoint",
        )
        mock_sync_repository_usage.return_value = {"queued": True}
        mock_run_agent_task_async.side_effect = self._mock_run_agent_task_async(
            [self._async_outcome(kopia_snapshot_id="kopia-frozen-endpoint")]
        )

        self._run_orchestrated_backup(task=task, snapshot=snapshot)

        backup_payload = mock_run_agent_task_async.call_args.kwargs["payload"]
        self.assertEqual(backup_payload["repository"]["endpoint"], internal)
        default_runtime = _repository_runtime_payload(
            repository=self.repository,
            execution_target=ExecutionTarget(
                node=self.agent,
                source_type="agent",
                source_ref_id=self.agent.id,
            ),
        )
        self.assertEqual(default_runtime["endpoint"], external)

    @patch("apps.protection.services.backup_orchestrator.enqueue_repository_usage_refresh")
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_run_backup_task_passes_frozen_runtime_policy(self, mock_run_agent_task_async, mock_sync_repository_usage):
        mock_sync_repository_usage.return_value = {"queued": True}
        policy = BackupPolicy.objects.create(
            organization_id=self.org.id,
            name="Skip unreadable",
            is_active=True,
            schedule={"enabled": True, "cron_expr": "0 2 * * *"},
            retention={
                "enabled": True,
                "recent_points": 2,
                "hourly_enabled": False,
                "hourly_hours": 1,
                "daily_enabled": False,
                "daily_days": 1,
                "weekly_enabled": False,
                "weekly_weeks": 1,
                "monthly_enabled": False,
                "monthly_months": 1,
                "annual_enabled": False,
                "annual_years": 1,
            },
            throttling={"enabled": False, "unlimited": True, "rate_mbps": 0},
            error_handling={
                "enabled": True,
                "ignore_directory_read_errors": True,
                "ignore_file_read_errors": True,
                "ignore_unknown_entries": False,
            },
        )
        self.config.backup_policy_id = policy.id
        self.config.save(update_fields=["backup_policy_id", "updated_at"])
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-policy-error-handling",
        )
        policy.error_handling = {
            "enabled": False,
            "ignore_directory_read_errors": False,
            "ignore_file_read_errors": False,
            "ignore_unknown_entries": False,
        }
        policy.save(update_fields=["error_handling", "updated_at"])
        self.config.compression_level = BackupConfig.CompressionLevel.HIGH
        self.config.save(update_fields=["compression_level", "updated_at"])
        mock_run_agent_task_async.side_effect = self._mock_run_agent_task_async(
            [self._async_outcome(kopia_snapshot_id="kopia-policy-1")]
        )

        result = self._run_orchestrated_backup(task=task, snapshot=snapshot)

        payload = mock_run_agent_task_async.call_args.kwargs["payload"]
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.policy_snapshot["version"], 1)
        self.assertEqual(snapshot.policy_snapshot["compression"]["level"], "balanced")
        self.assertEqual(payload["backup_policy"]["policy_id"], policy.id)
        self.assertEqual(
            payload["backup_policy"]["advanced_settings"],
            {
                "enabled": True,
                "skip_unreadable_directories": True,
                "skip_unreadable_files": True,
                "skip_unsupported_filesystem_entries": False,
            },
        )
        self.assertEqual(payload["compression"]["level"], "balanced")
        self.assertEqual(payload["compression"]["compressor"], "zstd")
        self.assertEqual(result["dir_count"], 4)
        self.assertEqual(mock_sync_repository_usage.call_count, 1)
        list_response = self.client.get(
            "/api/v1/protection/backup-source-snapshots/",
            {"source_type": "agent", "source_ref_id": self.agent.id},
            **self._headers(),
        )
        self.assertEqual(list_response.status_code, status.HTTP_200_OK, list_response.content)
        self.assertEqual(list_response.data["results"][0]["total_size_bytes"], 2048)
        self.assertEqual(list_response.data["results"][0]["file_count"], 32)
        self.assertEqual(list_response.data["results"][0]["dir_count"], 4)
        BackupSourceSnapshot.objects.filter(id=snapshot.id).update(
            total_size_bytes=0,
            file_count=0,
            dir_count=0,
        )
        fallback_response = self.client.get(
            "/api/v1/protection/backup-source-snapshots/",
            {"source_type": "agent", "source_ref_id": self.agent.id},
            **self._headers(),
        )
        self.assertEqual(fallback_response.status_code, status.HTTP_200_OK, fallback_response.content)
        self.assertEqual(fallback_response.data["results"][0]["total_size_bytes"], 2048)
        self.assertEqual(fallback_response.data["results"][0]["file_count"], 32)
        self.assertEqual(fallback_response.data["results"][0]["dir_count"], 4)
        self.assertEqual(result["source_snapshot_status"], BackupSourceSnapshot.Status.AVAILABLE)
        dispatch_event = TaskEvent.objects.get(
            task=task,
            step__step_name="kopia_snapshot",
            message="Dispatching directory backup to agent",
            metadata__node_id=self.agent.id,
            metadata__repository_id=self.repository.id,
        )
        self.assertEqual(dispatch_event.metadata["object_name"], "/data/projects")
        snapshot_event = TaskEvent.objects.get(
            task=task,
            step__step_name="kopia_snapshot",
            message="Directory snapshot created",
            metadata__kopia_snapshot_id="kopia-policy-1",
        )
        self.assertEqual(snapshot_event.metadata["object_id"], "kopia-policy-1")
        self.assertEqual(snapshot_event.metadata["object_name"], "/data/projects")
        logical_event = TaskEvent.objects.get(task=task, message="Logical snapshot created")
        self.assertEqual(logical_event.metadata["object_name"], snapshot.snapshot_uid)

    @patch("apps.protection.services.backup_orchestrator.enqueue_repository_usage_refresh")
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_run_backup_task_recovers_existing_successful_node_task(
        self,
        mock_run_agent_task_async,
        mock_sync_repository_usage,
    ):
        mock_sync_repository_usage.return_value = {"queued": True, "task_id": "usage-refresh"}
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-run-backup-task-recover-node-success",
        )
        directory = self.config.directories.first()
        node_task = NodeTask.objects.create(
            organization=self.org,
            node=self.agent,
            kind="backup.run",
            correlation_type="protection.backup",
            correlation_id=str(task.task_uuid),
            payload={
                "backup_config_dir_id": directory.id,
                "source_path": directory.path,
            },
            result={"kopia_snapshot_id": "kopia-recovered"},
            status=NodeTask.Status.SUCCESS,
            watchdog_deadline_at=timezone.now(),
        )

        result = self._run_orchestrated_backup(task=task, snapshot=snapshot)

        snapshot.refresh_from_db()
        row = BackupSourceSnapshotDirectory.objects.get(source_snapshot=snapshot)
        self.assertEqual(result["source_snapshot_status"], BackupSourceSnapshot.Status.AVAILABLE)
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.AVAILABLE)
        self.assertEqual(row.status, BackupSourceSnapshotDirectory.Status.AVAILABLE)
        self.assertEqual(row.kopia_snapshot_id, "kopia-recovered")
        mock_run_agent_task_async.assert_not_called()
        self.assertTrue(
            TaskEvent.objects.filter(
                task=task,
                step__step_name="kopia_snapshot",
                message="Directory snapshot created",
                metadata__node_task_id=str(node_task.id),
                metadata__kopia_snapshot_id="kopia-recovered",
            ).exists()
        )

    @patch("apps.protection.services.backup_orchestrator.enqueue_repository_usage_refresh")
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_run_backup_task_resumes_existing_running_node_task(
        self,
        mock_run_agent_task_async,
        mock_sync_repository_usage,
    ):
        mock_sync_repository_usage.return_value = {"queued": True, "task_id": "usage-refresh"}
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-run-backup-task-resume-node-running",
        )
        directory = self.config.directories.first()
        node_task = NodeTask.objects.create(
            organization=self.org,
            node=self.agent,
            kind="backup.run",
            correlation_type="protection.backup",
            correlation_id=str(task.task_uuid),
            payload={
                "backup_config_dir_id": directory.id,
                "source_path": directory.path,
            },
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now(),
        )
        mock_run_agent_task_async.assert_not_called()

        node_task.status = NodeTask.Status.SUCCESS
        node_task.result = {"kopia_snapshot_id": "kopia-resumed"}
        node_task.save(update_fields=["status", "result", "updated_at"])

        self._run_orchestrated_backup(task=task, snapshot=snapshot)

        row = BackupSourceSnapshotDirectory.objects.get(source_snapshot=snapshot)
        self.assertEqual(row.status, BackupSourceSnapshotDirectory.Status.AVAILABLE)
        self.assertEqual(row.kopia_snapshot_id, "kopia-resumed")
        mock_run_agent_task_async.assert_not_called()

    @patch(
        "apps.protection.services.backup_orchestrator.effective_agent_node_status",
        return_value=Node.Availability.ONLINE,
    )
    @patch("apps.protection.services.backup_orchestrator.enqueue_repository_usage_refresh")
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_run_backup_task_skips_existing_terminal_directory_result(
        self,
        mock_run_agent_task_async,
        mock_sync_repository_usage,
        _mock_node_online,
    ):
        mock_sync_repository_usage.return_value = {"queued": True, "task_id": "usage-refresh"}
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-run-backup-task-skip-terminal-directory",
        )
        directory = self.config.directories.first()
        BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=snapshot,
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=directory.id,
            source_path=directory.path,
            display_name=directory.display_name,
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-existing",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )

        run_backup_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=snapshot.id,
        )

        task.refresh_from_db()
        snapshot.refresh_from_db()
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.AVAILABLE)
        mock_run_agent_task_async.assert_not_called()

    def test_reconcile_interrupted_backup_tasks_advances_active_backup(self):
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-reconcile-interrupted-backup",
        )

        with patch(
            "apps.protection.services.backup_orchestrator.advance_backup",
            return_value={"status": Task.Status.RUNNING},
        ) as mock_advance:
            summary = reconcile_interrupted_backup_tasks(limit=10)

        self.assertEqual(summary["candidates"], 1)
        self.assertEqual(summary["advanced"], 1)
        mock_advance.assert_called_once_with(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=snapshot.id,
        )

    @patch(
        "apps.protection.services.backup_orchestrator.effective_agent_node_status",
        return_value=Node.Availability.ONLINE,
    )
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_reconcile_backup_tasks_advances_pending_backup_idempotently(
        self,
        mock_run_agent_task_async,
        _mock_node_online,
    ):
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-reconcile-pending-backup",
        )
        mock_run_agent_task_async.side_effect = self._mock_run_agent_task_async(
            [self._async_outcome(status=NodeTask.Status.RUNNING, kopia_snapshot_id="")]
        )

        first_summary = reconcile_backup_tasks(limit=10)
        second_summary = reconcile_backup_tasks(limit=10)

        task.refresh_from_db()
        logic_step = TaskStep.objects.get(task=task, step_name="create_logic_snapshot")
        kopia_step = TaskStep.objects.get(task=task, step_name="kopia_snapshot")
        directory = BackupSourceSnapshotDirectory.objects.get(source_snapshot=snapshot)
        self.assertEqual(first_summary["candidates"], 1)
        self.assertEqual(first_summary["advanced"], 1)
        self.assertEqual(second_summary["candidates"], 1)
        self.assertEqual(second_summary["advanced"], 1)
        self.assertEqual(task.status, Task.Status.RUNNING)
        self.assertIsNotNone(task.started_at)
        self.assertEqual(logic_step.status, TaskStep.Status.SUCCESS)
        self.assertEqual(float(logic_step.progress), 100)
        self.assertEqual(kopia_step.status, TaskStep.Status.RUNNING)
        self.assertEqual(directory.status, BackupSourceSnapshotDirectory.Status.RUNNING)
        self.assertIsNotNone(directory.node_task_id)
        self.assertEqual(snapshot.directories.count(), 1)
        self.assertEqual(mock_run_agent_task_async.call_count, 1)

    @patch("apps.protection.services.backup_orchestrator._requeue_same_backup_task")
    def test_stale_pending_queue_requeues_original_identity_once(
        self, mock_requeue
    ):
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-pending-queue-requeue",
        )
        Task.objects.filter(pk=task.pk).update(
            created_at=timezone.now() - timezone.timedelta(minutes=16)
        )
        task.refresh_from_db()

        with patch(
            "apps.node.services.internal.redis_store.ws_recovery_hold_active",
            return_value=False,
        ):
            action = _reconcile_pending_backup_queue(task=task, source_snapshot=snapshot)

        self.assertEqual(action, "requeued")
        mock_requeue.assert_called_once()
        requeued_task = mock_requeue.call_args.kwargs["task"]
        requeued_snapshot = mock_requeue.call_args.kwargs["source_snapshot"]
        self.assertEqual(requeued_task.task_uuid, task.task_uuid)
        self.assertEqual(requeued_snapshot.id, snapshot.id)
        task.refresh_from_db()
        self.assertEqual(task.result_payload["backup_queue_recovery"]["attempt_count"], 1)

    @patch("apps.protection.services.backup_orchestrator._stop_repository_server_for_task")
    def test_pending_queue_timeout_fully_seals_snapshot(self, mock_stop):
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-pending-queue-timeout",
        )
        directory_config = self.config.directories.first()
        directory = BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=snapshot,
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=directory_config.id,
            source_path=directory_config.path,
            repository_id=self.repository.id,
            status=BackupSourceSnapshotDirectory.Status.PENDING,
        )
        attempted_at = timezone.now() - timezone.timedelta(minutes=3)
        task.result_payload = {
            "source_snapshot_id": snapshot.id,
            "backup_queue_recovery": {
                "attempted_at": attempted_at.isoformat(),
                "attempt_count": 1,
            },
        }
        task.save(update_fields=["result_payload", "updated_at"])
        Task.objects.filter(pk=task.pk).update(
            created_at=timezone.now() - timezone.timedelta(minutes=20)
        )
        task.refresh_from_db()

        with patch(
            "apps.node.services.internal.redis_store.ws_recovery_hold_active",
            return_value=False,
        ):
            action = _reconcile_pending_backup_queue(task=task, source_snapshot=snapshot)

        self.assertEqual(action, "timed_out")
        task.refresh_from_db()
        snapshot.refresh_from_db()
        directory.refresh_from_db()
        self.assertEqual(task.status, Task.Status.TIMEOUT)
        self.assertEqual(task.error_code, "TASK_QUEUE_TIMEOUT")
        self.assertTrue(task.result_payload["queue_timeout_sealed"])
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.FAILED)
        self.assertEqual(snapshot.error_code, "TASK_QUEUE_TIMEOUT")
        self.assertEqual(directory.status, BackupSourceSnapshotDirectory.Status.FAILED)
        self.assertEqual(directory.error_code, "TASK_QUEUE_TIMEOUT")
        mock_stop.assert_called_once()

    @patch(
        "apps.protection.services.backup_orchestrator.effective_agent_node_status",
        return_value=Node.Availability.ONLINE,
    )
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_agent_restart_retries_once_inside_same_logical_snapshot(
        self, mock_run_agent_task_async, _online
    ):
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-agent-restart-same-snapshot",
        )
        mock_run_agent_task_async.side_effect = self._mock_run_agent_task_async(
            [
                {
                    "status": NodeTask.Status.FAILED,
                    "result": {},
                    "last_error": "agent restarted before task completed",
                },
                self._async_outcome(kopia_snapshot_id="restart-recovered-snapshot"),
            ]
        )

        result = self._run_orchestrated_backup(task=task, snapshot=snapshot)
        if result["status"] == Task.Status.RUNNING:
            result = self._run_orchestrated_backup(task=task, snapshot=snapshot)

        task.refresh_from_db()
        snapshot.refresh_from_db()
        directory = BackupSourceSnapshotDirectory.objects.get(source_snapshot=snapshot)
        attempts = list(
            NodeTask.objects.filter(
                correlation_type=protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE,
                correlation_id=str(task.task_uuid),
                kind="backup.run",
            ).order_by("created_at", "id")
        )
        self.assertEqual(result["status"], Task.Status.SUCCESS)
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.AVAILABLE)
        self.assertEqual(directory.retry_count, 1)
        self.assertEqual(directory.kopia_snapshot_id, "restart-recovered-snapshot")
        self.assertEqual(len(attempts), 2)
        self.assertNotEqual(attempts[0].id, attempts[1].id)
        self.assertTrue(all(row.correlation_id == str(task.task_uuid) for row in attempts))

    @patch(
        "apps.protection.services.backup_orchestrator.effective_agent_node_status",
        return_value=Node.Availability.ONLINE,
    )
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_prepared_snapshot_protocol_serializes_policy_then_dispatches_uploads_in_parallel(
        self,
        mock_run_agent_task_async,
        _mock_node_online,
    ):
        self.agent.metadata = {
            "inventory": {"capabilities": ["backup_prepared_snapshot_v1"]}
        }
        self.agent.save(update_fields=["metadata", "updated_at"])
        BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data/archive",
            display_name="archive",
            sort_order=1,
        )
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-prepared-snapshot-barrier",
        )
        snapshot.directory_count = 2
        snapshot.save(update_fields=["directory_count", "updated_at"])
        mock_run_agent_task_async.side_effect = self._mock_run_agent_task_async(
            [
                self._async_outcome(kopia_snapshot_id=""),
                self._async_outcome(kopia_snapshot_id=""),
                self._async_outcome(status=NodeTask.Status.RUNNING, kopia_snapshot_id=""),
                self._async_outcome(status=NodeTask.Status.RUNNING, kopia_snapshot_id=""),
            ]
        )

        first = self._run_orchestrated_backup(task=task, snapshot=snapshot)
        self.assertEqual(first["orchestrator"], "policy_prepare")
        self.assertEqual(
            [call.kwargs["kind"] for call in mock_run_agent_task_async.call_args_list],
            ["repository.policy.apply"],
        )
        policy_event = task.events.get(message="Preparing directory policy")
        self.assertEqual(policy_event.metadata["source_path"], "/data/projects")

        second = self._run_orchestrated_backup(task=task, snapshot=snapshot)
        self.assertEqual(second["orchestrator"], "policy_prepare")
        self.assertEqual(
            [call.kwargs["kind"] for call in mock_run_agent_task_async.call_args_list],
            ["repository.policy.apply", "repository.policy.apply"],
        )

        third = self._run_orchestrated_backup(task=task, snapshot=snapshot)
        task.refresh_from_db()
        self.assertEqual(third["orchestrator"], "in_progress")
        self.assertEqual(task.result_payload["backup_protocol"], "prepared_snapshot_v1")
        self.assertEqual(
            [call.kwargs["kind"] for call in mock_run_agent_task_async.call_args_list],
            [
                "repository.policy.apply",
                "repository.policy.apply",
                "backup.snapshot.create",
                "backup.snapshot.create",
            ],
        )
        self.assertEqual(
            set(
                BackupSourceSnapshotDirectory.objects.filter(
                    source_snapshot=snapshot
                ).values_list("status", flat=True)
            ),
            {BackupSourceSnapshotDirectory.Status.RUNNING},
        )

    @patch("apps.protection.services.backup_orchestrator.enqueue_repository_usage_refresh")
    @patch(
        "apps.protection.services.backup_orchestrator.effective_agent_node_status",
        return_value=Node.Availability.ONLINE,
    )
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_policy_prepare_retries_before_failing_directory(
        self,
        mock_run_agent_task_async,
        _mock_node_online,
        mock_usage_refresh,
    ):
        self.agent.metadata = {
            "inventory": {"capabilities": ["backup_prepared_snapshot_v1"]}
        }
        self.agent.save(update_fields=["metadata", "updated_at"])
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-policy-prepare-retry",
        )
        mock_usage_refresh.return_value = {"queued": True, "task_id": "usage-refresh"}
        failed_policy = self._async_outcome(
            status=NodeTask.Status.FAILED,
            kopia_snapshot_id="",
            result={
                "error_code": "POLICY_APPLY_FAILED",
                "policy_phase": "apply",
                "stderr_tail": "policy apply failed",
            },
            last_error="policy apply failed",
        )
        mock_run_agent_task_async.side_effect = self._mock_run_agent_task_async(
            [failed_policy, failed_policy]
        )

        with (
            patch.object(
                protection_conf,
                "PROTECTION_BACKUP_POLICY_PREPARE_MAX_RETRIES",
                1,
            ),
            patch.object(
                protection_conf,
                "PROTECTION_BACKUP_POLICY_PREPARE_RETRY_DELAYS",
                (0,),
            ),
        ):
            self._run_orchestrated_backup(task=task, snapshot=snapshot)
            scheduled = self._run_orchestrated_backup(task=task, snapshot=snapshot)
            self._run_orchestrated_backup(task=task, snapshot=snapshot)
            final = self._run_orchestrated_backup(task=task, snapshot=snapshot)

        row = BackupSourceSnapshotDirectory.objects.get(source_snapshot=snapshot)
        attempts = list(
            NodeTask.objects.filter(
                correlation_type=(
                    protection_conf.PROTECTION_BACKUP_POLICY_PREPARE_CORRELATION_TYPE
                ),
                correlation_id=str(task.task_uuid),
            ).order_by("created_at", "id")
        )
        self.assertEqual(scheduled["orchestrator"], "policy_prepare")
        self.assertEqual(final["status"], Task.Status.FAILED)
        self.assertEqual(row.status, BackupSourceSnapshotDirectory.Status.FAILED)
        self.assertEqual(row.error_code, "POLICY_APPLY_FAILED")
        self.assertEqual([attempt.payload["policy_attempt"] for attempt in attempts], [1, 2])
        self.assertEqual(mock_run_agent_task_async.call_count, 2)

    @patch("apps.protection.services.backup_orchestrator.enqueue_repository_usage_refresh")
    @patch(
        "apps.protection.services.backup_orchestrator.effective_agent_node_status",
        return_value=Node.Availability.ONLINE,
    )
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_policy_prepare_failure_does_not_block_other_parallel_uploads(
        self,
        mock_run_agent_task_async,
        _mock_node_online,
        mock_usage_refresh,
    ):
        self.agent.metadata = {
            "inventory": {"capabilities": ["backup_prepared_snapshot_v1"]}
        }
        self.agent.save(update_fields=["metadata", "updated_at"])
        BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data/archive",
            display_name="archive",
            sort_order=1,
        )
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-policy-failure-continues",
        )
        snapshot.directory_count = 2
        snapshot.save(update_fields=["directory_count", "updated_at"])
        mock_usage_refresh.return_value = {"queued": True, "task_id": "usage-refresh"}
        mock_run_agent_task_async.side_effect = self._mock_run_agent_task_async(
            [
                self._async_outcome(
                    status=NodeTask.Status.FAILED,
                    kopia_snapshot_id="",
                    result={
                        "error_code": "POLICY_APPLY_FAILED",
                        "policy_phase": "apply",
                        "stderr_tail": "policy apply failed",
                    },
                    last_error="policy apply failed",
                ),
                self._async_outcome(kopia_snapshot_id=""),
                self._async_outcome(kopia_snapshot_id="prepared-success"),
            ]
        )

        with patch.object(
            protection_conf,
            "PROTECTION_BACKUP_POLICY_PREPARE_MAX_RETRIES",
            0,
        ):
            self._run_orchestrated_backup(task=task, snapshot=snapshot)
            self._run_orchestrated_backup(task=task, snapshot=snapshot)
            result = self._run_orchestrated_backup(task=task, snapshot=snapshot)

        snapshot.refresh_from_db()
        rows = list(
            BackupSourceSnapshotDirectory.objects.filter(source_snapshot=snapshot).order_by(
                "backup_config_dir_id"
            )
        )
        self.assertEqual(result["status"], Task.Status.FAILED)
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.PARTIAL)
        self.assertEqual(rows[0].status, BackupSourceSnapshotDirectory.Status.FAILED)
        self.assertEqual(rows[0].error_code, "POLICY_APPLY_FAILED")
        self.assertEqual(rows[1].status, BackupSourceSnapshotDirectory.Status.AVAILABLE)
        self.assertEqual(
            [call.kwargs["kind"] for call in mock_run_agent_task_async.call_args_list],
            [
                "repository.policy.apply",
                "repository.policy.apply",
                "backup.snapshot.create",
            ],
        )

    @patch("apps.protection.services.backup_orchestrator.enqueue_repository_usage_refresh")
    @patch(
        "apps.protection.services.backup_orchestrator.effective_agent_node_status",
        return_value=Node.Availability.ONLINE,
    )
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_policy_not_found_repairs_after_parallel_siblings_finish(
        self,
        mock_run_agent_task_async,
        _mock_node_online,
        mock_usage_refresh,
    ):
        self.agent.metadata = {
            "inventory": {"capabilities": ["backup_prepared_snapshot_v1"]}
        }
        self.agent.save(update_fields=["metadata", "updated_at"])
        second_config_directory = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data/archive",
            display_name="archive",
            sort_order=1,
        )
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-policy-not-found-repair",
        )
        snapshot.directory_count = 2
        snapshot.save(update_fields=["directory_count", "updated_at"])
        mock_usage_refresh.return_value = {"queued": True, "task_id": "usage-refresh"}
        mock_run_agent_task_async.side_effect = self._mock_run_agent_task_async(
            [
                self._async_outcome(kopia_snapshot_id=""),
                self._async_outcome(kopia_snapshot_id=""),
                self._async_outcome(
                    status=NodeTask.Status.FAILED,
                    kopia_snapshot_id="",
                    result={
                        "error_code": "KOPIA_POLICY_NOT_FOUND",
                        "stderr_tail": "unable to get policy tree: policy not found",
                    },
                    last_error="kopia policy not found",
                ),
                self._async_outcome(status=NodeTask.Status.RUNNING, kopia_snapshot_id=""),
                self._async_outcome(kopia_snapshot_id="repaired-snapshot"),
            ]
        )

        self._run_orchestrated_backup(task=task, snapshot=snapshot)
        self._run_orchestrated_backup(task=task, snapshot=snapshot)
        self._run_orchestrated_backup(task=task, snapshot=snapshot)

        sibling = NodeTask.objects.get(
            kind="backup.snapshot.create",
            payload__backup_config_dir_id=second_config_directory.id,
        )
        sibling.status = NodeTask.Status.SUCCESS
        sibling.result = {"kopia_snapshot_id": "parallel-sibling-success"}
        sibling.last_error = ""
        sibling.save(update_fields=["status", "result", "last_error", "updated_at"])

        waiting = self._run_orchestrated_backup(task=task, snapshot=snapshot)
        self.assertEqual(waiting["orchestrator"], "in_progress")
        final = self._run_orchestrated_backup(task=task, snapshot=snapshot)

        snapshot.refresh_from_db()
        repaired = BackupSourceSnapshotDirectory.objects.get(
            source_snapshot=snapshot,
            backup_config_dir_id=self.config.directories.order_by("sort_order").first().id,
        )
        self.assertEqual(final["status"], Task.Status.SUCCESS)
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.AVAILABLE)
        self.assertEqual(repaired.kopia_snapshot_id, "repaired-snapshot")
        self.assertEqual(repaired.retry_count, 1)
        self.assertEqual(
            [call.kwargs["kind"] for call in mock_run_agent_task_async.call_args_list],
            [
                "repository.policy.apply",
                "repository.policy.apply",
                "backup.snapshot.create",
                "backup.snapshot.create",
                "backup.run",
            ],
        )

    def test_late_success_projects_terminal_failed_backup_to_available(self):
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-project-late-terminal-success",
        )
        config_directory = self.config.directories.first()
        task.status = Task.Status.FAILED
        task.error_code = "AGENT_OFFLINE"
        task.error_message = "Agent went offline during task execution."
        task.save(update_fields=["status", "error_code", "error_message", "updated_at"])
        snapshot.status = BackupSourceSnapshot.Status.FAILED
        snapshot.failed_directory_count = 1
        snapshot.error_code = "AGENT_OFFLINE"
        snapshot.error_message = "Agent went offline during task execution."
        snapshot.save(
            update_fields=[
                "status",
                "failed_directory_count",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            node=self.agent,
            kind="backup.run",
            correlation_type="protection.backup",
            correlation_id=str(task.task_uuid),
            payload={"backup_config_dir_id": config_directory.id},
            result={"kopia_snapshot_id": "late-kopia-success"},
            status=NodeTask.Status.SUCCESS,
            watchdog_deadline_at=timezone.now(),
        )
        BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=snapshot,
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=config_directory.id,
            source_path=config_directory.path,
            path_type=config_directory.path_type,
            display_name=config_directory.display_name,
            repository_id=self.repository.id,
            node_task_id=node_task.id,
            status=BackupSourceSnapshotDirectory.Status.FAILED,
            error_code="AGENT_BACKUP_FAILED",
            error_message="Agent went offline during task execution.",
        )

        outcome = project_backup_node_task_result(node_task_id=node_task.id)

        task.refresh_from_db()
        snapshot.refresh_from_db()
        row = BackupSourceSnapshotDirectory.objects.get(source_snapshot=snapshot)
        self.assertEqual(outcome["status"], "projected")
        self.assertTrue(outcome["task_corrected"])
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertIsNone(task.error_code)
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.AVAILABLE)
        self.assertEqual(row.status, BackupSourceSnapshotDirectory.Status.AVAILABLE)
        self.assertEqual(row.kopia_snapshot_id, "late-kopia-success")
        self.assertTrue(row.adopted_late_result)

    def test_late_success_does_not_resurrect_snapshot_delete_intent(self):
        task, snapshot = self._create_backup_task_and_snapshot(
            idempotency_key="test-project-late-delete-intent",
        )
        config_directory = self.config.directories.first()
        task.status = Task.Status.FAILED
        task.save(update_fields=["status", "updated_at"])
        snapshot.status = BackupSourceSnapshot.Status.DELETE_FAILED
        snapshot.save(update_fields=["status", "updated_at"])
        node_task = NodeTask.objects.create(
            organization=self.org,
            node=self.agent,
            kind="backup.run",
            correlation_type="protection.backup",
            correlation_id=str(task.task_uuid),
            payload={"backup_config_dir_id": config_directory.id},
            result={"kopia_snapshot_id": "must-not-resurrect"},
            status=NodeTask.Status.SUCCESS,
            watchdog_deadline_at=timezone.now(),
        )
        row = BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=snapshot,
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=config_directory.id,
            source_path=config_directory.path,
            repository_id=self.repository.id,
            node_task_id=node_task.id,
            status=BackupSourceSnapshotDirectory.Status.FAILED,
            error_code="AGENT_OFFLINE",
            error_message="Agent went offline during task execution.",
        )

        outcome = project_backup_node_task_result(node_task_id=node_task.id)

        snapshot.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual(outcome["reason"], "snapshot_delete_intent")
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.DELETE_FAILED)
        self.assertEqual(row.status, BackupSourceSnapshotDirectory.Status.FAILED)
        self.assertIsNone(row.kopia_snapshot_id)

    def test_proxy_bound_nas_repository_payload_uses_storage_id_subdir(self):
        proxy = Node.objects.create(
            organization=self.org,
            name="proxy-backup-task-1",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.62",
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="backup-task-proxy-nas-repo",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            nas_protocol=Repository.NasProtocol.NFS,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={
                "server_address": "10.0.0.20",
                "share_path": "/volume1/backup",
                "kopia_password": "repo-pass",
            },
        )

        payload = _repository_runtime_payload(
            repository=repository,
            execution_target=ExecutionTarget(
                node=proxy,
                source_type="nas",
                source_ref_id=1,
            ),
        )

        self.assertEqual(payload["subdir"], f"hp-repos/storage-{repository.id}")

    def test_proxy_repository_public_host_uses_inventory_ip_before_node_ip(self):
        proxy = Node.objects.create(
            organization=self.org,
            name="proxy-backup-task-inventory-host",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="192.168.65.1",
            metadata={"inventory": {"ip_addresses": ["10.0.0.65"]}},
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="backup-task-proxy-inventory-host-repo",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            nas_protocol=Repository.NasProtocol.NFS,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={
                "server_address": "10.0.0.20",
                "share_path": "/volume1/backup",
                "kopia_password": "repo-pass",
            },
        )

        host, source = _repository_public_host(repository=repository, node=proxy)

        self.assertEqual(host, "10.0.0.65")
        self.assertEqual(source, "node.metadata.inventory.ip_addresses")

    @patch("apps.protection.services.backup_orchestrator.enqueue_repository_usage_refresh")
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_run_backup_task_allows_agent_source_with_proxy_bound_nas_repository(
        self,
        mock_run_agent_task_async,
        mock_sync_repository_usage,
    ):
        mock_sync_repository_usage.return_value = {"queued": True, "task_id": "usage-refresh"}
        proxy = Node.objects.create(
            organization=self.org,
            name="proxy-backup-task-nas-runtime",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="192.168.65.1",
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="backup-task-proxy-nas-runtime-repo",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            nas_protocol=Repository.NasProtocol.NFS,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={
                "server_address": "10.0.0.20",
                "share_path": "/volume1/backup",
                "kopia_password": "repo-pass",
                "proxy_repository_server_host": "10.0.0.65",
            },
        )
        self.config.repository_id = repository.id
        self.config.save(update_fields=["repository_id", "updated_at"])
        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Backup Agent backup config",
            trigger_type=Task.TriggerType.MANUAL,
            request_payload={
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "backup_config_id": self.config.id,
                "repository_id": repository.id,
            },
            resources=[
                {"resource_type": "backup_source", "resource_id": self.agent.id},
                {"resource_type": "repository", "resource_id": repository.id},
            ],
            steps=[
                {"step_name": "create_logic_snapshot"},
                {"step_name": "kopia_snapshot"},
                {"step_name": "finalize_snapshot"},
            ],
        )
        snapshot = create_source_snapshot(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=repository.id,
            task_id=task.id,
            task_uuid=task.task_uuid,
            idempotency_key="proxy-bound-nas-runtime",
            directory_count=1,
        )
        mock_run_agent_task_async.side_effect = self._mock_run_agent_task_async(
            [
                self._async_outcome(status=NodeTask.Status.RUNNING, kopia_snapshot_id=""),
                self._async_outcome(status=NodeTask.Status.RUNNING, kopia_snapshot_id=""),
                self._async_outcome(kopia_snapshot_id="proxy-nas-snap-1"),
                self._async_outcome(kopia_snapshot_id=""),
            ]
        )

        result = run_backup_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=snapshot.id,
        )

        self.assertEqual(result["status"], Task.Status.RUNNING)
        self.assertEqual(result["orchestrator"], "repository_server_starting")
        start_calls = [
            call.kwargs
            for call in mock_run_agent_task_async.call_args_list
            if call.kwargs.get("kind") == "repository.server.start"
        ]
        self.assertEqual(len(start_calls), 1)
        self.assertEqual(start_calls[0]["node_id"], proxy.id)
        start_payload = start_calls[0]["payload"]
        self.assertEqual(start_payload["public_host"], "10.0.0.65")
        self.assertEqual(start_payload["public_host_source"], "repository.config.proxy_repository_server_host")
        self.assertEqual(start_payload["repository"]["type"], Repository.Type.NAS)
        self.assertEqual(start_payload["repository"]["subdir"], f"hp-repos/storage-{repository.id}")
        backup_calls = [
            call.kwargs
            for call in mock_run_agent_task_async.call_args_list
            if call.kwargs.get("kind") == "backup.run"
        ]
        self.assertEqual(backup_calls, [])

        server_task = NodeTask.objects.get(kind="repository.server.start")
        server_task.status = NodeTask.Status.SUCCESS
        server_task.result = {
            "session_id": f"backup-{task.task_uuid}-repo-{repository.id}",
            "server_url": "https://10.0.0.65:51515",
            "username": f"hfl-backup-{task.id}@hfl-proxy-{proxy.id}",
            "password": "server-pass",
            "server_cert_fingerprint": "ABC123",
        }
        server_task.save(update_fields=["status", "result", "updated_at"])

        result = run_backup_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=snapshot.id,
        )

        self.assertEqual(result["status"], Task.Status.RUNNING)
        self.assertEqual(result["orchestrator"], "repository_probe_running")
        probe_calls = [
            call.kwargs
            for call in mock_run_agent_task_async.call_args_list
            if call.kwargs.get("kind") == "repo.status"
        ]
        self.assertEqual(len(probe_calls), 1)
        self.assertEqual(probe_calls[0]["node_id"], self.agent.id)
        self.assertEqual(probe_calls[0]["payload"]["repository"]["type"], "kopia_server")

        probe_task = NodeTask.objects.get(kind="repo.status")
        probe_task.status = NodeTask.Status.SUCCESS
        probe_task.result = {"repository_type": "kopia_server"}
        probe_task.save(update_fields=["status", "result", "updated_at"])

        result = run_backup_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=snapshot.id,
        )

        snapshot.refresh_from_db()
        task.refresh_from_db()
        directory = BackupSourceSnapshotDirectory.objects.get(source_snapshot=snapshot)
        self.assertEqual(result["status"], Task.Status.SUCCESS)
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.AVAILABLE)
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertEqual(directory.kopia_snapshot_id, "proxy-nas-snap-1")
        self.assertEqual(
            directory.repository_locator,
            {
                "version": 1,
                "repository_id": repository.id,
                "repository_type": Repository.Type.NAS,
                "repository_subdir": f"hp-repos/storage-{repository.id}",
                "writer_node_id": self.agent.id,
                "access_node_id": proxy.id,
            },
        )
        backup_calls = [
            call.kwargs
            for call in mock_run_agent_task_async.call_args_list
            if call.kwargs.get("kind") == "backup.run"
        ]
        self.assertEqual(len(backup_calls), 1)
        self.assertEqual(backup_calls[0]["node_id"], self.agent.id)
        repository_payload = backup_calls[0]["payload"]["repository"]
        self.assertEqual(repository_payload["type"], "kopia_server")
        self.assertEqual(repository_payload["url"], "https://10.0.0.65:51515")
        self.assertEqual(repository_payload["username"], f"hfl-backup-{task.id}@hfl-proxy-{proxy.id}")
        self.assertEqual(repository_payload["password"], "server-pass")
        self.assertEqual(repository_payload["server_cert_fingerprint"], "ABC123")
        stop_calls = [
            call.kwargs
            for call in mock_run_agent_task_async.call_args_list
            if call.kwargs.get("kind") == "repository.server.stop"
        ]
        self.assertEqual(len(stop_calls), 1)
        self.assertEqual(stop_calls[0]["node_id"], proxy.id)

    @patch("apps.protection.services.backup_orchestrator.enqueue_repository_usage_refresh")
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_run_backup_task_fails_fast_when_proxy_repository_probe_fails(
        self,
        mock_run_agent_task_async,
        mock_sync_repository_usage,
    ):
        mock_sync_repository_usage.return_value = {"queued": True, "task_id": "usage-refresh"}
        proxy = Node.objects.create(
            organization=self.org,
            name="proxy-backup-task-nas-probe-fail",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="192.168.65.1",
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="backup-task-proxy-nas-probe-fail-repo",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            nas_protocol=Repository.NasProtocol.NFS,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={
                "server_address": "10.0.0.20",
                "share_path": "/volume1/backup",
                "kopia_password": "repo-pass",
                "proxy_repository_server_host": "10.0.0.65",
            },
        )
        self.config.repository_id = repository.id
        self.config.save(update_fields=["repository_id", "updated_at"])
        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Backup Agent backup config",
            trigger_type=Task.TriggerType.MANUAL,
            request_payload={
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "backup_config_id": self.config.id,
                "repository_id": repository.id,
            },
            resources=[
                {"resource_type": "backup_source", "resource_id": self.agent.id},
                {"resource_type": "repository", "resource_id": repository.id},
            ],
            steps=[
                {"step_name": "create_logic_snapshot"},
                {"step_name": "kopia_snapshot"},
                {"step_name": "finalize_snapshot"},
            ],
        )
        snapshot = create_source_snapshot(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=repository.id,
            task_id=task.id,
            task_uuid=task.task_uuid,
            idempotency_key="proxy-bound-nas-probe-fail",
            directory_count=1,
        )
        mock_run_agent_task_async.side_effect = self._mock_run_agent_task_async(
            [
                self._async_outcome(status=NodeTask.Status.RUNNING, kopia_snapshot_id=""),
                self._async_outcome(status=NodeTask.Status.RUNNING, kopia_snapshot_id=""),
                self._async_outcome(kopia_snapshot_id=""),
            ]
        )

        result = run_backup_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=snapshot.id,
        )
        self.assertEqual(result["orchestrator"], "repository_server_starting")

        server_task = NodeTask.objects.get(kind="repository.server.start")
        server_task.status = NodeTask.Status.SUCCESS
        server_task.result = {
            "session_id": f"backup-{task.task_uuid}-repo-{repository.id}",
            "server_url": "https://10.0.0.65:51515",
            "username": f"hfl-backup-{task.id}@hfl-proxy-{proxy.id}",
            "password": "server-pass",
            "server_cert_fingerprint": "ABC123",
        }
        server_task.save(update_fields=["status", "result", "updated_at"])

        result = run_backup_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=snapshot.id,
        )
        self.assertEqual(result["orchestrator"], "repository_probe_running")

        probe_task = NodeTask.objects.get(kind="repo.status")
        probe_task.status = NodeTask.Status.FAILED
        probe_task.last_error = "exit 1: exit status 1"
        probe_task.result = {
            "repository_connect": {
                "stderr": "dial tcp 10.0.0.65:51515: connect: no route to host",
            }
        }
        probe_task.save(update_fields=["status", "last_error", "result", "updated_at"])

        result = run_backup_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=snapshot.id,
        )

        task.refresh_from_db()
        snapshot.refresh_from_db()
        self.assertEqual(result["status"], Task.Status.FAILED)
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.FAILED)
        self.assertEqual(task.error_code, "BACKUP_REPOSITORY_ACCESS_UNAVAILABLE")
        self.assertIn("no route to host", task.error_message)
        backup_calls = [
            call.kwargs
            for call in mock_run_agent_task_async.call_args_list
            if call.kwargs.get("kind") == "backup.run"
        ]
        self.assertEqual(backup_calls, [])
        stop_calls = [
            call.kwargs
            for call in mock_run_agent_task_async.call_args_list
            if call.kwargs.get("kind") == "repository.server.stop"
        ]
        self.assertEqual(len(stop_calls), 1)
        self.assertEqual(stop_calls[0]["node_id"], proxy.id)

    @patch("apps.protection.services.backup_orchestrator.enqueue_repository_usage_refresh")
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_run_backup_task_times_out_directory_and_finalizes_task(
        self,
        mock_run_agent_task_async,
        mock_sync_repository_usage,
    ):
        mock_sync_repository_usage.return_value = {"queued": True, "task_id": "usage-refresh"}
        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Backup Agent backup config",
            trigger_type=Task.TriggerType.MANUAL,
            request_payload={
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "backup_config_id": self.config.id,
                "repository_id": self.repository.id,
            },
            resources=[
                {"resource_type": "backup_source", "resource_id": self.agent.id},
                {"resource_type": "repository", "resource_id": self.repository.id},
            ],
            steps=[
                {"step_name": "create_logic_snapshot"},
                {"step_name": "kopia_snapshot"},
                {"step_name": "finalize_snapshot"},
            ],
        )
        snapshot = create_source_snapshot(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=task.id,
            task_uuid=task.task_uuid,
            idempotency_key="test-run-backup-task-timeout",
            directory_count=1,
        )
        mock_run_agent_task_async.side_effect = self._mock_run_agent_task_async(
            [
                {
                    "status": NodeTask.Status.TIMEOUT,
                    "result": {},
                    "last_error": "Agent task watchdog timed out.",
                }
            ]
        )

        result = self._run_orchestrated_backup(task=task, snapshot=snapshot)

        task.refresh_from_db()
        snapshot.refresh_from_db()
        directory = BackupSourceSnapshotDirectory.objects.get(source_snapshot=snapshot)
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertEqual(float(task.progress), BACKUP_TRANSFER_START)
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.FAILED)
        self.assertEqual(directory.status, BackupSourceSnapshotDirectory.Status.FAILED)
        self.assertEqual(directory.error_code, "WATCHDOG_STALL")
        self.assertEqual(result["source_snapshot_status"], BackupSourceSnapshot.Status.FAILED)
        kopia_step = TaskStep.objects.get(task=task, step_name="kopia_snapshot")
        self.assertEqual(kopia_step.status, TaskStep.Status.FAILED)
        self.assertEqual(float(kopia_step.progress), 0)
        finalize_step = TaskStep.objects.get(task=task, step_name="finalize_snapshot")
        self.assertEqual(finalize_step.status, TaskStep.Status.FAILED)
        self.assertEqual(float(finalize_step.progress), 0)
        self.assertTrue(
            TaskEvent.objects.filter(
                task=task,
                step__step_name="kopia_snapshot",
                message="Directory backup failed",
                metadata__error_code="WATCHDOG_STALL",
            ).exists()
        )

    @patch("apps.protection.services.backup_orchestrator.enqueue_repository_usage_refresh")
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_run_backup_task_agent_failure_finalizes_task(
        self,
        mock_run_agent_task_async,
        mock_sync_repository_usage,
    ):
        mock_sync_repository_usage.return_value = {"queued": True, "task_id": "usage-refresh"}
        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Backup Agent backup config",
            trigger_type=Task.TriggerType.MANUAL,
            request_payload={
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "backup_config_id": self.config.id,
                "repository_id": self.repository.id,
            },
            resources=[
                {"resource_type": "backup_source", "resource_id": self.agent.id},
                {"resource_type": "repository", "resource_id": self.repository.id},
            ],
            steps=[
                {"step_name": "create_logic_snapshot"},
                {"step_name": "kopia_snapshot"},
                {"step_name": "finalize_snapshot"},
            ],
        )
        snapshot = create_source_snapshot(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=task.id,
            task_uuid=task.task_uuid,
            idempotency_key="test-run-backup-task-agent-failed",
            directory_count=1,
        )
        mock_run_agent_task_async.side_effect = self._mock_run_agent_task_async(
            [
                {
                    "status": NodeTask.Status.FAILED,
                    "result": {"stderr": "kopia repository connect failed"},
                    "last_error": "agent websocket is not routable",
                }
            ]
        )

        result = self._run_orchestrated_backup(task=task, snapshot=snapshot)

        task.refresh_from_db()
        snapshot.refresh_from_db()
        directory = BackupSourceSnapshotDirectory.objects.get(source_snapshot=snapshot)
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertEqual(float(task.progress), BACKUP_TRANSFER_START)
        self.assertEqual(snapshot.status, BackupSourceSnapshot.Status.FAILED)
        self.assertEqual(directory.status, BackupSourceSnapshotDirectory.Status.FAILED)
        self.assertEqual(directory.error_code, "AGENT_BACKUP_FAILED")
        self.assertIn("kopia repository connect failed", directory.error_message)
        self.assertEqual(result["source_snapshot_status"], BackupSourceSnapshot.Status.FAILED)
        self.assertTrue(
            TaskEvent.objects.filter(
                task=task,
                step__step_name="kopia_snapshot",
                message="Directory backup failed",
                metadata__error_code="AGENT_BACKUP_FAILED",
                metadata__error_message__contains="kopia repository connect failed",
            ).exists()
        )

    @patch("apps.protection.services.backup_orchestrator.enqueue_repository_usage_refresh")
    @patch("apps.protection.services.backup_orchestrator.run_agent_task_async")
    def test_run_backup_task_partial_directory_failure_uses_success_weight(
        self,
        mock_run_agent_task_async,
        mock_sync_repository_usage,
    ):
        mock_sync_repository_usage.return_value = {"queued": True, "task_id": "usage-refresh"}
        BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data/media",
            display_name="media",
            sort_order=1,
        )
        BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data/archive",
            display_name="archive",
            sort_order=2,
        )
        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Backup Agent backup config",
            trigger_type=Task.TriggerType.MANUAL,
            request_payload={
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "backup_config_id": self.config.id,
                "repository_id": self.repository.id,
            },
            resources=[
                {"resource_type": "backup_source", "resource_id": self.agent.id},
                {"resource_type": "repository", "resource_id": self.repository.id},
            ],
            steps=[
                {"step_name": "create_logic_snapshot"},
                {"step_name": "kopia_snapshot"},
                {"step_name": "finalize_snapshot"},
            ],
        )
        snapshot = create_source_snapshot(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=task.id,
            task_uuid=task.task_uuid,
            idempotency_key="test-run-backup-task-partial-failed",
            directory_count=3,
        )
        mock_run_agent_task_async.side_effect = self._mock_run_agent_task_async(
            [
                self._async_outcome(kopia_snapshot_id="kopia-snap-success"),
                {
                    "status": NodeTask.Status.FAILED,
                    "result": {},
                    "last_error": "failed 1",
                },
                {
                    "status": NodeTask.Status.FAILED,
                    "result": {},
                    "last_error": "failed 2",
                },
            ]
        )

        result = self._run_orchestrated_backup(task=task, snapshot=snapshot)

        task.refresh_from_db()
        snapshot.refresh_from_db()
        self.assertEqual(task.status, Task.Status.FAILED)
        expected_task_progress = BACKUP_TRANSFER_START + ((BACKUP_TRANSFER_END - BACKUP_TRANSFER_START) / 3)
        self.assertAlmostEqual(float(task.progress), expected_task_progress, places=1)
        self.assertEqual(snapshot.successful_directory_count, 1)
        self.assertEqual(snapshot.failed_directory_count, 2)
        self.assertEqual(result["successful_directory_count"], 1)
        self.assertEqual(result["failed_directory_count"], 2)
        kopia_step = TaskStep.objects.get(task=task, step_name="kopia_snapshot")
        self.assertEqual(kopia_step.status, TaskStep.Status.FAILED)
        self.assertEqual(float(kopia_step.progress), 33.33)
        finalize_step = TaskStep.objects.get(task=task, step_name="finalize_snapshot")
        self.assertEqual(finalize_step.status, TaskStep.Status.FAILED)
        self.assertEqual(float(finalize_step.progress), 0)


class ProtectionBackupProgressTests(TestCase):
    def test_kopia_percent_from_agent_progress(self):
        from apps.protection.services.backup_task import _kopia_percent_from_agent_progress

        self.assertEqual(_kopia_percent_from_agent_progress({"kopia_percent": 42}), 42.0)
        self.assertEqual(_kopia_percent_from_agent_progress({"percent": "55.5"}), 55.5)
        self.assertEqual(_kopia_percent_from_agent_progress({"phase": "repository_ready"}), 8.0)
        self.assertEqual(_kopia_percent_from_agent_progress({"phase": "running"}), 12.0)

    def test_backup_progress_for_directory_maps_into_task_range(self):
        from apps.protection.services.backup_task import _backup_progress_for_directory

        step_progress, task_progress = _backup_progress_for_directory(
            directory_index=1,
            total_dirs=2,
            kopia_percent=50,
        )
        self.assertEqual(step_progress, 50.0)
        self.assertEqual(task_progress, 54.5)

        _, task_progress_last = _backup_progress_for_directory(
            directory_index=2,
            total_dirs=2,
            kopia_percent=100,
        )
        self.assertEqual(task_progress_last, 96.99)

    def test_kopia_percent_from_repository_prepare_phase(self):
        from apps.protection.services.backup_task import _kopia_percent_from_agent_progress

        self.assertEqual(
            _kopia_percent_from_agent_progress({"phase": "repository_prepare"}),
            3.0,
        )
        self.assertEqual(
            _kopia_percent_from_agent_progress({"kopia_phase": "uploading", "kopia_percent": 72}),
            72.0,
        )
