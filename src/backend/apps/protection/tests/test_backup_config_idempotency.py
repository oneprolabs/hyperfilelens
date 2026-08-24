from __future__ import annotations

import threading
import time

from django.db import close_old_connections
from django.test import TransactionTestCase

from apps.protection.models import BackupConfig, BackupConfigCreateRequest
from apps.protection.services.backup_config_idempotency import (
    execute_idempotent_backup_config_create,
)


class BackupConfigCreateIdempotencyConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_same_key_executes_create_once(self):
        config = BackupConfig.objects.create(
            organization_id=901,
            name="Concurrent idempotency config",
            source_type="agent",
            source_ref_id=902,
            repository_id=903,
            repository_endpoint_type=BackupConfig.RepositoryEndpointType.EXTERNAL,
            compression_level=BackupConfig.CompressionLevel.BALANCED,
            recovery_plan_enabled=False,
        )
        callback_entered = threading.Event()
        release_callback = threading.Event()
        callback_count = 0
        callback_lock = threading.Lock()
        results = []
        errors = []

        def create_result():
            nonlocal callback_count
            with callback_lock:
                callback_count += 1
            callback_entered.set()
            release_callback.wait(timeout=5)
            return config, {"id": config.id, "status": "active"}, 201

        def execute():
            close_old_connections()
            try:
                results.append(
                    execute_idempotent_backup_config_create(
                        organization_id=901,
                        idempotency_key="concurrent-create",
                        data={"source_type": "agent", "source_ref_id": 902},
                        create=create_result,
                    )
                )
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                close_old_connections()

        first = threading.Thread(target=execute)
        second = threading.Thread(target=execute)
        first.start()
        self.assertTrue(callback_entered.wait(timeout=5))
        second.start()
        time.sleep(0.2)
        self.assertEqual(callback_count, 1)
        release_callback.set()
        first.join(timeout=5)
        second.join(timeout=5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(callback_count, 1)
        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(result.replayed for result in results), [False, True])
        self.assertEqual(
            BackupConfigCreateRequest.objects.filter(
                organization_id=901,
                idempotency_key="concurrent-create",
            ).count(),
            1,
        )
