from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node.models import Node
from apps.protection.models import BackupConfig, BackupConfigDirectory
from apps.protection.services.backup_config import (
    _sync_backup_config_directories,
    update_backup_config,
)
from apps.protection.services.directory_size_estimate import (
    DirectorySizeEstimateError,
    DirectorySizeEstimateResolveError,
    _ESTIMATE_UNAVAILABLE,
    backup_config_needs_directory_estimate_refresh,
    estimate_directory_size_bytes,
    refresh_backup_config_directory_estimates_by_id,
    refresh_missing_backup_config_directory_estimates,
)
from apps.storage.repositories.models import Repository
from apps.task.models import Task
from apps.task.services.interface import create_task


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

    def _agent_outcome(self, result):
        return SimpleNamespace(
            timed_out=False,
            ok=True,
            result=result,
            task=SimpleNamespace(last_error=""),
            stream_message=None,
        )

    @patch("apps.protection.services.directory_size_estimate.run_agent_task_sync")
    def test_zero_size_is_a_valid_agent_estimate(self, run_agent_task_sync):
        run_agent_task_sync.return_value = self._agent_outcome({"size_bytes": 0})

        self.assertEqual(
            estimate_directory_size_bytes(
                node_id=self.agent.id,
                path="/empty",
                path_type="file",
                organization_id=self.org.id,
                execution_target=self._target(),
            ),
            0,
        )

    @patch("apps.protection.services.directory_size_estimate.run_agent_task_sync")
    def test_missing_size_is_not_treated_as_an_empty_path(self, run_agent_task_sync):
        run_agent_task_sync.return_value = self._agent_outcome({})

        with self.assertRaisesMessage(ValidationError, "invalid path size response"):
            estimate_directory_size_bytes(
                node_id=self.agent.id,
                path="/empty",
                organization_id=self.org.id,
                execution_target=self._target(),
            )

    @patch(
        "apps.protection.services.directory_size_estimate.estimate_directory_size_bytes",
        return_value=4096,
    )
    @patch("apps.protection.services.directory_size_estimate._resolve_execution_target")
    def test_refresh_missing_persists_estimate(self, mock_resolve, mock_estimate):
        mock_resolve.return_value = self._target()
        total = refresh_missing_backup_config_directory_estimates(
            organization_id=self.org.id,
            config=self.config,
            source_type="agent",
            source_ref_id=self.agent.id,
        )
        self.directory.refresh_from_db()
        self.assertEqual(total, 4096)
        self.assertEqual(self.directory.estimated_size_bytes, 4096)
        self.assertIsNotNone(self.directory.size_estimated_at)
        mock_estimate.assert_called_once()

    @patch(
        "apps.protection.services.directory_size_estimate.estimate_directory_size_bytes",
        return_value=0,
    )
    @patch("apps.protection.services.directory_size_estimate._resolve_execution_target")
    def test_verified_zero_size_is_not_reestimated(self, mock_resolve, mock_estimate):
        mock_resolve.return_value = self._target()

        self.assertEqual(
            refresh_missing_backup_config_directory_estimates(
                organization_id=self.org.id,
                config=self.config,
                source_type="agent",
                source_ref_id=self.agent.id,
            ),
            0,
        )
        self.directory.refresh_from_db()
        self.assertEqual(self.directory.estimated_size_bytes, 0)
        self.assertIsNotNone(self.directory.size_estimated_at)
        self.assertFalse(backup_config_needs_directory_estimate_refresh(self.config))

        self.assertEqual(
            refresh_missing_backup_config_directory_estimates(
                organization_id=self.org.id,
                config=self.config,
                source_type="agent",
                source_ref_id=self.agent.id,
            ),
            0,
        )
        self.assertEqual(mock_estimate.call_count, 1)

    @patch(
        "apps.protection.services.directory_size_estimate.estimate_directory_size_bytes",
        side_effect=DirectorySizeEstimateError("timed out", permanent=False),
    )
    @patch("apps.protection.services.directory_size_estimate._resolve_execution_target")
    def test_refresh_timeout_stays_retryable(self, mock_resolve, _mock_estimate):
        mock_resolve.return_value = self._target()
        total = refresh_missing_backup_config_directory_estimates(
            organization_id=self.org.id,
            config=self.config,
            source_type="agent",
            source_ref_id=self.agent.id,
        )
        self.directory.refresh_from_db()
        self.assertEqual(total, 0)
        self.assertEqual(self.directory.estimated_size_bytes, 0)
        self.assertIsNone(self.directory.size_estimated_at)
        self.assertTrue(backup_config_needs_directory_estimate_refresh(self.config))

    @patch(
        "apps.protection.services.directory_size_estimate.estimate_directory_size_bytes",
        side_effect=DirectorySizeEstimateError("permanent", permanent=True),
    )
    @patch("apps.protection.services.directory_size_estimate._resolve_execution_target")
    def test_refresh_permanent_failure_marks_unavailable(
        self, mock_resolve, _mock_estimate
    ):
        mock_resolve.return_value = self._target()
        total = refresh_missing_backup_config_directory_estimates(
            organization_id=self.org.id,
            config=self.config,
            source_type="agent",
            source_ref_id=self.agent.id,
        )
        self.directory.refresh_from_db()
        self.assertEqual(total, 0)
        self.assertEqual(self.directory.estimated_size_bytes, _ESTIMATE_UNAVAILABLE)
        self.assertFalse(backup_config_needs_directory_estimate_refresh(self.config))

    @patch(
        "apps.protection.services.directory_size_estimate.estimate_directory_size_bytes"
    )
    @patch("apps.protection.services.directory_size_estimate._resolve_execution_target")
    def test_refresh_skips_cached_estimates(self, mock_resolve, mock_estimate):
        self.directory.estimated_size_bytes = 2048
        self.directory.save(update_fields=["estimated_size_bytes", "updated_at"])
        mock_resolve.return_value = self._target()
        total = refresh_missing_backup_config_directory_estimates(
            organization_id=self.org.id,
            config=self.config,
            source_type="agent",
            source_ref_id=self.agent.id,
        )
        self.assertEqual(total, 2048)
        mock_estimate.assert_not_called()

    @patch(
        "apps.protection.services.directory_size_estimate.estimate_directory_size_bytes",
        return_value=2_000_000_000,
    )
    @patch("apps.protection.services.directory_size_estimate._resolve_execution_target")
    def test_forced_refresh_replaces_cached_estimate(self, mock_resolve, mock_estimate):
        self.directory.estimated_size_bytes = 12_500_000
        self.directory.size_estimated_at = timezone.now()
        self.directory.save(
            update_fields=["estimated_size_bytes", "size_estimated_at", "updated_at"]
        )
        mock_resolve.return_value = self._target()

        result = refresh_backup_config_directory_estimates_by_id(
            config_id=self.config.id,
            force_refresh=True,
        )

        self.directory.refresh_from_db()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["du_total"], 2_000_000_000)
        self.assertTrue(result["du_total_known"])
        self.assertEqual(self.directory.estimated_size_bytes, 2_000_000_000)
        mock_estimate.assert_called_once()

    @patch(
        "apps.protection.services.directory_size_estimate.estimate_directory_size_bytes"
    )
    @patch("apps.protection.services.directory_size_estimate._resolve_execution_target")
    def test_refresh_skips_unavailable_marker(self, mock_resolve, mock_estimate):
        self.directory.estimated_size_bytes = _ESTIMATE_UNAVAILABLE
        self.directory.save(update_fields=["estimated_size_bytes", "updated_at"])
        total = refresh_missing_backup_config_directory_estimates(
            organization_id=self.org.id,
            config=self.config,
            source_type="agent",
            source_ref_id=self.agent.id,
        )
        self.assertEqual(total, 0)
        mock_resolve.assert_not_called()
        mock_estimate.assert_not_called()

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

    @patch("apps.protection.services.directory_size_estimate._resolve_execution_target")
    def test_refresh_skips_resolve_when_estimates_cached(self, mock_resolve):
        self.directory.estimated_size_bytes = 2048
        self.directory.save(update_fields=["estimated_size_bytes", "updated_at"])
        self.assertFalse(backup_config_needs_directory_estimate_refresh(self.config))
        total = refresh_missing_backup_config_directory_estimates(
            organization_id=self.org.id,
            config=self.config,
            source_type="agent",
            source_ref_id=self.agent.id,
        )
        self.assertEqual(total, 2048)
        mock_resolve.assert_not_called()

    @patch(
        "apps.protection.services.directory_size_estimate."
        "refresh_missing_backup_config_directory_estimates",
        return_value=1024,
    )
    def test_by_id_requeues_when_still_pending(self, mock_refresh):
        BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data/other",
            estimated_size_bytes=0,
            sort_order=1,
        )
        self.directory.estimated_size_bytes = 1024
        self.directory.save(update_fields=["estimated_size_bytes", "updated_at"])

        result = refresh_backup_config_directory_estimates_by_id(
            config_id=self.config.id,
            attempt=1,
        )
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["should_requeue"])
        self.assertEqual(result["attempt"], 1)
        mock_refresh.assert_called_once()

    @patch(
        "apps.protection.services.directory_size_estimate."
        "refresh_missing_backup_config_directory_estimates",
        return_value=0,
    )
    def test_by_id_stops_requeue_at_max_attempts(self, _mock_refresh):
        result = refresh_backup_config_directory_estimates_by_id(
            config_id=self.config.id,
            attempt=5,
        )
        self.assertEqual(result["status"], "exhausted")
        self.assertFalse(result["should_requeue"])
        self.directory.refresh_from_db()
        self.assertEqual(self.directory.estimated_size_bytes, 0)
        self.assertTrue(backup_config_needs_directory_estimate_refresh(self.config))

    @patch(
        "apps.protection.services.directory_size_estimate."
        "refresh_missing_backup_config_directory_estimates",
        side_effect=DirectorySizeEstimateResolveError("source offline"),
    )
    def test_by_id_requeues_resolve_failures(self, _mock_refresh):
        result = refresh_backup_config_directory_estimates_by_id(
            config_id=self.config.id,
            attempt=1,
        )
        self.assertEqual(result["status"], "resolve_failed")
        self.assertTrue(result["should_requeue"])
        self.directory.refresh_from_db()
        self.assertEqual(self.directory.estimated_size_bytes, 0)

    @patch(
        "apps.protection.services.directory_size_estimate."
        "refresh_missing_backup_config_directory_estimates",
        side_effect=DirectorySizeEstimateResolveError("source offline"),
    )
    def test_by_id_freezes_pending_after_resolve_exhausted(self, _mock_refresh):
        result = refresh_backup_config_directory_estimates_by_id(
            config_id=self.config.id,
            attempt=5,
        )
        self.assertEqual(result["status"], "resolve_exhausted")
        self.assertFalse(result["should_requeue"])
        self.directory.refresh_from_db()
        self.assertEqual(self.directory.estimated_size_bytes, _ESTIMATE_UNAVAILABLE)

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
        "refresh_backup_config_directory_estimates_by_id"
    )
    def test_task_requeues_when_service_requests(self, mock_by_id):
        from apps.protection.tasks.directory_size_estimate import (
            refresh_backup_config_directory_estimates_task,
        )

        mock_by_id.return_value = {
            "config_id": self.config.id,
            "status": "partial",
            "du_total": 0,
            "attempt": 2,
            "should_requeue": True,
        }
        with patch.object(
            refresh_backup_config_directory_estimates_task,
            "apply_async",
        ) as mock_async:
            result = refresh_backup_config_directory_estimates_task.run(
                config_id=self.config.id,
                attempt=2,
            )
        self.assertTrue(result["should_requeue"])
        mock_async.assert_called_once_with(
            kwargs={
                "config_id": self.config.id,
                "attempt": 3,
                "force_refresh": False,
                "task_uuid": None,
            },
            countdown=5,
        )

    @patch(
        "apps.protection.tasks.directory_size_estimate."
        "refresh_backup_config_directory_estimates_by_id"
    )
    def test_task_freezes_refreshed_total_on_backup_task(self, mock_by_id):
        from apps.protection.tasks.directory_size_estimate import (
            refresh_backup_config_directory_estimates_task,
        )

        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Backup directory size",
            request_payload={"backup_config_id": self.config.id},
        )
        mock_by_id.return_value = {
            "config_id": self.config.id,
            "status": "ok",
            "du_total": 2_000_000_000,
            "du_total_known": True,
            "attempt": 1,
            "should_requeue": False,
        }

        refresh_backup_config_directory_estimates_task.run(
            config_id=self.config.id,
            force_refresh=True,
            task_uuid=str(task.task_uuid),
        )

        task.refresh_from_db()
        self.assertEqual(task.request_payload["du_total"], 2_000_000_000)
        self.assertTrue(task.request_payload["du_total_known"])
        self.assertEqual(task.result_payload["du_total"], 2_000_000_000)
        self.assertTrue(task.result_payload["du_total_known"])

    @patch(
        "apps.protection.tasks.directory_size_estimate."
        "refresh_backup_config_directory_estimates_by_id"
    )
    def test_task_does_not_freeze_unverified_total(self, mock_by_id):
        from apps.protection.tasks.directory_size_estimate import (
            refresh_backup_config_directory_estimates_task,
        )

        task = create_task(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Backup directory size unavailable",
            request_payload={"backup_config_id": self.config.id},
        )
        mock_by_id.return_value = {
            "config_id": self.config.id,
            "status": "ok",
            "du_total": 0,
            "du_total_known": False,
            "attempt": 1,
            "should_requeue": False,
        }

        refresh_backup_config_directory_estimates_task.run(
            config_id=self.config.id,
            force_refresh=True,
            task_uuid=str(task.task_uuid),
        )

        task.refresh_from_db()
        self.assertNotIn("du_total", task.request_payload)
        self.assertNotIn("du_total", task.result_payload or {})
