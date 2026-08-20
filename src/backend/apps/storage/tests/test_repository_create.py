from unittest import mock

from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.storage.repositories.models import (
    Credential,
    Repository,
    RepositoryLocationClaim,
    RepositoryTask,
)
from apps.storage.services.internal.repository_create import (
    _create_error_code,
    enqueue_repository_create_task,
    run_repository_create_task,
)
from apps.storage.services.internal.repository_errors import (
    REPOSITORY_ALREADY_EXISTS_CODE,
    RepositoryAlreadyExistsError,
)
from apps.storage.services.internal.repository_initializer import (
    RepositoryInitializationError,
)
from apps.storage.services.internal.repository_location import (
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    reserve_repository_location,
)
from apps.storage.services.internal.s3_client import S3ClientError
from apps.storage.services.internal.nas_repository import NASRepositoryError
from apps.storage.services.internal.proxy_fs_repository import ProxyFSRepositoryError
from apps.task.models import Task


class RepositoryCreateTaskTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="repository-create-org",
            name="Repository Create Org",
        )
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="repository-create@test.local",
            password="test-pass",
        )
        Membership.objects.create(
            user=self.user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )

    def test_nas_write_denied_error_code_is_preserved(self):
        self.assertEqual(
            _create_error_code(
                NASRepositoryError(
                    "The SMB share was mounted, but the Agent could not create the repository directory.",
                    error_code="NAS_REPOSITORY_WRITE_DENIED",
                )
            ),
            "NAS_REPOSITORY_WRITE_DENIED",
        )

    def test_proxy_fs_agent_upgrade_error_code_is_preserved(self):
        self.assertEqual(
            _create_error_code(
                ProxyFSRepositoryError(
                    "Agent upgrade required.",
                    error_code="AGENT_UPGRADE_REQUIRED",
                )
            ),
            "AGENT_UPGRADE_REQUIRED",
        )

    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_proxy_nas_repository"
    )
    def test_nas_write_denied_failure_is_saved_on_task(self, initialize):
        proxy = Node.objects.create(
            organization=self.org,
            name="write-denied-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.41",
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="write-denied-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.SMB,
            status=Repository.Status.CREATING,
            health=Repository.Health.OFFLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={"server_address": "10.0.0.10", "share_path": "/backup"},
        )
        initialize.side_effect = NASRepositoryError(
            "The SMB share was mounted, but the Agent could not create the repository directory.",
            error_code="NAS_REPOSITORY_WRITE_DENIED",
        )
        repository_task = self._enqueue_create(repository)

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["error_code"], "NAS_REPOSITORY_WRITE_DENIED")
        repository_task.task.refresh_from_db()
        self.assertEqual(repository_task.task.error_code, "NAS_REPOSITORY_WRITE_DENIED")

    def _s3_repository(
        self, *, name: str = "async-s3", status=Repository.Status.CREATING
    ):
        credential = Credential.objects.create(
            organization_id=self.org.id,
            credential_type=Credential.Type.S3,
            metadata={"access_key_id": "AKIA_TEST"},
        )
        credential.set_secret_payload(
            {"secret_access_key": "secret", "kopia_password": "kopia-pass"}
        )
        credential.save()
        return Repository.objects.create(
            organization_id=self.org.id,
            name=name,
            repo_type=Repository.Type.S3,
            status=status,
            health=Repository.Health.OFFLINE,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="async-bucket",
            credential_id=credential.id,
            config={
                "region": "us-east-1",
                "endpoint": "s3.amazonaws.com",
                "prefix": "kopia",
                "access_key_id": "AKIA_TEST",
            },
        )

    def _enqueue_create(
        self,
        repository,
        *,
        operation_type=RepositoryTask.OperationType.CREATE_REPOSITORY,
    ):
        return enqueue_repository_create_task(
            repository=repository,
            operation_type=operation_type,
            dispatch=False,
        )

    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_run_create_task_s3_success(self, initialize, _enqueue):
        repository = self._s3_repository()
        repository_task = self._enqueue_create(repository)

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "success")
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.CREATED)
        self.assertEqual(repository.health, Repository.Health.ONLINE)
        initialize.assert_called_once_with(repository, recovery=False)
        _enqueue.assert_called_once()

    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_run_create_task_s3_init_failure_keeps_row(self, initialize):
        initialize.side_effect = RepositoryInitializationError("S3 init failed")
        repository = self._s3_repository()
        repository_task = self._enqueue_create(repository)

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "STORAGE.S3_VALIDATION_FAILED")
        self.assertEqual(
            result["error"],
            "Object storage validation failed. Check the connection settings and IAM permissions, then try again.",
        )
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.CREATE_FAILED)
        self.assertEqual(repository.health, Repository.Health.OFFLINE)
        self.assertTrue(Repository.objects.filter(id=repository.id).exists())

    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_run_create_task_s3_failure_does_not_store_upstream_detail(
        self, initialize
    ):
        try:
            raise S3ClientError("AccessDenied: secret-token-value")
        except S3ClientError as upstream:
            initialize.side_effect = RepositoryInitializationError(
                "unsafe wrapper text"
            )
            initialize.side_effect.__cause__ = upstream
        repository = self._s3_repository()
        repository_task = self._enqueue_create(repository)

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["error_code"], "STORAGE.S3_VALIDATION_FAILED")
        self.assertNotIn("secret-token-value", result["error"])
        repository_task.task.refresh_from_db()
        self.assertNotIn("secret-token-value", repository_task.task.error_message)

    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_run_create_task_persists_bucket_name_provider_failure(self, initialize):
        provider_error = ClientError(
            {
                "Error": {
                    "Code": "BucketAlreadyExists",
                    "Message": "unsafe provider detail",
                }
            },
            "CreateBucket",
        )
        try:
            raise S3ClientError("unsafe wrapper text") from provider_error
        except S3ClientError as upstream:
            initialize.side_effect = RepositoryInitializationError(
                "unsafe initialization detail"
            )
            initialize.side_effect.__cause__ = upstream
        repository = self._s3_repository()
        repository_task = self._enqueue_create(repository)

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(
            result["error_code"],
            "STORAGE.S3_BUCKET_NAME_UNAVAILABLE",
        )
        self.assertNotIn("unsafe", result["error"])
        repository_task.task.refresh_from_db()
        self.assertEqual(
            repository_task.task.error_code,
            "STORAGE.S3_BUCKET_NAME_UNAVAILABLE",
        )
        self.assertNotIn("unsafe", repository_task.task.error_message)

    @mock.patch(
        "apps.storage.services.internal.repository_create.check_s3_repository",
        side_effect=RepositoryInitializationError("wrong repository password"),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_first_create_already_exists_never_adopts_or_checks_access(
        self, initialize, check_repository
    ):
        initialize.side_effect = RepositoryAlreadyExistsError(
            "repository already exists"
        )
        repository = self._s3_repository()
        credential_id = repository.credential_id
        repository_task = self._enqueue_create(repository)

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], REPOSITORY_ALREADY_EXISTS_CODE)
        self.assertFalse(Repository.objects.filter(id=repository.id).exists())
        self.assertFalse(Credential.objects.filter(id=credential_id).exists())
        check_repository.assert_not_called()

    @mock.patch(
        "apps.storage.services.internal.repository_create.check_s3_repository",
        side_effect=RepositoryInitializationError("wrong repository password"),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository",
        side_effect=RepositoryAlreadyExistsError("repository already exists"),
    )
    def test_already_exists_cleanup_preserves_shared_legacy_credential(
        self,
        _initialize,
        _check_repository,
    ):
        repository = self._s3_repository(name="failed-shared-credential")
        credential_id = repository.credential_id
        retained = Repository.objects.create(
            organization_id=self.org.id,
            name="retained-shared-credential",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            credential_id=credential_id,
            s3_platform=Repository.S3Platform.AWS,
            s3_bucket="retained-bucket",
            config={
                "endpoint": "s3.amazonaws.com",
                "prefix": "retained/",
                "access_key_id": "test-access-key",
            },
        )
        repository_task = self._enqueue_create(repository)

        run_repository_create_task(repository_task_id=repository_task.id)

        self.assertFalse(Repository.objects.filter(id=repository.id).exists())
        self.assertTrue(Repository.objects.filter(id=retained.id).exists())
        self.assertTrue(Credential.objects.filter(id=credential_id).exists())

    @mock.patch(
        "apps.storage.services.internal.repository_create.check_s3_repository",
        side_effect=RepositoryInitializationError("wrong repository password"),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository",
        side_effect=RepositoryAlreadyExistsError("repository already exists"),
    )
    def test_already_exists_cleanup_rolls_back_as_one_transaction(
        self,
        _initialize,
        _check_repository,
    ):
        repository = self._s3_repository(name="atomic-already-exists")
        credential_id = repository.credential_id
        claim = reserve_repository_location(repository)
        repository_task = self._enqueue_create(repository)
        target_queryset = mock.Mock()
        target_queryset.delete.side_effect = RuntimeError("database write failed")

        with mock.patch(
            "apps.storage.services.internal.repository_create."
            "RepositoryExecutionTarget.objects.filter",
            return_value=target_queryset,
        ):
            with self.assertRaisesRegex(RuntimeError, "database write failed"):
                run_repository_create_task(repository_task_id=repository_task.id)

        self.assertTrue(Repository.objects.filter(id=repository.id).exists())
        self.assertTrue(Credential.objects.filter(id=credential_id).exists())
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.INITIALIZING)
        repository_task.task.refresh_from_db()
        self.assertEqual(repository_task.task.status, Task.Status.RUNNING)

    @mock.patch(
        "apps.storage.services.internal.repository_create.check_proxy_nas_repository",
        side_effect=NASRepositoryError("wrong repository password"),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_proxy_nas_repository"
    )
    def test_run_repair_bind_already_exists_restores_unbound(
        self, initialize, _enqueue, check_repository
    ):
        proxy = Node.objects.create(
            organization=self.org,
            name="repair-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.40",
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="repair-bind-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.SMB,
            status=Repository.Status.CREATING,
            health=Repository.Health.OFFLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={
                "server_address": "10.0.0.10",
                "share_path": "/backup",
                "proxy_mount_path": "/mnt/hfl/storage-repositories/repo-1-node-1",
            },
        )
        initialize.side_effect = RepositoryAlreadyExistsError(
            "repository already exists"
        )
        claim = reserve_repository_location(repository)
        repository_task = self._enqueue_create(
            repository,
            operation_type=RepositoryTask.OperationType.REPAIR_BIND,
        )

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], REPOSITORY_ALREADY_EXISTS_CODE)
        repository.refresh_from_db()
        self.assertIsNone(repository.bind_node_type)
        self.assertIsNone(repository.bind_node_id)
        self.assertEqual(repository.status, Repository.Status.CREATED)
        self.assertEqual(repository.health, Repository.Health.UNVERIFIED)
        self.assertNotIn("proxy_mount_path", repository.config)
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)
        initialize.assert_called_once()
        check_repository.assert_not_called()

    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.check_proxy_nas_repository"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_proxy_nas_repository",
        side_effect=RepositoryAlreadyExistsError("repository already exists"),
    )
    def test_repair_bind_retry_claims_its_interrupted_repository(
        self, initialize, check_repository, _enqueue
    ):
        proxy = Node.objects.create(
            organization=self.org,
            name="repair-proxy-retry",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.44",
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="repair-bind-retry-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.SMB,
            status=Repository.Status.CREATING,
            health=Repository.Health.OFFLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={
                "server_address": "10.0.0.10",
                "share_path": "/backup-retry",
                "proxy_mount_path": "/mnt/hfl/storage-repositories/repo-retry",
            },
        )
        claim = reserve_repository_location(repository)
        claim.state = RepositoryLocationClaim.State.RESIDUAL
        claim.save(update_fields=["state", "updated_at"])
        repository_task = self._enqueue_create(
            repository,
            operation_type=RepositoryTask.OperationType.REPAIR_BIND,
        )

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "success")
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.CREATED)
        self.assertEqual(repository.health, Repository.Health.ONLINE)
        self.assertEqual(repository.bind_node_id, proxy.id)
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.OWNED)
        initialize.assert_called_once()
        check_repository.assert_called_once()

    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_resume_skips_initialize_after_step_complete(self, initialize, _enqueue):
        repository = self._s3_repository()
        repository_task = self._enqueue_create(repository)
        task = repository_task.task
        task.status = Task.Status.RUNNING
        task.current_step = "initialize_repository"
        task.progress = 85
        task.save(update_fields=["status", "current_step", "progress", "updated_at"])

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "success")
        initialize.assert_not_called()
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.CREATED)
        self.assertEqual(repository.health, Repository.Health.ONLINE)

    @mock.patch("apps.storage.services.internal.repository_create.check_s3_repository")
    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_resume_already_exists_finalizes_after_access_verification(
        self,
        initialize,
        _enqueue,
        check_repository,
    ):
        initialize.side_effect = RepositoryAlreadyExistsError(
            "repository already exists"
        )
        repository = self._s3_repository()
        claim = reserve_repository_location(repository)
        claim.state = RepositoryLocationClaim.State.INITIALIZING
        claim.save(update_fields=["state", "updated_at"])
        repository_task = self._enqueue_create(repository)
        task = repository_task.task
        task.status = Task.Status.RUNNING
        task.current_step = "initialize_repository"
        task.progress = 45
        task.save(update_fields=["status", "current_step", "progress", "updated_at"])

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "success")
        self.assertTrue(Repository.objects.filter(id=repository.id).exists())
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.CREATED)
        self.assertEqual(repository.health, Repository.Health.ONLINE)
        check_repository.assert_called_once_with(repository)

    @mock.patch("apps.storage.services.internal.repository_create.check_s3_repository")
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository",
        side_effect=RepositoryAlreadyExistsError("repository already exists"),
    )
    def test_resume_without_location_claim_does_not_adopt_existing_repository(
        self,
        _initialize,
        check_repository,
    ):
        repository = self._s3_repository(name="resume-without-claim")
        repository_task = self._enqueue_create(repository)
        task = repository_task.task
        task.status = Task.Status.RUNNING
        task.current_step = "initialize_repository"
        task.progress = 45
        task.save(update_fields=["status", "current_step", "progress", "updated_at"])

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], REPOSITORY_ALREADY_EXISTS_CODE)
        self.assertFalse(Repository.objects.filter(id=repository.id).exists())
        check_repository.assert_not_called()

    @mock.patch(
        "apps.storage.services.internal.repository_create.check_s3_repository",
        side_effect=RepositoryInitializationError("wrong repository password"),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_resume_already_exists_without_access_proof_retains_residual_claim(
        self,
        initialize,
        check_repository,
    ):
        initialize.side_effect = RepositoryAlreadyExistsError(
            "repository already exists"
        )
        repository = self._s3_repository()
        claim = reserve_repository_location(repository)
        repository_task = self._enqueue_create(repository)
        task = repository_task.task
        task.status = Task.Status.RUNNING
        task.current_step = "initialize_repository"
        task.progress = 45
        task.save(update_fields=["status", "current_step", "progress", "updated_at"])

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "failed")
        self.assertTrue(Repository.objects.filter(id=repository.id).exists())
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.CREATE_FAILED)
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)
        initialize.assert_called_once_with(repository, recovery=True)
        check_repository.assert_called_once_with(repository)

    @mock.patch(
        "apps.storage.services.internal.repository_create.check_s3_repository",
        side_effect=RepositoryInitializationError("temporary verification failure"),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository",
        side_effect=RepositoryAlreadyExistsError("repository already exists"),
    )
    def test_retry_already_exists_without_access_proof_retains_residual_claim(
        self,
        _initialize,
        check_repository,
    ):
        repository = self._s3_repository(status=Repository.Status.CREATE_FAILED)
        claim = reserve_repository_location(repository)
        claim.state = RepositoryLocationClaim.State.RESIDUAL
        claim.save(update_fields=["state", "updated_at"])
        repository_task = self._enqueue_create(repository)

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "failed")
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.CREATE_FAILED)
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)
        _initialize.assert_called_once_with(repository, recovery=True)
        check_repository.assert_called_once_with(repository)

    @mock.patch("apps.storage.services.internal.repository_create.check_s3_repository")
    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository",
        side_effect=RepositoryAlreadyExistsError("repository already exists"),
    )
    def test_explicit_retry_may_recover_verified_residual_location(
        self,
        _initialize,
        _enqueue,
        check_repository,
    ):
        repository = self._s3_repository(status=Repository.Status.CREATE_FAILED)
        claim = reserve_repository_location(repository)
        claim.state = RepositoryLocationClaim.State.RESIDUAL
        claim.save(update_fields=["state", "updated_at"])
        repository_task = self._enqueue_create(repository)

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "success")
        repository.refresh_from_db()
        claim.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.CREATED)
        self.assertEqual(claim.state, RepositoryLocationClaim.State.OWNED)
        _initialize.assert_called_once_with(repository, recovery=True)
        check_repository.assert_called_once_with(repository)

    @mock.patch(
        "apps.storage.services.internal.repository_create.check_proxy_nas_repository",
        side_effect=NASRepositoryError("wrong repository password"),
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_proxy_nas_repository"
    )
    def test_repair_bind_resume_already_exists_still_restores_unbound(
        self, initialize, check_repository
    ):
        initialize.side_effect = RepositoryAlreadyExistsError(
            "repository already exists"
        )
        proxy = Node.objects.create(
            organization=self.org,
            name="repair-proxy-resume",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.43",
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="repair-bind-resume-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.SMB,
            status=Repository.Status.CREATING,
            health=Repository.Health.OFFLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={
                "server_address": "10.0.0.10",
                "share_path": "/backup",
                "proxy_mount_path": "/mnt/hfl/storage-repositories/repo-resume",
            },
        )
        repository_task = self._enqueue_create(
            repository,
            operation_type=RepositoryTask.OperationType.REPAIR_BIND,
        )
        claim = reserve_repository_location(repository)
        task = repository_task.task
        task.status = Task.Status.RUNNING
        task.current_step = "initialize_repository"
        task.progress = 45
        task.save(update_fields=["status", "current_step", "progress", "updated_at"])

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], REPOSITORY_ALREADY_EXISTS_CODE)
        repository.refresh_from_db()
        self.assertIsNone(repository.bind_node_type)
        self.assertIsNone(repository.bind_node_id)
        self.assertEqual(repository.status, Repository.Status.CREATED)
        self.assertEqual(repository.health, Repository.Health.UNVERIFIED)
        self.assertNotIn("proxy_mount_path", repository.config)
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.RESIDUAL)
        check_repository.assert_called_once()

    @mock.patch("apps.storage.services.internal.repository_create._set_create_step")
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_s3_repository"
    )
    def test_create_step_persist_failure_keeps_verified_location_owned(
        self, _initialize, set_step
    ):
        from apps.storage.services.internal.repository_operations import set_task_step
        from apps.task.models import TaskStep

        def _set_real(task, step_name, status, progress):
            if (
                step_name == "initialize_repository"
                and status == TaskStep.Status.SUCCESS
                and int(progress) >= 85
            ):
                raise RuntimeError("initialize step persist failed")
            task.refresh_from_db(fields=["current_step", "progress"])
            set_task_step(task, step_name, status=status, progress=progress)

        set_step.side_effect = _set_real
        repository = self._s3_repository(name="owned-after-step-failure")
        claim = reserve_repository_location(repository)
        repository_task = self._enqueue_create(repository)

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "failed")
        claim.refresh_from_db()
        self.assertEqual(claim.state, RepositoryLocationClaim.State.OWNED)
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.CREATE_FAILED)

    @mock.patch("apps.storage.services.internal.repository_create._run_repair_remount")
    def test_repair_remount_failure_restores_previous_proxy(self, remount):
        remount.side_effect = RuntimeError("remount failed")
        old_proxy = Node.objects.create(
            organization=self.org,
            name="old-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.41",
        )
        new_proxy = Node.objects.create(
            organization=self.org,
            name="new-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.42",
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="remount-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.OFFLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=old_proxy.id,
            config={
                "server_address": "10.0.0.10",
                "share_path": "/export",
                "proxy_mount_path": "/mnt/hfl/storage-repositories/repo-old",
            },
        )
        old_claim = reserve_repository_location(repository)
        mark_repository_location_owned(repository, owner_node_id=old_proxy.id)
        mark_repository_location_ownership_verified(
            repository,
            owner_node_id=old_proxy.id,
        )
        repository.bind_node_id = new_proxy.id
        repository.status = Repository.Status.CREATING
        repository.config = {
            **repository.config,
            "proxy_mount_path": "/mnt/hfl/storage-repositories/repo-new",
        }
        repository.save(
            update_fields=["bind_node_id", "status", "config", "updated_at"]
        )
        new_claim = reserve_repository_location(repository)
        repository_task = enqueue_repository_create_task(
            repository=repository,
            operation_type=RepositoryTask.OperationType.REPAIR_REMOUNT,
            remount_previous_node_id=old_proxy.id,
            remount_previous_mount_path="/mnt/hfl/storage-repositories/repo-old",
            remount_new_claim_id=new_claim.id,
            dispatch=False,
        )

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "failed")
        repository.refresh_from_db()
        self.assertEqual(repository.bind_node_id, old_proxy.id)
        self.assertEqual(repository.status, Repository.Status.CREATED)
        self.assertEqual(repository.health, Repository.Health.OFFLINE)
        self.assertEqual(
            repository.config.get("proxy_mount_path"),
            "/mnt/hfl/storage-repositories/repo-old",
        )
        old_claim.refresh_from_db()
        new_claim.refresh_from_db()
        self.assertEqual(old_claim.state, RepositoryLocationClaim.State.OWNED)
        self.assertEqual(new_claim.state, RepositoryLocationClaim.State.RESIDUAL)

    @mock.patch("apps.storage.services.internal.repository_create._set_create_step")
    @mock.patch("apps.storage.services.internal.repository_create._run_repair_remount")
    def test_remount_step_persist_failure_after_physical_keeps_new_proxy(
        self, remount, set_step
    ):
        from apps.storage.services.internal.repository_operations import set_task_step
        from apps.task.models import TaskStep

        remount.return_value = None

        def _set_real(task, step_name, status, progress):
            if (
                step_name == "initialize_repository"
                and status == TaskStep.Status.SUCCESS
                and int(progress) >= 85
            ):
                raise RuntimeError("initialize step persist failed")
            task.refresh_from_db(fields=["current_step", "progress"])
            set_task_step(task, step_name, status=status, progress=progress)

        set_step.side_effect = _set_real
        old_proxy = Node.objects.create(
            organization=self.org,
            name="old-proxy-step",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.48",
        )
        new_proxy = Node.objects.create(
            organization=self.org,
            name="new-proxy-step",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.49",
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="remount-step-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATING,
            health=Repository.Health.OFFLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=new_proxy.id,
            config={
                "server_address": "10.0.0.10",
                "share_path": "/export",
                "proxy_mount_path": "/mnt/hfl/storage-repositories/repo-new",
            },
        )
        repository_task = enqueue_repository_create_task(
            repository=repository,
            operation_type=RepositoryTask.OperationType.REPAIR_REMOUNT,
            remount_previous_node_id=old_proxy.id,
            remount_previous_mount_path="/mnt/hfl/storage-repositories/repo-old",
            dispatch=False,
        )

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "failed")
        remount.assert_called_once()
        repository.refresh_from_db()
        self.assertEqual(repository.bind_node_id, new_proxy.id)
        self.assertEqual(
            repository.config.get("proxy_mount_path"),
            "/mnt/hfl/storage-repositories/repo-new",
        )
        self.assertEqual(repository.status, Repository.Status.CREATED)
        self.assertEqual(repository.health, Repository.Health.OFFLINE)

    @mock.patch(
        "apps.storage.services.internal.repository_create._complete_create_success"
    )
    @mock.patch("apps.storage.services.internal.repository_create._run_repair_remount")
    def test_remount_finalize_failure_keeps_new_proxy(self, remount, complete):
        remount.return_value = None
        complete.side_effect = RuntimeError("finalize failed")
        old_proxy = Node.objects.create(
            organization=self.org,
            name="old-proxy-finalize",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.46",
        )
        new_proxy = Node.objects.create(
            organization=self.org,
            name="new-proxy-finalize",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.47",
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="remount-finalize-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATING,
            health=Repository.Health.OFFLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=new_proxy.id,
            config={
                "server_address": "10.0.0.10",
                "share_path": "/export",
                "proxy_mount_path": "/mnt/hfl/storage-repositories/repo-new",
            },
        )
        repository_task = enqueue_repository_create_task(
            repository=repository,
            operation_type=RepositoryTask.OperationType.REPAIR_REMOUNT,
            remount_previous_node_id=old_proxy.id,
            remount_previous_mount_path="/mnt/hfl/storage-repositories/repo-old",
            dispatch=False,
        )

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "failed")
        remount.assert_called_once()
        repository.refresh_from_db()
        self.assertEqual(repository.bind_node_id, new_proxy.id)
        self.assertEqual(
            repository.config.get("proxy_mount_path"),
            "/mnt/hfl/storage-repositories/repo-new",
        )
        self.assertEqual(repository.status, Repository.Status.CREATED)
        self.assertEqual(repository.health, Repository.Health.OFFLINE)

    @mock.patch("apps.storage.services.internal.repository_create._run_repair_remount")
    def test_remount_resume_after_rollback_does_not_rerun_remount(self, remount):
        old_proxy = Node.objects.create(
            organization=self.org,
            name="old-proxy-resume",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.44",
        )
        new_proxy = Node.objects.create(
            organization=self.org,
            name="new-proxy-resume",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.45",
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="remount-resume-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.OFFLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=old_proxy.id,
            config={
                "server_address": "10.0.0.10",
                "share_path": "/export",
                "proxy_mount_path": "/mnt/hfl/storage-repositories/repo-old",
            },
        )
        repository_task = enqueue_repository_create_task(
            repository=Repository.objects.get(pk=repository.id),
            operation_type=RepositoryTask.OperationType.REPAIR_REMOUNT,
            remount_previous_node_id=old_proxy.id,
            remount_previous_mount_path="/mnt/hfl/storage-repositories/repo-old",
            dispatch=False,
        )
        # Simulate enqueue-time payload pointing at the intended new proxy, while
        # the live row already rolled back to the previous proxy.
        task = repository_task.task
        payload = dict(task.request_payload or {})
        payload["bind_node_id"] = new_proxy.id
        task.request_payload = payload
        task.status = Task.Status.RUNNING
        task.current_step = "initialize_repository"
        task.progress = 45
        task.save(
            update_fields=[
                "request_payload",
                "status",
                "current_step",
                "progress",
                "updated_at",
            ]
        )
        # Keep live binding rolled back (enqueue temporarily set CREATING + new bind).
        repository.bind_node_id = old_proxy.id
        repository.status = Repository.Status.CREATED
        repository.health = Repository.Health.OFFLINE
        repository.config = {
            "server_address": "10.0.0.10",
            "share_path": "/export",
            "proxy_mount_path": "/mnt/hfl/storage-repositories/repo-old",
        }
        repository.save(
            update_fields=[
                "bind_node_id",
                "status",
                "health",
                "config",
                "updated_at",
            ]
        )

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "failed")
        self.assertTrue(result.get("idempotent"))
        remount.assert_not_called()
        repository.refresh_from_db()
        self.assertEqual(repository.bind_node_id, old_proxy.id)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.FAILED)
