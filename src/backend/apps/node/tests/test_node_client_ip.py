from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIRequestFactory

from apps.iam.models import Organization
from apps.node.api.views.node import NodeViewSet
from apps.node.api.serializers.node import NodeHeartbeatSerializer, NodeSerializer
from apps.node.models import Node, NodeCredential, NodeToken
from apps.node.models.base import NodeRole
from common.http.client_ip import client_ip_from_meta, client_ip_from_scope


class ClientIpHelperTests(SimpleTestCase):
    @override_settings(TRUSTED_PROXY=True)
    def test_prefers_x_forwarded_for(self):
        meta = {
            "HTTP_X_FORWARDED_FOR": "203.0.113.10, 172.18.0.7",
            "REMOTE_ADDR": "172.18.0.7",
        }
        self.assertEqual(client_ip_from_meta(meta), "203.0.113.10")

    def test_falls_back_to_remote_addr(self):
        meta = {"REMOTE_ADDR": "10.0.0.8"}
        self.assertEqual(client_ip_from_meta(meta), "10.0.0.8")

    @override_settings(TRUSTED_PROXY=False)
    def test_ignores_forwarded_header_without_trusted_proxy(self):
        meta = {
            "HTTP_X_FORWARDED_FOR": "203.0.113.10",
            "REMOTE_ADDR": "10.0.0.8",
        }
        self.assertEqual(client_ip_from_meta(meta), "10.0.0.8")

    @override_settings(TRUSTED_PROXY=True)
    def test_scope_reads_forwarded_header(self):
        scope = {
            "headers": [
                (b"x-forwarded-for", b"192.168.10.15, 172.18.0.7"),
            ],
            "client": ("172.18.0.7", 12345),
        }
        self.assertEqual(client_ip_from_scope(scope), "192.168.10.15")


@override_settings(TRUSTED_PROXY=True)
class NodeHeartbeatClientIpTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="node-ip-org", name="Node IP Org")
        self.token_row = NodeToken.objects.create(
            organization=self.org,
            role=NodeRole.AGENT,
            token="enroll-token-123",
            is_active=True,
        )
        self.factory = APIRequestFactory()

    def _credential_for(self, node: Node) -> str:
        secret = f"hfln_node-ip-{node.id}"
        credential = NodeCredential(
            organization=self.org,
            node=node,
            role=node.role,
            installation_id=f"node-ip-{node.id}",
        )
        credential.set_secret(secret)
        credential.save()
        return secret

    def test_heartbeat_separates_reported_host_ip_from_forwarded_client_ip(self):
        request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "role": "agent",
                "name": "agent-host",
                "version": "1.0.0",
                "os_name": "linux",
                "metadata": {
                    "inventory": {
                        "primary_ip_address": "10.20.1.15",
                        "ip_addresses": ["10.20.1.15"],
                    }
                },
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=self.token_row.token,
            HTTP_X_FORWARDED_FOR="192.168.10.15",
            REMOTE_ADDR="172.18.0.7",
        )
        response = NodeViewSet.as_view({"post": "heartbeat"})(request)
        self.assertEqual(response.status_code, 200)

        node = Node.objects.get(organization=self.org)
        self.assertEqual(str(node.ip_address), "10.20.1.15")
        self.assertEqual(str(node.connection_ip_address), "192.168.10.15")
        self.assertEqual(node.availability, Node.Availability.ONLINE)
        self.assertIsNotNone(node.availability_updated_at)

    def test_heartbeat_updates_host_and_connection_addresses_independently(self):
        node = Node.objects.create(
            organization=self.org,
            name="agent-existing",
            role=NodeRole.AGENT,
            ip_address="10.20.1.40",
        )
        node_credential = self._credential_for(node)
        request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "node_id": node.id,
                "role": "agent",
                "name": node.name,
                "metadata": {
                    "inventory": {
                        "primary_ip_address": "10.20.1.41",
                        "ip_addresses": ["10.20.1.41"],
                    }
                },
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=node_credential,
            HTTP_X_FORWARDED_FOR="192.168.7.51",
            REMOTE_ADDR="172.18.0.7",
        )
        response = NodeViewSet.as_view({"post": "heartbeat"})(request)
        self.assertEqual(response.status_code, 200)

        node.refresh_from_db()
        self.assertEqual(str(node.ip_address), "10.20.1.41")
        self.assertEqual(str(node.connection_ip_address), "192.168.7.51")

    def test_connection_only_heartbeat_does_not_overwrite_host_ip(self):
        node = Node.objects.create(
            organization=self.org,
            name="agent-existing",
            role=NodeRole.AGENT,
            ip_address="10.20.1.50",
        )
        node_credential = self._credential_for(node)
        request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "node_id": node.id,
                "role": "agent",
                "name": node.name,
            },
            format="json",
            HTTP_X_ORG_KEY=self.org.key,
            HTTP_X_NODE_TOKEN=node_credential,
            HTTP_X_FORWARDED_FOR="203.0.113.20",
            REMOTE_ADDR="172.18.0.7",
        )

        response = NodeViewSet.as_view({"post": "heartbeat"})(request)
        self.assertEqual(response.status_code, 200)
        node.refresh_from_db()
        self.assertEqual(str(node.ip_address), "10.20.1.50")
        self.assertEqual(str(node.connection_ip_address), "203.0.113.20")

    def test_host_ip_is_read_only_in_tenant_serializer(self):
        self.assertTrue(NodeSerializer().fields["ip_address"].read_only)
        self.assertTrue(NodeSerializer().fields["availability"].read_only)
        self.assertTrue(NodeSerializer().fields["availability_updated_at"].read_only)
        self.assertNotIn(
            "repository_server_address",
            NodeHeartbeatSerializer().fields,
        )
