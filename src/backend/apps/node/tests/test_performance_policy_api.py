"""Installed-node synchronization and console persistence integration."""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.iam.models import Organization
from apps.node.api.serializers.node import NodeSerializer
from apps.node.models import Node, NodeTask
from apps.node.services.internal.enrollment_auth import issue_node_credential


class PerformancePolicyAPITests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="perf-policy-isolated", name="Policy tests"
        )
        self.node = Node.objects.create(
            organization=self.org,
            name="Source",
            role=Node.Role.AGENT,
            installation_id="performance-policy-test-install",
            metadata={"inventory": {"cpu_cores": 8}, "unrelated": "preserve"},
        )
        self.secret = issue_node_credential(
            node=self.node,
            enrollment_token=None,
            installation_id=self.node.installation_id,
        )
        self.client = APIClient()
        self.path = f"/api/v1/node/agent/performance-policy?node_id={self.node.id}"
        self.headers = {
            "HTTP_X_ORG_KEY": self.org.key,
            "HTTP_X_NODE_TOKEN": self.secret,
        }

    def save(self, memory=2048):
        serializer = NodeSerializer(
            self.node,
            data={
                "metadata": {
                    "perf_settings": {
                        "cpu_cores": 2,
                        "mem_cap_mb": memory,
                        "bandwidth": 20,
                        "kopia_cache_mb": 0,
                        "log": "info",
                        "log_retention": 30,
                        "log_space_mb": 512,
                    }
                }
            },
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        saved = serializer.save()
        self.node.refresh_from_db()
        self.assertIs(saved, self.node)
        self.assertEqual(saved.metadata["performance_policy"]["revision"], self.node.metadata["performance_policy"]["revision"])

    def test_console_parser_aliases_preserve_metadata_and_activate_once(self):
        self.save(2050)
        self.assertEqual(self.node.metadata["unrelated"], "preserve")
        self.assertEqual(self.node.metadata["perf_settings"]["memCapMb"], 2050)
        self.assertEqual(self.node.metadata["performance_policy"]["revision"], 1)
        self.save(2050)
        self.assertEqual(self.node.metadata["performance_policy"]["revision"], 1)
        self.assertEqual(NodeTask.objects.count(), 0)

    def test_machine_contract_is_plain_json_not_console_envelope(self):
        self.save()
        response = self.client.get(self.path, **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["policy"]["settings"]["cpuCores"], 2)
        self.assertNotIn("data", response.json())
        self.assertNotIn(self.secret, response.content.decode())
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_unauthorized_wrong_org_deleted_and_unconfigured_nodes(self):
        response = self.client.get(self.path, **self.headers)
        self.assertEqual(response.json(), {"policy": None})
        for headers in (
            {},
            {**self.headers, "HTTP_X_ORG_KEY": "other"},
            {**self.headers, "HTTP_X_NODE_TOKEN": "bad"},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.get(self.path, **headers).status_code, 401)
        self.node.is_deleted = True
        self.node.save(update_fields=["is_deleted"])
        self.assertEqual(self.client.get(self.path, **self.headers).status_code, 401)

    def test_reports_are_bounded_versioned_and_do_not_create_tasks(self):
        self.save()
        report = {
            "revision": 1,
            "items": {
                "logLevel": {"state": "applied"},
                "memory": {"state": "monitoring"},
                "bandwidth": {"state": "degraded", "reason": "x" * 1000},
            },
        }
        response = self.client.post(self.path, report, format="json", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.node.refresh_from_db()
        self.assertEqual(
            self.node.metadata["performance_status"]["items"]["logLevel"]["state"],
            "applied",
        )
        self.assertEqual(
            len(
                self.node.metadata["performance_status"]["items"]["bandwidth"]["reason"]
            ),
            300,
        )
        self.assertEqual(NodeTask.objects.count(), 0)
        report["revision"] = 2
        self.assertEqual(
            self.client.post(
                self.path, report, format="json", **self.headers
            ).status_code,
            409,
        )
        self.node.refresh_from_db()
        self.assertEqual(self.node.metadata["performance_status"]["revision"], 1)

    def test_reconcile_newer_cached_revision_with_an_explicit_save(self):
        self.save()
        report = {
            "revision": 1,
            "observed_revision": 50,
            "items": {"cpu": {"state": "degraded"}},
        }
        self.assertEqual(
            self.client.post(
                self.path, report, format="json", **self.headers
            ).status_code,
            200,
        )
        self.node.refresh_from_db()
        self.save()
        self.assertEqual(self.node.metadata["performance_policy"]["revision"], 51)
