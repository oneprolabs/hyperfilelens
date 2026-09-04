from unittest import mock
import uuid

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
            created_by=self.owner,
            scope=LensGatewayLink.GatewayScope.USER,
            origin=LensGatewayLink.Origin.USER,
            workspace_root=f"/workspace/org-{self.org.id}/data",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.other_admin)

    @mock.patch("apps.lens_bridge.services.provisioning.enable_ai_on_gateway")
    def test_other_admin_can_enable_existing_organization_gateway(self, enable_ai):
        enable_ai.return_value = self.link
        response = self.client.post(
            reverse(
                "lens-gateway-enable-ai",
                kwargs={"pk": self.gateway.id},
            ),
            {},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 201)
        enable_ai.assert_called_once_with(
            org=self.org,
            gateway=self.gateway,
            name=None,
            created_by=self.other_admin,
            scope=LensGatewayLink.GatewayScope.ORGANIZATION,
        )

    def test_other_admin_can_read_ai_status(self):
        response = self.client.get(
            reverse(
                "lens-gateway-ai-status",
                kwargs={"pk": self.gateway.id},
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)

    def test_other_admin_can_read_private_gateway_chat_workload(self):
        response = self.client.get(
            reverse(
                "lens-gateway-chat-workload",
                kwargs={"pk": self.gateway.id},
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)

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

    @mock.patch("apps.lens_bridge.services.provisioning.browse_gateway_directory")
    def test_other_admin_can_browse_private_gateway(self, browse_gateway_directory):
        browse_gateway_directory.return_value = {"path": "/", "entries": []}
        response = self.client.get(
            reverse(
                "lens-gateway-browse",
                kwargs={"pk": self.gateway.id},
            ),
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 200)
        browse_gateway_directory.assert_called_once_with(
            org=self.org,
            gateway_id=self.gateway.id,
            path="",
            expected_scope=LensGatewayLink.GatewayScope.ORGANIZATION,
        )

    @mock.patch(
        "apps.lens_bridge.services.knowledge_source_sync.prepare_new_knowledge_source"
    )
    @mock.patch(
        "apps.lens_bridge.services.gateway_execution.gateway_readiness.require_copilot_gateway"
    )
    @mock.patch(
        "apps.lens_bridge.api.serializers.gateway_readiness.require_hfl_usable_gateway"
    )
    def test_peer_can_create_knowledge_source_on_organization_gateway(
        self,
        _usable,
        _copilot_ready,
        prepare_knowledge_source,
    ):
        self.link.sl_lensnode_uuid = uuid.uuid4()
        self.link.save(update_fields=["sl_lensnode_uuid", "updated_at"])
        prepare_knowledge_source.side_effect = lambda *, org, ks: ks
        response = self.client.post(
            reverse("lens-knowledge-source-list"),
            {
                "name": "Organization source",
                "gateway": self.gateway.id,
                "source_path": self.link.resolved_workspace_root(),
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            LensKnowledgeSource.objects.filter(
                organization=self.org,
                name="Organization source",
                created_by=self.other_admin,
            ).exists()
        )
