#!/usr/bin/env python3
"""Apply HyperFileLens SourceLens runtime environment defaults."""

from __future__ import annotations

import pathlib
import re
import shlex
import sys
from typing import Optional


QUEUE_KEY = "CELERY_TASK_DEFAULT_QUEUE"
QUEUE_ASSIGNMENT = re.compile(
    r"^[ \t]*(?:export[ \t]+)?([A-Z0-9_]+)[ \t]*=[ \t]*(.*)$", re.M
)


def queue_env_value(raw: str) -> Optional[str]:
    """Parse a queue value without interpreting quotes inside dotenv comments."""
    quote = None
    escaped = False
    for index, character in enumerate(raw):
        if escaped:
            escaped = False
        elif character == "\\" and quote != "'":
            escaped = True
        elif quote:
            if character == quote:
                quote = None
        elif character in ("'", '"'):
            quote = character
        elif character == "#" and (index == 0 or raw[index - 1].isspace()):
            raw = raw[:index]
            break
    try:
        tokens = shlex.split(raw, comments=False, posix=True)
    except ValueError:
        return None
    return tokens[0] if len(tokens) == 1 else ("" if not tokens else None)


def remove_default_queue_override(text: str, *, template: bool = False) -> str:
    """Drop template defaults or the verified legacy conflict, not custom queues."""
    values = {}
    for match in QUEUE_ASSIGNMENT.finditer(text):
        key, raw = match.groups()
        values.setdefault(key, []).append(queue_env_value(raw))
    if not template:
        defaults = values.get(QUEUE_KEY, [])
        if not defaults or any(value != "sourcelens" for value in defaults):
            return text
        for key in (
            "CELERY_TASK_QUEUES",
            "CELERY_WORKER_QUEUES",
            "CELERY_REQUIRED_QUEUES",
        ):
            for value in values.get(key, []):
                if key == "CELERY_WORKER_QUEUES" and value == "":
                    continue  # The SL entrypoint treats an empty value as unset.
                if value is None or {item.strip() for item in value.split(",")} != {
                    "backend",
                    "lens",
                }:
                    # Explicitly adding sourcelens, another queue or a blank
                    # declaration is not the known shipped conflict.
                    return text
    kept = []
    for line in text.splitlines(keepends=True):
        match = QUEUE_ASSIGNMENT.match(line)
        if not match or match.group(1) != QUEUE_KEY:
            kept.append(line)
    return "".join(kept)


def apply_runtime_env(text: str, *, template: bool = False) -> str:
    text = remove_default_queue_override(text, template=template)

    def set_key(name: str, value: str) -> None:
        nonlocal text
        pattern = rf"^{re.escape(name)}=.*$"
        replacement = f"{name}={value}"
        if re.search(pattern, text, flags=re.M):
            text = re.sub(pattern, replacement, text, count=1, flags=re.M)
        else:
            text = text.rstrip() + f"\n{replacement}\n"

    def ensure_key(name: str, value: str) -> None:
        nonlocal text
        if not re.search(rf"^{re.escape(name)}=.*$", text, flags=re.M):
            text = text.rstrip() + f"\n{name}={value}\n"

    # Skip Turnstile and other production-only gates until explicitly configured.
    set_key("DJANGO_DEBUG", "true")
    ensure_key("SENTRY_ENABLED", "false")
    ensure_key("SENTRY_DSN", "")
    ensure_key("SENTRY_FRONTEND_DSN", "")
    ensure_key("SENTRY_ENVIRONMENT", "")
    ensure_key("SENTRY_RELEASE", "")
    ensure_key("SENTRY_FRONTEND_RELEASE", "")
    set_key("SENTRY_PROFILING_SAMPLE_RATE", "0")
    set_key("SENTRY_SEND_DEFAULT_PII", "false")
    set_key("LENSNODE_PLANNING_REASONING_EFFORT", "medium")
    set_key("LENSNODE_EXECUTION_BACKEND", "trusted_container")
    set_key("LENSNODE_MAX_CONCURRENT_RUNS", "1")
    allowed_hosts_match = re.search(r"^ALLOWED_HOSTS=(.*)$", text, flags=re.M)
    allowed_hosts = []
    if allowed_hosts_match:
        allowed_hosts = [
            item.strip()
            for item in allowed_hosts_match.group(1).split(",")
            if item.strip()
        ]
    if "sourcelens-nginx" not in allowed_hosts:
        allowed_hosts.append("sourcelens-nginx")
    set_key("ALLOWED_HOSTS", ",".join(allowed_hosts))
    for name in (
        "NGINX_HTTP_PORT",
        "NGINX_HTTPS_PORT",
        "LENSNODE_HEAVY_WORK_CONCURRENCY",
    ):
        text = re.sub(
            rf"^{name}=.*\n?",
            "",
            text,
            count=1,
            flags=re.M,
        )
    return text


def main() -> None:
    if len(sys.argv) not in (2, 3) or (
        len(sys.argv) == 3 and sys.argv[2] != "--template"
    ):
        raise SystemExit(f"usage: {sys.argv[0]} ENV_FILE [--template]")
    path = pathlib.Path(sys.argv[1])
    path.write_text(
        apply_runtime_env(
            path.read_text(encoding="utf-8"), template=len(sys.argv) == 3
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
