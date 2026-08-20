from __future__ import annotations

import os
from datetime import timedelta
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.iam.models import Organization
from apps.node.models import Node, NodeTask
from apps.protection.models import BackupConfig
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.storage.conf import repository_health_interval_seconds
from apps.storage.periodic_tasks import register_periodic_tasks
from apps.storage.repositories.models import Repository, RepositoryLocationClaim
from apps.storage.services.interface import check_repository
from apps.storage.services.internal.repository_initializer import (
    RepositoryInitializationError,
)
from apps.storage.services.internal.nas_repository import (
    NASRepositoryError,
    check_proxy_nas_repository,
    nas_agent_repository_subdir,
    nas_proxy_repository_subdir,
)
from apps.storage.services.internal.repository_health import (
    project_repository_health_from_agent_result,
    probe_repository_health,
    probe_unbound_nas_repository_health,
)
from apps.storage.services.internal.repository_errors import (
    RepositoryHealthTransportUnconfirmed,
)
from apps.storage.services.internal.repository_location import (
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    mark_repository_location_residual,
    reserve_direct_nas_location,
    reserve_repository_location,
)
from apps.storage.services.internal.repository_ownership import (
    RepositoryOwnershipError,
)
from apps.storage.tasks import (
    check_storage_repository_health,
    dispatch_repository_health_checks,
    enqueue_startup_repository_health_checks,
)


