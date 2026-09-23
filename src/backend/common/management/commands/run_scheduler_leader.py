"""Run Celery Beat only while holding the cluster-wide PostgreSQL leader lock."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection


_SCHEDULER_LOCK_ID = 0x48464C534348  # "HFLSCH", stable across every HFL host.
_STANDBY_FILE = Path("/tmp/hfl-scheduler-standby")
_HEARTBEAT_KEY = "hyperfilelens:runtime:scheduler-heartbeat"


def touch_scheduler_heartbeat() -> None:
    try:
        import redis

        url = getattr(settings, "REDIS_URL", "") or os.getenv("REDIS_URL", "")
        if not url:
            return
        redis.from_url(url, socket_connect_timeout=2).set(
            _HEARTBEAT_KEY,
            str(time.time()),
            ex=30,
        )
    except Exception:
        # Redis health is reported independently; a transient heartbeat write
        # failure must not terminate the scheduler.
        return


class Command(BaseCommand):
    help = "Run Celery Beat under a PostgreSQL advisory leader lock."

    def add_arguments(self, parser):
        parser.add_argument("--retry-seconds", type=int, default=5)

    def handle(self, *args, **options):
        retry_seconds = max(1, int(options["retry_seconds"]))
        child: subprocess.Popen[str] | None = None
        stopping = False

        def stop(_signum, _frame):
            nonlocal stopping
            stopping = True
            if child is not None and child.poll() is None:
                child.terminate()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        while not stopping:
            connection.close()
            connection.ensure_connection()
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [_SCHEDULER_LOCK_ID])
                acquired = bool(cursor.fetchone()[0])
            if not acquired:
                _STANDBY_FILE.touch()
                self.stdout.write("Scheduler leader lock is held by another instance")
                touch_scheduler_heartbeat()
                time.sleep(retry_seconds)
                continue

            _STANDBY_FILE.unlink(missing_ok=True)
            self.stdout.write(self.style.SUCCESS("Scheduler leader lock acquired"))
            touch_scheduler_heartbeat()
            command = [
                "celery",
                "-A",
                "common",
                "beat",
                "--scheduler",
                "common.scheduling.scheduler:CoalescingDatabaseScheduler",
                "--loglevel=INFO",
            ]
            child = subprocess.Popen(command, env=os.environ.copy(), text=True)
            try:
                while child.poll() is None and not stopping:
                    # Keep the advisory-lock session alive and fail closed if it is lost.
                    time.sleep(retry_seconds)
                    touch_scheduler_heartbeat()
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1")
                        cursor.fetchone()
            except Exception:
                self.stderr.write("Scheduler leader database session was lost")
                if child.poll() is None:
                    child.terminate()
            finally:
                try:
                    if child.poll() is None:
                        child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT pg_advisory_unlock(%s)", [_SCHEDULER_LOCK_ID]
                        )
                except Exception:
                    pass
                connection.close()
                child = None

            if not stopping:
                time.sleep(retry_seconds)

        _STANDBY_FILE.unlink(missing_ok=True)
