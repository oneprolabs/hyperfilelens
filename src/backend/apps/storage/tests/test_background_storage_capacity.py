from __future__ import annotations

import os
from unittest import mock
from uuid import uuid4

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from redis.exceptions import RedisError

from apps.storage.conf import background_storage_concurrency
from apps.storage.services.internal.background_capacity import (
    BackgroundStorageLease,
    try_acquire_background_storage_capacity,
)
from apps.storage.services.internal.kopia_cli import KopiaControlDecision
from apps.storage.tasks import _controller_execution_control


class BackgroundStorageConfigurationTests(SimpleTestCase):
    def _configured_capacity(self, *, worker: str, background: str | None) -> int:
        with mock.patch.dict(
            os.environ,
            {"CELERY_WORKER_CONCURRENCY": worker},
            clear=False,
        ):
            os.environ.pop("CELERY_BACKGROUND_STORAGE_CONCURRENCY", None)
            if background is not None:
                os.environ["CELERY_BACKGROUND_STORAGE_CONCURRENCY"] = background
            return background_storage_concurrency()

    def test_defaults_to_half_of_worker_concurrency(self):
        self.assertEqual(self._configured_capacity(worker="4", background=None), 2)
        self.assertEqual(self._configured_capacity(worker="3", background=None), 1)

    def test_default_worker_configuration_reserves_one_process(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CELERY_WORKER_CONCURRENCY", None)
            os.environ.pop("CELERY_BACKGROUND_STORAGE_CONCURRENCY", None)
            self.assertEqual(background_storage_concurrency(), 1)

    def test_allows_capacity_above_half_but_below_worker_count(self):
        self.assertEqual(self._configured_capacity(worker="4", background="3"), 3)

    def test_rejects_capacity_that_could_exhaust_every_worker(self):
        with self.assertRaises(ImproperlyConfigured):
            self._configured_capacity(worker="4", background="4")

    def test_rejects_single_process_worker_configuration(self):
        with self.assertRaises(ImproperlyConfigured):
            self._configured_capacity(worker="1", background=None)


class BackgroundStorageLeaseTests(SimpleTestCase):
    @mock.patch(
        "apps.storage.services.internal.background_capacity."
        "background_storage_concurrency",
        return_value=2,
    )
    @mock.patch("apps.storage.services.internal.background_capacity._redis_client")
    def test_acquires_refreshes_and_releases_fenced_slot(
        self,
        redis_client,
        _capacity,
    ):
        client = redis_client.return_value
        client.eval.return_value = 1
        client.zrem.return_value = 1

        lease = try_acquire_background_storage_capacity(
            operation="maintenance",
            identity="repository-7",
        )

        self.assertIsInstance(lease, BackgroundStorageLease)
        self.assertTrue(lease.refresh())
        self.assertTrue(lease.release())
        self.assertIn("maintenance:repository-7:", lease.token)
        client.zrem.assert_called_once_with(
            "hfl:storage:background-capacity",
            lease.token,
        )

    @mock.patch(
        "apps.storage.services.internal.background_capacity."
        "background_storage_concurrency",
        return_value=2,
    )
    @mock.patch("apps.storage.services.internal.background_capacity._redis_client")
    def test_returns_none_when_capacity_is_full(self, redis_client, _capacity):
        redis_client.return_value.eval.return_value = 0

        lease = try_acquire_background_storage_capacity(
            operation="health",
            identity="repository-8",
        )

        self.assertIsNone(lease)

    def test_lost_capacity_lease_stops_controller_maintenance(self):
        lease = mock.Mock(valid=False)
        control = _controller_execution_control(
            repository_task_id=7,
            execution_token=uuid4(),
            heartbeat_interval_seconds=10,
            background_lease=lease,
        )

        self.assertEqual(control(), KopiaControlDecision.LOST_LEASE)

    @mock.patch("apps.storage.services.internal.background_capacity._redis_client")
    def test_temporary_refresh_failure_does_not_immediately_lose_lease(
        self,
        redis_client,
    ):
        redis_client.return_value.eval.side_effect = RedisError("temporary outage")
        lease = BackgroundStorageLease(
            token="maintenance:repository-7:token",
            operation="maintenance",
        )

        self.assertIsNone(lease._refresh_once())
        self.assertTrue(lease.valid)

    @mock.patch("apps.storage.services.internal.background_capacity._redis_client")
    def test_missing_redis_lease_is_authoritative_loss(self, redis_client):
        redis_client.return_value.eval.return_value = 0
        lease = BackgroundStorageLease(
            token="maintenance:repository-7:token",
            operation="maintenance",
        )

        self.assertFalse(lease._refresh_once())
        lease._mark_lost()
        self.assertFalse(lease.valid)

    @mock.patch("apps.storage.services.internal.background_capacity._redis_client")
    def test_coordination_failure_fails_closed(self, redis_client):
        redis_client.return_value.eval.side_effect = RedisError("unavailable")

        lease = try_acquire_background_storage_capacity(
            operation="usage",
            identity="periodic",
        )

        self.assertIsNone(lease)
