"""Changing an installed endpoint must not enroll or provision resources."""
import uuid
import base64
import hashlib
import hmac
import json
from unittest.mock import patch

from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.iam.models import Organization
from apps.node.models import Node, NodeTask, NodeCredential
from apps.node.services.internal.enrollment_auth import issue_node_credential
from apps.lens_bridge.models import LensGatewayLink


class NodeConnectionInfoTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="connection-test", name="Test")
        self.node = Node.objects.create(
            organization=self.org, name="Source", role=Node.Role.AGENT,
            installation_id="existing-installation",
        )
        self.secret = issue_node_credential(
            node=self.node, enrollment_token=None,
            installation_id=self.node.installation_id,
        )
        self.client = APIClient()

    def get(self, **headers):
        nonce = "a" * 64
        secret = headers.get("token", self.secret)
        org = headers.get("org", self.org.key)
        message = "\0".join(["node", nonce, str(self.node.id), org, self.node.role, self.node.installation_id])
        signature = hmac.new(hashlib.sha256(secret.encode()).digest(), message.encode(), hashlib.sha256).hexdigest()
        response = self.client.get(
            f"/api/v1/node/enrollment/connection-info?node_id={self.node.id}",
            HTTP_X_ORG_KEY=headers.get("org", self.org.key),
            HTTP_X_CONNECTION_NONCE=nonce,
            HTTP_X_CONNECTION_PROOF=signature,
        )
        if response.status_code == 200:
            self.assertIn("proof", response.data)
            payload = base64.b64decode(response.data["payload"])
            expected = hmac.new(hashlib.sha256(secret.encode()).digest(), b"server\0" + nonce.encode() + b"\0" + payload, hashlib.sha256).hexdigest()
            self.assertEqual(response.data["proof"], expected)
            response.data = json.loads(payload)
        return response

    @patch("apps.node.api.views.connection_info.agent_ws_routable", return_value=False)
    def test_existing_identity_without_writes(self, _routable):
        before = NodeCredential.objects.get(node=self.node).last_used_at
        response = self.get()
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data["installation_id"], "existing-installation")
        self.assertEqual(data["node_id"], str(self.node.id))
        self.assertFalse(data["busy"])
        self.assertNotIn(self.secret, str(data))
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(Node.objects.count(), 1)
        self.assertEqual(NodeCredential.objects.get(node=self.node).last_used_at, before)

    def test_bad_credentials_and_wrong_organization(self):
        for headers in ({"token": "bad"}, {"org": "other"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.get(**headers).status_code, 401)

    @patch("apps.node.api.views.connection_info.agent_ws_routable", return_value=False)
    def test_active_work_reported(self, _routable):
        NodeTask.objects.create(
            node=self.node, organization=self.org,
            requesting_organization_id=self.org.id, kind="backup.run",
            watchdog_deadline_at=timezone.now() + timedelta(minutes=5),
        )
        self.assertTrue(self.get().data["busy"])

    @patch("apps.node.api.views.connection_info.agent_ws_routable", return_value=False)
    def test_gateway_uses_existing_binding_without_provisioning(self, _routable):
        self.node.role = Node.Role.GATEWAY
        self.node.save(update_fields=["role"])
        self.secret = issue_node_credential(
            node=self.node, enrollment_token=None,
            installation_id=self.node.installation_id,
        )
        lens_uuid = uuid.uuid4()
        LensGatewayLink.objects.create(
            organization=self.org, gateway=self.node,
            scope=LensGatewayLink.GatewayScope.ORGANIZATION,
            sl_lensnode_uuid=lens_uuid,
        )
        snapshot = {
            "uuid": str(lens_uuid), "status": "offline",
            "tasks": [{"name": "knowledge_qa", "title": "Knowledge Q&A"}],
            "active_datasource_operations": [], "active_run_count": 0,
            "queued_run_count": 0, "awaiting_resume_count": 0,
        }
        with patch("apps.lens_bridge.services.sl_client.request_json", return_value=snapshot), patch(
            "apps.lens_bridge.services.provisioning.provision_gateway_lens_on_register",
        ) as provision:
            response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["lens"]["uuid"], str(lens_uuid))
        self.assertFalse(response.data["busy"])
        provision.assert_not_called()
        self.assertNotIn("token", response.data["lens"])
        for field, active in (
            ("active_datasource_operations", [{"task_id": "converting"}]),
            ("active_run_count", 1),
            ("queued_run_count", 1),
            ("awaiting_resume_count", 1),
        ):
            with self.subTest(field=field), patch(
                "apps.lens_bridge.services.sl_client.request_json",
                return_value={**snapshot, field: active},
            ):
                self.assertTrue(self.get().data["busy"])
        with patch(
            "apps.lens_bridge.services.sl_client.request_json",
            return_value={key: value for key, value in snapshot.items() if key != "tasks"},
        ):
            self.assertFalse(self.get().data["busy"])
        for operations in (None, {}):
            with self.subTest(operations=operations), patch(
                "apps.lens_bridge.services.sl_client.request_json",
                return_value={**snapshot, "active_datasource_operations": operations},
            ):
                self.assertEqual(self.get().status_code, 503)

    @patch("apps.node.api.views.connection_info.agent_ws_routable", return_value=False)
    def test_missing_gateway_binding_not_created(self, _routable):
        self.node.role = Node.Role.GATEWAY
        self.node.save(update_fields=["role"])
        self.secret = issue_node_credential(
            node=self.node, enrollment_token=None,
            installation_id=self.node.installation_id,
        )
        self.assertEqual(self.get().status_code, 409)
        self.assertFalse(LensGatewayLink.objects.exists())
