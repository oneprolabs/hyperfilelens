"""Managed credential incompatibility is detected before command execution."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.node.models import Node
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_provider import (
    MANAGED_REPOSITORY_AGENT_CAPABILITY,
    MANAGED_REPOSITORY_AGENT_UPGRADE_MESSAGE,
    ManagedRepositoryAgentUpgradeRequired,
    assert_managed_repository_agent_compatible,
)
from apps.storage.services.internal.repository_secrets import (
    build_repository_runtime_payload,
)


class ManagedRepositoryAgentTests(SimpleTestCase):
    def test_missing_or_v1_only_capability_requires_upgrade(self):
        for capabilities in ([], ["repository_s3_temporary_credentials_v1"]):
            with self.subTest(capabilities=capabilities):
                node = Node(metadata={"inventory": {"capabilities": capabilities}})
                with self.assertRaises(ManagedRepositoryAgentUpgradeRequired) as raised:
                    assert_managed_repository_agent_compatible(node)
                self.assertEqual(raised.exception.code, "AGENT_UPGRADE_REQUIRED")
                self.assertEqual(
                    raised.exception.message, MANAGED_REPOSITORY_AGENT_UPGRADE_MESSAGE,
                )

    def test_v2_capability_is_accepted_in_both_inventory_formats(self):
        capabilities = [MANAGED_REPOSITORY_AGENT_CAPABILITY]
        for metadata in (
            {"inventory": {"capabilities": capabilities}},
            {"capabilities": capabilities},
        ):
            with self.subTest(metadata=metadata):
                assert_managed_repository_agent_compatible(Node(metadata=metadata))

    @patch(
        "apps.storage.services.internal.repository_ownership.ownership_payload_for_node",
        return_value={},
    )
    @patch(
        "apps.storage.services.internal.repository_secrets.resolve_repository_secrets",
        return_value={"kopia_password": "test-password"},
    )
    @patch("apps.storage.services.internal.repository_provider.managed_repository_provider")
    def test_runtime_preflight_only_applies_to_managed_agent_execution(
        self, resolve_provider, _secrets, _ownership,
    ):
        repository = Repository(
            id=1, repo_type=Repository.Type.S3, status=Repository.Status.CREATED,
            s3_bucket="test-bucket", config={"endpoint": "https://s3.example.test"},
        )
        target = SimpleNamespace(node=Node(metadata={}))
        provider = Mock()
        provider.runtime_payload.return_value = {"managed": True}
        resolve_provider.return_value = provider

        with self.assertRaises(ManagedRepositoryAgentUpgradeRequired):
            build_repository_runtime_payload(
                repository=repository, execution_target=target,
            )
        provider.runtime_payload.assert_not_called()

        target.node.metadata = {"capabilities": [MANAGED_REPOSITORY_AGENT_CAPABILITY]}
        self.assertEqual(
            build_repository_runtime_payload(
                repository=repository, execution_target=target,
            ),
            {"managed": True},
        )
        # Controller-only callers have no Agent and remain independent of its
        # protocol; the actual Agent delivery still checks authoritative state.
        self.assertEqual(
            build_repository_runtime_payload(repository=repository), {"managed": True},
        )

        resolve_provider.return_value = None
        target.node.metadata = {}
        payload = build_repository_runtime_payload(
            repository=repository, execution_target=target,
        )
        self.assertEqual(payload["type"], "s3")
