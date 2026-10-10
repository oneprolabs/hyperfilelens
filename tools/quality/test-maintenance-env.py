#!/usr/bin/env python3
"""Regression checks for host timezone and maintenance default migration."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('sync_env', Path(__file__).parents[1] / 'config/sync_env.py')
sync_env = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sync_env
spec.loader.exec_module(sync_env)


class MaintenanceEnvironmentTests(unittest.TestCase):
    def test_migrates_old_defaults_without_touching_other_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('KEEP_ME=unchanged\nSTORAGE_MAINTENANCE_TIMEZONE=Asia/Shanghai\nSTORAGE_MAINTENANCE_FULL_INTERVAL_SECONDS=86400\n')
            sync_env.configure_maintenance_defaults(path)
            text = path.read_text()
            self.assertIn('KEEP_ME=unchanged\n', text)
            self.assertIn('STORAGE_MAINTENANCE_FULL_INTERVAL_SECONDS=172800\n', text)
            self.assertIn('STORAGE_MAINTENANCE_TIMEZONE=Asia/Shanghai\n', text)
            before = path.read_bytes()
            sync_env.configure_maintenance_defaults(path)
            self.assertEqual(before, path.read_bytes())

    def test_preserves_custom_operator_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('STORAGE_MAINTENANCE_TIMEZONE=Europe/Berlin\nSTORAGE_MAINTENANCE_FULL_INTERVAL_SECONDS=604800\nSTORAGE_MAINTENANCE_GLOBAL_CONCURRENCY=2\n')
            sync_env.configure_maintenance_defaults(path)
            self.assertIn('STORAGE_MAINTENANCE_FULL_INTERVAL_SECONDS=604800', path.read_text())
            self.assertIn('STORAGE_MAINTENANCE_GLOBAL_CONCURRENCY=2', path.read_text())


if __name__ == '__main__':
    unittest.main()
