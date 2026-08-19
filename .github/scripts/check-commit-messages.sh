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
import sys
import urllib.error
import urllib.request

repo = os.environ["GITHUB_REPOSITORY"]
pr = os.environ["PR_NUMBER"]
token = os.environ.get("GH_TOKEN", "")

# Allow offline testing by injecting a local fixture.
fixture = os.environ.get("HFL_COMMITS_FILE")
if fixture:
    with open(fixture, encoding="utf-8") as fh:
        commits = json.load(fh)
else:
    commits = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/repos/{repo}/pulls/{pr}/commits"
            f"?per_page=100&page={page}"
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "hyperfilelens-pr-checks",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                batch = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            # Workflow commands (::error) are only parsed from stdout.
            print(
                f"::error title=Commit message language check::"
                f"GitHub API returned HTTP {exc.code} for {esc(url)}: {esc(detail)}",
            )
            sys.exit(1)
        except urllib.error.URLError as exc:
            print(
                f"::error title=Commit message language check::"
                f"Failed to reach GitHub API for {esc(url)}: {esc(str(exc.reason))}",
            )
            sys.exit(1)
        commits.extend(batch)
        if len(batch) < 100:
            break
        page += 1

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
    (0x3000, 0x303F),   # CJK Symbols and Punctuation (full-width 、。， etc.)
    (0x3040, 0x309F),   # Hiragana
    (0x30A0, 0x30FF),   # Katakana
    (0x3400, 0x4DBF),   # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0xAC00, 0xD7AF),   # Hangul Syllables
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0xFF00, 0xFFEF),   # Halfwidth and Fullwidth Forms (full-width ：，？ etc.)
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


def esc(value):
    # Escape workflow-command special characters so annotations remain
    # parseable when the message contains ':', '%' or line breaks.
    # Order matters: '%' first, otherwise we re-escape the injected %0A/%25.
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
    )


def contains_non_latin(text):
    for ch in text:
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in NON_LATIN_BLOCKS):
            return True
    return False


def summarize(line, max_len=160):
    # Center the snippet on the first offending character so the annotation
    # always shows the non-English text, even in very long lines.
    if len(line) <= max_len:
        return line
    first_bad = min(i for i, ch in enumerate(line) if contains_non_latin(ch))
    start = max(0, first_bad - 60)
    end = min(len(line), start + max_len)
    return (
        ("..." if start > 0 else "")
        + line[start:end]
        + ("..." if end < len(line) else "")
    )


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
        snippet = summarize(line)
        print(
            f"::error title=Commit message language check::"
            f"Commit {sha}: non-English text: {esc(snippet)}"
        )
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write("### Commit message language check\n\n")
            fh.write("Found non-English text in commit messages:\n\n")
            for sha, line in violations:
                fh.write(f"- `{sha}`: `{summarize(line)}`\n")
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
