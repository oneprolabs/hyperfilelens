from django.test import SimpleTestCase

from apps.protection.conf import _backup_directory_concurrency


class BackupDirectoryConcurrencyTests(SimpleTestCase):
    def test_defaults_and_legacy_values_are_bounded_to_agent_capacity(self):
        self.assertEqual(_backup_directory_concurrency("2"), 2)
        self.assertEqual(_backup_directory_concurrency("serial"), 1)
        self.assertEqual(_backup_directory_concurrency("parallel"), 2)

    def test_invalid_values_fall_back_to_agent_capacity(self):
        for value in ("", "invalid", "0", "3", "99"):
            with self.subTest(value=value):
                self.assertEqual(_backup_directory_concurrency(value), 2)
