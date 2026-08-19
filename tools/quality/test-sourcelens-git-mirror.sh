#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# shellcheck source=../sourcelens/common.sh
source "${ROOT}/tools/sourcelens/common.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
network_log="${tmp}/network.log"
original_network_once="$(declare -f sourcelens_git_network_once)"

sourcelens_log() { :; }

sourcelens_git_network_once() {
	local route=$1 timeout_seconds=$2
	shift 2
	printf '%s|%s|%s\n' "${route}" "${timeout_seconds}" "$*" >>"${network_log}"
	case "${TEST_NETWORK_BEHAVIOR}:${route}" in
	mirror-success:mirror | mirror-fallback:official) return 0 ;;
	*) return 1 ;;
	esac
}

SOURCELENS_GIT_URL=https://github.com/oneprolabs/sourcelens.git
GITHUB_DOWNLOAD_MIRROR=https://ghfast.top
GITHUB_TOKEN=
SOURCELENS_GIT_TIMEOUT_SECONDS=120
SOURCELENS_GIT_RETRIES=2
SOURCELENS_GIT_FALLBACK_TIMEOUT_SECONDS=30

TEST_NETWORK_BEHAVIOR=mirror-success
sourcelens_git_network fetch origin --tags --prune
[[ "$(cat "${network_log}")" == 'mirror|120|fetch origin --tags --prune' ]]

: >"${network_log}"
TEST_NETWORK_BEHAVIOR=mirror-fallback
sourcelens_git_network fetch origin --tags --prune
[[ "$(grep -c '^mirror|120|' "${network_log}")" -eq 2 ]]
[[ "$(grep -c '^official|30|' "${network_log}")" -eq 1 ]]
[[ "$(tail -n 1 "${network_log}")" == 'official|30|fetch origin --tags --prune' ]]

: >"${network_log}"
TEST_NETWORK_BEHAVIOR=always-fail
GITHUB_DOWNLOAD_MIRROR=
if sourcelens_git_network fetch origin --tags --prune; then
	printf 'ERROR: direct SourceLens Git test unexpectedly succeeded\n' >&2
	exit 1
fi
[[ "$(grep -c '^official|120|' "${network_log}")" -eq 2 ]]
if grep -q '^mirror|' "${network_log}"; then
	printf 'ERROR: SourceLens Git used an unset mirror\n' >&2
	exit 1
fi

: >"${network_log}"
GITHUB_DOWNLOAD_MIRROR=https://ghfast.top
SOURCELENS_GIT_URL=https://git.example.test/oneprolabs/sourcelens.git
if sourcelens_git_network fetch origin --tags --prune; then
	printf 'ERROR: custom SourceLens Git URL test unexpectedly succeeded\n' >&2
	exit 1
fi
[[ "$(grep -c '^official|120|' "${network_log}")" -eq 2 ]]
if grep -q '^mirror|' "${network_log}"; then
	printf 'ERROR: SourceLens Git rewrote a non-GitHub URL\n' >&2
	exit 1
fi

eval "${original_network_once}"
timeout() {
	printf '%s\n' "$*" >>"${network_log}"
	return 0
}

: >"${network_log}"
SOURCELENS_GIT_URL=https://github.com/oneprolabs/sourcelens.git
GITHUB_DOWNLOAD_MIRROR=https://ghfast.top
GITHUB_TOKEN=
sourcelens_git_network_once mirror 120 fetch origin --tags --prune
grep -F 'url.https://ghfast.top/https://github.com/.insteadOf=https://github.com/' "${network_log}" >/dev/null

: >"${network_log}"
GITHUB_TOKEN=test-token
sourcelens_git_network_once official 30 fetch origin --tags --prune
grep -F 'url.https://x-access-token:test-token@github.com/.insteadOf=https://github.com/' "${network_log}" >/dev/null

dev_config="$(
	"${ROOT}/dev/sourcelens.sh" up \
		--github-download-mirror https://ghfast.top/ \
		--print-config
)"
grep -F 'github_download_mirror=https://ghfast.top' <<<"${dev_config}" >/dev/null
grep -F 'github_fallback_timeout=30' <<<"${dev_config}" >/dev/null

release_config="$(
	"${ROOT}/release/build-sourcelens.sh" \
		--github-download-mirror https://ghfast.top/ \
		--print-config
)"
grep -F 'github_download_mirror=https://ghfast.top' <<<"${release_config}" >/dev/null
grep -F 'github_fallback_timeout=30' <<<"${release_config}" >/dev/null

stack_prepare_body="$(sed -n '/^prepare_sourcelens_dev()/,/^}/p' "${ROOT}/dev/stack.sh")"
grep -F 'args+=(--github-download-mirror "${MIRROR_GITHUB_DOWNLOAD}")' <<<"${stack_prepare_body}" >/dev/null
grep -F 'export SOURCELENS_GIT_TIMEOUT_SECONDS SOURCELENS_GIT_RETRIES SOURCELENS_GIT_FALLBACK_TIMEOUT_SECONDS' \
	<<<"${stack_prepare_body}" >/dev/null
release_prepare_body="$(sed -n '/^build_sourcelens_bundle()/,/^}/p' "${ROOT}/release/build.sh")"
grep -F 'args+=(--github-download-mirror "${MIRROR_GITHUB_DOWNLOAD}")' <<<"${release_prepare_body}" >/dev/null
grep -F 'SOURCELENS_GIT_FALLBACK_TIMEOUT_SECONDS="${SOURCELENS_GIT_FALLBACK_TIMEOUT_SECONDS:-30}"' \
	<<<"${release_prepare_body}" >/dev/null

printf 'SourceLens Git mirror checks passed.\n'
