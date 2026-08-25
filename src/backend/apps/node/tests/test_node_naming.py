"""Tests for default node naming on enrollment."""

from django.test import TestCase

from apps.iam.models import Organization
from apps.node.models import Node
from apps.node.services.internal.node_naming import (
    hostname_from_metadata,
    is_auto_assigned_node_name,
    is_automatic_user_node_name,
    resolve_registration_node_name,
    runtime_principal_name,
    uniquify_node_name,
)


class NodeNamingTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="node-naming-org",
            name="Node Naming Org",
        )

    def test_hostname_from_inventory(self):
        meta = {"inventory": {"hostname": "host-a"}}
        self.assertEqual(hostname_from_metadata(meta), "host-a")

    def test_hostname_from_metadata_root(self):
        meta = {"hostname": "host-b"}
        self.assertEqual(hostname_from_metadata(meta), "host-b")

    def test_resolve_registration_prefers_hostname(self):
        name = resolve_registration_node_name(
            payload={
                "name": "new-node",
                "metadata": {"hostname": "proxy-gw-01"},
            },
        )
        self.assertEqual(name, "proxy-gw-01")

    def test_resolve_registration_keeps_custom_name(self):
        name = resolve_registration_node_name(
            payload={
                "name": "custom-label",
                "metadata": {},
            },
        )
        self.assertEqual(name, "custom-label")

    def test_user_install_name_includes_runtime_principal(self):
        name = resolve_registration_node_name(
            payload={
                "name": "host-a",
                "installation_mode": "user_continuous",
                "metadata": {
                    "hostname": "host-a",
                    "runtime_principal": {
                        "id": "1001",
                        "name": "backup-user",
                    },
                },
            },
        )
        self.assertEqual(name, "host-a · backup-user")

    def test_runtime_principal_name_ignores_malformed_metadata(self):
        self.assertEqual(runtime_principal_name({"runtime_principal": "root"}), "")

    def test_user_install_name_is_bounded_to_model_limit(self):
        name = resolve_registration_node_name(
            payload={
                "installation_mode": "user",
                "metadata": {
                    "hostname": "h" * 180,
                    "runtime_principal": {"name": "u" * 80},
                },
            }
        )
        self.assertEqual(len(name), 200)

    def test_unique_suffix_stays_within_model_limit(self):
        Node.objects.create(
            organization=self.org,
            name="n" * 200,
            role=Node.Role.AGENT,
        )
        name = uniquify_node_name(
            organization_id=self.org.id,
            name="n" * 220,
            exclude_node_id=12345,
        )
        self.assertEqual(len(name), 200)
        self.assertTrue(name.endswith("-12345"))

    def test_generated_user_name_recognizes_collision_suffix(self):
        self.assertTrue(
            is_automatic_user_node_name(
                name="host-a · backup-user-42",
                metadata={
                    "hostname": "host-a",
                    "runtime_principal": {"name": "backup-user"},
                },
                node_id=42,
            )
        )

    def test_custom_user_name_is_not_treated_as_generated(self):
        self.assertFalse(
            is_automatic_user_node_name(
                name="Finance files",
                metadata={
                    "hostname": "host-a",
                    "runtime_principal": {"name": "backup-user"},
                },
                node_id=42,
            )
        )

    def test_auto_assigned_names(self):
        self.assertTrue(is_auto_assigned_node_name("new-node"))
        self.assertTrue(is_auto_assigned_node_name(""))
        self.assertFalse(is_auto_assigned_node_name("proxy-gw-01"))
