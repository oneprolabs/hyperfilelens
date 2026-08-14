#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Any


PACK_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LANGUAGE_CODE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$")
COMPONENT_LOCALE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def fail(message: str) -> None:
    raise SystemExit(f"invalid runtime language pack: {message}")


def required_string(manifest: dict[str, Any], field: str) -> str:
    value = manifest.get(field)
    if not isinstance(value, str) or not value.strip():
        fail(f"{field!r} must be a non-empty string")
    return value.strip()


def language_code(manifest: dict[str, Any], field: str) -> str:
    value = required_string(manifest, field)
    if value != value.lower() or LANGUAGE_CODE_PATTERN.fullmatch(value) is None:
        fail(f"{field!r} must be a lowercase language code")
    if value == "en":
        fail("optional packs cannot replace the built-in English locale")
    return value


def locale_directory(language_code: str) -> str:
    language, separator, territory = language_code.lower().partition("-")
    if not separator:
        return language
    normalized = territory.title() if len(territory) > 2 else territory.upper()
    return f"{language}_{normalized}"


root = pathlib.Path(sys.argv[1]).resolve()
version = sys.argv[2]
manifest_path = root / "manifest.json"
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    fail(f"cannot read manifest.json: {exc}")
if not isinstance(manifest, dict):
    fail("manifest.json must contain an object")

if manifest.get("schema") != 2:
    fail("manifest schema must be 2")
if manifest.get("version") != version or manifest.get("compatible_app") != f"=={version}":
    fail("version contract does not match the application version")
if PACK_ID_PATTERN.fullmatch(required_string(manifest, "id")) is None:
    fail("invalid pack id")
required_string(manifest, "display_name")
frontend_code = language_code(manifest, "frontend_code")
backend_code = language_code(manifest, "backend_code")
raw_aliases = manifest.get("aliases", [])
if not isinstance(raw_aliases, list) or not all(isinstance(alias, str) for alias in raw_aliases):
    fail("'aliases' must be an array of language codes")
aliases: list[str] = []
for alias in raw_aliases:
    if alias != alias.lower() or LANGUAGE_CODE_PATTERN.fullmatch(alias) is None:
        fail("'aliases' must contain lowercase language codes")
    if alias == "en":
        fail("optional packs cannot claim the built-in English locale as an alias")
    aliases.append(alias)
if len(set(aliases)) != len(aliases):
    fail("'aliases' must not contain duplicates")
component_locale = required_string(manifest, "component_locale")
if COMPONENT_LOCALE_PATTERN.fullmatch(component_locale) is None:
    fail("'component_locale' is invalid")

frontend_messages = root / "frontend/messages.json"
component_messages = root / "frontend/element-plus.json"
backend_messages = (
    root
    / "backend/locale"
    / locale_directory(backend_code)
    / "LC_MESSAGES/django.mo"
)
for path in (frontend_messages, component_messages, backend_messages):
    if not path.is_file() or path.stat().st_size == 0:
        fail(f"missing required file: {path.relative_to(root)}")
for path in (frontend_messages, component_messages):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"invalid JSON file {path.relative_to(root)}: {exc}")
    if not isinstance(value, dict):
        fail(f"{path.relative_to(root)} must contain an object")

allowed = {
    pathlib.Path("manifest.json"),
    pathlib.Path("frontend/messages.json"),
    pathlib.Path("frontend/element-plus.json"),
    backend_messages.relative_to(root),
}
actual = {path.relative_to(root) for path in root.rglob("*") if path.is_file()}
if actual != allowed:
    fail(f"unexpected file set: {sorted(str(path) for path in actual ^ allowed)}")
