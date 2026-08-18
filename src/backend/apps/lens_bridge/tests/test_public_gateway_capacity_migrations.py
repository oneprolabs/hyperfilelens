"""Rollback behavior for Public Gateway capacity unit migrations."""

from importlib import import_module

from django.test import SimpleTestCase


class PublicGatewayCapacityMigrationTests(SimpleTestCase):
    def test_positive_mb_capacity_rounds_up_for_legacy_gib_schema(self):
        modules = (
            "apps.lens_bridge.migrations.0033_gateway_capacity_bytes",
            "apps.subscription.migrations.0010_public_gateway_capacity_bytes",
        )
        expected = {
            -1: -1,
            0: 0,
            500 * 1024**2: 1,
            1024 * 1024**2: 1,
            1536 * 1024**2: 2,
        }

        for module_name in modules:
            convert = import_module(module_name)._bytes_to_legacy_gib
            for capacity_bytes, capacity_gib in expected.items():
                with self.subTest(
                    module=module_name,
                    capacity_bytes=capacity_bytes,
                ):
                    self.assertEqual(convert(capacity_bytes), capacity_gib)
