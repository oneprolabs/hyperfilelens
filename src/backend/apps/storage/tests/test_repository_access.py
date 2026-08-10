from django.test import SimpleTestCase

from apps.node.models import Node
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_access import (
    explicit_repository_server_host,
)


class RepositoryServerAddressResolutionTests(SimpleTestCase):
    def _repository(self, config=None):
        return Repository(repo_type=Repository.Type.PROXY_FS, config=config or {})

    def _proxy(self, **kwargs):
        defaults = {
            "role": Node.Role.PROXY,
            "name": "proxy",
            "metadata": {},
        }
        defaults.update(kwargs)
        return Node(**defaults)

    def test_legacy_repository_address_has_highest_priority(self):
        repository = self._repository(
            {"proxy_repository_server_host": "legacy.example.test"}
        )
        proxy = self._proxy(
            repository_server_address="override.example.test",
            ip_address="10.0.0.40",
        )

        self.assertEqual(
            explicit_repository_server_host(repository=repository, node=proxy),
            (
                "legacy.example.test",
                "repository.config.proxy_repository_server_host",
            ),
        )

    def test_proxy_override_precedes_agent_reported_host_ip(self):
        repository = self._repository()
        proxy = self._proxy(
            repository_server_address="override.example.test",
            ip_address="10.0.0.40",
        )

        self.assertEqual(
            explicit_repository_server_host(repository=repository, node=proxy),
            ("override.example.test", "node.repository_server_address"),
        )

    def test_agent_reported_host_ip_is_the_default(self):
        repository = self._repository()
        proxy = self._proxy(ip_address="10.0.0.40")

        self.assertEqual(
            explicit_repository_server_host(repository=repository, node=proxy),
            ("10.0.0.40", "node.ip_address"),
        )

    def test_address_is_unavailable_when_proxy_reports_no_usable_address(self):
        repository = self._repository()
        proxy = self._proxy()

        self.assertEqual(
            explicit_repository_server_host(repository=repository, node=proxy),
            ("", ""),
        )
