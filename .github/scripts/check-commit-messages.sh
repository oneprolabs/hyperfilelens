#!/usr/bin/env bash
# Verify that pull request commit messages use English prose.
#
# The check is intentionally lenient about machine-generated or
# non-prose content so it does not block legitimate commits:
#   * markdown image attachments (![alt](url)) are ignored entirely
#   * markdown link URLs are dropped (link text is still checked)
#   * bare URLs and email addresses are ignored
#   * GitHub references (#123) are ignored
#   * standard trailers (Co-authored-by, Signed-off-by, ...) are ignored,
#     including non-Latin names in those trailers
# Any remaining non-Latin script (CJK, Cyrillic, Greek, Arabic, Hebrew,
# Hangul, etc.) fails the check.
set -euo pipefail

if [[ -z "${GITHUB_REPOSITORY:-}" || -z "${PR_NUMBER:-}" ]]; then
	printf 'ERROR: GITHUB_REPOSITORY and PR_NUMBER environment variables are required.\n' >&2
	exit 2
fi

python3 - <<'PY'
import json
import os
import re
import subprocess
import sys

repo = os.environ["GITHUB_REPOSITORY"]
pr = os.environ["PR_NUMBER"]

raw = subprocess.run(
    ["gh", "api", f"repos/{repo}/pulls/{pr}/commits", "--paginate"],
    check=True,
    capture_output=True,
    text=True,
).stdout
commits = json.loads(raw)

# Unicode blocks of non-Latin writing systems treated as non-English prose
# (ranges are inclusive).
NON_LATIN_BLOCKS = (
    (0x0370, 0x03FF),   # Greek and Coptic
    (0x0400, 0x052F),   # Cyrillic
    (0x0590, 0x05FF),   # Hebrew
    (0x0600, 0x06FF),   # Arabic
    (0x0750, 0x077F),   # Arabic Supplement
    (0x0900, 0x097F),   # Devanagari
    (0x0E00, 0x0E7F),   # Thai
    (0x1100, 0x11FF),   # Hangul Jamo
    (0x3040, 0x309F),   # Hiragana
    (0x30A0, 0x30FF),   # Katakana
    (0x3400, 0x4DBF),   # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0xAC00, 0xD7AF),   # Hangul Syllables
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0x20000, 0x2FA1F), # CJK Unified Ideographs Extension B..G
)

TRAILER_RE = re.compile(
    r"^(?:Co-authored-by|Signed-off-by|Reviewed-by|Tested-by|Acked-by|"
    r"Helped-by|Reported-by|Suggested-by):"
)


def clean(line):
    line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)  # markdown image (alt + url)
    line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)  # markdown link: keep text
    line = re.sub(r"https?://\S+", "", line)  # bare URLs
    line = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "", line)  # emails
    line = re.sub(r"#\d+", "", line)  # issue/PR references
    return line


def contains_non_latin(text):
    for ch in text:
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in NON_LATIN_BLOCKS):
            return True
    return False


violations = []
for commit in commits:
    sha = commit["sha"][:8]
    message = commit["commit"]["message"]
    for line in message.splitlines():
        if TRAILER_RE.match(line.strip()):
            continue
        cleaned = clean(line)
        if contains_non_latin(cleaned):
            violations.append((sha, line.strip()))

step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
if violations:
    for sha, line in violations:
        snippet = line[:160]
        print(
            f"::error title=Commit message language check::"
            f"Commit {sha}: non-English text: {snippet}"
        )
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write("### Commit message language check\n\n")
            fh.write("Found non-English text in commit messages:\n\n")
            for sha, line in violations:
                fh.write(f"- `{sha}`: `{line[:160]}`\n")
            fh.write("\n")
    sys.exit(1)

if step_summary:
    with open(step_summary, "a", encoding="utf-8") as fh:
        fh.write("### Commit message language check\n\n")
        fh.write(f"All {len(commits)} commit message(s) use English prose.\n")
print(
    f"::notice title=Commit message language check::"
    f"Checked {len(commits)} commit(s); all messages use English."
)
PY
