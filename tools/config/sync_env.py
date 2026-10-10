#!/usr/bin/env python3
"""Safely add missing dotenv defaults and report deprecated keys."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

ENV_ASSIGNMENT_RE = re.compile(
    r"^[ \t]*(?P<key>[A-Z_][A-Z0-9_]*)=(?P<value>[^\r\n]*)$",
    flags=re.MULTILINE,
)

DEPRECATED_ENV_KEYS: dict[str, str] = {
    "CAPTCHA_PROVIDER": "TURNSTILE_ENABLED",
    "HFL_REGISTRATION_ENABLED": "HFL_EMAIL_SIGNUP_ENABLED",
}


@dataclass(frozen=True)
class SyncResult:
    """Summary of a dotenv synchronization operation."""

    added_keys: tuple[str, ...]
    deprecated_keys: tuple[str, ...]


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def _assignments(text: str) -> dict[str, str]:
    return {
        match.group("key"): match.group("value")
        for match in ENV_ASSIGNMENT_RE.finditer(text)
    }


def _default_lines(text: str) -> list[tuple[str, str]]:
    defaults: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in ENV_ASSIGNMENT_RE.finditer(text):
        key = match.group("key")
        if key in seen:
            continue
        seen.add(key)
        defaults.append((key, match.group(0).lstrip(" \t")))
    return defaults


def _append_lines(text: str, lines: Sequence[str]) -> str:
    if not lines:
        return text

    newline = "\r\n" if "\r\n" in text else "\n"
    updated = text
    if updated and not updated.endswith(("\n", "\r")):
        updated += newline
    updated += newline.join(lines) + newline
    return updated


def _atomic_write(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def sync_env_file(env_path: Path, example_path: Path) -> SyncResult:
    """Append missing example assignments without changing existing values.

    Deprecated assignments remain untouched and are returned for reporting.

    Args:
        env_path: Existing dotenv file to update.
        example_path: Dotenv example containing current default assignments.

    Returns:
        Keys added to the dotenv file and deprecated keys found in it.

    Raises:
        FileNotFoundError: If either input file does not exist.
    """
    env_text = _read_text(env_path)
    example_text = _read_text(example_path)
    existing = _assignments(env_text)

    missing = [
        (key, line)
        for key, line in _default_lines(example_text)
        if key not in existing
    ]
    if missing:
        _atomic_write(env_path, _append_lines(env_text, [line for _key, line in missing]))

    deprecated = tuple(key for key in DEPRECATED_ENV_KEYS if key in existing)
    return SyncResult(
        added_keys=tuple(key for key, _line in missing),
        deprecated_keys=deprecated,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Add missing .env.example assignments without overwriting .env values.",
    )
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--example", type=Path, required=True)
    parser.add_argument("--maintenance-defaults", action="store_true")
    return parser


def configure_maintenance_defaults(env_path: Path) -> None:
    """Resolve the host timezone before Docker masks it with a UTC image default."""
    text = _read_text(env_path)
    values = _assignments(text)
    zone = values.get("STORAGE_MAINTENANCE_TIMEZONE", "").strip().strip("\"'")
    if not zone:
        zone_file = Path("/etc/timezone")
        if zone_file.exists():
            zone = zone_file.read_text().strip()
        localtime = str(Path("/etc/localtime").resolve())
        if not zone and "/zoneinfo/" in localtime:
            zone = localtime.split("/zoneinfo/", 1)[1]
        if not zone and sys.platform == "darwin":
            detected = subprocess.run(
                ["/usr/sbin/systemsetup", "-gettimezone"], capture_output=True,
                text=True, check=True,
            )
            zone = detected.stdout.strip().removeprefix("Time Zone: ")
    ZoneInfo(zone)  # Invalid/unknown host timezone must not silently schedule in UTC.
    updates = {"STORAGE_MAINTENANCE_TIMEZONE": zone}
    # Migrate previous shipped defaults; preserve non-default operator overrides.
    defaults = {
        "STORAGE_MAINTENANCE_FULL_INTERVAL_SECONDS": ("86400", "172800"),
        "STORAGE_MAINTENANCE_FULL_WINDOW_START": ("00:00", "01:00"),
        "STORAGE_MAINTENANCE_FULL_WINDOW_END": ("06:00", "05:00"),
        "STORAGE_MAINTENANCE_GLOBAL_CONCURRENCY": ("4", "1"),
    }
    for key, (old, new) in defaults.items():
        if key not in values or values[key].strip().strip("\"'") == old:
            updates[key] = new
    for key, value in updates.items():
        if key in values:
            text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.MULTILINE)
        else:
            text = _append_lines(text, [f"{key}={value}"])
    _atomic_write(env_path, text)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the dotenv synchronization command."""
    args = _build_parser().parse_args(argv)
    try:
        result = sync_env_file(args.env_file, args.example)
        if args.maintenance_defaults:
            configure_maintenance_defaults(args.env_file)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"ERROR: failed to synchronize environment file: {exc}", file=sys.stderr)
        return 1

    if result.added_keys:
        print(f"Added env keys: {', '.join(result.added_keys)}")
    for key in result.deprecated_keys:
        replacement = DEPRECATED_ENV_KEYS[key]
        print(
            f"Warning: {key} is deprecated and ignored; use {replacement} instead.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
