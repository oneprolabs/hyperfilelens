from __future__ import annotations

from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.protection.models import (
    BackupConfig,
    BackupConfigDirectory,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
)
from apps.protection.services.backup_source_snapshot import (
    create_source_snapshot,
    record_source_snapshot_directory_result,
)
from apps.protection.services.snapshot_delete import (
    create_and_queue_snapshot_delete_task,
    create_snapshot_delete_task,
    reconcile_snapshot_delete_tasks,
    run_snapshot_delete_task,
    snapshot_delete_retry_delay,
)
from apps.source.models import SourceResource
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_location import (
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    reserve_repository_location,
)
from apps.task.models import Task, TaskResource


class SnapshotDeleteTaskTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="snapshot-delete@test.local",
            email="snapshot-delete@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(
            key="snapshot-delete-org", name="Snapshot Delete Org"
        )
        Membership.objects.create(
            user=self.user, organization=self.org, role=Membership.Role.ADMIN
        )
        self.agent = Node.objects.create(
            organization=self.org,
            name="snapshot-delete-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="snapshot-delete-repo",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="snapshot-delete-bucket",
            config={
                "endpoint": "s3.example.internal:9000",
                "region": "cn-test-1",
                "prefix": "kopia/delete",
                "access_key_id": "ak",
                "secret_access_key": "sk",
                "kopia_password": "123456",
                "use_tls": False,
            },
        )
        reserve_repository_location(self.repository)
        mark_repository_location_owned(self.repository)
        mark_repository_location_ownership_verified(self.repository)
        self.config = BackupConfig.objects.create(
            organization_id=self.org.id,
            name="Snapshot delete config",
            source_type="agent",
            source_ref_id=self.agent.id,
            repository_id=self.repository.id,
        )
        self.dir_a = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data/a",
            display_name="a",
        )
        self.dir_b = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=self.config,
            path="/data/b",
            display_name="b",
        )
        self.task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Snapshot delete source task",
        )
        self.snapshot = create_source_snapshot(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=self.config.id,
            repository_id=self.repository.id,
            task_id=self.task.id,
            task_uuid=self.task.task_uuid,
            idempotency_key="snapshot-delete-source",
            status=BackupSourceSnapshot.Status.AVAILABLE,
            directory_count=2,
        )
        BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=self.snapshot,
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=self.dir_a.id,
            source_path="/data/a",
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-a",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=self.snapshot,
            organization_id=self.org.id,
            backup_config_id=self.config.id,
            backup_config_dir_id=self.dir_b.id,
            source_path="/data/b",
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-b",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )

    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_run_snapshot_delete_task_marks_logical_snapshot_deleted(
        self, mock_run_agent_task_sync
    ):
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)
        source_resource = task.resources.get(
            resource_type=TaskResource.Type.BACKUP_SOURCE
        )
        self.assertEqual(source_resource.resource_subtype, "agent")
        self.assertEqual(source_resource.resource_id, self.agent.id)
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(id="node-delete-1", status="success", last_error=""),
            result={
                "deleted_count": 2,
                "failed_count": 0,
                "results": [
                    {"kopia_snapshot_id": "kopia-a", "status": "success"},
                    {"kopia_snapshot_id": "kopia-b", "status": "success"},
                ],
            },
            ok=True,
            timed_out=False,
        )

        result = run_snapshot_delete_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=self.snapshot.id,
        )

        self.snapshot.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertEqual(self.snapshot.status, BackupSourceSnapshot.Status.DELETED)
        self.assertIsNotNone(self.snapshot.deleted_at)
        self.assertEqual(result["deleted_count"], 2)
        self.assertFalse(
            BackupSourceSnapshotDirectory.objects.filter(
                source_snapshot=self.snapshot,
            )
            .exclude(status=BackupSourceSnapshotDirectory.Status.DELETED)
            .exists()
        )
        payload = mock_run_agent_task_sync.call_args.kwargs["payload"]
        self.assertEqual(payload["kopia_snapshot_ids"], ["kopia-a", "kopia-b"])
        events = task.events.filter(
            message="Deleting physical Kopia snapshot"
        ).order_by("seq")
        self.assertEqual(events.count(), 2)
        self.assertEqual(
            events[0].metadata["kopia_snapshot_display"], "kopia-a (/data/a)"
        )
        self.assertEqual(
            events[1].metadata["kopia_snapshot_display"], "kopia-b (/data/b)"
        )
        self.assertEqual(events[0].metadata["object_id"], "kopia-a")
        self.assertEqual(events[0].metadata["object_names"], ["/data/a"])
        self.assertEqual(events[1].metadata["object_id"], "kopia-b")
        self.assertEqual(events[1].metadata["object_names"], ["/data/b"])

    def test_create_snapshot_delete_rejects_repository_being_removed(self):
        self.repository.status = Repository.Status.REMOVING
        self.repository.save(update_fields=["status", "updated_at"])

        with self.assertRaisesMessage(
            ValidationError,
            "Repository is no longer available for this operation.",
        ):
            create_snapshot_delete_task(source_snapshot=self.snapshot)

        self.snapshot.refresh_from_db()
        self.assertEqual(self.snapshot.status, BackupSourceSnapshot.Status.AVAILABLE)
        self.assertFalse(
            Task.objects.filter(task_type=Task.Type.SNAPSHOT_DELETE).exists()
        )

    def test_retry_snapshot_delete_rejects_repository_being_removed(self):
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)
        task.status = Task.Status.FAILED
        task.save(update_fields=["status", "updated_at"])
        self.snapshot.status = BackupSourceSnapshot.Status.DELETE_FAILED
        self.snapshot.save(update_fields=["status", "updated_at"])
        self.repository.status = Repository.Status.REMOVING
        self.repository.save(update_fields=["status", "updated_at"])

        with self.assertRaisesMessage(
            ValidationError,
            "Repository is no longer available for this operation.",
        ):
            create_and_queue_snapshot_delete_task(source_snapshot=self.snapshot)

        task.refresh_from_db()
        self.snapshot.refresh_from_db()
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertEqual(
            self.snapshot.status,
            BackupSourceSnapshot.Status.DELETE_FAILED,
        )

    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_snapshot_delete_groups_by_locator_and_preserves_partial_results(
        self,
        mock_run_agent_task_sync,
    ):
        original_proxy = Node.objects.create(
            organization=self.org,
            name="snapshot-delete-original-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        replacement_proxy = Node.objects.create(
            organization=self.org,
            name="snapshot-delete-replacement-proxy",
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
        rows = list(
            BackupSourceSnapshotDirectory.objects.filter(
                source_snapshot=self.snapshot,
            ).order_by("id")
        )
        for row, access_node in zip(
            rows,
            (original_proxy, replacement_proxy),
            strict=True,
        ):
            row.repository_locator = {
                "version": 1,
                "repository_id": self.repository.id,
                "repository_type": Repository.Type.PROXY_FS,
                "repository_subdir": "",
                "writer_node_id": self.agent.id,
                "access_node_id": access_node.id,
            }
            row.save(update_fields=["repository_locator", "updated_at"])

        def _delete_group(**kwargs):
            snapshot_ids = kwargs["payload"]["kopia_snapshot_ids"]
            failed = kwargs["node_id"] == replacement_proxy.id
            return SimpleNamespace(
                task=SimpleNamespace(
                    id="node-delete-group",
                    status="failed" if failed else "success",
                    last_error="delete failed" if failed else "",
                ),
                result={
                    "deleted_count": 0 if failed else len(snapshot_ids),
                    "failed_count": len(snapshot_ids) if failed else 0,
                    "results": [
                        {
                            "kopia_snapshot_id": snapshot_id,
                            "status": "failed" if failed else "success",
                            **({"error_message": "boom"} if failed else {}),
                        }
                        for snapshot_id in snapshot_ids
                    ],
                },
                ok=not failed,
                timed_out=False,
            )

        mock_run_agent_task_sync.side_effect = _delete_group
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)

        result = run_snapshot_delete_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=self.snapshot.id,
        )

        task.refresh_from_db()
        self.snapshot.refresh_from_db()
        rows = list(
            BackupSourceSnapshotDirectory.objects.filter(
                source_snapshot=self.snapshot,
            ).order_by("id")
        )
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertEqual(
            self.snapshot.status,
            BackupSourceSnapshot.Status.DELETE_FAILED,
        )
        self.assertEqual(rows[0].status, BackupSourceSnapshotDirectory.Status.DELETED)
        self.assertEqual(rows[1].status, BackupSourceSnapshotDirectory.Status.AVAILABLE)
        self.assertEqual(mock_run_agent_task_sync.call_count, 2)
        self.assertEqual(
            {
                call.kwargs["node_id"]
                for call in mock_run_agent_task_sync.call_args_list
            },
            {original_proxy.id, replacement_proxy.id},
        )

    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_run_snapshot_delete_task_keeps_logical_snapshot_when_partial_delete_fails(
        self, mock_run_agent_task_sync
    ):
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(
                id="node-delete-2", status="failed", last_error="delete failed"
            ),
            result={
                "deleted_count": 1,
                "failed_count": 1,
                "results": [
                    {"kopia_snapshot_id": "kopia-a", "status": "success"},
                    {
                        "kopia_snapshot_id": "kopia-b",
                        "status": "failed",
                        "error_message": "boom",
                    },
                ],
            },
            ok=False,
            timed_out=False,
        )

        run_snapshot_delete_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=self.snapshot.id,
        )

        self.snapshot.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertEqual(
            self.snapshot.status, BackupSourceSnapshot.Status.DELETE_FAILED
        )
        self.assertEqual(
            BackupSourceSnapshotDirectory.objects.get(
                kopia_snapshot_id="kopia-a"
            ).status,
            BackupSourceSnapshotDirectory.Status.DELETED,
        )
        self.assertEqual(
            BackupSourceSnapshotDirectory.objects.get(
                kopia_snapshot_id="kopia-b"
            ).status,
            BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )

    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_already_absent_physical_snapshots_complete_logical_delete(
        self, mock_run_agent_task_sync
    ):
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(
                id="node-delete-absent", status="failed", last_error="2 deletes failed"
            ),
            result={
                "deleted_count": 0,
                "failed_count": 2,
                "results": [
                    {
                        "kopia_snapshot_id": "kopia-a",
                        "status": "failed",
                        "delete": {"stderr": "no snapshots matched kopia-a"},
                    },
                    {
                        "kopia_snapshot_id": "kopia-b",
                        "status": "failed",
                        "delete": {"stderr": "no snapshots matched kopia-b"},
                    },
                ],
            },
            ok=False,
            timed_out=False,
        )

        result = run_snapshot_delete_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=self.snapshot.id,
        )

        self.snapshot.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(self.snapshot.status, BackupSourceSnapshot.Status.DELETED)
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertEqual(result["already_absent_count"], 2)

    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_legacy_sentinel_row_is_not_dispatched(self, mock_run_agent_task_sync):
        BackupSourceSnapshotDirectory.objects.filter(
            source_snapshot=self.snapshot,
            backup_config_dir_id=self.dir_b.id,
        ).update(kopia_snapshot_id="None")
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(
                id="node-delete-sentinel", status="success", last_error=""
            ),
            result={
                "deleted_count": 1,
                "failed_count": 0,
                "results": [
                    {"kopia_snapshot_id": "kopia-a", "status": "success"},
                ],
            },
            ok=True,
            timed_out=False,
        )
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)

        result = run_snapshot_delete_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=self.snapshot.id,
        )

        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertEqual(result["deleted_count"], 2)
        self.assertEqual(
            mock_run_agent_task_sync.call_args.kwargs["payload"]["kopia_snapshot_ids"],
            ["kopia-a"],
        )

    def test_retry_delay_sequence_caps_at_two_hours(self):
        self.assertEqual(
            [
                int(snapshot_delete_retry_delay(i).total_seconds() / 60)
                for i in range(9)
            ],
            [1, 4, 16, 30, 60, 120, 120, 120, 120],
        )

    def test_active_delete_lookup_uses_request_payload(self):
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)

        duplicate = create_snapshot_delete_task(source_snapshot=self.snapshot)

        self.assertEqual(duplicate.id, task.id)
        self.assertEqual(
            Task.objects.filter(
                task_type=Task.Type.SNAPSHOT_DELETE,
                request_payload__source_snapshot_id=self.snapshot.id,
            ).count(),
            1,
        )
        self.assertEqual(
            list(task.resources.values_list("resource_type", flat=True)),
            [TaskResource.Type.BACKUP_SOURCE],
        )

    @patch(
        "apps.protection.services.snapshot_delete_execution.delete_s3_snapshots",
        return_value={
            "deleted_count": 2,
            "failed_count": 0,
            "results": [
                {"kopia_snapshot_id": "kopia-a", "status": "success"},
                {"kopia_snapshot_id": "kopia-b", "status": "success"},
            ],
        },
    )
    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_offline_s3_writer_falls_back_to_controller(
        self,
        mock_run_agent_task_sync,
        mock_delete_s3_snapshots,
    ):
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)
        self.agent.availability = Node.Availability.OFFLINE
        self.agent.save(update_fields=["availability", "updated_at"])

        result = run_snapshot_delete_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=self.snapshot.id,
        )

        self.snapshot.refresh_from_db()
        task.refresh_from_db()
        mock_run_agent_task_sync.assert_not_called()
        mock_delete_s3_snapshots.assert_called_once_with(
            self.repository,
            snapshot_ids=["kopia-a", "kopia-b"],
            timeout_seconds=3600,
        )
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertEqual(self.snapshot.status, BackupSourceSnapshot.Status.DELETED)
        self.assertEqual(result["deleted_count"], 2)

    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_busy_s3_writer_does_not_shift_cleanup_to_controller(
        self,
        mock_run_agent_task_sync,
    ):
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)
        self.agent.status = Node.Status.UPGRADING
        self.agent.save(update_fields=["status", "updated_at"])

        with patch(
            "apps.protection.services.snapshot_delete_execution.delete_s3_snapshots"
        ) as mock_delete_s3_snapshots:
            run_snapshot_delete_task(
                organization_id=self.org.id,
                task_uuid=str(task.task_uuid),
                source_snapshot_id=self.snapshot.id,
            )

        task.refresh_from_db()
        mock_run_agent_task_sync.assert_not_called()
        mock_delete_s3_snapshots.assert_not_called()
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertIn("busy", task.error_message)

    @patch("apps.protection.services.snapshot_delete_execution.delete_s3_snapshots")
    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_online_s3_writer_remains_preferred_over_controller(
        self,
        mock_run_agent_task_sync,
        mock_delete_s3_snapshots,
    ):
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(
                id="node-delete-online", status="success", last_error=""
            ),
            result={
                "deleted_count": 2,
                "failed_count": 0,
                "results": [
                    {"kopia_snapshot_id": "kopia-a", "status": "success"},
                    {"kopia_snapshot_id": "kopia-b", "status": "success"},
                ],
            },
            ok=True,
            timed_out=False,
        )
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)

        run_snapshot_delete_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=self.snapshot.id,
        )

        mock_run_agent_task_sync.assert_called_once()
        self.assertEqual(
            mock_run_agent_task_sync.call_args.kwargs["node_id"],
            self.agent.id,
        )
        mock_delete_s3_snapshots.assert_not_called()

    @patch(
        "apps.protection.services.snapshot_delete_execution.delete_s3_snapshots",
        return_value={
            "deleted_count": 2,
            "failed_count": 0,
            "results": [
                {"kopia_snapshot_id": "kopia-a", "status": "success"},
                {"kopia_snapshot_id": "kopia-b", "status": "success"},
            ],
        },
    )
    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_s3_delivery_failure_before_dispatch_uses_controller(
        self,
        mock_run_agent_task_sync,
        mock_delete_s3_snapshots,
    ):
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(
                id="node-delete-undelivered",
                status="failed",
                last_error="agent websocket is not routable",
                dispatched_at=None,
            ),
            result={},
            ok=False,
            timed_out=False,
        )
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)

        run_snapshot_delete_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=self.snapshot.id,
        )

        mock_run_agent_task_sync.assert_called_once()
        mock_delete_s3_snapshots.assert_called_once()

    @patch("apps.protection.services.snapshot_delete_execution.delete_s3_snapshots")
    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_s3_failure_after_dispatch_does_not_run_twice_on_controller(
        self,
        mock_run_agent_task_sync,
        mock_delete_s3_snapshots,
    ):
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(
                id="node-delete-dispatched",
                status="failed",
                last_error="Agent went offline during task execution.",
                dispatched_at=timezone.now(),
            ),
            result={},
            ok=False,
            timed_out=False,
        )
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)

        run_snapshot_delete_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=self.snapshot.id,
        )

        mock_run_agent_task_sync.assert_called_once()
        mock_delete_s3_snapshots.assert_not_called()

    @patch("apps.protection.services.snapshot_delete_execution.delete_s3_snapshots")
    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_nas_source_s3_cleanup_prefers_backup_writer_proxy(
        self,
        mock_run_agent_task_sync,
        mock_delete_s3_snapshots,
    ):
        proxy = Node.objects.create(
            organization=self.org,
            name="snapshot-delete-nas-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        nas_source = SourceResource.objects.create(
            organization_id=self.org.id,
            name="snapshot-delete-nas",
            resource_type="nas",
            bound_node=proxy,
            availability="online",
            mount_status="mounted",
        )
        self.config.source_type = "nas"
        self.config.source_ref_id = nas_source.id
        self.config.save(update_fields=["source_type", "source_ref_id"])
        self.snapshot.source_type = "nas"
        self.snapshot.source_ref_id = nas_source.id
        self.snapshot.save(update_fields=["source_type", "source_ref_id", "updated_at"])
        BackupSourceSnapshotDirectory.objects.filter(
            source_snapshot=self.snapshot,
        ).update(
            repository_locator={
                "version": 1,
                "repository_id": self.repository.id,
                "repository_type": Repository.Type.S3,
                "repository_subdir": "",
                "writer_node_id": proxy.id,
                "access_node_id": None,
            }
        )
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(id="proxy-delete-s3", status="success", last_error=""),
            result={
                "deleted_count": 2,
                "failed_count": 0,
                "results": [
                    {"kopia_snapshot_id": "kopia-a", "status": "success"},
                    {"kopia_snapshot_id": "kopia-b", "status": "success"},
                ],
            },
            ok=True,
            timed_out=False,
        )
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)

        run_snapshot_delete_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=self.snapshot.id,
        )

        self.assertEqual(
            mock_run_agent_task_sync.call_args.kwargs["node_id"],
            proxy.id,
        )
        mock_delete_s3_snapshots.assert_not_called()

    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_offline_direct_nas_writer_never_uses_controller(
        self,
        mock_run_agent_task_sync,
    ):
        self.repository.repo_type = Repository.Type.NAS
        self.repository.nas_protocol = Repository.NasProtocol.NFS
        self.repository.s3_bucket = ""
        self.repository.config = {
            "server_address": "10.0.0.20",
            "share_path": "/backup",
            "kopia_password": "repo-pass",
        }
        self.repository.save()
        self.agent.metadata = {"platform": "linux"}
        self.agent.availability = Node.Availability.OFFLINE
        self.agent.save(update_fields=["metadata", "availability", "updated_at"])
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)

        with patch(
            "apps.protection.services.snapshot_delete_execution.delete_s3_snapshots"
        ) as mock_delete_s3_snapshots:
            run_snapshot_delete_task(
                organization_id=self.org.id,
                task_uuid=str(task.task_uuid),
                source_snapshot_id=self.snapshot.id,
            )

        task.refresh_from_db()
        mock_run_agent_task_sync.assert_not_called()
        mock_delete_s3_snapshots.assert_not_called()
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertIn("offline", task.error_message)

    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_offline_proxy_filesystem_never_uses_controller(
        self,
        mock_run_agent_task_sync,
    ):
        proxy = Node.objects.create(
            organization=self.org,
            name="snapshot-delete-offline-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.OFFLINE,
        )
        self.repository.repo_type = Repository.Type.PROXY_FS
        self.repository.s3_bucket = ""
        self.repository.bind_node_type = Repository.BindNodeType.PROXY
        self.repository.bind_node_id = proxy.id
        self.repository.config = {
            "proxy_node_dir": "/srv/hfl-repository",
            "kopia_password": "repo-pass",
        }
        self.repository.save()
        BackupSourceSnapshotDirectory.objects.filter(
            source_snapshot=self.snapshot,
        ).update(
            repository_locator={
                "version": 1,
                "repository_id": self.repository.id,
                "repository_type": Repository.Type.PROXY_FS,
                "repository_subdir": "",
                "writer_node_id": self.agent.id,
                "access_node_id": proxy.id,
            }
        )
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)

        with patch(
            "apps.protection.services.snapshot_delete_execution.delete_s3_snapshots"
        ) as mock_delete_s3_snapshots:
            run_snapshot_delete_task(
                organization_id=self.org.id,
                task_uuid=str(task.task_uuid),
                source_snapshot_id=self.snapshot.id,
            )

        task.refresh_from_db()
        mock_run_agent_task_sync.assert_not_called()
        mock_delete_s3_snapshots.assert_not_called()
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertIn("offline", task.error_message)

    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_offline_proxy_bound_nas_never_uses_controller(
        self,
        mock_run_agent_task_sync,
    ):
        proxy = Node.objects.create(
            organization=self.org,
            name="snapshot-delete-offline-nas-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.OFFLINE,
        )
        self.repository.repo_type = Repository.Type.NAS
        self.repository.nas_protocol = Repository.NasProtocol.NFS
        self.repository.s3_bucket = ""
        self.repository.bind_node_type = Repository.BindNodeType.PROXY
        self.repository.bind_node_id = proxy.id
        self.repository.config = {
            "server_address": "10.0.0.20",
            "share_path": "/backup",
            "kopia_password": "repo-pass",
        }
        self.repository.save()
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)

        with patch(
            "apps.protection.services.snapshot_delete_execution.delete_s3_snapshots"
        ) as mock_delete_s3_snapshots:
            run_snapshot_delete_task(
                organization_id=self.org.id,
                task_uuid=str(task.task_uuid),
                source_snapshot_id=self.snapshot.id,
            )

        task.refresh_from_db()
        mock_run_agent_task_sync.assert_not_called()
        mock_delete_s3_snapshots.assert_not_called()
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertIn("offline", task.error_message)

    @patch(
        "apps.protection.services.snapshot_delete_execution.delete_s3_snapshots",
        return_value={
            "deleted_count": 2,
            "failed_count": 0,
            "results": [
                {"kopia_snapshot_id": "kopia-a", "status": "success"},
                {"kopia_snapshot_id": "kopia-b", "status": "success"},
            ],
        },
    )
    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_data_gateway_is_never_selected_as_snapshot_delete_worker(
        self,
        mock_run_agent_task_sync,
        mock_delete_s3_snapshots,
    ):
        gateway = Node.objects.create(
            organization=self.org,
            name="snapshot-delete-data-gateway",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        BackupSourceSnapshotDirectory.objects.filter(
            source_snapshot=self.snapshot,
        ).update(
            repository_locator={
                "version": 1,
                "repository_id": self.repository.id,
                "repository_type": Repository.Type.S3,
                "repository_subdir": "",
                "writer_node_id": gateway.id,
                "access_node_id": None,
            }
        )
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)

        run_snapshot_delete_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=self.snapshot.id,
        )

        mock_run_agent_task_sync.assert_not_called()
        mock_delete_s3_snapshots.assert_called_once()

    @patch(
        "apps.protection.services.snapshot_delete_execution.delete_s3_snapshots",
        return_value={
            "deleted_count": 0,
            "failed_count": 1,
            "results": [
                {
                    "kopia_snapshot_id": "kopia-a",
                    "status": "failed",
                    "delete": {"stderr_tail": "no snapshots matched kopia-a"},
                },
                {
                    "kopia_snapshot_id": "kopia-b",
                    "status": "failed",
                    "delete": {"stderr_tail": "no snapshots matched kopia-b"},
                },
            ],
        },
    )
    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_controller_treats_already_absent_snapshots_as_idempotent_success(
        self,
        mock_run_agent_task_sync,
        _delete_s3_snapshots,
    ):
        self.agent.availability = Node.Availability.OFFLINE
        self.agent.save(update_fields=["availability", "updated_at"])
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)

        result = run_snapshot_delete_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=self.snapshot.id,
        )

        task.refresh_from_db()
        self.snapshot.refresh_from_db()
        mock_run_agent_task_sync.assert_not_called()
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertEqual(self.snapshot.status, BackupSourceSnapshot.Status.DELETED)
        self.assertEqual(result["already_absent_count"], 2)

    def test_reconcile_recovers_stale_running_task_by_request_payload(self):
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)
        Task.objects.filter(id=task.id).update(
            status=Task.Status.RUNNING,
            started_at=timezone.now() - timedelta(hours=3),
            updated_at=timezone.now() - timedelta(hours=3),
        )
        current = timezone.now()

        result = reconcile_snapshot_delete_tasks(now=current)

        self.snapshot.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(result["recovered_running"], 1)
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertEqual(task.error_code, "SNAPSHOT_DELETE_INTERRUPTED")
        self.assertEqual(
            self.snapshot.status, BackupSourceSnapshot.Status.DELETE_FAILED
        )

    def test_directory_result_normalizes_none_instead_of_stringifying_it(self):
        row = record_source_snapshot_directory_result(
            source_snapshot=self.snapshot,
            backup_config_dir_id=self.dir_a.id,
            source_path=self.dir_a.path,
            repository_id=self.repository.id,
            status=BackupSourceSnapshotDirectory.Status.FAILED,
            kopia_snapshot_id=None,
            error_code="TEST_FAILED",
            error_message="failed before creating a snapshot",
        )

        row.refresh_from_db()
        self.assertIsNone(row.kopia_snapshot_id)

    def test_repair_command_dry_run_does_not_change_state(self):
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)
        stdout = StringIO()

        call_command("repair_snapshot_delete_state", stdout=stdout)

        self.snapshot.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(self.snapshot.status, BackupSourceSnapshot.Status.DELETING)
        self.assertEqual(task.status, Task.Status.PENDING)
        self.assertIn("DRY-RUN:", stdout.getvalue())

    @patch("apps.protection.services.snapshot_delete.queue_snapshot_delete_task")
    def test_repair_command_apply_retries_unresolved_delete(
        self, mock_queue_snapshot_delete_task
    ):
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)
        stdout = StringIO()

        with self.captureOnCommitCallbacks(execute=True):
            call_command(
                "repair_snapshot_delete_state",
                apply=True,
                retry=True,
                stdout=stdout,
            )

        self.snapshot.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(self.snapshot.status, BackupSourceSnapshot.Status.DELETING)
        self.assertEqual(task.status, Task.Status.PENDING)
        self.assertEqual(task.retry_count, 1)
        mock_queue_snapshot_delete_task.assert_called_once()
        self.assertIn("queued=1", stdout.getvalue())

    def test_repair_command_finalizes_rows_without_physical_snapshot_ids(self):
        BackupSourceSnapshotDirectory.objects.filter(
            source_snapshot=self.snapshot
        ).update(
            status=BackupSourceSnapshotDirectory.Status.CANCELLED,
            kopia_snapshot_id="None",
        )
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)

        call_command("repair_snapshot_delete_state", apply=True, stdout=StringIO())

        self.snapshot.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(self.snapshot.status, BackupSourceSnapshot.Status.DELETED)
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertFalse(
            BackupSourceSnapshotDirectory.objects.filter(source_snapshot=self.snapshot)
            .exclude(status=BackupSourceSnapshotDirectory.Status.DELETED)
            .exists()
        )

    @patch("apps.protection.services.snapshot_delete.run_agent_task_sync")
    def test_delete_failed_manual_retry_reuses_original_task(
        self, mock_run_agent_task_sync
    ):
        task = create_snapshot_delete_task(source_snapshot=self.snapshot)
        mock_run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(
                id="node-delete-retry", status="failed", last_error="temporary failure"
            ),
            result={
                "results": [
                    {"kopia_snapshot_id": "kopia-a", "status": "success"},
                    {
                        "kopia_snapshot_id": "kopia-b",
                        "status": "failed",
                        "error_message": "temporary",
                    },
                ],
            },
            ok=False,
            timed_out=False,
        )
        run_snapshot_delete_task(
            organization_id=self.org.id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=self.snapshot.id,
        )

        retried = create_and_queue_snapshot_delete_task(source_snapshot=self.snapshot)

        self.snapshot.refresh_from_db()
        retried.refresh_from_db()
        self.assertEqual(retried.id, task.id)
        self.assertEqual(retried.task_uuid, task.task_uuid)
        self.assertEqual(retried.status, Task.Status.PENDING)
        self.assertEqual(retried.retry_count, 1)
        self.assertEqual(self.snapshot.status, BackupSourceSnapshot.Status.DELETING)
        self.assertEqual(
            retried.request_payload["kopia_snapshot_ids"],
            ["kopia-a", "kopia-b"],
        )
