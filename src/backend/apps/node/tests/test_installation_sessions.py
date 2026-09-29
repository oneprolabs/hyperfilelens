"""Resumable installation sessions and per-node credential tests."""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from apps.iam.models import Organization
from apps.node.api.views.installation_session import InstallationSessionView
from apps.node.api.views.node import NodeViewSet
from apps.node.models import Node, NodeCredential, NodeInstallationSession, NodeToken
from apps.node.models.base import NodeRole
from apps.node.services.internal.agent_ws_auth import validate_agent_ws_credentials
from apps.node.services.internal import enrollment_auth


class InstallationSessionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="session-org", name="Session Org")
        self.token = NodeToken.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            token="installation-token",
            expires_at=timezone.now() + timedelta(hours=24),
        )
        self.factory = APIRequestFactory()

    def _open_session(self, installation_id: str):
        request = self.factory.post(
            "/api/v1/node/enrollment/session",
            {"role": NodeRole.AGENT, "installation_id": installation_id},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=self.token.token,
        )
        return InstallationSessionView.as_view()(request)

    def _register(
        self,
        installation_id: str,
        session_secret: str,
        *,
        existing_node_credential: str = "",
    ):
        request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "role": NodeRole.AGENT,
                "name": installation_id,
                "version": "1.0.0",
                "os_name": "linux",
                "installation_id": installation_id,
                "existing_node_credential": existing_node_credential,
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=session_secret,
        )
        return NodeViewSet.as_view({"post": "heartbeat"})(request)

    def test_session_registration_issues_node_credential(self):
        opened = self._open_session("host-a")
        self.assertEqual(opened.status_code, 201)
        session_secret = opened.data["installation_session"]

        registered = self._register("host-a", session_secret)
        self.assertEqual(registered.status_code, 200)
        self.assertTrue(registered.data["node_credential"].startswith("hfln_"))

        node = Node.objects.get(installation_id="host-a")
        self.assertTrue(
            validate_agent_ws_credentials(node.id, registered.data["node_credential"])
        )
        self.assertTrue(NodeCredential.objects.filter(node=node).exists())
        self.assertEqual(
            NodeInstallationSession.objects.get(installation_id="host-a").status,
            NodeInstallationSession.Status.COMPLETED,
        )

    def test_session_rejects_a_different_installation_identity(self):
        opened = self._open_session("host-a")

        rejected = self._register(
            "host-b",
            opened.data["installation_session"],
        )

        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(
            rejected.data["error"],
            "installation session does not match this installation identity",
        )
        self.assertFalse(Node.objects.filter(installation_id="host-b").exists())

    def test_started_session_can_finish_after_link_is_revoked(self):
        opened = self._open_session("host-a")
        self.token.soft_delete()

        registered = self._register("host-a", opened.data["installation_session"])

        self.assertEqual(registered.status_code, 200)
        self.assertTrue(registered.data["node_credential"].startswith("hfln_"))
        self.assertEqual(
            NodeInstallationSession.objects.get(installation_id="host-a").status,
            NodeInstallationSession.Status.COMPLETED,
        )

    def test_same_link_opens_sessions_for_more_than_ten_hosts(self):
        for index in range(12):
            installation_id = f"host-{index}"
            opened = self._open_session(installation_id)
            self.assertEqual(opened.status_code, 201)
        self.assertEqual(
            NodeInstallationSession.objects.filter(
                enrollment_token=self.token,
                status=NodeInstallationSession.Status.ACTIVE,
            ).count(),
            12,
        )

    def test_session_rejects_oversized_installation_identity(self):
        rejected = self._open_session("x" * 129)

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(
            rejected.data["error"],
            "installation_id must not exceed 128 characters",
        )

    def test_idempotent_session_rotation_reuses_the_active_session(self):
        first = self._open_session("host-a")
        second = self._open_session("host-a")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(
            first.data["installation_session"],
            second.data["installation_session"],
        )
        self.assertEqual(
            NodeInstallationSession.objects.filter(
                installation_id="host-a",
                status=NodeInstallationSession.Status.ACTIVE,
            ).count(),
            1,
        )

    def test_existing_installation_reenrolls_without_creating_another_node(self):
        node = Node.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            name="host-a",
            installation_id="host-a",
        )
        NodeCredential.objects.create(
            organization=self.org,
            node=node,
            role=NodeRole.AGENT,
            installation_id="host-a",
            secret_prefix="old-prefix",
            secret_hash="a" * 64,
        )
        opened = self._open_session("host-a")

        registered = self._register("host-a", opened.data["installation_session"])

        self.assertEqual(registered.status_code, 200)
        self.assertEqual(registered.data["node_id"], node.id)
        self.assertTrue(registered.data["node_credential"].startswith("hfln_"))
        self.assertEqual(Node.objects.filter(installation_id="host-a").count(), 1)

    def test_existing_valid_credential_is_reused_during_reenrollment(self):
        node = Node.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            name="host-a",
            installation_id="host-a",
        )
        credential = "hfln_existing-credential"
        credential_row = NodeCredential(
            organization=self.org,
            node=node,
            role=NodeRole.AGENT,
            installation_id="host-a",
        )
        credential_row.set_secret(credential)
        credential_row.save()
        original_hash = credential_row.secret_hash
        opened = self._open_session("host-a")

        registered = self._register(
            "host-a",
            opened.data["installation_session"],
            existing_node_credential=credential,
        )

        self.assertEqual(registered.status_code, 200)
        self.assertEqual(registered.data["node_id"], node.id)
        self.assertTrue(registered.data["credential_reused"])
        self.assertNotIn("node_credential", registered.data)
        credential_row.refresh_from_db()
        self.assertEqual(credential_row.secret_hash, original_hash)
        self.assertTrue(validate_agent_ws_credentials(node.id, credential))

    def test_new_installation_identity_creates_a_new_console_record(self):
        first_session = self._open_session("installation-a")
        first = self._register(
            "installation-a",
            first_session.data["installation_session"],
        )
        second_session = self._open_session("installation-b")
        second = self._register(
            "installation-b",
            second_session.data["installation_session"],
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(first.data["node_id"], second.data["node_id"])
        self.assertEqual(Node.objects.count(), 2)

    def test_expired_enrollment_token_does_not_expire_node_credential(self):
        opened = self._open_session("host-a")
        registered = self._register("host-a", opened.data["installation_session"])
        node = Node.objects.get(installation_id="host-a")
        credential = registered.data["node_credential"]

        self.token.expires_at = timezone.now() - timedelta(minutes=1)
        self.token.save(update_fields=["expires_at"])

        self.assertTrue(validate_agent_ws_credentials(node.id, credential))

    def test_expired_enrollment_token_cannot_open_another_session(self):
        opened = self._open_session("host-a")
        registered = self._register("host-a", opened.data["installation_session"])
        self.assertEqual(registered.status_code, 200)
        self.token.expires_at = timezone.now() - timedelta(minutes=1)
        self.token.save(update_fields=["expires_at"])

        rejected = self._open_session("host-a")

        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(rejected.data["error"], "invalid or expired enrollment token")

    def test_revoked_enrollment_token_cannot_open_another_session(self):
        opened = self._open_session("host-a")
        registered = self._register("host-a", opened.data["installation_session"])
        self.assertEqual(registered.status_code, 200)
        self.token.is_active = False
        self.token.save(update_fields=["is_active"])

        rejected = self._open_session("host-a")

        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(rejected.data["error"], "invalid or expired enrollment token")

    def test_failed_installation_releases_its_session(self):
        opened = self._open_session("host-a")
        session_secret = opened.data["installation_session"]
        session_id = NodeInstallationSession.objects.get(installation_id="host-a").pk
        request = self.factory.delete(
            "/api/v1/node/enrollment/session",
            {"role": NodeRole.AGENT, "installation_id": "host-a"},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=session_secret,
        )

        with mock.patch.object(enrollment_auth, "release_session_slot") as release_slot:
            with self.captureOnCommitCallbacks(execute=True):
                released = InstallationSessionView.as_view()(request)
            release_slot.assert_called_once_with(self.org.key, session_id)

        self.assertEqual(released.status_code, 204)
        self.assertEqual(
            NodeInstallationSession.objects.get(installation_id="host-a").status,
            NodeInstallationSession.Status.RELEASED,
        )
        self.assertEqual(self._open_session("host-b").status_code, 201)

    def test_expired_session_can_release_its_slot_after_identity_check(self):
        opened = self._open_session("host-a")
        secret = opened.data["installation_session"]
        session = NodeInstallationSession.objects.get(installation_id="host-a")
        session.idle_expires_at = timezone.now() - timedelta(seconds=1)
        session.save(update_fields=["idle_expires_at"])

        request = self.factory.delete(
            "/api/v1/node/enrollment/session",
            {"role": NodeRole.AGENT, "installation_id": "host-a"},
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=secret,
        )
        with mock.patch.object(enrollment_auth, "release_session_slot") as release_slot:
            with self.captureOnCommitCallbacks(execute=True):
                response = InstallationSessionView.as_view()(request)
            self.assertEqual(response.status_code, 204)
            release_slot.assert_called_once_with(self.org.key, session.pk)
        session.refresh_from_db()
        self.assertEqual(session.status, NodeInstallationSession.Status.RELEASED)

    def test_session_slot_release_is_idempotent_but_rejects_wrong_identity(self):
        opened = self._open_session("host-a")
        secret = opened.data["installation_session"]
        session = NodeInstallationSession.objects.get(installation_id="host-a")

        def request(identity: str):
            return self.factory.delete(
                "/api/v1/node/enrollment/session",
                {"role": NodeRole.AGENT, "installation_id": identity},
                format="json",
                HTTP_X_ORG_KEY=self.org.key,
                HTTP_X_NODE_TOKEN=secret,
            )

        with mock.patch.object(enrollment_auth, "release_session_slot") as release_slot:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(
                    InstallationSessionView.as_view()(request("wrong-host")).status_code,
                    404,
                )
            release_slot.assert_not_called()
            for _ in range(2):
                with self.captureOnCommitCallbacks(execute=True):
                    response = InstallationSessionView.as_view()(request("host-a"))
                self.assertEqual(response.status_code, 204)
            self.assertEqual(
                release_slot.call_args_list,
                [
                    mock.call(self.org.key, session.pk),
                    mock.call(self.org.key, session.pk),
                ],
            )

    def test_completed_session_slot_releases_only_after_commit(self):
        opened = self._open_session("host-a")
        session = NodeInstallationSession.objects.get(installation_id="host-a")
        with mock.patch.object(enrollment_auth, "release_session_slot") as release_slot:
            with self.captureOnCommitCallbacks(execute=True):
                response = self._register("host-a", opened.data["installation_session"])
                self.assertEqual(response.status_code, 200)
                release_slot.assert_not_called()
            release_slot.assert_called_once_with(self.org.key, session.pk)

    def test_rolled_back_session_release_does_not_free_slot(self):
        opened = self._open_session("host-a")
        secret = opened.data["installation_session"]
        with mock.patch.object(enrollment_auth, "release_session_slot") as release_slot:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                with self.assertRaises(RuntimeError):
                    with transaction.atomic():
                        enrollment_auth.release_installation_session(
                            org=self.org,
                            secret=secret,
                            role=NodeRole.AGENT,
                            installation_id="host-a",
                        )
                        raise RuntimeError("rollback")
            self.assertEqual(callbacks, [])
            release_slot.assert_not_called()

    def test_legacy_credential_is_rotated_during_existing_node_heartbeat(self):
        legacy = NodeToken.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            token="legacy-token",
            enrollment_mode=NodeToken.EnrollmentMode.LEGACY,
            is_active=False,
            used_at=timezone.now(),
        )
        node = Node.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            name="legacy-host",
            installation_id="legacy-host-id",
        )
        request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "node_id": node.id,
                "role": NodeRole.AGENT,
                "name": node.name,
                "installation_id": node.installation_id,
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=legacy.token,
        )

        response = NodeViewSet.as_view({"post": "heartbeat"})(request)

        self.assertEqual(response.status_code, 200)
        credential = response.data["node_credential"]
        self.assertTrue(credential.startswith("hfln_"))
        self.assertTrue(validate_agent_ws_credentials(node.id, credential))
        self.assertFalse(validate_agent_ws_credentials(node.id, legacy.token))

        second = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "node_id": node.id,
                "role": NodeRole.AGENT,
                "name": node.name,
                "installation_id": node.installation_id,
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=legacy.token,
        )
        rejected = NodeViewSet.as_view({"post": "heartbeat"})(second)
        self.assertEqual(rejected.status_code, 401)

    def test_legacy_token_cannot_migrate_after_credential_exists_for_other_node(self):
        legacy = NodeToken.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            token="shared-legacy-token",
            enrollment_mode=NodeToken.EnrollmentMode.LEGACY,
            is_active=False,
            used_at=timezone.now(),
        )
        first = Node.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            name="legacy-first",
            installation_id="legacy-first-id",
        )
        migrated = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "node_id": first.id,
                "role": NodeRole.AGENT,
                "name": first.name,
                "installation_id": first.installation_id,
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=legacy.token,
        )
        self.assertEqual(
            NodeViewSet.as_view({"post": "heartbeat"})(migrated).status_code,
            200,
        )
        # A different node without a credential can still migrate once.
        second = Node.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            name="legacy-second",
            installation_id="legacy-second-id",
        )
        second_request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "node_id": second.id,
                "role": NodeRole.AGENT,
                "name": second.name,
                "installation_id": second.installation_id,
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=legacy.token,
        )
        second_response = NodeViewSet.as_view({"post": "heartbeat"})(second_request)
        self.assertEqual(second_response.status_code, 200)
        self.assertFalse(validate_agent_ws_credentials(first.id, legacy.token))
        self.assertFalse(validate_agent_ws_credentials(second.id, legacy.token))

    def test_legacy_heartbeat_backfills_stable_installation_identity(self):
        legacy = NodeToken.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            token="legacy-backfill-token",
            enrollment_mode=NodeToken.EnrollmentMode.LEGACY,
            is_active=False,
            used_at=timezone.now(),
        )
        node = Node.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            name="legacy-backfill-host",
        )
        request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "node_id": node.id,
                "role": NodeRole.AGENT,
                "name": node.name,
                "installation_id": "hfli_backfilled",
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=legacy.token,
        )

        response = NodeViewSet.as_view({"post": "heartbeat"})(request)

        self.assertEqual(response.status_code, 200)
        node.refresh_from_db()
        self.assertEqual(node.installation_id, "hfli_backfilled")

    def test_used_legacy_credential_rotates_after_link_expiry(self):
        legacy = NodeToken.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            token="expired-legacy-token",
            enrollment_mode=NodeToken.EnrollmentMode.LEGACY,
            is_active=True,
            used_at=timezone.now() - timedelta(days=30),
            expires_at=timezone.now() - timedelta(days=1),
        )
        node = Node.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            name="offline-legacy-host",
            installation_id="offline-legacy-host-id",
        )
        request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "node_id": node.id,
                "role": NodeRole.AGENT,
                "name": node.name,
                "installation_id": node.installation_id,
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=legacy.token,
        )

        response = NodeViewSet.as_view({"post": "heartbeat"})(request)

        self.assertEqual(response.status_code, 200)
        credential = response.data["node_credential"]
        self.assertTrue(credential.startswith("hfln_"))
        self.assertTrue(validate_agent_ws_credentials(node.id, credential))
        self.assertFalse(validate_agent_ws_credentials(node.id, legacy.token))

    def test_soft_delete_revokes_node_credential(self):
        opened = self._open_session("host-a")
        registered = self._register("host-a", opened.data["installation_session"])
        node = Node.objects.get(installation_id="host-a")
        credential = registered.data["node_credential"]

        node.soft_delete()

        row = NodeCredential.all_objects.get(node=node)
        self.assertFalse(row.is_active)
        self.assertIsNotNone(row.revoked_at)
        self.assertFalse(validate_agent_ws_credentials(node.id, credential))

    def test_soft_deleted_host_can_enroll_again_with_same_installation_id(self):
        first_session = self._open_session("host-a")
        first_registration = self._register(
            "host-a",
            first_session.data["installation_session"],
        )
        original = Node.objects.get(installation_id="host-a")
        original.soft_delete()

        second_session = self._open_session("host-a")
        second_registration = self._register(
            "host-a",
            second_session.data["installation_session"],
        )

        self.assertEqual(first_registration.status_code, 200)
        self.assertEqual(second_session.status_code, 201)
        self.assertEqual(second_registration.status_code, 200)
        replacement = Node.objects.get(installation_id="host-a")
        self.assertNotEqual(replacement.id, original.id)
        self.assertEqual(
            Node.all_objects.filter(installation_id="host-a").count(),
            2,
        )
