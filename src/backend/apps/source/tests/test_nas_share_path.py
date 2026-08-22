from django.test import SimpleTestCase

from apps.source.services.internal.nas_share_path import (
    normalize_share_relative_path,
    normalize_user_share_path,
    share_root_label,
    to_mount_path,
    to_share_relative,
)


class NasSharePathTests(SimpleTestCase):
    def test_share_root_label(self):
        self.assertEqual(
            share_root_label(
                resource_type="nas",
                config={"protocol": "nfs", "export_path": "/root/nfs_share"},
            ),
            "/root/nfs_share",
        )
        self.assertEqual(
            share_root_label(
                resource_type="nas",
                config={"protocol": "smb", "share": "MyShare"},
            ),
            "MyShare",
        )

    def test_to_share_relative(self):
        mount_root = "/opt/hyperfilelens-agent/mounts/custom/nfs-share"
        self.assertEqual(to_share_relative(mount_root, mount_root), "/")
        self.assertEqual(to_share_relative(mount_root, f"{mount_root}/bbb"), "/bbb")
        self.assertEqual(
            to_share_relative(mount_root, f"{mount_root}/nfsdir/aaa"),
            "/nfsdir/aaa",
        )

    def test_to_mount_path(self):
        mount_root = "/opt/hyperfilelens-agent/mounts/custom/nfs-share"
        self.assertEqual(to_mount_path(mount_root, "/"), mount_root)
        self.assertEqual(to_mount_path(mount_root, "/bbb"), f"{mount_root}/bbb")
        self.assertEqual(
            to_mount_path(mount_root, f"{mount_root}/nfsdir"),
            f"{mount_root}/nfsdir",
        )

    def test_normalize_user_share_path(self):
        mount_root = "/opt/hyperfilelens-agent/mounts/custom/nfs-share"
        export_path = "/root/nfs_share"
        self.assertEqual(
            normalize_user_share_path(
                mount_root=mount_root,
                export_path=export_path,
                user_path="/bbb",
            ),
            "/bbb",
        )
        self.assertEqual(
            normalize_user_share_path(
                mount_root=mount_root,
                export_path=export_path,
                user_path="/root/nfs_share/bbb",
            ),
            "/bbb",
        )
        self.assertEqual(
            normalize_user_share_path(
                mount_root=mount_root,
                export_path=export_path,
                user_path=f"{mount_root}/nfsdir/aaa",
            ),
            "/nfsdir/aaa",
        )

    def test_normalize_share_relative_path(self):
        self.assertEqual(normalize_share_relative_path(""), "/")
        self.assertEqual(normalize_share_relative_path("bbb"), "/bbb")
        self.assertEqual(normalize_share_relative_path("/nfsdir/../bbb"), "/bbb")
