from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.iam.models import Organization
from apps.node.models import Node
from apps.protection.services.repository_compatibility import (
    validate_backup_repository_compatible,
)
from apps.storage.repositories.models import Repository


class DirectNasRepositoryCompatibilityTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="direct-nas-compatibility-org",
            name="Direct NAS Compatibility Org",
        )

    def _agent(self, *, name: str, os_name: str = "", metadata: dict | None = None):
        return Node.objects.create(
            organization=self.org,
            name=name,
            role=Node.Role.AGENT,
            os_name=os_name,
            metadata=metadata or {},
        )

    def _nas_repository(
        self,
        *,
        name: str,
        protocol: str,
        proxy: Node | None = None,
    ):
        return Repository.objects.create(
            organization_id=self.org.id,
            name=name,
            repo_type=Repository.Type.NAS,
            nas_protocol=protocol,
            status=Repository.Status.CREATED,
            health=Repository.Health.UNVERIFIED,
            bind_node_type=(Repository.BindNodeType.PROXY if proxy else None),
            bind_node_id=(proxy.id if proxy else None),
            config={
                "server_address": "10.0.0.30",
                "share_path": "/backup",
                "kopia_password": "test-kopia-password",
            },
        )

    def _validate(self, *, agent: Node, repository: Repository):
        return validate_backup_repository_compatible(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=agent.id,
            repository_id=repository.id,
        )

    def test_confirmed_linux_agent_can_use_direct_nfs_and_smb(self):
        agent = self._agent(
            name="linux-agent",
            metadata={"inventory": {"platform": "linux"}},
        )

        for protocol in (Repository.NasProtocol.NFS, Repository.NasProtocol.SMB):
            with self.subTest(protocol=protocol):
                repository = self._nas_repository(
                    name=f"direct-{protocol}-linux",
                    protocol=protocol,
                )
                self.assertEqual(self._validate(agent=agent, repository=repository), repository)

    def test_non_linux_or_unknown_agent_cannot_use_direct_nas(self):
        platforms = (
            ("windows", "Windows Server 2022"),
            ("macos", "Darwin 24.0"),
            ("unknown", ""),
        )

        for platform, os_name in platforms:
            agent = self._agent(name=f"{platform}-agent", os_name=os_name)
            for protocol in (Repository.NasProtocol.NFS, Repository.NasProtocol.SMB):
                with self.subTest(platform=platform, protocol=protocol):
                    repository = self._nas_repository(
                        name=f"direct-{protocol}-{platform}",
                        protocol=protocol,
                    )
                    with self.assertRaisesMessage(
                        ValidationError,
                        "Direct NAS repositories are compatible only with Linux Host sources.",
                    ):
                        self._validate(agent=agent, repository=repository)

    def test_proxy_bound_nas_remains_available_to_non_linux_agents(self):
        windows_agent = self._agent(
            name="proxy-windows-agent",
            os_name="Windows Server 2022",
        )
        proxy = Node.objects.create(
            organization=self.org,
            name="repository-proxy",
            role=Node.Role.PROXY,
        )

        for protocol in (Repository.NasProtocol.NFS, Repository.NasProtocol.SMB):
            with self.subTest(protocol=protocol):
                repository = self._nas_repository(
                    name=f"proxy-{protocol}-windows",
                    protocol=protocol,
                    proxy=proxy,
                )
                self.assertEqual(
                    self._validate(agent=windows_agent, repository=repository),
                    repository,
                )
