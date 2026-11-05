"""The cache baseline upgrade is tenant-wide and leaves resource policy alone."""

from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from apps.iam.models import Organization
from apps.node.models import Node
from apps.node.services.internal.repository_cache_policy import node_kopia_cache_size_mb

upgrade = import_module(
    "apps.node.migrations.0024_raise_repository_cache_baseline"
).raise_repository_cache_baseline


class RepositoryCacheMigrationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="cache-one", name="One")
        self.other = Organization.objects.create(key="cache-two", name="Two")

    def migrate(self):
        upgrade(apps, SimpleNamespace(connection=connection))

    def node(self, metadata=None, **kwargs):
        return Node.all_objects.create(
            organization=kwargs.pop("organization", self.org),
            name="Cache upgrade",
            role=kwargs.pop("role", "agent"),
            metadata=metadata if metadata is not None else {},
            **kwargs,
        )

    def test_cache_only_upgrade_across_tenants_and_offline_nodes(self):
        nodes = [
            self.node(organization=org, role=role, availability="offline")
            for org in (self.org, self.other) for role in ("agent", "proxy")
        ]
        legacy = self.node({"perf_settings": {
            "kopia_cache_mb": 16384, "cpuCores": 7, "bandwidth": 15,
        }, "inventory": {"hostname": "original"}})
        self.migrate()
        for node in [*nodes, legacy]:
            node.refresh_from_db()
            expected = 16384 if node.pk == legacy.pk else 8192
            self.assertEqual(node_kopia_cache_size_mb(node), expected)
            self.assertEqual(node.metadata["perf_settings"]["kopiaCacheMb"], expected)
            self.assertNotIn("performance_policy", node.metadata)
        self.assertEqual(legacy.metadata["perf_settings"]["cpuCores"], 7)
        self.assertEqual(legacy.metadata["perf_settings"]["kopia_cache_mb"], 16384)
        self.assertEqual(legacy.metadata["inventory"], {"hostname": "original"})

    def test_existing_policy_values_revision_and_unrelated_settings(self):
        cases = [None, 0, 1024, 4096, 8192, 16384, 65536, -1, 65537, True, "8192", 8192.0]
        for value in cases:
            with self.subTest(value=value):
                settings = {
                    "cpuCores": 4, "memCapMb": 2048, "bandwidth": 12,
                    "log": "info", "logRetention": 14, "logSpaceMb": 512,
                }
                if value is not None:
                    settings["kopiaCacheMb"] = value
                node = self.node({
                    "performance_policy": {"revision": 2, "settings": settings},
                    "performance_status": {"revision": 2, "observed_revision": 5},
                    "perf_settings": deepcopy(settings),
                    "unrelated": {"keep": True},
                })
                self.migrate()
                node.refresh_from_db()
                expected = value if type(value) is int and 8192 <= value <= 65536 else 8192
                policy = node.metadata["performance_policy"]
                self.assertEqual(node_kopia_cache_size_mb(node), expected)
                self.assertEqual(node.metadata["perf_settings"]["kopiaCacheMb"], expected)
                self.assertEqual(
                    {k: v for k, v in policy["settings"].items() if k != "kopiaCacheMb"},
                    {k: v for k, v in settings.items() if k != "kopiaCacheMb"},
                )
                self.assertEqual(node.metadata["unrelated"], {"keep": True})
                if type(value) is int and value == expected:
                    self.assertEqual(policy["revision"], 2)
                    self.assertIn("performance_status", node.metadata)
                else:
                    self.assertEqual(policy["revision"], 6)
                    self.assertNotIn("performance_status", node.metadata)
                snapshot = deepcopy(node.metadata)
                self.migrate()
                node.refresh_from_db()
                self.assertEqual(node.metadata, snapshot)

    def test_gateway_and_deleted_nodes_unchanged(self):
        excluded = [
            self.node(role="gateway"),
            self.node(is_deleted=True),
            self.node(role="proxy", is_deleted=True),
        ]
        self.migrate()
        for node in excluded:
            node.refresh_from_db()
            self.assertEqual(node.metadata, {})

    def test_user_can_lower_or_disable_cache_after_upgrade(self):
        node = self.node()
        self.migrate()
        node.refresh_from_db()
        for value in (0, 1024, 16384):
            node.metadata["performance_policy"] = {
                "revision": 1, "settings": {"kopiaCacheMb": value},
            }
            self.assertEqual(node_kopia_cache_size_mb(node), value)


class RepositoryCacheUpgradeExecutionTests(TransactionTestCase):
    """Exercise historical model state and Django's one-time migration marker."""

    def test_repeated_upgrade_preserves_later_user_changes(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [("node", "0023_platform_token_owner_constraint")]
        after = [("node", "0024_raise_repository_cache_baseline")]
        try:
            executor.migrate(before)
            old_apps = executor.loader.project_state(before).apps
            Organization = old_apps.get_model("iam", "Organization")
            HistoricalNode = old_apps.get_model("node", "Node")
            org = Organization.objects.create(key="historical-cache", name="Historical")
            node = HistoricalNode.objects.create(
                organization_id=org.pk, name="Offline proxy", role="proxy",
                metadata={"perf_settings": {"kopiaCacheMb": 0}},
            )
            MigrationExecutor(connection).migrate(after)
            node.refresh_from_db()
            self.assertEqual(node.metadata["perf_settings"]["kopiaCacheMb"], 8192)
            self.assertNotIn("performance_policy", node.metadata)
            node.metadata["perf_settings"]["kopiaCacheMb"] = 0
            node.save(update_fields=["metadata"])
            MigrationExecutor(connection).migrate(after)
            node.refresh_from_db()
            self.assertEqual(node.metadata["perf_settings"]["kopiaCacheMb"], 0)
        finally:
            MigrationExecutor(connection).migrate(latest)
