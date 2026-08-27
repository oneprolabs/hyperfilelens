from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node.models import Node
from apps.protection.models import (
    BackupConfig,
    BackupConfigDirectory,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
)
from apps.protection.services.backup_target_validation import TargetValidationResult
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.storage.repositories.models import Repository


class RestoreTargetValidationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user = get_user_model().objects.create_user(
            username="restore-target-validation@test.local",
            email="restore-target-validation@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(
            key="restore-target-validation-org",
            name="Restore Target Validation Org",
        )
        Membership.objects.create(
            user=user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.source = self._node("source", metadata={"inventory": {"os": "linux"}})
        self.target = self._node("target", metadata={"inventory": {"os": "linux"}})
        self.repository = Repository.objects.create(
            organization_id=self.org.id,
            name="restore repository",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_bucket="restore-bucket",
            config={
                "endpoint": "s3.example.internal:9000",
                "access_key_id": "ak",
                "secret_access_key": "sk",
                "kopia_password": "password",
            },
        )
        config = BackupConfig.objects.create(
            organization_id=self.org.id,
            name="restore validation config",
            source_type="agent",
            source_ref_id=self.source.id,
            repository_id=self.repository.id,
        )
        config_dir = BackupConfigDirectory.objects.create(
            organization_id=self.org.id,
            backup_config=config,
            path="/data",
        )
        self.snapshot = BackupSourceSnapshot.objects.create(
            organization_id=self.org.id,
            snapshot_uid="restore-validation-snapshot",
            idempotency_key="restore-validation-snapshot",
            source_type="agent",
            source_ref_id=self.source.id,
            backup_config_id=config.id,
            repository_id=self.repository.id,
            task_id=1,
            status=BackupSourceSnapshot.Status.AVAILABLE,
        )
        self.directory = BackupSourceSnapshotDirectory.objects.create(
            organization_id=self.org.id,
            source_snapshot=self.snapshot,
            backup_config_id=config.id,
            backup_config_dir_id=config_dir.id,
            source_path="/data",
            repository_id=self.repository.id,
            kopia_snapshot_id="kopia-restore-validation",
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        self.client.force_authenticate(user=user)

    def _node(self, name, *, role=Node.Role.AGENT, metadata=None):
        return Node.objects.create(
            organization=self.org,
            name=name,
            role=role,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata=metadata or {},
        )

    def _payload(self, *, target_type="agent", target_ref_id=None):
        return {
            "targets": [
                {
                    "key": "source-1",
                    "source_snapshot_id": self.snapshot.id,
                    "target_type": target_type,
                    "target_ref_id": target_ref_id or self.target.id,
                }
            ]
        }

    def _post(self, payload):
        return self.client.post(
            "/api/v1/restore/target-validations/",
            payload,
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
        )

    @patch(
        "apps.restore.services.target_validation.validate_restore_repository_assignments"
    )
    def test_target_agent_validates_snapshot_repository_from_target(self, validate):
        validate.side_effect = lambda **kwargs: {
            assignment[0].key: TargetValidationResult(status="success")
            for assignment in kwargs["assignments"]
        }

        response = self._post(self._payload())

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["status"], "success")
        assignment = validate.call_args.kwargs["assignments"][0]
        self.assertEqual(assignment[1].node.id, self.target.id)
        self.assertEqual(assignment[2].id, self.repository.id)
        self.assertEqual(assignment[3].node.id, self.target.id)

    @patch(
        "apps.restore.services.target_validation.validate_restore_repository_assignments"
    )
    def test_direct_nas_uses_snapshot_subdir_on_linux_target(self, validate):
        self.repository.repo_type = Repository.Type.NAS
        self.repository.s3_bucket = ""
        self.repository.nas_protocol = Repository.NasProtocol.NFS
        self.repository.bind_node_type = None
        self.repository.bind_node_id = None
        self.repository.config = {
            "server_address": "10.0.0.20",
            "share_path": "/snapshots",
            "kopia_password": "password",
        }
        self.repository.save()
        self.directory.repository_locator = {
            "version": 1,
            "repository_id": self.repository.id,
            "repository_type": Repository.Type.NAS,
            "repository_subdir": f"hp-repos/agent-{self.source.id}",
            "writer_node_id": self.source.id,
            "access_node_id": None,
        }
        self.directory.save(update_fields=["repository_locator", "updated_at"])
        validate.side_effect = lambda **kwargs: {
            assignment[0].key: TargetValidationResult(status="success")
            for assignment in kwargs["assignments"]
        }

        response = self._post(self._payload())

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        assignment = validate.call_args.kwargs["assignments"][0]
        self.assertEqual(assignment[1].node.id, self.target.id)
        self.assertEqual(
            assignment[3].repository_payload["subdir"],
            f"hp-repos/agent-{self.source.id}",
        )

    @patch("apps.protection.services.backup_target_validation._execute_agent_task")
    def test_direct_nas_returns_mount_helper_guidance(self, execute):
        self.repository.repo_type = Repository.Type.NAS
        self.repository.s3_bucket = ""
        self.repository.nas_protocol = Repository.NasProtocol.SMB
        self.repository.bind_node_type = None
        self.repository.bind_node_id = None
        self.repository.config = {
            "server_address": "10.0.0.20",
            "share_path": "snapshots",
            "kopia_password": "password",
        }
        self.repository.save()
        self.directory.repository_locator = {
            "version": 1,
            "repository_id": self.repository.id,
            "repository_type": Repository.Type.NAS,
            "repository_subdir": f"hp-repos/agent-{self.source.id}",
            "writer_node_id": self.source.id,
            "access_node_id": None,
        }
        self.directory.save(update_fields=["repository_locator", "updated_at"])

        def outcome(**kwargs):
            failed = kwargs["kind"] == "repo.status"
            return type(
                "AgentOutcome",
                (),
                {
                    "ok": not failed,
                    "status": "failed" if failed else "success",
                    "message": (
                        "mount SMB share: cifs-utils is not installed "
                        "(missing mount.cifs helper)"
                        if failed
                        else ""
                    ),
                    "result": (
                        {
                            "error_code": "NAS_MOUNT_HELPER_MISSING",
                            "remediation": "install_nas_mount_helper",
                            "dependency": "cifs-utils",
                            "helper": "mount.cifs",
                        }
                        if failed
                        else {}
                    ),
                    "timed_out": False,
                },
            )()

        execute.side_effect = outcome

        response = self._post(self._payload())

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        result = response.data["results"][0]
        self.assertEqual(result["code"], "NAS_MOUNT_FAILED")
        self.assertEqual(result["details"]["remediation"], "install_nas_mount_helper")
        self.assertEqual(result["details"]["dependency"], "cifs-utils")
        self.assertEqual(result["details"]["helper"], "mount.cifs")
        self.assertEqual(result["details"]["execution_node_name"], self.target.name)
        probe, cleanup = execute.call_args_list
        mount_point = probe.kwargs["payload"]["repository"]["nas"]["mount_point"]
        self.assertIn("/mounts/validations/", mount_point)
        self.assertEqual(cleanup.kwargs["kind"], "nas.unmount")
        self.assertEqual(cleanup.kwargs["payload"], {"mount_point": mount_point})

    @patch(
        "apps.restore.services.target_validation.validate_restore_repository_assignments"
    )
    def test_direct_nas_rejects_non_linux_agent_target(self, validate):
        self.repository.repo_type = Repository.Type.NAS
        self.repository.s3_bucket = ""
        self.repository.nas_protocol = Repository.NasProtocol.NFS
        self.repository.bind_node_type = None
        self.repository.bind_node_id = None
        self.repository.config = {
            "server_address": "10.0.0.20",
            "share_path": "/snapshots",
            "kopia_password": "password",
        }
        self.repository.save()
        self.target.metadata = {"inventory": {"os": "windows"}}
        self.target.os_name = "Windows Server 2025"
        self.target.save(update_fields=["metadata", "os_name", "updated_at"])

        response = self._post(self._payload())

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["status"], "failed")
        self.assertEqual(
            response.data["results"][0]["code"], "RESTORE_TARGET_INCOMPATIBLE"
        )
        validate.assert_not_called()

    @patch(
        "apps.restore.services.target_validation.validate_restore_repository_assignments"
    )
    def test_direct_nas_to_source_nas_validates_from_bound_proxy(self, validate):
        self.repository.repo_type = Repository.Type.NAS
        self.repository.s3_bucket = ""
        self.repository.nas_protocol = Repository.NasProtocol.NFS
        self.repository.bind_node_type = None
        self.repository.bind_node_id = None
        self.repository.config = {
            "server_address": "10.0.0.20",
            "share_path": "/snapshots",
            "kopia_password": "password",
        }
        self.repository.save()
        proxy = self._node("target-nas-proxy", role=Node.Role.PROXY)
        target_nas = SourceResource.objects.create(
            organization=self.org,
            name="restore target NAS",
            resource_type=ResourceType.NAS,
            bound_node=proxy,
            availability="online",
            config={"server": "10.0.0.30", "share": "restore"},
        )
        validate.side_effect = lambda **kwargs: {
            assignment[0].key: TargetValidationResult(status="success")
            for assignment in kwargs["assignments"]
        }

        response = self._post(
            self._payload(target_type="nas", target_ref_id=target_nas.id)
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        assignment = validate.call_args.kwargs["assignments"][0]
        self.assertEqual(assignment[1].node.id, proxy.id)
        self.assertEqual(assignment[3].node.id, proxy.id)

    @patch("apps.protection.services.backup_target_validation._execute_agent_task")
    def test_proxy_repository_is_probed_from_restore_target(self, execute):
        proxy = self._node("repository-proxy", role=Node.Role.PROXY)
        proxy.ip_address = "10.0.0.65"
        proxy.save(update_fields=["ip_address", "updated_at"])
        self.repository.repo_type = Repository.Type.PROXY_FS
        self.repository.s3_bucket = ""
        self.repository.bind_node_type = Repository.BindNodeType.PROXY
        self.repository.bind_node_id = proxy.id
        self.repository.config = {
            "proxy_node_dir": "/srv/hfl-repository",
            "kopia_password": "password",
            "proxy_repository_server_host": "10.0.0.65",
        }
        self.repository.save()
        self.directory.repository_locator = {
            "version": 1,
            "repository_id": self.repository.id,
            "repository_type": Repository.Type.PROXY_FS,
            "repository_subdir": "",
            "writer_node_id": self.source.id,
            "access_node_id": proxy.id,
        }
        self.directory.save(update_fields=["repository_locator", "updated_at"])

        def outcome(**kwargs):
            kind = kwargs["kind"]
            result = {}
            if kind == "repository.server.start":
                result = {
                    "server_url": "https://10.0.0.65:51515",
                    "server_cert_fingerprint": "sha256:fingerprint",
                }
            return type(
                "AgentOutcome",
                (),
                {
                    "ok": True,
                    "status": "success",
                    "message": "",
                    "result": result,
                    "timed_out": False,
                },
            )()

        execute.side_effect = outcome

        response = self._post(self._payload())

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["status"], "success")
        calls = execute.call_args_list
        start = next(
            call for call in calls if call.kwargs["kind"] == "repository.server.start"
        )
        probe = next(call for call in calls if call.kwargs["kind"] == "repo.status")
        stop = next(
            call for call in calls if call.kwargs["kind"] == "repository.server.stop"
        )
        self.assertEqual(start.kwargs["node_id"], proxy.id)
        self.assertFalse(start.kwargs["payload"]["repair_mount"])
        self.assertEqual(probe.kwargs["node_id"], self.target.id)
        self.assertEqual(probe.kwargs["payload"]["probe"], "restore_target_validation")
        self.assertTrue(probe.kwargs["payload"]["skip_ownership_check"])
        self.assertEqual(stop.kwargs["node_id"], proxy.id)
