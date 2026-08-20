#!/usr/bin/env python3
"""Reject literal and escaped CJK outside localized publication boundaries."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LANGUAGE_PACK_DATA_ROOT = Path("language-packs/packs")
LOCALIZED_WEBSITE_ROOTS = (Path("website/zh"),)
LOCALIZED_WEBSITE_FILES = frozenset(
    {
        Path("website/.vitepress/config.mts"),
        Path("website/.vitepress/navigation/zh.ts"),
        Path("website/.vitepress/theme/HomeLandingZh.vue"),
        Path("website/.vitepress/theme/languages.ts"),
    },
)
CJK_CODE_POINT_RANGES = (
    (0x1100, 0x11FF),
    (0x3040, 0x30FF),
    (0x3130, 0x318F),
    (0x31F0, 0x31FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xAC00, 0xD7AF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)
UNICODE_ESCAPE_PATTERN = re.compile(
    r"\\u\{(?P<braced>[0-9a-fA-F]{1,6})\}"
    r"|\\u(?P<short>[0-9a-fA-F]{4})"
    r"|\\U(?P<long>[0-9a-fA-F]{8})",
)
SURROGATE_PAIR_PATTERN = re.compile(
    r"\\u(?P<high>d[89abAB][0-9a-fA-F]{2})"
    r"\\u(?P<low>d[c-fC-F][0-9a-fA-F]{2})",
)


def is_cjk_code_point(code_point: int) -> bool:
    """Return whether a Unicode code point belongs to a CJK range."""
    return any(start <= code_point <= end for start, end in CJK_CODE_POINT_RANGES)


def iter_cjk_references(text: str) -> Iterator[tuple[int, int, str]]:
    """Yield offsets, code points, and kinds for literal or escaped CJK."""
    references: list[tuple[int, int, str]] = []
    for offset, character in enumerate(text):
        code_point = ord(character)
        if is_cjk_code_point(code_point):
            references.append((offset, code_point, "character"))

    for match in UNICODE_ESCAPE_PATTERN.finditer(text):
        hexadecimal = next(group for group in match.groups() if group is not None)
        code_point = int(hexadecimal, 16)
        if is_cjk_code_point(code_point):
            references.append((match.start(), code_point, "Unicode escape"))

    for match in SURROGATE_PAIR_PATTERN.finditer(text):
        high = int(match.group("high"), 16)
        low = int(match.group("low"), 16)
        code_point = 0x10000 + ((high - 0xD800) << 10) + low - 0xDC00
        if is_cjk_code_point(code_point):
            references.append((match.start(), code_point, "Unicode escape"))

    yield from sorted(references)


def iter_public_files() -> Iterator[Path]:
    """Yield tracked files and untracked files that Git does not ignore."""
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    for raw_relative_path in result.stdout.split(b"\0"):
        if not raw_relative_path:
            continue
        path = REPOSITORY_ROOT / os.fsdecode(raw_relative_path)
        if path.is_file():
            yield path


def find_violations(path: Path) -> list[str]:
    """Return formatted literal or escaped CJK violations for a text file."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    violations: list[str] = []
    relative_path = path.relative_to(REPOSITORY_ROOT)
    for line_number, line in enumerate(content.splitlines(), start=1):
        for offset, code_point, kind in iter_cjk_references(line):
            violations.append(
                f"{relative_path}:{line_number}:{offset + 1}: "
                f"CJK {kind} (U+{code_point:04X})",
            )
    return violations


def is_language_pack_data_path(relative_path: Path) -> bool:
    """Return whether a path contains language-specific package data."""
    return (
        relative_path == LANGUAGE_PACK_DATA_ROOT
        or LANGUAGE_PACK_DATA_ROOT in relative_path.parents
    )


def is_localized_website_path(relative_path: Path) -> bool:
    """Return whether a path is an approved localized website source."""
    return relative_path in LOCALIZED_WEBSITE_FILES or any(
        relative_path == root or root in relative_path.parents
        for root in LOCALIZED_WEBSITE_ROOTS
    )


def is_localized_publication_path(relative_path: Path) -> bool:
    """Return whether a path may contain localized publication content."""
    return is_language_pack_data_path(relative_path) or is_localized_website_path(
        relative_path,
    )


def main() -> int:
    """Scan public paths and contents and return a CI-friendly status code."""
    violations: list[str] = []
    for path in iter_public_files():
        relative_path = path.relative_to(REPOSITORY_ROOT)
        if is_localized_publication_path(relative_path):
            continue
        for _offset, code_point, kind in iter_cjk_references(str(relative_path)):
            violations.append(
                f"{relative_path}: path contains CJK {kind} "
                f"(U+{code_point:04X})",
            )
        violations.extend(find_violations(path))
    if violations:
        print("Literal and escaped CJK are only allowed in localized publication sources:")
        print("\n".join(violations))
        return 1

    print("English source boundary check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
