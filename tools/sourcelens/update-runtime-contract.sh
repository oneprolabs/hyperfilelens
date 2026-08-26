#!/usr/bin/env bash
# Resolve a SourceLens release tag to its peeled commit and update the runtime lock.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HFL_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=defaults.env
source "${SCRIPT_DIR}/defaults.env"

usage() {
	cat <<'USAGE'
Usage: tools/sourcelens/update-runtime-contract.sh [TAG] [options]

Options:
  --check        Verify the existing contract without modifying it
  --git-url URL  Resolve TAG from this Git repository
  --output FILE  Write the runtime contract to FILE

TAG is required when updating and is read from the contract with --check.
The default contract is deploy/online/sourcelens/runtime.json.
USAGE
}

mode=update
git_ref=""
git_url="${SOURCELENS_GIT_URL}"
output="${HFL_ROOT}/deploy/online/sourcelens/runtime.json"

while [[ $# -gt 0 ]]; do
	case "$1" in
	--check)
		mode=check
		shift
		;;
	--git-url)
		[[ $# -ge 2 && -n "${2:-}" ]] || { printf 'ERROR: --git-url requires a value\n' >&2; exit 2; }
		git_url=$2
		shift 2
		;;
	--output)
		[[ $# -ge 2 && -n "${2:-}" ]] || { printf 'ERROR: --output requires a value\n' >&2; exit 2; }
		output=$2
		shift 2
		;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		if [[ -z "${git_ref}" && "$1" != -* ]]; then
			git_ref=$1
			shift
		else
			printf 'ERROR: unsupported option: %s\n' "$1" >&2
			exit 2
		fi
		;;
	esac
done

if [[ "${mode}" == "check" && -z "${git_ref}" ]]; then
	git_ref="$(python3 - "${output}" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
try:
    print(json.loads(path.read_text(encoding="utf-8")).get("git_ref") or "")
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid SourceLens runtime contract: {exc}") from exc
PY
	)"
fi
[[ "${git_ref}" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]] || {
	printf 'ERROR: SourceLens TAG must use vX.Y.Z format\n' >&2
	exit 2
}
version=${BASH_REMATCH[1]}
command -v git >/dev/null 2>&1 || {
	printf 'ERROR: git is required to resolve the SourceLens runtime contract\n' >&2
	exit 2
}
if ! command -v timeout >/dev/null 2>&1 \
	|| ! timeout --foreground 1s true >/dev/null 2>&1; then
	printf 'ERROR: GNU timeout with --foreground is required to resolve the SourceLens runtime contract\n' >&2
	exit 2
fi
temporary="$(mktemp -d "${TMPDIR:-/tmp}/hfl-sourcelens-contract.XXXXXX")"
trap 'rm -rf "${temporary}"' EXIT
git -C "${temporary}" init --bare --quiet
fetch_timeout_seconds="${SOURCELENS_CONTRACT_GIT_TIMEOUT_SECONDS:-60}"
fetch_attempts="${SOURCELENS_CONTRACT_GIT_ATTEMPTS:-3}"
[[ "${fetch_timeout_seconds}" =~ ^[1-9][0-9]*$ && "${fetch_attempts}" =~ ^[1-9][0-9]*$ ]] || {
	printf 'ERROR: SourceLens contract Git timeout and attempts must be positive integers\n' >&2
	exit 2
}
fetch_succeeded=0
for attempt in $(seq 1 "${fetch_attempts}"); do
	if timeout --foreground --kill-after=5s "${fetch_timeout_seconds}s" \
		env GIT_TERMINAL_PROMPT=0 \
		git -C "${temporary}" fetch --quiet --force --depth=1 \
		"${git_url}" "refs/tags/${git_ref}"; then
		fetch_succeeded=1
		break
	fi
	printf 'WARN: SourceLens tag resolution failed or timed out (attempt %s/%s)\n' \
		"${attempt}" "${fetch_attempts}" >&2
	if [[ "${attempt}" -lt "${fetch_attempts}" ]]; then
		sleep 2
	fi
done
[[ "${fetch_succeeded}" -eq 1 ]] || {
	printf 'ERROR: could not resolve SourceLens tag %s from %s\n' "${git_ref}" "${git_url}" >&2
	exit 1
}
git_commit="$(git -C "${temporary}" rev-parse 'FETCH_HEAD^{commit}')"
[[ "${git_commit}" =~ ^[0-9a-f]{40}$ ]] || {
	printf 'ERROR: %s did not resolve to a commit\n' "${git_ref}" >&2
	exit 1
}

if [[ "${mode}" == "update" ]]; then
	mkdir -p "$(dirname "${output}")"
fi
python3 - "${mode}" "${output}" "${git_ref}" "${version}" "${git_commit}" <<'PY'
import json
import os
import pathlib
import sys
import tempfile

mode = sys.argv[1]
output = pathlib.Path(sys.argv[2])
payload = {
    "git_commit": sys.argv[5],
    "git_ref": sys.argv[3],
    "version": sys.argv[4],
}
if mode == "check":
    try:
        actual = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid SourceLens runtime contract: {exc}") from exc
    if actual != payload:
        differences = sorted(set(actual) | set(payload), key=str)
        differences = [name for name in differences if actual.get(name) != payload.get(name)]
        raise SystemExit(
            "SourceLens runtime contract differs from the peeled tag identity: "
            + ", ".join(differences)
        )
    raise SystemExit(0)

descriptor, temporary = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
try:
    os.fchmod(descriptor, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")
    os.replace(temporary, output)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
if [[ "${mode}" == "check" ]]; then
	printf 'Verified %s: %s @ %s\n' "${output}" "${git_ref}" "${git_commit}"
else
	printf 'Updated %s: %s @ %s\n' "${output}" "${git_ref}" "${git_commit}"
fi
