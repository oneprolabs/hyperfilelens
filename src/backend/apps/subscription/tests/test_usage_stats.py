"""Usage statistics tests."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.subscription.services.internal.usage import collect_usage_stats


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
