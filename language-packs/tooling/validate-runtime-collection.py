#!/usr/bin/env python3
"""Validate cross-pack identity and language ownership before release packaging."""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any


def fail(message: str) -> None:
    raise SystemExit(f"invalid runtime language-pack collection: {message}")


def required_string(manifest: dict[str, Any], field: str, pack_id: str) -> str:
    value = manifest.get(field)
    if not isinstance(value, str) or not value.strip():
        fail(f"pack {pack_id!r} field {field!r} must be a non-empty string")
    return value.strip()


root = pathlib.Path(sys.argv[1]).resolve()
expected_ids = sys.argv[2:]
if not expected_ids:
    fail("no expected packs were provided")
if len(set(expected_ids)) != len(expected_ids):
    fail("the expected pack list contains duplicate ids")

frontend_owners: dict[str, str] = {}
backend_owners: dict[str, str] = {}
language_owners: dict[str, tuple[str, str]] = {"en": ("built-in", "en")}
for expected_id in expected_ids:
    manifest_path = root / expected_id / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"cannot read {manifest_path}: {exc}")
    if not isinstance(manifest, dict):
        fail(f"{manifest_path} must contain an object")
    pack_id = required_string(manifest, "id", expected_id)
    if pack_id != expected_id:
        fail(f"manifest id {pack_id!r} does not match catalog id {expected_id!r}")
    frontend_code = required_string(manifest, "frontend_code", pack_id)
    backend_code = required_string(manifest, "backend_code", pack_id)
    aliases = manifest.get("aliases", [])
    if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
        fail(f"pack {pack_id!r} has invalid aliases")

    previous = frontend_owners.get(frontend_code)
    if previous is not None:
        fail(f"packs {previous!r} and {pack_id!r} share frontend code {frontend_code!r}")
    frontend_owners[frontend_code] = pack_id
    previous = backend_owners.get(backend_code)
    if previous is not None:
        fail(f"packs {previous!r} and {pack_id!r} share backend code {backend_code!r}")
    backend_owners[backend_code] = pack_id

    for code in {frontend_code, backend_code, *aliases}:
        previous_identity = language_owners.get(code)
        if previous_identity is not None and previous_identity[1] != backend_code:
            fail(
                f"language code {code!r} maps to both "
                f"{previous_identity[0]!r} and {pack_id!r}"
            )
        language_owners[code] = (pack_id, backend_code)
