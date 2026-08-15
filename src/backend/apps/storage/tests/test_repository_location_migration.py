from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class RepositoryLocationMigrationTests(TransactionTestCase):
    migrate_from = [("storage", "0016_merge_0015_storage_branches")]
    migrate_to = [("storage", "0017_repository_location_claims")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self._seed_existing_repositories(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _seed_existing_repositories(self, apps):
        Repository = apps.get_model("storage", "Repository")
        Shard = apps.get_model("storage", "RepositoryUsageShard")
        shared = {
            "organization_id": 1,
            "repo_type": "s3",
            "status": "created",
            "health": "online",
            "s3_platform": "huaweicloud",
            "s3_bucket": "migration-bucket",
        }
        first = Repository.objects.create(
            **shared,
            name="migration-root",
            config={
                "endpoint": "obs.example.test",
                "region": "region-1",
                "prefix": "hfl/",
                "access_key_id": "migration-account",
            },
        )
        nested = Repository.objects.create(
            **shared,
            name="migration-nested",
            config={
                "endpoint": "https://obs.example.test/",
                "region": "region-1",
                "prefix": "hfl/nested/",
                "access_key_id": "migration-account",
            },
        )
        direct_nas = Repository.objects.create(
            organization_id=1,
            name="migration-direct-nas",
            repo_type="nas",
            status="created",
            health="online",
            nas_protocol="nfs",
            config={"server_address": "nas.example.test", "share_path": "/backup"},
        )
        Shard.objects.create(
            organization_id=1,
            repository_id=direct_nas.id,
            node_id=42,
            repository_subdir="hp-repos/agent-42",
            status="success",
            last_success_checked_at=timezone.now(),
        )
        inactive_shard = Shard.objects.create(
            organization_id=1,
            repository_id=direct_nas.id,
            node_id=43,
            repository_subdir="hp-repos/agent-43",
            status="success",
            is_active=False,
            last_success_checked_at=timezone.now(),
        )
        self.overlapping_repository_ids = [first.id, nested.id]
        self.direct_nas_id = direct_nas.id
        self.inactive_direct_nas_shard_id = inactive_shard.id
        bound_nas = Repository.objects.create(
            organization_id=1,
            name="migration-bound-nas-with-history",
            repo_type="nas",
            status="created",
            health="online",
            nas_protocol="nfs",
            bind_node_type="proxy",
            bind_node_id=77,
            config={"server_address": "nas.example.test", "share_path": "/bound"},
        )
        Shard.objects.create(
            organization_id=1,
            repository_id=bound_nas.id,
            node_id=78,
            repository_subdir="hp-repos/agent-78",
            status="success",
            last_success_checked_at=timezone.now(),
        )
        self.bound_nas_with_history_id = bound_nas.id
        bucket_root = Repository.objects.create(
            **{**shared, "s3_bucket": "migration-root-bucket"},
            name="migration-bucket-root",
            config={
                "endpoint": "obs.example.test",
                "region": "region-1",
                "prefix": "",
                "access_key_id": "migration-account",
            },
        )
        self.bucket_root_repository_id = bucket_root.id
        retained = Repository.objects.create(
            organization_id=1,
            name="migration-retained-s3",
            repo_type="s3",
            status="removed",
            health="offline",
            cleanup_result="force_skipped",
            s3_platform="huaweicloud",
            s3_bucket="retained-migration-bucket",
            config={
                "endpoint": "obs.example.test",
                "prefix": "hfl/",
                "access_key_id": "retained-account",
            },
        )
        deleted = Repository.objects.create(
            organization_id=1,
            name="migration-deleted-s3",
            repo_type="s3",
            status="removed",
            health="offline",
            cleanup_result="deleted",
            s3_platform="huaweicloud",
            s3_bucket="deleted-migration-bucket",
            config={
                "endpoint": "obs.example.test",
                "prefix": "hfl/",
                "access_key_id": "deleted-account",
            },
        )
        self.retained_repository_id = retained.id
        self.deleted_repository_id = deleted.id
        uncertain = Repository.objects.create(
            organization_id=1,
            name="migration-remove-failed-s3",
            repo_type="s3",
            status="remove_failed",
            health="offline",
            s3_platform="huaweicloud",
            s3_bucket="uncertain-migration-bucket",
            config={
                "endpoint": "obs.example.test",
                "prefix": "hfl/",
                "access_key_id": "uncertain-account",
            },
        )
        self.uncertain_repository_id = uncertain.id

    def test_backfill_quarantines_overlap_and_owns_verified_direct_nas_shard(self):
        Claim = self.apps.get_model("storage", "RepositoryLocationClaim")

        overlap_states = set(
            Claim.objects.filter(
                repository_id__in=self.overlapping_repository_ids,
            ).values_list("state", flat=True)
        )
        self.assertEqual(overlap_states, {"residual"})

        direct_claim = Claim.objects.get(
            repository_id=self.direct_nas_id,
            owner_node_id=42,
        )
        self.assertEqual(direct_claim.state, "owned")
        self.assertTrue(direct_claim.legacy_adoption_required)
        self.assertEqual(direct_claim.owner_node_id, 42)
        self.assertEqual(direct_claim.root_path, "hp-repos/agent-42")

        inactive_claim = Claim.objects.get(
            repository_id=self.direct_nas_id,
            owner_node_id=43,
        )
        self.assertEqual(inactive_claim.state, "residual")
        self.assertEqual(inactive_claim.root_path, "hp-repos/agent-43")

        bound_claims = Claim.objects.filter(
            repository_id=self.bound_nas_with_history_id,
        )
        self.assertEqual(bound_claims.count(), 2)
        self.assertEqual(
            bound_claims.get(scope="repository").state,
            "owned",
        )
        historical_bound_claim = bound_claims.get(scope="direct_nas_agent")
        self.assertEqual(historical_bound_claim.state, "residual")
        self.assertEqual(historical_bound_claim.owner_node_id, 78)

        bucket_root_claim = Claim.objects.get(
            repository_id=self.bucket_root_repository_id
        )
        self.assertEqual(bucket_root_claim.state, "owned")
        self.assertEqual(bucket_root_claim.root_path, "/")
        self.assertTrue(bucket_root_claim.legacy_adoption_required)

        retained_claim = Claim.objects.get(repository_id=self.retained_repository_id)
        self.assertEqual(retained_claim.state, "residual")
        self.assertFalse(
            Claim.objects.filter(repository_id=self.deleted_repository_id).exists()
        )
        self.assertEqual(
            Claim.objects.get(repository_id=self.uncertain_repository_id).state,
            "residual",
        )
