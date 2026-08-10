from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.iam.models import Membership, Organization
from apps.node.models import Node, NodeTask
from apps.protection.services.backup_target_validation import (
    _ActivityRegistry,
    _AgentOutcome,
    _execute_agent_task,
    validate_backup_targets,
)
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.storage.repositories.models import Repository


class BackupTargetValidationApiTests(TransactionTestCase):
    def setUp(self):
        self.client = APIClient()
        user = get_user_model().objects.create_user(
            username="target-validation@test.local",
            email="target-validation@test.local",
            password="test-pass",
        )
        self.org = Organization.objects.create(
            key="target-validation-org",
            name="Target Validation Org",
        )
        Membership.objects.create(
            user=user,
            organization=self.org,
            role=Membership.Role.ADMIN,
        )
        self.agent = Node.objects.create(
            organization=self.org,
            name="validation-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.20",
        )
        self.s3_repository = Repository.objects.create(
            organization_id=self.org.id,
            name="validation-s3",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="validation-bucket",
            config={
                "endpoint": "s3.example.test:9000",
                "external_endpoint": "s3.example.test:9000",
                "internal_endpoint": "s3.internal.test:9000",
                "region": "test-1",
                "prefix": "kopia",
                "access_key_id": "test-access-key",
                "secret_access_key": "test-secret-key",
                "kopia_password": "test-kopia-password",
                "use_tls": False,
            },
        )
        self.client.force_authenticate(user=user)

    def _headers(self):
        return {"HTTP_X_ORG_KEY": self.org.key}

    def _source(self, *, key: str = "source-row", agent: Node | None = None):
        return {
            "key": key,
            "source_type": "agent",
            "source_ref_id": (agent or self.agent).id,
            "repository_id": self.s3_repository.id,
            "repository_endpoint_type": "external",
        }

    @mock.patch(
        "apps.protection.api.views.backup_target_validation.validate_backup_targets"
    )
    def test_post_validates_request_and_returns_service_results(self, validate):
        validate.return_value = {
            "status": "success",
            "results": [
                {
                    "key": "source-row",
                    "status": "success",
                    "code": None,
                    "message": "",
                }
            ],
        }

        response = self.client.post(
            "/api/v1/protection/backup-target-validations/",
            {"sources": [self._source()]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "success")
        validate.assert_called_once()
        self.assertEqual(validate.call_args.kwargs["organization_id"], self.org.id)

    def test_post_rejects_duplicate_source_row_keys(self):
        response = self.client.post(
            "/api/v1/protection/backup-target-validations/",
            {"sources": [self._source(), self._source()]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @mock.patch(
        "apps.protection.services.backup_target_validation._execute_agent_task"
    )
    def test_s3_routes_are_deduplicated_and_use_selected_endpoint(self, execute):
        execute.return_value = _AgentOutcome(
            ok=True,
            status="success",
            message="",
            result={},
        )
        sources = [
            self._source(key="row-one"),
            self._source(key="row-two"),
        ]

        result = validate_backup_targets(
            organization_id=self.org.id,
            sources=sources,
        )

        self.assertEqual(result["status"], "success", result)
        self.assertEqual([item["key"] for item in result["results"]], ["row-one", "row-two"])
        execute.assert_called_once()
        call = execute.call_args.kwargs
        self.assertEqual(call["kind"], "repo.status")
        self.assertEqual(call["payload"]["repository"]["endpoint"], "s3.example.test:9000")
        self.assertTrue(call["payload"]["health_only"])
        self.assertNotIn("backup_config_dir_id", call["payload"])

    @mock.patch(
        "apps.protection.services.backup_target_validation._execute_agent_task"
    )
    def test_s3_clock_skew_reports_actionable_error_before_generic_status(self, execute):
        clock_skew_messages = (
            "The difference between the request time and the server's time is too large.",
            "RequestTimeTooSkewed",
            "request time is too skewed",
        )
        for diagnostic in clock_skew_messages:
            with self.subTest(diagnostic=diagnostic):
                execute.return_value = _AgentOutcome(
                    ok=False,
                    status="failed",
                    message=(
                        "open repository: repository is not connected. "
                        "See https://kopia.io/docs/repositories/"
                    ),
                    result={
                        "repository_connect": {
                            "stderr": f"{diagnostic} test-secret-key",
                        },
                        "repository_status": {
                            "stderr": "open repository: repository is not connected",
                        },
                    },
                )

                result = validate_backup_targets(
                    organization_id=self.org.id,
                    sources=[self._source()],
                )

                row = result["results"][0]
                self.assertEqual(row["code"], "S3_CLOCK_SKEW")
                self.assertIn("source host clock", row["message"].lower())
                self.assertIn("trusted NTP", row["message"])
                self.assertEqual(row["details"]["stage"], "repository_connect")
                self.assertEqual(
                    row["details"]["remediation"],
                    "synchronize_source_time",
                )
                self.assertNotIn("test-secret-key", json.dumps(row))
                self.assertNotIn("repository is not connected", row["message"])

    @mock.patch(
        "apps.protection.services.backup_target_validation._execute_agent_task"
    )
    def test_s3_non_clock_failure_keeps_existing_connection_error(self, execute):
        execute.return_value = _AgentOutcome(
            ok=False,
            status="failed",
            message="error connecting to repository: access denied",
            result={
                "repository_connect": {
                    "stderr": "error connecting to repository: access denied",
                }
            },
        )

        result = validate_backup_targets(
            organization_id=self.org.id,
            sources=[self._source()],
        )

        row = result["results"][0]
        self.assertEqual(row["code"], "S3_CONNECTION_FAILED")
        self.assertEqual(row["message"], "error connecting to repository: access denied")

    @mock.patch(
        "apps.protection.services.backup_target_validation._execute_agent_task"
    )
    def test_direct_nas_only_tests_isolated_mount_and_unmounts(self, execute):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="direct-nas",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            config={
                "server_address": "10.0.0.30",
                "share_path": "/backup",
                "kopia_password": "direct-kopia-password",
            },
        )
        execute.return_value = _AgentOutcome(
            ok=True,
            status="success",
            message="",
            result={},
        )

        result = validate_backup_targets(
            organization_id=self.org.id,
            sources=[
                {
                    "key": "direct-row",
                    "source_type": "agent",
                    "source_ref_id": self.agent.id,
                    "repository_id": repository.id,
                    "repository_endpoint_type": "external",
                }
            ],
        )

        self.assertEqual(result["status"], "success", result)
        self.assertEqual(
            [call.kwargs["kind"] for call in execute.call_args_list],
            ["nas.test", "nas.unmount"],
        )
        test_payload = execute.call_args_list[0].kwargs["payload"]
        self.assertTrue(test_payload["cleanup_after_test"])
        self.assertIn("/mounts/validations/", test_payload["mount_point"])
        self.assertNotIn("repository", test_payload)

    @mock.patch(
        "apps.protection.services.backup_target_validation._execute_agent_task"
    )
    def test_direct_nas_cleanup_failure_fails_the_row(self, execute):
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="direct-nas-cleanup-failure",
            repo_type=Repository.Type.NAS,
            nas_protocol=Repository.NasProtocol.NFS,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            config={
                "server_address": "10.0.0.31",
                "share_path": "/backup",
                "kopia_password": "direct-kopia-password",
            },
        )
        execute.side_effect = [
            _AgentOutcome(ok=True, status="success", message="", result={}),
            _AgentOutcome(
                ok=False,
                status="failed",
                message="device is busy",
                result={},
            ),
        ]

        result = validate_backup_targets(
            organization_id=self.org.id,
            sources=[
                {
                    "key": "direct-row",
                    "source_type": "agent",
                    "source_ref_id": self.agent.id,
                    "repository_id": repository.id,
                    "repository_endpoint_type": "external",
                }
            ],
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["results"][0]["code"], "CLEANUP_FAILED")
        self.assertIn("device is busy", result["results"][0]["message"])

    @mock.patch(
        "apps.protection.services.backup_target_validation._execute_agent_task"
    )
    def test_same_proxy_repository_is_probed_directly(self, execute):
        proxy = Node.objects.create(
            organization=self.org,
            name="shared-source-repository-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.41",
        )
        source = SourceResource.objects.create(
            organization=self.org,
            name="same-proxy-nas-source",
            resource_type=ResourceType.NAS,
            bound_node=proxy,
            availability="online",
            config={
                "protocol": "nfs",
                "server": "10.0.0.50",
                "export_path": "/source",
            },
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="same-proxy-fs",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={
                "proxy_node_dir": "/srv/backups",
                "kopia_password": "proxy-kopia-password",
            },
        )
        execute.return_value = _AgentOutcome(
            ok=True,
            status="success",
            message="",
            result={},
        )

        result = validate_backup_targets(
            organization_id=self.org.id,
            sources=[
                {
                    "key": "same-proxy-row",
                    "source_type": "nas",
                    "source_ref_id": source.id,
                    "repository_id": repository.id,
                    "repository_endpoint_type": "external",
                }
            ],
        )

        self.assertEqual(result["status"], "success", result)
        execute.assert_called_once()
        call = execute.call_args.kwargs
        self.assertEqual(call["node_id"], proxy.id)
        self.assertEqual(call["kind"], "repo.status")
        self.assertTrue(call["payload"]["health_only"])

    @mock.patch(
        "apps.protection.services.backup_target_validation._execute_agent_task"
    )
    def test_cross_node_proxy_repository_reuses_server_and_stops_it(self, execute):
        proxy = Node.objects.create(
            organization=self.org,
            name="repository-proxy",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.40",
        )
        agent_two = Node.objects.create(
            organization=self.org,
            name="validation-agent-two",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            ip_address="10.0.0.21",
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="proxy-fs",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={
                "proxy_node_dir": "/srv/backups",
                "kopia_password": "proxy-kopia-password",
            },
        )

        def outcome(**kwargs):
            if kwargs["kind"] == "repository.server.start":
                return _AgentOutcome(
                    ok=True,
                    status="success",
                    message="",
                    result={
                        "server_url": "https://10.0.0.40:51515",
                        "server_cert_fingerprint": "ABC123",
                    },
                )
            return _AgentOutcome(
                ok=True,
                status="success",
                message="",
                result={},
            )

        execute.side_effect = outcome
        sources = [
            {
                "key": "agent-one",
                "source_type": "agent",
                "source_ref_id": self.agent.id,
                "repository_id": repository.id,
                "repository_endpoint_type": "external",
            },
            {
                "key": "agent-two",
                "source_type": "agent",
                "source_ref_id": agent_two.id,
                "repository_id": repository.id,
                "repository_endpoint_type": "external",
            },
        ]

        result = validate_backup_targets(
            organization_id=self.org.id,
            sources=sources,
        )

        self.assertEqual(result["status"], "success", result)
        kinds = [call.kwargs["kind"] for call in execute.call_args_list]
        self.assertEqual(kinds.count("repository.server.start"), 1)
        self.assertEqual(kinds.count("repository.server.stop"), 1)
        self.assertEqual(kinds.count("repo.status"), 2)
        start_call = next(
            call
            for call in execute.call_args_list
            if call.kwargs["kind"] == "repository.server.start"
        )
        self.assertEqual(start_call.kwargs["payload"]["public_host"], "10.0.0.40")
        self.assertEqual(
            start_call.kwargs["payload"]["public_host_source"],
            "node.ip_address",
        )
        probe_calls = [
            call
            for call in execute.call_args_list
            if call.kwargs["kind"] == "repo.status"
        ]
        self.assertTrue(
            all(call.kwargs["payload"]["health_only"] for call in probe_calls)
        )

    @mock.patch(
        "apps.protection.services.backup_target_validation._execute_agent_task"
    )
    def test_cross_node_proxy_repository_reports_actionable_failure_codes(self, execute):
        proxy = Node.objects.create(
            organization=self.org,
            name="repository-proxy-errors",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            ip_address="10.0.0.45",
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="proxy-fs-errors",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={
                "proxy_node_dir": "/srv/backups",
                "kopia_password": "proxy-kopia-password",
            },
        )
        source = {
            "key": "proxy-error-row",
            "source_type": "agent",
            "source_ref_id": self.agent.id,
            "repository_id": repository.id,
            "repository_endpoint_type": "external",
        }

        cases = (
            (
                "no available Repository Server port in TCP range 51515-52014",
                None,
                "PROXY_REPOSITORY_SERVER_PORT_EXHAUSTED",
                "server_start",
            ),
            (
                "kopia server failed to start",
                None,
                "PROXY_REPOSITORY_SERVER_START_FAILED",
                "server_start",
            ),
            (
                None,
                "dial tcp 10.0.0.45:51515: i/o timeout",
                "PROXY_REPOSITORY_SERVER_UNREACHABLE",
                "source_probe",
            ),
            (
                None,
                "TLS certificate fingerprint mismatch",
                "PROXY_REPOSITORY_SERVER_CONNECTION_FAILED",
                "source_probe",
            ),
        )
        for start_message, probe_message, expected_code, expected_stage in cases:
            with self.subTest(code=expected_code):
                execute.reset_mock()

                def outcome(**kwargs):
                    kind = kwargs["kind"]
                    if kind == "repository.server.start" and start_message:
                        return _AgentOutcome(
                            ok=False,
                            status="failed",
                            message=start_message,
                            result={},
                        )
                    if kind == "repository.server.start":
                        return _AgentOutcome(
                            ok=True,
                            status="success",
                            message="",
                            result={
                                "server_url": "https://10.0.0.45:51515",
                                "server_cert_fingerprint": "ABC123",
                            },
                        )
                    if kind == "repo.status":
                        return _AgentOutcome(
                            ok=False,
                            status="failed",
                            message=str(probe_message or ""),
                            result={},
                        )
                    return _AgentOutcome(
                        ok=True,
                        status="success",
                        message="",
                        result={},
                    )

                execute.side_effect = outcome
                result = validate_backup_targets(
                    organization_id=self.org.id,
                    sources=[source],
                )

                row = result["results"][0]
                self.assertEqual(row["code"], expected_code, row)
                self.assertEqual(row["details"]["stage"], expected_stage)
                self.assertEqual(row["details"]["proxy_address"], "10.0.0.45")
                self.assertEqual(row["details"]["port_range"], "51515-52014")
                self.assertNotIn("proxy-kopia-password", json.dumps(row))

    @mock.patch(
        "apps.protection.services.backup_target_validation._execute_agent_task"
    )
    def test_cross_node_proxy_repository_reports_missing_address_without_dispatch(self, execute):
        proxy = Node.objects.create(
            organization=self.org,
            name="repository-proxy-no-address",
            role=Node.Role.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="proxy-fs-no-address",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=proxy.id,
            config={"proxy_node_dir": "/srv/backups"},
        )

        result = validate_backup_targets(
            organization_id=self.org.id,
            sources=[
                {
                    "key": "missing-address-row",
                    "source_type": "agent",
                    "source_ref_id": self.agent.id,
                    "repository_id": repository.id,
                    "repository_endpoint_type": "external",
                }
            ],
        )

        row = result["results"][0]
        self.assertEqual(row["code"], "PROXY_REPOSITORY_SERVER_ADDRESS_MISSING")
        self.assertEqual(row["details"]["stage"], "address_resolution")
        execute.assert_not_called()

    @mock.patch(
        "apps.protection.services.backup_target_validation._execute_agent_task"
    )
    def test_offline_source_returns_row_failure_without_dispatch(self, execute):
        self.agent.availability = Node.Availability.OFFLINE
        self.agent.save(update_fields=["availability"])

        result = validate_backup_targets(
            organization_id=self.org.id,
            sources=[self._source()],
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["results"][0]["code"], "SOURCE_NODE_OFFLINE")
        execute.assert_not_called()

    @mock.patch(
        "apps.protection.services.backup_target_validation._execute_agent_task"
    )
    def test_cross_organization_sources_and_repositories_are_rejected(self, execute):
        other_org = Organization.objects.create(
            key="other-target-validation-org",
            name="Other Target Validation Org",
        )
        other_agent = Node.objects.create(
            organization=other_org,
            name="other-validation-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )
        other_repository = Repository.objects.create(
            organization_id=other_org.id,
            name="other-validation-s3",
            repo_type=Repository.Type.S3,
            status=Repository.Status.CREATED,
            health=Repository.Health.ONLINE,
            s3_platform=Repository.S3Platform.CUSTOM,
            s3_bucket="other-validation-bucket",
            config={
                "endpoint": "other-s3.example.test:9000",
                "access_key_id": "other-access-key",
                "secret_access_key": "other-secret-key",
                "kopia_password": "other-kopia-password",
            },
        )

        result = validate_backup_targets(
            organization_id=self.org.id,
            sources=[
                {
                    **self._source(key="foreign-repository"),
                    "repository_id": other_repository.id,
                },
                {
                    **self._source(key="foreign-source"),
                    "source_ref_id": other_agent.id,
                },
            ],
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            [item["code"] for item in result["results"]],
            ["REPOSITORY_NOT_FOUND", "SOURCE_NOT_FOUND"],
        )
        execute.assert_not_called()

    @mock.patch(
        "apps.protection.services.backup_target_validation.cancel_agent_task"
    )
    @mock.patch(
        "apps.protection.services.backup_target_validation.wait_for_agent_task"
    )
    @mock.patch(
        "apps.protection.services.backup_target_validation.run_agent_task_async"
    )
    def test_wait_failure_cancels_task_and_persists_no_plaintext_secret(
        self,
        run_async,
        wait_for_task,
        cancel_task,
    ):
        run_async.return_value = SimpleNamespace(task_id="validation-task-id")
        wait_for_task.side_effect = RuntimeError("transport failed")
        secret = "validation-password-value"

        outcome = _execute_agent_task(
            organization_id=self.org.id,
            node_id=self.agent.id,
            kind="repo.status",
            payload={"repository": {"password": secret}},
            request_id="validation-request-id",
            registry=_ActivityRegistry(),
            deadline=time.monotonic() + 10,
            max_wait_seconds=5,
        )

        self.assertFalse(outcome.ok)
        self.assertNotIn("transport failed", outcome.message)
        cancel_task.assert_called_once_with(
            task_id="validation-task-id",
            reason="backup target validation operation failed",
        )
        persisted = run_async.call_args.kwargs["persisted_payload"]
        self.assertNotIn(secret, json.dumps(persisted))
        self.assertEqual(persisted["repository"]["password"], "******")

    @mock.patch(
        "apps.protection.services.backup_target_validation.cancel_agent_task"
    )
    @mock.patch(
        "apps.protection.services.backup_target_validation.wait_for_agent_task"
    )
    @mock.patch(
        "apps.protection.services.backup_target_validation.run_agent_task_async"
    )
    def test_timeout_cancels_task_and_scrubs_persisted_result(
        self,
        run_async,
        wait_for_task,
        cancel_task,
    ):
        secret = "result-password-value"
        node_task = SimpleNamespace(
            pk="validation-task-id",
            status=NodeTask.Status.RUNNING,
            result={"password": secret, "detail": f"password={secret}"},
            last_error=f"password={secret}",
        )
        run_async.return_value = SimpleNamespace(
            task_id="validation-task-id",
            task=node_task,
        )
        wait_for_task.return_value = SimpleNamespace(
            task=node_task,
            ok=False,
            timed_out=True,
        )

        with mock.patch.object(NodeTask.objects, "filter") as task_filter:
            outcome = _execute_agent_task(
                organization_id=self.org.id,
                node_id=self.agent.id,
                kind="repo.status",
                payload={"repository": {"password": secret}},
                request_id="validation-request-id",
                registry=_ActivityRegistry(),
                deadline=time.monotonic() + 10,
                max_wait_seconds=5,
            )

        self.assertTrue(outcome.timed_out)
        self.assertNotIn(secret, json.dumps(outcome.result))
        cancel_task.assert_called_once_with(
            task_id="validation-task-id",
            reason="backup target validation operation timed out",
        )
        update = task_filter.return_value.update.call_args.kwargs
        self.assertNotIn(secret, json.dumps(update))
        self.assertEqual(update["result"]["password"], "******")
