import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import (
    LensGatewayLink,
    LensKnowledgeSource,
    LensWorkspaceBinding,
)
from apps.lens_bridge.services import platform_lens
from apps.lens_bridge.services.gateway_execution import (
    context_for_gateway_link,
    context_for_workspace_binding,
    require_organization_gateway_link,
)
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.internal.node_workload import get_node_remove_blockers


class GatewayExecutionContextTests(TestCase):
    def setUp(self):
        self.tenant = Organization.objects.create(key="tenant-exec", name="Tenant")
        self.user = get_user_model().objects.create_user(
            username="gateway-exec@example.test",
            email="gateway-exec@example.test",
        )

    @mock.patch("apps.lens_bridge.services.gateway_execution.gateway_readiness.require_copilot_gateway")
    def test_platform_gateway_keeps_tenant_data_and_uses_platform_execution(self, _ready):
        platform_org = platform_lens.get_or_create_platform_org()
        node = Node.objects.create(
            organization=platform_org,
            name="shared-platform-gateway",
            role=NodeRole.GATEWAY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )
        link = LensGatewayLink.objects.create(
            organization=platform_org,
            gateway=node,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
            workspace_root="/workspace/org-platform/data",
        )

        context = context_for_gateway_link(
            tenant_organization=self.tenant,
            gateway_link=link,
        )

        self.assertEqual(context.tenant_organization, self.tenant)
        self.assertEqual(context.execution_organization, platform_org)
        self.assertTrue(context.is_platform)

    @mock.patch("apps.lens_bridge.services.gateway_execution.gateway_readiness.require_copilot_gateway")
    def test_private_gateway_cannot_cross_tenant_boundary(self, _ready):
        other_org = Organization.objects.create(key="other-exec", name="Other")
        node = Node.objects.create(
            organization=other_org,
            name="other-private-gateway",
            role=NodeRole.GATEWAY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )
        link = LensGatewayLink.objects.create(
            organization=other_org,
            gateway=node,
            created_by=self.user,
            scope=LensGatewayLink.GatewayScope.ORGANIZATION,
        )

        with self.assertRaises(ValidationError):
            context_for_gateway_link(
                tenant_organization=self.tenant,
                gateway_link=link,
            )

    @mock.patch("apps.lens_bridge.services.gateway_execution.gateway_readiness.require_copilot_gateway")
    def test_private_gateway_is_shared_inside_organization(self, _ready):
        node = Node.objects.create(
            organization=self.tenant,
            name="private-user-gateway",
            role=NodeRole.GATEWAY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )
        link = LensGatewayLink.objects.create(
            organization=self.tenant,
            gateway=node,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
        )
        context = context_for_gateway_link(
            tenant_organization=self.tenant,
            gateway_link=link,
        )
        resolved = require_organization_gateway_link(
            tenant_organization=self.tenant,
            gateway_id=node.id,
        )

        self.assertEqual(context.gateway_link, link)
        self.assertEqual(resolved, link)

    @mock.patch(
        "apps.lens_bridge.services.gateway_execution.gateway_readiness.require_copilot_gateway"
    )
    def test_peer_can_select_organization_gateway_for_chat(self, _ready):
        node = Node.objects.create(
            organization=self.tenant,
            name="organization-chat-gateway",
            role=NodeRole.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        link = LensGatewayLink.objects.create(
            organization=self.tenant,
            gateway=node,
            owner_user=self.user,
            created_by=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
            sl_lensnode_uuid=uuid.uuid4(),
        )
        peer = get_user_model().objects.create_user(
            username="organization-gateway-peer@example.test",
            email="organization-gateway-peer@example.test",
        )

        selected = platform_lens.resolve_gateway_link_for_copilot(
            self.tenant,
            user=peer,
            gateway_link_id=link.id,
        )

        self.assertEqual(selected, link)

    def test_platform_gateway_removal_sees_tenant_knowledge_sources(self):
        platform_org = platform_lens.get_or_create_platform_org()
        node = Node.objects.create(
            organization=platform_org,
            name="platform-gateway-with-tenant-ks",
            role=NodeRole.GATEWAY,
        )
        link = LensGatewayLink.objects.create(
            organization=platform_org,
            gateway=node,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            origin=LensGatewayLink.Origin.PLATFORM,
        )
        LensKnowledgeSource.objects.create(
            organization=self.tenant,
            name="Tenant workspace",
            gateway=node,
            gateway_link=link,
            source_path="/backup/data",
        )

        blockers = get_node_remove_blockers(node=node)

        self.assertIn("knowledge_source_bound", {blocker.code for blocker in blockers})

    def test_cleanup_context_accepts_failed_identity_without_relaxing_restore(self):
        node = Node.objects.create(
            organization=self.tenant,
            name="failed-prepare-gateway",
            role=NodeRole.GATEWAY,
        )
        link = LensGatewayLink.objects.create(
            organization=self.tenant,
            gateway=node,
            owner_user=self.user,
            scope=LensGatewayLink.GatewayScope.USER,
            workspace_root="/workspace/tenant/data",
        )
        knowledge_source = LensKnowledgeSource.objects.create(
            organization=self.tenant,
            name="Failed managed workspace",
            gateway=node,
            gateway_link=link,
            source_path="/source/data",
            created_by=self.user,
        )
        binding = LensWorkspaceBinding.objects.create(
            organization=self.tenant,
            knowledge_source=knowledge_source,
            gateway_link=link,
            execution_organization_id=self.tenant.id,
            execution_node_id=node.id,
            workspace_kind=LensWorkspaceBinding.WorkspaceKind.MANAGED_RESTORE,
            workspace_root="/workspace/tenant/data",
            relative_path="tenants/1/knowledge-sources/failed",
            state=LensWorkspaceBinding.State.ERROR,
            identity_status=LensWorkspaceBinding.IdentityStatus.ERROR,
        )

        with self.assertRaises(ValidationError):
            context_for_workspace_binding(
                tenant_organization=self.tenant,
                workspace_binding_id=binding.id,
                require_ready=False,
            )

        context, resolved = context_for_workspace_binding(
            tenant_organization=self.tenant,
            workspace_binding_id=binding.id,
            require_ready=False,
            allow_deleting=True,
        )

        self.assertEqual(context.gateway.id, node.id)
        self.assertEqual(resolved.id, binding.id)
