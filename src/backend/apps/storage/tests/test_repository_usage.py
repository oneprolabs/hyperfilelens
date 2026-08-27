from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from common.errors import AppError
from apps.iam.models import Organization
from apps.node.models import Node
from apps.node.agent_paths import repository_mount_point
from apps.protection.models import BackupConfig, BackupSourceSnapshot
from apps.storage.repositories.models import (
    Repository,
    RepositoryLocationClaim,
    RepositoryUsageShard,
)
from apps.storage.services.internal.kopia_cli import KopiaRepositoryBusyError
from apps.storage.services.internal.nas_repository import nas_agent_repository_subdir
from apps.storage.services.internal.repository_location import (
    mark_repository_location_owned,
    reserve_direct_nas_location,
)
from apps.storage.services.internal.repository_usage import (
    RepositoryUsageProbeResult,
    _parse_agent_repo_status_result,
    assert_repository_quota_available,
    capacity_bytes_from_config,
    kopia_estimated_usage_from_packed,
    kopia_repository_estimated_usage_bytes,
    parse_kopia_content_stats,
    sync_all_repositories,
    sync_organization_repositories,
    sync_repository_usage,
)


class RepositoryUsageTests(TestCase):
    def _mark_direct_nas_location_owned(
        self,
        *,
        repository: Repository,
        node: Node,
    ) -> str:
        subdir = nas_agent_repository_subdir(node.id)
        reserve_direct_nas_location(
            repository=repository,
            node_id=node.id,
            repository_subdir=subdir,
        )
        mark_repository_location_owned(
            repository,
            owner_node_id=node.id,
            repository_subdir=subdir,
        )
        return subdir

    def test_capacity_bytes_from_config(self):
        self.assertEqual(capacity_bytes_from_config({"quota_gb": 10}), 10 * 1024**3)
        self.assertEqual(capacity_bytes_from_config({"quota_gb": 0}), 0)
        self.assertEqual(capacity_bytes_from_config(None), 0)

    def test_repository_quota_rejects_full_fresh_observation(self):
        repository = Repository(
            id=798,
            config={"quota_gb": 1},
            estimated_usage_bytes=1024**3,
            usage_probe_status=Repository.MetricProbeStatus.SUCCESS,
            usage_last_success_at=timezone.now(),
        )

        with self.assertRaisesMessage(
            AppError,
            "configured Storage Quota",
        ) as context:
            assert_repository_quota_available(repository)

        self.assertEqual(context.exception.code, "BACKUP.REPOSITORY_QUOTA_EXCEEDED")

    def test_repository_quota_fails_open_for_stale_observation(self):
        repository = Repository(
            config={"quota_gb": 1},
            estimated_usage_bytes=2 * 1024**3,
            usage_probe_status=Repository.MetricProbeStatus.SUCCESS,
            usage_last_success_at=timezone.now() - timedelta(minutes=16),
        )

        assert_repository_quota_available(repository)

    def test_repository_quota_allows_unlimited_under_limit_and_unknown_usage(self):
        now = timezone.now()
        cases = (
            {
                "name": "unlimited",
                "config": {"quota_gb": 0},
                "used": 2 * 1024**3,
                "status": Repository.MetricProbeStatus.SUCCESS,
                "checked_at": now,
            },
            {
                "name": "under limit",
                "config": {"quota_gb": 2},
                "used": 1024**3,
                "status": Repository.MetricProbeStatus.SUCCESS,
                "checked_at": now,
            },
            {
                "name": "failed probe",
                "config": {"quota_gb": 1},
                "used": 2 * 1024**3,
                "status": Repository.MetricProbeStatus.FAILED,
                "checked_at": now,
            },
            {
                "name": "missing successful observation",
                "config": {"quota_gb": 1},
                "used": 2 * 1024**3,
                "status": Repository.MetricProbeStatus.SUCCESS,
                "checked_at": None,
            },
        )

        for case in cases:
            with self.subTest(case["name"]):
                repository = Repository(
                    config=case["config"],
                    estimated_usage_bytes=case["used"],
                    usage_probe_status=case["status"],
                    usage_last_success_at=case["checked_at"],
                )
                assert_repository_quota_available(repository, now=now)

    @mock.patch(
        "apps.storage.services.internal.repository_usage.sync_repository_usage"
    )
    def test_scheduled_usage_prioritizes_oldest_repository(self, sync_usage):
        org = Organization.objects.create(
            key="scheduled-usage-priority-org",
            name="Scheduled Usage Priority Org",
        )
        newer = Repository.objects.create(
            organization_id=org.id,
            name="newer-repository",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            last_checked_at=timezone.now() - timedelta(minutes=20),
        )
        older = Repository.objects.create(
            organization_id=org.id,
            name="older-repository",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            last_checked_at=timezone.now() - timedelta(minutes=30),
        )

        result = sync_all_repositories(limit=1, force=True)

        self.assertEqual(result["repositories_attempted"], 1)
        self.assertEqual(result["repositories_synced"], 1)
        self.assertEqual(result["repositories_failed"], 0)
        sync_usage.assert_called_once()
        self.assertEqual(sync_usage.call_args.args[0].id, older.id)
        self.assertNotEqual(sync_usage.call_args.args[0].id, newer.id)

    @mock.patch(
        "apps.storage.services.internal.repository_health."
        "dispatch_automatic_repository_observation"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_usage._run_repository_usage_probe"
    )
    def test_scheduled_usage_dispatches_agent_observation_without_waiting(
        self,
        synchronous_probe,
        dispatch_observation,
    ):
        org = Organization.objects.create(
            key="scheduled-usage-org",
            name="Scheduled Usage Org",
        )
        repository = Repository.objects.create(
            organization_id=org.id,
            name="scheduled-proxy-fs",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=42,
        )
        dispatch_observation.return_value = [mock.Mock(id="node-task")]

        result = sync_organization_repositories(
            organization_id=org.id,
            force=True,
            async_agent_probes=True,
        )

        self.assertEqual(result["repositories_synced"], 1)
        self.assertEqual(result["observations_dispatched"], 1)
        self.assertEqual(result["snapshots_upserted"], 0)
        dispatch_observation.assert_called_once_with(
            repository=repository,
            include_usage=True,
        )
        synchronous_probe.assert_not_called()

    @mock.patch(
        "apps.storage.services.internal.repository_health."
        "dispatch_automatic_repository_observation"
    )
    @mock.patch("apps.storage.services.internal.repository_usage.sync_repository_usage")
    def test_scheduled_usage_continues_after_one_repository_fails(
        self,
        sync_usage,
        dispatch_observation,
    ):
        org = Organization.objects.create(
            key="scheduled-usage-isolation-org",
            name="Scheduled Usage Isolation Org",
        )
        failed_repository = Repository.objects.create(
            organization_id=org.id,
            name="offline-proxy-repository",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=42,
            last_checked_at=timezone.now() - timedelta(hours=1),
        )
        healthy_repository = Repository.objects.create(
            organization_id=org.id,
            name="healthy-s3-repository",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            last_checked_at=timezone.now() - timedelta(minutes=30),
        )
        dispatch_observation.side_effect = ValidationError("bound proxy is offline")

        result = sync_all_repositories(
            force=True,
            async_agent_probes=True,
        )

        self.assertEqual(result["repositories_attempted"], 2)
        self.assertEqual(result["repositories_synced"], 1)
        self.assertEqual(result["repositories_failed"], 1)
        self.assertEqual(
            result["failed_repository_ids"],
            [failed_repository.id],
        )
        dispatch_observation.assert_called_once_with(
            repository=failed_repository,
            include_usage=True,
        )
        sync_usage.assert_called_once_with(
            healthy_repository,
            recorded_at=None,
        )

    def test_parse_kopia_content_stats_json(self):
        payload = '{"totalSize": 2048, "totalFileCount": 3}'
        self.assertEqual(parse_kopia_content_stats(payload), 2048)

    def test_parse_kopia_content_stats_text(self):
        text = "Total Size: 1.5 GB\nTotal File Count: 10"
        parsed = parse_kopia_content_stats(text)
        self.assertGreater(parsed, 1024**3)

    def test_parse_kopia_content_stats_prefers_total_packed_text(self):
        text = "Count: 70\nTotal Bytes: 10 MB\nTotal Packed: 2 MB (compression 80%)"
        parsed = parse_kopia_content_stats(text)
        self.assertEqual(parsed, 2 * 1024**2)

    def test_kopia_estimated_usage_from_packed(self):
        self.assertEqual(kopia_estimated_usage_from_packed(100), 105)

    @mock.patch(
        "apps.storage.services.internal.repository_usage.connect_s3_repository",
        side_effect=KopiaRepositoryBusyError("repository busy"),
    )
    def test_s3_usage_preserves_repository_busy_signal(self, _connect):
        repository = Repository(
            id=17,
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
        )

        with self.assertRaises(KopiaRepositoryBusyError):
            kopia_repository_estimated_usage_bytes(repository)

    def test_parse_agent_repo_status_result(self):
        estimated, total, mount_point, usage_error, capacity_error = _parse_agent_repo_status_result(
            {
                "estimated_usage_bytes": 210,
                "space_info": {"total_bytes": 1000, "used_bytes": 300},
            }
        )
        self.assertEqual(estimated, 210)
        self.assertEqual(total, 1000)
        self.assertEqual(mount_point, "")
        self.assertEqual(usage_error, "")
        self.assertEqual(capacity_error, "")

    def test_parse_agent_repo_status_result_falls_back_to_space_used(self):
        estimated, total, mount_point, _usage_error, _capacity_error = _parse_agent_repo_status_result(
            {
                "repository_type": "proxy_fs",
                "space_info": {"total_bytes": 1000, "used_bytes": 300},
            }
        )
        self.assertEqual(estimated, 300)
        self.assertEqual(total, 1000)
        self.assertEqual(mount_point, "")

    def test_parse_agent_repo_status_result_accepts_zero_usage_probe(self):
        estimated, total, _mount_point, usage_error, capacity_error = _parse_agent_repo_status_result(
            {
                "usage_probe": {"status": "success", "estimated_usage_bytes": 0},
                "capacity_probe": {"status": "success", "total_bytes": 1000},
            }
        )
        self.assertEqual(estimated, 0)
        self.assertEqual(total, 1000)
        self.assertEqual(usage_error, "")
        self.assertEqual(capacity_error, "")

    def test_parse_agent_repo_status_result_keeps_partial_probe_error(self):
        estimated, total, _mount_point, usage_error, capacity_error = _parse_agent_repo_status_result(
            {
                "usage_probe": {"status": "success", "estimated_usage_bytes": 100},
                "capacity_probe": {"status": "failed", "error": "statfs failed"},
            }
        )
        self.assertEqual(estimated, 100)
        self.assertIsNone(total)
        self.assertEqual(usage_error, "")
        self.assertEqual(capacity_error, "statfs failed")

    def test_parse_agent_repo_status_result_strips_repository_subdir_from_space_path(self):
        _estimated, _total, mount_point, _usage_error, _capacity_error = _parse_agent_repo_status_result(
            {
                "repository_type": "nas",
                "space_info": {
                    "path": "/mnt/hfl/storage-repositories/repo-34-node-43/hp-repos/agent-52",
                    "total_bytes": 1000,
                    "used_bytes": 300,
                },
            },
            repository_subdir="hp-repos/agent-52",
        )

        self.assertEqual(mount_point, "/mnt/hfl/storage-repositories/repo-34-node-43")

    def test_parse_agent_repo_status_result_prefers_reported_storage_mount_point(self):
        _estimated, _total, mount_point, _usage_error, _capacity_error = _parse_agent_repo_status_result(
            {
                "space_info": {
                    "path": "/data/repository-a",
                    "mount_point": "/data",
                    "total_bytes": 1000,
                    "used_bytes": 300,
                },
            },
        )

        self.assertEqual(mount_point, "/data")

    @mock.patch(
        "apps.storage.services.internal.repository_usage.collect_usage_candidates",
        return_value=RepositoryUsageProbeResult(0, None),
    )
    def test_sync_applies_quota_capacity(self, _collect):
        repo = Repository.objects.create(
            organization_id=1,
            name="quota-repo",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.OFFLINE,
            config={"proxy_node_dir": "/data/repo", "quota_gb": 2},
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=9,
        )
        sync_repository_usage(repo)
        repo.refresh_from_db()
        self.assertEqual(repo.capacity_bytes, 2 * 1024**3)

    @mock.patch(
        "apps.storage.services.internal.repository_usage.agent_repository_usage_probe",
        return_value=RepositoryUsageProbeResult(
            5 * 1024**3,
            100 * 1024**3,
            mount_point="/",
            storage_used_bytes=10 * 1024**3,
            storage_available_bytes=90 * 1024**3,
            storage_pool_key="/dev/sda1|/",
        ),
    )
    def test_proxy_fs_sync_uses_agent_kopia_usage_and_mount_capacity(self, _probe):
        repo = Repository.objects.create(
            organization_id=1,
            name="proxy-fs-repo",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            estimated_usage_bytes=999,
            config={"proxy_node_dir": "/data/repo"},
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=9,
        )

        sync_repository_usage(repo)

        repo.refresh_from_db()
        self.assertEqual(repo.estimated_usage_bytes, 5 * 1024**3)
        self.assertEqual(repo.capacity_bytes, 100 * 1024**3)
        self.assertEqual(repo.storage_total_bytes, 100 * 1024**3)
        self.assertEqual(repo.storage_used_bytes, 10 * 1024**3)
        self.assertEqual(repo.storage_available_bytes, 90 * 1024**3)
        self.assertEqual(repo.storage_pool_key, "proxy:9:/dev/sda1|/")
        self.assertEqual(repo.storage_mount_point, "/")
        self.assertEqual(repo.usage_probe_status, Repository.MetricProbeStatus.SUCCESS)
        self.assertEqual(repo.capacity_probe_status, Repository.MetricProbeStatus.SUCCESS)

    @mock.patch(
        "apps.storage.services.internal.repository_usage.agent_repository_usage_probe",
        return_value=RepositoryUsageProbeResult(100, None, capacity_error="statfs failed"),
    )
    def test_proxy_fs_sync_marks_only_capacity_probe_failed(self, _probe):
        repo = Repository.objects.create(
            organization_id=1,
            name="partial-probe-repo",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"proxy_node_dir": "/data/repo"},
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=9,
        )

        sync_repository_usage(repo)

        repo.refresh_from_db()
        self.assertEqual(repo.estimated_usage_bytes, 100)
        self.assertEqual(repo.usage_probe_status, Repository.MetricProbeStatus.SUCCESS)
        self.assertEqual(repo.capacity_probe_status, Repository.MetricProbeStatus.FAILED)
        self.assertEqual(repo.capacity_last_error, "statfs failed")

    @mock.patch("apps.storage.services.internal.repository_usage.agent_repository_usage_probe")
    def test_proxy_fs_sync_uses_quota_capacity_without_path_probe(self, mock_probe):
        repo = Repository.objects.create(
            organization_id=1,
            name="proxy-fs-quota-repo",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            estimated_usage_bytes=999,
            config={"proxy_node_dir": "/data/repo", "quota_gb": 2},
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=9,
        )
        mock_probe.return_value = RepositoryUsageProbeResult(
            3 * 1024**3,
            100 * 1024**3,
            storage_used_bytes=5 * 1024**3,
            storage_available_bytes=95 * 1024**3,
        )

        sync_repository_usage(repo)

        repo.refresh_from_db()
        self.assertEqual(repo.estimated_usage_bytes, 3 * 1024**3)
        self.assertEqual(repo.capacity_bytes, 2 * 1024**3)
        self.assertEqual(repo.storage_total_bytes, 100 * 1024**3)
        self.assertEqual(repo.storage_available_bytes, 95 * 1024**3)

    @mock.patch(
        "apps.storage.services.internal.repository_usage.agent_repository_usage_probe",
        return_value=RepositoryUsageProbeResult(2 * 1024**3, 50 * 1024**3),
    )
    def test_nas_sync_uses_agent_probe(self, _probe):
        repo = Repository.objects.create(
            organization_id=1,
            name="nas-repo",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            config={"server_address": "192.168.1.10", "share_path": "/export/data"},
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=9,
        )

        sync_repository_usage(repo)

        repo.refresh_from_db()
        self.assertEqual(repo.estimated_usage_bytes, 2 * 1024**3)
        self.assertEqual(repo.capacity_bytes, 50 * 1024**3)
        self.assertEqual(repo.storage_total_bytes, 50 * 1024**3)
        self.assertEqual(repo.storage_pool_key, "nas:nas:192.168.1.10:/export/data")

    @mock.patch(
        "apps.storage.services.internal.repository_usage.kopia_repository_estimated_usage_bytes",
        return_value=None,
    )
    def test_sync_does_not_fallback_to_snapshot_logical_size(self, _kopia_estimated):
        repo = Repository.objects.create(
            organization_id=1,
            name="s3-repo",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            estimated_usage_bytes=128,
            config={"quota_gb": 2},
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="bucket",
        )
        BackupSourceSnapshot.objects.create(
            organization_id=1,
            snapshot_uid="bss-test",
            idempotency_key="bss-test",
            source_type="agent",
            source_ref_id=1,
            backup_config_id=1,
            repository_id=repo.id,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
            total_size_bytes=10 * 1024**3,
        )

        sync_repository_usage(repo)

        repo.refresh_from_db()
        self.assertEqual(repo.estimated_usage_bytes, 128)
        self.assertEqual(repo.usage_probe_status, Repository.MetricProbeStatus.FAILED)

    @mock.patch(
        "apps.storage.services.internal.repository_usage."
        "kopia_repository_estimated_usage_bytes",
        side_effect=KopiaRepositoryBusyError("repository busy"),
    )
    def test_batch_sync_defers_busy_s3_repository_without_changing_metrics(
        self,
        _kopia_estimated,
    ):
        repo = Repository.objects.create(
            organization_id=1,
            name="s3-busy",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            estimated_usage_bytes=128,
            usage_probe_status=Repository.MetricProbeStatus.SUCCESS,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="bucket",
        )

        result = sync_all_repositories(force=True)

        repo.refresh_from_db()
        self.assertEqual(result["repositories_deferred"], 1)
        self.assertEqual(result["deferred_repository_ids"], [repo.id])
        self.assertEqual(result["repositories_failed"], 0)
        self.assertEqual(repo.estimated_usage_bytes, 128)
        self.assertEqual(
            repo.usage_probe_status,
            Repository.MetricProbeStatus.SUCCESS,
        )

    @mock.patch(
        "apps.storage.services.internal.repository_usage."
        "kopia_repository_estimated_usage_bytes",
        return_value=256,
    )
    def test_batch_sync_stops_at_repository_boundary_when_lease_is_lost(
        self,
        _kopia_estimated,
    ):
        first = Repository.objects.create(
            organization_id=1,
            name="s3-first",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="first-bucket",
        )
        second = Repository.objects.create(
            organization_id=1,
            name="s3-second",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="second-bucket",
        )
        should_continue = mock.Mock(side_effect=[True, False])

        result = sync_all_repositories(
            force=True,
            should_continue=should_continue,
        )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(result["stopped_early"])
        self.assertEqual(result["repositories_attempted"], 1)
        self.assertEqual(result["repositories_synced"], 1)
        self.assertEqual(first.usage_probe_status, Repository.MetricProbeStatus.SUCCESS)
        self.assertNotEqual(
            second.usage_probe_status,
            Repository.MetricProbeStatus.SUCCESS,
        )

    @mock.patch(
        "apps.storage.services.internal.repository_usage.kopia_repository_estimated_usage_bytes",
        return_value=256,
    )
    def test_s3_sync_records_successful_usage_probe(self, _kopia_estimated):
        repo = Repository.objects.create(
            organization_id=1,
            name="s3-success",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="bucket",
        )

        sync_repository_usage(repo)

        repo.refresh_from_db()
        self.assertEqual(repo.estimated_usage_bytes, 256)
        self.assertEqual(repo.usage_probe_status, Repository.MetricProbeStatus.SUCCESS)
        self.assertIsNotNone(repo.usage_last_success_at)

    @mock.patch(
        "apps.storage.services.internal.repository_usage.kopia_repository_estimated_usage_bytes",
        return_value=0,
    )
    def test_s3_sync_records_empty_repository_as_success(self, _kopia_estimated):
        repo = Repository.objects.create(
            organization_id=1,
            name="s3-empty",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="bucket",
        )

        sync_repository_usage(repo)

        repo.refresh_from_db()
        self.assertEqual(repo.estimated_usage_bytes, 0)
        self.assertEqual(repo.usage_probe_status, Repository.MetricProbeStatus.SUCCESS)

    @mock.patch("apps.storage.services.internal.repository_usage._run_repository_usage_probe")
    def test_unbound_nas_sync_aggregates_direct_agent_shards(self, run_probe):
        org = Organization.objects.create(key="usage-org", name="Usage Org")
        agent_a = Node.objects.create(
            organization=org,
            name="agent-a",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.1.1",
        )
        agent_b = Node.objects.create(
            organization=org,
            name="agent-b",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.1.2",
        )
        repo = Repository.objects.create(
            organization_id=org.id,
            name="direct-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            config={"server_address": "10.0.0.10", "share_path": "/backup"},
        )
        BackupConfig.objects.create(
            organization_id=org.id,
            name="config-a",
            source_type="agent",
            source_ref_id=agent_a.id,
            repository_id=repo.id,
        )
        self._mark_direct_nas_location_owned(repository=repo, node=agent_a)
        self._mark_direct_nas_location_owned(repository=repo, node=agent_b)
        BackupConfig.objects.create(
            organization_id=org.id,
            name="config-b",
            source_type="agent",
            source_ref_id=agent_b.id,
            repository_id=repo.id,
        )

        def _probe(**kwargs):
            if kwargs["node_id"] == agent_a.id:
                return RepositoryUsageProbeResult(100, 1000)
            return RepositoryUsageProbeResult(250, 800)

        run_probe.side_effect = _probe

        sync_repository_usage(repo)

        repo.refresh_from_db()
        self.assertEqual(repo.estimated_usage_bytes, 350)
        self.assertEqual(repo.capacity_bytes, 1000)
        self.assertEqual(repo.health, Repository.Health.UNVERIFIED)
        self.assertEqual(
            RepositoryUsageShard.objects.filter(repository_id=repo.id, is_active=True).count(),
            2,
        )

    @mock.patch("apps.storage.services.internal.repository_usage._run_repository_usage_probe")
    def test_unbound_nas_sync_tracks_source_config_on_agent(self, run_probe):
        org = Organization.objects.create(key="usage-dedupe-org", name="Usage Dedupe Org")
        agent = Node.objects.create(
            organization=org,
            name="agent-a",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.2.1",
        )
        repo = Repository.objects.create(
            organization_id=org.id,
            name="direct-nas-dedupe",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            config={"server_address": "10.0.0.10", "share_path": "/backup"},
        )
        config = BackupConfig.objects.create(
            organization_id=org.id,
            name="config-a",
            source_type="agent",
            source_ref_id=agent.id,
            repository_id=repo.id,
        )
        self._mark_direct_nas_location_owned(repository=repo, node=agent)
        run_probe.return_value = RepositoryUsageProbeResult(
            128,
            1024,
            mount_point=repository_mount_point(repo.id, node_id=agent.id),
        )

        sync_repository_usage(repo)

        repo.refresh_from_db()
        self.assertEqual(repo.estimated_usage_bytes, 128)
        self.assertEqual(repo.health, Repository.Health.UNVERIFIED)
        self.assertEqual(run_probe.call_count, 1)
        shard = RepositoryUsageShard.objects.get(repository_id=repo.id, node_id=agent.id)
        self.assertEqual(shard.source_config_count, 1)
        self.assertEqual(shard.source_config_ids, [config.id])
        self.assertEqual(
            shard.mount_point,
            repository_mount_point(repo.id, node_id=agent.id),
        )

    @mock.patch("apps.storage.services.internal.repository_usage._run_repository_usage_probe")
    def test_unbound_nas_sync_keeps_last_success_when_probe_fails(self, run_probe):
        org = Organization.objects.create(key="usage-fail-org", name="Usage Fail Org")
        agent = Node.objects.create(
            organization=org,
            name="agent-a",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.3.1",
        )
        repo = Repository.objects.create(
            organization_id=org.id,
            name="direct-nas-fail",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            estimated_usage_bytes=512,
            capacity_bytes=4096,
            config={"server_address": "10.0.0.10", "share_path": "/backup"},
        )
        config = BackupConfig.objects.create(
            organization_id=org.id,
            name="config-a",
            source_type="agent",
            source_ref_id=agent.id,
            repository_id=repo.id,
        )
        self._mark_direct_nas_location_owned(repository=repo, node=agent)
        RepositoryUsageShard.objects.create(
            organization_id=org.id,
            repository_id=repo.id,
            usage_scope=RepositoryUsageShard.Scope.DIRECT_NAS_AGENT,
            node_id=agent.id,
            repository_subdir=f"hp-repos/agent-{agent.id}",
            estimated_usage_bytes=512,
            capacity_bytes=4096,
            source_config_count=1,
            source_config_ids=[config.id],
            status=RepositoryUsageShard.Status.SUCCESS,
            is_active=True,
            last_checked_at=timezone.now(),
            last_success_checked_at=timezone.now(),
        )
        run_probe.return_value = RepositoryUsageProbeResult(None, None, "timeout")

        sync_repository_usage(repo)

        repo.refresh_from_db()
        self.assertEqual(repo.estimated_usage_bytes, 512)
        self.assertEqual(repo.capacity_bytes, 4096)
        self.assertEqual(repo.health, Repository.Health.UNVERIFIED)
        shard = RepositoryUsageShard.objects.get(repository_id=repo.id, node_id=agent.id)
        self.assertEqual(shard.status, RepositoryUsageShard.Status.FAILED)
        self.assertEqual(shard.estimated_usage_bytes, 512)

    @mock.patch("apps.storage.services.internal.repository_usage._run_repository_usage_probe")
    def test_unbound_nas_sync_does_not_reactivate_residual_location(
        self,
        run_probe,
    ):
        org = Organization.objects.create(
            key="usage-residual-org",
            name="Usage Residual Org",
        )
        agent = Node.objects.create(
            organization=org,
            name="agent-a",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.4.1",
        )
        repo = Repository.objects.create(
            organization_id=org.id,
            name="direct-nas-residual",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            estimated_usage_bytes=512,
            capacity_bytes=4096,
            config={"server_address": "10.0.0.10", "share_path": "/backup"},
        )
        config = BackupConfig.objects.create(
            organization_id=org.id,
            name="config-a",
            source_type="agent",
            source_ref_id=agent.id,
            repository_id=repo.id,
        )
        subdir = self._mark_direct_nas_location_owned(
            repository=repo,
            node=agent,
        )
        claim = RepositoryLocationClaim.objects.get(
            repository=repo,
            owner_node_id=agent.id,
            root_path=subdir,
        )
        claim.state = RepositoryLocationClaim.State.RESIDUAL
        claim.save(update_fields=["state", "updated_at"])
        RepositoryUsageShard.objects.create(
            organization_id=org.id,
            repository_id=repo.id,
            usage_scope=RepositoryUsageShard.Scope.DIRECT_NAS_AGENT,
            node_id=agent.id,
            repository_subdir=subdir,
            estimated_usage_bytes=512,
            capacity_bytes=4096,
            source_config_count=1,
            source_config_ids=[config.id],
            status=RepositoryUsageShard.Status.SUCCESS,
            is_active=True,
            last_checked_at=timezone.now(),
            last_success_checked_at=timezone.now(),
        )

        sync_repository_usage(repo)

        run_probe.assert_not_called()
        shard = RepositoryUsageShard.objects.get(
            repository_id=repo.id,
            node_id=agent.id,
        )
        self.assertFalse(shard.is_active)
        self.assertEqual(shard.status, RepositoryUsageShard.Status.SKIPPED)
        self.assertEqual(shard.source_config_ids, [config.id])
        self.assertIn("ownership requires verification", shard.last_error)

    def test_unbound_nas_usage_sync_preserves_health_without_associated_sources(self):
        org = Organization.objects.create(key="usage-empty-org", name="Usage Empty Org")
        repo = Repository.objects.create(
            organization_id=org.id,
            name="direct-nas-empty",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.OFFLINE,
            config={"server_address": "10.0.0.10", "share_path": "/backup"},
        )

        sync_repository_usage(repo)

        repo.refresh_from_db()
        self.assertEqual(repo.health, Repository.Health.OFFLINE)
