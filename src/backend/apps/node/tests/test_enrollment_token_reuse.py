"""Enrollment token reuse and expiry."""

from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from apps.iam.models import Organization
from apps.node.api.serializers import NodeTokenCreateSerializer, NodeTokenSerializer
from apps.node.api.views.node import NodeViewSet
from apps.node.api.views.artifact_release import (
    AgentArtifact,
    AgentReleasesAuthView,
    AgentReleaseView,
    _load_release_token,
)
from apps.node.api.views.enrollment_helpers import token_usable_for_artifact_download
from apps.node.models import Node, NodeInstallationMode, NodeToken
from apps.node.models.base import NodeRole
from apps.node.services.internal.enrollment_auth import validate_node_credential


class EnrollmentTokenReuseTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="reuse-org", name="Reuse Org")
        self.token_row = NodeToken.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            token="reuse-token-abc",
            is_active=True,
        )
        self.factory = APIRequestFactory()

    def _heartbeat(
        self,
        *,
        name: str,
        token: str | None = None,
        installation_id: str = "",
        installation_mode: str = NodeInstallationMode.SYSTEM,
        host_fingerprint: str = "",
    ):
        request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "role": "agent",
                "name": name,
                "version": "1.0.0",
                "os_name": "linux",
                "installation_id": installation_id,
                "installation_mode": installation_mode,
                "host_fingerprint": host_fingerprint,
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=token or self.token_row.token,
        )
        return NodeViewSet.as_view({"post": "heartbeat"})(request)

    def test_same_token_registers_multiple_nodes(self):
        first = self._heartbeat(name="host-a")
        self.assertEqual(first.status_code, 200)
        second = self._heartbeat(name="host-b")
        self.assertEqual(second.status_code, 200)

        self.token_row.refresh_from_db()
        self.assertTrue(self.token_row.is_active)
        self.assertIsNotNone(self.token_row.used_at)
        self.assertEqual(Node.objects.filter(organization=self.org).count(), 2)

    def test_host_fingerprint_prevents_a_second_installation(self):
        fingerprint = "a" * 64
        first = self._heartbeat(
            name="host-a",
            installation_id="hfli_host_a",
            host_fingerprint=fingerprint,
        )
        self.assertEqual(first.status_code, 200)

        self.token_row.installation_mode = NodeInstallationMode.USER
        self.token_row.save(update_fields=["installation_mode"])
        second = self._heartbeat(
            name="host-a-user",
            installation_id="hfli_host_a_user",
            installation_mode=NodeInstallationMode.USER,
            host_fingerprint=fingerprint,
        )

        self.assertEqual(second.status_code, 409)
        self.assertIn("remove its console record", second.data["error"])
        self.assertEqual(Node.objects.filter(organization=self.org).count(), 1)

    def test_offline_host_must_be_uninstalled_before_changing_mode(self):
        fingerprint = "c" * 64
        first = self._heartbeat(
            name="host-a",
            installation_id="hfli_host_a",
            host_fingerprint=fingerprint,
        )
        self.assertEqual(first.status_code, 200)
        first_credential = first.data["node_credential"]
        node = Node.objects.get(organization=self.org)
        node.availability = Node.Availability.OFFLINE
        node.save(update_fields=["availability", "updated_at"])

        self.token_row.installation_mode = NodeInstallationMode.USER
        self.token_row.save(update_fields=["installation_mode"])
        replacement = self._heartbeat(
            name="host-a-user",
            installation_id="hfli_host_a_user",
            installation_mode=NodeInstallationMode.USER,
            host_fingerprint=fingerprint,
        )

        self.assertEqual(replacement.status_code, 409)
        self.assertEqual(Node.objects.filter(organization=self.org).count(), 1)
        node.refresh_from_db()
        self.assertEqual(node.installation_id, "hfli_host_a")
        self.assertEqual(node.installation_mode, NodeInstallationMode.SYSTEM)
        self.assertTrue(validate_node_credential(node, first_credential, touch=False))

    def test_database_rejects_duplicate_active_host_fingerprint(self):
        fingerprint = "b" * 64
        Node.objects.create(
            organization=self.org,
            name="host-a",
            role=NodeRole.AGENT,
            host_fingerprint=fingerprint,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Node.objects.create(
                organization=self.org,
                name="host-b",
                role=NodeRole.AGENT,
                host_fingerprint=fingerprint,
            )

    @mock.patch(
        "apps.node.api.views.node.sync_agent_source_host",
        side_effect=RuntimeError("sync failed"),
    )
    def test_source_host_sync_failure_rolls_back_registration(self, _mock_sync):
        with self.assertRaises(RuntimeError):
            self._heartbeat(name="host-sync-fails")
        self.assertEqual(Node.objects.filter(organization=self.org).count(), 0)
        self.token_row.refresh_from_db()
        self.assertIsNone(self.token_row.used_at)

    def test_expired_token_rejects_new_registration(self):
        self.token_row.expires_at = timezone.now() - timedelta(minutes=1)
        self.token_row.save(update_fields=["expires_at"])

        response = self._heartbeat(name="host-expired")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Node.objects.filter(organization=self.org).count(), 0)

    def test_used_current_token_does_not_bypass_expiry_for_artifact_download(self):
        self.token_row.is_active = False
        self.token_row.used_at = timezone.now()
        self.token_row.expires_at = timezone.now() - timedelta(minutes=1)
        self.token_row.save(update_fields=["is_active", "used_at", "expires_at"])

        self.assertFalse(
            token_usable_for_artifact_download(
                org=self.org,
                token=self.token_row.token,
                role=NodeRole.AGENT,
            )
        )

    def test_used_legacy_token_remains_available_during_migration(self):
        self.token_row.enrollment_mode = NodeToken.EnrollmentMode.LEGACY
        self.token_row.is_active = False
        self.token_row.used_at = timezone.now()
        self.token_row.expires_at = timezone.now() - timedelta(days=1)
        self.token_row.save(
            update_fields=["enrollment_mode", "is_active", "used_at", "expires_at"]
        )

        self.assertTrue(
            token_usable_for_artifact_download(
                org=self.org,
                token=self.token_row.token,
                role=NodeRole.AGENT,
            )
        )

    def test_create_serializer_sets_default_expiry(self):
        ser = NodeTokenCreateSerializer(data={"role": NodeRole.AGENT})
        self.assertTrue(ser.is_valid(), ser.errors)
        row = ser.save(organization=self.org)
        self.assertIsNotNone(row.expires_at)
        self.assertGreater(row.expires_at, timezone.now())
        self.assertEqual(row.enrollment_mode, NodeToken.EnrollmentMode.CURRENT)
        self.assertEqual(row.installation_mode, NodeInstallationMode.SYSTEM)

    def test_create_serializer_accepts_user_level_source_agent(self):
        ser = NodeTokenCreateSerializer(
            data={
                "role": NodeRole.AGENT,
                "installation_mode": NodeInstallationMode.USER,
            }
        )
        self.assertTrue(ser.is_valid(), ser.errors)

        row = ser.save(organization=self.org)

        self.assertEqual(row.installation_mode, NodeInstallationMode.USER)

    def test_create_serializer_rejects_user_level_infrastructure_role(self):
        for role in (NodeRole.PROXY, NodeRole.GATEWAY):
            with self.subTest(role=role):
                ser = NodeTokenCreateSerializer(
                    data={
                        "role": role,
                        "installation_mode": NodeInstallationMode.USER,
                    }
                )
                self.assertFalse(ser.is_valid())
                self.assertIn("installation_mode", ser.errors)

    def test_database_rejects_user_level_infrastructure_role(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            NodeToken.objects.create(
                organization=self.org,
                role=NodeRole.PROXY,
                installation_mode=NodeInstallationMode.USER,
            )

    def test_heartbeat_copies_installation_mode_from_token(self):
        self.token_row.installation_mode = NodeInstallationMode.USER
        self.token_row.save(update_fields=["installation_mode"])
        request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "role": "agent",
                "installation_mode": "user",
                "name": "user-host",
                "installation_id": "hfli_user_host",
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=self.token_row.token,
        )

        response = NodeViewSet.as_view({"post": "heartbeat"})(request)

        self.assertEqual(response.status_code, 200)
        node = Node.objects.get(organization=self.org)
        self.assertEqual(node.installation_mode, NodeInstallationMode.USER)

    def test_reenrollment_cannot_change_existing_installation_mode(self):
        node = Node.objects.create(
            organization=self.org,
            name="fixed-system-host",
            role=NodeRole.AGENT,
            installation_mode=NodeInstallationMode.SYSTEM,
            installation_id="hfli_fixed_mode",
        )
        self.token_row.installation_mode = NodeInstallationMode.USER
        self.token_row.save(update_fields=["installation_mode"])
        request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "role": "agent",
                "installation_mode": "user",
                "name": "fixed-system-host",
                "installation_id": node.installation_id,
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=self.token_row.token,
        )

        response = NodeViewSet.as_view({"post": "heartbeat"})(request)

        self.assertEqual(response.status_code, 409)
        node.refresh_from_db()
        self.assertEqual(node.installation_mode, NodeInstallationMode.SYSTEM)

    def test_existing_node_authentication_precedes_mode_conflict(self):
        node = Node.objects.create(
            organization=self.org,
            name="authenticated-mode-check",
            role=NodeRole.AGENT,
            installation_mode=NodeInstallationMode.SYSTEM,
        )
        request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "node_id": node.id,
                "role": NodeRole.AGENT,
                "installation_mode": NodeInstallationMode.USER,
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN="invalid-node-credential",
        )

        response = NodeViewSet.as_view({"post": "heartbeat"})(request)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error"], "invalid node credential")

    def test_create_serializer_rejects_unbounded_expiry(self):
        ser = NodeTokenCreateSerializer(
            data={"role": NodeRole.AGENT, "expires_at": None}
        )
        self.assertTrue(ser.is_valid(), ser.errors)
        row = ser.save(organization=self.org)
        self.assertIsNotNone(row.expires_at)

    def test_create_serializer_cannot_issue_platform_gateway_token(self):
        ser = NodeTokenCreateSerializer(
            data={
                "role": NodeRole.GATEWAY,
                "gateway_scope": "platform",
            }
        )
        self.assertTrue(ser.is_valid(), ser.errors)

        row = ser.save(organization=self.org)

        self.assertEqual(row.gateway_scope, "")

    def test_update_serializer_cannot_promote_gateway_scope(self):
        self.token_row.role = NodeRole.GATEWAY
        self.token_row.save(update_fields=["role"])
        ser = NodeTokenSerializer(
            self.token_row,
            data={"gateway_scope": "platform"},
            partial=True,
        )
        self.assertTrue(ser.is_valid(), ser.errors)

        row = ser.save()

        self.assertEqual(row.gateway_scope, "")

    def test_update_serializer_cannot_extend_expiry_or_change_role(self):
        original_expiry = self.token_row.expires_at
        ser = NodeTokenSerializer(
            self.token_row,
            data={
                "role": NodeRole.GATEWAY,
                "expires_at": timezone.now() + timedelta(days=30),
            },
            partial=True,
        )
        self.assertTrue(ser.is_valid(), ser.errors)

        row = ser.save()

        self.assertEqual(row.role, NodeRole.AGENT)
        self.assertEqual(row.expires_at, original_expiry)

    @override_settings(HFL_INSECURE_TLS=False)
    def test_token_response_exposes_strict_tls_policy(self):
        data = NodeTokenSerializer(self.token_row).data

        self.assertTrue(data["tls_verify"])

    def test_token_secret_is_only_exposed_during_creation(self):
        hidden = NodeTokenSerializer(self.token_row).data
        created = NodeTokenSerializer(
            self.token_row,
            context={"include_token": True},
        ).data

        self.assertEqual(hidden["token"], "")
        self.assertEqual(created["token"], self.token_row.token)

    def test_signed_release_url_does_not_embed_enrollment_secret(self):
        request = self.factory.get(
            "/api/v1/node/enrollment/agent/release",
            {
                "org": self.org.key,
                "role": NodeRole.AGENT,
                "token": self.token_row.token,
                "platform": "linux",
                "arch": "amd64",
                "api_base": "https://console.example",
            },
        )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = AgentArtifact(
                platform="linux",
                arch="amd64",
                version="1.0.0",
                filename="agent.tar.gz",
            )
            artifact_path = root / artifact.version / artifact.filename
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_bytes(b"agent bundle")
            with (
                mock.patch(
                    "apps.node.api.views.artifact_release._get_agent_artifact",
                    return_value=artifact,
                ),
                mock.patch(
                    "apps.node.api.views.artifact_release.agent_releases_root",
                    return_value=root,
                ),
            ):
                response = AgentReleaseView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        signed = parse_qs(urlparse(response.data["download_url"]).query)["t"][0]
        payload = _load_release_token(signed, max_age=600)
        self.assertIsNotNone(payload)
        self.assertNotIn("enroll", payload)
        self.assertEqual(payload["token_id"], self.token_row.id)

        auth_request = self.factory.get(
            "/api/v1/node/enrollment/agent/releases-auth",
            {"t": signed},
            HTTP_X_ORIGINAL_URI=payload["p"],
        )
        authorized = AgentReleasesAuthView.as_view()(auth_request)
        self.assertEqual(authorized.status_code, 204)

        self.token_row.is_active = False
        self.token_row.save(update_fields=["is_active"])
        denied = AgentReleasesAuthView.as_view()(auth_request)
        self.assertEqual(denied.status_code, 401)

    def test_registered_node_credential_can_issue_and_auth_release_download(self):
        """Remote upgrade uses durable NodeCredential, not enrollment token."""
        from apps.node.api.views.enrollment_helpers import (
            token_usable_for_artifact_download,
            token_usable_for_bootstrap,
        )
        from apps.node.models import NodeCredential

        node = Node.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            name="registered-host",
            installation_id="registered-host",
        )
        credential_secret = "hfln_registered-node-credential"
        credential = NodeCredential(
            organization=self.org,
            node=node,
            role=NodeRole.AGENT,
            installation_id="registered-host",
        )
        credential.set_secret(credential_secret)
        credential.save()

        self.assertTrue(
            token_usable_for_artifact_download(
                org=self.org,
                token=credential_secret,
                role=NodeRole.AGENT,
            )
        )
        self.assertFalse(
            token_usable_for_bootstrap(
                org=self.org,
                token=credential_secret,
                role=NodeRole.AGENT,
            )
        )

        request = self.factory.get(
            "/api/v1/node/enrollment/agent/release",
            {
                "org": self.org.key,
                "role": NodeRole.AGENT,
                "token": credential_secret,
                "platform": "linux",
                "arch": "amd64",
                "api_base": "https://console.example",
            },
        )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = AgentArtifact(
                platform="linux",
                arch="amd64",
                version="1.0.0",
                filename="agent.tar.gz",
            )
            artifact_path = root / artifact.version / artifact.filename
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_bytes(b"agent bundle")
            with (
                mock.patch(
                    "apps.node.api.views.artifact_release._get_agent_artifact",
                    return_value=artifact,
                ),
                mock.patch(
                    "apps.node.api.views.artifact_release.agent_releases_root",
                    return_value=root,
                ),
            ):
                response = AgentReleaseView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        signed = parse_qs(urlparse(response.data["download_url"]).query)["t"][0]
        payload = _load_release_token(signed, max_age=600)
        self.assertIsNotNone(payload)
        self.assertNotIn("enroll", payload)
        self.assertNotIn("token_id", payload)
        self.assertEqual(payload["credential_id"], credential.id)
        self.assertEqual(payload["node_id"], node.id)

        auth_request = self.factory.get(
            "/api/v1/node/enrollment/agent/releases-auth",
            {"t": signed},
            HTTP_X_ORIGINAL_URI=payload["p"],
        )
        authorized = AgentReleasesAuthView.as_view()(auth_request)
        self.assertEqual(authorized.status_code, 204)

        credential.is_active = False
        credential.save(update_fields=["is_active"])
        denied = AgentReleasesAuthView.as_view()(auth_request)
        self.assertEqual(denied.status_code, 401)

    def test_wrong_role_node_credential_rejected_for_release(self):
        from apps.node.models import NodeCredential

        node = Node.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            name="wrong-role-host",
            installation_id="wrong-role-host",
        )
        credential_secret = "hfln_wrong-role-credential"
        credential = NodeCredential(
            organization=self.org,
            node=node,
            role=NodeRole.AGENT,
            installation_id="wrong-role-host",
        )
        credential.set_secret(credential_secret)
        credential.save()

        request = self.factory.get(
            "/api/v1/node/enrollment/agent/release",
            {
                "org": self.org.key,
                "role": NodeRole.GATEWAY,
                "token": credential_secret,
                "platform": "linux",
                "arch": "amd64",
            },
        )
        response = AgentReleaseView.as_view()(request)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error"], "invalid enrollment token")
