from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node.models import Node, NodeTask
from apps.protection.models import (
    BackupConfig,
    BackupConfigCreateRequest,
    BackupConfigDirectory,
    BackupPolicy,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
    FileFilterRule,
)
from apps.protection.services import backup_config as backup_config_service
from apps.protection.services.backup_config_reset import (
    ensure_backup_config_reset_task,
    reconcile_stuck_backup_config_reset_tasks,
    run_backup_config_reset_task,
)
from apps.protection.services.backup_source_snapshot import create_source_snapshot
from apps.protection.services.repository_policy import (
    sync_backup_config_repository_policy,
)
from apps.restore.models import RestorePlan
from apps.source.constants import ResourceType
from apps.source.models import SourceBackupPipelineEntry, SourceResource
from apps.storage.repositories.models import (
    Repository,
    RepositoryLocationClaim,
    RepositoryUsageShard,
)
from apps.storage.services.internal.repository_errors import (
    REPOSITORY_ALREADY_EXISTS_CODE,
)
from apps.storage.services.internal.repository_location import (
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    mark_repository_location_residual,
    reserve_direct_nas_location,
    reserve_repository_location,
)
from apps.task.models import Task, TaskResource
from common.errors import AppError


class ProtectionBackupConfigApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="backup-config-api@test.local",
            email="backup-config-api@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(
            key="backup-config-test-org", name="Backup Config Test Org"
        )
        Membership.objects.create(
            user=self.user, organization=self.org, role=Membership.Role.ADMIN
        )
        self.agent = Node.objects.create(
            organization=self.org,
            name="agent-backup-config-1",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.41",
            os_name="linux",
            metadata={"inventory": {"capabilities": ["repository_ownership_v1"]}},
        )
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="backup-config-repo",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="backup-config-bucket",
            config={
                "endpoint": "s3.example.internal:9000",
                "region": "cn-test-1",
                "prefix": "kopia/config",
                "access_key_id": "ak-test",
                "secret_access_key": "sk-test",
                "kopia_password": "123456",
                "use_tls": False,
            },
        )
        self._mark_repository_owned(self.repository)
        self.client.force_authenticate(user=self.user)

    def _mark_repository_owned(self, repository: Repository) -> Repository:
        reserve_repository_location(repository)
        mark_repository_location_owned(repository)
        mark_repository_location_ownership_verified(repository)
        return repository

    def _headers(self):
        return {"HTTP_X_ORG_KEY": self.org.key}

    def _error_fields(self, response) -> set[str]:
        data = response.data if isinstance(response.data, dict) else {}
        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        errors = payload.get("errors") if isinstance(payload, dict) else []
        if isinstance(errors, list):
            return {
                str(error.get("field") or "")
                for error in errors
                if isinstance(error, dict)
            }
        return set(data.keys())

    def _payload(
        self, *, source_ref_id: int | None = None, name: str = "Agent backup config"
    ):
        return {
            "name": name,
            "remark": "",
            "source_type": "agent",
            "source_ref_id": source_ref_id or self.agent.id,
            "repository_id": self.repository.id,
            "compression_level": "balanced",
            "directories": [{"path": "/data"}],
        }

    def _direct_nas_repository(self, *, name: str = "direct-nas-repo"):
        return Repository.objects.create(
            organization_id=self.org.id,
            name=name,
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            config={
                "server_address": "10.0.0.15",
                "share_path": "/volume1/backup",
                "kopia_password": "repo-pass",
            },
        )

    def _distinct_endpoint_repository(
        self,
        *,
        name: str = "distinct-endpoint-repo",
    ) -> Repository:
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name=name,
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.ALIYUN,
            s3_bucket=f"{name}-bucket",
            config={
                "endpoint": "oss-cn-hangzhou.aliyuncs.com",
                "external_endpoint": "oss-cn-hangzhou.aliyuncs.com",
                "internal_endpoint": "oss-cn-hangzhou-internal.aliyuncs.com",
                "region": "cn-hangzhou",
                "prefix": "kopia/config",
                "access_key_id": "ak-test",
                "secret_access_key": "sk-test",
                "kopia_password": "123456",
                "s3_url_style": "virtual_hosted",
                "use_tls": True,
            },
        )
        return self._mark_repository_owned(repository)

    def _successful_agent_task(self):
        return SimpleNamespace(
            task=SimpleNamespace(status="success", last_error=""),
            result={"ok": True, "ownership_verified": True},
        )

    def _run_latest_provision(self, *, config_name: str):
        from apps.protection.services.backup_config_provision import (
            run_backup_config_provision_task,
        )

        config = BackupConfig.objects.get(name=config_name)
        task = Task.objects.get(task_uuid=config.provisioning_task_uuid)
        with (
            mock.patch(
                "apps.protection.tasks.repository_policy.sync_backup_config_repository_policy_task.delay"
            ),
            mock.patch(
                "apps.protection.tasks.directory_size_estimate.refresh_backup_config_directory_estimates_task.delay"
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            result = run_backup_config_provision_task(task_id=task.id)
        config.refresh_from_db()
        task.refresh_from_db()
        return config, task, result

    def _proxy(self, *, name: str = "backup-config-proxy"):
        return Node.objects.create(
            organization=self.org,
            name=name,
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.42",
            metadata={"inventory": {"capabilities": ["repository_ownership_v1"]}},
        )

    def _proxy_fs_repository(
        self, *, proxy: Node | None = None, name: str = "proxy-fs-repo"
    ):
        proxy = proxy or self._proxy()
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name=name,
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={"proxy_node_dir": "/repo"},
        )
        return self._mark_repository_owned(repository)

    def _proxy_bound_nas_repository(
        self, *, proxy: Node | None = None, name: str = "proxy-nas-repo"
    ):
        proxy = proxy or self._proxy()
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name=name,
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={
                "server_address": "10.0.0.16",
                "share_path": "/volume1/proxy-backup",
            },
        )
        return self._mark_repository_owned(repository)

    def _nas_source(
        self,
        *,
        proxy: Node,
        name: str = "backup-config-nas-source",
        server: str = "10.0.0.15",
        share: str = "source-share",
    ):
        return SourceResource.objects.create(
            organization=self.org,
            name=name,
            resource_type=ResourceType.NAS,
            bound_node=proxy,
            availability="online",
            config={"protocol": "smb", "server": server, "share": share},
        )

    def _nas_payload(
        self, *, source: SourceResource, repository: Repository, path: str = "/data"
    ):
        payload = self._payload(name=f"NAS backup {source.id}")
        payload.update(
            {
                "source_type": "nas",
                "source_ref_id": source.id,
                "repository_id": repository.id,
                "directories": [{"path": path, "path_type": "directory"}],
            }
        )
        return payload

    @mock.patch(
        "apps.protection.api.views.backup_config."
        "refresh_backup_config_directory_estimates_task.delay"
    )
    @mock.patch(
        "apps.protection.api.views.backup_config."
        "sync_backup_config_repository_policy_task.delay"
    )
    def test_create_backup_config_queues_directory_size_precache(
        self,
        mock_policy_delay,
        mock_estimate_delay,
    ):
        with self.captureOnCommitCallbacks(execute=True):
            create = self.client.post(
                "/api/v1/protection/backup-configs/",
                self._payload(name="Precache estimates config"),
                format="json",
                **self._headers(),
            )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        mock_policy_delay.assert_called_once_with(config_id=create.data["id"])
        mock_estimate_delay.assert_called_once_with(config_id=create.data["id"])

    def test_create_backup_config_checks_quota_in_creation_transaction(self):
        repository_locked = False
        original_lock = backup_config_service._lock_repository_for_backup_config

        def lock_repository(**kwargs):
            nonlocal repository_locked
            result = original_lock(**kwargs)
            repository_locked = True
            return result

        def enforce_quota(_organization, resource_type, *, additional=1):
            self.assertTrue(repository_locked)
            quota_checks.append((resource_type, additional))

        quota_checks = []
        with (
            mock.patch.object(
                backup_config_service,
                "_lock_repository_for_backup_config",
                side_effect=lock_repository,
            ),
            mock.patch(
                "apps.subscription.services.interface.enforce_license_quota",
                side_effect=enforce_quota,
            ),
        ):
            response = self.client.post(
                "/api/v1/protection/backup-configs/",
                self._payload(name="Quota transaction config"),
                format="json",
                **self._headers(),
            )

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.content
        )
        self.assertEqual(
            quota_checks,
            [
                ("max_protected_sources", 1),
                ("max_storage_gb", 0),
            ],
        )

    def test_create_backup_config_advances_source_pipeline_to_step3(self):
        source_key = f"agent:{self.agent.id}"
        step2 = self.client.post(
            "/api/v1/source/backup-selectable/pipeline/",
            {"ids": [source_key], "step": 2},
            format="json",
            **self._headers(),
        )
        self.assertEqual(step2.status_code, status.HTTP_200_OK)

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)

        entry = SourceBackupPipelineEntry.objects.get(
            organization=self.org,
            source_kind="agent",
            ref_id=self.agent.id,
        )
        self.assertEqual(entry.step, 3)

        step3 = self.client.get(
            "/api/v1/source/backup-selectable/?step=3&page=1&page_size=10",
            **self._headers(),
        )
        self.assertEqual(step3.status_code, status.HTTP_200_OK)
        self.assertIn(source_key, {row["id"] for row in step3.data["results"]})

        step2_after = self.client.get(
            "/api/v1/source/backup-selectable/?step=2&page=1&page_size=10",
            **self._headers(),
        )
        self.assertEqual(step2_after.status_code, status.HTTP_200_OK)
        self.assertNotIn(source_key, {row["id"] for row in step2_after.data["results"]})

    def test_create_backup_config_idempotent_replay_returns_original_result(self):
        headers = {**self._headers(), "HTTP_IDEMPOTENCY_KEY": "backup-config-agent-1"}

        first = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(),
            format="json",
            **headers,
        )
        second = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(),
            format="json",
            **headers,
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.content)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED, second.content)
        self.assertEqual(first.data, second.data)
        self.assertEqual(first.headers["Idempotency-Replayed"], "false")
        self.assertEqual(second.headers["Idempotency-Replayed"], "true")
        self.assertEqual(BackupConfig.objects.count(), 1)
        request_record = BackupConfigCreateRequest.objects.get(
            organization_id=self.org.id,
            idempotency_key="backup-config-agent-1",
        )
        self.assertEqual(request_record.backup_config_id, first.data["id"])
        self.assertEqual(request_record.response_status, status.HTTP_201_CREATED)

    def test_create_backup_config_rejects_idempotency_key_payload_conflict(self):
        headers = {**self._headers(), "HTTP_IDEMPOTENCY_KEY": "backup-config-conflict"}
        first = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(),
            format="json",
            **headers,
        )
        conflicting_payload = self._payload(name="Different config")

        second = self.client.post(
            "/api/v1/protection/backup-configs/",
            conflicting_payload,
            format="json",
            **headers,
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.content)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT, second.content)
        self.assertEqual(
            second.data["error_code"],
            "BACKUP_CONFIG.IDEMPOTENCY_CONFLICT",
        )
        self.assertEqual(BackupConfig.objects.count(), 1)

    def test_failed_create_does_not_consume_idempotency_key(self):
        headers = {**self._headers(), "HTTP_IDEMPOTENCY_KEY": "backup-config-retry"}
        invalid_payload = self._payload()
        invalid_payload["repository_id"] = 999999

        failed = self.client.post(
            "/api/v1/protection/backup-configs/",
            invalid_payload,
            format="json",
            **headers,
        )
        succeeded = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(),
            format="json",
            **headers,
        )

        self.assertEqual(failed.status_code, status.HTTP_400_BAD_REQUEST, failed.content)
        self.assertEqual(succeeded.status_code, status.HTTP_201_CREATED, succeeded.content)
        self.assertEqual(
            BackupConfigCreateRequest.objects.filter(
                organization_id=self.org.id,
                idempotency_key="backup-config-retry",
            ).count(),
            1,
        )

    def test_create_without_idempotency_key_preserves_legacy_behavior(self):
        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(),
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertFalse(BackupConfigCreateRequest.objects.exists())

    def test_direct_nas_idempotent_replay_does_not_duplicate_provision_task(self):
        repository = self._direct_nas_repository(name="idempotent-direct-nas")
        payload = self._payload(name="Idempotent Direct NAS")
        payload["repository_id"] = repository.id
        headers = {**self._headers(), "HTTP_IDEMPOTENCY_KEY": "backup-config-direct-nas"}

        first = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **headers,
        )
        BackupConfig.objects.filter(id=first.data["id"]).update(
            status=BackupConfig.Status.ACTIVE
        )
        second = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **headers,
        )

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED, first.content)
        self.assertEqual(second.status_code, status.HTTP_202_ACCEPTED, second.content)
        self.assertEqual(first.data, second.data)
        self.assertEqual(second.data["status"], BackupConfig.Status.PROVISIONING)
        self.assertEqual(
            BackupConfig.objects.get(id=first.data["id"]).status,
            BackupConfig.Status.ACTIVE,
        )
        self.assertEqual(BackupConfig.objects.count(), 1)
        self.assertEqual(
            Task.objects.filter(task_type=Task.Type.BACKUP_CONFIG_PROVISION).count(),
            1,
        )

    def test_backup_config_idempotency_key_is_isolated_by_organization(self):
        other_org = Organization.objects.create(
            key="backup-config-other-org",
            name="Backup Config Other Org",
        )
        Membership.objects.create(
            user=self.user,
            organization=other_org,
            role=Membership.Role.ADMIN,
        )
        other_agent = Node.objects.create(
            organization=other_org,
            name="other-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.99",
            os_name="linux",
            metadata={"inventory": {"capabilities": ["repository_ownership_v1"]}},
        )
        other_repository = Repository.objects.create(
            organization_id=other_org.id,
            name="other-repository",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="other-backup-config-bucket",
            config={
                "endpoint": "s3.example.internal:9000",
                "region": "cn-test-1",
                "prefix": "kopia/other",
                "access_key_id": "ak-test",
                "secret_access_key": "sk-test",
                "kopia_password": "123456",
                "use_tls": False,
            },
        )
        self._mark_repository_owned(other_repository)
        key_header = {"HTTP_IDEMPOTENCY_KEY": "shared-across-organizations"}

        first = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(),
            format="json",
            **self._headers(),
            **key_header,
        )
        second = self.client.post(
            "/api/v1/protection/backup-configs/",
            {
                **self._payload(source_ref_id=other_agent.id, name="Other org config"),
                "repository_id": other_repository.id,
            },
            format="json",
            HTTP_X_ORG_KEY=other_org.key,
            **key_header,
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.content)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED, second.content)
        self.assertEqual(
            BackupConfigCreateRequest.objects.filter(
                idempotency_key="shared-across-organizations"
            ).count(),
            2,
        )

    def test_create_backup_config_rejects_invalid_idempotency_key(self):
        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(),
            format="json",
            **self._headers(),
            HTTP_IDEMPOTENCY_KEY="x" * 129,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(BackupConfig.objects.exists())
        self.assertFalse(BackupConfigCreateRequest.objects.exists())

    def test_distinct_s3_endpoints_require_explicit_selection(self):
        repository = self._distinct_endpoint_repository()
        payload = self._payload(name="Missing Endpoint selection")
        payload["repository_id"] = repository.id

        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("repository_endpoint_type", self._error_fields(response))
        self.assertFalse(
            BackupConfig.objects.filter(name="Missing Endpoint selection").exists()
        )

    def test_distinct_s3_endpoint_selection_persists_and_serializes(self):
        repository = self._distinct_endpoint_repository()
        payload = self._payload(name="Internal Endpoint config")
        payload.update(
            {
                "repository_id": repository.id,
                "repository_endpoint_type": "internal",
            }
        )

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        self.assertEqual(create.data["repository_endpoint_type"], "internal")
        config = BackupConfig.objects.get(id=create.data["id"])
        self.assertEqual(config.repository_endpoint_type, "internal")

        preserve = self.client.patch(
            f"/api/v1/protection/backup-configs/{config.id}/",
            {"remark": "keep internal"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(preserve.status_code, status.HTTP_200_OK, preserve.content)
        self.assertEqual(preserve.data["repository_endpoint_type"], "internal")

        update = self.client.patch(
            f"/api/v1/protection/backup-configs/{config.id}/",
            {"repository_endpoint_type": "external"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(update.status_code, status.HTTP_200_OK, update.content)
        self.assertEqual(update.data["repository_endpoint_type"], "external")
        config.refresh_from_db()
        self.assertEqual(config.repository_endpoint_type, "external")

    def test_equal_s3_endpoints_default_to_external(self):
        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Equal Endpoint config"),
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.content
        )
        self.assertEqual(response.data["repository_endpoint_type"], "external")

    def test_internal_selection_is_rejected_for_equal_or_non_s3_endpoints(self):
        for repository in (self.repository, self._direct_nas_repository()):
            with self.subTest(repository_type=repository.repo_type):
                payload = self._payload(name=f"Invalid internal {repository.repo_type}")
                payload.update(
                    {
                        "repository_id": repository.id,
                        "repository_endpoint_type": "internal",
                    }
                )
                response = self.client.post(
                    "/api/v1/protection/backup-configs/",
                    payload,
                    format="json",
                    **self._headers(),
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("repository_endpoint_type", self._error_fields(response))

    def test_repository_target_cannot_be_changed(self):
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Repository change Endpoint config"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        repository = self._distinct_endpoint_repository()

        response = self.client.patch(
            f"/api/v1/protection/backup-configs/{create.data['id']}/",
            {"repository_id": repository.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("repository_id", self._error_fields(response))

    def test_backup_config_cannot_change_while_backup_is_active(self):
        repository = self._distinct_endpoint_repository()
        payload = self._payload(name="Active backup Endpoint config")
        payload.update(
            {
                "repository_id": repository.id,
                "repository_endpoint_type": "internal",
            }
        )
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        config_id = create.data["id"]
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            status=Task.Status.PENDING,
            display_name="Active endpoint backup",
            request_payload={
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "backup_config_id": config_id,
            },
        )

        blocked = self.client.patch(
            f"/api/v1/protection/backup-configs/{config_id}/",
            {"repository_endpoint_type": "external"},
            format="json",
            **self._headers(),
        )

        self.assertEqual(blocked.status_code, status.HTTP_409_CONFLICT)
        problem = blocked.data["data"]
        self.assertEqual(problem["code"], "BACKUP.ALREADY_RUNNING")
        self.assertEqual(problem["meta"]["task_uuid"], str(task.task_uuid))
        self.assertEqual(problem["meta"]["task_type"], Task.Type.BACKUP)
        self.assertEqual(problem["meta"]["source_type"], "agent")
        self.assertEqual(problem["meta"]["source_ref_id"], self.agent.id)
        task.status = Task.Status.SUCCESS
        task.save(update_fields=["status", "updated_at"])

        allowed = self.client.patch(
            f"/api/v1/protection/backup-configs/{config_id}/",
            {"repository_endpoint_type": "external"},
            format="json",
            **self._headers(),
        )

        self.assertEqual(allowed.status_code, status.HTTP_200_OK, allowed.content)
        self.assertEqual(allowed.data["repository_endpoint_type"], "external")

    def test_backup_config_compression_defaults_and_updates_strictly(self):
        payload = self._payload(name="Default compression config")
        payload.pop("compression_level")
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        self.assertEqual(create.data["compression_level"], "balanced")

        update = self.client.patch(
            f"/api/v1/protection/backup-configs/{create.data['id']}/",
            {"compression_level": "high"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(update.status_code, status.HTTP_200_OK, update.content)
        self.assertEqual(update.data["compression_level"], "high")

        preserve = self.client.patch(
            f"/api/v1/protection/backup-configs/{create.data['id']}/",
            {"remark": "unchanged compression"},
            format="json",
            **self._headers(),
        )
        self.assertEqual(preserve.status_code, status.HTTP_200_OK, preserve.content)
        self.assertEqual(preserve.data["compression_level"], "high")

    def test_backup_config_rejects_old_empty_and_unknown_compression_values(self):
        for value in (None, "", "fast", "best", "unknown"):
            with self.subTest(value=value):
                payload = self._payload(name=f"Invalid compression {value}")
                payload["compression_level"] = value
                response = self.client.post(
                    "/api/v1/protection/backup-configs/",
                    payload,
                    format="json",
                    **self._headers(),
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("compression_level", self._error_fields(response))

    def test_create_backup_config_rejects_duplicate_source(self):
        first = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Primary source config"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.content)

        duplicate = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Duplicate source config"),
            format="json",
            **self._headers(),
        )

        self.assertEqual(duplicate.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("source_ref_id", self._error_fields(duplicate))

    def test_create_backup_config_rejects_relative_directory_path(self):
        payload = self._payload(name="Relative directory config")
        payload["directories"] = [{"path": "data"}]

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._error_fields(create), {"directories"})
        self.assertFalse(
            BackupConfig.objects.filter(name="Relative directory config").exists()
        )

    def test_update_backup_config_rejects_duplicate_source(self):
        agent_two = Node.objects.create(
            organization=self.org,
            name="agent-backup-config-2",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.43",
        )
        first = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Primary source config"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.content)
        second = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(source_ref_id=agent_two.id, name="Secondary source config"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(second.status_code, status.HTTP_201_CREATED, second.content)

        update = self.client.patch(
            f"/api/v1/protection/backup-configs/{second.data['id']}/",
            {"source_ref_id": self.agent.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(update.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("source_ref_id", self._error_fields(update))

    @mock.patch(
        "apps.storage.services.internal.repository_usage.enqueue_repository_usage_refresh"
    )
    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_create_backup_config_initializes_direct_nas_agent_subdir(
        self,
        run_agent_task_sync,
        enqueue_usage,
    ):
        run_agent_task_sync.return_value = self._successful_agent_task()
        nas_repo = self._direct_nas_repository()
        payload = self._payload(name="Direct NAS config")
        payload["repository_id"] = nas_repo.id

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_202_ACCEPTED, create.content)
        self.assertEqual(create.data["status"], BackupConfig.Status.PROVISIONING)
        config = BackupConfig.objects.get(id=create.data["id"])
        provision_task = Task.objects.get(task_uuid=config.provisioning_task_uuid)
        self.assertEqual(provision_task.request_payload["repository_id"], nas_repo.id)
        self.assertTrue(
            provision_task.resources.filter(
                resource_type=TaskResource.Type.REPOSITORY,
                resource_id=nas_repo.id,
            ).exists()
        )
        self._run_latest_provision(config_name="Direct NAS config")
        run_agent_task_sync.assert_called_once()
        call = run_agent_task_sync.call_args.kwargs
        self.assertEqual(call["node_id"], self.agent.id)
        self.assertEqual(call["kind"], "repo.initialize")
        repository_payload = call["payload"]["repository"]
        self.assertEqual(repository_payload["type"], Repository.Type.NAS)
        self.assertEqual(
            repository_payload["subdir"], f"hp-repos/agent-{self.agent.id}"
        )
        self.assertEqual(repository_payload["kopia_password"], "repo-pass")
        self.assertEqual(repository_payload["nas"]["export_path"], "/volume1/backup")
        nas_repo.refresh_from_db()
        self.assertEqual(nas_repo.health, Repository.Health.ONLINE)
        self.assertIsNotNone(nas_repo.last_checked_at)
        shard = RepositoryUsageShard.objects.get(
            repository_id=nas_repo.id,
            node_id=self.agent.id,
        )
        self.assertIsNotNone(shard.last_success_checked_at)
        claim = RepositoryLocationClaim.objects.get(
            repository=nas_repo,
            owner_node_id=self.agent.id,
        )
        self.assertEqual(claim.state, RepositoryLocationClaim.State.OWNED)
        self.assertEqual(claim.root_path, f"hp-repos/agent-{self.agent.id}")
        enqueue_usage.assert_called_once_with(
            organization_id=self.org.id,
            repository_ids=[nas_repo.id],
            force=True,
            trigger="protection.backup_config.provision",
        )

    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_direct_nas_activation_survives_followup_dispatch_failure(
        self,
        run_agent_task_sync,
    ):
        from apps.protection.services.backup_config_provision import (
            run_backup_config_provision_task,
        )

        run_agent_task_sync.return_value = self._successful_agent_task()
        nas_repo = self._direct_nas_repository(name="followup-failure-direct-nas")
        payload = self._payload(name="Followup failure Direct NAS config")
        payload["repository_id"] = nas_repo.id
        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.content)
        config = BackupConfig.objects.get(id=response.data["id"])
        task = Task.objects.get(task_uuid=config.provisioning_task_uuid)

        with (
            mock.patch(
                "apps.protection.tasks.repository_policy.sync_backup_config_repository_policy_task.delay",
                side_effect=RuntimeError("broker unavailable"),
            ),
            mock.patch(
                "apps.protection.tasks.directory_size_estimate.refresh_backup_config_directory_estimates_task.delay"
            ) as estimate_delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            result = run_backup_config_provision_task(task_id=task.id)

        config.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(result["status"], "success")
        self.assertEqual(config.status, BackupConfig.Status.ACTIVE)
        self.assertEqual(task.status, Task.Status.SUCCESS)
        estimate_delay.assert_called_once_with(config_id=config.id)
        claim = RepositoryLocationClaim.objects.get(
            repository=nas_repo,
            owner_node_id=self.agent.id,
        )
        self.assertEqual(claim.state, RepositoryLocationClaim.State.OWNED)
        self.assertIsNotNone(claim.ownership_verified_at)

    def test_successful_node_result_projection_is_leased_while_parent_waits(self):
        from apps.protection.services.backup_config_provision import (
            _claim_task_execution,
        )
        from apps.task.services.interface import start_task

        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP_CONFIG_PROVISION,
            display_name="Lease storage validation result",
            trigger_type=Task.TriggerType.SYSTEM,
            status=Task.Status.PENDING,
        )
        task = start_task(
            task_uuid=task.task_uuid,
            organization_id=self.org.id,
        )
        NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=self.agent,
            parent_task=task,
            kind="repo.status",
            status=NodeTask.Status.SUCCESS,
            watchdog_deadline_at=timezone.now(),
            result={"ownership_verified": True},
        )
        Task.objects.filter(id=task.id).update(status=Task.Status.WAITING)

        _task, first_state, _node_task = _claim_task_execution(task_id=task.id)
        _task, second_state, _node_task = _claim_task_execution(task_id=task.id)

        self.assertEqual(first_state, "node_success")
        self.assertEqual(second_state, "dispatch_in_progress")

    @mock.patch(
        "apps.protection.services.backup_config_provision.queue_backup_config_provision_task",
        return_value=True,
    )
    def test_provision_reconciler_excludes_tasks_with_active_agent_work(
        self,
        queue_task,
    ):
        from apps.protection.services.backup_config_provision import (
            reconcile_backup_config_provision_tasks,
        )

        stale_at = timezone.now() - timedelta(minutes=5)
        active_parent = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP_CONFIG_PROVISION,
            display_name="Active Agent validation",
            trigger_type=Task.TriggerType.SYSTEM,
            status=Task.Status.WAITING,
            started_at=stale_at,
        )
        dispatchable = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP_CONFIG_PROVISION,
            display_name="Dispatchable validation",
            trigger_type=Task.TriggerType.SYSTEM,
            status=Task.Status.WAITING,
            started_at=stale_at,
        )
        Task.objects.filter(id__in=[active_parent.id, dispatchable.id]).update(
            updated_at=stale_at
        )
        NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=self.agent,
            parent_task=active_parent,
            kind="repo.status",
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now() + timedelta(minutes=5),
        )

        result = reconcile_backup_config_provision_tasks(limit=10, stale_seconds=30)

        self.assertEqual(result["active_agent_tasks"], 1)
        self.assertEqual(result["dispatch_attempted"], 1)
        queue_task.assert_called_once_with(task_id=dispatchable.id)

    def test_upgrade_recovery_candidates_exclude_unupgraded_agents(self):
        from apps.protection.services.backup_config_provision import (
            _upgrade_recovery_candidates,
        )

        capable = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Capable recovery candidate"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(capable.status_code, status.HTTP_201_CREATED, capable.content)
        capable_config = BackupConfig.objects.get(id=capable.data["id"])
        BackupConfig.objects.filter(id=capable_config.id).update(
            status=BackupConfig.Status.PROVISION_FAILED,
            provisioning_error_code="AGENT_UPGRADE_REQUIRED",
        )

        old_agent = Node.objects.create(
            organization=self.org,
            name="agent-without-repository-ownership",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.43",
            metadata={"inventory": {"capabilities": []}},
        )
        old = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(
                source_ref_id=old_agent.id,
                name="Unupgraded recovery candidate",
            ),
            format="json",
            **self._headers(),
        )
        self.assertEqual(old.status_code, status.HTTP_201_CREATED, old.content)
        old_config = BackupConfig.objects.get(id=old.data["id"])
        BackupConfig.objects.filter(id=old_config.id).update(
            status=BackupConfig.Status.PROVISION_FAILED,
            provisioning_error_code="AGENT_UPGRADE_REQUIRED",
        )

        candidates = _upgrade_recovery_candidates(limit=10)

        self.assertEqual([config.id for config in candidates], [capable_config.id])

    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_direct_nas_provision_requires_agent_ownership_capability(
        self,
        run_agent_task_sync,
    ):
        self.agent.metadata = {"inventory": {"capabilities": []}}
        self.agent.save(update_fields=["metadata", "updated_at"])
        nas_repo = self._direct_nas_repository(name="upgrade-required-direct-nas")
        payload = self._payload(name="Upgrade required Direct NAS config")
        payload["repository_id"] = nas_repo.id

        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.content)
        config, task, result = self._run_latest_provision(
            config_name="Upgrade required Direct NAS config"
        )
        self.assertEqual(result["error_code"], "AGENT_UPGRADE_REQUIRED")
        self.assertEqual(config.status, BackupConfig.Status.PROVISION_FAILED)
        self.assertEqual(config.provisioning_error_code, "AGENT_UPGRADE_REQUIRED")
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertEqual(task.current_step, "initialize_repository")
        self.assertEqual(
            task.steps.get(step_name="initialize_repository").status,
            "failed",
        )
        self.assertEqual(
            task.steps.get(step_name="activate_backup_config").status,
            "skipped",
        )
        run_agent_task_sync.assert_not_called()
        self.assertFalse(nas_repo.location_claims.exists())

        discarded = self.client.delete(
            f"/api/v1/protection/backup-configs/{config.id}/",
            **self._headers(),
        )
        self.assertEqual(discarded.status_code, status.HTTP_200_OK, discarded.content)
        self.assertFalse(BackupConfig.objects.filter(id=config.id).exists())
        task.refresh_from_db()
        self.assertTrue(task.result_payload["backup_config_discarded"])
        pipeline = SourceBackupPipelineEntry.objects.get(
            organization=self.org,
            source_kind="agent",
            ref_id=self.agent.id,
        )
        self.assertEqual(pipeline.step, 2)

    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_direct_nas_retry_ignores_successful_node_task_from_previous_attempt(
        self,
        run_agent_task_sync,
    ):
        self.agent.metadata = {"inventory": {"capabilities": []}}
        self.agent.save(update_fields=["metadata", "updated_at"])
        nas_repo = self._direct_nas_repository(name="retry-attempt-direct-nas")
        payload = self._payload(name="Retry attempt Direct NAS config")
        payload["repository_id"] = nas_repo.id
        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.content)
        config, task, _result = self._run_latest_provision(
            config_name="Retry attempt Direct NAS config"
        )
        NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=self.agent,
            parent_task=task,
            kind="repo.initialize",
            status=NodeTask.Status.SUCCESS,
            watchdog_deadline_at=timezone.now(),
            result={"ownership_verified": True},
        )

        self.agent.metadata = {
            "inventory": {"capabilities": ["repository_ownership_v1"]}
        }
        self.agent.save(update_fields=["metadata", "updated_at"])
        retry = self.client.post(
            f"/api/v1/protection/backup-configs/{config.id}/retry-provision/",
            {},
            format="json",
            **self._headers(),
        )
        self.assertEqual(retry.status_code, status.HTTP_202_ACCEPTED, retry.content)

        run_agent_task_sync.return_value = self._successful_agent_task()
        config, task, result = self._run_latest_provision(
            config_name="Retry attempt Direct NAS config"
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(config.status, BackupConfig.Status.ACTIVE)
        self.assertEqual(task.status, Task.Status.SUCCESS)
        run_agent_task_sync.assert_called_once()

    @mock.patch(
        "apps.protection.services.backup_config._advance_pipeline",
        side_effect=ValidationError("pipeline unavailable"),
    )
    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_direct_nas_persistence_failure_does_not_touch_remote_storage(
        self,
        run_agent_task_sync,
        _advance_pipeline,
    ):
        run_agent_task_sync.return_value = self._successful_agent_task()
        nas_repo = self._direct_nas_repository(name="persistence-failed-direct-nas")
        payload = self._payload(name="Persistence failed Direct NAS config")
        payload["repository_id"] = nas_repo.id

        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            BackupConfig.objects.filter(
                name="Persistence failed Direct NAS config"
            ).exists()
        )
        self.assertFalse(RepositoryLocationClaim.objects.filter(repository=nas_repo).exists())
        run_agent_task_sync.assert_not_called()

    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_direct_nas_initialize_retains_residual_if_repository_is_removing(
        self,
        run_agent_task_sync,
    ):
        nas_repo = self._direct_nas_repository(name="removing-direct-nas")

        def complete_after_removal(**_kwargs):
            Repository.objects.filter(pk=nas_repo.id).update(
                status=Repository.Status.REMOVING
            )
            return self._successful_agent_task()

        run_agent_task_sync.side_effect = complete_after_removal
        payload = self._payload(name="Removing Direct NAS config")
        payload["repository_id"] = nas_repo.id

        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        config, task, result = self._run_latest_provision(
            config_name="Removing Direct NAS config"
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(config.status, BackupConfig.Status.PROVISION_FAILED)
        self.assertEqual(task.status, Task.Status.FAILED)
        claim = RepositoryLocationClaim.objects.get(
            repository=nas_repo,
            owner_node_id=self.agent.id,
        )
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)
        self.assertFalse(
            RepositoryUsageShard.objects.filter(
                repository_id=nas_repo.id,
                node_id=self.agent.id,
                is_active=True,
            ).exists()
        )
        nas_repo.refresh_from_db()
        self.assertEqual(nas_repo.status, Repository.Status.REMOVING)
        self.assertEqual(nas_repo.health, Repository.Health.UNVERIFIED)

    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_create_backup_config_revalidates_repository_before_persistence(
        self,
        run_agent_task_sync,
    ):
        run_agent_task_sync.return_value = self._successful_agent_task()
        nas_repo = self._direct_nas_repository(name="late-removing-direct-nas")
        original_lock = backup_config_service._lock_repository_for_backup_config

        def remove_before_lock(**kwargs):
            Repository.objects.filter(pk=nas_repo.id).update(
                status=Repository.Status.REMOVING
            )
            return original_lock(**kwargs)

        payload = self._payload(name="Late removing Direct NAS config")
        payload["repository_id"] = nas_repo.id
        with mock.patch.object(
            backup_config_service,
            "_lock_repository_for_backup_config",
            side_effect=remove_before_lock,
        ):
            response = self.client.post(
                "/api/v1/protection/backup-configs/",
                payload,
                format="json",
                **self._headers(),
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("repository_id", self._error_fields(response))
        self.assertFalse(
            BackupConfig.objects.filter(name="Late removing Direct NAS config").exists()
        )

    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    @mock.patch(
        "apps.subscription.services.interface.enforce_license_quota",
        side_effect=ValidationError("license quota exceeded"),
    )
    def test_license_rejection_does_not_initialize_direct_nas_repository(
        self,
        _enforce_quota,
        run_agent_task_sync,
    ):
        nas_repo = self._direct_nas_repository(name="quota-rejected-direct-nas")
        payload = self._payload(name="Quota rejected config")
        payload["repository_id"] = nas_repo.id

        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        run_agent_task_sync.assert_not_called()
        self.assertFalse(nas_repo.location_claims.exists())
        self.assertFalse(
            RepositoryUsageShard.objects.filter(repository_id=nas_repo.id).exists()
        )

    @mock.patch(
        "apps.storage.services.internal.repository_usage.enqueue_repository_usage_refresh"
    )
    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_create_backup_config_initializes_direct_nas_source_on_bound_proxy(
        self,
        run_agent_task_sync,
        enqueue_usage,
    ):
        run_agent_task_sync.return_value = self._successful_agent_task()
        proxy = self._proxy(name="direct-nas-source-proxy")
        source = self._nas_source(proxy=proxy, name="direct-nas-source")
        nas_repo = self._direct_nas_repository(name="direct-nas-source-repo")

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._nas_payload(source=source, repository=nas_repo),
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_202_ACCEPTED, create.content)
        self._run_latest_provision(config_name=create.data["name"])
        call = run_agent_task_sync.call_args.kwargs
        self.assertEqual(call["node_id"], proxy.id)
        self.assertEqual(call["kind"], "repo.initialize")
        self.assertEqual(
            call["payload"]["repository"]["subdir"], f"hp-repos/agent-{proxy.id}"
        )
        enqueue_usage.assert_called_once()

    @mock.patch(
        "apps.storage.services.internal.repository_usage.enqueue_repository_usage_refresh"
    )
    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_issue_637_late_proxy_result_activates_direct_nas_once(
        self,
        run_agent_task_sync,
        enqueue_usage,
    ):
        from apps.protection.services.backup_config_provision import (
            run_backup_config_provision_task,
        )

        proxy = self._proxy(name="issue-637-late-result-proxy")
        source = self._nas_source(proxy=proxy, name="issue-637-late-result-source")
        repository = self._direct_nas_repository(name="issue-637-late-result-repo")
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._nas_payload(source=source, repository=repository),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_202_ACCEPTED, create.content)
        config = BackupConfig.objects.get(id=create.data["id"])
        task = Task.objects.get(task_uuid=config.provisioning_task_uuid)

        def timeout_after_dispatch(**kwargs):
            NodeTask.objects.create(
                organization=self.org,
                requesting_organization_id=self.org.id,
                node=proxy,
                parent_task=kwargs["parent_task"],
                kind=kwargs["kind"],
                correlation_type=kwargs["correlation_type"],
                correlation_id=kwargs["correlation_id"],
                status=NodeTask.Status.RUNNING,
                watchdog_deadline_at=timezone.now() + timedelta(minutes=5),
                payload=kwargs["payload"],
            )
            raise TimeoutError("Controller wait expired")

        run_agent_task_sync.side_effect = timeout_after_dispatch
        first = run_backup_config_provision_task(task_id=task.id)

        task.refresh_from_db()
        config.refresh_from_db()
        self.assertEqual(first["status"], "waiting")
        self.assertEqual(task.status, Task.Status.WAITING)
        self.assertEqual(config.status, BackupConfig.Status.PROVISIONING)
        claim = RepositoryLocationClaim.objects.get(
            repository=repository,
            owner_node_id=proxy.id,
        )
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)

        node_task = NodeTask.objects.get(parent_task=task)
        node_task.status = NodeTask.Status.SUCCESS
        node_task.result = {
            "ownership_verified": True,
            "mount_point": "/mnt/hfl/issue-637-late-result",
        }
        node_task.save(update_fields=["status", "result", "updated_at"])
        with (
            mock.patch(
                "apps.protection.tasks.repository_policy.sync_backup_config_repository_policy_task.delay"
            ) as policy_delay,
            mock.patch(
                "apps.protection.tasks.directory_size_estimate.refresh_backup_config_directory_estimates_task.delay"
            ) as estimate_delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            second = run_backup_config_provision_task(task_id=task.id)
            third = run_backup_config_provision_task(task_id=task.id)

        config.refresh_from_db()
        task.refresh_from_db()
        claim.refresh_from_db()
        self.assertEqual(second["status"], "success")
        self.assertEqual(third["status"], "success")
        self.assertEqual(config.status, BackupConfig.Status.ACTIVE)
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertEqual(claim.state, RepositoryLocationClaim.State.OWNED)
        self.assertIsNotNone(claim.ownership_verified_at)
        self.assertEqual(
            RepositoryUsageShard.objects.filter(
                repository_id=repository.id,
                node_id=proxy.id,
                is_active=True,
            ).count(),
            1,
        )
        policy_delay.assert_called_once_with(config_id=config.id)
        estimate_delay.assert_called_once_with(config_id=config.id)
        enqueue_usage.assert_called_once()

    @mock.patch(
        "apps.storage.services.internal.repository_usage.enqueue_repository_usage_refresh"
    )
    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_create_backup_config_rejects_existing_direct_nas_repository(
        self,
        run_agent_task_sync,
        _enqueue_usage,
    ):
        run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(
                status="failed",
                last_error="repository already exists",
            ),
            result={"error_code": REPOSITORY_ALREADY_EXISTS_CODE},
        )
        nas_repo = self._direct_nas_repository()
        payload = self._payload(name="Direct NAS conflict")
        payload["repository_id"] = nas_repo.id

        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.content)
        config, task, result = self._run_latest_provision(
            config_name="Direct NAS conflict"
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(config.status, BackupConfig.Status.PROVISION_FAILED)
        self.assertEqual(task.error_code, REPOSITORY_ALREADY_EXISTS_CODE)
        self.assertFalse(
            RepositoryUsageShard.objects.filter(repository_id=nas_repo.id).exists()
        )
        claim = RepositoryLocationClaim.objects.get(repository=nas_repo)
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)
        self.assertEqual(
            [call.kwargs["kind"] for call in run_agent_task_sync.call_args_list],
            ["repo.initialize"],
        )

    @mock.patch(
        "apps.storage.services.internal.repository_usage.enqueue_repository_usage_refresh"
    )
    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_create_backup_config_adopts_its_interrupted_direct_nas_initialize(
        self,
        run_agent_task_sync,
        _enqueue_usage,
    ):
        run_agent_task_sync.side_effect = [
            SimpleNamespace(
                task=SimpleNamespace(
                    status="failed",
                    last_error="repository already exists",
                ),
                result={"error_code": REPOSITORY_ALREADY_EXISTS_CODE},
            ),
            self._successful_agent_task(),
        ]
        nas_repo = self._direct_nas_repository()
        repository_subdir = f"hp-repos/agent-{self.agent.id}"
        reserve_direct_nas_location(
            repository=nas_repo,
            node_id=self.agent.id,
            repository_subdir=repository_subdir,
        )
        mark_repository_location_residual(
            nas_repo,
            owner_node_id=self.agent.id,
            repository_subdir=repository_subdir,
        )
        payload = self._payload(name="Direct NAS interrupted init")
        payload["repository_id"] = nas_repo.id

        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.content)
        self._run_latest_provision(config_name="Direct NAS interrupted init")
        claim = RepositoryLocationClaim.objects.get(repository=nas_repo)
        self.assertEqual(claim.state, RepositoryLocationClaim.State.OWNED)
        self.assertTrue(
            RepositoryUsageShard.objects.filter(
                repository_id=nas_repo.id,
                node_id=self.agent.id,
            ).exists()
        )
        self.assertEqual(
            [call.kwargs["kind"] for call in run_agent_task_sync.call_args_list],
            ["repo.initialize", "repo.status"],
        )

    @mock.patch(
        "apps.storage.services.internal.repository_usage.enqueue_repository_usage_refresh"
    )
    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_create_backup_config_connects_previously_managed_direct_nas_repository(
        self,
        run_agent_task_sync,
        _enqueue_usage,
    ):
        run_agent_task_sync.return_value = self._successful_agent_task()
        nas_repo = self._direct_nas_repository()
        RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=nas_repo.id,
            node_id=self.agent.id,
            repository_subdir=f"hp-repos/agent-{self.agent.id}",
            status=RepositoryUsageShard.Status.SUCCESS,
            last_success_checked_at=timezone.now(),
        )
        payload = self._payload(name="Managed Direct NAS")
        payload["repository_id"] = nas_repo.id

        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.content)
        self._run_latest_provision(config_name="Managed Direct NAS")
        self.assertEqual(run_agent_task_sync.call_args.kwargs["kind"], "repo.status")

    @mock.patch(
        "apps.storage.services.internal.repository_usage.enqueue_repository_usage_refresh"
    )
    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_create_backup_config_reinitializes_cleaned_direct_nas_target(
        self,
        run_agent_task_sync,
        _enqueue_usage,
    ):
        run_agent_task_sync.return_value = self._successful_agent_task()
        nas_repo = self._direct_nas_repository()
        RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=nas_repo.id,
            node_id=self.agent.id,
            repository_subdir=f"hp-repos/agent-{self.agent.id}",
            status=RepositoryUsageShard.Status.SUCCESS,
            is_active=False,
            last_success_checked_at=timezone.now(),
        )
        payload = self._payload(name="Reused Direct NAS")
        payload["repository_id"] = nas_repo.id

        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.content)
        self._run_latest_provision(config_name="Reused Direct NAS")
        self.assertEqual(
            run_agent_task_sync.call_args.kwargs["kind"], "repo.initialize"
        )
        claim = RepositoryLocationClaim.objects.get(
            repository=nas_repo,
            owner_node_id=self.agent.id,
        )
        self.assertEqual(claim.state, RepositoryLocationClaim.State.OWNED)

    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_backup_revalidates_residual_direct_nas_claim(
        self,
        run_agent_task_sync,
    ):
        nas_repo = self._direct_nas_repository(name="residual-direct-nas")
        repository_subdir = f"hp-repos/agent-{self.agent.id}"
        RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=nas_repo.id,
            node_id=self.agent.id,
            repository_subdir=repository_subdir,
            status=RepositoryUsageShard.Status.SUCCESS,
            last_success_checked_at=timezone.now(),
        )
        claim = backup_config_service.reserve_direct_nas_location(
            repository=nas_repo,
            node_id=self.agent.id,
            repository_subdir=repository_subdir,
        )
        claim.state = RepositoryLocationClaim.State.RESIDUAL
        claim.save(update_fields=["state", "updated_at"])

        def verify_while_initializing(**_kwargs):
            claim.refresh_from_db()
            self.assertEqual(
                claim.state,
                RepositoryLocationClaim.State.INITIALIZING,
            )
            return self._successful_agent_task()

        run_agent_task_sync.side_effect = verify_while_initializing

        backup_config_service.ensure_direct_nas_repository_for_backup(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            repository_id=nas_repo.id,
        )

        self.assertEqual(run_agent_task_sync.call_args.kwargs["kind"], "repo.status")
        self.assertFalse(
            run_agent_task_sync.call_args.kwargs["payload"]["allow_ownership_adoption"]
        )
        claim = (
            RepositoryLocationClaim.objects.filter(
                repository=nas_repo,
                owner_node_id=self.agent.id,
            )
            .order_by("-id")
            .first()
        )
        self.assertIsNotNone(claim)
        self.assertEqual(claim.state, RepositoryLocationClaim.State.OWNED)

    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_backup_does_not_adopt_unmarked_owned_direct_nas_claim(
        self,
        run_agent_task_sync,
    ):
        run_agent_task_sync.return_value = self._successful_agent_task()
        nas_repo = self._direct_nas_repository(name="unmarked-owned-direct-nas")
        repository_subdir = f"hp-repos/agent-{self.agent.id}"
        RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=nas_repo.id,
            node_id=self.agent.id,
            repository_subdir=repository_subdir,
            status=RepositoryUsageShard.Status.SUCCESS,
            last_success_checked_at=timezone.now(),
        )
        claim = reserve_direct_nas_location(
            repository=nas_repo,
            node_id=self.agent.id,
            repository_subdir=repository_subdir,
        )
        claim.state = RepositoryLocationClaim.State.OWNED
        claim.legacy_adoption_required = False
        claim.save(update_fields=["state", "legacy_adoption_required", "updated_at"])

        backup_config_service.ensure_direct_nas_repository_for_backup(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            repository_id=nas_repo.id,
        )

        self.assertEqual(run_agent_task_sync.call_args.kwargs["kind"], "repo.status")
        self.assertFalse(
            run_agent_task_sync.call_args.kwargs["payload"]["allow_ownership_adoption"]
        )

    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_backup_reuses_owned_direct_nas_claim_without_remote_probe(
        self,
        run_agent_task_sync,
    ):
        nas_repo = self._direct_nas_repository(name="owned-direct-nas")
        repository_subdir = f"hp-repos/agent-{self.agent.id}"
        RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=nas_repo.id,
            node_id=self.agent.id,
            repository_subdir=repository_subdir,
            status=RepositoryUsageShard.Status.SUCCESS,
            last_success_checked_at=timezone.now(),
        )
        backup_config_service.reserve_direct_nas_location(
            repository=nas_repo,
            node_id=self.agent.id,
            repository_subdir=repository_subdir,
        )
        mark_repository_location_ownership_verified(
            nas_repo,
            owner_node_id=self.agent.id,
            repository_subdir=repository_subdir,
        )

        backup_config_service.ensure_direct_nas_repository_for_backup(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            repository_id=nas_repo.id,
        )

        run_agent_task_sync.assert_not_called()

    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_backup_rejects_direct_nas_without_ownership_proof(
        self,
        run_agent_task_sync,
    ):
        run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(status="success", last_error=""),
            result={"ok": True},
        )
        nas_repo = self._direct_nas_repository(name="unverified-direct-nas")

        with self.assertRaises(AppError):
            backup_config_service.ensure_direct_nas_repository_for_backup(
                organization_id=self.org.id,
                source_type="agent",
                source_ref_id=self.agent.id,
                repository_id=nas_repo.id,
            )

        claim = RepositoryLocationClaim.objects.get(
            repository=nas_repo,
            owner_node_id=self.agent.id,
        )
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)
        self.assertIsNone(claim.ownership_verified_at)
        self.assertFalse(
            RepositoryUsageShard.objects.filter(
                repository_id=nas_repo.id,
                node_id=self.agent.id,
                is_active=True,
            ).exists()
        )

    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_backup_allows_old_agent_to_probe_migrated_direct_nas(
        self,
        run_agent_task_sync,
    ):
        self.agent.metadata = {"inventory": {"capabilities": []}}
        self.agent.save(update_fields=["metadata", "updated_at"])
        run_agent_task_sync.return_value = SimpleNamespace(
            task=SimpleNamespace(status="success", last_error=""),
            result={"ok": True, "mount_point": "/mnt/hfl/repository"},
        )
        nas_repo = self._direct_nas_repository(name="legacy-direct-nas")
        repository_subdir = f"hp-repos/agent-{self.agent.id}"
        RepositoryUsageShard.objects.create(
            organization_id=self.org.id,
            repository_id=nas_repo.id,
            node_id=self.agent.id,
            repository_subdir=repository_subdir,
            status=RepositoryUsageShard.Status.SUCCESS,
            last_success_checked_at=timezone.now(),
        )
        claim = reserve_direct_nas_location(
            repository=nas_repo,
            node_id=self.agent.id,
            repository_subdir=repository_subdir,
        )
        claim.state = RepositoryLocationClaim.State.OWNED
        claim.legacy_adoption_required = True
        claim.save(
            update_fields=[
                "state",
                "legacy_adoption_required",
                "updated_at",
            ]
        )

        backup_config_service.ensure_direct_nas_repository_for_backup(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            repository_id=nas_repo.id,
        )

        self.assertEqual(run_agent_task_sync.call_args.kwargs["kind"], "repo.status")
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.OWNED)
        self.assertIsNone(claim.ownership_verified_at)

    def test_create_backup_config_accepts_agent_source_with_proxy_fs_repository(self):
        proxy_repo = self._proxy_fs_repository()
        payload = self._payload(name="Agent to proxy fs")
        payload["repository_id"] = proxy_repo.id

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        self.assertTrue(BackupConfig.objects.filter(name="Agent to proxy fs").exists())

    def test_create_backup_config_accepts_agent_source_with_proxy_bound_nas_repository(
        self,
    ):
        proxy_repo = self._proxy_bound_nas_repository()
        payload = self._payload(name="Agent to proxy nas")
        payload["repository_id"] = proxy_repo.id

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        self.assertTrue(BackupConfig.objects.filter(name="Agent to proxy nas").exists())

    def test_create_backup_config_accepts_nas_source_with_cross_proxy_repository_server(
        self,
    ):
        source_proxy = self._proxy(name="source-proxy")
        repository_proxy = self._proxy(name="repository-proxy")
        source = self._nas_source(proxy=source_proxy)
        repository = self._proxy_bound_nas_repository(proxy=repository_proxy)
        repository.config = {
            **repository.config,
            "proxy_repository_server_host": "repo-proxy.example.internal",
        }
        repository.save(update_fields=["config", "updated_at"])

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._nas_payload(source=source, repository=repository),
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)

    def test_create_backup_config_accepts_same_proxy_nas_repository_without_server_host(
        self,
    ):
        proxy = self._proxy(name="same-proxy-no-host")
        source = self._nas_source(proxy=proxy, name="same-proxy-nas-source")
        repository = self._proxy_bound_nas_repository(
            proxy=proxy, name="same-proxy-repository"
        )

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._nas_payload(source=source, repository=repository),
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)

    def test_create_backup_config_rejects_cross_proxy_repository_without_reachable_host(
        self,
    ):
        source_proxy = self._proxy(name="source-proxy-no-host")
        repository_proxy = self._proxy(name="repository-proxy-no-host")
        repository_proxy.ip_address = ""
        repository_proxy.save(update_fields=["ip_address", "updated_at"])
        source = self._nas_source(proxy=source_proxy, name="nas-source-no-host")
        repository = self._proxy_bound_nas_repository(
            proxy=repository_proxy, name="repo-no-host"
        )

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._nas_payload(source=source, repository=repository),
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            create.status_code, status.HTTP_400_BAD_REQUEST, create.content
        )
        self.assertIn("repository_id", self._error_fields(create))

    @mock.patch(
        "apps.protection.services.repository_compatibility.protection_conf.PROTECTION_PROXY_REPOSITORY_SERVER_ENABLED",
        False,
    )
    def test_create_backup_config_rejects_cross_proxy_repository_when_server_mode_disabled(
        self,
    ):
        source_proxy = self._proxy(name="source-proxy-feature-disabled")
        repository_proxy = self._proxy(name="repository-proxy-feature-disabled")
        source = self._nas_source(
            proxy=source_proxy, name="nas-source-feature-disabled"
        )
        repository = self._proxy_bound_nas_repository(
            proxy=repository_proxy, name="repo-feature-disabled"
        )
        repository.config = {
            **repository.config,
            "proxy_repository_server_host": "repo.example.internal",
        }
        repository.save(update_fields=["config", "updated_at"])

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._nas_payload(source=source, repository=repository),
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            create.status_code, status.HTTP_400_BAD_REQUEST, create.content
        )
        self.assertIn("repository_id", self._error_fields(create))

    def test_create_backup_config_rejects_cross_proxy_repository_when_proxy_offline(
        self,
    ):
        source_proxy = self._proxy(name="source-proxy-repo-offline")
        repository_proxy = self._proxy(name="repository-proxy-offline")
        repository_proxy.availability = Node.Availability.OFFLINE
        repository_proxy.save(update_fields=["availability", "updated_at"])
        source = self._nas_source(proxy=source_proxy, name="nas-source-repo-offline")
        repository = self._proxy_bound_nas_repository(
            proxy=repository_proxy, name="repo-offline"
        )
        repository.config = {
            **repository.config,
            "proxy_repository_server_host": "repo.example.internal",
        }
        repository.save(update_fields=["config", "updated_at"])

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._nas_payload(source=source, repository=repository),
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            create.status_code, status.HTTP_400_BAD_REQUEST, create.content
        )
        self.assertIn("repository_id", self._error_fields(create))

    def test_create_backup_config_accepts_cross_proxy_proxy_fs_repository(self):
        source_proxy = self._proxy(name="source-proxy-proxy-fs")
        repository_proxy = self._proxy(name="repository-proxy-proxy-fs")
        source = self._nas_source(proxy=source_proxy, name="nas-source-proxy-fs")
        repository = self._proxy_fs_repository(
            proxy=repository_proxy, name="cross-proxy-fs"
        )
        repository.config = {
            **repository.config,
            "proxy_repository_server_host": "repo-fs.example.internal",
        }
        repository.save(update_fields=["config", "updated_at"])

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._nas_payload(source=source, repository=repository),
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)

    def test_create_backup_config_rejects_target_repository_source_path(self):
        proxy = self._proxy(name="self-backup-proxy")
        source = self._nas_source(
            proxy=proxy,
            name="self-backup-source",
            server="192.168.8.82",
            share="smb-share",
        )
        repository = self._proxy_bound_nas_repository(
            proxy=proxy, name="self-backup-repo"
        )
        repository.nas_protocol = Repository.NasProtocol.SMB
        repository.config = {
            "server_address": "192.168.8.82",
            "share_path": "/smb-share",
        }
        repository.save(update_fields=["nas_protocol", "config", "updated_at"])

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._nas_payload(
                source=source,
                repository=repository,
                path=f"/hp-repos/storage-{repository.id}/s",
            ),
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            create.status_code, status.HTTP_400_BAD_REQUEST, create.content
        )
        self.assertIn("directories", self._error_fields(create))

    def test_create_backup_config_allows_repository_sibling_source_path(self):
        proxy = self._proxy(name="sibling-path-proxy")
        source = self._nas_source(
            proxy=proxy,
            name="sibling-path-source",
            server="192.168.8.82",
            share="smb-share",
        )
        repository = self._proxy_bound_nas_repository(
            proxy=proxy, name="sibling-path-repo"
        )
        repository.nas_protocol = Repository.NasProtocol.SMB
        repository.config = {
            "server_address": "192.168.8.82",
            "share_path": "/smb-share",
        }
        repository.save(update_fields=["nas_protocol", "config", "updated_at"])

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._nas_payload(
                source=source, repository=repository, path="/hp-repos/restore"
            ),
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)

        update = self.client.patch(
            f"/api/v1/protection/backup-configs/{create.data['id']}/",
            {
                "directories": [
                    {
                        "path": f"/hp-repos/storage-{repository.id}",
                        "path_type": "directory",
                    }
                ],
            },
            format="json",
            **self._headers(),
        )
        self.assertEqual(
            update.status_code, status.HTTP_400_BAD_REQUEST, update.content
        )
        self.assertIn("directories", self._error_fields(update))

    def test_create_backup_config_rejects_direct_nas_repository_source_path(self):
        proxy = self._proxy(name="direct-self-backup-proxy")
        source = self._nas_source(
            proxy=proxy,
            name="direct-self-backup-source",
            server="192.168.8.82",
            share="smb-share",
        )
        repository = self._direct_nas_repository(name="direct-self-backup-repo")
        repository.nas_protocol = Repository.NasProtocol.SMB
        repository.config = {
            "server_address": "192.168.8.82",
            "share_path": "/smb-share",
        }
        repository.save(update_fields=["nas_protocol", "config", "updated_at"])

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._nas_payload(
                source=source,
                repository=repository,
                path=f"/hp-repos/agent-{proxy.id}/data",
            ),
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            create.status_code, status.HTTP_400_BAD_REQUEST, create.content
        )
        self.assertIn("directories", self._error_fields(create))

    def test_cross_proxy_repository_policy_is_applied_at_backup_runtime(self):
        source_proxy = self._proxy(name="policy-source-proxy")
        repository_proxy = self._proxy(name="policy-repository-proxy")
        source = self._nas_source(proxy=source_proxy, name="policy-nas-source")
        repository = self._proxy_bound_nas_repository(
            proxy=repository_proxy, name="policy-repo"
        )
        repository.config = {
            **repository.config,
            "proxy_repository_server_host": "repo-policy.example.internal",
        }
        repository.save(update_fields=["config", "updated_at"])
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._nas_payload(source=source, repository=repository),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)

        result = sync_backup_config_repository_policy(config_id=create.data["id"])

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "runtime_policy_applied_during_backup")

    @mock.patch(
        "apps.storage.services.internal.repository_usage.enqueue_repository_usage_refresh"
    )
    @mock.patch("apps.protection.services.backup_config.run_agent_task_sync")
    def test_update_backup_config_rejects_switching_to_direct_nas(
        self,
        run_agent_task_sync,
        enqueue_usage,
    ):
        run_agent_task_sync.return_value = self._successful_agent_task()
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Switch target config"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        nas_repo = self._direct_nas_repository(name="direct-nas-repo-2")

        update = self.client.patch(
            f"/api/v1/protection/backup-configs/{create.data['id']}/",
            {"repository_id": nas_repo.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(update.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("repository_id", self._error_fields(update))
        run_agent_task_sync.assert_not_called()
        enqueue_usage.assert_not_called()

    def test_delete_backup_config_is_not_supported(self):
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="No delete config"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)

        delete = self.client.delete(
            f"/api/v1/protection/backup-configs/{create.data['id']}/",
            **self._headers(),
        )
        self.assertEqual(delete.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(BackupConfig.objects.filter(id=create.data["id"]).exists())

    def test_reset_backup_config_requires_exact_reset_confirmation(self):
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Reset confirmation config"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)

        for confirmation in ("reset", "RESET ", " RESET"):
            with self.subTest(confirmation=confirmation):
                response = self.client.post(
                    "/api/v1/protection/backup-configs/reset/",
                    {
                        "source_ids": [f"agent:{self.agent.id}"],
                        "confirmation": confirmation,
                    },
                    format="json",
                    **self._headers(),
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            Task.objects.filter(task_type=Task.Type.BACKUP_CONFIG_RESET).exists()
        )

    @mock.patch(
        "apps.protection.tasks.backup_config_reset.execute_backup_config_reset_task.delay"
    )
    def test_reset_backup_config_creates_reset_task_and_marks_config_resetting(
        self, delay
    ):
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Reset API config"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        config_id = create.data["id"]

        response = self.client.post(
            "/api/v1/protection/backup-configs/reset/",
            {"source_ids": [f"agent:{self.agent.id}"], "confirmation": "RESET"},
            format="json",
            **self._headers(),
        )

        self.assertEqual(
            response.status_code, status.HTTP_202_ACCEPTED, response.content
        )
        self.assertEqual(response.data["created_count"], 1)
        task_uuid = response.data["results"][0]["task_uuid"]
        task = Task.objects.get(task_uuid=task_uuid)
        self.assertEqual(task.task_type, Task.Type.BACKUP_CONFIG_RESET)
        self.assertTrue(
            task.resources.filter(
                resource_type=TaskResource.Type.BACKUP_SOURCE,
                resource_subtype="agent",
                resource_id=self.agent.id,
            ).exists()
        )
        self.assertEqual(
            list(task.resources.values_list("resource_type", flat=True)),
            [TaskResource.Type.BACKUP_SOURCE],
        )
        config = BackupConfig.objects.get(id=config_id)
        self.assertEqual(config.status, BackupConfig.Status.RESETTING)
        self.assertEqual(str(config.reset_task_uuid), str(task.task_uuid))
        delay.assert_not_called()

    @mock.patch(
        "apps.protection.tasks.backup_config_reset."
        "execute_backup_config_reset_task.delay"
    )
    def test_reset_reconciler_skips_parent_with_active_agent_delete(self, delay):
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP_CONFIG_RESET,
            status=Task.Status.PENDING,
            request_payload={
                "source_type": "agent",
                "source_ref_id": self.agent.id,
            },
        )
        Task.objects.filter(pk=task.pk).update(
            updated_at=timezone.now() - timedelta(minutes=5)
        )
        NodeTask.objects.create(
            organization=self.org,
            node=self.agent,
            parent_task=task,
            kind="snapshot.delete",
            correlation_type="protection.backup_config_reset",
            correlation_id=f"{task.task_uuid}:0:test",
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now() + timedelta(hours=1),
        )

        result = reconcile_stuck_backup_config_reset_tasks(stale_seconds=90)

        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["redispatched"], 0)
        delay.assert_not_called()

    def test_reset_backup_config_rejects_active_backup_before_creating_task(self):
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Reset blocked by active backup"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        backup_task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Active backup",
            status=Task.Status.PENDING,
        )
        TaskResource.objects.create(
            task=backup_task,
            resource_type=TaskResource.Type.BACKUP_SOURCE,
            resource_subtype="agent",
            resource_id=self.agent.id,
            is_primary=True,
        )

        for active_status in (
            Task.Status.PENDING,
            Task.Status.WAITING,
            Task.Status.BLOCKED,
            Task.Status.RUNNING,
        ):
            with self.subTest(active_status=active_status):
                Task.objects.filter(pk=backup_task.pk).update(status=active_status)
                response = self.client.post(
                    "/api/v1/protection/backup-configs/reset/",
                    {
                        "source_ids": [f"agent:{self.agent.id}"],
                        "confirmation": "RESET",
                    },
                    format="json",
                    **self._headers(),
                )

                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                problem = response.data["data"]
                self.assertEqual(problem["code"], "BACKUP.ALREADY_RUNNING")
                self.assertEqual(
                    problem["meta"]["task_uuid"], str(backup_task.task_uuid)
                )
                self.assertEqual(problem["meta"]["task_type"], Task.Type.BACKUP)
                self.assertEqual(problem["meta"]["status"], active_status)
                self.assertEqual(problem["meta"]["source_type"], "agent")
                self.assertEqual(problem["meta"]["source_ref_id"], self.agent.id)
                self.assertFalse(
                    Task.objects.filter(
                        task_type=Task.Type.BACKUP_CONFIG_RESET
                    ).exists()
                )

        Task.objects.filter(pk=backup_task.pk).update(status=Task.Status.CANCELLED)
        node_task = NodeTask.objects.create(
            organization=self.org,
            node=self.agent,
            kind="backup.run",
            correlation_type="protection.backup",
            correlation_id=str(backup_task.task_uuid),
            status=NodeTask.Status.RUNNING,
            cancel_requested_at=timezone.now(),
            watchdog_deadline_at=timezone.now() + timedelta(hours=2),
        )
        with mock.patch("apps.node.conf.TASK_CANCEL_GRACE_SECONDS", 300):
            stopping = self.client.post(
                "/api/v1/protection/backup-configs/reset/",
                {
                    "source_ids": [f"agent:{self.agent.id}"],
                    "confirmation": "RESET",
                },
                format="json",
                **self._headers(),
            )
            self.assertEqual(stopping.status_code, status.HTTP_409_CONFLICT)
            self.assertEqual(stopping.data["data"]["meta"]["status"], "stopping")

            node_task.cancel_requested_at = timezone.now() - timedelta(seconds=301)
            node_task.save(update_fields=["cancel_requested_at", "updated_at"])
            finished = self.client.post(
                "/api/v1/protection/backup-configs/reset/",
                {
                    "source_ids": [f"agent:{self.agent.id}"],
                    "confirmation": "RESET",
                },
                format="json",
                **self._headers(),
            )
            self.assertEqual(finished.status_code, status.HTTP_202_ACCEPTED)

    @mock.patch(
        "apps.protection.tasks.backup_config_reset.execute_backup_config_reset_task.delay"
    )
    def test_reset_backup_config_reuses_active_reset_task(self, delay):
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Reset duplicate config"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        payload = {"source_ids": [f"agent:{self.agent.id}"], "confirmation": "RESET"}

        first = self.client.post(
            "/api/v1/protection/backup-configs/reset/",
            payload,
            format="json",
            **self._headers(),
        )
        second = self.client.post(
            "/api/v1/protection/backup-configs/reset/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED, first.content)
        self.assertEqual(second.status_code, status.HTTP_202_ACCEPTED, second.content)
        self.assertEqual(first.data["created_count"], 1)
        self.assertEqual(second.data["created_count"], 0)
        self.assertEqual(
            first.data["results"][0]["task_uuid"],
            second.data["results"][0]["task_uuid"],
        )
        self.assertTrue(first.data["results"][0]["created"])
        self.assertFalse(second.data["results"][0]["created"])
        self.assertEqual(
            Task.objects.filter(task_type=Task.Type.BACKUP_CONFIG_RESET).count(), 1
        )
        delay.assert_not_called()

    def test_reset_creation_is_blocked_by_active_source_unregister(self):
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Reset blocked by unregister"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        unregister_task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.SOURCE_UNREGISTER,
            display_name="Unregister backup source",
            status=Task.Status.RUNNING,
        )
        TaskResource.objects.create(
            task=unregister_task,
            resource_type=TaskResource.Type.BACKUP_SOURCE,
            resource_subtype="agent",
            resource_id=self.agent.id,
            is_primary=True,
        )

        with self.assertRaises(ValidationError):
            ensure_backup_config_reset_task(
                organization_id=self.org.id,
                source_type="agent",
                source_ref_id=self.agent.id,
            )

    def test_reset_creation_is_blocked_by_active_storage_validation(self):
        nas_repo = self._direct_nas_repository(name="reset-provisioning-direct-nas")
        payload = self._payload(name="Reset blocked by storage validation")
        payload["repository_id"] = nas_repo.id
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_202_ACCEPTED, create.content)
        Task.objects.filter(
            task_uuid=create.data["provisioning_task_uuid"],
        ).update(status=Task.Status.WAITING)

        with self.assertRaises(ValidationError):
            ensure_backup_config_reset_task(
                organization_id=self.org.id,
                source_type="agent",
                source_ref_id=self.agent.id,
            )

        config = BackupConfig.objects.get(id=create.data["id"])
        self.assertEqual(config.status, BackupConfig.Status.PROVISIONING)
        self.assertFalse(
            Task.objects.filter(task_type=Task.Type.BACKUP_CONFIG_RESET).exists()
        )
        config = BackupConfig.objects.get(pk=create.data["id"])
        self.assertNotEqual(config.status, BackupConfig.Status.RESETTING)

    @mock.patch("apps.protection.services.backup_config_reset.run_agent_task_async")
    def test_run_backup_config_reset_deletes_snapshots_configs_and_returns_to_step2(
        self, run_agent_task_async
    ):
        source_key = f"agent:{self.agent.id}"
        self.client.post(
            "/api/v1/source/backup-selectable/pipeline/",
            {"ids": [source_key], "step": 2},
            format="json",
            **self._headers(),
        )
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Reset service config"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        config = BackupConfig.objects.get(id=create.data["id"])
        directory = BackupConfigDirectory.objects.get(backup_config=config)
        backup_task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Reset source backup task",
        )
        snapshot = create_source_snapshot(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=config.id,
            repository_id=self.repository.id,
            task_id=backup_task.id,
            task_uuid=backup_task.task_uuid,
            idempotency_key="reset-service-source",
            status=BackupSourceSnapshot.Status.AVAILABLE,
            directory_count=1,
        )
        BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=snapshot,
            organization_id=self.org.id,
            backup_config_id=config.id,
            backup_config_dir_id=directory.id,
            source_path="/data",
            repository_id=self.repository.id,
            kopia_snapshot_id="reset-kopia-1",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        reset_task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP_CONFIG_RESET,
            display_name="Reset backup configuration for agent",
            request_payload={
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "backup_config_ids": [config.id],
                "repository_ids": [self.repository.id],
                "source_snapshot_ids": [snapshot.id],
            },
            current_step="prepare_reset",
        )
        for idx, step in enumerate(
            [
                "prepare_reset",
                "delete_kopia_snapshots",
                "delete_snapshot_records",
                "delete_restore_plans",
                "delete_backup_configs",
                "finalize_reset",
            ],
            start=1,
        ):
            reset_task.steps.create(step_index=idx, step_name=step)
        config.status = BackupConfig.Status.RESETTING
        config.reset_task_uuid = reset_task.task_uuid
        config.save(update_fields=["status", "reset_task_uuid", "updated_at"])

        def dispatch_async(**kwargs):
            node_task = NodeTask.objects.create(
                organization_id=kwargs["organization_id"],
                node_id=kwargs["node_id"],
                kind=kwargs["kind"],
                payload=kwargs["persisted_payload"],
                correlation_type=kwargs["correlation_type"],
                correlation_id=kwargs["correlation_id"],
                parent_task=kwargs["parent_task"],
                status=NodeTask.Status.RUNNING,
                dispatched_at=timezone.now(),
                accepted_at=timezone.now(),
                watchdog_deadline_at=timezone.now() + timedelta(hours=1),
            )
            return SimpleNamespace(task=node_task, task_id=str(node_task.id))

        run_agent_task_async.side_effect = dispatch_async

        waiting = run_backup_config_reset_task(
            organization_id=self.org.id,
            task_uuid=str(reset_task.task_uuid),
            source_type="agent",
            source_ref_id=self.agent.id,
        )

        reset_task.refresh_from_db()
        self.assertEqual(waiting["status"], "waiting")
        self.assertEqual(reset_task.status, Task.Status.PENDING)
        node_task = NodeTask.objects.get(
            parent_task=reset_task,
            kind="snapshot.delete",
        )
        node_task.status = NodeTask.Status.SUCCESS
        node_task.result = {
            "deleted_count": 1,
            "failed_count": 0,
            "results": [
                {"kopia_snapshot_id": "reset-kopia-1", "status": "success"}
            ],
        }
        node_task.save(update_fields=["status", "result", "updated_at"])

        result = run_backup_config_reset_task(
            organization_id=self.org.id,
            task_uuid=str(reset_task.task_uuid),
            source_type="agent",
            source_ref_id=self.agent.id,
        )

        reset_task.refresh_from_db()
        self.assertEqual(reset_task.status, Task.Status.SUCCESS)
        self.assertEqual(result["backup_configs_removed"], 1)
        self.assertFalse(BackupConfig.objects.filter(id=config.id).exists())
        self.assertFalse(BackupSourceSnapshot.objects.filter(id=snapshot.id).exists())
        entry = SourceBackupPipelineEntry.objects.get(
            organization=self.org,
            source_kind="agent",
            ref_id=self.agent.id,
        )
        self.assertEqual(entry.step, 2)
        run_agent_task_async.assert_called_once()

    @mock.patch("apps.protection.services.backup_config_reset.run_agent_task_async")
    def test_run_backup_config_reset_treats_missing_kopia_snapshot_as_deleted(
        self, run_agent_task_async
    ):
        source_key = f"agent:{self.agent.id}"
        self.client.post(
            "/api/v1/source/backup-selectable/pipeline/",
            {"ids": [source_key], "step": 2},
            format="json",
            **self._headers(),
        )
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Reset orphan snapshot config"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        config = BackupConfig.objects.get(id=create.data["id"])
        directory = BackupConfigDirectory.objects.get(backup_config=config)
        backup_task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Reset orphan snapshot backup task",
        )
        snapshot = create_source_snapshot(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
            backup_config_id=config.id,
            repository_id=self.repository.id,
            task_id=backup_task.id,
            task_uuid=backup_task.task_uuid,
            idempotency_key="reset-orphan-source",
            status=BackupSourceSnapshot.Status.AVAILABLE,
            directory_count=1,
        )
        BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=snapshot,
            organization_id=self.org.id,
            backup_config_id=config.id,
            backup_config_dir_id=directory.id,
            source_path="/data",
            repository_id=self.repository.id,
            kopia_snapshot_id="orphan-kopia-1",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        config.status = BackupConfig.Status.RESET_FAILED
        config.save(update_fields=["status", "updated_at"])
        reset_task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP_CONFIG_RESET,
            display_name="Reset backup configuration for orphan snapshot",
            request_payload={
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "backup_config_ids": [config.id],
                "repository_ids": [self.repository.id],
                "source_snapshot_ids": [snapshot.id],
            },
            current_step="prepare_reset",
        )
        for idx, step in enumerate(
            [
                "prepare_reset",
                "delete_kopia_snapshots",
                "delete_snapshot_records",
                "delete_restore_plans",
                "delete_backup_configs",
                "finalize_reset",
            ],
            start=1,
        ):
            reset_task.steps.create(step_index=idx, step_name=step)

        def dispatch_async(**kwargs):
            node_task = NodeTask.objects.create(
                organization_id=kwargs["organization_id"],
                node_id=kwargs["node_id"],
                kind=kwargs["kind"],
                payload=kwargs["persisted_payload"],
                correlation_type=kwargs["correlation_type"],
                correlation_id=kwargs["correlation_id"],
                parent_task=kwargs["parent_task"],
                status=NodeTask.Status.RUNNING,
                dispatched_at=timezone.now(),
                accepted_at=timezone.now(),
                watchdog_deadline_at=timezone.now() + timedelta(hours=1),
            )
            return SimpleNamespace(task=node_task, task_id=str(node_task.id))

        run_agent_task_async.side_effect = dispatch_async

        waiting = run_backup_config_reset_task(
            organization_id=self.org.id,
            task_uuid=str(reset_task.task_uuid),
            source_type="agent",
            source_ref_id=self.agent.id,
        )

        reset_task.refresh_from_db()
        self.assertEqual(waiting["status"], "waiting")
        self.assertEqual(reset_task.status, Task.Status.PENDING)
        node_task = NodeTask.objects.get(
            parent_task=reset_task,
            kind="snapshot.delete",
        )
        node_task.status = NodeTask.Status.FAILED
        node_task.last_error = "1 snapshot delete operation(s) failed"
        node_task.result = {
            "deleted_count": 0,
            "failed_count": 1,
            "results": [
                {
                    "kopia_snapshot_id": "orphan-kopia-1",
                    "status": "failed",
                    "error_message": "exit 1: exit status 1",
                    "delete": {
                        "stderr": (
                            "error deleting snapshots by root ID orphan-kopia-1: "
                            "no snapshots matched orphan-kopia-1"
                        ),
                        "stderr_tail": (
                            "error deleting snapshots by root ID orphan-kopia-1: "
                            "no snapshots matched orphan-kopia-1"
                        ),
                        "exit_code": 1,
                    },
                }
            ],
        }
        node_task.save(
            update_fields=["status", "last_error", "result", "updated_at"]
        )

        result = run_backup_config_reset_task(
            organization_id=self.org.id,
            task_uuid=str(reset_task.task_uuid),
            source_type="agent",
            source_ref_id=self.agent.id,
        )

        reset_task.refresh_from_db()
        self.assertEqual(reset_task.status, Task.Status.SUCCESS)
        self.assertEqual(result["backup_configs_removed"], 1)
        self.assertFalse(BackupConfig.objects.filter(id=config.id).exists())
        self.assertFalse(BackupSourceSnapshot.objects.filter(id=snapshot.id).exists())
        entry = SourceBackupPipelineEntry.objects.get(
            organization=self.org,
            source_kind="agent",
            ref_id=self.agent.id,
        )
        self.assertEqual(entry.step, 2)
        run_agent_task_async.assert_called_once()

    def test_revert_backup_flow_from_step3_to_step2(self):
        source_key = f"agent:{self.agent.id}"
        step2 = self.client.post(
            "/api/v1/source/backup-selectable/pipeline/",
            {"ids": [source_key], "step": 2},
            format="json",
            **self._headers(),
        )
        self.assertEqual(step2.status_code, status.HTTP_200_OK)

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Revert me"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        config_id = create.data["id"]

        revert = self.client.post(
            "/api/v1/source/backup-selectable/pipeline/revert/",
            {"ids": [source_key], "target_step": 2},
            format="json",
            **self._headers(),
        )
        self.assertEqual(revert.status_code, status.HTTP_200_OK)
        self.assertEqual(revert.data["updated"], [source_key])
        self.assertFalse(BackupConfig.objects.filter(id=config_id).exists())

        entry = SourceBackupPipelineEntry.objects.get(
            organization=self.org,
            source_kind="agent",
            ref_id=self.agent.id,
        )
        self.assertEqual(entry.step, 2)

        step3 = self.client.get(
            "/api/v1/source/backup-selectable/?step=3&page=1&page_size=10",
            **self._headers(),
        )
        self.assertNotIn(source_key, {row["id"] for row in step3.data["results"]})

        step2_list = self.client.get(
            "/api/v1/source/backup-selectable/?step=2&page=1&page_size=10",
            **self._headers(),
        )
        self.assertIn(source_key, {row["id"] for row in step2_list.data["results"]})

    def test_create_backup_config_rejects_missing_source_without_partial_config(self):
        missing_source_id = self.agent.id + 9999
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(
                source_ref_id=missing_source_id, name="Missing source config"
            ),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            BackupConfig.objects.filter(name="Missing source config").exists()
        )

    def test_create_backup_config_is_blocked_by_active_source_unregister(self):
        unregister_task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.SOURCE_UNREGISTER,
            display_name="Unregister source before configuration",
            status=Task.Status.RUNNING,
        )
        TaskResource.objects.create(
            task=unregister_task,
            resource_type=TaskResource.Type.BACKUP_SOURCE,
            resource_subtype="agent",
            resource_id=self.agent.id,
            is_primary=True,
        )

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Blocked by active unregister"),
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_400_BAD_REQUEST, create.content)
        self.assertFalse(
            BackupConfig.objects.filter(name="Blocked by active unregister").exists()
        )

    def test_create_backup_config_rejects_parent_child_directories(self):
        payload = self._payload(name="Parent child dirs")
        payload["directories"] = [{"path": "/data"}, {"path": "/data/projects"}]

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(BackupConfig.objects.filter(name="Parent child dirs").exists())

    def test_create_backup_config_persists_real_policy_filter_target_and_recovery_plan(
        self,
    ):
        policy = BackupPolicy.objects.create(
            organization_id=self.org.id,
            name="Hourly policy",
            schedule={"enabled": True, "cron_expr": "0 * * * *"},
            retention={"enabled": True, "recent_points": 3},
            throttling={"enabled": False, "unlimited": True, "rate_mbps": 0},
            error_handling={},
        )
        filter_a = FileFilterRule.objects.create(
            organization_id=self.org.id,
            name="Ignore tmp",
            ignore_patterns="*.tmp",
        )
        payload = self._payload(name="Full real config")
        payload.update(
            {
                "backup_policy_id": policy.id,
                "file_filter_rule_id": filter_a.id,
                "directories": [
                    {
                        "path": "/data/report.txt",
                        "path_type": "file",
                        "estimated_size_bytes": 1234,
                    }
                ],
                "recovery_plan_enabled": True,
                "recovery_plans": [
                    {
                        "source_path": "/data/report.txt",
                        "restore_host_id": self.agent.id,
                        "restore_dir": "/restore/data",
                        "conflict_mode": "overwrite",
                    }
                ],
            }
        )

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        self.assertEqual(create.data["backup_policy_id"], policy.id)
        self.assertEqual(create.data["file_filter_rule_id"], filter_a.id)
        self.assertNotIn("file_filter_rule_ids", create.data)
        self.assertNotIn("throttling", create.data)
        self.assertNotIn("nas_target_mode", create.data)
        self.assertEqual(create.data["directories"][0]["path_type"], "file")
        self.assertEqual(create.data["directories"][0]["estimated_size_bytes"], 1234)
        self.assertEqual(
            create.data["recovery_plans"][0]["restore_host_id"], self.agent.id
        )

        config = BackupConfig.objects.get(id=create.data["id"])
        self.assertEqual(config.file_filter_rule_id, filter_a.id)
        self.assertEqual(
            config.directories.get().path_type, BackupConfigDirectory.PathType.FILE
        )
        self.assertEqual(config.directories.get().estimated_size_bytes, 1234)
        plan = RestorePlan.objects.get(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.agent.id,
        )
        self.assertEqual(plan.restore_dir, "/restore/data")

        listing = self.client.get(
            "/api/v1/protection/backup-configs/",
            **self._headers(),
        )
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(listing.data["results"][0]["directory_count"], 1)

    def test_create_backup_config_rejects_relative_recovery_source_path(self):
        payload = self._payload(name="Relative recovery config")
        payload.update(
            {
                "recovery_plan_enabled": True,
                "recovery_plans": [
                    {
                        "source_path": ".",
                        "restore_host_id": self.agent.id,
                        "restore_dir": "/restore/data",
                        "conflict_mode": "skip",
                    }
                ],
            }
        )

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._error_fields(create), {"source_path"})
        self.assertFalse(
            BackupConfig.objects.filter(name="Relative recovery config").exists()
        )

    def test_patch_backup_config_replaces_directories_and_remaps_restore_plan(self):
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            {
                **self._payload(name="Patch dirs config"),
                "directories": [{"path": "/data/report.txt", "path_type": "file"}],
                "recovery_plan_enabled": True,
                "recovery_plans": [
                    {
                        "source_path": "/data/report.txt",
                        "restore_host_id": self.agent.id,
                        "restore_dir": "/restore/data",
                        "conflict_mode": "skip",
                    }
                ],
            },
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        config_id = create.data["id"]
        original_plan = RestorePlan.objects.get(backup_config_id=config_id)
        original_dir_id = original_plan.backup_config_dir_id

        patch = self.client.patch(
            f"/api/v1/protection/backup-configs/{config_id}/",
            {
                "directories": [
                    {"path": "/data", "path_type": "directory"},
                    {"path": "/logs", "path_type": "directory"},
                ],
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(patch.status_code, status.HTTP_200_OK, patch.content)
        self.assertEqual(patch.data["directory_count"], 2)
        self.assertEqual(
            list(
                BackupConfigDirectory.objects.filter(backup_config_id=config_id)
                .order_by("sort_order")
                .values_list("path", flat=True)
            ),
            ["/data", "/logs"],
        )
        original_plan.refresh_from_db()
        self.assertNotEqual(original_plan.backup_config_dir_id, original_dir_id)
        self.assertEqual(
            original_plan.backup_config_dir_id,
            BackupConfigDirectory.objects.get(
                backup_config_id=config_id, path="/data"
            ).id,
        )

    def test_patch_backup_config_rejects_relative_directory_path(self):
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            self._payload(name="Patch relative directory config"),
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        config_id = create.data["id"]

        patch = self.client.patch(
            f"/api/v1/protection/backup-configs/{config_id}/",
            {"directories": [{"path": "logs", "path_type": "directory"}]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(patch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._error_fields(patch), {"directories"})
        self.assertEqual(
            list(
                BackupConfigDirectory.objects.filter(
                    backup_config_id=config_id
                ).values_list("path", flat=True)
            ),
            ["/data"],
        )

    def test_patch_backup_config_rejects_relative_restore_plan_source_path(self):
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            {
                **self._payload(name="Patch relative restore config"),
                "directories": [
                    {
                        "path": "/root/backup_dir_81/rp_scripts",
                        "path_type": "directory",
                    },
                    {
                        "path": "/root/backup_dir_81/hyperfilelens-agent",
                        "path_type": "directory",
                    },
                ],
                "recovery_plan_enabled": True,
                "recovery_plans": [
                    {
                        "source_path": "/root/backup_dir_81/rp_scripts",
                        "restore_host_id": self.agent.id,
                        "restore_dir": "/restore/data",
                        "conflict_mode": "skip",
                    }
                ],
            },
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        config_id = create.data["id"]
        directory = BackupConfigDirectory.objects.get(
            backup_config_id=config_id,
            path="/root/backup_dir_81/rp_scripts",
        )
        plan = RestorePlan.objects.get(backup_config_id=config_id)
        plan.source_path = "."
        plan.backup_config_dir_id = directory.id
        plan.save(update_fields=["source_path", "backup_config_dir_id", "updated_at"])

        patch = self.client.patch(
            f"/api/v1/protection/backup-configs/{config_id}/",
            {
                "directories": [
                    {"path": "/root/kopia.log", "path_type": "file"},
                    {
                        "path": "/root/backup_dir_81/rp_scripts",
                        "path_type": "directory",
                    },
                    {
                        "path": "/root/backup_dir_81/hyperfilelens-agent",
                        "path_type": "directory",
                    },
                ],
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(patch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._error_fields(patch), {"directories"})
        self.assertEqual(
            list(
                BackupConfigDirectory.objects.filter(backup_config_id=config_id)
                .order_by("sort_order")
                .values_list("path", flat=True)
            ),
            [
                "/root/backup_dir_81/rp_scripts",
                "/root/backup_dir_81/hyperfilelens-agent",
            ],
        )
        plan.refresh_from_db()
        self.assertEqual(plan.source_path, ".")
        self.assertEqual(plan.backup_config_dir_id, directory.id)

    def test_patch_backup_config_rejects_directory_update_that_orphans_restore_plan(
        self,
    ):
        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            {
                **self._payload(name="Reject orphan restore config"),
                "directories": [{"path": "/data/report.txt", "path_type": "file"}],
                "recovery_plan_enabled": True,
                "recovery_plans": [
                    {
                        "source_path": "/data/report.txt",
                        "restore_host_id": self.agent.id,
                        "restore_dir": "/restore/data",
                        "conflict_mode": "skip",
                    }
                ],
            },
            format="json",
            **self._headers(),
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        config_id = create.data["id"]

        patch = self.client.patch(
            f"/api/v1/protection/backup-configs/{config_id}/",
            {"directories": [{"path": "/other", "path_type": "directory"}]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(patch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            list(
                BackupConfigDirectory.objects.filter(
                    backup_config_id=config_id
                ).values_list("path", flat=True)
            ),
            ["/data/report.txt"],
        )

    def test_create_backup_config_accepts_zero_sort_order_and_nested_recovery_source(
        self,
    ):
        payload = self._payload(name="Nested recovery config")
        payload.update(
            {
                "directories": [
                    {"path": "/tmp/ghw/dir1", "estimated_size_bytes": 0},
                    {"path": "/tmp/ghw/dir2", "estimated_size_bytes": 0},
                    {"path": "/tmp/ghw/dir3", "estimated_size_bytes": 0},
                ],
                "recovery_plan_enabled": True,
                "recovery_plans": [
                    {
                        "source_path": "/tmp/ghw/dir1",
                        "restore_host_id": self.agent.id,
                        "restore_dir": "/tmp/recover",
                        "conflict_mode": "skip",
                    },
                    {
                        "source_path": "/tmp/ghw/dir3/inner_dir2",
                        "restore_host_id": self.agent.id,
                        "restore_dir": "/tmp/recover/inner_dir2",
                        "conflict_mode": "skip",
                    },
                ],
            }
        )

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        plans = list(
            RestorePlan.objects.filter(
                organization_id=self.org.id,
                source_type="agent",
                source_ref_id=self.agent.id,
            ).order_by("sort_order")
        )
        self.assertEqual([plan.sort_order for plan in plans], [0, 1])
        self.assertEqual(plans[1].source_path, "/tmp/ghw/dir3/inner_dir2")
        self.assertEqual(
            BackupConfigDirectory.objects.get(id=plans[1].backup_config_dir_id).path,
            "/tmp/ghw/dir3",
        )

    def test_create_backup_config_accepts_whole_snapshot_recovery_plan(self):
        payload = self._payload(name="Whole snapshot recovery config")
        payload.update(
            {
                "directories": [
                    {"path": "/tmp/ghw/dir1", "estimated_size_bytes": 0},
                    {"path": "/tmp/ghw/dir2", "estimated_size_bytes": 0},
                ],
                "recovery_plan_enabled": True,
                "recovery_plans": [
                    {
                        "source_path": "",
                        "restore_host_id": self.agent.id,
                        "restore_dir": "/tmp/recover",
                        "conflict_mode": "skip",
                    }
                ],
            }
        )

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        plans = list(
            RestorePlan.objects.filter(
                organization_id=self.org.id,
                backup_config_id=create.data["id"],
            ).order_by("sort_order", "source_path")
        )
        self.assertEqual(
            [plan.source_path for plan in plans], ["/tmp/ghw/dir1", "/tmp/ghw/dir2"]
        )

    def test_create_backup_config_accepts_recovery_plan_target_ref_id(self):
        payload = self._payload(name="Recovery target ref config")
        payload.update(
            {
                "recovery_plan_enabled": True,
                "recovery_plans": [
                    {
                        "source_path": "/data",
                        "target_type": "agent",
                        "target_ref_id": self.agent.id,
                        "restore_dir": "/restore/data",
                        "conflict_mode": "skip",
                    }
                ],
            }
        )

        create = self.client.post(
            "/api/v1/protection/backup-configs/",
            payload,
            format="json",
            **self._headers(),
        )

        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.content)
        self.assertEqual(
            create.data["recovery_plans"][0]["target_ref_id"], self.agent.id
        )
        plan = RestorePlan.objects.get(
            organization_id=self.org.id,
            backup_config_id=create.data["id"],
        )
        self.assertEqual(plan.target_type, "agent")
        self.assertEqual(plan.target_ref_id, self.agent.id)
