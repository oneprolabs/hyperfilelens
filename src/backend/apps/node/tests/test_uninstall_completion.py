"""Detached Agent uninstall completion callback tests."""

from __future__ import annotations

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Organization
from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.services.internal.uninstall_completion import (
    attach_uninstall_completion,
    complete_detached_uninstall,
)


class UninstallCompletionTests(TestCase):
    def setUp(self) -> None:
        self.org = Organization.objects.create(
            key="uninstall-completion-org",
            name="Uninstall Completion Org",
        )
        self.node = Node.objects.create(
            organization=self.org,
            name="uninstall-completion-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )

    def _task(self, *, force: bool = False) -> NodeTask:
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.uninstall",
            payload={"force_cleanup": force},
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=(
                timezone.now()
                + timezone.timedelta(
                    seconds=node_conf.LIFECYCLE_DETACHED_TIMEOUT_SECONDS,
                )
            ),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"remove:{self.node.id}",
        )
        return attach_uninstall_completion(task=task)

    def test_success_callback_is_authoritative_and_idempotent(self) -> None:
        task = self._task()
        token = task.payload["completion"]["token"]

        first = complete_detached_uninstall(
            token=token,
            cleanup_complete=True,
        )
        second = complete_detached_uninstall(
            token=token,
            cleanup_complete=True,
        )

        task.refresh_from_db()
        self.assertEqual(first.task_status, NodeTask.Status.SUCCESS)
        self.assertTrue(second.idempotent)
        self.assertTrue(task.result["cleanup_complete"])
        self.assertTrue(task.result["completion_received_at"])
        self.assertNotIn("token", task.payload["completion"])
        self.assertTrue(task.payload["completion"]["used_at"])

    def test_strict_failure_preserves_failed_lifecycle(self) -> None:
        task = self._task(force=False)

        outcome = complete_detached_uninstall(
            token=task.payload["completion"]["token"],
            cleanup_complete=False,
            cleanup_failures=[
                {"code": "mount_busy", "detail": "Managed mount is still active."}
            ],
            retained_resources=["/opt/hyperfilelens-agent/mounts/source-1"],
        )

        task.refresh_from_db()
        self.assertEqual(outcome.task_status, NodeTask.Status.FAILED)
        self.assertEqual(task.result["outcome"], "cleanup_failed")
        self.assertFalse(self.node.is_deleted)

    def test_force_failure_finishes_with_residue(self) -> None:
        task = self._task(force=True)

        outcome = complete_detached_uninstall(
            token=task.payload["completion"]["token"],
            cleanup_complete=False,
            cleanup_failures=[
                {"code": "sidecar_remove_failed", "detail": "LensNode remains."}
            ],
            retained_resources=["lensnode_sidecar"],
        )

        task.refresh_from_db()
        self.assertEqual(outcome.task_status, NodeTask.Status.SUCCESS)
        self.assertEqual(task.result["outcome"], "force_cleanup_success")
        self.assertFalse(task.result["cleanup_complete"])
        self.assertEqual(task.result["retained_resources"], ["lensnode_sidecar"])

    def test_callback_cannot_claim_complete_while_reporting_residue(self) -> None:
        task = self._task(force=False)

        outcome = complete_detached_uninstall(
            token=task.payload["completion"]["token"],
            cleanup_complete=True,
            cleanup_failures=[
                {
                    "code": "mount_busy",
                    "detail": "Managed mount is still active.",
                    "untrusted_extra": "must not be persisted",
                }
            ],
            retained_resources=["managed_mount"],
        )

        task.refresh_from_db()
        self.assertFalse(outcome.cleanup_complete)
        self.assertEqual(task.status, NodeTask.Status.FAILED)
        self.assertNotIn("untrusted_extra", task.result["cleanup_failures"][0])

    def test_callback_endpoint_rejects_invalid_token(self) -> None:
        response = APIClient().post(
            "/api/v1/node/agent-uninstall/completion/",
            {"token": "invalid", "cleanup_complete": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "invalid_uninstall_completion")
