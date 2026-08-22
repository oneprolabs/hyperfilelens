"""Gateway and Copilot file-selection quota safety tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.subscription.services.quota import (
    assert_gateway_select_within_limits,
    normalize_scope_path,
    relative_scope_path,
    resolve_scope_entry,
    summarize_gateway_select_scopes,
)
from common.errors import AppError
from common.extension_spi import (
    clear_providers_for_tests,
    register_quota_provider,
    restore_providers_for_tests,
)


class _LimitsProvider:
    """QuotaProvider stub exposing only the configured selection limits."""

    def __init__(self, limits: dict[str, int]) -> None:
        self._limits = limits

    def check_quota(self, organization, resource_type: str, additional: int = 1):
        return None

    def get_limits(self, organization) -> dict[str, int]:
        return dict(self._limits)

    def validate_quota(self, organization, quota_type: str, amount: int = 1) -> dict:
        return {"is_valid": True, "quota_type": quota_type}

    def on_license_activated(self, organization, license_obj) -> None:
        return None


class GatewaySelectAccountingTests(SimpleTestCase):
    """Server-side path and occupancy accounting must ignore client claims."""

    def test_normalize_scope_path_unifies_separators(self):
        self.assertEqual(normalize_scope_path("C:\\data\\docs\\"), "C:/data/docs")
        self.assertEqual(normalize_scope_path("/documents/"), "/documents")
        self.assertEqual(
            normalize_scope_path("/documents"),
            normalize_scope_path("\\documents"),
        )

    def test_relative_scope_path(self):
        self.assertEqual(
            relative_scope_path(root="/documents", selected="/documents"), ""
        )
        self.assertEqual(
            relative_scope_path(root="/documents", selected="/documents/a/b.txt"),
            "a/b.txt",
        )
        self.assertEqual(
            relative_scope_path(root="/", selected="/documents/a.txt"),
            "documents/a.txt",
        )
        self.assertEqual(relative_scope_path(root="/documents", selected="/other"), "")

    def test_root_empty_directory_is_known_zero(self):
        directory = SimpleNamespace(
            source_path="/documents", file_count=0, size_bytes=0
        )

        files, size_bytes, unknown = summarize_gateway_select_scopes(
            [{"source_path": "/documents", "path_type": "dir"}],
            [directory],
        )

        self.assertEqual(files, 0)
        self.assertEqual(size_bytes, 0)
        self.assertFalse(unknown)

    def test_root_windows_path_matches_posix_style(self):
        directory = SimpleNamespace(source_path="C:\\docs", file_count=3, size_bytes=30)

        files, size_bytes, unknown = summarize_gateway_select_scopes(
            [{"source_path": "C:/docs", "path_type": "dir"}],
            [directory],
        )

        self.assertEqual(files, 3)
        self.assertEqual(size_bytes, 30)
        self.assertFalse(unknown)

    def test_nested_directory_is_unknown(self):
        directory = SimpleNamespace(
            source_path="/documents", file_count=10, size_bytes=100
        )

        files, size_bytes, unknown = summarize_gateway_select_scopes(
            [{"source_path": "/documents/nested", "path_type": "dir"}],
            [directory],
        )

        self.assertEqual(files, 0)
        self.assertEqual(size_bytes, 0)
        self.assertTrue(unknown)

    def test_unverified_nested_path_is_not_counted_as_file(self):
        directory = SimpleNamespace(
            source_path="/documents", file_count=10, size_bytes=100
        )

        files, size_bytes, unknown = summarize_gateway_select_scopes(
            [{"source_path": "/documents/a.txt", "path_type": "unknown"}],
            [directory],
        )

        self.assertEqual(files, 0)
        self.assertEqual(size_bytes, 0)
        self.assertTrue(unknown)

    def test_resolved_nested_file_counts_size(self):
        directory = SimpleNamespace(
            source_path="/documents", file_count=10, size_bytes=100
        )

        files, size_bytes, unknown = summarize_gateway_select_scopes(
            [
                {
                    "source_path": "/documents/a.txt",
                    "path_type": "file",
                    "size_bytes": 42,
                }
            ],
            [directory],
        )

        self.assertEqual(files, 1)
        self.assertEqual(size_bytes, 42)
        self.assertFalse(unknown)

    def test_nested_file_without_size_is_unknown(self):
        directory = SimpleNamespace(
            source_path="/documents", file_count=10, size_bytes=100
        )

        files, size_bytes, unknown = summarize_gateway_select_scopes(
            [{"source_path": "/documents/a.txt", "path_type": "file"}],
            [directory],
        )

        self.assertEqual(files, 1)
        self.assertEqual(size_bytes, 0)
        self.assertTrue(unknown)

    @patch(
        "apps.protection.services.snapshot_browser.browse_snapshot_directory",
        return_value={
            "entries": [
                {"name": "a.txt", "path": "a.txt", "type": "file", "size_bytes": 99}
            ]
        },
    )
    def test_resolve_nested_file_uses_server_size(self, browse):
        directory = SimpleNamespace(id=7, source_path="/documents", path_type="dir")

        path_type, size_bytes = resolve_scope_entry(
            organization_id=9,
            directory=directory,
            source_path="/documents/a.txt",
            claimed_type="dir",
        )

        self.assertEqual(path_type, "file")
        self.assertEqual(size_bytes, 99)
        browse.assert_called_once()
        self.assertEqual(browse.call_args.kwargs["limit"], 2000)

    @patch(
        "apps.protection.services.snapshot_browser.browse_snapshot_directory",
        return_value={
            "entries": [
                {"name": "a.txt", "path": "a.txt", "type": "file", "size_bytes": None}
            ]
        },
    )
    def test_resolve_nested_file_without_server_size_is_unknown(self, _browse):
        directory = SimpleNamespace(id=7, source_path="/documents", path_type="dir")

        path_type, size_bytes = resolve_scope_entry(
            organization_id=9,
            directory=directory,
            source_path="/documents/a.txt",
            claimed_type="file",
        )

        self.assertEqual(path_type, "file")
        self.assertIsNone(size_bytes)

    def test_resolve_root_ignores_claimed_type(self):
        directory = SimpleNamespace(
            id=1,
            source_path="/documents",
            path_type="dir",
            size_bytes=123,
        )

        resolved = resolve_scope_entry(
            organization_id=1,
            directory=directory,
            source_path="/documents",
            claimed_type="file",
        )

        self.assertEqual(resolved, ("dir", 123))

    @patch(
        "apps.protection.services.snapshot_browser.browse_snapshot_directory",
        return_value={"entries": [{"name": "nested", "path": "nested", "type": "dir"}]},
    )
    def test_resolve_nested_directory_ignores_file_claim(self, _browse):
        directory = SimpleNamespace(id=7, source_path="/documents", path_type="dir")

        resolved = resolve_scope_entry(
            organization_id=9,
            directory=directory,
            source_path="/documents/nested",
            claimed_type="file",
        )

        self.assertEqual(resolved, ("dir", None))

    @patch(
        "apps.protection.services.snapshot_browser.browse_snapshot_directory",
        side_effect=RuntimeError("agent down"),
    )
    def test_resolve_browse_failure_is_unknown(self, _browse):
        directory = SimpleNamespace(id=7, source_path="/documents", path_type="dir")

        resolved = resolve_scope_entry(
            organization_id=9,
            directory=directory,
            source_path="/documents/a.txt",
            claimed_type="file",
        )

        self.assertEqual(resolved, ("unknown", None))


class GatewaySelectLimitTests(SimpleTestCase):
    """Finite Extension limits must fail closed for unprovable selections."""

    def setUp(self):
        self._spi_previous = clear_providers_for_tests()
        self.organization = SimpleNamespace(id=9, key="gateway-select")

    def tearDown(self):
        restore_providers_for_tests(self._spi_previous)

    def test_known_empty_selection_is_allowed_under_finite_limits(self):
        register_quota_provider(
            _LimitsProvider(
                {
                    "gateway_select_max_files": 200,
                    "gateway_select_max_bytes": 2 * 1024**3,
                }
            )
        )

        assert_gateway_select_within_limits(
            organization=self.organization,
            file_count=0,
            size_bytes=0,
            unknown_directory=False,
        )

    def test_unknown_directory_is_rejected_under_finite_limits(self):
        register_quota_provider(
            _LimitsProvider(
                {
                    "gateway_select_max_files": 200,
                    "gateway_select_max_bytes": -1,
                }
            )
        )

        with self.assertRaises(AppError) as ctx:
            assert_gateway_select_within_limits(
                organization=self.organization,
                file_count=0,
                size_bytes=0,
                unknown_directory=True,
            )

        self.assertEqual(ctx.exception.code, "SUBSCRIPTION.QUOTA_EXCEEDED")
        self.assertEqual(ctx.exception.meta["quota_type"], "gateway_select_max_files")

    def test_file_and_byte_limits_are_enforced_independently(self):
        register_quota_provider(
            _LimitsProvider(
                {
                    "gateway_select_max_files": 1,
                    "gateway_select_max_bytes": 10,
                }
            )
        )

        with self.assertRaises(AppError) as files_ctx:
            assert_gateway_select_within_limits(
                organization=self.organization,
                file_count=2,
                size_bytes=1,
            )
        self.assertEqual(
            files_ctx.exception.meta["quota_type"], "gateway_select_max_files"
        )

        with self.assertRaises(AppError) as bytes_ctx:
            assert_gateway_select_within_limits(
                organization=self.organization,
                file_count=1,
                size_bytes=11,
            )
        self.assertEqual(
            bytes_ctx.exception.meta["quota_type"], "gateway_select_max_bytes"
        )
