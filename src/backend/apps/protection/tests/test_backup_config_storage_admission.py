"""Configuration saves must not consume or require measured backup storage."""

from unittest import skipUnless
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.protection.models import BackupConfig, BackupPolicy, BackupSourceSnapshot
from apps.protection.services.policy_execution import schedule_due_backup_tasks
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_location import (
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    reserve_repository_location,
)
from apps.subscription.constants import COMMUNITY_DEFAULT_LIMITS
from apps.subscription.services.interface import enforce_license_quota
from apps.task.models import Task
from common.errors import AppError
from common.extension_spi import (
    clear_providers_for_tests,
    register_quota_provider,
    restore_providers_for_tests,
)


class BackupConfigStorageAdmissionMixin:
    """Exercise real quota measurement, with no Agent or broker calls."""

    enterprise = False
    storage_limit = 1024**2

    def setUp(self):
        super().setUp()
        test_settings = override_settings(
            HFL_QUOTA_ENFORCEMENT_ENABLED=True,
            SECURE_SSL_REDIRECT=False,
            CHANNEL_LAYERS={
                "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
            },
        )
        test_settings.enable()
        self.addCleanup(test_settings.disable)
        previous = clear_providers_for_tests()
        self.addCleanup(restore_providers_for_tests, previous)
        user = get_user_model().objects.create_user(
            username="config-storage@test.local",
            email="config-storage@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(
            key="config-storage-org", name="Configuration storage admission"
        )
        Membership.objects.create(
            user=user, organization=self.org, role=Membership.Role.OWNER
        )
        self.agent = Node.objects.create(
            organization=self.org,
            name="config-storage-agent",
            role=Node.Role.AGENT,
            os_name="linux",
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="Selected storage",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            usage_probe_status=Repository.MetricProbeStatus.SUCCESS,
            usage_last_success_at=timezone.now(),
            estimated_usage_bytes=64,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="config-storage-bucket",
            config={
                "endpoint": "s3.example.internal:9000",
                "region": "test-region",
                "access_key_id": "test-access-key",
                "secret_access_key": "test-secret-key",
                "kopia_password": "test-repository-password",
                "use_tls": False,
            },
        )
        reserve_repository_location(self.repository)
        mark_repository_location_owned(self.repository)
        mark_repository_location_ownership_verified(self.repository)
        limits = {
            "max_protected_sources": 1,
            "max_storage_bytes": self.storage_limit,
        }
        if self.enterprise:
            from apps.subscription.services.internal.effective_quota import (
                set_org_quota_limits,
            )
            from apps.subscription.services.quota_provider import LedgerQuotaProvider

            set_org_quota_limits(organization=self.org, limits=limits)
            register_quota_provider(LedgerQuotaProvider())
        else:
            quota_limits = patch(
                "apps.subscription.services.quota.COMMUNITY_DEFAULT_LIMITS",
                {**COMMUNITY_DEFAULT_LIMITS, **limits},
            )
            quota_limits.start()
            self.addCleanup(quota_limits.stop)
        self.client = APIClient()
        self.client.force_authenticate(user=user)

    def _save_config(self):
        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            {
                "name": "Save without storage admission",
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "repository_id": self.repository.id,
                "directories": [{"path": "/data"}],
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertFalse(Task.objects.filter(task_type=Task.Type.BACKUP).exists())
        self.assertFalse(BackupSourceSnapshot.objects.exists())
        return BackupConfig.objects.get(pk=response.data["id"])

    def _assert_storage_rejected(self, code: str):
        with self.assertRaises(AppError) as raised:
            enforce_license_quota(self.org, "max_storage_bytes", additional=0)
        self.assertEqual(raised.exception.code, code)

    def _set_unmeasured_storage(self, probe_status: str):
        Repository.objects.filter(pk=self.repository.pk).update(
            usage_probe_status=probe_status, usage_last_success_at=None
        )

    def test_save_with_pending_first_usage(self):
        self._set_unmeasured_storage(Repository.MetricProbeStatus.PENDING)
        self._assert_storage_rejected("SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE")
        self._save_config()

    def test_save_with_failed_first_usage(self):
        self._set_unmeasured_storage(Repository.MetricProbeStatus.FAILED)
        self._assert_storage_rejected("SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE")
        self._save_config()

    def test_save_with_other_unmeasured_repository(self):
        Repository.objects.create(
            organization_id=self.org.id,
            name="Other storage awaiting first measurement",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            usage_probe_status=Repository.MetricProbeStatus.PENDING,
        )
        self._assert_storage_rejected("SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE")
        self._save_config()

    def test_save_when_storage_quota_is_full(self):
        Repository.objects.filter(pk=self.repository.pk).update(
            estimated_usage_bytes=self.storage_limit
        )
        self._assert_storage_rejected("SUBSCRIPTION.QUOTA_EXCEEDED")
        self._save_config()

    def test_save_still_rejects_protected_source_quota_exhaustion(self):
        other_agent = Node.objects.create(
            organization=self.org, name="Already protected", role=Node.Role.AGENT
        )
        BackupConfig.objects.create(
            organization_id=self.org.id,
            name="Existing protected source",
            source_type="agent",
            source_ref_id=other_agent.id,
            repository_id=self.repository.id,
        )
        response = self.client.post(
            "/api/v1/protection/backup-configs/",
            {
                "name": "Over protected-source limit",
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "repository_id": self.repository.id,
                "directories": [{"path": "/data"}],
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.content)
        self.assertFalse(
            BackupConfig.objects.filter(source_ref_id=self.agent.id).exists()
        )

    @patch("apps.protection.services.backup_task._queue_backup_execution")
    def test_saved_config_cannot_start_backup_until_usage_is_measured(self, queue):
        self._set_unmeasured_storage(Repository.MetricProbeStatus.PENDING)
        self._save_config()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/v1/protection/backup-tasks/",
                {"source_ids": [f"agent:{self.agent.id}"], "trigger_type": "manual"},
                format="json",
                HTTP_X_ORG_KEY=self.org.key,
            )
        # Batch admission may report a failed item instead of an HTTP error.
        self.assertEqual(response.data["created_count"], 0, response.content)
        self.assertEqual(response.data["results"][0]["status"], "failed")
        self.assertIn("Quota usage", response.data["results"][0]["message"])
        self.assertFalse(Task.objects.filter(task_type=Task.Type.BACKUP).exists())
        self.assertFalse(BackupSourceSnapshot.objects.exists())
        queue.assert_not_called()

        Repository.objects.filter(pk=self.repository.pk).update(
            usage_probe_status=Repository.MetricProbeStatus.SUCCESS,
            usage_last_success_at=timezone.now(),
        )
        with (
            patch(
                "apps.protection.tasks.directory_size_estimate."
                "refresh_backup_config_directory_estimates_task.delay"
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            retry = self.client.post(
                "/api/v1/protection/backup-tasks/",
                {"source_ids": [f"agent:{self.agent.id}"], "trigger_type": "manual"},
                format="json",
                HTTP_X_ORG_KEY=self.org.key,
            )
        self.assertEqual(retry.data["created_count"], 1, retry.content)
        queue.assert_called_once()

    @patch("apps.protection.services.backup_task._queue_backup_execution")
    def test_scheduled_backup_still_rejects_full_storage(self, queue):
        Repository.objects.filter(pk=self.repository.pk).update(
            estimated_usage_bytes=self.storage_limit
        )
        config = self._save_config()
        policy = BackupPolicy.objects.create(
            organization_id=self.org.id,
            name="Scheduled backup",
            is_active=True,
            schedule={"enabled": True, "cron_expr": "* * * * *"},
        )
        BackupConfig.objects.filter(pk=config.pk).update(backup_policy_id=policy.id)
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(AppError) as raised:
                schedule_due_backup_tasks(now=timezone.now())
        self.assertEqual(raised.exception.code, "SUBSCRIPTION.QUOTA_EXCEEDED")
        self.assertFalse(Task.objects.filter(task_type=Task.Type.BACKUP).exists())
        self.assertFalse(BackupSourceSnapshot.objects.exists())
        queue.assert_not_called()


class CommunityBackupConfigStorageAdmissionTests(
    BackupConfigStorageAdmissionMixin, TestCase
):
    def test_usage_error_identifies_only_unmeasured_repositories_in_this_organization(self):
        blocker = Repository.objects.create(
            organization_id=self.org.id,
            name="Unmeasured storage",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
        )
        other_org = Organization.objects.create(key="other-usage-org", name="Other")
        Repository.objects.create(
            organization_id=other_org.id,
            name="Private storage",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
        )
        with self.assertRaises(AppError) as raised:
            enforce_license_quota(self.org, "max_storage_bytes", additional=0)
        self.assertEqual(raised.exception.code, "SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE")
        self.assertEqual(raised.exception.meta["scope"], "organization")
        self.assertEqual(raised.exception.meta["blocking_repository_ids"], [blocker.id])
        self.assertFalse(raised.exception.meta["blocking_repositories_truncated"])


@skipUnless(apps.is_installed("apps.subscription_gov"), "Requires the EE extension")
class EnterpriseBackupConfigStorageAdmissionTests(
    BackupConfigStorageAdmissionMixin, TestCase
):
    enterprise = True