class RepositoryHealthConfigurationTests(SimpleTestCase):
    def test_defaults_to_five_minutes(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STORAGE_REPOSITORY_HEALTH_INTERVAL_SECONDS", None)
            self.assertEqual(repository_health_interval_seconds(), 300)

    def test_accepts_configured_interval(self):
        with mock.patch.dict(
            os.environ,
            {"STORAGE_REPOSITORY_HEALTH_INTERVAL_SECONDS": "600"},
        ):
            self.assertEqual(repository_health_interval_seconds(), 600)

    def test_rejects_invalid_or_too_short_interval(self):
        for value in ("invalid", "59", "0"):
            with (
                self.subTest(value=value),
                mock.patch.dict(
                    os.environ,
                    {"STORAGE_REPOSITORY_HEALTH_INTERVAL_SECONDS": value},
                ),
            ):
                with self.assertRaises(ImproperlyConfigured):
                    repository_health_interval_seconds()

    @mock.patch("apps.storage.periodic_tasks.TASK_REGISTRY.add")
    @mock.patch("apps.storage.periodic_tasks.maintenance_settings")
    @mock.patch.dict(
        os.environ,
        {"STORAGE_REPOSITORY_HEALTH_INTERVAL_SECONDS": "420"},
    )
    def test_periodic_dispatcher_uses_configured_interval(
        self,
        maintenance_settings,
        registry_add,
    ):
        maintenance_settings.return_value = mock.Mock(
            scan_interval=timedelta(seconds=60),
            enabled=True,
        )

        register_periodic_tasks()

        health_call = next(
            call
            for call in registry_add.call_args_list
            if call.kwargs["name"] == "storage_dispatch_repository_health_checks"
        )
        self.assertEqual(
            health_call.kwargs["schedule"].run_every,
            timedelta(seconds=420),
        )
        recovery_call = next(
            call
            for call in registry_add.call_args_list
            if call.kwargs["name"] == "storage_reconcile_repository_operations"
        )
        self.assertEqual(
            recovery_call.kwargs["schedule"].run_every,
            timedelta(seconds=60),
        )
        self.assertTrue(recovery_call.kwargs["enabled"])


class RepositoryHealthTaskTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            key="health-task-org",
            name="Health Task Org",
        )

    def _repository(self, name: str, repo_type: str, **kwargs) -> Repository:
        return Repository.objects.create(
            organization_id=self.organization.id,
            name=name,
            repo_type=repo_type,
            status=kwargs.pop("status", Repository.Status.CREATED),
            health=kwargs.pop("health", Repository.Health.ONLINE),
            config=kwargs.pop("config", {}),
            **kwargs,
        )

    @mock.patch("apps.storage.tasks.check_storage_repository_health.apply_async")
    def test_dispatches_all_supported_created_repositories(
        self,
        apply_async,
    ):
        s3 = self._repository("s3", Repository.Type.S3, s3_bucket="bucket")
        local = self._repository(
            "local",
            Repository.Type.PROXY_FS,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=10,
        )
        bound_nas = self._repository(
            "bound-nas",
            Repository.Type.NAS,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=10,
        )
        direct_nas = self._repository("direct-nas", Repository.Type.NAS)
        self._repository(
            "failed-local",
            Repository.Type.PROXY_FS,
            status=Repository.Status.CREATE_FAILED,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=10,
        )

        result = dispatch_repository_health_checks.run()

        self.assertEqual(result["dispatched"], 4)
        dispatched_ids = {
            call.kwargs["kwargs"]["repository_id"]
            for call in apply_async.call_args_list
        }
        self.assertEqual(
            dispatched_ids,
            {s3.id, local.id, bound_nas.id, direct_nas.id},
        )

    @mock.patch("apps.storage.tasks.cache.delete")
    @mock.patch("apps.storage.tasks.cache.add", return_value=False)
    def test_repository_lock_skips_duplicate_check(self, _cache_add, cache_delete):
        repository = self._repository("s3", Repository.Type.S3, s3_bucket="bucket")

        result = check_storage_repository_health.run(repository_id=repository.id)

        self.assertTrue(result["locked"])
        cache_delete.assert_not_called()

    @mock.patch("apps.storage.tasks.check_storage_repository_health.apply_async")
    @mock.patch("apps.storage.tasks.cache.delete")
    @mock.patch("apps.storage.tasks.cache.add", return_value=True)
    @mock.patch(
        "apps.storage.tasks.probe_repository_health",
        side_effect=RuntimeError("network down"),
    )
    def test_single_failure_keeps_health_and_schedules_one_retry(
        self,
        _probe,
        _cache_add,
        cache_delete,
        apply_async,
    ):
        checked_at = timezone.now() - timedelta(hours=1)
        repository = self._repository(
            "s3",
            Repository.Type.S3,
            s3_bucket="bucket",
            last_checked_at=checked_at,
            capacity_bytes=1000,
            estimated_usage_bytes=250,
        )
        original_updated_at = repository.updated_at

        result = check_storage_repository_health.run(repository_id=repository.id)

        repository.refresh_from_db()
        self.assertEqual(result["status"], Repository.Health.ONLINE)
        self.assertTrue(result["retry_scheduled"])
        self.assertEqual(repository.health, Repository.Health.ONLINE)
        self.assertEqual(repository.health_failures, 1)
        self.assertEqual(repository.last_checked_at, checked_at)
        self.assertEqual(repository.updated_at, original_updated_at)
        self.assertEqual(repository.capacity_bytes, 1000)
        self.assertEqual(repository.estimated_usage_bytes, 250)
        cache_delete.assert_called_once()
        apply_async.assert_called_once_with(
            kwargs={"repository_id": repository.id, "retry_attempt": 1},
            countdown=30,
        )

    @mock.patch("apps.storage.tasks.cache.delete")
    @mock.patch("apps.storage.tasks.cache.add", return_value=True)
    @mock.patch(
        "apps.storage.tasks.probe_repository_health",
        side_effect=RuntimeError("network down"),
    )
    def test_retry_failure_marks_repository_offline(
        self, _probe, _cache_add, _cache_delete
    ):
        repository = self._repository(
            "s3",
            Repository.Type.S3,
            s3_bucket="bucket",
            health=Repository.Health.ONLINE,
            health_failures=1,
        )

        result = check_storage_repository_health.run(
            repository_id=repository.id,
            retry_attempt=1,
        )

        repository.refresh_from_db()
        self.assertEqual(result["status"], Repository.Health.OFFLINE)
        self.assertEqual(repository.health, Repository.Health.OFFLINE)
        self.assertEqual(repository.health_failures, 2)

    @mock.patch("apps.storage.tasks.check_storage_repository_health.apply_async")
    @mock.patch("apps.storage.tasks.cache.delete")
    @mock.patch("apps.storage.tasks.cache.add", return_value=True)
    @mock.patch(
        "apps.storage.tasks.probe_repository_health",
        side_effect=RepositoryHealthTransportUnconfirmed("result ACK congested"),
    )
    def test_transport_unknown_preserves_health_without_retry(
        self,
        _probe,
        _cache_add,
        _cache_delete,
        apply_async,
    ):
        repository = self._repository(
            "local",
            Repository.Type.PROXY_FS,
            health=Repository.Health.ONLINE,
            health_failures=1,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=10,
        )

        result = check_storage_repository_health.run(repository_id=repository.id)

        repository.refresh_from_db()
        self.assertEqual(result["status"], Repository.Health.ONLINE)
        self.assertEqual(result["probe_status"], "transport_unknown")
        self.assertEqual(repository.health, Repository.Health.ONLINE)
        self.assertEqual(repository.health_failures, 1)
        apply_async.assert_not_called()

    @mock.patch("apps.storage.tasks.cache.delete")
    @mock.patch("apps.storage.tasks.cache.add", return_value=True)
    @mock.patch(
        "apps.storage.tasks.probe_repository_health",
        return_value=Repository.Health.ONLINE,
    )
    def test_success_changes_only_health(
        self,
        _probe,
        _cache_add,
        _cache_delete,
    ):
        checked_at = timezone.now() - timedelta(hours=1)
        repository = self._repository(
            "s3",
            Repository.Type.S3,
            health=Repository.Health.OFFLINE,
            s3_bucket="bucket",
            config={"endpoint": "https://s3.example.com"},
            last_checked_at=checked_at,
            capacity_bytes=1000,
            estimated_usage_bytes=250,
        )
        original_updated_at = repository.updated_at
        original_config = dict(repository.config)

        result = check_storage_repository_health.run(repository_id=repository.id)

        repository.refresh_from_db()
        self.assertEqual(result["status"], Repository.Health.ONLINE)
        self.assertEqual(repository.health, Repository.Health.ONLINE)
        self.assertEqual(repository.health_failures, 0)
        self.assertEqual(repository.last_checked_at, checked_at)
        self.assertEqual(repository.updated_at, original_updated_at)
        self.assertEqual(repository.config, original_config)
        self.assertEqual(repository.capacity_bytes, 1000)
        self.assertEqual(repository.estimated_usage_bytes, 250)

    @mock.patch("apps.storage.tasks.dispatch_repository_health_checks.apply_async")
    def test_worker_ready_enqueues_startup_health_dispatch(self, apply_async):
        enqueue_startup_repository_health_checks()

        apply_async.assert_called_once_with(kwargs={"startup": True})

    @mock.patch("apps.storage.tasks.cache.add", return_value=False)
    def test_duplicate_startup_dispatch_is_skipped(self, cache_add):
        result = dispatch_repository_health_checks.run(startup=True)

        self.assertEqual(result["dispatched"], 0)
        self.assertEqual(result["skipped"], "duplicate_startup")
        cache_add.assert_called_once()


class UnboundNASRepositoryHealthTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            key="direct-nas-health-org",
            name="Direct NAS Health Org",
        )
        self.repository = Repository.objects.create(
            organization_id=self.organization.id,
            name="direct-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            config={
                "server_address": "10.0.0.10",
                "share_path": "/backup",
                "kopia_password": "repo-pass",
            },
        )

    def _node(self, name: str, *, role: str = Node.Role.AGENT, online: bool = True):
        return Node.objects.create(
            organization=self.organization,
            name=name,
            role=role,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE
            if online
            else Node.Availability.OFFLINE,
            ip_address="10.0.1.1",
        )

    def _agent_config(self, node: Node, *, name: str):
        config = BackupConfig.objects.create(
            organization_id=self.organization.id,
            name=name,
            source_type="agent",
            source_ref_id=node.id,
            repository_id=self.repository.id,
        )
        self._mark_location_owned(node.id)
        return config

    def _nas_config(self, source: SourceResource, *, name: str):
        config = BackupConfig.objects.create(
            organization_id=self.organization.id,
            name=name,
            source_type="nas",
            source_ref_id=source.id,
            repository_id=self.repository.id,
        )
        self._mark_location_owned(source.bound_node_id)
        return config

    def _mark_location_owned(self, node_id: int) -> None:
        subdir = nas_agent_repository_subdir(node_id)
        reserve_direct_nas_location(
            repository=self.repository,
            node_id=node_id,
            repository_subdir=subdir,
        )
        mark_repository_location_owned(
            self.repository,
            owner_node_id=node_id,
            repository_subdir=subdir,
        )
        mark_repository_location_ownership_verified(
            self.repository,
            owner_node_id=node_id,
            repository_subdir=subdir,
        )

    @staticmethod
    def _agent_outcome(status: str):
        return mock.Mock(
            task=mock.Mock(id="node-task", status=status, last_error=""),
            result={"ownership_verified": status == "success"},
            timed_out=False,
            ok=status == "success",
        )

    @mock.patch("apps.storage.services.internal.repository_health.run_agent_task_sync")
    def test_without_associations_stays_unverified_without_agent_task(self, run_agent):
        health = probe_unbound_nas_repository_health(self.repository)

        self.assertEqual(health, Repository.Health.UNVERIFIED)
        run_agent.assert_not_called()

    @mock.patch("apps.storage.services.internal.repository_health.run_agent_task_sync")
    def test_agent_path_success_marks_repository_online(self, run_agent):
        agent = self._node("agent-a")
        self._agent_config(agent, name="agent-config")
        run_agent.return_value = self._agent_outcome("success")

        health = probe_unbound_nas_repository_health(self.repository)

        self.assertEqual(health, Repository.Health.ONLINE)
        run_agent.assert_called_once()
        call = run_agent.call_args.kwargs
        self.assertEqual(call["node_id"], agent.id)
        self.assertEqual(call["kind"], "repo.status")
        self.assertTrue(call["payload"]["health_only"])
        self.assertEqual(
            call["payload"]["repository"]["subdir"],
            nas_agent_repository_subdir(agent.id),
        )

    @mock.patch("apps.storage.services.internal.repository_health.run_agent_task_sync")
    def test_legacy_unverified_agent_path_is_adopted_by_health_probe(
        self,
        run_agent,
    ):
        agent = self._node("legacy-agent")
        BackupConfig.objects.create(
            organization_id=self.organization.id,
            name="legacy-agent-config",
            source_type="agent",
            source_ref_id=agent.id,
            repository_id=self.repository.id,
        )
        subdir = nas_agent_repository_subdir(agent.id)
        claim = reserve_direct_nas_location(
            repository=self.repository,
            node_id=agent.id,
            repository_subdir=subdir,
        )
        mark_repository_location_owned(
            self.repository,
            owner_node_id=agent.id,
            repository_subdir=subdir,
        )
        claim.legacy_adoption_required = True
        claim.save(update_fields=["legacy_adoption_required", "updated_at"])
        run_agent.return_value = self._agent_outcome("success")

        health = probe_unbound_nas_repository_health(self.repository)

        self.assertEqual(health, Repository.Health.ONLINE)
        claim.refresh_from_db()
        self.assertIsNotNone(claim.ownership_verified_at)
        self.assertTrue(
            run_agent.call_args.kwargs["payload"]["allow_ownership_adoption"]
        )

    @mock.patch("apps.storage.services.internal.repository_health.run_agent_task_sync")
    def test_direct_nas_health_requires_explicit_ownership_proof(self, run_agent):
        agent = self._node("old-agent")
        BackupConfig.objects.create(
            organization_id=self.organization.id,
            name="old-agent-config",
            source_type="agent",
            source_ref_id=agent.id,
            repository_id=self.repository.id,
        )
        subdir = nas_agent_repository_subdir(agent.id)
        claim = reserve_direct_nas_location(
            repository=self.repository,
            node_id=agent.id,
            repository_subdir=subdir,
        )
        mark_repository_location_owned(
            self.repository,
            owner_node_id=agent.id,
            repository_subdir=subdir,
        )
        run_agent.return_value = mock.Mock(
            task=mock.Mock(id="node-task", status="success", last_error=""),
            result={},
            timed_out=False,
            ok=True,
        )

        health = probe_unbound_nas_repository_health(self.repository)

        self.assertEqual(health, Repository.Health.OFFLINE)
        claim.refresh_from_db()
        self.assertIsNone(claim.ownership_verified_at)

    @mock.patch("apps.storage.services.internal.repository_health.run_agent_task_sync")
    def test_legacy_direct_nas_health_stays_online_with_old_agent(self, run_agent):
        agent = self._node("legacy-compatible-agent")
        self._agent_config(agent, name="legacy-compatible-config")
        claim = RepositoryLocationClaim.objects.get(
            repository=self.repository,
            owner_node_id=agent.id,
            scope=RepositoryLocationClaim.Scope.DIRECT_NAS_AGENT,
        )
        claim.legacy_adoption_required = True
        claim.ownership_verified_at = None
        claim.save(update_fields=["legacy_adoption_required", "ownership_verified_at"])
        run_agent.return_value = mock.Mock(
            task=mock.Mock(id="old-agent-task", status="success", last_error=""),
            result={},
            timed_out=False,
            ok=True,
        )

        health = probe_unbound_nas_repository_health(self.repository)

        self.assertEqual(health, Repository.Health.ONLINE)
        claim.refresh_from_db()
        self.assertIsNone(claim.ownership_verified_at)

    @mock.patch("apps.storage.services.internal.repository_health.run_agent_task_sync")
    def test_all_execution_paths_failed_marks_repository_offline(self, run_agent):
        agent_a = self._node("agent-a")
        agent_b = self._node("agent-b")
        self._agent_config(agent_a, name="agent-config-a")
        self._agent_config(agent_b, name="agent-config-b")
        run_agent.side_effect = [
            self._agent_outcome("failed"),
            self._agent_outcome("failed"),
        ]

        health = probe_unbound_nas_repository_health(self.repository)

        self.assertEqual(health, Repository.Health.OFFLINE)
        self.assertEqual(run_agent.call_count, 2)

    @mock.patch("apps.storage.services.internal.repository_health.run_agent_task_sync")
    def test_any_success_marks_repository_online(self, run_agent):
        agent_a = self._node("agent-a")
        agent_b = self._node("agent-b")
        self._agent_config(agent_a, name="agent-config-a")
        self._agent_config(agent_b, name="agent-config-b")
        run_agent.side_effect = [
            RuntimeError("agent request timed out"),
            self._agent_outcome("success"),
        ]

        health = probe_unbound_nas_repository_health(self.repository)

        self.assertEqual(health, Repository.Health.ONLINE)
        self.assertEqual(run_agent.call_count, 2)

    @mock.patch("apps.storage.services.internal.repository_health.run_agent_task_sync")
    def test_nas_source_uses_its_bound_proxy(self, run_agent):
        proxy = self._node("proxy-a", role=Node.Role.PROXY)
        source = SourceResource.objects.create(
            organization=self.organization,
            name="nas-source",
            resource_type=ResourceType.NAS,
            bound_node=proxy,
        )
        self._nas_config(source, name="nas-config")
        run_agent.return_value = self._agent_outcome("success")

        health = probe_unbound_nas_repository_health(self.repository)

        self.assertEqual(health, Repository.Health.ONLINE)
        self.assertEqual(run_agent.call_args.kwargs["node_id"], proxy.id)
        self.assertEqual(
            run_agent.call_args.kwargs["payload"]["repository"]["subdir"],
            nas_agent_repository_subdir(proxy.id),
        )

    @mock.patch("apps.storage.services.internal.repository_health.run_agent_task_sync")
    def test_sources_on_same_execution_node_are_deduplicated(self, run_agent):
        proxy = self._node("proxy-a", role=Node.Role.PROXY)
        source_a = SourceResource.objects.create(
            organization=self.organization,
            name="nas-source-a",
            resource_type=ResourceType.NAS,
            bound_node=proxy,
        )
        source_b = SourceResource.objects.create(
            organization=self.organization,
            name="nas-source-b",
            resource_type=ResourceType.NAS,
            bound_node=proxy,
        )
        self._nas_config(source_a, name="nas-config-a")
        self._nas_config(source_b, name="nas-config-b")
        run_agent.return_value = self._agent_outcome("success")

        health = probe_unbound_nas_repository_health(self.repository)

        self.assertEqual(health, Repository.Health.ONLINE)
        run_agent.assert_called_once()

    @mock.patch("apps.storage.services.internal.repository_health.run_agent_task_sync")
    def test_offline_execution_node_counts_as_unavailable(self, run_agent):
        agent = self._node("agent-a", online=False)
        self._agent_config(agent, name="agent-config")

        with self.assertRaises(RepositoryHealthTransportUnconfirmed):
            probe_unbound_nas_repository_health(self.repository)
        run_agent.assert_not_called()

    @mock.patch("apps.storage.services.internal.repository_health.run_agent_task_sync")
    def test_timed_out_execution_path_is_transport_unknown(self, run_agent):
        agent = self._node("agent-timeout")
        self._agent_config(agent, name="agent-timeout-config")
        run_agent.return_value = mock.Mock(
            task=mock.Mock(
                id="node-task-timeout",
                status="timeout",
                last_error="watchdog timeout (no progress)",
                accepted_at=timezone.now(),
            ),
            result={},
            timed_out=False,
            ok=False,
        )

        with self.assertRaises(RepositoryHealthTransportUnconfirmed):
            probe_unbound_nas_repository_health(self.repository)

    @mock.patch("apps.storage.services.internal.repository_health.run_agent_task_sync")
    def test_residual_location_is_rechecked_without_ownership_adoption(self, run_agent):
        agent = self._node("agent-a")
        self._agent_config(agent, name="agent-config")
        mark_repository_location_residual(
            self.repository,
            owner_node_id=agent.id,
            repository_subdir=nas_agent_repository_subdir(agent.id),
        )
        RepositoryLocationClaim.objects.filter(
            repository=self.repository,
            owner_node_id=agent.id,
            scope=RepositoryLocationClaim.Scope.DIRECT_NAS_AGENT,
        ).update(ownership_verified_at=None)

        health = probe_unbound_nas_repository_health(self.repository)

        self.assertEqual(health, Repository.Health.OFFLINE)
        run_agent.assert_called_once()
        self.assertFalse(
            run_agent.call_args.kwargs["payload"]["allow_ownership_adoption"]
        )


class RepositoryHealthResultProjectionTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            key="repository-health-projection-org",
            name="Repository Health Projection Org",
        )
        self.proxy = Node.objects.create(
            organization=self.organization,
            name="projection-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.repository = Repository.objects.create(
            organization_id=self.organization.id,
            name="projection-local",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.OFFLINE,
            health_failures=2,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.proxy.id,
        )

    def _repo_status_task(self) -> NodeTask:
        return NodeTask.objects.create(
            organization=self.organization,
            node=self.proxy,
            kind="repo.status",
            correlation_type="storage_repository",
            correlation_id=str(self.repository.id),
            status=NodeTask.Status.SUCCESS,
            result={"ownership_verified": True},
            watchdog_deadline_at=timezone.now(),
        )

    def test_current_late_success_restores_online_health(self):
        task = self._repo_status_task()

        projected = project_repository_health_from_agent_result(node_task=task)

        self.assertTrue(projected)
        self.repository.refresh_from_db()
        self.assertEqual(self.repository.health, Repository.Health.ONLINE)
        self.assertEqual(self.repository.health_failures, 0)

    def test_stale_result_after_repository_configuration_change_is_ignored(self):
        task = self._repo_status_task()
        self.repository.config = {"proxy_node_dir": "/new-location"}
        self.repository.save(update_fields=["config", "updated_at"])

        projected = project_repository_health_from_agent_result(node_task=task)

        self.assertFalse(projected)
        self.repository.refresh_from_db()
        self.assertEqual(self.repository.health, Repository.Health.OFFLINE)
        self.assertEqual(self.repository.health_failures, 2)

class RepositoryHealthProbeTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            key="repository-health-probe-org",
            name="Repository Health Probe Org",
        )

    @mock.patch(
        "apps.storage.services.internal.repository_health.check_proxy_nas_repository"
    )
    def test_bound_nas_probe_requests_health_only(self, check_proxy_nas):
        repository = Repository.objects.create(
            organization_id=self.organization.id,
            name="bound-nas",
            repo_type=Repository.Type.NAS,
            status=Repository.Status.CREATED,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=10,
        )

        health = probe_repository_health(repository)

        self.assertEqual(health, Repository.Health.ONLINE)
        check_proxy_nas.assert_called_once_with(repository, health_only=True)

    @mock.patch(
        "apps.storage.services.internal.repository_location.mark_repository_location_ownership_verified"
    )
    @mock.patch("apps.storage.services.internal.nas_repository.run_agent_task_sync")
    def test_bound_nas_health_probe_does_not_sync_mount_path(
        self, run_agent, mark_ownership_verified
    ):
        proxy = Node.objects.create(
            organization=self.organization,
            name="proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        repository = Repository.objects.create(
            organization_id=self.organization.id,
            name="bound-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={
                "server_address": "10.0.0.10",
                "share_path": "/backup",
                "kopia_password": "repo-pass",
            },
        )
        original_updated_at = repository.updated_at
        original_config = dict(repository.config)
        run_agent.return_value = mock.Mock(
            task=mock.Mock(id="node-task", status="success", last_error=""),
            result={
                "mount_point": "/new/mount/path",
                "ownership_verified": True,
            },
            timed_out=False,
            ok=True,
        )

        check_proxy_nas_repository(repository, health_only=True)

        repository.refresh_from_db()
        self.assertEqual(repository.config, original_config)
        self.assertEqual(repository.updated_at, original_updated_at)
        self.assertTrue(run_agent.call_args.kwargs["payload"]["health_only"])
        mark_ownership_verified.assert_called_once_with(
            repository,
            owner_node_id=proxy.id,
            repository_subdir=nas_proxy_repository_subdir(repository),
        )

    @mock.patch("apps.storage.services.internal.nas_repository.run_agent_task_sync")
    def test_bound_nas_health_requires_explicit_ownership_proof(self, run_agent):
        proxy = Node.objects.create(
            organization=self.organization,
            name="old-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        repository = Repository.objects.create(
            organization_id=self.organization.id,
            name="bound-nas-without-proof",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={
                "server_address": "10.0.0.10",
                "share_path": "/backup",
                "kopia_password": "repo-pass",
            },
        )
        run_agent.return_value = mock.Mock(
            task=mock.Mock(id="node-task", status="success", last_error=""),
            result={},
            timed_out=False,
            ok=True,
        )

        with self.assertRaises(NASRepositoryError):
            check_proxy_nas_repository(repository, health_only=True)

    @mock.patch("apps.storage.services.interface.sync_repository_usage")
    @mock.patch(
        "apps.storage.services.interface.probe_unbound_nas_repository_health",
        return_value=Repository.Health.ONLINE,
    )
    def test_manual_unbound_nas_check_uses_direct_probe(
        self,
        direct_probe,
        sync_usage,
    ):
        repository = Repository.objects.create(
            organization_id=self.organization.id,
            name="direct-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
        )
        sync_usage.side_effect = lambda value: value

        checked = check_repository(repository=repository)

        repository.refresh_from_db()
        self.assertEqual(checked.health, Repository.Health.ONLINE)
        self.assertEqual(repository.health, Repository.Health.ONLINE)
        self.assertIsNotNone(repository.last_checked_at)
        direct_probe.assert_called_once_with(repository)
        sync_usage.assert_called_once()

    @mock.patch("apps.storage.services.interface.check_s3_repository")
    def test_manual_s3_check_invalidates_claim_on_ownership_failure(
        self,
        check_s3_repository,
    ):
        repository = Repository.objects.create(
            organization_id=self.organization.id,
            name="s3-missing-owner-marker",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="bucket",
            config={
                "endpoint": "s3.example.test",
                "prefix": "hfl/",
                "access_key_id": "account",
            },
        )
        claim = reserve_repository_location(repository)
        mark_repository_location_owned(repository)
        mark_repository_location_ownership_verified(repository)
        ownership_error = RepositoryOwnershipError("ownership marker is missing")
        initialization_error = RepositoryInitializationError(
            "ownership marker is missing"
        )
        initialization_error.__cause__ = ownership_error
        check_s3_repository.side_effect = initialization_error

        with self.assertRaises(DRFValidationError):
            check_repository(repository=repository)

        repository.refresh_from_db()
        claim.refresh_from_db()
        self.assertEqual(repository.health, Repository.Health.OFFLINE)
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)
