"""Load installed language packs without modifying application source code."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import to_locale

LANG_PACKS_ENV = "HFL_LANG_PACKS_DIR"
APP_VERSION_ENV = "HFL_PRODUCT_VERSION"
DEFAULT_LANG_PACKS_ROOT = Path("/opt/backend/lang-packs")
SUPPORTED_MANIFEST_SCHEMAS = frozenset({1, 2})
_PACK_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_LANGUAGE_CODE_PATTERN = re.compile(
    r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LanguagePackSettings:
    """Django settings contributed by installed language packs."""

    languages: tuple[tuple[str, str], ...] = ()
    locale_paths: tuple[Path, ...] = ()
    language_code_mapping: tuple[tuple[str, str], ...] = ()


def _fail(manifest_path: Path, message: str) -> ImproperlyConfigured:
    """Build a configuration error that identifies the invalid manifest."""
    return ImproperlyConfigured(f"Invalid language pack {manifest_path}: {message}")


def _required_string(
    manifest: dict[str, Any],
    field: str,
    manifest_path: Path,
) -> str:
    """Return a required non-empty manifest string."""
    value = manifest.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _fail(manifest_path, f"{field!r} must be a non-empty string")
    return value.strip()


def _validate_language_code(code: str, field: str, manifest_path: Path) -> str:
    """Validate and normalize a BCP 47-style language code."""
    normalized = code.lower()
    if code != normalized:
        raise _fail(manifest_path, f"{field!r} must use lowercase language codes")
    if not _LANGUAGE_CODE_PATTERN.fullmatch(normalized):
        raise _fail(manifest_path, f"{field!r} has an invalid language code")
    return normalized


def _read_manifest(manifest_path: Path) -> dict[str, Any]:
    """Read one JSON manifest and report actionable configuration errors."""
    try:
        raw_manifest = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(raw_manifest)
    except OSError as exc:
        raise _fail(manifest_path, f"cannot read file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise _fail(manifest_path, f"invalid JSON: {exc.msg}") from exc

    if not isinstance(manifest, dict):
        raise _fail(manifest_path, "top-level value must be an object")
    return manifest


def _validate_pack_directory(pack_dir: Path, root: Path) -> Path:
    """Ensure an installed pack directory cannot escape the configured root."""
    resolved_root = root.resolve()
    resolved_pack = pack_dir.resolve()
    if resolved_pack.parent != resolved_root:
        raise ImproperlyConfigured(
            f"Language pack directory escapes configured root: {pack_dir}",
        )
    return resolved_pack


def load_language_pack_settings(root: Path) -> LanguagePackSettings:
    """Load and validate all language packs installed below ``root``.

    Missing roots are treated as an installation with no optional languages.
    Existing roots fail closed when a pack is malformed so deployment problems
    are visible at startup instead of producing partially translated behavior.
    """
    if not root.exists():
        return LanguagePackSettings()
    if not root.is_dir():
        raise ImproperlyConfigured(f"Language pack root is not a directory: {root}")

    languages: list[tuple[str, str]] = []
    locale_paths: list[Path] = []
    aliases: dict[str, str] = {}
    seen_backend_codes: set[str] = set()
    seen_frontend_codes: set[str] = set()
    app_version = os.environ.get(APP_VERSION_ENV, "").strip()

    for pack_dir in sorted(
        path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")
    ):
        resolved_pack = _validate_pack_directory(pack_dir, root)
        manifest_path = resolved_pack / "manifest.json"
        if not manifest_path.is_file():
            raise _fail(manifest_path, "manifest.json is required")

        manifest = _read_manifest(manifest_path)
        manifest_schema = manifest.get("schema")
        if manifest_schema not in SUPPORTED_MANIFEST_SCHEMAS:
            raise _fail(
                manifest_path,
                "'schema' must be one of "
                f"{sorted(SUPPORTED_MANIFEST_SCHEMAS)}",
            )

        pack_id = _required_string(manifest, "id", manifest_path)
        if not _PACK_ID_PATTERN.fullmatch(pack_id):
            raise _fail(manifest_path, "'id' must use lowercase letters, digits, and hyphens")
        if pack_id != resolved_pack.name:
            raise _fail(manifest_path, "'id' must match its installation directory")

        display_name = _required_string(manifest, "display_name", manifest_path)
        pack_version = _required_string(manifest, "version", manifest_path)
        compatible_app = _required_string(manifest, "compatible_app", manifest_path)
        if manifest_schema == 2:
            if not app_version:
                raise _fail(
                    manifest_path,
                    f"schema 2 requires {APP_VERSION_ENV}",
                )
            if pack_version != app_version or compatible_app != f"=={app_version}":
                raise _fail(
                    manifest_path,
                    f"schema 2 must exactly match application {app_version}",
                )
        frontend_code = _validate_language_code(
            _required_string(manifest, "frontend_code", manifest_path),
            "frontend_code",
            manifest_path,
        )
        backend_code = _validate_language_code(
            _required_string(manifest, "backend_code", manifest_path),
            "backend_code",
            manifest_path,
        )

        if backend_code == "en" or frontend_code == "en":
            raise _fail(manifest_path, "optional packs cannot replace the built-in English locale")
        if backend_code in seen_backend_codes:
            raise _fail(manifest_path, f"duplicate backend code {backend_code!r}")
        if frontend_code in seen_frontend_codes:
            raise _fail(manifest_path, f"duplicate frontend code {frontend_code!r}")

        raw_aliases = manifest.get("aliases", [])
        if not isinstance(raw_aliases, list) or not all(
            isinstance(alias, str) for alias in raw_aliases
        ):
            raise _fail(manifest_path, "'aliases' must be an array of language codes")

        locale_path = resolved_pack / "backend" / "locale"
        locale_messages = locale_path / to_locale(backend_code) / "LC_MESSAGES" / "django.mo"
        frontend_messages = resolved_pack / "frontend" / "messages.json"
        component_messages = resolved_pack / "frontend" / "element-plus.json"
        if not locale_messages.is_file():
            raise _fail(manifest_path, f"missing backend catalog: {locale_messages}")
        if not frontend_messages.is_file():
            raise _fail(manifest_path, f"missing frontend messages: {frontend_messages}")
        if manifest_schema == 2 and not component_messages.is_file():
            raise _fail(
                manifest_path,
                f"missing component messages: {component_messages}",
            )

        pack_aliases = {backend_code, frontend_code}
        pack_aliases.update(
            _validate_language_code(alias, "aliases", manifest_path)
            for alias in raw_aliases
        )
        if "en" in pack_aliases:
            raise _fail(
                manifest_path,
                "optional packs cannot claim the built-in English locale as an alias",
            )
        for alias in sorted(pack_aliases):
            existing = aliases.get(alias)
            if existing is not None and existing != backend_code:
                raise _fail(manifest_path, f"language alias {alias!r} is already registered")
            aliases[alias] = backend_code

        languages.append((backend_code, display_name))
        locale_paths.append(locale_path)
        seen_backend_codes.add(backend_code)
        seen_frontend_codes.add(frontend_code)

    return LanguagePackSettings(
        languages=tuple(languages),
        locale_paths=tuple(locale_paths),
        language_code_mapping=tuple(sorted(aliases.items())),
    )


LANGUAGE_PACK_SETTINGS = load_language_pack_settings(
    Path(os.environ.get(LANG_PACKS_ENV, str(DEFAULT_LANG_PACKS_ROOT))),
)
EXTRA_LANGUAGES = LANGUAGE_PACK_SETTINGS.languages
EXTRA_LOCALE_PATHS = LANGUAGE_PACK_SETTINGS.locale_paths
EXTRA_LANGUAGE_CODE_MAPPING = dict(LANGUAGE_PACK_SETTINGS.language_code_mapping)
