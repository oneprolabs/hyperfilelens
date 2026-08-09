"""Per–Public Data Gateway capacity + occupancy metering."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from apps.lens_bridge.models import LensGatewayLink, LensSessionLink
from apps.lens_bridge.services import platform_lens
from apps.lens_bridge.services.public_gateway_capacity import (
    assert_public_gateway_capacity,
    get_public_gateway_capacity_gb,
    org_public_gateway_capacity_used_gb,
    public_gateway_capacity_payload,
    set_public_gateway_capacity_gb,
)
from apps.node.models import Node
from common.errors import AppError


def _make_platform_gateway(*, name: str, capacity_gb: int = -1) -> LensGatewayLink:
    org = platform_lens.get_or_create_platform_org()
    node = Node.objects.create(
        organization=org,
        name=name,
        role=Node.Role.GATEWAY,
        status=Node.Status.ACTIVE,
        availability=Node.Availability.ONLINE,
    )
    return LensGatewayLink.objects.create(
        organization=org,
        gateway=node,
        scope=LensGatewayLink.GatewayScope.PLATFORM,
        origin=LensGatewayLink.Origin.PLATFORM,
        capacity_gb=capacity_gb,
    )


class PublicGatewayCapacityServiceTests(TestCase):
    def setUp(self):
        self.link_a = _make_platform_gateway(name="pg-a", capacity_gb=-1)
        self.link_b = _make_platform_gateway(name="pg-b", capacity_gb=-1)

    def test_default_unlimited(self):
        self.assertEqual(get_public_gateway_capacity_gb(gateway_link=self.link_a), -1)
        payload = public_gateway_capacity_payload(gateway_link=self.link_a)
        self.assertTrue(payload["unlimited"])
        self.assertIsNone(payload["limit_bytes"])

    def test_set_and_read_capacity_is_per_gateway(self):
        set_public_gateway_capacity_gb(gateway_link=self.link_a, capacity_gb=10)
        self.link_a.refresh_from_db()
        self.assertEqual(get_public_gateway_capacity_gb(gateway_link=self.link_a), 10)
        self.assertEqual(get_public_gateway_capacity_gb(gateway_link=self.link_b), -1)

    def test_rejects_invalid_capacity(self):
        with self.assertRaises(ValueError):
            set_public_gateway_capacity_gb(gateway_link=self.link_a, capacity_gb=-2)

    def test_zero_capacity_is_hard_empty_not_unlimited(self):
        set_public_gateway_capacity_gb(gateway_link=self.link_a, capacity_gb=0)
        self.link_a.refresh_from_db()
        self.assertEqual(get_public_gateway_capacity_gb(gateway_link=self.link_a), 0)
        payload = public_gateway_capacity_payload(gateway_link=self.link_a)
        self.assertFalse(payload["unlimited"])
        self.assertEqual(payload["limit_bytes"], 0)
        with self.assertRaises(AppError) as ctx:
            assert_public_gateway_capacity(
                gateway_link=self.link_a,
                additional_bytes=1,
            )
        self.assertEqual(ctx.exception.code, "SUBSCRIPTION.QUOTA_EXCEEDED")
        with self.assertRaises(AppError):
            assert_public_gateway_capacity(
                gateway_link=self.link_a,
                additional_bytes=0,
            )

    @patch(
        "apps.lens_bridge.services.public_gateway_capacity.public_gateway_used_bytes",
        return_value=(8 * 1024**3, False),
    )
    def test_assert_blocks_when_pool_full(self, _mock_used):
        set_public_gateway_capacity_gb(gateway_link=self.link_a, capacity_gb=10)
        self.link_a.refresh_from_db()
        with self.assertRaises(AppError) as ctx:
            assert_public_gateway_capacity(
                gateway_link=self.link_a,
                additional_bytes=3 * 1024**3,
            )
        self.assertEqual(ctx.exception.code, "SUBSCRIPTION.QUOTA_EXCEEDED")
        self.assertEqual(ctx.exception.meta.get("quota_type"), "gateway.public_capacity_gb")

    @patch(
        "apps.lens_bridge.services.public_gateway_capacity.public_gateway_used_bytes",
        return_value=(1 * 1024**3, False),
    )
    def test_assert_allows_within_capacity(self, _mock_used):
        set_public_gateway_capacity_gb(gateway_link=self.link_a, capacity_gb=10)
        self.link_a.refresh_from_db()
        assert_public_gateway_capacity(
            gateway_link=self.link_a,
            additional_bytes=2 * 1024**3,
        )

    def test_assert_unlimited_allows_unknown(self):
        assert_public_gateway_capacity(
            gateway_link=self.link_a,
            additional_bytes=100 * 1024**3,
            unknown_size=True,
        )

    def test_missing_directory_marks_occupancy_unknown(self):
        from apps.lens_bridge.services import public_gateway_capacity as cap

        nbytes, unknown = cap._occupancy_from_scope_dicts(
            organization_id=1,
            scopes=[
                {
                    "source_path": "/missing",
                    "backup_snapshot_directory_id": 99999999,
                    "path_type": "file",
                }
            ],
            re_resolve=False,
        )
        self.assertEqual(nbytes, 0)
        self.assertTrue(unknown)

    def test_org_usage_includes_provisioning_reservation(self):
        from apps.iam.models import Organization

        tenant = Organization.objects.create(key="cap-tenant", name="Cap Tenant")
        user = User.objects.create_user(
            username="cap@test.local",
            email="cap@test.local",
            password="x",
        )
        gib = 1024**3
        LensSessionLink.objects.create(
            organization=tenant,
            hfl_user=user,
            title="t",
            gateway_link=self.link_a,
            source_scopes_json=[
                {
                    "source_path": "/root",
                    "backup_snapshot_directory_id": 1,
                    "path_type": "file",
                    "size_bytes": 2 * gib,
                }
            ],
            status=LensSessionLink.Status.ACTIVE,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            knowledge_source=None,
        )
        with patch(
            "apps.lens_bridge.services.public_gateway_capacity._occupancy_from_scope_dicts",
            return_value=(2 * gib, False),
        ):
            used_gb = org_public_gateway_capacity_used_gb(organization_id=tenant.id)
        self.assertAlmostEqual(used_gb, 2.0, places=3)

    @patch(
        "apps.lens_bridge.services.public_gateway_capacity._occupancy_from_scope_dicts",
        return_value=(2 * 1024**3, False),
    )
    def test_used_bytes_scoped_to_gateway_link(self, mock_occ):
        from apps.lens_bridge.services import public_gateway_capacity as cap

        gateway_qs = MagicMock()
        gateway_qs.values_list.return_value = [self.link_a.id, self.link_b.id]
        session_a = SimpleNamespace(
            organization_id=5,
            gateway_link_id=self.link_a.id,
            source_scopes_json=[{"source_path": "/a", "backup_snapshot_directory_id": 1}],
        )
        session_b = SimpleNamespace(
            organization_id=5,
            gateway_link_id=self.link_b.id,
            source_scopes_json=[{"source_path": "/b", "backup_snapshot_directory_id": 2}],
        )
        session_qs = MagicMock()
        session_qs.only.return_value = [session_a, session_b]
        binding_qs = MagicMock()
        binding_qs.exclude.return_value.select_related.return_value = []

        with (
            patch(
                "apps.lens_bridge.services.platform_lens.platform_gateway_links",
                return_value=gateway_qs,
            ),
            patch(
                "apps.lens_bridge.models.LensWorkspaceBinding.objects.filter",
                return_value=binding_qs,
            ),
            patch(
                "apps.lens_bridge.models.LensSessionLink.objects.filter",
                return_value=session_qs,
            ),
        ):
            used_map = cap.bulk_public_gateway_used_bytes(
                [self.link_a.id, self.link_b.id]
            )

        self.assertEqual(used_map[self.link_a.id][0], 2 * 1024**3)
        self.assertEqual(used_map[self.link_b.id][0], 2 * 1024**3)
        self.assertEqual(mock_occ.call_count, 2)
