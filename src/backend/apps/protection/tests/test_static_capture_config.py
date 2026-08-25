import hashlib
import uuid

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.protection.services.backup_config import _validate_directories
from apps.protection.services.backup_task import _agent_backup_payload


class StaticCaptureConfigTests(SimpleTestCase):
    def captured_rows(self):
        entries = [("file", "/data/a.txt"), ("directory", "/data/empty")]
        manifest_hash = hashlib.sha256(
            "".join(f"{path_type}\0{path}\n" for path_type, path in entries).encode()
        ).hexdigest()
        group_id = str(uuid.uuid4())
        return [
            {
                "path": path,
                "path_type": path_type,
                "scope_mode": "static_recursive_files",
                "capture_group_id": group_id,
                "capture_root": "/data",
                "captured_at": "2026-08-24T09:00:00Z",
                "capture_entry_count": 2,
                "capture_file_count": 1,
                "capture_directory_count": 1,
                "capture_manifest_hash": manifest_hash,
            }
            for path_type, path in entries
        ]

    def test_validates_complete_static_capture_group(self):
        rows = _validate_directories(self.captured_rows())

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["scope_mode"], "static_recursive_files")
        self.assertEqual(rows[0]["capture_entry_count"], 2)
        self.assertEqual(rows[0]["capture_file_count"], 1)

    def test_rejects_partial_static_capture_group(self):
        rows = self.captured_rows()
        rows.pop()

        with self.assertRaisesRegex(ValidationError, "inconsistent"):
            _validate_directories(rows)

    def test_rejects_changed_static_capture_manifest(self):
        rows = self.captured_rows()
        rows[0]["capture_manifest_hash"] = "f" * 64

        with self.assertRaisesRegex(ValidationError, "manifest changed"):
            _validate_directories(rows)

    def test_marks_frozen_empty_directory_in_agent_payload(self):
        payload = _agent_backup_payload(
            source_path="/data/empty",
            backup_config_dir_id=1,
            repository_payload={"type": "filesystem"},
            scope_mode="static_recursive_files",
            path_type="directory",
        )

        self.assertEqual(payload["scope_mode"], "static_recursive_files")
        self.assertEqual(payload["path_type"], "directory")

    def test_keeps_dynamic_agent_payload_wire_compatible(self):
        payload = _agent_backup_payload(
            source_path="/data",
            backup_config_dir_id=1,
            repository_payload={"type": "filesystem"},
        )

        self.assertNotIn("scope_mode", payload)
        self.assertNotIn("path_type", payload)
