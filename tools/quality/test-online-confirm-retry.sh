#!/usr/bin/env bash
# Verify online installer confirmation re-prompts on invalid input and still
# cancels on blank / explicit no. Ctrl+C remains the process INT trap (exit 130).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
installer="${ROOT}/deploy/online/install.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

grep -F 'Enter y or n (or press Enter to cancel).' "${installer}" >/dev/null
grep -F 'HFL_CONFIRM_TTY' "${installer}" >/dev/null

# Extract fail + confirm_installation into an isolated harness.
python3 - "${installer}" "${tmp}/confirm.sh" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(encoding="utf-8")
target = Path(sys.argv[2])


def extract_function(name: str) -> str:
    marker = f"\n{name}() {{"
    start = source.index(marker) + 1
    depth = 0
    for index in range(start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise SystemExit(f"unterminated function {name}")


fragment = extract_function("fail") + "\n\n" + extract_function("confirm_installation") + "\n"
target.write_text(
    "#!/usr/bin/env bash\nset -euo pipefail\n"
    "ASSUME_YES=0\nINSTALL_ACTION=Install\nTAG=v0.0.0\n\n"
    + fragment
    + "\nconfirm_installation\n",
    encoding="utf-8",
)
PY
chmod +x "${tmp}/confirm.sh"

# Invalid input matching the reported typo (t + backspace), then yes -> success.
printf 't\010\ny\n' >"${tmp}/answers-yes"
HFL_CONFIRM_TTY="${tmp}/answers-yes" bash "${tmp}/confirm.sh" >/dev/null 2>"${tmp}/warn-yes"
grep -F '[WARN] Enter y or n (or press Enter to cancel).' "${tmp}/warn-yes" >/dev/null

# Invalid input, then blank Enter -> cancelled.
printf 'nope\n\n' >"${tmp}/answers-cancel"
set +e
HFL_CONFIRM_TTY="${tmp}/answers-cancel" bash "${tmp}/confirm.sh" >/dev/null 2>"${tmp}/warn-cancel"
status=$?
set -e
[[ "${status}" -eq 1 ]]
grep -F '[FAIL] installation cancelled' "${tmp}/warn-cancel" >/dev/null
grep -F '[WARN] Enter y or n (or press Enter to cancel).' "${tmp}/warn-cancel" >/dev/null

# Explicit n cancels without a retry warning.
printf 'n\n' >"${tmp}/answers-n"
set +e
HFL_CONFIRM_TTY="${tmp}/answers-n" bash "${tmp}/confirm.sh" >/dev/null 2>"${tmp}/warn-n"
status=$?
set -e
[[ "${status}" -eq 1 ]]
grep -F '[FAIL] installation cancelled' "${tmp}/warn-n" >/dev/null
if grep -F '[WARN] Enter y or n' "${tmp}/warn-n" >/dev/null; then
	echo "explicit n should not warn about invalid input" >&2
	exit 1
fi

printf 'Online installer confirmation retry checks passed.\n'
