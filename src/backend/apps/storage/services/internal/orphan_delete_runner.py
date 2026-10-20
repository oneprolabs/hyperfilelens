"""Standalone bounded CLI supervisor. No Django setup, database, or credentials on disk."""

from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys


def _receipt(path: Path, attempt: str, *, phase: str, **details) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as handle:
        os.chmod(temporary, 0o600)
        json.dump({
            "attempt": attempt, "phase": phase,
            "observed_at": datetime.now(timezone.utc).isoformat(), **details,
        }, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def supervise(command: list[str], *, lease_fd: int, timeout: float,
              receipt: Path, attempt: str) -> tuple[int, str]:
    """Keep the kernel lease in both supervisor and child until execution stops."""
    process = None

    def interrupted(_signal, _frame):
        raise InterruptedError

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    if not sys.platform.startswith("linux"):
        raise OSError("Controller deletion supervision requires Linux.")
    libc = ctypes.CDLL(None, use_errno=True)
    supervisor_pid = os.getpid()

    def bind_lifetime():
        # This helper runs only in this single-threaded standalone supervisor,
        # never as a preexec hook in a threaded Django/Celery process.
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
            os._exit(127)
        if os.getppid() != supervisor_pid:  # Close death-before-prctl race.
            os._exit(127)

    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=(lease_fd,), start_new_session=True,
            preexec_fn=bind_lifetime,
        )
        _receipt(receipt, attempt, phase="running", process_id=process.pid)
        return process.wait(timeout=timeout), "completed"
    except subprocess.TimeoutExpired:
        return -signal.SIGKILL, "timeout"
    except InterruptedError:
        return -signal.SIGKILL, "interrupted"
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # If termination cannot be confirmed, do not emit a completion
            # receipt. The child still owns its inherited execution lease.
            process.wait(timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lease-fd", type=int, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--timeout", type=float, required=True)
    args = parser.parse_args()
    request = json.load(sys.stdin)
    try:
        code, outcome = supervise(
            request["command"], lease_fd=args.lease_fd, timeout=args.timeout,
            receipt=args.receipt, attempt=args.attempt,
        )
    except OSError:
        code = 127  # Never expose inherited credentials or raw CLI output.
        outcome = "execution_failed"
    _receipt(args.receipt, args.attempt, phase="finished", exit_code=code, outcome=outcome)


if __name__ == "__main__":
    main()
