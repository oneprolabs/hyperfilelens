from __future__ import annotations

import base64
import zipfile
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.protection.models import (
    BackupConfig,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
)
from apps.protection.services.backup_source_snapshot import create_source_snapshot
from apps.protection.services.snapshot_browser import SnapshotFileDownload
from apps.protection.services.snapshot_download import run_snapshot_download_task
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
        self.org = Organization.objects.create(key="snapshot-browser-org", name="Snapshot Browser Org")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.agent = Node.objects.create(
            organization=self.org,
            name="snapshot-browser-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
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
    def test_browse_proxy_bound_nas_snapshot_directory_uses_bound_proxy(self, mock_run_agent_task_sync):
        proxy = Node.objects.create(
            organization=self.org,
            name="snapshot-browser-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
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
        self.assertEqual(payload["repository"]["subdir"], f"hp-repos/storage-{self.repository.id}")

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
    def test_browse_snapshot_directory_normalizes_kopia_directory_type(self, mock_run_agent_task_sync):
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

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.content)

    def test_browse_rejects_unavailable_directory(self):
        self.directory.status = BackupSourceSnapshotDirectory.Status.FAILED
        self.directory.kopia_snapshot_id = None
        self.directory.error_message = "failed"
        self.directory.save(update_fields=["status", "kopia_snapshot_id", "error_message"])

        response = self.client.get(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/browse/",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.content)

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
    def test_download_root_file_snapshot(self, mock_run_agent_task_sync):
        self.directory.path_type = BackupSourceSnapshotDirectory.PathType.FILE
        self.directory.source_path = "/data/report.txt"
        self.directory.kopia_snapshot_id = "kopia-file-snapshot-1"
        self.directory.save(update_fields=["path_type", "source_path", "kopia_snapshot_id"])
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

    @patch("apps.protection.services.snapshot_download._queue_snapshot_download_execution")
    def test_create_snapshot_download_task(self, mock_queue):
        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/download-tasks/",
            {"path": "readme.txt"},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(response.data["task_type"], "snapshot_download")
        self.assertEqual(response.data["trigger_type"], Task.TriggerType.MANUAL)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["request_payload"]["source_snapshot_directory_id"], self.directory.id)
        self.assertEqual(response.data["request_payload"]["path"], "readme.txt")
        task = Task.objects.get(task_uuid=response.data["task_uuid"])
        source_resource = task.resources.get(resource_type=TaskResource.Type.BACKUP_SOURCE)
        self.assertEqual(source_resource.resource_subtype, "agent")
        self.assertEqual(source_resource.resource_id, self.agent.id)
        self.assertEqual(
            list(task.steps.order_by("step_index").values_list("step_name", flat=True)),
            ["snapshot_download_restore", "snapshot_download_transfer", "snapshot_download_finalize"],
        )
        mock_queue.assert_called_once()

    @patch("apps.protection.services.snapshot_download._queue_snapshot_download_execution")
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

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(response.data["request_payload"]["path"], "")
        self.assertIn("report.txt", response.data["display_name"])
        mock_queue.assert_called_once()

    @patch("apps.protection.services.snapshot_download._queue_snapshot_download_execution")
    def test_create_snapshot_batch_download_task(self, mock_queue):
        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/batch-download-tasks/",
            {"paths": ["docs", "readme.txt", "readme.txt"]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(response.data["task_type"], "snapshot_download")
        self.assertEqual(response.data["trigger_type"], Task.TriggerType.MANUAL)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["request_payload"]["source_snapshot_directory_id"], self.directory.id)
        self.assertEqual(response.data["request_payload"]["paths"], ["docs", "readme.txt"])
        task = Task.objects.get(task_uuid=response.data["task_uuid"])
        source_resource = task.resources.get(resource_type=TaskResource.Type.BACKUP_SOURCE)
        self.assertEqual(source_resource.resource_subtype, "agent")
        self.assertEqual(source_resource.resource_id, self.agent.id)
        mock_queue.assert_called_once()

    @patch("apps.protection.services.snapshot_download._queue_snapshot_download_execution")
    def test_create_snapshot_batch_download_task_rejects_parent_child_conflict(self, mock_queue):
        response = self.client.post(
            f"/api/v1/protection/backup-source-snapshot-directories/{self.directory.id}/batch-download-tasks/",
            {"paths": ["docs", "docs/readme.txt"]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        mock_queue.assert_not_called()

    @patch("apps.protection.services.snapshot_download.download_snapshot_file")
    def test_run_snapshot_batch_download_task_persists_zip_artifact(self, mock_download_snapshot_file):
        nested = BytesIO()
        with zipfile.ZipFile(nested, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("guide.txt", b"guide")
        mock_download_snapshot_file.side_effect = [
            SnapshotFileDownload(filename="docs.zip", content=nested.getvalue(), content_type="application/zip"),
            SnapshotFileDownload(filename="readme.txt", content=b"hello", content_type="application/octet-stream"),
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
            ["snapshot_download_restore", "snapshot_download_transfer", "snapshot_download_finalize"],
            start=1,
        ):
            TaskStep.objects.create(task=task, step_index=index, step_name=step_name)

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
        starting_event = TaskEvent.objects.get(task=task, message="Starting snapshot download")
        self.assertEqual(starting_event.step_id, restore_step.id)
        self.assertEqual(starting_event.metadata["object_id"], self.directory.kopia_snapshot_id)
        self.assertEqual(starting_event.metadata["object_names"], ["docs", "readme.txt"])
        ready_event = TaskEvent.objects.get(task=task, message="Snapshot download artifact is ready")
        self.assertEqual(ready_event.step_id, transfer_step.id)
        self.assertEqual(ready_event.metadata["object_name"], ready_event.metadata["filename"])
        artifact = task.snapshot_download_artifact
        self.assertEqual(artifact.content_type, "application/zip")
        with zipfile.ZipFile(artifact.storage_path, "r") as archive:
            self.assertEqual(archive.read("docs/guide.txt"), b"guide")
            self.assertEqual(archive.read("readme.txt"), b"hello")
