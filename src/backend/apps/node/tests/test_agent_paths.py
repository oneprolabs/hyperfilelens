from django.test import SimpleTestCase

from apps.node.agent_paths import (
    agent_mounts_dir,
    repository_mount_point,
    require_agent_mount_path,
    source_mount_point,
)


class AgentPathsTests(SimpleTestCase):
    def test_repository_mount_point(self):
        self.assertEqual(
            repository_mount_point(42, node_id=3),
            "/opt/hyperfilelens-agent/mounts/repositories/repo-42-node-3",
        )

    def test_source_mount_point(self):
        self.assertEqual(
            source_mount_point(12),
            "/opt/hyperfilelens-agent/mounts/sources/source-12",
        )

    def test_require_agent_mount_path_accepts_canonical_path(self):
        canonical = f"{agent_mounts_dir()}/custom/smb-media"
        self.assertEqual(require_agent_mount_path(canonical), canonical)

    def test_require_agent_mount_path_rejects_legacy_path(self):
        with self.assertRaises(ValueError):
            require_agent_mount_path("/mnt/nas/data")
