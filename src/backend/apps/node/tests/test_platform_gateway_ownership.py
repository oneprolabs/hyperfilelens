"""Platform infrastructure has no fabricated tenant or implicit tenant access."""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Membership, Organization
from apps.iam.services.registration_service import (
    organization_key_available,
    unique_org_key_for_name,
)
from apps.lens_bridge.models import LensGatewayLink
from apps.node.models import Node, NodeTask
from apps.node.api.serializers.node import NodeSerializer
from apps.node.selectors.internal.node_query import node_by_id
from apps.node.selectors.internal.node_task_query import (
    node_task_by_correlation,
    node_task_by_id_for_requesting_org,
)
from apps.node.services.internal.agent_task import run_agent_task_async
from apps.node.services.internal.local_platform_gateway import (
    LOCAL_PLATFORM_GATEWAY_METADATA,
    ensure_local_platform_gateway_token,
)
from apps.node.services.internal.task import create_agent_task
from apps.node.services.internal.transport_identity import PLATFORM_TRANSPORT_NAMESPACE


class PlatformGatewayOwnershipTests(TestCase):
    def gateway(self, **kwargs):
        return Node.objects.create(
            organization=None, scope=Node.Scope.PLATFORM,
            role=Node.Role.GATEWAY, name="Public Gateway", **kwargs,
        )

    def test_gateway_bootstrap_does_not_create_an_internal_organization(self):
        token = ensure_local_platform_gateway_token()
        gateway = self.gateway()
        LensGatewayLink.objects.create(
            organization=None, gateway=gateway, scope=LensGatewayLink.GatewayScope.PLATFORM,
        )
        self.assertEqual(Organization.objects.count(), 0)
        self.assertIsNone(token.organization_id)
        self.assertEqual(token.transport_key, PLATFORM_TRANSPORT_NAMESPACE)

        call_command("seed_initial_data", stdout=StringIO())

        self.assertEqual(Organization.objects.count(), 1)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(Membership.objects.count(), 1)
        self.assertEqual(Organization.objects.get().name, "HyperFileLens")
        self.assertFalse(Organization.objects.filter(key="__platform_lens__").exists())

    def test_new_local_token_preserves_the_installed_legacy_namespace(self):
        self.gateway(
            transport_namespace="legacy-installed-namespace",
            metadata=dict(LOCAL_PLATFORM_GATEWAY_METADATA),
        )
        token = ensure_local_platform_gateway_token()
        self.assertEqual(token.transport_key, "legacy-installed-namespace")
        self.assertEqual(Organization.objects.count(), 0)

    def test_tenant_key_allocation_cannot_shadow_an_installed_namespace(self):
        self.gateway(transport_namespace="old-route")
        self.assertFalse(organization_key_available(PLATFORM_TRANSPORT_NAMESPACE))
        self.assertFalse(organization_key_available("old-route"))
        self.assertEqual(unique_org_key_for_name("Old Route"), "old-route-1")
        self.assertEqual(Organization.objects.count(), 0)

    def test_missing_owner_is_not_an_unrestricted_node_query_or_dispatch(self):
        tenant = Organization.objects.create(key="tenant", name="Tenant")
        private = Node.objects.create(organization=tenant, role="gateway", name="Private")
        public = self.gateway()
        self.assertIsNone(node_by_id(org=None, node_id=private.pk))
        self.assertIsNone(node_by_id(org=tenant, node_id=public.pk))
        with self.assertRaises(ValueError):
            run_agent_task_async(node_id=public.pk, kind="agent.upgrade")
        with self.assertRaises(ValueError):
            create_agent_task(org=None, node=private, kind="agent.upgrade")
        self.assertEqual(NodeTask.objects.count(), 0)

    def test_console_payload_cannot_change_node_ownership_or_transport_identity(self):
        tenant = Organization.objects.create(key="tenant", name="Tenant")
        private = Node.objects.create(organization=tenant, role="gateway", name="Private")
        serializer = NodeSerializer(
            private, data={
                "organization": None, "scope": "platform",
                "transport_namespace": PLATFORM_TRANSPORT_NAMESPACE,
                "transport_key": PLATFORM_TRANSPORT_NAMESPACE,
                "name": "Renamed",
            }, partial=True,
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("organization", serializer.errors)
        serializer = NodeSerializer(
            private, data={
                "scope": "platform",
                "transport_namespace": PLATFORM_TRANSPORT_NAMESPACE,
                "transport_key": PLATFORM_TRANSPORT_NAMESPACE,
                "name": "Renamed",
            }, partial=True,
        )
        serializer.is_valid(raise_exception=True)
        self.assertEqual(serializer.validated_data, {"name": "Renamed"})
        serializer.save()
        private.refresh_from_db()
        self.assertEqual(private.organization_id, tenant.pk)
        self.assertEqual(private.scope, Node.Scope.ORGANIZATION)
        self.assertEqual(private.transport_namespace, "")

    def test_shared_business_tasks_keep_requesting_tenant_and_maintenance_has_none(self):
        tenant = Organization.objects.create(key="tenant", name="Tenant")
        other = Organization.objects.create(key="other", name="Other")
        public = self.gateway()
        with self.assertRaises(ValueError):
            create_agent_task(org=None, node=public, kind="lens.ks.prepare")
        business = create_agent_task(
            org=None, node=public, kind="lens.ks.prepare",
            requesting_organization_id=tenant.pk,
        )
        maintenance = create_agent_task(org=None, node=public, kind="agent.upgrade")
        self.assertIsNone(business.organization_id)
        self.assertEqual(business.requesting_organization_id, tenant.pk)
        self.assertIsNone(maintenance.organization_id)
        self.assertIsNone(maintenance.requesting_organization_id)
        self.assertEqual(
            node_task_by_id_for_requesting_org(org=tenant, task_id=business.pk).pk,
            business.pk,
        )
        self.assertIsNone(node_task_by_id_for_requesting_org(org=other, task_id=business.pk))
        self.assertIsNone(node_task_by_id_for_requesting_org(org=tenant, task_id=maintenance.pk))
        with self.assertRaises(ValueError):
            node_task_by_id_for_requesting_org(org=None, task_id=maintenance.pk)


class NodeTaskCorrelationOwnershipTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Organization.objects.create(key="tenant", name="Tenant")
        self.other = Organization.objects.create(key="other", name="Other")
        self.private = Node.objects.create(
            organization=self.tenant, role="gateway", name="Private",
        )
        self.foreign = Node.objects.create(
            organization=self.other, role="gateway", name="Foreign",
        )
        self.public = Node.objects.create(
            organization=None, scope=Node.Scope.PLATFORM,
            role="gateway", name="Public",
        )

    def task(
        self,
        node: Node,
        *,
        status: str = NodeTask.Status.PENDING,
        correlation_type: str = "node.lifecycle",
        correlation_id: str = "shared-operation",
    ) -> NodeTask:
        return NodeTask.objects.create(
            organization=node.organization, node=node, kind="agent.upgrade",
            status=status, correlation_type=correlation_type,
            correlation_id=correlation_id, watchdog_deadline_at=timezone.now(),
        )

    def test_platform_lookup_does_not_fall_back_to_a_tenant_task(self) -> None:
        self.task(self.private)
        for active_only in (False, True):
            with self.subTest(active_only=active_only):
                self.assertIsNone(node_task_by_correlation(
                    org=None, correlation_type="node.lifecycle",
                    correlation_id="shared-operation", active_only=active_only,
                ))
        self.assertIsNone(node_task_by_correlation(
            org=self.other, correlation_type="node.lifecycle",
            correlation_id="shared-operation",
        ))

    def test_same_correlation_keeps_platform_and_tenants_isolated(self) -> None:
        platform = self.task(self.public)
        tenant = self.task(self.private)
        foreign = self.task(self.foreign)
        for owner, expected in (
            (None, platform), (self.tenant, tenant), (self.other, foreign),
        ):
            for active_only in (False, True):
                with self.subTest(owner=owner, active_only=active_only):
                    actual = node_task_by_correlation(
                        org=owner, correlation_type="node.lifecycle",
                        correlation_id="shared-operation",
                        active_only=active_only,
                    )
                    self.assertEqual(actual.pk, expected.pk)

    def test_platform_lookup_preserves_status_and_correlation_filters(self) -> None:
        completed = self.task(self.public, status=NodeTask.Status.SUCCESS)
        self.task(self.private)
        self.task(self.public, correlation_type="another.lifecycle")
        self.task(self.public, correlation_id="another-operation")
        self.assertIsNone(node_task_by_correlation(
            org=None, correlation_type="node.lifecycle",
            correlation_id="shared-operation", active_only=True,
        ))
        self.assertEqual(node_task_by_correlation(
            org=None, correlation_type="node.lifecycle",
            correlation_id="shared-operation",
        ).pk, completed.pk)
        running = self.task(self.public, status=NodeTask.Status.RUNNING)
        self.assertEqual(node_task_by_correlation(
            org=None, correlation_type="node.lifecycle",
            correlation_id="shared-operation", active_only=True,
        ).pk, running.pk)
