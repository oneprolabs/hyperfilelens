from unittest import mock

from django.test import SimpleTestCase

from apps.storage.services.internal.repository_execution_lock import (
    repository_execution_lock,
)


class RepositoryExecutionLockTests(SimpleTestCase):
    @mock.patch(
        "apps.storage.services.internal.repository_execution_lock.connection"
    )
    def test_postgres_lock_rejects_second_executor(self, connection):
        connection.vendor = "postgresql"
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (False,)

        with repository_execution_lock(
            operation="s3-repository-cleanup",
            operation_id=42,
        ) as acquired:
            self.assertFalse(acquired)

        self.assertEqual(cursor.execute.call_count, 1)
        self.assertIn("pg_try_advisory_lock", cursor.execute.call_args.args[0])

    @mock.patch(
        "apps.storage.services.internal.repository_execution_lock.connection"
    )
    def test_postgres_lock_is_released_after_operation(self, connection):
        connection.vendor = "postgresql"
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (True,)

        with repository_execution_lock(
            operation="repository-create",
            operation_id=17,
        ) as acquired:
            self.assertTrue(acquired)

        self.assertEqual(cursor.execute.call_count, 2)
        self.assertIn("pg_advisory_unlock", cursor.execute.call_args.args[0])
