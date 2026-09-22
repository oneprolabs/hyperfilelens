"""Seed two ready-to-run sources for manually testing the Start Backup flow."""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
import uuid

from apps.iam.models import Membership
from apps.node.models.node import Node
from apps.protection.models.backup_config import BackupConfig, BackupConfigDirectory
from apps.protection.models.backup_source_snapshot import BackupSourceSnapshot, BackupSourceSnapshotDirectory
from apps.source.constants import PipelineStep
from apps.source.models.source_pipeline import SourceBackupPipelineEntry
from apps.storage.repositories.models import Repository


class Command(BaseCommand):
    help = "Create two idempotent ready sources in Protected > Start Backup"

    @transaction.atomic
    def handle(self, *args, **options):
        membership = Membership.objects.filter(is_active=True).select_related("organization").first()
        if not membership:
            self.stderr.write(self.style.ERROR("No active organization membership found."))
            return
        org = membership.organization

        for index, (host, path) in enumerate(
            (("[TEST] recovery-agent-01", "/home/test-user/documents"),
             ("[TEST] recovery-agent-02", "/srv/application-data")),
            start=1,
        ):
            node, _ = Node.objects.update_or_create(
                organization=org,
                name=host,
                defaults={
                    "role": "agent", "installation_mode": "system",
                    "status": "active", "availability": "online",
                    "os_name": "Ubuntu 24.04", "ip_address": f"192.0.2.{20 + index}",
                },
            )
            repo, _ = Repository.objects.update_or_create(
                organization_id=org.id,
                name=f"[TEST] Recovery repository {index}",
                defaults={"repo_type": "s3", "status": "created", "health": "online"},
            )
            config, _ = BackupConfig.objects.update_or_create(
                organization_id=org.id,
                source_type="agent",
                source_ref_id=node.id,
                defaults={"name": f"[TEST] Recovery backup {index}", "repository_id": repo.id, "status": "active", "recovery_plan_enabled": False},
            )
            directory, _ = BackupConfigDirectory.objects.update_or_create(
                backup_config=config, path=path,
                defaults={"organization_id": org.id, "path_type": "directory", "display_name": path},
            )
            SourceBackupPipelineEntry.objects.update_or_create(
                organization=org, source_kind="agent", ref_id=node.id,
                defaults={"step": PipelineStep.READY, "source_name": node.name, "source_hostname": node.name, "source_ip": node.ip_address or "", "source_status": "active", "source_availability": "online"},
            )
            snapshot, _ = BackupSourceSnapshot.objects.update_or_create(
                organization_id=org.id,
                snapshot_uid=f"test-recovery-{node.id}",
                defaults={
                    "idempotency_key": f"test-recovery-{node.id}", "source_type": "agent",
                    "source_ref_id": node.id, "backup_config_id": config.id,
                    "repository_id": repo.id, "task_id": 0, "task_uuid": uuid.uuid4(),
                    "status": "available", "started_at": timezone.now(), "finished_at": timezone.now(),
                    "directory_count": 1, "successful_directory_count": 1,
                    "total_size_bytes": 1024, "file_count": 3, "dir_count": 1,
                },
            )
            BackupSourceSnapshotDirectory.objects.update_or_create(
                source_snapshot=snapshot, backup_config_dir_id=directory.id,
                defaults={
                    "organization_id": org.id, "backup_config_id": config.id,
                    "source_path": path, "path_type": "directory", "display_name": path,
                    "repository_id": repo.id, "kopia_snapshot_id": f"test-kopia-{node.id}",
                    "status": "available", "size_bytes": 1024, "file_count": 3, "dir_count": 1,
                },
            )
            self.stdout.write(self.style.SUCCESS(f"Ready source: {node.name} (id={node.id})"))
