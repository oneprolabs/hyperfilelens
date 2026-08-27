from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import SimpleTestCase

from apps.storage.services.internal.kopia_cli import (
    KopiaRepositoryBusyError,
    _repository_config_lock,
)


class KopiaConfigLockTests(SimpleTestCase):
    def test_acquires_and_releases_repository_lock(self):
        with TemporaryDirectory() as temporary_directory:
            config_file = Path(temporary_directory) / "repository.config"

            with _repository_config_lock(config_file):
                self.assertTrue((config_file.parent / ".repository.lock").exists())

    @mock.patch(
        "apps.storage.services.internal.kopia_cli.time.monotonic",
        side_effect=[0.0, 1.0],
    )
    @mock.patch(
        "apps.storage.services.internal.kopia_cli.fcntl.flock",
        side_effect=BlockingIOError,
    )
    def test_reports_busy_after_finite_wait(self, _flock, _monotonic):
        with (
            TemporaryDirectory() as temporary_directory,
            mock.patch.dict(
                os.environ,
                {"HFL_KOPIA_CONFIG_LOCK_TIMEOUT_SECONDS": "1"},
            ),
            self.assertRaises(KopiaRepositoryBusyError),
        ):
            config_file = Path(temporary_directory) / "repository.config"
            with _repository_config_lock(config_file):
                self.fail("busy repository lock must not be acquired")
