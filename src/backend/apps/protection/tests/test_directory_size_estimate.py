from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.protection.models import BackupConfig, BackupConfigDirectory
from apps.protection.services.backup_config import (
    _sync_backup_config_directories,
    update_backup_config,
)
from apps.protection.services.directory_size_estimate import (
    _ESTIMATE_UNAVAILABLE,
    backup_config_needs_directory_estimate_refresh,
    directory_size_correlation_id,
    enqueue_backup_config_directory_estimates,
    reconcile_directory_size_estimate,
)
from apps.storage.repositories.models import Repository


class DirectorySizeEstimateTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="dir-size-org",
            name="Directory Size Org",
        )
        self.agent = Node.objects.create(
            organization=self.org,
            name="dir-size-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.81",
        )
        self.agent_b = Node.objects.create(
            organization=self.org,
            name="dir-size-agent-b",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.82",
        )
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="dir-size-repo",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="dir-size-bucket",
            config={
                "endpoint": "s3.example.internal:9000",
                "region": "cn-test-1",
                "prefix": "kopia/dir-size",
                "access_key_id": "ak-test",
                "secret_access_key": "sk-test",
                "kopia_password": "repo-password",
                "use_tls": False,
            },
        )
        self.config = BackupConfig.objects.create(
            organization_id=self.org.id,
            name="Dir size config",
            source_type="agent",
            source_ref_id=self.agent.id,
            repository_id=self.repository.id,
            compression_level=BackupConfig.CompressionLevel.BALANCED,
        )
        self.directory = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/home/ubuntu",
            estimated_size_bytes=0,
            sort_order=0,
        )

    def _target(self):
        return SimpleNamespace(
            source_type="agent",
            root_path="",
            nas_payload=None,
            node=self.agent,
        )

    def test_path_type_change_invalidates_cached_estimate(self):
        self.directory.path_type = BackupConfigDirectory.PathType.DIRECTORY
        self.directory.estimated_size_bytes = 4096
        self.directory.size_estimated_at = timezone.now()
        self.directory.save(
            update_fields=[
                "path_type",
                "estimated_size_bytes",
                "size_estimated_at",
                "updated_at",
            ]
        )
        _sync_backup_config_directories(
            config=self.config,
            directories_data=[
                {
                    "path": "/home/ubuntu",
                    "path_type": BackupConfigDirectory.PathType.FILE,
                }
            ],
        )
        self.directory.refresh_from_db()
        self.assertEqual(
            self.directory.path_type,
            BackupConfigDirectory.PathType.FILE,
        )
        self.assertEqual(self.directory.estimated_size_bytes, 0)
        self.assertIsNone(self.directory.size_estimated_at)

    def test_unknown_path_type_omission_keeps_cached_estimate(self):
        self.directory.path_type = BackupConfigDirectory.PathType.DIRECTORY
        self.directory.estimated_size_bytes = 4096
        self.directory.save(
            update_fields=["path_type", "estimated_size_bytes", "updated_at"]
        )
        _sync_backup_config_directories(
            config=self.config,
            directories_data=[
                {
                    "path": "/home/ubuntu",
                    "path_type": BackupConfigDirectory.PathType.UNKNOWN,
                    "display_name": "Home",
                    "estimated_size_bytes": 0,
                }
            ],
        )
        self.directory.refresh_from_db()
        self.assertEqual(
            self.directory.path_type,
            BackupConfigDirectory.PathType.DIRECTORY,
        )
        self.assertEqual(self.directory.estimated_size_bytes, 4096)
        self.assertEqual(self.directory.display_name, "Home")

    def test_unchanged_path_keeps_cached_estimate(self):
        self.directory.path_type = BackupConfigDirectory.PathType.DIRECTORY
        self.directory.estimated_size_bytes = 4096
        self.directory.save(
            update_fields=["path_type", "estimated_size_bytes", "updated_at"]
        )
        _sync_backup_config_directories(
            config=self.config,
            directories_data=[
                {
                    "path": "/home/ubuntu",
                    "path_type": BackupConfigDirectory.PathType.DIRECTORY,
                    "display_name": "Home",
                }
            ],
        )
        self.directory.refresh_from_db()
        self.assertEqual(self.directory.estimated_size_bytes, 4096)
        self.assertEqual(self.directory.display_name, "Home")

    def test_changing_verified_positive_estimate_to_zero_invalidates_it(self):
        self.directory.path_type = BackupConfigDirectory.PathType.FILE
        self.directory.estimated_size_bytes = 1024
        self.directory.size_estimated_at = timezone.now()
        self.directory.save(
            update_fields=[
                "path_type",
                "estimated_size_bytes",
                "size_estimated_at",
                "updated_at",
            ]
        )

        _sync_backup_config_directories(
            config=self.config,
            directories_data=[
                {
                    "path": "/home/ubuntu",
                    "path_type": BackupConfigDirectory.PathType.FILE,
                    "estimated_size_bytes": 0,
                }
            ],
        )

        self.directory.refresh_from_db()
        self.assertEqual(self.directory.estimated_size_bytes, 0)
        self.assertIsNone(self.directory.size_estimated_at)

    def test_directory_sync_reopens_unavailable_estimate(self):
        self.directory.path_type = BackupConfigDirectory.PathType.DIRECTORY
        self.directory.estimated_size_bytes = _ESTIMATE_UNAVAILABLE
        self.directory.save(
            update_fields=["path_type", "estimated_size_bytes", "updated_at"]
        )
        _sync_backup_config_directories(
            config=self.config,
            directories_data=[
                {
                    "path": "/home/ubuntu",
                    "path_type": BackupConfigDirectory.PathType.DIRECTORY,
                }
            ],
        )
        self.directory.refresh_from_db()
        self.assertEqual(self.directory.estimated_size_bytes, 0)
        self.assertTrue(backup_config_needs_directory_estimate_refresh(self.config))

    def test_source_change_invalidates_cached_estimates(self):
        self.directory.estimated_size_bytes = 4096
        self.directory.size_estimated_at = timezone.now()
        self.directory.save(
            update_fields=["estimated_size_bytes", "size_estimated_at", "updated_at"]
        )
        update_backup_config(
            config=self.config,
            data={
                "name": self.config.name,
                "source_type": "agent",
                "source_ref_id": self.agent_b.id,
                "repository_id": self.repository.id,
            },
        )
        self.directory.refresh_from_db()
        self.config.refresh_from_db()
        self.assertEqual(self.config.source_ref_id, self.agent_b.id)
        self.assertEqual(self.directory.estimated_size_bytes, 0)
        self.assertIsNone(self.directory.size_estimated_at)
        self.assertTrue(backup_config_needs_directory_estimate_refresh(self.config))

    @patch(
        "apps.protection.tasks.directory_size_estimate."
        "enqueue_backup_config_directory_estimates"
    )
    def test_task_dispatches_estimates_without_waiting(self, mock_enqueue):
        from apps.protection.tasks.directory_size_estimate import (
            refresh_backup_config_directory_estimates_task,
        )

        mock_enqueue.return_value = {
            "config_id": self.config.id,
            "status": "queued",
            "queued": 1,
        }
        result = refresh_backup_config_directory_estimates_task.run(
            config_id=self.config.id,
            task_uuid="backup-task",
        )
        self.assertEqual(result["status"], "queued")
        mock_enqueue.assert_called_once_with(
            config_id=self.config.id,
            force_refresh=False,
            task_uuid="backup-task",
        )

    @patch(
        "apps.protection.tasks.directory_size_estimate."
        "enqueue_backup_config_directory_estimates"
    )
    def test_task_preserves_explicit_force_refresh(self, mock_enqueue):
        from apps.protection.tasks.directory_size_estimate import (
            refresh_backup_config_directory_estimates_task,
        )

        refresh_backup_config_directory_estimates_task.run(
            config_id=self.config.id,
            force_refresh=True,
            task_uuid="backup-task",
        )
        mock_enqueue.assert_called_once_with(
            config_id=self.config.id,
            force_refresh=True,
            task_uuid="backup-task",
        )

    @patch(
        "apps.protection.tasks.directory_size_estimate."
        "enqueue_backup_config_directory_estimates"
    )
    def test_task_does_not_wait_for_estimate_result(self, mock_enqueue):
        from apps.protection.tasks.directory_size_estimate import (
            refresh_backup_config_directory_estimates_task,
        )

        mock_enqueue.return_value = {"config_id": self.config.id, "queued": 0}

        result = refresh_backup_config_directory_estimates_task.run(
            config_id=self.config.id,
            task_uuid="backup-task",
        )
        self.assertEqual(result["queued"], 0)
        mock_enqueue.assert_called_once_with(
            config_id=self.config.id,
            force_refresh=False,
            task_uuid="backup-task",
        )

    @patch(
        "apps.protection.tasks.directory_size_estimate."
        "refresh_backup_config_directory_estimates_task.apply_async"
    )
    @patch(
        "apps.protection.tasks.directory_size_estimate."
        "enqueue_backup_config_directory_estimates"
    )
    def test_task_retries_target_resolution_with_a_finite_budget(
        self, mock_enqueue, mock_apply
    ):
        from apps.protection.tasks.directory_size_estimate import (
            refresh_backup_config_directory_estimates_task,
        )

        mock_enqueue.return_value = {
            "config_id": self.config.id,
            "status": "resolve_failed",
            "queued": 0,
        }

        refresh_backup_config_directory_estimates_task.run(
            config_id=self.config.id,
            attempt=1,
            task_uuid="backup-task",
        )

        mock_apply.assert_called_once_with(
            kwargs={
                "config_id": self.config.id,
                "attempt": 2,
                "force_refresh": False,
                "task_uuid": "backup-task",
            },
            countdown=30,
        )

        mock_apply.reset_mock()
        refresh_backup_config_directory_estimates_task.run(
            config_id=self.config.id,
            attempt=node_conf.PATH_SIZE_MAX_RETRIES,
            task_uuid="backup-task",
        )
        mock_apply.assert_not_called()

    @patch(
        "apps.protection.tasks.directory_size_estimate."
        "reconcile_directory_size_estimate_task.apply_async"
    )
    @patch("apps.protection.services.directory_size_estimate.run_agent_task_async")
    @patch("apps.protection.services.directory_size_estimate._resolve_execution_target")
    def test_enqueue_dispatches_without_sync_wait(
        self,
        mock_resolve,
        mock_dispatch,
        mock_reconcile,
    ):
        mock_resolve.return_value = self._target()
        mock_dispatch.return_value = SimpleNamespace(task_id="node-task")

        result = enqueue_backup_config_directory_estimates(
            config_id=self.config.id,
            task_uuid="backup-task",
        )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["queued"], 1)
        mock_dispatch.assert_called_once()
        mock_reconcile.assert_called_once()

    @patch(
        "apps.protection.tasks.directory_size_estimate."
        "reconcile_directory_size_estimate_task.apply_async"
    )
    @patch("apps.protection.services.directory_size_estimate.run_agent_task_async")
    @patch("apps.protection.services.directory_size_estimate._resolve_execution_target")
    def test_enqueue_respects_agent_path_size_capacity(
        self,
        mock_resolve,
        mock_dispatch,
        mock_reconcile,
    ):
        BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/home/other",
            estimated_size_bytes=0,
            sort_order=1,
        )
        mock_resolve.return_value = self._target()

        def dispatch(**kwargs):
            node_task = NodeTask.objects.create(
                organization=self.org,
                requesting_organization_id=self.org.id,
                node=self.agent,
                kind=kwargs["kind"],
                correlation_type=kwargs["correlation_type"],
                correlation_id=kwargs["correlation_id"],
                status=NodeTask.Status.PENDING,
                watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            )
            return SimpleNamespace(task_id=node_task.id)

        mock_dispatch.side_effect = dispatch

        result = enqueue_backup_config_directory_estimates(config_id=self.config.id)

        self.assertEqual(result["queued"], 1)
        mock_dispatch.assert_called_once()
        mock_reconcile.assert_called_once()

    @patch(
        "apps.protection.tasks.directory_size_estimate."
        "reconcile_directory_size_estimate_task.apply_async"
    )
    @patch(
        "apps.protection.services.directory_size_estimate.run_agent_task_async",
        side_effect=RuntimeError("route unavailable"),
    )
    @patch("apps.protection.services.directory_size_estimate._resolve_execution_target")
    def test_enqueue_does_not_fan_out_after_dispatch_failure(
        self,
        mock_resolve,
        mock_dispatch,
        mock_reconcile,
    ):
        BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/home/other",
            estimated_size_bytes=0,
            sort_order=1,
        )
        mock_resolve.return_value = self._target()

        result = enqueue_backup_config_directory_estimates(config_id=self.config.id)

        self.assertEqual(result["status"], "dispatch_failed")
        self.assertEqual(result["queued"], 0)
        mock_dispatch.assert_called_once()
        mock_reconcile.assert_not_called()

    @patch(
        "apps.protection.tasks.directory_size_estimate."
        "reconcile_directory_size_estimate_task.apply_async"
    )
    def test_reconcile_active_estimate_reschedules_short_monitor(self, mock_apply):
        correlation_id = directory_size_correlation_id(
            config=self.config,
            directory=self.directory,
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=self.agent,
            kind="path.size",
            correlation_type=node_conf.PATH_SIZE_CORRELATION_TYPE,
            correlation_id=correlation_id,
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
        )

        result = reconcile_directory_size_estimate(
            config_id=self.config.id,
            directory_id=self.directory.id,
            node_task_id=str(node_task.id),
            correlation_id=correlation_id,
        )

        self.assertEqual(result["status"], "pending")
        mock_apply.assert_called_once()

    @patch("apps.protection.services.directory_size_estimate.cancel_agent_task")
    @patch(
        "apps.protection.tasks.directory_size_estimate."
        "reconcile_directory_size_estimate_task.apply_async"
    )
    def test_reconcile_expired_estimate_tracks_cancellation_to_terminal(
        self, mock_apply, mock_cancel
    ):
        correlation_id = directory_size_correlation_id(
            config=self.config,
            directory=self.directory,
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=self.agent,
            kind="path.size",
            correlation_type=node_conf.PATH_SIZE_CORRELATION_TYPE,
            correlation_id=correlation_id,
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now() - timezone.timedelta(seconds=1),
        )

        result = reconcile_directory_size_estimate(
            config_id=self.config.id,
            directory_id=self.directory.id,
            node_task_id=str(node_task.id),
            correlation_id=correlation_id,
        )

        self.directory.refresh_from_db()
        self.assertEqual(result["status"], "canceling")
        self.assertEqual(self.directory.estimated_size_bytes, 0)
        mock_cancel.assert_called_once_with(
            task_id=str(node_task.id),
            reason="path size timeout",
        )
        mock_apply.assert_called_once()

    @patch(
        "apps.protection.services.directory_size_estimate."
        "_schedule_directory_estimate_refresh"
    )
    def test_reconcile_success_persists_matching_generation_only(self, mock_refresh):
        correlation_id = directory_size_correlation_id(
            config=self.config,
            directory=self.directory,
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=self.agent,
            kind="path.size",
            correlation_type=node_conf.PATH_SIZE_CORRELATION_TYPE,
            correlation_id=correlation_id,
            status=NodeTask.Status.SUCCESS,
            result={"size_bytes": 4096},
            watchdog_deadline_at=timezone.now(),
        )

        result = reconcile_directory_size_estimate(
            config_id=self.config.id,
            directory_id=self.directory.id,
            node_task_id=str(node_task.id),
            correlation_id=correlation_id,
        )

        self.directory.refresh_from_db()
        self.assertEqual(result, {"status": "success", "size_bytes": 4096})
        self.assertEqual(self.directory.estimated_size_bytes, 4096)
        self.assertIsNotNone(self.directory.size_estimated_at)
        mock_refresh.assert_called_once()

    @patch(
        "apps.protection.services.directory_size_estimate."
        "_schedule_directory_estimate_refresh"
    )
    def test_reconcile_agent_unavailable_stays_retryable(self, mock_refresh):
        correlation_id = directory_size_correlation_id(
            config=self.config,
            directory=self.directory,
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=self.agent,
            kind="path.size",
            correlation_type=node_conf.PATH_SIZE_CORRELATION_TYPE,
            correlation_id=correlation_id,
            status=NodeTask.Status.FAILED,
            result={"diagnostic_error_code": "AGENT_UNAVAILABLE"},
            last_error="agent websocket is not routable",
            watchdog_deadline_at=timezone.now(),
        )

        result = reconcile_directory_size_estimate(
            config_id=self.config.id,
            directory_id=self.directory.id,
            node_task_id=str(node_task.id),
            correlation_id=correlation_id,
        )

        self.directory.refresh_from_db()
        self.assertEqual(result["status"], "retryable")
        self.assertEqual(self.directory.estimated_size_bytes, 0)
        self.assertIsNone(self.directory.size_estimated_at)
        mock_refresh.assert_called_once_with(
            config_id=self.config.id,
            task_uuid=None,
            countdown=30,
        )

    @patch(
        "apps.protection.services.directory_size_estimate."
        "_schedule_directory_estimate_refresh"
    )
    @patch(
        "apps.protection.services.directory_size_estimate."
        "_path_size_retry_exhausted",
        return_value=True,
    )
    def test_reconcile_retry_exhaustion_marks_estimate_unavailable(
        self, _retry_exhausted, mock_refresh
    ):
        correlation_id = directory_size_correlation_id(
            config=self.config,
            directory=self.directory,
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=self.agent,
            kind="path.size",
            correlation_type=node_conf.PATH_SIZE_CORRELATION_TYPE,
            correlation_id=correlation_id,
            status=NodeTask.Status.FAILED,
            result={"diagnostic_error_code": "AGENT_UNAVAILABLE"},
            last_error="agent websocket is not routable",
            watchdog_deadline_at=timezone.now(),
        )

        result = reconcile_directory_size_estimate(
            config_id=self.config.id,
            directory_id=self.directory.id,
            node_task_id=str(node_task.id),
            correlation_id=correlation_id,
        )

        self.directory.refresh_from_db()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(self.directory.estimated_size_bytes, _ESTIMATE_UNAVAILABLE)
        mock_refresh.assert_called_once_with(config_id=self.config.id, task_uuid=None)

    def test_reconcile_does_not_apply_stale_generation(self):
        correlation_id = directory_size_correlation_id(
            config=self.config,
            directory=self.directory,
        )
        node_task = NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=self.agent,
            kind="path.size",
            correlation_type=node_conf.PATH_SIZE_CORRELATION_TYPE,
            correlation_id=correlation_id,
            status=NodeTask.Status.SUCCESS,
            result={"size_bytes": 4096},
            watchdog_deadline_at=timezone.now(),
        )
        self.directory.path = "/home/changed"
        self.directory.save(update_fields=["path", "updated_at"])

        result = reconcile_directory_size_estimate(
            config_id=self.config.id,
            directory_id=self.directory.id,
            node_task_id=str(node_task.id),
            correlation_id=correlation_id,
        )

        self.directory.refresh_from_db()
        self.assertEqual(result["status"], "stale")
        self.assertEqual(self.directory.estimated_size_bytes, 0)
