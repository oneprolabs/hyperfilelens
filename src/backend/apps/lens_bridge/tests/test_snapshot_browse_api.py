import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.lens_bridge.services import snapshot_scope_tasks
from apps.node.models import Node, NodeTask


class SnapshotBrowseApiTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="snapshot-browse-owner@example.test",
            email="snapshot-browse-owner@example.test",
        )
        self.organization, _ = provision_registered_user_tenant(self.owner)
        self.peer = get_user_model().objects.create_user(
            username="snapshot-browse-peer@example.test",
            email="snapshot-browse-peer@example.test",
        )
        Membership.objects.create(
            organization=self.organization,
            user=self.peer,
            role=Membership.Role.OPERATOR,
            is_active=True,
        )
        self.other_user = get_user_model().objects.create_user(
            username="snapshot-browse-other@example.test",
            email="snapshot-browse-other@example.test",
        )
        self.other_organization, _ = provision_registered_user_tenant(self.other_user)
        self.node = Node.objects.create(
            organization=self.organization,
            name="Insight browse Agent",
            role=Node.Role.AGENT,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    @staticmethod
    def _payload(response):
        body = response.json()
        return body.get("data", body)

    def _create_task(
        self,
        *,
        kind="lens.snapshot.browse",
        correlation_type=snapshot_scope_tasks.BROWSE_CORRELATION_TYPE,
        correlation_user=None,
    ):
        user = correlation_user or self.owner
        return NodeTask.objects.create(
            organization=self.organization,
            requesting_organization_id=self.organization.id,
            node=self.node,
            kind=kind,
            correlation_type=correlation_type,
            correlation_id=f"user:{user.id}:{uuid.uuid4()}",
            status=NodeTask.Status.SUCCESS,
            result={
                "has_more": True,
                "entries": [
                    {
                        "name": "reports",
                        "path": "reports",
                        "type": "dir",
                    }
                ]
            },
            watchdog_deadline_at=timezone.now() + timezone.timedelta(minutes=5),
        )

    @patch("apps.lens_bridge.services.snapshot_scope_tasks.dispatch_snapshot_browse")
    @patch("apps.iam.permissions_org.get_authz_provider")
    def test_operator_can_dispatch_browse_asynchronously(
        self,
        get_authz_provider,
        dispatch,
    ):
        task_id = uuid.uuid4()
        get_authz_provider.return_value = SimpleNamespace(
            get_org_role=lambda _user, _org_key: Membership.Role.OPERATOR,
        )
        dispatch.return_value = SimpleNamespace(
            id=task_id,
            status=NodeTask.Status.PENDING,
        )

        response = self.client.post(
            reverse("lens-copilot-snapshot-browse"),
            {
                "directory_id": 31,
                "backup_source_snapshot_id": 71,
                "gateway_link_id": 17,
                "path": "reports",
            },
            format="json",
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(self._payload(response)["task_id"], str(task_id))
        dispatch.assert_called_once()
        kwargs = dispatch.call_args.kwargs
        self.assertEqual(kwargs["organization_id"], self.organization.id)
        self.assertEqual(kwargs["directory_id"], 31)
        self.assertEqual(kwargs["backup_source_snapshot_id"], 71)
        self.assertEqual(kwargs["gateway_link_id"], 17)
        self.assertEqual(kwargs["requesting_user_id"], self.owner.id)
        self.assertEqual(kwargs["path"], "reports")
        self.assertEqual(kwargs["limit"], 500)
        self.assertTrue(kwargs["correlation_id"].startswith(f"user:{self.owner.id}:"))

    def test_owner_can_read_their_completed_browse_task(self):
        task = self._create_task()

        response = self.client.get(
            reverse(
                "lens-copilot-snapshot-browse-task",
                kwargs={"task_id": task.id},
            ),
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(response.status_code, 200)
        payload = self._payload(response)
        self.assertEqual(payload["status"], NodeTask.Status.SUCCESS)
        self.assertEqual(payload["entries"][0]["path"], "reports")
        self.assertIs(payload["has_more"], True)

    def test_tenant_can_read_task_executed_by_shared_platform_gateway(self):
        platform_org = Organization.objects.create(
            key="snapshot-browse-platform",
            name="Snapshot Browse Platform",
        )
        platform_gateway = Node.objects.create(
            organization=platform_org,
            name="Shared Insight gateway",
            role=Node.Role.GATEWAY,
        )
        task = NodeTask.objects.create(
            organization=platform_org,
            requesting_organization_id=self.organization.id,
            node=platform_gateway,
            kind="lens.snapshot.browse",
            correlation_type=snapshot_scope_tasks.BROWSE_CORRELATION_TYPE,
            correlation_id=f"user:{self.owner.id}:{uuid.uuid4()}",
            status=NodeTask.Status.SUCCESS,
            result={"entries": [], "has_more": False},
            watchdog_deadline_at=timezone.now() + timezone.timedelta(minutes=5),
        )

        response = self.client.get(
            reverse(
                "lens-copilot-snapshot-browse-task",
                kwargs={"task_id": task.id},
            ),
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._payload(response)["task_id"], str(task.id))

    def test_failed_task_does_not_expose_agent_diagnostics(self):
        task = self._create_task()
        task.status = NodeTask.Status.FAILED
        task.last_error = "repository /var/lib/hfl/private.config failed"
        task.result = {"config_file": "/var/lib/hfl/private.config"}
        task.save(update_fields=["status", "last_error", "result", "updated_at"])

        response = self.client.get(
            reverse(
                "lens-copilot-snapshot-browse-task",
                kwargs={"task_id": task.id},
            ),
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(response.status_code, 200)
        payload = self._payload(response)
        self.assertEqual(
            payload["error"],
            "Unable to browse the selected snapshot. Try again.",
        )
        self.assertEqual(payload["error_code"], "INSIGHT.SNAPSHOT_BROWSE_FAILED")
        self.assertIs(payload["retryable"], False)
        self.assertNotIn("entries", payload)
        self.assertNotIn("private.config", str(payload))

    def test_timeout_returns_structured_retryable_error(self):
        task = self._create_task()
        task.status = NodeTask.Status.TIMEOUT
        task.result = {}
        task.save(update_fields=["status", "result", "updated_at"])

        response = self.client.get(
            reverse(
                "lens-copilot-snapshot-browse-task",
                kwargs={"task_id": task.id},
            ),
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(response.status_code, 200)
        payload = self._payload(response)
        self.assertEqual(payload["error_code"], "INSIGHT.SNAPSHOT_BROWSE_TIMEOUT")
        self.assertIs(payload["retryable"], True)

    def test_same_organization_peer_cannot_read_another_users_task(self):
        task = self._create_task()
        self.client.force_authenticate(user=self.peer)

        response = self.client.get(
            reverse(
                "lens-copilot-snapshot-browse-task",
                kwargs={"task_id": task.id},
            ),
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(response.status_code, 404)

    def test_other_organization_cannot_read_the_task(self):
        task = self._create_task()
        self.client.force_authenticate(user=self.other_user)

        response = self.client.get(
            reverse(
                "lens-copilot-snapshot-browse-task",
                kwargs={"task_id": task.id},
            ),
            HTTP_X_ORG_KEY=self.other_organization.key,
        )

        self.assertEqual(response.status_code, 404)

    def test_non_insight_browse_task_is_not_exposed(self):
        task = self._create_task(correlation_type="protection.snapshot_browse")

        response = self.client.get(
            reverse(
                "lens-copilot-snapshot-browse-task",
                kwargs={"task_id": task.id},
            ),
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(response.status_code, 404)

    def test_non_browse_insight_task_is_not_exposed(self):
        task = self._create_task(kind="snapshot.browse")

        response = self.client.get(
            reverse(
                "lens-copilot-snapshot-browse-task",
                kwargs={"task_id": task.id},
            ),
            HTTP_X_ORG_KEY=self.organization.key,
        )

        self.assertEqual(response.status_code, 404)
