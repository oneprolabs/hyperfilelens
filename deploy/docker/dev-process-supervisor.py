#!/usr/bin/env python3
"""Restart one development process on source changes or unexpected exits."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time

from watchfiles import watch


LOGGER = logging.getLogger("hfl-dev-supervisor")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="append", required=True, dest="watch_paths")
    parser.add_argument("--ignore", action="append", default=[], dest="ignore_paths")
    parser.add_argument("--max-restarts", type=int, default=5)
    parser.add_argument("--stable-seconds", type=float, default=30.0)
    parser.add_argument("--base-delay", type=float, default=1.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def _is_relevant_change(
    changed_path: str,
    *,
    ignored_paths: tuple[Path, ...],
) -> bool:
    path = Path(changed_path).resolve()
    if path.suffix != ".py":
        return False
    return not any(
        path == ignored or ignored in path.parents for ignored in ignored_paths
    )


def _watch_changes(
    *,
    watch_paths: tuple[Path, ...],
    ignored_paths: tuple[Path, ...],
    events: queue.Queue[str],
    stop_event: threading.Event,
) -> None:
    def notify(event: str) -> None:
        try:
            events.put_nowait(event)
        except queue.Full:
            pass

    try:
        for batch in watch(*watch_paths, stop_event=stop_event):
            if any(
                _is_relevant_change(path, ignored_paths=ignored_paths)
                for _change, path in batch
            ):
                notify("change")
    except Exception:
        LOGGER.exception("source watcher failed")
        notify("watcher_failed")


def _stop_process(process: subprocess.Popen[bytes], *, timeout: float = 10.0) -> None:
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return

    if process.poll() is None:
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass

    # The session leader can exit before descendants. Always fence the owned
    # process group before launching a replacement; ProcessLookupError means it
    # already drained cleanly.
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        process.wait(timeout=5)


def _start_process(command: list[str]) -> subprocess.Popen[bytes]:
    LOGGER.info("starting child: %s", " ".join(command))
    return subprocess.Popen(
        command,
        env=os.environ.copy(),
        start_new_session=True,
    )


def supervise(
    *,
    command: list[str],
    watch_paths: tuple[Path, ...],
    ignored_paths: tuple[Path, ...],
    max_restarts: int,
    stable_seconds: float,
    base_delay: float,
) -> int:
    """Run and restart ``command`` while watching Python source paths."""
    events: queue.Queue[str] = queue.Queue(maxsize=1)
    stop_event = threading.Event()
    stopping = False
    process: subprocess.Popen[bytes] | None = None

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        stop_event.set()
        if process is not None:
            _stop_process(process)

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    watcher = threading.Thread(
        target=_watch_changes,
        kwargs={
            "watch_paths": watch_paths,
            "ignored_paths": ignored_paths,
            "events": events,
            "stop_event": stop_event,
        },
        daemon=True,
    )
    watcher.start()

    failures = 0
    process_started_at = 0.0
    exit_code = 0
    try:
        process = _start_process(command)
        process_started_at = time.monotonic()
        while not stopping:
            try:
                event = events.get(timeout=0.25)
            except queue.Empty:
                event = ""
            else:
                if event == "watcher_failed":
                    LOGGER.error("source watcher stopped; exiting container")
                    return 2
                LOGGER.info("Python source changed; restarting child")
                _stop_process(process)
                while not events.empty():
                    events.get_nowait()
                failures = 0
                process = _start_process(command)
                process_started_at = time.monotonic()
                continue

            current_exit = process.poll()
            if current_exit is None:
                if time.monotonic() - process_started_at >= stable_seconds:
                    failures = 0
                continue
            exit_code = current_exit
            # The session leader may exit before a descendant. Always clean the
            # owned process group before starting a replacement.
            _stop_process(process)

            runtime = time.monotonic() - process_started_at
            failures = 1 if runtime >= stable_seconds else failures + 1
            if failures > max(0, max_restarts):
                LOGGER.error(
                    "child failed too often; exiting status=%s failures=%s",
                    exit_code,
                    failures,
                )
                return exit_code or 1
            delay = max(0.0, base_delay) * min(2 ** (failures - 1), 8)
            LOGGER.warning(
                "child exited status=%s; retrying in %.1fs (%s/%s)",
                exit_code,
                delay,
                failures,
                max_restarts,
            )
            if stop_event.wait(delay):
                break
            process = _start_process(command)
            process_started_at = time.monotonic()
    finally:
        stop_event.set()
        if process is not None:
            _stop_process(process)
        watcher.join(timeout=2)
    return exit_code


def main() -> int:
    """Parse CLI arguments and supervise the configured process."""
    logging.basicConfig(level=logging.INFO, format="[dev-supervisor] %(message)s")
    args = _parser().parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("a child command is required after --")
    return supervise(
        command=command,
        watch_paths=tuple(Path(path).resolve() for path in args.watch_paths),
        ignored_paths=tuple(Path(path).resolve() for path in args.ignore_paths),
        max_restarts=max(0, int(args.max_restarts)),
        stable_seconds=max(1.0, float(args.stable_seconds)),
        base_delay=max(0.0, float(args.base_delay)),
    )


if __name__ == "__main__":
    raise SystemExit(main())
