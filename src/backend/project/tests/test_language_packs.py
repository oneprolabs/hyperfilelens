"""Tests for runtime language-pack manifest loading."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from project.settings.lang_packs import load_language_pack_settings


class LanguagePackSettingsTests(SimpleTestCase):
    """Verify language packs are discovered safely and deterministically."""

    def _write_pack(
        self,
        root: Path,
        pack_id: str = "fr",
        frontend_code: str = "fr",
        backend_code: str = "fr",
        aliases: list[str] | None = None,
        schema: int = 1,
    ) -> Path:
        """Create the smallest valid language pack used by loader tests."""
        pack_dir = root / pack_id
        backend_messages = (
            pack_dir
            / "backend"
            / "locale"
            / backend_code
            / "LC_MESSAGES"
            / "django.mo"
        )
        frontend_messages = pack_dir / "frontend" / "messages.json"
        backend_messages.parent.mkdir(parents=True)
        frontend_messages.parent.mkdir(parents=True)
        backend_messages.write_bytes(b"compiled-catalog")
        frontend_messages.write_text("{}", encoding="utf-8")
        if schema == 2:
            (pack_dir / "frontend" / "element-plus.json").write_text(
                "{}",
                encoding="utf-8",
            )
        manifest = {
            "schema": schema,
            "id": pack_id,
            "display_name": "French",
            "version": "1.0.0",
            "compatible_app": "==1.0.0" if schema == 2 else ">=1.0.0,<2.0.0",
            "frontend_code": frontend_code,
            "backend_code": backend_code,
            "aliases": aliases or ["fr-fr"],
        }
        (pack_dir / "manifest.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
        return pack_dir

    def test_missing_root_has_no_optional_languages(self) -> None:
        """A fresh installation should work before any pack is installed."""
        with TemporaryDirectory() as temp_dir:
            settings = load_language_pack_settings(Path(temp_dir) / "missing")

        self.assertEqual(settings.languages, ())
        self.assertEqual(settings.locale_paths, ())
        self.assertEqual(settings.language_code_mapping, ())

    def test_valid_pack_contributes_languages_paths_and_aliases(self) -> None:
        """A valid manifest should produce all required Django settings."""
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pack_dir = self._write_pack(root)
            settings = load_language_pack_settings(root)

        self.assertEqual(settings.languages, (("fr", "French"),))
        self.assertEqual(settings.locale_paths, (pack_dir / "backend" / "locale",))
        self.assertEqual(
            dict(settings.language_code_mapping),
            {"fr": "fr", "fr-fr": "fr"},
        )

    def test_schema_two_requires_component_catalog(self) -> None:
        """Data-only component translations are part of the schema-two contract."""
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pack_dir = self._write_pack(root, schema=2)
            (pack_dir / "frontend" / "element-plus.json").unlink()

            with patch.dict("os.environ", {"HFL_PRODUCT_VERSION": "1.0.0"}):
                with self.assertRaisesRegex(ImproperlyConfigured, "component messages"):
                    load_language_pack_settings(root)

    def test_schema_two_requires_runtime_version_identity(self) -> None:
        """Strict packages cannot load when the product version is unavailable."""
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_pack(root, schema=2)
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaisesRegex(ImproperlyConfigured, "HFL_PRODUCT_VERSION"):
                    load_language_pack_settings(root)

    def test_schema_two_must_match_the_runtime_version(self) -> None:
        """A release cannot load catalogs produced for another application version."""
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_pack(root, schema=2)
            with patch.dict("os.environ", {"HFL_PRODUCT_VERSION": "1.0.1"}):
                with self.assertRaisesRegex(ImproperlyConfigured, "exactly match"):
                    load_language_pack_settings(root)

    def test_pack_id_must_match_installation_directory(self) -> None:
        """A manifest cannot redirect discovery to another directory."""
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pack_dir = self._write_pack(root)
            manifest_path = pack_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["id"] = "another-id"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ImproperlyConfigured, "installation directory"):
                load_language_pack_settings(root)

    def test_missing_catalog_fails_closed(self) -> None:
        """Incomplete packs should stop startup rather than load partially."""
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pack_dir = self._write_pack(root)
            (pack_dir / "frontend" / "messages.json").unlink()

            with self.assertRaisesRegex(ImproperlyConfigured, "frontend messages"):
                load_language_pack_settings(root)

    def test_duplicate_aliases_across_packs_are_rejected(self) -> None:
        """One browser language tag cannot resolve to two backend locales."""
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_pack(root, aliases=["fr-ca"])
            self._write_pack(
                root,
                pack_id="de",
                frontend_code="de",
                backend_code="de",
                aliases=["fr-ca"],
            )

            with self.assertRaisesRegex(ImproperlyConfigured, "already registered"):
                load_language_pack_settings(root)

    def test_builtin_english_cannot_be_claimed_as_an_alias(self) -> None:
        """Optional packs must not redirect the built-in fallback locale."""
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_pack(root, aliases=["en"])

            with self.assertRaisesRegex(ImproperlyConfigured, "built-in English"):
                load_language_pack_settings(root)
