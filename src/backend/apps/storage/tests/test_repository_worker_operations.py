from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.iam.models import Organization
from apps.monitor.models import OperationalEvent
from apps.storage.repositories.models import Credential, Repository
from apps.storage.services.internal import repository_credential_rotation
from apps.storage.services.internal.repository_check import (
    enqueue_repository_check_task,
    run_repository_check_task,
)
from apps.storage.services.internal.repository_credential_rotation import (
    enqueue_repository_credential_rotation,
    run_repository_credential_rotation_task,
)
from apps.storage.services.internal.repository_initializer import (
    RepositoryInitializationError,
)
from apps.storage.services.internal.kopia_cli import KopiaRepositoryBusyError
from apps.task.models import Task, TaskStep


class RepositoryWorkerOperationTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            key="repository-worker-operations",
            name="Repository Worker Operations",
        )

    def _s3_repository(self, *, secret: str = "old-secret") -> Repository:
        credential = Credential(
            organization_id=self.organization.id,
            credential_type=Credential.Type.S3,
        )
        credential.set_secret_payload(
            {
                "secret_access_key": secret,
                "kopia_password": "repository-password",
            }
        )
        credential.save()
        return Repository.objects.create(
            organization_id=self.organization.id,
            name="Worker-owned S3",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            credential_id=credential.id,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="worker-test-bucket",
            config={
                "endpoint": "s3.example.test",
                "region": "us-east-1",
                "prefix": "hfl/",
                "access_key_id": "old-access-key",
                "s3_url_style": "path",
                "use_tls": True,
            },
        )

    @mock.patch("apps.storage.services.internal.repository_check.sync_repository_usage")
    @mock.patch(
        "apps.storage.services.internal.repository_check.probe_repository_health",
        return_value=Repository.Health.ONLINE,
    )
    def test_manual_check_runs_as_a_durable_worker_operation(
        self,
        probe_health,
        sync_usage,
    ):
        repository = self._s3_repository()
        repository_task = enqueue_repository_check_task(repository=repository)

        result = run_repository_check_task(repository_task_id=repository_task.id)

        repository.refresh_from_db()
        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "success")
        self.assertEqual(repository.health, Repository.Health.ONLINE)
        self.assertIsNotNone(repository.last_checked_at)
        self.assertEqual(repository_task.task.status, Task.Status.SUCCESS)
        probe_health.assert_called_once_with(repository_task.repository)
        sync_usage.assert_called_once()

    @mock.patch(
        "apps.storage.services.internal.repository_check.probe_repository_health",
        side_effect=RuntimeError("probe failed with old-secret"),
    )
    def test_manual_check_persists_a_safe_visible_failure(self, _probe_health):
        repository = self._s3_repository(secret="old-secret")
        repository_task = enqueue_repository_check_task(repository=repository)

        result = run_repository_check_task(repository_task_id=repository_task.id)

        repository.refresh_from_db()
        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(repository.health, Repository.Health.OFFLINE)
        self.assertEqual(repository_task.task.status, Task.Status.FAILED)
        self.assertNotIn("old-secret", repository_task.task.error_message)
        self.assertIn("******", repository_task.task.error_message)

    @mock.patch(
        "apps.storage.services.internal.repository_check.probe_repository_health"
    )
    def test_concurrent_offline_transition_does_not_emit_a_duplicate_event(
        self,
        probe_health,
    ):
        repository = self._s3_repository()
        repository_task = enqueue_repository_check_task(repository=repository)

        def mark_offline_then_fail(_repository):
            Repository.objects.filter(pk=repository.id).update(
                health=Repository.Health.OFFLINE,
            )
            raise RuntimeError("probe failed")

        probe_health.side_effect = mark_offline_then_fail
        with self.captureOnCommitCallbacks(execute=True):
            result = run_repository_check_task(repository_task_id=repository_task.id)

        repository.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(repository.health, Repository.Health.OFFLINE)
        self.assertIsNotNone(repository.last_checked_at)
        self.assertFalse(
            OperationalEvent.objects.filter(event_type="repository.offline").exists()
        )

    @mock.patch(
        "apps.storage.services.internal.repository_check.probe_repository_health"
    )
    def test_manual_check_does_not_mark_busy_repository_offline(self, probe_health):
        repository = self._s3_repository()
        repository_task = enqueue_repository_check_task(repository=repository)
        wrapped = RepositoryInitializationError("repository busy")
        wrapped.__cause__ = KopiaRepositoryBusyError("repository busy")
        probe_health.side_effect = wrapped

        result = run_repository_check_task(repository_task_id=repository_task.id)

        repository.refresh_from_db()
        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "STORAGE.REPOSITORY_BUSY")
        self.assertEqual(repository.health, Repository.Health.ONLINE)
        self.assertIsNone(repository.last_checked_at)
        self.assertEqual(repository_task.task.status, Task.Status.FAILED)
        self.assertEqual(
            repository_task.task.error_code,
            "STORAGE.REPOSITORY_BUSY",
        )

    def test_repeated_manual_check_reuses_the_active_task(self):
        repository = self._s3_repository()

        first = enqueue_repository_check_task(repository=repository)
        second = enqueue_repository_check_task(repository=repository)

        self.assertEqual(first.id, second.id)

    @mock.patch(
        "apps.storage.services.internal.repository_check.sync_repository_usage",
        side_effect=RuntimeError("usage refresh failed"),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_check.probe_repository_health",
        return_value=Repository.Health.ONLINE,
    )
    def test_usage_failure_does_not_overwrite_successful_connectivity(
        self,
        _probe_health,
        _sync_usage,
    ):
        repository = self._s3_repository()
        repository_task = enqueue_repository_check_task(repository=repository)

        result = run_repository_check_task(repository_task_id=repository_task.id)

        repository.refresh_from_db()
        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(repository.health, Repository.Health.ONLINE)
        self.assertEqual(repository_task.task.status, Task.Status.FAILED)

    @mock.patch(
        "apps.storage.services.internal.repository_credential_rotation.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_credential_rotation.check_s3_repository"
    )
    def test_s3_credentials_activate_only_after_worker_verification(
        self,
        check_repository,
        enqueue_usage,
    ):
        repository = self._s3_repository()
        previous_credential_id = repository.credential_id
        repository_task = enqueue_repository_credential_rotation(
            repository=repository,
            name="Renamed after verification",
            config={"access_key_id": "new-access-key", "use_tls": False},
            credential_payload={"secret_access_key": "new-secret"},
        )
        candidate_id = repository_task.task.request_payload["candidate_credential_id"]

        repository.refresh_from_db()
        self.assertEqual(repository.credential_id, previous_credential_id)
        result = run_repository_credential_rotation_task(
            repository_task_id=repository_task.id
        )

        repository.refresh_from_db()
        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "success")
        self.assertEqual(repository_task.task.status, Task.Status.SUCCESS)
        self.assertEqual(repository.credential_id, candidate_id)
        self.assertEqual(repository.name, "Renamed after verification")
        self.assertEqual(repository.config["access_key_id"], "new-access-key")
        self.assertFalse(repository.config["use_tls"])
        self.assertFalse(Credential.objects.filter(id=previous_credential_id).exists())
        self.assertNotIn(
            "repository_credential_rotation",
            Credential.objects.get(id=candidate_id).metadata,
        )
        checked_repository = check_repository.call_args.args[0]
        self.assertEqual(checked_repository.credential_id, candidate_id)
        self.assertFalse(check_repository.call_args.kwargs["refresh_namespace"])
        enqueue_usage.assert_called_once()

    @mock.patch(
        "apps.storage.services.internal.repository_credential_rotation.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_credential_rotation.check_s3_repository"
    )
    def test_legacy_config_secrets_can_be_rotated_without_losing_kopia_password(
        self,
        _check_repository,
        _enqueue_usage,
    ):
        repository = Repository.objects.create(
            organization_id=self.organization.id,
            name="Legacy S3",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="legacy-bucket",
            config={
                "endpoint": "s3.example.test",
                "prefix": "hfl/",
                "access_key_id": "legacy-access-key",
                "secret_access_key": "legacy-secret",
                "kopia_password": "legacy-kopia-password",
                "s3_url_style": "path",
            },
        )
        repository_task = enqueue_repository_credential_rotation(
            repository=repository,
            name=repository.name,
            config={},
            credential_payload={"secret_access_key": "new-secret"},
        )

        result = run_repository_credential_rotation_task(
            repository_task_id=repository_task.id
        )

        repository.refresh_from_db()
        self.assertEqual(result["status"], "success")
        self.assertIsNotNone(repository.credential_id)
        self.assertNotIn("secret_access_key", repository.config)
        self.assertNotIn("kopia_password", repository.config)
        secrets = Credential.objects.get(id=repository.credential_id).get_secret_payload()
        self.assertEqual(secrets["secret_access_key"], "new-secret")
        self.assertEqual(secrets["kopia_password"], "legacy-kopia-password")

    @mock.patch(
        "apps.storage.services.internal.repository_credential_rotation.check_s3_repository",
        side_effect=RepositoryInitializationError("new-secret was rejected"),
    )
    def test_failed_rotation_keeps_old_credentials_and_removes_candidate(
        self,
        _check_repository,
    ):
        repository = self._s3_repository()
        previous_credential_id = repository.credential_id
        repository_task = enqueue_repository_credential_rotation(
            repository=repository,
            name=repository.name,
            config={"access_key_id": "new-access-key"},
            credential_payload={"secret_access_key": "new-secret"},
        )
        candidate_id = repository_task.task.request_payload["candidate_credential_id"]

        result = run_repository_credential_rotation_task(
            repository_task_id=repository_task.id
        )

        repository.refresh_from_db()
        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(repository_task.task.status, Task.Status.FAILED)
        self.assertEqual(repository.credential_id, previous_credential_id)
        self.assertTrue(Credential.objects.filter(id=previous_credential_id).exists())
        self.assertFalse(Credential.objects.filter(id=candidate_id).exists())
        self.assertNotIn("new-secret", repository_task.task.error_message)

    @mock.patch(
        "apps.storage.services.internal.repository_credential_rotation.check_s3_repository"
    )
    def test_finalization_interruption_does_not_delete_activated_credentials(
        self,
        _check_repository,
    ):
        repository = self._s3_repository()
        repository_task = enqueue_repository_credential_rotation(
            repository=repository,
            name=repository.name,
            config={},
            credential_payload={"secret_access_key": "new-secret"},
        )
        candidate_id = repository_task.task.request_payload["candidate_credential_id"]

        with mock.patch(
            "apps.storage.services.internal.repository_credential_rotation._complete_rotation_success",
            side_effect=RuntimeError("worker interrupted during finalization"),
        ):
            with self.assertRaisesMessage(RuntimeError, "worker interrupted"):
                run_repository_credential_rotation_task(
                    repository_task_id=repository_task.id
                )

        repository.refresh_from_db()
        self.assertEqual(repository.credential_id, candidate_id)
        self.assertTrue(Credential.objects.filter(id=candidate_id).exists())

    @mock.patch(
        "apps.storage.services.internal.repository_credential_rotation.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_credential_rotation.check_s3_repository"
    )
    def test_recovery_converges_steps_after_credential_activation(
        self,
        _check_repository,
        _enqueue_usage,
    ):
        repository = self._s3_repository()
        repository_task = enqueue_repository_credential_rotation(
            repository=repository,
            name=repository.name,
            config={},
            credential_payload={"secret_access_key": "new-secret"},
        )

        original_set_step = repository_credential_rotation._set_step

        def interrupt_after_activation(task, step_name, status, progress):
            if (
                step_name == "activate_credentials"
                and status == TaskStep.Status.SUCCESS
                and progress == 100
            ):
                raise RuntimeError("worker interrupted after credential activation")
            return original_set_step(task, step_name, status, progress)

        with mock.patch.object(
            repository_credential_rotation,
            "_set_step",
            side_effect=interrupt_after_activation,
        ):
            with self.assertRaisesMessage(RuntimeError, "worker interrupted"):
                run_repository_credential_rotation_task(
                    repository_task_id=repository_task.id
                )

        result = run_repository_credential_rotation_task(
            repository_task_id=repository_task.id
        )

        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "success")
        self.assertEqual(repository_task.task.status, Task.Status.SUCCESS)
        self.assertFalse(
            repository_task.task.steps.exclude(
                status=TaskStep.Status.SUCCESS
            ).exists()
        )

    @mock.patch(
        "apps.storage.services.internal.repository_credential_rotation.check_s3_repository"
    )
    def test_concurrent_settings_change_is_not_overwritten(
        self,
        _check_repository,
    ):
        repository = self._s3_repository()
        previous_credential_id = repository.credential_id
        repository_task = enqueue_repository_credential_rotation(
            repository=repository,
            name="Candidate name",
            config={"quota_gb": 10},
            credential_payload={"secret_access_key": "new-secret"},
        )
        Repository.objects.filter(pk=repository.id).update(
            name="Concurrent name",
            config={**repository.config, "quota_gb": 20},
        )

        result = run_repository_credential_rotation_task(
            repository_task_id=repository_task.id
        )

        repository.refresh_from_db()
        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(repository_task.task.status, Task.Status.FAILED)
        self.assertEqual(repository.name, "Concurrent name")
        self.assertEqual(repository.config["quota_gb"], 20)
        self.assertEqual(repository.credential_id, previous_credential_id)

    def test_second_rotation_is_rejected_while_first_is_active(self):
        repository = self._s3_repository()
        enqueue_repository_credential_rotation(
            repository=repository,
            name=repository.name,
            config={},
            credential_payload={"secret_access_key": "first-secret"},
        )

        with self.assertRaisesMessage(ValidationError, "active operation"):
            enqueue_repository_credential_rotation(
                repository=repository,
                name=repository.name,
                config={},
                credential_payload={"secret_access_key": "second-secret"},
            )

    def test_candidate_decryption_failure_finishes_task_and_keeps_old_credentials(self):
        repository = self._s3_repository()
        previous_credential_id = repository.credential_id
        repository_task = enqueue_repository_credential_rotation(
            repository=repository,
            name=repository.name,
            config={},
            credential_payload={"secret_access_key": "new-secret"},
        )
        candidate_id = repository_task.task.request_payload["candidate_credential_id"]

        with mock.patch.object(
            Credential,
            "get_secret_payload",
            side_effect=ValueError("candidate cannot be decrypted"),
        ):
            result = run_repository_credential_rotation_task(
                repository_task_id=repository_task.id
            )

        repository.refresh_from_db()
        repository_task.task.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(repository_task.task.status, Task.Status.FAILED)
        self.assertEqual(repository.credential_id, previous_credential_id)
        self.assertFalse(Credential.objects.filter(id=candidate_id).exists())
