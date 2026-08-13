from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.storage.repositories.models import Credential, Repository, RepositoryTask
from apps.storage.services.internal.repository_create import (
    _create_error_code,
    enqueue_repository_create_task,
    run_repository_create_task,
)
from apps.storage.services.internal.repository_errors import (
    REPOSITORY_ALREADY_EXISTS_CODE,
    RepositoryAlreadyExistsError,
)
from apps.storage.services.internal.repository_initializer import RepositoryInitializationError
from apps.storage.services.internal.s3_client import S3ClientError
from apps.storage.services.internal.nas_repository import NASRepositoryError
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
            _create_error_code(NASRepositoryError(
                "The SMB share was mounted, but the Agent could not create the repository directory.",
                error_code="NAS_REPOSITORY_WRITE_DENIED",
            )),
            "NAS_REPOSITORY_WRITE_DENIED",
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

    def _s3_repository(self, *, name: str = "async-s3", status=Repository.Status.CREATING):
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

    def _enqueue_create(self, repository, *, operation_type=RepositoryTask.OperationType.CREATE_REPOSITORY):
        return enqueue_repository_create_task(
            repository=repository,
            operation_type=operation_type,
            dispatch=False,
        )

    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch("apps.storage.services.internal.repository_create.initialize_s3_repository")
    def test_run_create_task_s3_success(self, initialize, _enqueue):
        repository = self._s3_repository()
        repository_task = self._enqueue_create(repository)

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "success")
        repository.refresh_from_db()
        self.assertEqual(repository.status, Repository.Status.CREATED)
        self.assertEqual(repository.health, Repository.Health.ONLINE)
        initialize.assert_called_once_with(repository)
        _enqueue.assert_called_once()

    @mock.patch("apps.storage.services.internal.repository_create.initialize_s3_repository")
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

    @mock.patch("apps.storage.services.internal.repository_create.initialize_s3_repository")
    def test_run_create_task_s3_failure_does_not_store_upstream_detail(self, initialize):
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

    @mock.patch("apps.storage.services.internal.repository_create.initialize_s3_repository")
    def test_run_create_task_s3_already_exists_deletes_row(self, initialize):
        initialize.side_effect = RepositoryAlreadyExistsError("repository already exists")
        repository = self._s3_repository()
        credential_id = repository.credential_id
        repository_task = self._enqueue_create(repository)

        result = run_repository_create_task(repository_task_id=repository_task.id)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], REPOSITORY_ALREADY_EXISTS_CODE)
        self.assertFalse(Repository.objects.filter(id=repository.id).exists())
        self.assertFalse(Credential.objects.filter(id=credential_id).exists())

    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_proxy_nas_repository"
    )
    def test_run_repair_bind_already_exists_restores_unbound(self, initialize, _enqueue):
        proxy = Node.objects.create(
            organization=self.org,
            name="repair-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
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
        initialize.side_effect = RepositoryAlreadyExistsError("repository already exists")
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
        initialize.assert_called_once()

    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch("apps.storage.services.internal.repository_create.initialize_s3_repository")
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

    @mock.patch(
        "apps.storage.services.internal.repository_create.enqueue_repository_usage_refresh"
    )
    @mock.patch("apps.storage.services.internal.repository_create.initialize_s3_repository")
    def test_resume_already_exists_finalizes_instead_of_delete(self, initialize, _enqueue):
        initialize.side_effect = RepositoryAlreadyExistsError("repository already exists")
        repository = self._s3_repository()
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

    @mock.patch(
        "apps.storage.services.internal.repository_create.initialize_proxy_nas_repository"
    )
    def test_repair_bind_resume_already_exists_still_restores_unbound(self, initialize):
        initialize.side_effect = RepositoryAlreadyExistsError("repository already exists")
        proxy = Node.objects.create(
            organization=self.org,
            name="repair-proxy-resume",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
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

    @mock.patch("apps.storage.services.internal.repository_create._run_repair_remount")
    def test_repair_remount_failure_restores_previous_proxy(self, remount):
        remount.side_effect = RuntimeError("remount failed")
        old_proxy = Node.objects.create(
            organization=self.org,
            name="old-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.41",
        )
        new_proxy = Node.objects.create(
            organization=self.org,
            name="new-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.42",
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="remount-nas",
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
        repository.refresh_from_db()
        self.assertEqual(repository.bind_node_id, old_proxy.id)
        self.assertEqual(repository.status, Repository.Status.CREATED)
        self.assertEqual(repository.health, Repository.Health.OFFLINE)
        self.assertEqual(
            repository.config.get("proxy_mount_path"),
            "/mnt/hfl/storage-repositories/repo-old",
        )

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
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.48",
        )
        new_proxy = Node.objects.create(
            organization=self.org,
            name="new-proxy-step",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
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
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.46",
        )
        new_proxy = Node.objects.create(
            organization=self.org,
            name="new-proxy-finalize",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
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
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.44",
        )
        new_proxy = Node.objects.create(
            organization=self.org,
            name="new-proxy-resume",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
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
