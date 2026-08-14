import unittest
from unittest.mock import patch

from apps.lens_bridge import deploy


class LensDeployUrlTest(unittest.TestCase):
    @patch("apps.lens_bridge.deploy.env_int", return_value=11445)
    @patch("apps.lens_bridge.deploy.env_str")
    def test_bundled_console_url_uses_frontend_host_and_console_port(
        self, env_str, _env_int
    ):
        values = {
            "SOURCELENS_CONSOLE_URL": "",
            "SOURCELENS_MODE": "bundled",
            "FRONTEND_URL": "https://console.example.com:11443",
        }
        env_str.side_effect = lambda key, default="": values.get(key, default)

        self.assertEqual(
            deploy.sourcelens_console_url(),
            "https://console.example.com:11445",
        )

    @patch("apps.lens_bridge.deploy.env_int", return_value=11445)
    @patch("apps.lens_bridge.deploy.env_str")
    def test_bundled_console_url_supports_ipv6(self, env_str, _env_int):
        values = {
            "SOURCELENS_CONSOLE_URL": "",
            "SOURCELENS_MODE": "bundled",
            "FRONTEND_URL": "https://[2001:db8::1]:11443",
        }
        env_str.side_effect = lambda key, default="": values.get(key, default)

        self.assertEqual(
            deploy.sourcelens_console_url(),
            "https://[2001:db8::1]:11445",
        )

    @patch("apps.lens_bridge.deploy.env_str")
    def test_external_console_url_uses_explicit_service_url(self, env_str):
        values = {
            "SOURCELENS_CONSOLE_URL": "",
            "SOURCELENS_MODE": "external",
            "LENS_BASE_URL": "https://sourcelens.example.com/",
        }
        env_str.side_effect = lambda key, default="": values.get(key, default)

        self.assertEqual(
            deploy.sourcelens_console_url(),
            "https://sourcelens.example.com",
        )

    @patch("apps.lens_bridge.deploy.env_str")
    def test_explicit_console_url_takes_precedence(self, env_str):
        values = {
            "SOURCELENS_CONSOLE_URL": "https://lens-console.example.com/admin/",
        }
        env_str.side_effect = lambda key, default="": values.get(key, default)

        self.assertEqual(
            deploy.sourcelens_console_url(),
            "https://lens-console.example.com/admin",
        )

    @patch("apps.lens_bridge.deploy.env_str")
    def test_lens_gateway_base_url_defaults_to_frontend_sourcelens(self, env_str):
        def side_effect(key, default=""):
            values = {
                "SOURCELENS_MODE": "bundled",
                "LENS_GATEWAY_BASE_URL": "",
                "FRONTEND_URL": "https://console.example.com",
                "LENS_BASE_URL": "http://host.docker.internal:20080",
            }
            return values.get(key, default)

        env_str.side_effect = side_effect
        self.assertEqual(
            deploy.lens_gateway_base_url(),
            "https://console.example.com/sourcelens",
        )

    @patch("apps.lens_bridge.deploy.env_str")
    def test_lens_gateway_base_url_rewrites_docker_internal(self, env_str):
        def side_effect(key, default=""):
            values = {
                "SOURCELENS_MODE": "bundled",
                "LENS_GATEWAY_BASE_URL": "",
                "FRONTEND_URL": "",
                "LENS_BASE_URL": "http://host.docker.internal:20080",
            }
            return values.get(key, default)

        env_str.side_effect = side_effect
        self.assertEqual(deploy.lens_gateway_base_url(), "http://127.0.0.1:20080")

    @patch("apps.lens_bridge.deploy.env_str")
    def test_lens_gateway_base_url_honors_explicit_override(self, env_str):
        def side_effect(key, default=""):
            values = {
                "SOURCELENS_MODE": "bundled",
                "LENS_GATEWAY_BASE_URL": "http://192.168.1.10:20080",
                "FRONTEND_URL": "https://console.example.com",
                "LENS_BASE_URL": "http://host.docker.internal:20080",
            }
            return values.get(key, default)

        env_str.side_effect = side_effect
        self.assertEqual(deploy.lens_gateway_base_url(), "http://192.168.1.10:20080")

    @patch("apps.lens_bridge.deploy.env_str")
    def test_lens_gateway_base_url_passthrough_localhost(self, env_str):
        def side_effect(key, default=""):
            values = {
                "SOURCELENS_MODE": "bundled",
                "LENS_GATEWAY_BASE_URL": "",
                "FRONTEND_URL": "",
                "LENS_BASE_URL": "http://localhost:20080",
            }
            return values.get(key, default)

        env_str.side_effect = side_effect
        self.assertEqual(deploy.lens_gateway_base_url(), "http://localhost:20080")

    @patch("apps.lens_bridge.deploy.env_str")
    def test_external_mode_uses_lens_base_url_for_gateways(self, env_str):
        def side_effect(key, default=""):
            values = {
                "SOURCELENS_MODE": "external",
                "LENS_GATEWAY_BASE_URL": "",
                "FRONTEND_URL": "https://console.example.com",
                "LENS_BASE_URL": "https://sourcelens.example.com",
            }
            return values.get(key, default)

        env_str.side_effect = side_effect
        self.assertEqual(
            deploy.lens_gateway_base_url(),
            "https://sourcelens.example.com",
        )

    @patch("apps.lens_bridge.deploy.env_str")
    def test_bundled_gateway_uses_control_plane_relative_path(self, env_str):
        env_str.side_effect = lambda key, default="": (
            "bundled" if key == "SOURCELENS_MODE" else default
        )

        self.assertEqual(deploy.lens_gateway_base_path(), "/sourcelens")

    @patch("apps.lens_bridge.deploy.env_str")
    def test_external_gateway_does_not_override_absolute_url(self, env_str):
        env_str.side_effect = lambda key, default="": (
            "external" if key == "SOURCELENS_MODE" else default
        )

        self.assertEqual(deploy.lens_gateway_base_path(), "")

    @patch("apps.lens_bridge.deploy.env_int", return_value=12443)
    @patch("apps.lens_bridge.deploy.env_str")
    def test_local_platform_gateway_uses_loopback_for_bundled_mode(
        self, env_str, _env_int
    ):
        env_str.side_effect = lambda key, default="": (
            "bundled" if key == "SOURCELENS_MODE" else default
        )
        self.assertEqual(
            deploy.local_platform_lens_gateway_base_url(),
            "https://127.0.0.1:12443/sourcelens",
        )

    @patch("apps.lens_bridge.deploy.env_str")
    def test_local_platform_gateway_keeps_external_sourcelens_url(self, env_str):
        def side_effect(key, default=""):
            values = {
                "SOURCELENS_MODE": "external",
                "LENS_GATEWAY_BASE_URL": "https://sourcelens.example.com",
            }
            return values.get(key, default)

        env_str.side_effect = side_effect
        self.assertEqual(
            deploy.local_platform_lens_gateway_base_url(),
            "https://sourcelens.example.com",
        )
