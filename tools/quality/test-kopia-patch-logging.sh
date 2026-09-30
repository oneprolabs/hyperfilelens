#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Source without starting a Kopia build.
# shellcheck source=../kopia/prepare.sh
source "${ROOT}/tools/kopia/prepare.sh"

tmp="$(mktemp -d)"
trap 'rm -rf -- "${tmp}"' EXIT
export HFL_LOG_FILE="${tmp}/session.log"
touch "${HFL_LOG_FILE}"

run_patch_test "Checking test patch" "Test patch checks passed" \
	bash -c 'printf "ok github.com/kopia/kopia/cli 0.01s\\n"' \
	>"${tmp}/success.out" 2>&1
grep -F '[INFO] [kopia] Checking test patch' "${tmp}/success.out" >/dev/null
grep -F '[ OK ] [kopia] Test patch checks passed' "${tmp}/success.out" >/dev/null
if grep -F 'github.com/kopia/kopia' "${tmp}/success.out" >/dev/null; then
	printf 'Go package results leaked into successful terminal output\n' >&2
	exit 1
fi
grep -F 'ok github.com/kopia/kopia/cli 0.01s' "${HFL_LOG_FILE}" >/dev/null

if run_patch_test "Checking broken patch" "Broken patch checks passed" \
	bash -c 'printf "FAIL github.com/kopia/kopia/cli\\n"; exit 7' \
	>"${tmp}/failure.out" 2>&1; then
	printf 'A failed Go test was reported as successful\n' >&2
	exit 1
else
	status=$?
fi
[[ "${status}" -eq 7 ]]
grep -F '[OUT ] [kopia] FAIL github.com/kopia/kopia/cli' "${tmp}/failure.out" >/dev/null
grep -F '[FAIL] [kopia] Checking broken patch failed' "${tmp}/failure.out" >/dev/null
if grep -F 'Broken patch checks passed' "${tmp}/failure.out" >/dev/null; then
	printf 'A success message followed the failing Go test\n' >&2
	exit 1
fi

printf 'Kopia patch logging checks passed.\n'
