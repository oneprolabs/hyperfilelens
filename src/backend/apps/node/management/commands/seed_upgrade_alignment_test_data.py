"""Seed a synthetic in-progress upgrade for manual UI layout verification."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.iam.models import Membership
from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole


class Command(BaseCommand):
    help = "Create an idempotent synthetic Agent upgrade for UI testing"

    @transaction.atomic
    def handle(self, *args, **options):
        membership = (
            Membership.objects.filter(is_active=True)
            .select_related("organization")
            .first()
        )
        if membership is None:
            self.stderr.write(self.style.ERROR("No active organization membership found."))
            return

        org = membership.organization
        node, _ = Node.objects.update_or_create(
            organization=org,
            name="[TEST] Upgrade alignment host",
            defaults={
                "role": NodeRole.AGENT,
                "installation_mode": Node.InstallationMode.SYSTEM,
                "status": Node.Status.UPGRADING,
                "availability": Node.Availability.ONLINE,
                "version": "0.2.27",
                "os_name": "Ubuntu 24.04",
                "ip_address": "192.0.2.142",
                "metadata": {"test_fixture": "upgrade_alignment"},
            },
        )
        now = timezone.now()
        task, _ = NodeTask.objects.update_or_create(
            organization=org,
            node=node,
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{node.id}",
            defaults={
                "requesting_organization_id": org.id,
                "kind": "agent.upgrade",
                "payload": {
                    "source_version": "0.2.27",
                    "target_version": "0.3.5",
                },
                "result": {
                    "last_progress": {
                        "phase": "download",
                        "download": {
                            "state": "downloading",
                            "downloaded_bytes": 8 * 1024 * 1024,
                            "total_bytes": 21_600_000,
                            "bytes_per_second": 5 * 1024 * 1024,
                            "elapsed_seconds": 11,
                            "attempt": 1,
                            "max_attempts": 3,
                        },
                    },
                },
                "status": NodeTask.Status.RUNNING,
                "dispatched_at": now,
                "watchdog_deadline_at": now + timedelta(hours=1),
                "last_progress_at": now,
            },
        )

        self.stdout.write(self.style.SUCCESS(
            "Upgrade alignment test data ready: "
            f"node_id={node.id}, task_id={task.id}, "
            f"host={node.name}, versions=0.2.27→0.3.5"
        ))
