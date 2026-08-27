from django.test import SimpleTestCase

from apps.node.services.internal.repository_server import (
    repository_server_diagnostic_code,
    repository_server_public_error_message,
)


class RepositoryServerDiagnosticTests(SimpleTestCase):
    def test_structured_diagnostic_has_safe_public_message(self):
        code = repository_server_diagnostic_code(
            {"error_code": "REPOSITORY_SERVER_READY_TIMEOUT"},
            "sensitive internal detail",
        )

        self.assertEqual(code, "REPOSITORY_SERVER_READY_TIMEOUT")
        self.assertIn("did not make", repository_server_public_error_message(code))

    def test_legacy_tls_timeout_is_mapped_for_old_agents(self):
        code = repository_server_diagnostic_code(
            {},
            "kopia server did not create TLS certificate within 15s: internal log",
        )

        self.assertEqual(code, "REPOSITORY_SERVER_AGENT_UPGRADE_REQUIRED")
        self.assertIn("Upgrade", repository_server_public_error_message(code))

    def test_structured_code_is_recovered_from_last_error(self):
        code = repository_server_diagnostic_code(
            {},
            "REPOSITORY_SERVER_PROCESS_EXITED: sensitive internal detail",
        )

        self.assertEqual(code, "REPOSITORY_SERVER_PROCESS_EXITED")

    def test_legacy_port_exhaustion_is_mapped(self):
        code = repository_server_diagnostic_code(
            {},
            "no available Repository Server port in TCP range 51515-52014",
        )

        self.assertEqual(code, "REPOSITORY_SERVER_PORT_UNAVAILABLE")

    def test_unknown_failure_does_not_receive_an_incorrect_mapping(self):
        self.assertEqual(
            repository_server_diagnostic_code({}, "unrelated failure"),
            "",
        )
