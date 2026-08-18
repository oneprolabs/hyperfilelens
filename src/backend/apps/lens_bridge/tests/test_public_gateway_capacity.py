"""Per–Public Data Gateway capacity + occupancy metering."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from apps.lens_bridge.models import (
    LensGatewayLink,
    LensKnowledgeSource,
    LensSessionLink,
    LensWorkspaceBinding,
)
from apps.lens_bridge.services import platform_lens
from apps.lens_bridge.services.public_gateway_capacity import (
    assert_public_gateway_capacity,
    get_public_gateway_capacity_bytes,
    org_public_gateway_capacity_used_bytes,
    public_gateway_capacity_payload,
    set_public_gateway_capacity_bytes,
)
from apps.node.models import Node
from common.errors import AppError


def _make_platform_gateway(*, name: str, capacity_bytes: int = -1) -> LensGatewayLink:
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
        capacity_bytes=capacity_bytes,
    )


class PublicGatewayCapacityServiceTests(TestCase):
    def setUp(self):
        self.link_a = _make_platform_gateway(name="pg-a", capacity_bytes=-1)
        self.link_b = _make_platform_gateway(name="pg-b", capacity_bytes=-1)

    def test_default_unlimited(self):
        self.assertEqual(get_public_gateway_capacity_bytes(gateway_link=self.link_a), -1)
        payload = public_gateway_capacity_payload(gateway_link=self.link_a)
        self.assertTrue(payload["unlimited"])
        self.assertIsNone(payload["limit_bytes"])

    def test_set_and_read_capacity_is_per_gateway(self):
        set_public_gateway_capacity_bytes(
            gateway_link=self.link_a,
            capacity_bytes=10 * 1024**2,
        )
        self.link_a.refresh_from_db()
        self.assertEqual(
            get_public_gateway_capacity_bytes(gateway_link=self.link_a),
            10 * 1024**2,
        )
        self.assertEqual(get_public_gateway_capacity_bytes(gateway_link=self.link_b), -1)

    def test_rejects_invalid_capacity(self):
        with self.assertRaises(ValueError):
            set_public_gateway_capacity_bytes(gateway_link=self.link_a, capacity_bytes=-2)

        for invalid in (True, False, 1, 1.5, "1.5", 2**63):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError, OverflowError)):
                    set_public_gateway_capacity_bytes(
                        gateway_link=self.link_a,
                        capacity_bytes=invalid,
                    )

        self.link_a.refresh_from_db()
        self.assertEqual(self.link_a.capacity_bytes, -1)

    def test_zero_capacity_is_hard_empty_not_unlimited(self):
        set_public_gateway_capacity_bytes(gateway_link=self.link_a, capacity_bytes=0)
        self.link_a.refresh_from_db()
        self.assertEqual(get_public_gateway_capacity_bytes(gateway_link=self.link_a), 0)
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
        set_public_gateway_capacity_bytes(
            gateway_link=self.link_a,
            capacity_bytes=10 * 1024**3,
        )
        self.link_a.refresh_from_db()
        with self.assertRaises(AppError) as ctx:
            assert_public_gateway_capacity(
                gateway_link=self.link_a,
                additional_bytes=3 * 1024**3,
            )
        self.assertEqual(ctx.exception.code, "SUBSCRIPTION.QUOTA_EXCEEDED")
        self.assertEqual(
            ctx.exception.meta.get("quota_type"), "gateway.public_capacity_bytes"
        )

    @patch(
        "apps.lens_bridge.services.public_gateway_capacity.public_gateway_used_bytes",
        return_value=(1 * 1024**3, False),
    )
    def test_assert_allows_within_capacity(self, _mock_used):
        set_public_gateway_capacity_bytes(
            gateway_link=self.link_a,
            capacity_bytes=10 * 1024**3,
        )
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

    def test_corrupt_scope_summary_fails_closed_without_crashing(self):
        from apps.lens_bridge.services import public_gateway_capacity as cap
        from apps.protection.models import BackupSourceSnapshotDirectory

        directory = SimpleNamespace(
            source_path="/root",
            file_count=1,
            size_bytes=42,
        )
        with patch.object(
            BackupSourceSnapshotDirectory.objects,
            "filter",
        ) as directory_filter:
            directory_filter.return_value.first.return_value = directory
            nbytes, unknown = cap._occupancy_from_scope_dicts(
                organization_id=1,
                scopes=[
                    {
                        "source_path": "/root/file.txt",
                        "backup_snapshot_directory_id": 31,
                        "path_type": "file",
                        "file_count": 0,
                        "size_bytes": "not-a-number",
                    }
                ],
                re_resolve=False,
            )

        self.assertEqual(nbytes, 0)
        self.assertTrue(unknown)

    def test_negative_legacy_scope_size_fails_closed(self):
        from apps.lens_bridge.services import public_gateway_capacity as cap
        from apps.protection.models import BackupSourceSnapshotDirectory

        directory = SimpleNamespace(
            source_path="/root",
            file_count=1,
            size_bytes=42,
        )
        with patch.object(
            BackupSourceSnapshotDirectory.objects,
            "filter",
        ) as directory_filter:
            directory_filter.return_value.first.return_value = directory
            nbytes, unknown = cap._occupancy_from_scope_dicts(
                organization_id=1,
                scopes=[
                    {
                        "source_path": "/root/file.txt",
                        "backup_snapshot_directory_id": 31,
                        "path_type": "file",
                        "size_bytes": -1,
                    }
                ],
                re_resolve=False,
            )

        self.assertEqual(nbytes, 0)
        self.assertTrue(unknown)

    def test_fractional_scope_summary_fails_closed(self):
        from apps.lens_bridge.services import public_gateway_capacity as cap
        from apps.protection.models import BackupSourceSnapshotDirectory

        directory = SimpleNamespace(source_path="/root", file_count=1, size_bytes=42)
        with patch.object(
            BackupSourceSnapshotDirectory.objects,
            "filter",
        ) as directory_filter:
            directory_filter.return_value.first.return_value = directory
            nbytes, unknown = cap._occupancy_from_scope_dicts(
                organization_id=1,
                scopes=[
                    {
                        "source_path": "/root/file.txt",
                        "backup_snapshot_directory_id": 31,
                        "path_type": "file",
                        "file_count": 1.5,
                        "size_bytes": 42,
                    }
                ],
                re_resolve=False,
            )

        self.assertEqual(nbytes, 0)
        self.assertTrue(unknown)

    def test_scope_directory_is_resolved_within_the_session_organization(self):
        from apps.lens_bridge.services import public_gateway_capacity as cap
        from apps.protection.models import BackupSourceSnapshotDirectory

        with patch.object(
            BackupSourceSnapshotDirectory.objects,
            "filter",
        ) as directory_filter:
            directory_filter.return_value.first.return_value = None
            nbytes, unknown = cap._occupancy_from_scope_dicts(
                organization_id=17,
                scopes=[
                    {
                        "source_path": "/root/file.txt",
                        "backup_snapshot_directory_id": 31,
                        "path_type": "file",
                        "file_count": 1,
                        "size_bytes": 42,
                    }
                ],
                re_resolve=False,
            )

        directory_filter.assert_called_once_with(id=31, organization_id=17)
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
            capacity_reservation_status=(
                LensSessionLink.CapacityReservationStatus.RESERVED
            ),
            capacity_reserved_bytes=2 * gib,
        )
        used_bytes = org_public_gateway_capacity_used_bytes(organization_id=tenant.id)
        self.assertEqual(used_bytes, 2 * gib)

    @patch(
        "apps.lens_bridge.services.public_gateway_capacity._occupancy_from_scope_dicts",
        return_value=(2 * 1024**3, False),
    )
    def test_used_bytes_scoped_to_gateway_link(self, mock_occ):
        from apps.lens_bridge.services import public_gateway_capacity as cap

        gateway_qs = MagicMock()
        gateway_qs.values_list.return_value = [self.link_a.id, self.link_b.id]
        session_a = SimpleNamespace(
            gateway_link_id=self.link_a.id,
            capacity_reservation_status="reserved",
            capacity_reserved_bytes=2 * 1024**3,
        )
        session_b = SimpleNamespace(
            gateway_link_id=self.link_b.id,
            capacity_reservation_status="reserved",
            capacity_reserved_bytes=2 * 1024**3,
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
        mock_occ.assert_not_called()

    def test_unresolved_session_does_not_reserve_capacity(self):
        from apps.iam.models import Organization

        tenant = Organization.objects.create(
            key="cap-unresolved",
            name="Cap Unresolved",
        )
        user = User.objects.create_user(username="cap-unresolved@test.local")
        LensSessionLink.objects.create(
            organization=tenant,
            hfl_user=user,
            title="pending",
            gateway_link=self.link_a,
            source_scopes_json=[
                {
                    "source_path": "/root/nested",
                    "backup_snapshot_directory_id": 1,
                    "path_type": "unknown",
                }
            ],
            status=LensSessionLink.Status.ACTIVE,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            scope_resolution_status=LensSessionLink.ScopeResolutionStatus.PENDING,
            capacity_reservation_status=(
                LensSessionLink.CapacityReservationStatus.PENDING
            ),
        )

        self.assertEqual(
            org_public_gateway_capacity_used_bytes(organization_id=tenant.id),
            0,
        )

    def test_corrupt_negative_reservation_fails_closed(self):
        from apps.lens_bridge.services import public_gateway_capacity as cap

        used, unknown = cap.session_scope_occupancy(
            session=SimpleNamespace(
                capacity_reservation_status="reserved",
                capacity_reserved_bytes=-1,
            )
        )

        self.assertEqual(used, 0)
        self.assertTrue(unknown)

    def test_deleting_chat_keeps_its_reservation_until_cleanup_finishes(self):
        from apps.iam.models import Organization

        tenant = Organization.objects.create(
            key="cap-deleting",
            name="Cap Deleting",
        )
        user = User.objects.create_user(username="cap-deleting@test.local")
        gib = 1024**3
        LensSessionLink.objects.create(
            organization=tenant,
            hfl_user=user,
            title="deleting",
            gateway_link=self.link_a,
            status=LensSessionLink.Status.ARCHIVED,
            lifecycle_status=LensSessionLink.LifecycleStatus.DELETING,
            capacity_reservation_status=(
                LensSessionLink.CapacityReservationStatus.RESERVED
            ),
            capacity_reserved_bytes=2 * gib,
        )

        self.assertEqual(
            org_public_gateway_capacity_used_bytes(organization_id=tenant.id),
            2 * gib,
        )

    def test_provisioning_chat_reservation_is_not_double_counted_with_workspace(
        self,
    ):
        from apps.iam.models import Organization
        from apps.lens_bridge.services import public_gateway_capacity as cap

        tenant = Organization.objects.create(
            key="cap-transition",
            name="Cap Transition",
        )
        user = User.objects.create_user(username="cap-transition@test.local")
        gib = 1024**3
        knowledge_source = LensKnowledgeSource.objects.create(
            organization=tenant,
            name="Transitioning workspace",
            gateway=self.link_a.gateway,
            gateway_link=self.link_a,
            source_scopes_json=[
                {
                    "source_path": "/root",
                    "backup_snapshot_directory_id": 1,
                    "path_type": "file",
                    "file_count": 1,
                    "size_bytes": 2 * gib,
                }
            ],
            created_by=user,
        )
        LensWorkspaceBinding.objects.create(
            organization=tenant,
            knowledge_source=knowledge_source,
            gateway_link=self.link_a,
            execution_organization_id=self.link_a.organization_id,
            execution_node_id=self.link_a.gateway_id,
            workspace_kind=LensWorkspaceBinding.WorkspaceKind.MANAGED_RESTORE,
            workspace_root="/workspace",
            relative_path="hfl-ks-transition",
        )
        LensSessionLink.objects.create(
            organization=tenant,
            hfl_user=user,
            title="transitioning",
            gateway_link=self.link_a,
            knowledge_source=knowledge_source,
            status=LensSessionLink.Status.ACTIVE,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            capacity_reservation_status=(
                LensSessionLink.CapacityReservationStatus.RESERVED
            ),
            capacity_reserved_bytes=2 * gib,
        )

        with patch.object(
            cap,
            "_occupancy_from_scope_dicts",
            wraps=cap._occupancy_from_scope_dicts,
        ) as occupancy:
            used, unknown = cap.public_gateway_used_bytes(
                gateway_link_id=self.link_a.id,
            )

        self.assertEqual(used, 2 * gib)
        self.assertFalse(unknown)
        occupancy.assert_not_called()

    def test_reservation_on_another_gateway_does_not_hide_workspace_usage(self):
        from apps.iam.models import Organization
        from apps.lens_bridge.services import public_gateway_capacity as cap

        tenant = Organization.objects.create(
            key="cap-cross-gateway",
            name="Cap Cross Gateway",
        )
        user = User.objects.create_user(username="cap-cross-gateway@test.local")
        gib = 1024**3
        knowledge_source = LensKnowledgeSource.objects.create(
            organization=tenant,
            name="Gateway A workspace",
            gateway=self.link_a.gateway,
            gateway_link=self.link_a,
            source_scopes_json=[],
            created_by=user,
        )
        LensWorkspaceBinding.objects.create(
            organization=tenant,
            knowledge_source=knowledge_source,
            gateway_link=self.link_a,
            execution_organization_id=self.link_a.organization_id,
            execution_node_id=self.link_a.gateway_id,
            workspace_kind=LensWorkspaceBinding.WorkspaceKind.MANAGED_RESTORE,
            workspace_root="/workspace",
            relative_path="hfl-ks-cross-gateway",
        )
        LensSessionLink.objects.create(
            organization=tenant,
            hfl_user=user,
            title="mismatched reservation",
            gateway_link=self.link_b,
            knowledge_source=knowledge_source,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            capacity_reservation_status=(
                LensSessionLink.CapacityReservationStatus.RESERVED
            ),
            capacity_reserved_bytes=2 * gib,
        )

        with patch.object(
            cap,
            "_occupancy_from_scope_dicts",
            return_value=(3 * gib, False),
        ) as occupancy:
            used_map = cap.bulk_public_gateway_used_bytes(
                [self.link_a.id, self.link_b.id]
            )

        self.assertEqual(used_map[self.link_a.id][0], 3 * gib)
        self.assertEqual(used_map[self.link_b.id][0], 2 * gib)
        occupancy.assert_called_once()
