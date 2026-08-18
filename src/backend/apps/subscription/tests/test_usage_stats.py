"""Usage statistics tests."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.storage.repositories.models import Repository
from apps.subscription.services.internal.usage import (
    collect_instance_meter_usage,
    collect_instance_usage_stats,
    collect_meter_usage,
    collect_usage_stats,
)


class UsageStatsTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="usage@test.local",
            email="usage@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(key="usage-test-org", name="Usage Test Org")
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )

    def test_collect_usage_counts_nodes_by_role(self):
        Node.objects.create(
            organization=self.org,
            name="agent-1",
            role=NodeRole.AGENT,
            status="online",
        )
        Node.objects.create(
            organization=self.org,
            name="proxy-1",
            role=NodeRole.PROXY,
            status="online",
        )
        Node.objects.create(
            organization=self.org,
            name="gateway-1",
            role=NodeRole.GATEWAY,
            status="online",
        )

        usage = collect_usage_stats(organization_id=self.org.id)

        self.assertEqual(usage["nodes_count"], 3)
        self.assertEqual(usage["agents_count"], 1)
        self.assertEqual(usage["proxies_count"], 1)
        self.assertEqual(usage["gateways_count"], 1)

    def test_collect_usage_sums_ai_tokens_from_ledger(self):
        import uuid

        from django.utils import timezone

        from apps.lens_bridge.models import LensUsageLedger

        LensUsageLedger.objects.create(
            organization=self.org,
            hfl_user=self.user,
            sl_user_id=1,
            sl_run_uuid=uuid.uuid4(),
            total_tokens=1200,
            occurred_at=timezone.now(),
        )
        LensUsageLedger.objects.create(
            organization=self.org,
            hfl_user=self.user,
            sl_user_id=1,
            sl_run_uuid=uuid.uuid4(),
            total_tokens=800,
            occurred_at=timezone.now(),
        )

        usage = collect_usage_stats(organization_id=self.org.id)
        self.assertEqual(usage["ai_tokens_used"], 2000)
        self.assertEqual(usage["ai_requests_used"], 2000)

    def test_collect_instance_usage_excludes_internal_platform_org(self):
        customer_org = Organization.objects.create(
            key="usage-customer-two",
            name="Usage Customer Two",
        )
        customer_user = get_user_model().objects.create_user(
            username="usage-two@test.local",
            email="usage-two@test.local",
            password="test-pass",
        )
        Membership.objects.create(
            user=customer_user,
            organization=customer_org,
            role=Membership.Role.ADMIN,
        )
        Node.objects.create(
            organization=customer_org,
            name="customer-agent",
            role=NodeRole.AGENT,
            status="online",
        )
        platform_org = Organization.objects.create(
            key="__platform_lens__",
            name="Platform Lens",
        )
        Membership.objects.create(
            user=self.user,
            organization=platform_org,
            role=Membership.Role.ADMIN,
        )
        Node.objects.create(
            organization=platform_org,
            name="platform-agent",
            role=NodeRole.AGENT,
            status="online",
        )
        Repository.objects.create(
            organization_id=customer_org.id,
            name="customer-object-storage",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            estimated_usage_bytes=2 * 1024**3,
            usage_probe_status=Repository.MetricProbeStatus.SUCCESS,
        )
        Repository.objects.create(
            organization_id=platform_org.id,
            name="platform-object-storage",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            estimated_usage_bytes=3 * 1024**3,
            usage_probe_status=Repository.MetricProbeStatus.SUCCESS,
        )

        usage = collect_instance_usage_stats()

        self.assertEqual(usage["organizations_count"], 2)
        self.assertEqual(usage["users_count"], 2)
        self.assertEqual(usage["agents_count"], 1)
        self.assertEqual(usage["object_storage_count"], 1)
        self.assertEqual(usage["storage_used_gb"], 2)

    def test_user_meter_uses_one_direct_query_at_org_and_instance_scope(self):
        second_org = Organization.objects.create(
            key="usage-query-count-two",
            name="Usage Query Count Two",
        )
        second_user = get_user_model().objects.create_user(
            username="usage-query-two@test.local",
            email="usage-query-two@test.local",
            password="test-pass",
        )
        Membership.objects.create(
            user=second_user,
            organization=second_org,
            role=Membership.Role.ADMIN,
        )

        with self.assertNumQueries(1):
            org_used = collect_meter_usage(
                organization_id=self.org.id,
                usage_key="users_count",
            )
        with self.assertNumQueries(1):
            instance_used = collect_instance_meter_usage(usage_key="users_count")

        self.assertEqual(org_used, 1)
        self.assertEqual(instance_used, 2)

    def test_unknown_direct_meter_fails_closed(self):
        with self.assertRaises(ValueError):
            collect_instance_meter_usage(usage_key="unknown_meter")

    @patch(
        "apps.lens_bridge.services.public_gateway_capacity."
        "org_public_gateway_used_bytes",
        return_value=(1024, True),
    )
    @patch(
        "apps.lens_bridge.services.public_gateway_capacity."
        "bulk_public_gateway_used_bytes",
        return_value={1: (1024, True)},
    )
    def test_incomplete_public_gateway_meter_fails_closed(
        self,
        _bulk_used,
        _org_used,
    ):
        with self.assertRaises(RuntimeError):
            collect_meter_usage(
                organization_id=self.org.id,
                usage_key="public_gateway_capacity_used_bytes",
            )
        with self.assertRaises(RuntimeError):
            collect_instance_meter_usage(
                usage_key="public_gateway_capacity_used_bytes",
            )
