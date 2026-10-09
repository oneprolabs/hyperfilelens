"""Structured path access errors and legacy Agent compatibility."""

from django.test import SimpleTestCase

from apps.source.api.views.backup_selectable import _agent_path_access_error_code
from apps.source.services.internal.backup_source_directory import (
    BackupSourceDirectoryError,
)


class PathAccessErrorTests(SimpleTestCase):
    def test_explicit_reason_takes_precedence_over_diagnostic(self) -> None:
        for code in (
            "PATH_OUTSIDE_USER_HOME",
            "PATH_READ_PERMISSION_DENIED",
            "PATH_PERMISSION_DENIED",
        ):
            with self.subTest(code=code):
                error = BackupSourceDirectoryError(
                    "permission denied", agent_error_code=code
                )
                self.assertEqual(
                    _agent_path_access_error_code(error), f"AGENT.{code}"
                )

    def test_legacy_permission_text_stays_generic(self) -> None:
        for message in (
            "Permission denied",
            "access denied",
            "Access is denied",
        ):
            with self.subTest(message=message):
                self.assertEqual(
                    _agent_path_access_error_code(BackupSourceDirectoryError(message)),
                    "AGENT.PATH_PERMISSION_DENIED",
                )

    def test_unrelated_errors_do_not_select_permission_guidance(self) -> None:
        for message, code in (
            ("path not found", ""),
            ("request timed out", ""),
            ("user-level Agent supports only local file protection", ""),
            ("permission denied", "AGENT_PATH_FORBIDDEN"),
            ("permission denied", "PATH_INVALID"),
        ):
            with self.subTest(message=message, code=code):
                self.assertEqual(
                    _agent_path_access_error_code(
                        BackupSourceDirectoryError(message, agent_error_code=code)
                    ),
                    "",
                )
