from __future__ import annotations

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node.models import Node, NodeTask
from apps.protection.models import BackupSourceSnapshot, BackupSourceSnapshotDirectory
from apps.protection.services.snapshot_repository_locator import (
    ensure_snapshot_repository_locator,
    resolve_snapshot_repository_locator,
    resolve_snapshot_repository_reader,
)
from apps.storage.repositories.models import Repository


class SnapshotRepositoryLocatorTests(TestCase):
    def setUp(self) -> None:
        self.organization = Organization.objects.create(
            key="snapshot-locator-org",
            name="Snapshot Locator Org",
        )
        self.writer = Node.objects.create(
            organization=self.organization,
            name="snapshot-writer",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.reader = Node.objects.create(
            organization=self.organization,
            name="snapshot-reader",
            role=Node.Role.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.repository = Repository.objects.create(
            organization_id=self.organization.id,
            name="snapshot-direct-nas",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            nas_protocol=Repository.NasProtocol.NFS,
            config={
                "server_address": "10.0.0.20",
                "share_path": "/volume1/backup",
                "kopia_password": "repo-pass",
            },
        )
        self.snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.organization.id,
            snapshot_uid="snapshot-locator-1",
            idempotency_key="snapshot-locator-1",
            source_type="agent",
            source_ref_id=self.writer.id,
            backup_config_id=101,
            repository_id=self.repository.id,
            task_id=201,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        self.directory = BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=self.snapshot,
            organization_id=self.organization.id,
            backup_config_id=101,
            backup_config_dir_id=102,
            source_path="/data",
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-locator-1",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )

    def test_persisted_direct_nas_locator_uses_writer_shard(self) -> None:
        locator = ensure_snapshot_repository_locator(
            directory=self.directory,
            repository=self.repository,
            writer_node_id=self.writer.id,
        )

        self.directory.refresh_from_db()
        self.assertEqual(locator.repository_subdir, f"hp-repos/agent-{self.writer.id}")
        self.assertEqual(
            self.directory.repository_locator["repository_subdir"],
            f"hp-repos/agent-{self.writer.id}",
        )

    def test_pending_locator_tracks_retry_node_then_becomes_immutable(self) -> None:
        self.directory.kopia_snapshot_id = None
        self.directory.save(update_fields=["kopia_snapshot_id", "updated_at"])
        ensure_snapshot_repository_locator(
            directory=self.directory,
            repository=self.repository,
            writer_node_id=self.writer.id,
        )
        ensure_snapshot_repository_locator(
            directory=self.directory,
            repository=self.repository,
            writer_node_id=self.reader.id,
        )

        self.directory.refresh_from_db()
        self.assertEqual(
            self.directory.repository_locator["repository_subdir"],
            f"hp-repos/agent-{self.reader.id}",
        )

        self.directory.kopia_snapshot_id = "kopia-final"
        self.directory.save(update_fields=["kopia_snapshot_id", "updated_at"])
        ensure_snapshot_repository_locator(
            directory=self.directory,
            repository=self.repository,
            writer_node_id=self.writer.id,
        )

        self.directory.refresh_from_db()
        self.assertEqual(
            self.directory.repository_locator["repository_subdir"],
            f"hp-repos/agent-{self.reader.id}",
        )

    def test_stale_retry_cannot_replace_locator_after_snapshot_exists(self) -> None:
        stale_directory = BackupSourceSnapshotDirectory.objects.get(
            pk=self.directory.pk,
        )
        ensure_snapshot_repository_locator(
            directory=self.directory,
            repository=self.repository,
            writer_node_id=self.writer.id,
        )
        self.directory.kopia_snapshot_id = "kopia-created-by-writer"
        self.directory.save(update_fields=["kopia_snapshot_id", "updated_at"])

        locator = ensure_snapshot_repository_locator(
            directory=stale_directory,
            repository=self.repository,
            writer_node_id=self.reader.id,
        )

        stale_directory.refresh_from_db()
        self.assertEqual(locator.writer_node_id, self.writer.id)
        self.assertEqual(
            stale_directory.repository_locator["repository_subdir"],
            f"hp-repos/agent-{self.writer.id}",
        )

    def test_legacy_locator_prefers_original_backup_node_task(self) -> None:
        original_writer = Node.objects.create(
            organization=self.organization,
            name="original-snapshot-writer",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.OFFLINE,
        )
        node_task = NodeTask.objects.create(
            organization=self.organization,
            node=original_writer,
            kind="backup.run",
            correlation_type="protection.backup",
            correlation_id="snapshot-locator-backup",
            status=NodeTask.Status.SUCCESS,
            payload={},
            result={},
            watchdog_deadline_at=timezone.now(),
        )
        self.directory.node_task_id = node_task.id
        self.directory.save(update_fields=["node_task_id", "updated_at"])

        locator = resolve_snapshot_repository_locator(
            directory=self.directory,
            repository=self.repository,
        )

        self.directory.refresh_from_db()
        self.assertEqual(locator.writer_node_id, original_writer.id)
        self.assertEqual(
            locator.repository_subdir,
            f"hp-repos/agent-{original_writer.id}",
        )
        self.assertEqual(self.directory.repository_locator, locator.payload())

    def test_invalid_stored_locator_is_rejected_instead_of_derived(self) -> None:
        self.directory.repository_locator = {
            "version": 1,
            "repository_id": self.repository.id + 1,
            "repository_type": Repository.Type.NAS,
            "repository_subdir": f"hp-repos/agent-{self.writer.id}",
            "writer_node_id": self.writer.id,
            "access_node_id": None,
        }
        self.directory.save(update_fields=["repository_locator", "updated_at"])

        with self.assertRaisesMessage(
            ValidationError,
            "Snapshot repository locator does not match its repository.",
        ):
            resolve_snapshot_repository_locator(
                directory=self.directory,
                repository=self.repository,
            )

    def test_reader_runs_on_gateway_without_changing_snapshot_shard(self) -> None:
        ensure_snapshot_repository_locator(
            directory=self.directory,
            repository=self.repository,
            writer_node_id=self.writer.id,
        )

        access = resolve_snapshot_repository_reader(
            directory=self.directory,
            repository=self.repository,
            fallback_node=self.reader,
            source_type="agent",
            source_ref_id=self.reader.id,
        )

        self.assertEqual(access.node.id, self.reader.id)
        self.assertEqual(
            access.repository_payload["subdir"],
            f"hp-repos/agent-{self.writer.id}",
        )

    def test_proxy_filesystem_snapshot_keeps_original_access_node(self) -> None:
        original_proxy = Node.objects.create(
            organization=self.organization,
            name="original-repository-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        replacement_proxy = Node.objects.create(
            organization=self.organization,
            name="replacement-repository-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.repository.repo_type = Repository.Type.PROXY_FS
        self.repository.nas_protocol = None
        self.repository.bind_node_type = Repository.BindNodeType.PROXY
        self.repository.bind_node_id = original_proxy.id
        self.repository.config = {
            "proxy_node_dir": "/srv/hfl-repository",
            "kopia_password": "repo-pass",
        }
        self.repository.save()
        ensure_snapshot_repository_locator(
            directory=self.directory,
            repository=self.repository,
            writer_node_id=self.writer.id,
        )
        self.repository.bind_node_id = replacement_proxy.id
        self.repository.save(update_fields=["bind_node_id", "updated_at"])

        access = resolve_snapshot_repository_reader(
            directory=self.directory,
            repository=self.repository,
            fallback_node=self.reader,
        )

        self.assertEqual(access.node.id, original_proxy.id)

    def test_legacy_proxy_filesystem_locator_uses_repository_server_task(self) -> None:
        original_proxy = Node.objects.create(
            organization=self.organization,
            name="legacy-original-repository-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        replacement_proxy = Node.objects.create(
            organization=self.organization,
            name="legacy-replacement-repository-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.repository.repo_type = Repository.Type.PROXY_FS
        self.repository.nas_protocol = None
        self.repository.bind_node_type = Repository.BindNodeType.PROXY
        self.repository.bind_node_id = replacement_proxy.id
        self.repository.config = {
            "proxy_node_dir": "/srv/hfl-repository",
            "kopia_password": "repo-pass",
        }
        self.repository.save()
        NodeTask.objects.create(
            organization=self.organization,
            node=original_proxy,
            kind="repository.server.start",
            correlation_type="protection.backup",
            correlation_id=str(self.snapshot.task_uuid),
            status=NodeTask.Status.SUCCESS,
            payload={"repository": {"id": self.repository.id}},
            result={},
            watchdog_deadline_at=timezone.now(),
        )
        NodeTask.objects.create(
            organization=self.organization,
            node=replacement_proxy,
            kind="repository.server.start",
            correlation_type="protection.backup",
            correlation_id=str(self.snapshot.task_uuid),
            status=NodeTask.Status.SUCCESS,
            payload={"repository": {"id": self.repository.id + 1}},
            result={},
            watchdog_deadline_at=timezone.now(),
        )

        access = resolve_snapshot_repository_reader(
            directory=self.directory,
            repository=self.repository,
            fallback_node=self.reader,
        )

        self.directory.refresh_from_db()
        self.assertEqual(access.node.id, original_proxy.id)
        self.assertEqual(
            self.directory.repository_locator["access_node_id"],
            original_proxy.id,
        )

    def test_proxy_filesystem_locator_requires_access_node(self) -> None:
        proxy = Node.objects.create(
            organization=self.organization,
            name="missing-locator-access-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.repository.repo_type = Repository.Type.PROXY_FS
        self.repository.nas_protocol = None
        self.repository.bind_node_type = Repository.BindNodeType.PROXY
        self.repository.bind_node_id = proxy.id
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
            "writer_node_id": self.writer.id,
            "access_node_id": None,
        }
        self.directory.save(update_fields=["repository_locator", "updated_at"])

        with self.assertRaisesMessage(
            ValidationError,
            "Proxy filesystem snapshot locator has no access node.",
        ):
            resolve_snapshot_repository_locator(
                directory=self.directory,
                repository=self.repository,
            )
