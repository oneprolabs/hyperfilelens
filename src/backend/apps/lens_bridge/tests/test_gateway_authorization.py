from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.iam.models import Membership
from apps.iam.services.registration_service import provision_registered_user_tenant
from apps.lens_bridge.models import LensGatewayLink, LensKnowledgeSource
from apps.node.models import Node
from apps.node.models.base import NodeRole


class PrivateGatewayApiAuthorizationTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="private-gateway-owner@example.test",
            email="private-gateway-owner@example.test",
        )
        self.org, _ = provision_registered_user_tenant(self.owner)
        self.other_admin = get_user_model().objects.create_user(
            username="other-gateway-admin@example.test",
            email="other-gateway-admin@example.test",
        )
        Membership.objects.create(
            user=self.other_admin,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.gateway = Node.objects.create(
            organization=self.org,
            name="Private gateway",
            role=NodeRole.GATEWAY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )
        self.link = LensGatewayLink.objects.create(
            organization=self.org,
            gateway=self.gateway,
            owner_user=self.owner,
            scope=LensGatewayLink.GatewayScope.USER,
            origin=LensGatewayLink.Origin.USER,
            workspace_root=f"/workspace/org-{self.org.id}/data",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.other_admin)

    @mock.patch("apps.lens_bridge.services.provisioning.sl_client.request_json")
    def test_other_admin_cannot_enable_ai_or_receive_token(self, request_json):
        response = self.client.post(
            reverse(
                "lens-gateway-enable-ai",
                kwargs={"pk": self.gateway.id},
            ),
            {},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 400)
        request_json.assert_not_called()

    def test_other_admin_cannot_read_ai_status(self):
        response = self.client.get(
            reverse(
                "lens-gateway-ai-status",
                kwargs={"pk": self.gateway.id},
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 400)

    def test_other_admin_cannot_read_private_gateway_chat_workload(self):
        response = self.client.get(
            reverse(
                "lens-gateway-chat-workload",
                kwargs={"pk": self.gateway.id},
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 400)

    def test_owner_can_update_private_gateway_chat_workload(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.patch(
            reverse(
                "lens-gateway-chat-workload",
                kwargs={"pk": self.gateway.id},
            ),
            {
                "chat_prepare_concurrency": 2,
                "chat_queue_capacity": 20,
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        self.link.refresh_from_db()
        self.assertEqual(self.link.chat_prepare_concurrency, 2)
        self.assertEqual(self.link.chat_queue_capacity, 20)

    @mock.patch("apps.node.services.interface.run_agent_task_sync")
    def test_other_admin_cannot_browse_private_gateway(self, run_agent_task):
        response = self.client.get(
            reverse(
                "lens-gateway-browse",
                kwargs={"pk": self.gateway.id},
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 400)
        run_agent_task.assert_not_called()

    def test_cross_user_knowledge_source_request_never_persists_row(self):
        response = self.client.post(
            reverse("lens-knowledge-source-list"),
            {
                "name": "Unauthorized source",
                "gateway": self.gateway.id,
                "source_path": self.link.resolved_workspace_root(),
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            LensKnowledgeSource.objects.filter(
                organization=self.org,
                name="Unauthorized source",
            ).exists()
        )
