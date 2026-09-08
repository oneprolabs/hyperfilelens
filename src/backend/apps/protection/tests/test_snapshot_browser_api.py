from __future__ import annotations

import base64
import hashlib
import tempfile
import zipfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.protection.models import (
    BackupConfig,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
    SnapshotDownloadArtifact,
)
from apps.protection.services.backup_source_snapshot import create_source_snapshot
from apps.protection.services.snapshot_browser import (
    SnapshotArtifactUploadUnsupported,
    SnapshotFileDownload,
)
from apps.protection.services.snapshot_download import (
    _create_pending_artifact,
    cleanup_expired_snapshot_download_artifacts,
    prepare_snapshot_artifact_upload,
    run_snapshot_download_task,
)
from apps.storage.repositories.models import Repository
from apps.task.models import Task, TaskEvent, TaskResource, TaskStep


class SnapshotBrowserApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="snapshot-browser@test.local",
            email="snapshot-browser@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(
            key="snapshot-browser-org", name="Snapshot Browser Org"
        )
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.agent = Node.objects.create(
            organization=self.org,
            name="snapshot-browser-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="snapshot-browser-repo",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_bucket="snapshot-browser-bucket",
            config={
                "endpoint": "s3.example.internal:9000",
                "access_key_id": "ak",
                "secret_access_key": "sk",
                "kopia_password": "123456",
                "use_tls": False,
            },
        )
        self.config = BackupConfig.objects.create(
            organization_id=self.org.id,
            name="Snapshot browser config",
            source_type="agent",
            source_ref_id=self.agent.id,
            repository_id=self.repository.id,
        )
        self.task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Backup Snapshot browser config",
        )
        self.snapshot = create_source_snapshot(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=self.task.id,
            task_uuid=self.task.task_uuid,
            idempotency_key="snapshot-browser-test",
            status=BackupSourceSnapshot.Status.AVAILABLE,
            directory_count=1,
        )
        self.directory = BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=self.snapshot,
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=123,
            source_path="/data/projects",
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-snapshot-1",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        self.client.force_authenticate(user=self.user)

    def _headers(self):
        return {"HTTP_X_ORG_KEY": self.org.key}

    @override_settings(PROTECTION_SNAPSHOT_DOWNLOAD_MAX_LOGICAL_BYTES=321 * 1024 * 1024)
    def test_snapshot_detail_includes_effective_download_limits(self):
        response = self.client.get(
            f"/api/v1/protection/backup-source-snapshots/{self.snapshot.id}/",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(
            response.data["download_limits"],
            {
                "max_selected_items": 100,
                "max_logical_size_bytes": 321 * 1024 * 1024,
            },
        )

    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    def test_browse_snapshot_directory(self, mock_run_agent_task_sync):
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            result={
                "entries": [
                    {
                        "name": "docs",
                        "path": "docs",
                        "type": "dir",
                        "is_dir": True,
                        "size_bytes": 0,
                    },
                    {
                        "name": "readme.txt",
                        "path": "readme.txt",
                        "type": "file",
                        "size_bytes": 12,
                    },
                ]
            },
            task=SimpleNamespace(last_error=""),
        )

        response = self.client.get(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/browse/",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["directory_id"], self.directory.id)
        self.assertEqual(response.data["entries"][0]["type"], "dir")
        self.assertEqual(response.data["entries"][0]["downloadable"], True)
        self.assertEqual(response.data["entries"][1]["downloadable"], True)
        mock_run_agent_task_sync.assert_called_once()
        payload = mock_run_agent_task_sync.call_args.kwargs["payload"]
        self.assertEqual(payload["snapshot_id"], "kopia-snapshot-1")

    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    def test_browse_snapshot_directory_forwards_pagination(
        self, mock_run_agent_task_sync
    ):
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            result={
                "entries": [{"name": "part-0200.dat", "type": "file", "size": 12}],
                "has_more": True,
                "next_cursor": "250",
            },
            task=SimpleNamespace(last_error=""),
        )

        response = self.client.get(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/browse/"
            "?path=archive&limit=50&cursor=200",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["next_cursor"], "250")
        self.assertIs(response.data["has_more"], True)
        payload = mock_run_agent_task_sync.call_args.kwargs["payload"]
        self.assertEqual(payload["path"], "archive")
        self.assertEqual(payload["limit"], 50)
        self.assertEqual(payload["cursor"], "200")

    def test_browse_snapshot_directory_rejects_invalid_cursor(self):
        response = self.client.get(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/browse/"
            "?cursor=next",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertIn(b"non-negative integer", response.content)

    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    def test_browse_snapshot_directory_pages_legacy_agent_results(
        self, mock_run_agent_task_sync
    ):
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            result={
                "entries": [
                    {"name": f"part-{index:04d}.dat", "type": "file", "size": index}
                    for index in range(5)
                ],
            },
            task=SimpleNamespace(last_error=""),
        )

        first = self.client.get(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/browse/"
            "?limit=2",
            **self._headers(),
        )
        second = self.client.get(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/browse/"
            f"?limit=2&cursor={first.data['next_cursor']}",
            **self._headers(),
        )

        self.assertEqual(
            [row["name"] for row in first.data["entries"]],
            ["part-0000.dat", "part-0001.dat"],
        )
        self.assertEqual(first.data["next_cursor"], "2")
        self.assertIs(first.data["has_more"], True)
        self.assertEqual(
            [row["name"] for row in second.data["entries"]],
            ["part-0002.dat", "part-0003.dat"],
        )
        self.assertEqual(second.data["next_cursor"], "4")
        self.assertIs(second.data["has_more"], True)

    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    def test_browse_proxy_bound_nas_snapshot_directory_uses_bound_proxy(
        self, mock_run_agent_task_sync
    ):
        proxy = Node.objects.create(
            organization=self.org,
            name="snapshot-browser-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.agent.availability = Node.Availability.OFFLINE
        self.agent.save(update_fields=["availability"])
        self.repository.repo_type = Repository.Type.NAS
        self.repository.nas_protocol = Repository.NasProtocol.NFS
        self.repository.s3_bucket = ""
        self.repository.bind_node_type = Repository.BindNodeType.PROXY
        self.repository.bind_node_id = proxy.id
        self.repository.config = {
            "server_address": "10.0.0.20",
            "share_path": "/volume1/backup",
            "kopia_password": "repo-pass",
        }
        self.repository.save(
            update_fields=[
                "repo_type",
                "nas_protocol",
                "s3_bucket",
                "bind_node_type",
                "bind_node_id",
                "config",
            ]
        )
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            result={"entries": []},
            task=SimpleNamespace(last_error=""),
        )

        response = self.client.get(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/browse/",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        mock_run_agent_task_sync.assert_called_once()
        self.assertEqual(mock_run_agent_task_sync.call_args.kwargs["node_id"], proxy.id)
        payload = mock_run_agent_task_sync.call_args.kwargs["payload"]
        self.assertEqual(payload["repository"]["type"], Repository.Type.NAS)
        self.assertEqual(
            payload["repository"]["subdir"], f"hp-repos/storage-{self.repository.id}"
        )

    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    def test_browse_proxy_filesystem_snapshot_uses_original_proxy(
        self,
        mock_run_agent_task_sync,
    ):
        original_proxy = Node.objects.create(
            organization=self.org,
            name="snapshot-browser-original-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        replacement_proxy = Node.objects.create(
            organization=self.org,
            name="snapshot-browser-replacement-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.repository.repo_type = Repository.Type.PROXY_FS
        self.repository.s3_bucket = ""
        self.repository.bind_node_type = Repository.BindNodeType.PROXY
        self.repository.bind_node_id = replacement_proxy.id
        self.repository.config = {
            "proxy_node_dir": "/srv/hfl-repository",
            "kopia_password": "repo-pass",
        }
        self.repository.save()
        self.directory.repository_locator = {
            "version": 1,
            "repository_id": self.repository.id,
            "repository_type": Repository.Type.PROXY_FS,
            "repository_subdir": "",
            "writer_node_id": self.agent.id,
            "access_node_id": original_proxy.id,
        }
        self.directory.save(update_fields=["repository_locator", "updated_at"])
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            result={"entries": []},
            task=SimpleNamespace(last_error=""),
        )

        response = self.client.get(
            f"/api/v1/protection/backup-source-snapshot-directories/"
            f"{self.directory.id}/browse/",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(
            mock_run_agent_task_sync.call_args.kwargs["node_id"],
            original_proxy.id,
        )

    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    def test_browse_snapshot_directory_normalizes_kopia_directory_type(
        self, mock_run_agent_task_sync
    ):
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            result={
                "entries": [
                    {
                        "name": "images",
                        "type": "d",
                        "mode": "drwxr-xr-x",
                        "size": 0,
                    },
                    {
                        "name": "logo.png",
                        "type": "f",
                        "mode": "-rw-r--r--",
                        "size": 42,
                    },
                ]
            },
            task=SimpleNamespace(last_error=""),
        )

        response = self.client.get(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/browse/?path=docs",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["path"], "docs")
        self.assertEqual(response.data["parent_path"], "")
        self.assertEqual(response.data["entries"][0]["type"], "dir")
        self.assertEqual(response.data["entries"][0]["path"], "docs/images")
        self.assertEqual(response.data["entries"][0]["downloadable"], True)
        self.assertEqual(response.data["entries"][1]["type"], "file")
        self.assertEqual(response.data["entries"][1]["path"], "docs/logo.png")

    def test_browse_rejects_path_traversal(self):
        response = self.client.get(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/browse/?path=../secret",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_403_FORBIDDEN, response.content
        )

    def test_browse_rejects_unavailable_directory(self):
        self.directory.status = BackupSourceSnapshotDirectory.Status.FAILED
        self.directory.kopia_snapshot_id = None
        self.directory.error_message = "failed"
        self.directory.save(
            update_fields=["status", "kopia_snapshot_id", "error_message"]
        )

        response = self.client.get(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/browse/",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_403_FORBIDDEN, response.content
        )

    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    def test_download_snapshot_file(self, mock_run_agent_task_sync):
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            result={
                "filename": "readme.txt",
                "content_base64": base64.b64encode(b"hello snapshot").decode("ascii"),
            },
            task=SimpleNamespace(last_error=""),
        )

        response = self.client.get(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/download/?path=readme.txt",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.content, b"hello snapshot")
        self.assertIn("readme.txt", response["Content-Disposition"])
        kwargs = mock_run_agent_task_sync.call_args.kwargs
        self.assertIn("repository", kwargs["payload"])
        self.assertNotIn("repository", kwargs["persisted_payload"])

    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    def test_download_empty_snapshot_file(self, mock_run_agent_task_sync):
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            result={
                "filename": "__init__.py",
                "content_base64": "",
                "size_bytes": 0,
            },
            task=SimpleNamespace(last_error=""),
        )

        response = self.client.get(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/download/?path=pkg/__init__.py",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.content, b"")
        self.assertIn("__init__.py", response["Content-Disposition"])

    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    def test_download_reports_legacy_agent_result_limit(self, mock_run_agent_task_sync):
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            result={
                "filename": "large.bin",
                "size_bytes": 1024 * 1024,
                "result_truncated": True,
            },
            task=SimpleNamespace(last_error=""),
        )

        response = self.client.get(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/download/?path=large.bin",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertIn(b"Upgrade the Agent", response.content)

    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    def test_download_root_file_snapshot(self, mock_run_agent_task_sync):
        self.directory.path_type = BackupSourceSnapshotDirectory.PathType.FILE
        self.directory.source_path = "/data/report.txt"
        self.directory.kopia_snapshot_id = "kopia-file-snapshot-1"
        self.directory.save(
            update_fields=["path_type", "source_path", "kopia_snapshot_id"]
        )
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            result={
                "filename": "download",
                "content_base64": base64.b64encode(b"file content").decode("ascii"),
            },
            task=SimpleNamespace(last_error=""),
        )

        response = self.client.get(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/download/",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.content, b"file content")
        self.assertIn("report.txt", response["Content-Disposition"])
        payload = mock_run_agent_task_sync.call_args.kwargs["payload"]
        self.assertEqual(payload["snapshot_id"], "kopia-file-snapshot-1")
        self.assertEqual(payload["path"], "")

    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    def test_create_snapshot_download_task(self, mock_queue):
        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/download-tasks/",
            {"path": "readme.txt"},
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.content
        )
        self.assertEqual(response.data["task_type"], "snapshot_download")
        self.assertEqual(response.data["trigger_type"], Task.TriggerType.MANUAL)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(
            response.data["request_payload"]["source_snapshot_directory_id"],
            self.directory.id,
        )
        self.assertEqual(response.data["request_payload"]["path"], "readme.txt")
        task = Task.objects.get(task_uuid=response.data["task_uuid"])
        source_resource = task.resources.get(
            resource_type=TaskResource.Type.BACKUP_SOURCE
        )
        self.assertEqual(source_resource.resource_subtype, "agent")
        self.assertEqual(source_resource.resource_id, self.agent.id)
        self.assertEqual(
            list(task.steps.order_by("step_index").values_list("step_name", flat=True)),
            [
                "snapshot_download_restore",
                "snapshot_download_transfer",
                "snapshot_download_finalize",
            ],
        )
        mock_queue.assert_called_once()

    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    def test_create_root_file_snapshot_download_task(self, mock_queue):
        self.directory.path_type = BackupSourceSnapshotDirectory.PathType.FILE
        self.directory.source_path = "/data/report.txt"
        self.directory.save(update_fields=["path_type", "source_path"])

        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/download-tasks/",
            {"path": ""},
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.content
        )
        self.assertEqual(response.data["request_payload"]["path"], "")
        self.assertIn("report.txt", response.data["display_name"])
        mock_queue.assert_called_once()

    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    def test_create_snapshot_batch_download_task(self, mock_queue):
        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/batch-download-tasks/",
            {"paths": ["docs", "readme.txt", "readme.txt"]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.content
        )
        self.assertEqual(response.data["task_type"], "snapshot_download")
        self.assertEqual(response.data["trigger_type"], Task.TriggerType.MANUAL)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(
            response.data["request_payload"]["source_snapshot_directory_id"],
            self.directory.id,
        )
        self.assertEqual(
            response.data["request_payload"]["paths"], ["docs", "readme.txt"]
        )
        task = Task.objects.get(task_uuid=response.data["task_uuid"])
        source_resource = task.resources.get(
            resource_type=TaskResource.Type.BACKUP_SOURCE
        )
        self.assertEqual(source_resource.resource_subtype, "agent")
        self.assertEqual(source_resource.resource_id, self.agent.id)
        mock_queue.assert_called_once()

    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    def test_create_snapshot_batch_download_task_rejects_parent_child_conflict(
        self, mock_queue
    ):
        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/batch-download-tasks/",
            {"paths": ["docs", "docs/readme.txt"]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        mock_queue.assert_not_called()

    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    def test_create_snapshot_group_download_task_across_source_paths(
        self, mock_run_agent_task_sync, mock_queue
    ):
        self.agent.metadata = {
            "inventory": {"capabilities": ["snapshot_multi_download_v1"]}
        }
        self.agent.save(update_fields=["metadata"])
        second_directory = BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=self.snapshot,
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=124,
            source_path="/var/log/app",
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-snapshot-2",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            result={"logical_size_bytes": 4096, "selected_count": 3},
            task=SimpleNamespace(last_error=""),
        )

        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshots/{self.snapshot.id}/download-tasks/",
            {
                "groups": [
                    {
                        "directory_id": self.directory.id,
                        "paths": ["docs", "readme.txt"],
                    },
                    {"directory_id": second_directory.id, "paths": ["app.log"]},
                ]
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.content
        )
        payload = response.data["request_payload"]
        self.assertEqual(payload["selected_count"], 3)
        self.assertEqual(payload["logical_size_bytes"], 4096)
        self.assertEqual(
            [
                (group["directory_id"], group["snapshot_id"], group["paths"])
                for group in payload["groups"]
            ],
            [
                (self.directory.id, "kopia-snapshot-1", ["docs", "readme.txt"]),
                (second_directory.id, "kopia-snapshot-2", ["app.log"]),
            ],
        )
        artifact = SnapshotDownloadArtifact.objects.get(
            task__task_uuid=response.data["task_uuid"]
        )
        self.assertEqual(artifact.filename, "snapshot-download.zip")
        mock_run_agent_task_sync.assert_called_once()
        agent_call = mock_run_agent_task_sync.call_args.kwargs
        self.assertEqual(agent_call["kind"], "snapshot.download.plan")
        self.assertEqual(agent_call["payload"]["groups"], payload["groups"])
        self.assertEqual(agent_call["persisted_payload"]["groups"], payload["groups"])
        mock_queue.assert_called_once()

    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    @patch("apps.protection.services.snapshot_download.plan_snapshot_download_groups")
    def test_create_snapshot_group_download_rejects_more_than_100_items(
        self, mock_plan, mock_queue
    ):
        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshots/{self.snapshot.id}/download-tasks/",
            {
                "groups": [
                    {
                        "directory_id": self.directory.id,
                        "paths": [f"item-{index}.dat" for index in range(101)],
                    }
                ]
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertIn(b"At most 100 items", response.content)
        mock_plan.assert_not_called()
        mock_queue.assert_not_called()

    @override_settings(PROTECTION_SNAPSHOT_DOWNLOAD_MAX_LOGICAL_BYTES=200 * 1024 * 1024)
    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    @patch("apps.protection.services.snapshot_download.plan_snapshot_download_groups")
    def test_create_snapshot_group_download_rejects_logical_size_limit(
        self, mock_plan, mock_queue
    ):
        mock_plan.return_value = {
            "logical_size_bytes": 200 * 1024 * 1024 + 1,
            "selected_count": 1,
        }

        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshots/{self.snapshot.id}/download-tasks/",
            {"groups": [{"directory_id": self.directory.id, "paths": ["large.bin"]}]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertEqual(
            response.data["data"]["code"],
            "PROTECTION.SNAPSHOT_DOWNLOAD_SIZE_LIMIT_EXCEEDED",
        )
        self.assertEqual(
            response.data["data"]["meta"]["selected_size_bytes"],
            200 * 1024 * 1024 + 1,
        )
        self.assertEqual(
            response.data["data"]["meta"]["max_size_bytes"],
            200 * 1024 * 1024,
        )
        self.assertFalse(
            Task.objects.filter(task_type=Task.Type.SNAPSHOT_DOWNLOAD).exists()
        )
        mock_plan.assert_called_once()
        mock_queue.assert_not_called()

    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    @patch("apps.protection.services.snapshot_download.plan_snapshot_download_groups")
    def test_create_snapshot_group_download_rejects_another_logical_snapshot(
        self, mock_plan, mock_queue
    ):
        other_snapshot = create_source_snapshot(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=self.task.id,
            task_uuid=self.task.task_uuid,
            idempotency_key="snapshot-browser-other-test",
            status=BackupSourceSnapshot.Status.AVAILABLE,
            directory_count=1,
        )
        other_directory = BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=other_snapshot,
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=125,
            source_path="/other",
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-snapshot-other",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )

        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshots/{self.snapshot.id}/download-tasks/",
            {"groups": [{"directory_id": other_directory.id, "paths": ["file.txt"]}]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertIn(b"selected snapshot point", response.content)
        mock_plan.assert_not_called()
        mock_queue.assert_not_called()

    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    def test_create_snapshot_group_download_uses_legacy_mode_for_one_source_path(
        self,
        mock_queue,
        mock_run_agent_task_sync,
    ):
        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshots/{self.snapshot.id}/download-tasks/",
            {
                "groups": [
                    {
                        "directory_id": self.directory.id,
                        "paths": ["docs", "readme.txt"],
                    }
                ]
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.content
        )
        self.assertEqual(
            response.data["request_payload"]["paths"], ["docs", "readme.txt"]
        )
        self.assertIs(
            response.data["request_payload"]["package_as_snapshot_archive"], True
        )
        self.assertNotIn("groups", response.data["request_payload"])
        artifact = SnapshotDownloadArtifact.objects.get(
            task__task_uuid=response.data["task_uuid"]
        )
        self.assertEqual(artifact.filename, "snapshot-download.zip")
        mock_run_agent_task_sync.assert_not_called()
        mock_queue.assert_called_once()

    @override_settings(PROTECTION_SNAPSHOT_DOWNLOAD_MAX_LOGICAL_BYTES=200 * 1024 * 1024)
    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    def test_create_snapshot_group_download_uses_legacy_mode_for_directory_root(
        self,
        mock_queue,
        mock_run_agent_task_sync,
    ):
        self.directory.path_type = BackupSourceSnapshotDirectory.PathType.DIRECTORY
        self.directory.size_bytes = 128 * 1024 * 1024
        self.directory.save(update_fields=["path_type", "size_bytes"])

        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshots/{self.snapshot.id}/download-tasks/",
            {"groups": [{"directory_id": self.directory.id, "paths": [""]}]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.content
        )
        self.assertEqual(response.data["request_payload"]["path"], "")
        self.assertIs(
            response.data["request_payload"]["package_as_snapshot_archive"], True
        )
        self.assertNotIn("groups", response.data["request_payload"])
        mock_run_agent_task_sync.assert_not_called()
        mock_queue.assert_called_once()

    @patch("apps.protection.services.snapshot_download.download_snapshot_file")
    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    def test_legacy_directory_root_download_keeps_snapshot_zip_contract(
        self,
        mock_queue,
        mock_download_snapshot_file,
    ):
        self.directory.path_type = BackupSourceSnapshotDirectory.PathType.DIRECTORY
        self.directory.size_bytes = 1024
        self.directory.save(update_fields=["path_type", "size_bytes"])
        nested = BytesIO()
        with zipfile.ZipFile(nested, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("docs/readme.txt", b"directory root")
        mock_download_snapshot_file.return_value = SnapshotFileDownload(
            filename="snapshot.zip",
            content=nested.getvalue(),
            content_type="application/zip",
        )

        with (
            tempfile.TemporaryDirectory() as media_root,
            override_settings(MEDIA_ROOT=media_root),
        ):
            response = self.client.post(
                f"/api/v1/protection/backup-source-snapshots/{self.snapshot.id}/download-tasks/",
                {"groups": [{"directory_id": self.directory.id, "paths": [""]}]},
                format="json",
                **self._headers(),
            )
            self.assertEqual(
                response.status_code, status.HTTP_201_CREATED, response.content
            )
            task = Task.objects.get(task_uuid=response.data["task_uuid"])

            result = run_snapshot_download_task(task=task)

            self.assertEqual(result["filename"], "snapshot-download.zip")
            call = mock_download_snapshot_file.call_args.kwargs
            self.assertEqual(call["path"], "")
            self.assertIs(call["allow_directory_root"], True)
            artifact = task.snapshot_download_artifact
            artifact.refresh_from_db()
            with zipfile.ZipFile(artifact.storage_path, "r") as archive:
                self.assertEqual(
                    archive.read("snapshot-download/docs/readme.txt"),
                    b"directory root",
                )
        mock_queue.assert_called_once()

    @override_settings(PROTECTION_SNAPSHOT_DOWNLOAD_MAX_LOGICAL_BYTES=200 * 1024 * 1024)
    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    def test_create_snapshot_group_download_rejects_oversized_directory_root(
        self,
        mock_queue,
        mock_run_agent_task_sync,
    ):
        self.directory.path_type = BackupSourceSnapshotDirectory.PathType.DIRECTORY
        self.directory.size_bytes = 200 * 1024 * 1024 + 1
        self.directory.save(update_fields=["path_type", "size_bytes"])

        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshots/{self.snapshot.id}/download-tasks/",
            {"groups": [{"directory_id": self.directory.id, "paths": [""]}]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertEqual(
            response.data["data"]["code"],
            "PROTECTION.SNAPSHOT_DOWNLOAD_SIZE_LIMIT_EXCEEDED",
        )
        self.assertEqual(
            response.data["data"]["meta"]["selected_size_bytes"],
            200 * 1024 * 1024 + 1,
        )
        mock_run_agent_task_sync.assert_not_called()
        mock_queue.assert_not_called()

    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    def test_cross_source_path_root_download_requires_new_capability(
        self,
        mock_queue,
        mock_run_agent_task_sync,
    ):
        self.agent.metadata = {
            "inventory": {"capabilities": ["snapshot_multi_download_v1"]}
        }
        self.agent.save(update_fields=["metadata"])
        self.directory.path_type = BackupSourceSnapshotDirectory.PathType.DIRECTORY
        self.directory.save(update_fields=["path_type"])
        second_directory = BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=self.snapshot,
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=127,
            source_path="/var/log/app",
            path_type=BackupSourceSnapshotDirectory.PathType.DIRECTORY,
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-snapshot-2",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )

        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshots/{self.snapshot.id}/download-tasks/",
            {
                "groups": [
                    {"directory_id": self.directory.id, "paths": [""]},
                    {"directory_id": second_directory.id, "paths": ["app.log"]},
                ]
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_409_CONFLICT, response.content
        )
        self.assertEqual(
            response.data["data"]["code"],
            "PROTECTION.SNAPSHOT_MULTI_DOWNLOAD_UPGRADE_REQUIRED",
        )
        self.assertIn(b"backup source", response.content)
        self.assertNotIn(b"Agent", response.content)
        mock_run_agent_task_sync.assert_not_called()
        mock_queue.assert_not_called()

    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    def test_cross_source_path_root_download_uses_new_capability(
        self,
        mock_queue,
        mock_run_agent_task_sync,
    ):
        self.agent.metadata = {
            "inventory": {"capabilities": ["snapshot_source_path_download_v1"]}
        }
        self.agent.save(update_fields=["metadata"])
        self.directory.path_type = BackupSourceSnapshotDirectory.PathType.DIRECTORY
        self.directory.size_bytes = 1024
        self.directory.save(update_fields=["path_type", "size_bytes"])
        second_directory = BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=self.snapshot,
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=128,
            source_path="/var/log/app",
            path_type=BackupSourceSnapshotDirectory.PathType.DIRECTORY,
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-snapshot-2",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
            size_bytes=2048,
        )
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            ok=True,
            timed_out=False,
            result={"logical_size_bytes": 3072, "selected_count": 2},
            task=SimpleNamespace(last_error=""),
        )

        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshots/{self.snapshot.id}/download-tasks/",
            {
                "groups": [
                    {"directory_id": self.directory.id, "paths": [""]},
                    {"directory_id": second_directory.id, "paths": [""]},
                ]
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.content
        )
        self.assertEqual(
            [group["paths"] for group in response.data["request_payload"]["groups"]],
            [[""], [""]],
        )
        mock_run_agent_task_sync.assert_called_once()
        mock_queue.assert_called_once()

    @patch("apps.protection.services.snapshot_download.download_snapshot_file")
    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    def test_legacy_root_file_download_keeps_snapshot_zip_contract(
        self,
        mock_queue,
        mock_download_snapshot_file,
    ):
        self.directory.path_type = BackupSourceSnapshotDirectory.PathType.FILE
        self.directory.source_path = "/root/auto_install.sh"
        self.directory.save(update_fields=["path_type", "source_path"])
        content = b"#!/bin/sh\necho ready\n"
        uploaded_paths: list[Path] = []

        def simulate_legacy_artifact_upload(**kwargs):
            artifact = kwargs["upload_artifact"]
            uploaded_path = Path(artifact.storage_path).with_name("download")
            uploaded_path.parent.mkdir(parents=True, exist_ok=True)
            uploaded_path.write_bytes(content)
            artifact.filename = "download"
            artifact.content_type = "application/octet-stream"
            artifact.size_bytes = len(content)
            artifact.sha256 = hashlib.sha256(content).hexdigest()
            artifact.storage_path = str(uploaded_path)
            artifact.status = SnapshotDownloadArtifact.Status.READY
            artifact.save()
            uploaded_paths.append(uploaded_path)
            return SnapshotFileDownload(
                filename="download",
                content=b"",
                content_type="application/octet-stream",
                artifact_id=artifact.id,
            )

        mock_download_snapshot_file.side_effect = simulate_legacy_artifact_upload

        with (
            tempfile.TemporaryDirectory() as media_root,
            override_settings(MEDIA_ROOT=media_root),
        ):
            response = self.client.post(
                f"/api/v1/protection/backup-source-snapshots/{self.snapshot.id}/download-tasks/",
                {"groups": [{"directory_id": self.directory.id, "paths": [""]}]},
                format="json",
                **self._headers(),
            )

            self.assertEqual(
                response.status_code, status.HTTP_201_CREATED, response.content
            )
            task = Task.objects.get(task_uuid=response.data["task_uuid"])
            self.assertEqual(task.request_payload["path"], "")
            self.assertIs(task.request_payload["package_as_snapshot_archive"], True)
            result = run_snapshot_download_task(task=task)

            self.assertEqual(result["filename"], "snapshot-download.zip")
            artifact = task.snapshot_download_artifact
            artifact.refresh_from_db()
            self.assertEqual(artifact.filename, "snapshot-download.zip")
            self.assertEqual(artifact.content_type, "application/zip")
            self.assertEqual(Path(artifact.storage_path).name, "snapshot-download.zip")
            with zipfile.ZipFile(artifact.storage_path, "r") as archive:
                self.assertEqual(
                    archive.read("snapshot-download/auto_install.sh"),
                    content,
                )
            self.assertFalse(uploaded_paths[0].exists())
        mock_queue.assert_called_once()

    @patch("apps.protection.services.snapshot_browser.run_agent_task_sync")
    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    def test_create_snapshot_group_download_requires_upgrade_only_across_source_paths(
        self,
        mock_queue,
        mock_run_agent_task_sync,
    ):
        second_directory = BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=self.snapshot,
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=126,
            source_path="/var/log/app",
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-snapshot-2",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )

        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshots/{self.snapshot.id}/download-tasks/",
            {
                "groups": [
                    {"directory_id": self.directory.id, "paths": ["readme.txt"]},
                    {"directory_id": second_directory.id, "paths": ["app.log"]},
                ]
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_409_CONFLICT, response.content
        )
        self.assertEqual(
            response.data["data"]["code"],
            "PROTECTION.SNAPSHOT_MULTI_DOWNLOAD_UPGRADE_REQUIRED",
        )
        self.assertIn(b"single Source Path", response.content)
        self.assertIn(b"backup source", response.content)
        self.assertNotIn(b"Agent", response.content)
        mock_run_agent_task_sync.assert_not_called()
        mock_queue.assert_not_called()

    @patch("apps.protection.services.snapshot_download.download_snapshot_file")
    def test_run_snapshot_batch_download_task_persists_zip_artifact(
        self, mock_download_snapshot_file
    ):
        nested = BytesIO()
        with zipfile.ZipFile(nested, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("guide.txt", b"guide")
        mock_download_snapshot_file.side_effect = [
            SnapshotArtifactUploadUnsupported("legacy Agent"),
            SnapshotFileDownload(
                filename="docs.zip",
                content=nested.getvalue(),
                content_type="application/zip",
            ),
            SnapshotFileDownload(
                filename="readme.txt",
                content=b"hello",
                content_type="application/octet-stream",
            ),
        ]
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.SNAPSHOT_DOWNLOAD,
            display_name="Batch snapshot download",
            request_payload={
                "source_snapshot_directory_id": self.directory.id,
                "paths": ["docs", "readme.txt"],
            },
        )
        for index, step_name in enumerate(
            [
                "snapshot_download_restore",
                "snapshot_download_transfer",
                "snapshot_download_finalize",
            ],
            start=1,
        ):
            TaskStep.objects.create(task=task, step_index=index, step_name=step_name)
        _create_pending_artifact(
            task=task,
            directory_id=self.directory.id,
            relative_path="docs,readme.txt",
            filename=f"snapshot-download-{task.id}.zip",
        )

        result = run_snapshot_download_task(task=task)

        self.assertIn("artifact_id", result)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertEqual(
            list(task.steps.order_by("step_index").values_list("status", flat=True)),
            [TaskStep.Status.SUCCESS, TaskStep.Status.SUCCESS, TaskStep.Status.SUCCESS],
        )
        restore_step = task.steps.get(step_name="snapshot_download_restore")
        transfer_step = task.steps.get(step_name="snapshot_download_transfer")
        starting_event = TaskEvent.objects.get(
            task=task, message="Starting snapshot download"
        )
        self.assertEqual(starting_event.step_id, restore_step.id)
        self.assertEqual(
            starting_event.metadata["object_id"], self.directory.kopia_snapshot_id
        )
        self.assertEqual(
            starting_event.metadata["object_names"], ["docs", "readme.txt"]
        )
        ready_event = TaskEvent.objects.get(
            task=task, message="Snapshot download artifact is ready"
        )
        self.assertEqual(ready_event.step_id, transfer_step.id)
        self.assertEqual(
            ready_event.metadata["object_name"], ready_event.metadata["filename"]
        )
        artifact = task.snapshot_download_artifact
        self.assertEqual(artifact.content_type, "application/zip")
        with zipfile.ZipFile(artifact.storage_path, "r") as archive:
            self.assertEqual(archive.read("docs/guide.txt"), b"guide")
            self.assertEqual(archive.read("readme.txt"), b"hello")

    @override_settings(PROTECTION_SNAPSHOT_DOWNLOAD_MAX_BYTES=1024)
    @patch(
        "apps.protection.services.snapshot_download._queue_snapshot_download_execution"
    )
    def test_agent_streams_snapshot_artifact_with_signed_task_token(self, mock_queue):
        with (
            tempfile.TemporaryDirectory() as media_root,
            override_settings(MEDIA_ROOT=media_root),
        ):
            response = self.client.post(
                f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/download-tasks/",
                {"path": "readme.txt"},
                format="json",
                **self._headers(),
            )
            self.assertEqual(
                response.status_code, status.HTTP_201_CREATED, response.content
            )
            artifact = SnapshotDownloadArtifact.objects.get(
                task__task_uuid=response.data["task_uuid"]
            )
            Task.objects.filter(id=artifact.task_id).update(status=Task.Status.RUNNING)
            upload = prepare_snapshot_artifact_upload(
                artifact=artifact, node_id=self.agent.id
            )
            content = b"streamed snapshot content"
            checksum = hashlib.sha256(content).hexdigest()

            uploaded = self.client.generic(
                "PUT",
                upload["path"],
                data=content,
                content_type="application/octet-stream",
                HTTP_AUTHORIZATION=f"Bearer {upload['token']}",
                HTTP_X_CONTENT_SHA256=checksum,
                HTTP_X_ARTIFACT_FILENAME="readme.txt",
            )

            self.assertEqual(uploaded.status_code, status.HTTP_200_OK, uploaded.content)
            artifact.refresh_from_db()
            self.assertEqual(artifact.status, SnapshotDownloadArtifact.Status.READY)
            self.assertEqual(artifact.sha256, checksum)
            self.assertEqual(artifact.size_bytes, len(content))
            self.assertEqual(Path(artifact.storage_path).read_bytes(), content)
            replayed = self.client.generic(
                "PUT",
                upload["path"],
                data=content,
                content_type="application/octet-stream",
                HTTP_AUTHORIZATION=f"Bearer {upload['token']}",
                HTTP_X_CONTENT_SHA256=checksum,
                HTTP_X_ARTIFACT_FILENAME="readme.txt",
            )
            self.assertEqual(replayed.status_code, status.HTTP_200_OK, replayed.content)
            download_url = self.client.get(
                f"/api/v1/protection/snapshot-download-artifacts/{artifact.id}/download-url/",
                **self._headers(),
            )
            self.assertEqual(
                download_url.status_code, status.HTTP_200_OK, download_url.content
            )
            downloaded = self.client.get(download_url.data["url"])
            self.assertEqual(downloaded.status_code, status.HTTP_200_OK)
            artifact.refresh_from_db()
            self.assertEqual(artifact.status, SnapshotDownloadArtifact.Status.READY)
            self.assertTrue(Path(artifact.storage_path).exists())
            self.assertEqual(b"".join(downloaded.streaming_content), content)
            artifact.refresh_from_db()
            self.assertEqual(artifact.status, SnapshotDownloadArtifact.Status.DELETED)
            self.assertIsNotNone(artifact.downloaded_at)
            self.assertFalse(Path(artifact.storage_path).exists())
            mock_queue.assert_called_once()

    def test_cleanup_expires_failed_artifacts_and_partial_files(self):
        with (
            tempfile.TemporaryDirectory() as media_root,
            override_settings(MEDIA_ROOT=media_root),
        ):
            task = Task.objects.create(
                organization_id=self.org.id,
                task_type=Task.Type.SNAPSHOT_DOWNLOAD,
                display_name="Expired snapshot download",
            )
            artifact = _create_pending_artifact(
                task=task,
                directory_id=self.directory.id,
                relative_path="readme.txt",
                filename="readme.txt",
            )
            artifact.status = SnapshotDownloadArtifact.Status.FAILED
            artifact.expires_at = timezone.now() - timedelta(seconds=1)
            artifact.save(update_fields=["status", "expires_at", "updated_at"])
            path = Path(artifact.storage_path)
            path.parent.mkdir(parents=True)
            partial = path.with_name(f".{path.name}.stale.part")
            partial.write_bytes(b"partial")

            self.assertEqual(cleanup_expired_snapshot_download_artifacts(), 1)

            artifact.refresh_from_db()
            self.assertEqual(artifact.status, SnapshotDownloadArtifact.Status.EXPIRED)
            self.assertFalse(partial.exists())
