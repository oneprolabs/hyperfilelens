from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.node.models import Node, NodeTask, NodeToken
from apps.node.models.base import NodeRole
from apps.task.models import Task, TaskResource

User = get_user_model()


class NodeApiOrgScopingTests(APITestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            username="node-scope-a@test.local",
            email="node-scope-a@test.local",
            password="Pass1234",
            is_active=True,
        )
        self.org_a, _ = provision_registered_user_tenant(self.user_a)

        self.user_b = User.objects.create_user(
            username="node-scope-b@test.local",
            email="node-scope-b@test.local",
            password="Pass1234",
            is_active=True,
        )
        self.org_b, _ = provision_registered_user_tenant(self.user_b)

        self.node_a = Node.objects.create(
            organization=self.org_a,
            name="agent-a",
            role=NodeRole.AGENT,
        )
        self.node_b = Node.objects.create(
            organization=self.org_b,
            name="agent-b",
            role=NodeRole.AGENT,
        )

    def test_node_list_only_includes_retrievable_nodes(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get(
            reverse("node-list"),
            HTTP_X_ORG_KEY=self.org_a.key,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["results"] if isinstance(response.data, dict) else response.data
        node_ids = [item["id"] for item in rows]
        self.assertEqual(node_ids, [self.node_a.id])
        self.assertNotIn(self.node_b.id, node_ids)

        detail = self.client.get(
            reverse("node-detail", kwargs={"pk": self.node_a.id}),
            HTTP_X_ORG_KEY=self.org_a.key,
        )
        self.assertEqual(detail.status_code, status.HTTP_200_OK)

    def test_node_display_name_can_change_while_backup_is_active(self):
        backup_task = Task.objects.create(
            organization_id=self.org_a.id,
            task_type=Task.Type.BACKUP,
            display_name="Active backup",
            status=Task.Status.RUNNING,
        )
        TaskResource.objects.create(
            task=backup_task,
            resource_type=TaskResource.Type.BACKUP_SOURCE,
            resource_subtype="agent",
            resource_id=self.node_a.id,
            is_primary=True,
        )
        self.client.force_authenticate(user=self.user_a)

        response = self.client.patch(
            reverse("node-detail", kwargs={"pk": self.node_a.id}),
            {"name": "agent-a-renamed"},
            format="json",
            HTTP_X_ORG_KEY=self.org_a.key,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.node_a.refresh_from_db()
        self.assertEqual(self.node_a.name, "agent-a-renamed")

    def test_node_list_is_scoped_to_x_org_key(self):
        self.client.force_authenticate(user=self.user_a)

        response = self.client.get(
            reverse("node-list"),
            HTTP_X_ORG_KEY=self.org_a.key,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["results"] if isinstance(response.data, dict) else response.data
        node_ids = [item["id"] for item in rows]
        self.assertEqual(node_ids, [self.node_a.id])
        self.assertNotIn(self.node_b.id, node_ids)

    def test_node_list_requires_org_context(self):
        self.client.force_authenticate(user=self.user_a)

        response = self.client.get(reverse("node-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["data"]["code"], "AUTH.FORBIDDEN")

    def test_node_list_rejects_foreign_org_key(self):
        self.client.force_authenticate(user=self.user_a)

        response = self.client.get(
            reverse("node-list"),
            HTTP_X_ORG_KEY=self.org_b.key,
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["data"]["code"], "AUTH.FORBIDDEN")

    def test_node_detail_cross_tenant_returns_404(self):
        self.client.force_authenticate(user=self.user_a)

        response = self.client.get(
            reverse("node-detail", kwargs={"pk": self.node_b.id}),
            HTTP_X_ORG_KEY=self.org_a.key,
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_node_token_create_uses_active_org(self):
        self.client.force_authenticate(user=self.user_a)

        response = self.client.post(
            reverse("node-token-list"),
            {"role": NodeRole.AGENT, "note": "scope-test"},
            format="json",
            HTTP_X_ORG_KEY=self.org_a.key,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        token_id = response.data["id"]
        row = NodeToken.objects.get(id=token_id)
        self.assertEqual(row.organization_id, self.org_a.id)

    def test_node_token_create_rejects_body_org_mismatch(self):
        self.client.force_authenticate(user=self.user_a)

        response = self.client.post(
            reverse("node-token-list"),
            {"role": NodeRole.AGENT, "org": self.org_b.key},
            format="json",
            HTTP_X_ORG_KEY=self.org_a.key,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        payload = response.data.get("data", response.data)
        self.assertIn(
            "org",
            {item.get("field") for item in payload.get("errors", [])},
        )

    def test_enrollment_token_create_rejects_body_org_mismatch(self):
        self.client.force_authenticate(user=self.user_a)

        response = self.client.post(
            reverse("enrollment-token-create"),
            {"role": NodeRole.AGENT, "org": self.org_b.key},
            format="json",
            HTTP_X_ORG_KEY=self.org_a.key,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        payload = response.data.get("data", response.data)
        self.assertIn(
            "org",
            {item.get("field") for item in payload.get("errors", [])},
        )
        self.assertFalse(
            NodeToken.objects.filter(organization=self.org_b, role=NodeRole.AGENT).exists()
        )

    def test_node_task_wait_does_not_recurse_on_get_queryset(self):
        self.client.force_authenticate(user=self.user_a)
        task = NodeTask.objects.create(
            organization=self.org_a,
            node=self.node_a,
            kind="agent.upgrade",
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now(),
        )

        response = self.client.get(
            reverse("node-task-wait", kwargs={"pk": task.id}),
            {"timeout": 1},
            HTTP_X_ORG_KEY=self.org_a.key,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["task_id"], str(task.id))
        self.assertIn("status", response.data)
        self.assertIn("timed_out", response.data)
