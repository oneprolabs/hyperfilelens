#!/usr/bin/env python3
"""Render hfl-enroll JSON events as the dev stack's terminal log format.

The enrollment binary remains responsible for the standalone rich/plain
experience.  This adapter is only used when the dev stack embeds the Gateway
installer and therefore needs stable, single-line HFL records.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone


STATUS = {
    "OK": " OK ",
    " OK ": " OK ",
    "....": "....",
    "STEP": "....",
    "WARN": "WARN",
    "FAIL": "FAIL",
    "ERROR": "FAIL",
    "INFO": "INFO",
    "SKIP": "SKIP",
    "OUT": "OUT ",
    "OUT ": "OUT ",
}

ANSI = {
    " OK ": "\033[32m",
    "....": "\033[35m",
    "WARN": "\033[33m",
    "FAIL": "\033[31m",
    "SKIP": "\033[36m",
    "INFO": "\033[36m",
    "OUT ": "\033[36m",
}


def color_enabled() -> bool:
    return (
        os.environ.get("HFL_RENDER_COLOR") == "1"
        and os.environ.get("TERM", "") != "dumb"
        and not os.environ.get("NO_COLOR")
    )


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def emit(status: str, component: str, message: str) -> None:
    message = " ".join(message.strip().split())
    if not message:
        return
    token = STATUS.get(status, status)
    if color_enabled() and token in ANSI:
        token = f"{ANSI[token]}{token}\033[0m"
    print(f"[{timestamp()}] [{token}] [{component}] {message}")


def render_json(event: dict[str, object]) -> None:
    event_type = event.get("type")
    if event_type == "install_target":
        emit("STEP", "gateway", "Preparing Gateway host")
        return
    if event_type == "install_phase":
        emit(str(event.get("status", "STEP")), "gateway", str(event.get("message", "")))
        return
    if event_type == "install_event":
        emit(str(event.get("status", "INFO")), "gateway", str(event.get("message", "")))
        return
    if event_type in {"install_result", "gateway_result"}:
        result = str(event.get("result", ""))
        if result == "failed":
            emit("FAIL", "gateway", str(event.get("reason", "Gateway installation failed")))
        # The successful result is rendered once in the final dev summary.
        return


def render_plain(line: str) -> None:
    line = line.strip()
    if not line:
        return
    if line.startswith("Container "):
        emit("OUT", "docker", line)
        return
    if line in {
        "Target",
        "Preflight checks",
        "Installation summary",
        "Next step",
        "Useful commands",
        "Installing AI engine",
    }:
        if line == "Installing AI engine":
            emit("STEP", "gateway", line)
        return
    match = re.match(r"^\[([^]]+)\]\s*(.*)$", line)
    if match and match.group(1).strip() in STATUS:
        emit(match.group(1).strip(), "gateway", match.group(2))
        return
    # Rich installer detail values (root, amd64, free-space figures, paths and
    # command hints) are intentionally omitted from the compact dev console.
    if re.match(r"^(Console|Organization|Role|Hostname|Platform|Node ID|Agent version|Service state|AI engine|Console state|Install path|Data path|Log file|Data removal|Next step)\s+", line):
        return
    if line.startswith(("Traceback", "Error", "ERROR", "fatal", "FATAL", "curl:", "docker:")):
        emit("FAIL", "gateway", line)
        return
    # A helper can still fall back to plain text when an older Agent package
    # does not understand HFL_OUTPUT=json. Do not hide diagnostics just
    # because they do not use one of the known rich-output headings.
    if re.search(r"\b(fail(?:ed|ure)?|error|exception|unable)\b", line, re.IGNORECASE):
        emit("FAIL", "gateway", line)


def main() -> int:
    for raw in sys.stdin:
        line = raw.rstrip("\r\n")
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            render_plain(line)
            continue
        if isinstance(event, dict):
            render_json(event)
        else:
            render_plain(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
