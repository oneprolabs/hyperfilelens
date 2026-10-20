"""A public Gateway's installed routing identity is not its tenant ownership."""

import base64
import hashlib
import hmac
import json
import uuid
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient, APIRequestFactory

from apps.iam.models import Organization
from apps.lens_bridge.models import LensGatewayLink
from apps.node.api.views import artifact_release
from apps.node.api.views.enrollment_helpers import token_usable_for_bootstrap
from apps.node.api.views.installation_session import InstallationSessionView
from apps.node.api.views.node import NodeViewSet
from apps.node.models import Node, NodeCredential, NodeToken
from apps.node.services.internal.enrollment_auth import (
    active_node_credential,
    issue_node_credential,
    open_installation_session,
    release_installation_session,
    resolve_enrollment_authorization,
)
from apps.node.services.internal.transport_identity import (
    node_for_transport,
    resolve_transport_scope,
)


class GatewayTransportIdentityTests(TestCase):
    def setUp(self):
        self.namespace = "installed-public-gateway"
        self.org = Organization.objects.create(key="unrelated-tenant", name="Tenant")
        self.token = NodeToken.objects.create(
            organization=None, role="gateway", gateway_scope="platform",
            transport_namespace=self.namespace,
        )
        self.node = Node.objects.create(
            organization=None, scope=Node.Scope.PLATFORM, name="Public Gateway", role="gateway",
            transport_namespace=self.namespace, installation_id="public-installation",
        )
        self.link = LensGatewayLink.objects.create(
            organization=None, gateway=self.node, scope="platform",
            sl_lensnode_uuid=uuid.uuid4(),
        )
        self.secret = issue_node_credential(
            node=self.node, enrollment_token=self.token,
            installation_id=self.node.installation_id,
        )
        self.factory = APIRequestFactory()

    def rename_owner_key(self):
        self.org.key = "changed-infrastructure-key"
        self.org.save(update_fields=["key"])

    def test_public_token_snapshots_the_key_and_does_not_rewrite_it_on_save(self):
        self.assertEqual(self.token.transport_namespace, self.namespace)
        self.rename_owner_key()
        self.token.note = "Still the same installation"
        self.token.save(update_fields=["note"])
        self.token.refresh_from_db()
        self.node.refresh_from_db()
        self.assertEqual(self.token.transport_key, self.namespace)
        self.assertEqual(self.node.transport_key, self.namespace)

    def test_namespace_router_is_one_sql_query_and_not_an_authentication_grant(self):
        self.rename_owner_key()
        with self.assertNumQueries(1):
            selected = resolve_transport_scope(self.namespace)
        self.assertIsNone(selected.organization)
        self.assertEqual(selected.scope, "platform")
        self.assertIsNone(resolve_enrollment_authorization(
            org=selected.organization, secret="invalid", role="gateway", namespace=self.namespace,
        ))

    def test_installed_node_keeps_routing_after_its_enrollment_token_is_deleted(self):
        self.rename_owner_key()
        self.token.delete()
        self.assertEqual(resolve_transport_scope(self.namespace).scope, "platform")
        self.assertEqual(
            node_for_transport(namespace=self.namespace, node_id=self.node.pk).pk,
            self.node.pk,
        )

    def test_ambiguous_namespace_fails_closed(self):
        self.rename_owner_key()
        Organization.objects.create(key=self.namespace, name="Unrelated tenant")
        self.assertIsNone(resolve_transport_scope(self.namespace))

    def test_historical_shared_keys_are_disambiguated_by_bound_records_not_claimed_scope(self):
        tenant = Organization.objects.create(key=self.namespace, name="Historical tenant")
        private_token = NodeToken.objects.create(organization=tenant, role="gateway")
        self.assertIsNone(resolve_transport_scope(self.namespace))
        public_scope = resolve_transport_scope(self.namespace, secret=self.token.token)
        private_scope = resolve_transport_scope(self.namespace, secret=private_token.token)
        self.assertEqual(public_scope.scope, "platform")
        self.assertIsNone(public_scope.organization)
        self.assertEqual(private_scope.scope, "organization")
        self.assertEqual(private_scope.organization.pk, tenant.pk)
        self.token.is_active = False
        self.token.save(update_fields=["is_active"])
        request = self.factory.post(
            "/api/v1/node/enrollment/session",
            {"role": "gateway", "installation_id": "revoked-record-bound-token"},
            format="json", HTTP_X_ORG_KEY=self.namespace,
            HTTP_X_NODE_TOKEN=self.token.token,
        )
        self.assertEqual(InstallationSessionView.as_view()(request).status_code, 401)

    def test_disabling_unrelated_tenant_does_not_disable_platform_namespace(self):
        self.rename_owner_key()
        self.org.is_active = False
        self.org.save(update_fields=["is_active"])
        self.assertEqual(resolve_transport_scope(self.namespace).scope, "platform")
        self.assertIsNotNone(node_for_transport(namespace=self.namespace, node_id=self.node.pk))

    def test_private_nodes_and_tokens_keep_the_existing_tenant_key_behavior(self):
        tenant = Organization.objects.create(key="tenant", name="Tenant")
        private = Node.objects.create(organization=tenant, name="Private", role="gateway")
        token = NodeToken.objects.create(organization=tenant, role="gateway")
        self.assertEqual(private.transport_namespace, "")
        self.assertEqual(token.transport_namespace, "")
        self.assertEqual(private.transport_key, tenant.key)
        self.assertEqual(token.transport_key, tenant.key)

    def test_database_rejects_non_gateway_and_private_token_namespaces(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Node.objects.create(
                organization=self.org, role="agent", name="Invalid",
                transport_namespace=self.namespace,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            NodeToken.objects.create(
                organization=self.org, role="gateway", gateway_scope="organization",
                transport_namespace=self.namespace,
            )

    def test_valid_secrets_do_not_authorize_a_different_namespace(self):
        self.rename_owner_key()
        self.assertIsNotNone(resolve_enrollment_authorization(
            org=None, secret=self.token.token, role="gateway", namespace=self.namespace,
        ))
        self.assertIsNone(resolve_enrollment_authorization(
            org=None, secret=self.token.token, role="gateway", namespace=self.org.key,
        ))
        self.assertIsNone(active_node_credential(
            org=None, secret=self.secret, role="gateway", namespace=self.org.key,
        ))
        self.assertFalse(token_usable_for_bootstrap(
            org=None, token=self.secret, role="gateway", namespace=self.namespace,
        ))

    def test_session_exchange_and_release_keep_the_installed_namespace(self):
        self.rename_owner_key()
        Organization.objects.create(key=self.namespace, name="Historical shared key")
        request = self.factory.post(
            "/api/v1/node/enrollment/session",
            {"role": "gateway", "installation_id": "new-public-installation"},
            format="json", HTTP_X_ORG_KEY=self.namespace,
            HTTP_X_NODE_TOKEN=self.token.token,
        )
        response = InstallationSessionView.as_view()(request)
        self.assertEqual(response.status_code, 201)
        secret = response.data["installation_session"]
        authorization = resolve_enrollment_authorization(
            org=None, role="gateway", secret=secret, namespace=self.namespace,
        )
        self.assertIsNotNone(authorization)
        self.assertIsNone(resolve_enrollment_authorization(
            org=None, role="gateway", secret=secret, namespace=self.org.key,
        ))
        with patch(
            "apps.node.services.internal.enrollment_auth.release_session_slot",
        ) as release, self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(release_installation_session(
                org=None, role="gateway", secret=secret,
                installation_id="new-public-installation", namespace=self.namespace,
            ))
        release.assert_called_once_with(self.namespace, authorization.session.pk)

    @patch("apps.lens_bridge.services.provisioning.provision_gateway_lens_on_register", return_value=None)
    def test_existing_gateway_heartbeat_does_not_rotate_its_credential(self, provision):
        self.rename_owner_key()
        before = NodeCredential.objects.get(node=self.node).secret_hash
        request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "node_id": self.node.pk, "role": "gateway", "version": "1.0.0",
                "os_name": "linux", "installation_id": self.node.installation_id,
            },
            format="json", HTTP_X_ORG_KEY=self.namespace, HTTP_X_NODE_TOKEN=self.secret,
        )
        response = NodeViewSet.as_view({"post": "heartbeat"})(request)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("node_credential", response.data)
        self.assertEqual(NodeCredential.objects.get(node=self.node).secret_hash, before)
        self.assertEqual(Node.objects.count(), 1)
        provision.assert_called_once()

    @patch("apps.subscription.services.interface.enforce_node_role_quota")
    @patch("apps.lens_bridge.services.provisioning.provision_gateway_lens_on_register", return_value=None)
    def test_registration_copies_only_the_server_authorized_namespace(self, _provision, _quota):
        self.rename_owner_key()
        request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "role": "gateway", "version": "1.0.0", "os_name": "linux",
                "installation_id": "another-installation",
                "transport_namespace": "client-cannot-choose-this",
            },
            format="json", HTTP_X_ORG_KEY=self.namespace,
            HTTP_X_NODE_TOKEN=self.token.token,
        )
        response = NodeViewSet.as_view({"post": "heartbeat"})(request)
        self.assertEqual(response.status_code, 200)
        registered = Node.objects.get(pk=response.data["node_id"])
        self.assertEqual(registered.transport_namespace, self.namespace)
        self.assertIsNone(registered.organization_id)
        self.assertEqual(registered.scope, Node.Scope.PLATFORM)

    def test_token_revoked_between_lookup_and_row_lock_cannot_enroll(self):
        def resolve_then_revoke(**kwargs):
            authorization = resolve_enrollment_authorization(**kwargs)
            NodeToken.objects.filter(pk=self.token.pk).update(is_active=False)
            return authorization

        request = self.factory.post(
            "/api/v1/node/nodes/heartbeat/",
            {
                "role": "gateway", "version": "1.0.0", "os_name": "linux",
                "installation_id": "revoked-before-lock",
            },
            format="json", HTTP_X_ORG_KEY=self.namespace,
            HTTP_X_NODE_TOKEN=self.token.token,
        )
        with patch(
            "apps.node.api.views.node.resolve_enrollment_authorization",
            side_effect=resolve_then_revoke,
        ):
            response = NodeViewSet.as_view({"post": "heartbeat"})(request)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Node.objects.count(), 1)
        self.assertEqual(NodeCredential.objects.count(), 1)

    def test_performance_sync_uses_node_namespace_and_still_requires_its_credential(self):
        self.rename_owner_key()
        path = f"/api/v1/node/agent/performance-policy?node_id={self.node.pk}"
        client = APIClient()
        for namespace, secret, expected in (
            (self.namespace, self.secret, 200),
            (self.org.key, self.secret, 401),
            (self.namespace, "invalid", 401),
        ):
            with self.subTest(namespace=namespace, secret_valid=secret == self.secret):
                response = client.get(
                    path, HTTP_X_ORG_KEY=namespace, HTTP_X_NODE_TOKEN=secret,
                )
                self.assertEqual(response.status_code, expected)

    def node_status(self, *, namespace=None, secret=None, node_id=None):
        return APIClient().get(
            "/api/v1/node/enrollment/node-status",
            {"node_id": self.node.pk if node_id is None else node_id},
            HTTP_X_ORG_KEY=self.namespace if namespace is None else namespace,
            HTTP_X_NODE_TOKEN=self.secret if secret is None else secret,
        )

    @patch("apps.node.api.views.enrollment_node_status.agent_ws_routable", return_value=True)
    @patch("apps.node.api.views.enrollment_node_status.agent_connection_status", return_value="online")
    def test_install_verification_accepts_platform_namespace_without_an_organization(
        self, connection_status, routable,
    ):
        for namespace in ("__platform_lens__", "platform:hfl"):
            with self.subTest(namespace=namespace):
                Node.objects.filter(pk=self.node.pk).update(transport_namespace=namespace)
                self.assertFalse(Organization.objects.filter(key=namespace).exists())
                response = self.node_status(namespace=namespace)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data, {
                    "node_id": self.node.pk, "status": "online", "routable": True,
                })
                routable.assert_called_with(agent_id=self.node.pk)
                self.assertIsNone(connection_status.call_args.args[0].organization_id)

    @patch("apps.node.api.views.enrollment_node_status.agent_ws_routable")
    @patch("apps.node.api.views.enrollment_node_status.agent_connection_status")
    def test_install_verification_keeps_namespace_and_credential_checks(
        self, connection_status, routable,
    ):
        for namespace, secret, expected in (
            (self.org.key, self.secret, 404),
            ("unknown-namespace", self.secret, 404),
            (self.namespace, "invalid", 401),
            (self.namespace, self.token.token, 401),
            (self.namespace, "", 400),
        ):
            with self.subTest(namespace=namespace, expected=expected):
                response = self.node_status(namespace=namespace, secret=secret)
                self.assertEqual(response.status_code, expected, response.data)
        connection_status.assert_not_called()
        routable.assert_not_called()

    @patch("apps.node.api.views.enrollment_node_status.agent_ws_routable", return_value=False)
    @patch("apps.node.api.views.enrollment_node_status.agent_connection_status", return_value="offline")
    def test_install_verification_does_not_report_an_offline_gateway_as_online(
        self, _connection_status, _routable,
    ):
        response = self.node_status()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "offline")
        self.assertFalse(response.data["routable"])
        self.node.is_deleted = True
        self.node.save(update_fields=["is_deleted", "updated_at"])
        self.assertEqual(self.node_status().status_code, 404)

    @patch("apps.node.api.views.enrollment_node_status.agent_ws_routable", return_value=True)
    @patch("apps.node.api.views.enrollment_node_status.agent_connection_status", return_value="online")
    def test_install_verification_preserves_private_node_organization_lifecycle(
        self, _connection_status, _routable,
    ):
        token = NodeToken.objects.create(organization=self.org, role="gateway")
        node = Node.objects.create(
            organization=self.org, name="Private Gateway", role="gateway",
            installation_id="private-installation",
        )
        secret = issue_node_credential(
            node=node, enrollment_token=token, installation_id=node.installation_id,
        )
        response = self.node_status(
            namespace=self.org.key, secret=secret, node_id=node.pk,
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.org.is_active = False
        self.org.save(update_fields=["is_active"])
        self.assertEqual(self.node_status(
            namespace=self.org.key, secret=secret, node_id=node.pk,
        ).status_code, 404)
        self.assertEqual(self.node_status().status_code, 200)

    def test_install_verification_rejects_invalid_node_ids_without_server_errors(self):
        for node_id in ("0", "-1", "9223372036854775808", "9" * 5000, "\N{SUPERSCRIPT TWO}"):
            with self.subTest(length=len(node_id)):
                response = self.node_status(node_id=node_id)
                self.assertEqual(response.status_code, 400, response.data)

    def install_result(self, *, namespace=None, secret=None, node_id=None):
        return APIClient().post(
            "/api/v1/node/enrollment/gateway-install-status",
            {
                "node_id": self.node.pk if node_id is None else node_id,
                "status": "success", "phase": "install",
            },
            format="json",
            HTTP_X_ORG_KEY=self.namespace if namespace is None else namespace,
            HTTP_X_NODE_TOKEN=self.secret if secret is None else secret,
        )

    def test_platform_install_result_updates_its_existing_link_without_a_tenant(self):
        for namespace in ("__platform_lens__", "platform:hfl"):
            with self.subTest(namespace=namespace):
                Node.objects.filter(pk=self.node.pk).update(transport_namespace=namespace)
                response = self.install_result(namespace=namespace)
                self.assertEqual(response.status_code, 200, response.data)
                self.link.refresh_from_db()
                self.assertIsNone(self.link.organization_id)
                self.assertEqual(self.link.scope, "platform")
                self.assertEqual(self.link.config_json["install_status"], "success")
                self.assertFalse(Organization.objects.filter(key=namespace).exists())

    @patch("apps.node.api.views.gateway_install_status.provisioning.record_gateway_install_status")
    def test_install_result_rejects_wrong_namespaces_credentials_and_node_roles(self, record):
        for namespace, secret, expected in (
            (self.org.key, self.secret, 404),
            ("unknown-namespace", self.secret, 404),
            (self.namespace, "invalid", 401),
            (self.namespace, self.token.token, 401),
        ):
            with self.subTest(namespace=namespace, expected=expected):
                self.assertEqual(self.install_result(
                    namespace=namespace, secret=secret,
                ).status_code, expected)
        host = Node.objects.create(organization=self.org, name="Host", role="agent")
        self.assertEqual(self.install_result(
            namespace=self.org.key, node_id=host.pk,
        ).status_code, 404)
        self.node.is_deleted = True
        self.node.save(update_fields=["is_deleted", "updated_at"])
        self.assertEqual(self.install_result().status_code, 404)
        record.assert_not_called()

    def test_private_install_result_keeps_organization_lifecycle_checks(self):
        token = NodeToken.objects.create(organization=self.org, role="gateway")
        node = Node.objects.create(
            organization=self.org, name="Private", role="gateway",
            installation_id="private-installation",
        )
        link = LensGatewayLink.objects.create(
            organization=self.org, gateway=node, scope="organization",
        )
        secret = issue_node_credential(
            node=node, enrollment_token=token, installation_id=node.installation_id,
        )
        self.assertEqual(self.install_result(
            namespace=self.org.key, node_id=node.pk, secret=secret,
        ).status_code, 200)
        link.refresh_from_db()
        self.assertEqual(link.config_json["install_status"], "success")
        self.org.is_active = False
        self.org.save(update_fields=["is_active"])
        self.assertEqual(self.install_result(
            namespace=self.org.key, node_id=node.pk, secret=secret,
        ).status_code, 404)

    def test_install_result_rejects_invalid_node_ids_without_server_errors(self):
        for node_id in ("0", "-1", "9223372036854775808", "9" * 5000, "\N{SUPERSCRIPT TWO}"):
            with self.subTest(length=len(node_id)):
                response = self.install_result(node_id=node_id)
                self.assertEqual(response.status_code, 400, response.data)

    @patch("apps.node.api.views.connection_info.agent_ws_routable", return_value=False)
    @patch("apps.lens_bridge.services.sl_client.request_json")
    def test_connection_proof_preserves_namespace_without_changing_agent_protocol(self, remote, _route):
        self.rename_owner_key()
        remote.return_value = {
            "uuid": str(self.link.sl_lensnode_uuid), "status": "online",
            "active_datasource_operations": [],
        }
        nonce = "a" * 64
        key = hashlib.sha256(self.secret.encode()).digest()
        message = "\0".join([
            "node", nonce, str(self.node.pk), self.namespace,
            self.node.role, self.node.installation_id,
        ])
        proof = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
        response = APIClient().get(
            f"/api/v1/node/enrollment/connection-info?node_id={self.node.pk}",
            HTTP_X_ORG_KEY=self.namespace, HTTP_X_CONNECTION_NONCE=nonce,
            HTTP_X_CONNECTION_PROOF=proof,
        )
        self.assertEqual(response.status_code, 200)
        payload = base64.b64decode(response.data["payload"])
        expected = hmac.new(
            key, b"server\0" + nonce.encode() + b"\0" + payload, hashlib.sha256,
        ).hexdigest()
        self.assertEqual(response.data["proof"], expected)
        self.assertEqual(json.loads(payload)["org_key"], self.namespace)

    @patch("apps.node.api.views.artifact_release.try_acquire_slot", return_value=(True, 1))
    def test_existing_signed_downloads_remain_bound_to_the_original_namespace(self, acquire):
        self.rename_owner_key()
        Organization.objects.create(key=self.namespace, name="Historical shared key")
        path = "/media/agent-releases/1.0.0/gateway.tar.gz"
        credential = NodeCredential.objects.get(node=self.node)
        for kind, bounds in (
            ("maintenance", {"maintenance_node_id": self.node.pk}),
            ("credential", {"credential_id": credential.pk, "node_id": self.node.pk}),
            ("enrollment", {"token_id": self.token.pk}),
        ):
            with self.subTest(kind=kind):
                signed = artifact_release._make_release_token(
                    {"p": path, "org": self.namespace, "role": "gateway", **bounds},
                    ttl_seconds=600,
                )
                request = self.factory.get(
                    "/api/v1/node/enrollment/agent/releases/auth",
                    {"t": signed}, HTTP_X_ORIGINAL_URI=f"{path}?t={signed}",
                )
                response = artifact_release.AgentReleasesAuthView.as_view()(request)
                self.assertEqual(response.status_code, 204)
                self.assertEqual(response["X-Tenant-Key"], self.namespace)
                self.assertEqual(acquire.call_args.args[0], self.namespace)

    def test_a_session_with_another_namespace_cannot_be_opened(self):
        self.rename_owner_key()
        with self.assertRaises(PermissionError):
            open_installation_session(
                org=None, enrollment_secret=self.token.token, role="gateway",
                installation_id="invalid-session", namespace=self.org.key,
            )
