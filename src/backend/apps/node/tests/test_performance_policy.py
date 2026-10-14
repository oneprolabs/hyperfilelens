"""Pure policy-contract tests do not require a production database."""

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.node.services.internal.performance_policy import (
    merge_performance_metadata,
    normalize_report,
    normalize_settings,
)


class PerformancePolicyTests(SimpleTestCase):
    def settings(self):
        return {
            "cpuCores": 2,
            "memCapMb": 2048,
            "bandwidth": 20,
            "kopiaCacheMb": 1024,
            "log": "info",
            "logRetention": 30,
            "logSpaceMb": 512,
        }

    def test_legacy_values_are_not_activated_without_explicit_save(self):
        current = {"perf_settings": self.settings(), "inventory": {"cpu_cores": 8}}
        updated = merge_performance_metadata(current, {"label": "node"}, role="agent")
        self.assertNotIn("performance_policy", updated)
        self.assertEqual(updated["inventory"], current["inventory"])
        self.assertNotIn("label", current)

    def test_explicit_save_is_versioned_idempotent_and_protected(self):
        initial = {"inventory": {"cpu_cores": 8}, "performance_status": {"revision": 0}}
        first = merge_performance_metadata(
            initial,
            {
                "perf_settings": self.settings(),
                "performance_policy": {"revision": 999},
                "performance_status": {"items": {"cpu": {"state": "applied"}}},
            },
            role="agent",
        )
        self.assertEqual(first["performance_policy"]["revision"], 1)
        self.assertNotIn("performance_status", first)
        second = merge_performance_metadata(
            first, {"perf_settings": self.settings()}, role="agent"
        )
        self.assertEqual(second["performance_policy"], first["performance_policy"])
        changed = self.settings()
        changed["memCapMb"] = 2050
        third = merge_performance_metadata(
            second, {"perf_settings": changed}, role="agent"
        )
        self.assertEqual(third["performance_policy"]["revision"], 2)
        self.assertEqual(third["performance_policy"]["settings"]["memCapMb"], 2050)

    def test_rejects_boolean_fraction_unknown_and_out_of_range_values(self):
        for key, value in (
            ("cpuCores", True),
            ("cpuCores", 1.5),
            ("memCapMb", 127),
            ("bandwidth", 0),
            ("log", "trace"),
            ("logSpaceMb", 8193),
            ("kopiaCacheMb", 65537),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ValidationError):
                raw = self.settings()
                raw[key] = value
                normalize_settings(raw, role="agent")
        with self.assertRaises(ValidationError):
            normalize_settings(
                {**self.settings(), "command": "do-not-execute"}, role="agent"
            )

    def test_gateway_does_not_enable_repository_cache(self):
        normalized = normalize_settings(self.settings(), role="gateway")
        self.assertNotIn("kopiaCacheMb", normalized)

    def test_host_bounds_and_report_revision(self):
        with self.assertRaises(ValidationError):
            merge_performance_metadata(
                {"inventory": {"cpu_cores": 1}},
                {"perf_settings": self.settings()},
                role="agent",
            )
        for revision in (True, 0, 2):
            with self.subTest(revision=revision), self.assertRaises(ValidationError):
                normalize_report({"revision": revision, "items": {}}, revision=1)
        report = normalize_report(
            {
                "revision": 1,
                "items": {
                    "cpu": {"state": "unsupported", "reason": "x" * 1000},
                    "memory": {"state": "monitoring"},
                },
            },
            revision=1,
        )
        self.assertEqual(len(report["items"]["cpu"]["reason"]), 300)
        self.assertEqual(report["items"]["memory"]["state"], "monitoring")

    def test_registration_preserves_console_policy_and_ignores_agent_injection(self):
        from apps.node.services.internal.local_platform_gateway import (
            registration_metadata,
        )

        existing = {
            "perf_settings": self.settings(),
            "performance_policy": {"revision": 2},
            "performance_status": {"revision": 2},
        }
        result = registration_metadata(
            {"performance_policy": {"revision": 999}, "inventory": {"cpu_cores": 8}},
            existing_metadata=existing,
        )
        self.assertEqual(result["performance_policy"], {"revision": 2})
        self.assertEqual(result["perf_settings"], self.settings())
        new = registration_metadata({"performance_policy": {"revision": 999}})
        self.assertNotIn("performance_policy", new)
