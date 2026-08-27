#!/usr/bin/env python3
"""Apply HyperFileLens SourceLens runtime environment defaults."""
from __future__ import annotations

import pathlib
import re
import sys


def apply_runtime_env(text: str) -> str:
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
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} ENV_FILE")
    path = pathlib.Path(sys.argv[1])
    path.write_text(apply_runtime_env(path.read_text(encoding="utf-8")), encoding="utf-8")


if __name__ == "__main__":
    main()
