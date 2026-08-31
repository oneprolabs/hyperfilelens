#!/usr/bin/env python3
"""Regression tests for the repository language-source boundary checker."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CHECKER_SOURCE = Path(__file__).with_name("check-english-source.py")
CJK_SAMPLE_CODE_POINT = 0x4F60


def cjk_sample() -> str:
    """Return a CJK character without storing it literally in source."""
    return chr(CJK_SAMPLE_CODE_POINT)


def cjk_unicode_escapes() -> tuple[str, ...]:
    """Return supported escaped CJK forms using ASCII source."""
    slash = chr(92)
    supplementary_code_point = 0x20000
    adjusted = supplementary_code_point - 0x10000
    high_surrogate = 0xD800 + (adjusted >> 10)
    low_surrogate = 0xDC00 + (adjusted & 0x3FF)
    return (
        f"{slash}u{CJK_SAMPLE_CODE_POINT:04x}",
        f"{slash}U{supplementary_code_point:08x}",
        f"{slash}u{{{supplementary_code_point:x}}}",
        f"{slash}u{high_surrogate:04x}{slash}u{low_surrogate:04x}",
    )


class EnglishSourceCheckerTests(unittest.TestCase):
    """Verify that the checker follows Git's public source boundary."""

    def setUp(self) -> None:
        """Create an isolated Git repository containing the checker."""
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repository_root = Path(self.temporary_directory.name)
        checker = self.repository_root / "tools/quality/check-english-source.py"
        checker.parent.mkdir(parents=True)
        shutil.copy2(CHECKER_SOURCE, checker)
        (self.repository_root / ".gitignore").write_text(
            ".tmp/\n",
            encoding="utf-8",
        )
        self.run_git("init", "--quiet")
        self.run_git("add", ".gitignore", "tools/quality/check-english-source.py")

    def run_git(self, *arguments: str) -> None:
        """Run Git in the isolated repository."""
        subprocess.run(
            ["git", *arguments],
            cwd=self.repository_root,
            check=True,
            stdout=subprocess.PIPE,
        )

    def run_checker(self) -> subprocess.CompletedProcess[str]:
        """Run the copied checker and return its captured result."""
        return subprocess.run(
            [sys.executable, "tools/quality/check-english-source.py"],
            cwd=self.repository_root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_ignored_file_is_not_checked(self) -> None:
        """Ignore local files even when their paths and contents contain CJK."""
        ignored_directory = self.repository_root / ".tmp"
        ignored_directory.mkdir()
        sample = cjk_sample()
        (ignored_directory / f"{sample}{sample}.txt").write_text(
            f"{sample}{sample}\n",
            encoding="utf-8",
        )

        result = self.run_checker()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("English source boundary check passed.", result.stdout)

    def test_language_pack_content_is_allowed(self) -> None:
        """Allow translated data only inside an individual language pack."""
        translated_file = self.repository_root / "language-packs/packs/zh-hans/messages.json"
        translated_file.parent.mkdir(parents=True)
        translated_file.write_text(
            f'{{"welcome": "{cjk_sample()}"}}\n',
            encoding="utf-8",
        )

        result = self.run_checker()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("English source boundary check passed.", result.stdout)

    def test_localized_root_readme_is_allowed(self) -> None:
        """Allow the explicitly named localized root README."""
        translated_file = self.repository_root / "README.zh-CN.md"
        translated_file.write_text(f"# {cjk_sample()}\n", encoding="utf-8")

        result = self.run_checker()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("English source boundary check passed.", result.stdout)

    def test_root_readme_language_switch_is_allowed(self) -> None:
        """Allow only the standard localized label in the root README switch."""
        chinese_label = f"{chr(0x4E2D)}{chr(0x6587)}"
        readme = self.repository_root / "README.md"
        readme.write_text(
            f"English | [{chinese_label}](README.zh-CN.md)\n",
            encoding="utf-8",
        )

        result = self.run_checker()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("English source boundary check passed.", result.stdout)

    def test_other_localized_root_readme_content_is_rejected(self) -> None:
        """Keep rejecting localized prose elsewhere in the English README."""
        readme = self.repository_root / "README.md"
        readme.write_text(f"# {cjk_sample()}\n", encoding="utf-8")

        result = self.run_checker()

        self.assertEqual(result.returncode, 1)
        self.assertIn("README.md:1:3", result.stdout)

    def test_localized_website_content_is_allowed(self) -> None:
        """Allow translated content inside an approved website locale root."""
        translated_file = self.repository_root / "website/zh/docs/index.md"
        translated_file.parent.mkdir(parents=True)
        translated_file.write_text(f"# {cjk_sample()}\n", encoding="utf-8")

        result = self.run_checker()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("English source boundary check passed.", result.stdout)

    def test_localized_website_navigation_is_allowed(self) -> None:
        """Allow locale labels in the explicit website navigation source."""
        navigation_file = self.repository_root / "website/.vitepress/navigation/zh.ts"
        navigation_file.parent.mkdir(parents=True)
        navigation_file.write_text(f"export const label = '{cjk_sample()}'\n", encoding="utf-8")

        result = self.run_checker()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("English source boundary check passed.", result.stdout)

    def test_english_website_content_remains_english_only(self) -> None:
        """Do not let the localized website exemption cover English pages."""
        english_file = self.repository_root / "website/en/docs/index.md"
        english_file.parent.mkdir(parents=True)
        english_file.write_text(f"# {cjk_sample()}\n", encoding="utf-8")

        result = self.run_checker()

        self.assertEqual(result.returncode, 1)
        self.assertIn("website/en/docs/index.md:1:3", result.stdout)

    def test_website_tooling_remains_english_only(self) -> None:
        """Do not let the localized website exemption hide general tooling."""
        tooling_file = self.repository_root / "website/.vitepress/theme/index.ts"
        tooling_file.parent.mkdir(parents=True)
        tooling_file.write_text(f"// {cjk_sample()}\n", encoding="utf-8")

        result = self.run_checker()

        self.assertEqual(result.returncode, 1)
        self.assertIn("website/.vitepress/theme/index.ts:1:4", result.stdout)

    def test_language_pack_tooling_remains_english_only(self) -> None:
        """Do not let the translation-data exemption hide tooling source."""
        tooling_file = self.repository_root / "language-packs/tooling/build.py"
        tooling_file.parent.mkdir(parents=True)
        tooling_file.write_text(f"# {cjk_sample()}\n", encoding="utf-8")

        result = self.run_checker()

        self.assertEqual(result.returncode, 1)
        self.assertIn("language-packs/tooling/build.py:1:3", result.stdout)

    def test_untracked_nonignored_file_is_checked(self) -> None:
        """Check new source files before they are added to Git."""
        (self.repository_root / "notes.txt").write_text(
            f"{cjk_sample()}\n",
            encoding="utf-8",
        )

        result = self.run_checker()

        self.assertEqual(result.returncode, 1)
        self.assertIn("notes.txt:1:1", result.stdout)
        self.assertIn("U+4F60", result.stdout)

    def test_tracked_file_is_checked_even_when_ignored(self) -> None:
        """Keep checking tracked files after an ignore rule is introduced."""
        tracked_file = self.repository_root / "tracked.txt"
        tracked_file.write_text(f"{cjk_sample()}\n", encoding="utf-8")
        self.run_git("add", "tracked.txt")
        with (self.repository_root / ".gitignore").open(
            "a",
            encoding="utf-8",
        ) as ignore_file:
            ignore_file.write("tracked.txt\n")

        result = self.run_checker()

        self.assertEqual(result.returncode, 1)
        self.assertIn("tracked.txt:1:1", result.stdout)

    def test_cjk_unicode_escape_is_checked(self) -> None:
        """Reject an ASCII source representation of an escaped CJK character."""
        for escaped_cjk in cjk_unicode_escapes():
            with self.subTest(escaped_cjk=escaped_cjk):
                (self.repository_root / "notes.txt").write_text(
                    f"{escaped_cjk}\n",
                    encoding="utf-8",
                )

                result = self.run_checker()

                self.assertEqual(result.returncode, 1)
                self.assertIn("notes.txt:1:1", result.stdout)
                self.assertIn("CJK Unicode escape", result.stdout)


if __name__ == "__main__":
    unittest.main()
