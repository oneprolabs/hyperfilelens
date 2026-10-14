import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from apps.storage.conf import health_storage_concurrency, repository_usage_interval_seconds
from apps.storage.services.internal import kopia_cli
from apps.storage.services.internal.background_capacity import try_acquire_background_storage_capacity
from apps.storage.services.internal.repository_sampling import finish_sample, reserve_sample, sampling_slot
from apps.storage.services.internal.repository_usage import enqueue_repository_usage_refresh
from apps.iam.models import Organization
from apps.storage.repositories.models import Repository


def _fake_connection(_repository, _args, *, config_file, **_kwargs):
    config_file.write_text(json.dumps({
        "readonly": True,
        "storage": {"type": "s3", "config": {"accessKeyID": "fixture-id", "secretAccessKey": "fixture-secret"}},
        "caching": {},
    }))
    return SimpleNamespace(returncode=0, stdout="", stderr="")


class CollectionConnectionTests(SimpleTestCase):
    def test_usage_lease_loss_fences_results_and_resets_context(self):
        active = [True]
        with kopia_cli.usage_collection_execution(lambda: active[0]):
            kopia_cli.assert_usage_collection_active()
            active[0] = False
            with self.assertRaises(kopia_cli.KopiaExecutionLeaseLost):
                kopia_cli.assert_usage_collection_active()
        kopia_cli.assert_usage_collection_active()

    @mock.patch.object(kopia_cli, "_s3_connection_fingerprint", return_value="new-target")
    @mock.patch.object(kopia_cli, "_s3_flags", return_value=[])
    @mock.patch.object(kopia_cli, "_run_repository_command_unlocked")
    def test_changed_target_invalidates_config_without_removing_cache(self, command, _flags, _fingerprint):
        command.side_effect = _fake_connection
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "usage.repository.config"
            config.write_text(json.dumps({
                "readonly": True, "storage": {"type": "s3"},
                "caching": {"cacheDirectory": "cache"},
            }))
            kopia_cli._connection_fingerprint_file(config).write_text("old-target")
            sentinel = Path(directory) / "cached-index"
            sentinel.write_text("cache")
            kopia_cli.ensure_usage_connection(SimpleNamespace(id=7, repo_type="s3"), config_file=config)
            self.assertTrue(sentinel.exists())
            self.assertTrue(config.exists())
            self.assertNotIn("secretAccessKey", json.loads(config.read_text())["storage"]["config"])
        command.assert_called_once()

    @mock.patch.object(kopia_cli, "_invalidate_changed_s3_connection")
    @mock.patch.object(kopia_cli, "_run_repository_command_unlocked")
    def test_matching_readonly_config_skips_connect(self, command, invalidate):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "usage.repository.config"
            config.write_text(json.dumps({"readonly": True, "storage": {"type": "s3"}, "caching": {"cacheDirectory": "cache"}}))
            kopia_cli.ensure_usage_connection(SimpleNamespace(id=7), config_file=config)
        invalidate.assert_called_once()
        command.assert_not_called()

    @mock.patch.object(kopia_cli, "_write_s3_connection_fingerprint")
    @mock.patch.object(kopia_cli, "_s3_flags", return_value=[])
    @mock.patch.object(kopia_cli, "_invalidate_changed_s3_connection")
    @mock.patch.object(kopia_cli, "_run_repository_command_unlocked")
    def test_missing_or_writable_config_connects_readonly(self, command, _invalidate, _flags, _fingerprint):
        command.side_effect = _fake_connection
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "usage.repository.config"
            config.write_text(json.dumps({"readonly": False, "storage": {}}))
            kopia_cli.ensure_usage_connection(SimpleNamespace(id=7), config_file=config)
        self.assertIn("--readonly", command.call_args.args[1])

    @mock.patch.object(kopia_cli.subprocess, "run")
    def test_capability_probe_cached_per_binary_revision(self, run):
        kopia_cli._content_stats_supports_json.cache_clear()
        run.return_value = SimpleNamespace(returncode=0, stdout="--raw", stderr="")
        self.assertFalse(kopia_cli._content_stats_supports_json("/test/kopia", 1))
        self.assertFalse(kopia_cli._content_stats_supports_json("/test/kopia", 1))
        self.assertFalse(kopia_cli._content_stats_supports_json("/test/kopia", 2))
        self.assertEqual(run.call_count, 2)
        kopia_cli._content_stats_supports_json.cache_clear()


class CollectionCadenceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_failed_collection_does_not_reopen_root_attempt(self):
        repository = SimpleNamespace(id=75, organization_id=22, usage_last_success_at=None)
        now = datetime(2026, 10, 13, 10, 29, tzinfo=UTC)
        key, _slot = reserve_sample(repository, "usage", 1800, now=now)
        with mock.patch.object(cache, "touch") as touch:
            finish_sample(key, successful=False)
        touch.assert_not_called()
        self.assertIsNone(reserve_sample(repository, "usage", 1800, now=now))
        self.assertIsNotNone(reserve_sample(
            repository, "usage", 1800, now=now + timedelta(minutes=30),
        ))

    def test_capacity_deferrals_have_cycle_wide_attempt_limit(self):
        repository = SimpleNamespace(id=75, organization_id=22, usage_last_success_at=None)
        now = datetime(2026, 10, 13, 10, 29, tzinfo=UTC)
        for _ in range(3):
            key, _slot = reserve_sample(repository, "usage", 1800, now=now)
            finish_sample(key, successful=False, deferred=True)
            # Expire only the cooldown, not the cycle attempt counter.
            cache.delete(key)
        self.assertIsNone(reserve_sample(repository, "usage", 1800, now=now))
        self.assertIsNotNone(reserve_sample(
            repository, "usage", 1800, now=now + timedelta(minutes=30),
        ))

    def test_fixed_offsets_do_not_drift_after_late_completion(self):
        now = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
        slots = [sampling_slot(75, 1800, now=now + timedelta(minutes=30 * i)) for i in range(3)]
        self.assertEqual(slots[1][1] - slots[0][1], 1800)
        self.assertEqual(slots[2][1] - slots[1][1], 1800)
        self.assertLess(slots[0][1] - slots[0][0], 1620)

    def test_reservation_deduplicates_and_cache_loss_can_recover(self):
        repository = SimpleNamespace(id=75, organization_id=22, usage_last_success_at=None)
        now = datetime(2026, 10, 13, 10, 29, tzinfo=UTC)
        self.assertIsNotNone(reserve_sample(repository, "usage", 1800, now=now))
        self.assertIsNone(reserve_sample(repository, "usage", 1800, now=now))
        cache.clear()
        self.assertIsNotNone(reserve_sample(repository, "usage", 1800, now=now))
        repository.usage_last_success_at = now
        cache.clear()
        self.assertIsNone(reserve_sample(repository, "usage", 1800, now=now))

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_defaults_and_independent_health_override(self):
        self.assertEqual(repository_usage_interval_seconds(), 1800)
        self.assertEqual(health_storage_concurrency(), 1)
        os.environ["CELERY_WORKER_CONCURRENCY"] = "4"
        os.environ["CELERY_STORAGE_HEALTH_CONCURRENCY"] = "2"
        self.assertEqual(health_storage_concurrency(), 2)

    @mock.patch("apps.storage.services.internal.background_capacity._redis_client")
    def test_three_pools_use_different_keys(self, redis_client):
        redis_client.return_value.eval.return_value = 1
        keys = set()
        for operation in ("repository-health", "repository-usage-sync", "repository-maintenance"):
            lease = try_acquire_background_storage_capacity(operation=operation, identity="75")
            keys.add(lease.capacity_key)
        self.assertEqual(len(keys), 3)


class CollectionCoalescingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(key="collection-phase1", name="Collection")
        self.repository = Repository.objects.create(
            organization_id=self.org.id, name="observation",
            repo_type=Repository.Type.S3, status=Repository.Status.CREATED,
            s3_bucket="isolated-test", config={},
        )

    @mock.patch("apps.storage.tasks.reconcile_storage_repositories.apply_async")
    def test_batch_and_completion_requests_share_repository_fence(self, enqueue):
        enqueue.return_value = SimpleNamespace(id="collection-1")
        first = enqueue_repository_usage_refresh(organization_id=self.org.id)
        second = enqueue_repository_usage_refresh(
            organization_id=self.org.id, repository_ids=[self.repository.id],
            trigger="backup.completed",
        )
        self.assertTrue(first["queued"])
        self.assertTrue(second["deduplicated"])
        enqueue.assert_called_once()
        key = f"storage:usage-pending:{self.org.id}:{self.repository.id}"
        self.assertTrue(cache.get(f"{key}:followup"))

    @mock.patch("apps.storage.tasks.reconcile_storage_repositories.apply_async")
    def test_failed_publication_does_not_permanently_suppress_refresh(self, enqueue):
        enqueue.side_effect = RuntimeError("isolated broker unavailable")
        enqueue_repository_usage_refresh(organization_id=self.org.id)
        enqueue.side_effect = None
        enqueue.return_value = SimpleNamespace(id="recovered")
        self.assertTrue(enqueue_repository_usage_refresh(organization_id=self.org.id)["queued"])

    @mock.patch("apps.storage.tasks.reconcile_storage_repositories.apply_async")
    def test_foreign_organization_cannot_enqueue_repository(self, enqueue):
        self.assertFalse(enqueue_repository_usage_refresh(
            organization_id=self.org.id + 1, repository_ids=[self.repository.id],
        )["queued"])
        enqueue.assert_not_called()

    @mock.patch("apps.storage.tasks.sync_all_repositories")
    @mock.patch("apps.storage.tasks.reconcile_storage_repositories.apply_async")
    def test_periodic_due_check_never_runs_heavy_collection(self, enqueue, sync_all):
        from apps.storage.tasks import reconcile_storage_repositories

        result = reconcile_storage_repositories.run(
            periodic=True, sampling_interval_seconds=60,
        )
        self.assertEqual(result["queued"], 1)
        self.assertEqual(enqueue.call_args.kwargs["kwargs"]["repository_ids"], [self.repository.id])
        sync_all.assert_not_called()

    @mock.patch("apps.storage.tasks.sync_organization_repositories")
    def test_duplicate_delivery_does_not_run_second_collection(self, collect):
        from apps.storage.tasks import reconcile_storage_repositories

        key = f"storage:usage-active:{self.org.id}:{self.repository.id}"
        cache.set(key, True, timeout=600)
        result = reconcile_storage_repositories.run(
            organization_id=self.org.id, repository_ids=[self.repository.id],
        )
        self.assertEqual(result["status"], "deduplicated")
        collect.assert_not_called()

    @mock.patch("apps.storage.services.internal.repository_usage.apply_repository_usage_probe")
    @mock.patch("apps.storage.services.internal.repository_usage.collect_usage_candidates")
    def test_lease_loss_while_waiting_for_database_lock_cannot_publish(self, collect, publish):
        from apps.storage.services.internal.repository_usage import sync_repository_usage

        active = [True]

        def locked_repository(**_kwargs):
            active[0] = False
            return self.repository

        with (
            kopia_cli.usage_collection_execution(lambda: active[0]),
            mock.patch.object(Repository.objects, "select_for_update") as locked,
        ):
            locked.return_value.get.side_effect = locked_repository
            with self.assertRaises(kopia_cli.KopiaExecutionLeaseLost):
                sync_repository_usage(self.repository)
        collect.assert_called_once()
        publish.assert_not_called()
