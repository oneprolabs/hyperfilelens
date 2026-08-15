from unittest import mock

from django.test import TestCase

from apps.storage.repositories.models import (
    Repository,
    RepositoryLocationClaim,
    RepositoryLocationNamespace,
)
from apps.storage.services.internal.repository_location import (
    RepositoryLocationConflict,
    mark_repository_location_initialization_failed,
    mark_repository_location_initializing,
    mark_repository_location_owned,
    mark_repository_location_residual,
    release_repository_location,
    repository_location_spec,
    resolve_s3_repository_namespace,
    reserve_direct_nas_location,
    reserve_repository_location,
)
from apps.storage.services.internal.repository_ownership import (
    RepositoryOwnershipError,
    claim_s3_repository_ownership,
    verify_s3_repository_ownership,
)


class RepositoryLocationClaimTests(TestCase):
    def _s3_repository(
        self,
        *,
        name: str,
        prefix: str,
        access_key_id: str = "account-a",
        organization_id: int = 1,
    ) -> Repository:
        return Repository.objects.create(
            organization_id=organization_id,
            name=name,
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATING,
            health=Repository.Health.OFFLINE,
            s3_platform=Repository.S3Platform.HUAWEICLOUD,
            s3_bucket="shared-bucket",
            config={
                "endpoint": "obs.cn-north-4.myhuaweicloud.com",
                "region": "cn-north-4",
                "prefix": prefix,
                "access_key_id": access_key_id,
            },
        )

    def _direct_nas_repository(self, *, name: str) -> Repository:
        return Repository.objects.create(
            organization_id=1,
            name=name,
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            nas_protocol=Repository.NasProtocol.NFS,
            config={
                "server_address": "nas.example.test",
                "share_path": "/backup",
            },
        )

    def test_rejects_exact_and_nested_s3_roots_for_same_account(self):
        first = self._s3_repository(name="Primary", prefix="hfl/")
        reserve_repository_location(first)

        exact = self._s3_repository(name="Exact", prefix="hfl/")
        with self.assertRaises(RepositoryLocationConflict):
            reserve_repository_location(exact)

        nested = self._s3_repository(name="Nested", prefix="hfl/a/")
        with self.assertRaises(RepositoryLocationConflict):
            reserve_repository_location(nested)

    def test_legacy_bucket_root_claim_blocks_every_s3_prefix(self):
        legacy = self._s3_repository(name="Legacy Bucket Root", prefix="")
        claim = reserve_repository_location(legacy)
        mark_repository_location_owned(legacy)

        self.assertEqual(claim.root_path, "/")

        nested = self._s3_repository(name="Nested", prefix="hfl/")
        with self.assertRaises(RepositoryLocationConflict):
            reserve_repository_location(nested)

    def test_s3_prefix_claim_blocks_later_bucket_root(self):
        nested = self._s3_repository(name="Nested", prefix="hfl/")
        reserve_repository_location(nested)

        bucket_root = self._s3_repository(name="Bucket Root", prefix="")
        with self.assertRaises(RepositoryLocationConflict):
            reserve_repository_location(bucket_root)

    def test_public_cloud_accounts_have_independent_namespaces(self):
        first = self._s3_repository(
            name="Account A",
            prefix="hfl/",
            access_key_id="account-a",
        )
        second = self._s3_repository(
            name="Account B",
            prefix="hfl/",
            access_key_id="account-b",
        )

        reserve_repository_location(first)
        reserve_repository_location(second)

        self.assertEqual(RepositoryLocationClaim.objects.count(), 2)
        self.assertEqual(
            RepositoryLocationClaim.objects.values("namespace_id").distinct().count(),
            2,
        )

    def test_unresolved_legacy_s3_claim_blocks_nested_new_account_location(self):
        legacy = self._s3_repository(name="Legacy", prefix="hfl/")
        legacy_claim = reserve_repository_location(legacy)
        conservative_spec = repository_location_spec(
            legacy,
            s3_namespace_resolved=True,
        )
        conservative_namespace = RepositoryLocationNamespace.objects.create(
            namespace_key=conservative_spec.namespace_key,
            kind=conservative_spec.kind,
            display_hint=conservative_spec.display_hint,
        )
        legacy_claim.namespace = conservative_namespace
        legacy_claim.namespace_resolved_at = None
        legacy_claim.save(
            update_fields=["namespace", "namespace_resolved_at", "updated_at"]
        )

        candidate = self._s3_repository(
            name="New account",
            prefix="hfl/child/",
            access_key_id="account-b",
        )
        reserve_repository_location(candidate)

        with self.assertRaises(RepositoryLocationConflict):
            resolve_s3_repository_namespace(candidate, owner_id="cloud-account-b")

    def test_resolved_legacy_s3_claim_allows_other_cloud_account(self):
        first = self._s3_repository(name="Account A", prefix="hfl/")
        reserve_repository_location(first)
        resolve_s3_repository_namespace(first, owner_id="cloud-account-a")

        second = self._s3_repository(
            name="Account B",
            prefix="hfl/",
            access_key_id="account-b",
        )
        reserve_repository_location(second)

        claim = resolve_s3_repository_namespace(
            second,
            owner_id="cloud-account-b",
        )
        self.assertEqual(claim.repository_id, second.id)

    def test_bucket_scoped_credentials_keep_independent_account_claims(self):
        first = self._s3_repository(
            name="Bucket-scoped account A",
            prefix="hfl/",
            access_key_id="bucket-account-a",
        )
        second = self._s3_repository(
            name="Bucket-scoped account B",
            prefix="hfl/",
            access_key_id="bucket-account-b",
        )
        reserve_repository_location(first)
        reserve_repository_location(second)

        first_claim = resolve_s3_repository_namespace(first, owner_id=None)
        second_claim = resolve_s3_repository_namespace(second, owner_id=None)

        self.assertNotEqual(first_claim.namespace_id, second_claim.namespace_id)
        self.assertIsNotNone(first_claim.namespace_resolved_at)
        self.assertIsNotNone(second_claim.namespace_resolved_at)

    def test_late_initialization_failure_does_not_override_newer_owned_state(self):
        repository = self._direct_nas_repository(name="Concurrent initialization")
        claim = reserve_direct_nas_location(
            repository=repository,
            node_id=17,
            repository_subdir="hp-repos/agent-17",
        )
        mark_repository_location_initializing(repository, owner_node_id=17)
        mark_repository_location_owned(repository, owner_node_id=17)

        mark_repository_location_initialization_failed(
            repository,
            owner_node_id=17,
        )

        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.OWNED)

    def test_direct_nas_state_transition_is_scoped_to_one_subdirectory(self):
        repository = self._direct_nas_repository(name="Historical subdirectories")
        current = reserve_direct_nas_location(
            repository=repository,
            node_id=23,
            repository_subdir="hp-repos/agent-23",
        )
        historical = reserve_direct_nas_location(
            repository=repository,
            node_id=23,
            repository_subdir="hp-repos/legacy-agent-23",
        )
        mark_repository_location_residual(
            repository,
            owner_node_id=23,
            repository_subdir=historical.root_path,
        )

        mark_repository_location_owned(
            repository,
            owner_node_id=23,
            repository_subdir=current.root_path,
        )

        current.refresh_from_db()
        historical.refresh_from_db()
        self.assertEqual(current.state, RepositoryLocationClaim.State.OWNED)
        self.assertEqual(historical.state, RepositoryLocationClaim.State.RESIDUAL)

    def test_cross_organization_conflict_does_not_disclose_repository_name(self):
        first = self._s3_repository(
            name="Private repository name",
            prefix="hfl/",
            organization_id=1,
        )
        second = self._s3_repository(
            name="Other org",
            prefix="hfl/",
            organization_id=2,
        )
        reserve_repository_location(first)

        with self.assertRaises(RepositoryLocationConflict) as raised:
            reserve_repository_location(second)

        self.assertNotIn("Private repository name", str(raised.exception))
        self.assertIsNone(raised.exception.conflicting_repository_id)

    def test_direct_nas_claim_is_delayed_and_scoped_to_agent_subdirectory(self):
        first = self._direct_nas_repository(name="First")
        second = self._direct_nas_repository(name="Second")

        self.assertIsNone(reserve_repository_location(first))
        self.assertFalse(first.location_claims.exists())

        reserve_direct_nas_location(
            repository=first,
            node_id=7,
            repository_subdir="hp-repos/agent-7",
        )
        reserve_direct_nas_location(
            repository=second,
            node_id=8,
            repository_subdir="hp-repos/agent-8",
        )

        with self.assertRaises(RepositoryLocationConflict):
            reserve_direct_nas_location(
                repository=second,
                node_id=7,
                repository_subdir="hp-repos/agent-7/child",
            )

    def test_same_private_nas_location_on_different_agents_has_separate_claims(self):
        first = self._direct_nas_repository(name="Company A")
        second = self._direct_nas_repository(name="Company B")

        first_claim = reserve_direct_nas_location(
            repository=first,
            node_id=7,
            repository_subdir="hp-repos/shared",
        )
        second_claim = reserve_direct_nas_location(
            repository=second,
            node_id=8,
            repository_subdir="hp-repos/shared",
        )

        self.assertNotEqual(first_claim.namespace_id, second_claim.namespace_id)

    def test_bound_nas_repositories_share_proxy_namespace_but_use_sibling_roots(self):
        first = Repository.objects.create(
            organization_id=1,
            name="Primary NAS",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATING,
            health=Repository.Health.OFFLINE,
            nas_protocol=Repository.NasProtocol.NFS,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=17,
            config={
                "server_address": "192.168.1.10",
                "share_path": "/backup",
            },
        )
        nested = Repository.objects.create(
            organization_id=1,
            name="Nested NAS",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATING,
            health=Repository.Health.OFFLINE,
            nas_protocol=Repository.NasProtocol.NFS,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=17,
            config={
                "server_address": "192.168.1.10",
                "share_path": "/backup",
            },
        )

        first_claim = reserve_repository_location(first)
        nested_claim = reserve_repository_location(nested)

        # Bound NAS repositories use repository-specific managed subdirectories,
        # so two roots on the same NAS/Proxy are siblings rather than overlaps.
        self.assertEqual(first_claim.namespace_id, nested_claim.namespace_id)
        self.assertNotEqual(first_claim.root_path, nested_claim.root_path)

    def test_smb_share_identity_is_case_insensitive(self):
        first = self._direct_nas_repository(name="First")
        first.nas_protocol = Repository.NasProtocol.SMB
        first.config = {**first.config, "share_path": "/Backup"}
        first.save(update_fields=["nas_protocol", "config", "updated_at"])
        second = self._direct_nas_repository(name="Second")
        second.nas_protocol = Repository.NasProtocol.SMB
        second.config = {**second.config, "share_path": "/backup"}
        second.save(update_fields=["nas_protocol", "config", "updated_at"])

        reserve_direct_nas_location(
            repository=first,
            node_id=7,
            repository_subdir="hp-repos/agent-7",
        )

        with self.assertRaises(RepositoryLocationConflict):
            reserve_direct_nas_location(
                repository=second,
                node_id=7,
                repository_subdir="hp-repos/agent-7",
            )

    def test_claim_state_tracks_owned_residual_and_released_boundaries(self):
        repository = self._s3_repository(name="Lifecycle", prefix="hfl/")
        claim = reserve_repository_location(repository)
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESERVED)

        mark_repository_location_initializing(repository)
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.INITIALIZING)

        mark_repository_location_owned(repository)
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.OWNED)
        self.assertIsNotNone(claim.initialized_at)

        mark_repository_location_residual(repository)
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)

        release_repository_location(repository)
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RELEASED)
        self.assertIsNotNone(claim.released_at)

    def test_proxy_filesystem_nested_base_paths_share_one_node_namespace(self):
        first = Repository.objects.create(
            organization_id=1,
            name="Primary local disk",
            repo_type=Repository.Type.PROXY_FS,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=9,
            config={
                "proxy_node_base_dir": "/data",
                "proxy_node_dir": "/data/hfl-repo-1",
            },
        )
        nested = Repository.objects.create(
            organization_id=1,
            name="Nested local disk",
            repo_type=Repository.Type.PROXY_FS,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=9,
            config={
                "proxy_node_base_dir": "/data/hfl-repo-1/child",
                "proxy_node_dir": "/data/hfl-repo-1/child/hfl-repo-2",
            },
        )

        reserve_repository_location(first)
        with self.assertRaises(RepositoryLocationConflict):
            reserve_repository_location(nested)

    def test_s3_region_and_catalog_label_do_not_split_physical_namespace(self):
        first = self._s3_repository(name="Primary", prefix="hfl/")
        second = self._s3_repository(name="Duplicate", prefix="hfl/child/")
        second.s3_platform = Repository.S3Platform.CUSTOM
        second.config = {**second.config, "region": "different-label"}
        second.save(update_fields=["s3_platform", "config", "updated_at"])

        reserve_repository_location(first)
        with self.assertRaises(RepositoryLocationConflict):
            reserve_repository_location(second)

    @mock.patch(
        "apps.storage.services.internal.repository_ownership.s3_prefix_has_any_state",
        return_value=True,
    )
    @mock.patch(
        "apps.storage.services.internal.repository_ownership.read_s3_object",
        return_value=None,
    )
    @mock.patch(
        "apps.storage.services.internal.repository_ownership._ensure_s3_namespace_resolved"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_ownership._s3_args",
        return_value={},
    )
    def test_s3_claim_rejects_hidden_physical_state(
        self,
        _s3_args,
        _resolve_namespace,
        _read_marker,
        _prefix_has_state,
    ):
        repository = self._s3_repository(name="Versioned residue", prefix="hfl/")

        with self.assertRaisesRegex(
            RepositoryOwnershipError,
            "historical versions",
        ):
            claim_s3_repository_ownership(repository)

    @mock.patch(
        "apps.storage.services.internal.repository_ownership.mark_repository_location_ownership_verified"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_ownership.put_s3_object_if_absent",
        return_value=True,
    )
    @mock.patch(
        "apps.storage.services.internal.repository_ownership.s3_prefix_has_any_state",
        return_value=False,
    )
    @mock.patch("apps.storage.services.internal.repository_ownership.read_s3_object")
    @mock.patch(
        "apps.storage.services.internal.repository_ownership.list_s3_object_keys",
        return_value=[],
    )
    @mock.patch(
        "apps.storage.services.internal.repository_ownership._ensure_s3_namespace_resolved"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_ownership._s3_args",
        return_value={},
    )
    def test_bucket_root_uses_root_marker_and_scans_entire_bucket(
        self,
        _s3_args,
        _resolve_namespace,
        _list_keys,
        read_marker,
        prefix_has_state,
        put_marker,
        _mark_verified,
    ):
        repository = self._s3_repository(name="Bucket Root", prefix="")
        marker = b'{"marker":"persisted"}'
        read_marker.side_effect = [None, marker]
        with mock.patch(
            "apps.storage.services.internal.repository_ownership._require_matching_marker"
        ):
            claim_s3_repository_ownership(repository)

        prefix_has_state.assert_called_once_with(prefix="")
        put_marker.assert_called_once()
        self.assertEqual(
            put_marker.call_args.kwargs["key"],
            ".hyperfilelens/repository-owner-v1.json",
        )

    @mock.patch(
        "apps.storage.services.internal.repository_ownership._reject_foreign_ancestor_markers",
        side_effect=RepositoryOwnershipError("nested in another repository"),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_ownership._ensure_s3_namespace_resolved"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_ownership._s3_args",
        return_value={},
    )
    def test_legacy_adoption_rejects_foreign_ancestor_before_writing_marker(
        self,
        _s3_args,
        _resolve_namespace,
        _reject_ancestor,
    ):
        repository = self._s3_repository(name="Nested legacy", prefix="hfl/child/")

        with self.assertRaisesRegex(
            RepositoryOwnershipError,
            "nested in another repository",
        ):
            verify_s3_repository_ownership(repository, adopt_legacy=True)

    def test_legacy_adoption_rejects_foreign_descendant_before_writing_marker(self):
        repository = self._s3_repository(name="Legacy parent", prefix="hfl/")
        with (
            mock.patch(
                "apps.storage.services.internal.repository_ownership._s3_args",
                return_value={},
            ),
            mock.patch(
                "apps.storage.services.internal.repository_ownership._ensure_s3_namespace_resolved"
            ),
            mock.patch(
                "apps.storage.services.internal.repository_ownership.read_s3_object",
                return_value=None,
            ),
            mock.patch(
                "apps.storage.services.internal.repository_ownership._reject_foreign_descendant_markers",
                side_effect=RepositoryOwnershipError(
                    "contains another managed repository"
                ),
            ),
            mock.patch(
                "apps.storage.services.internal.repository_ownership.put_s3_object_if_absent"
            ) as put_marker,
        ):
            with self.assertRaisesRegex(
                RepositoryOwnershipError,
                "contains another managed repository",
            ):
                verify_s3_repository_ownership(repository, adopt_legacy=True)

        put_marker.assert_not_called()
