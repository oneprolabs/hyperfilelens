#!/usr/bin/env bash
# Exercise lifecycle locking without invoking systemd or an installed Agent.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
installer="${ROOT}/src/agent/packaging/install/install.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

# Load just the real lock helpers, not installer setup or command dispatch.
# shellcheck disable=SC1090
source <(sed -n '/^acquire_lifecycle_lock()/,/^}/p; /^release_lifecycle_lock()/,/^}/p' "${installer}")
agent_lifecycle_dir() { printf '%s/lifecycle' "$1"; }
is_user_mode() { [[ "${mode}" == "user" || "${mode}" == "user_continuous" ]]; }
process_start_marker() { printf 'test-start-%s' "$1"; }
log_fail() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }

for mode in system user user_continuous; do
	root="${tmp}/${mode}"
	mkdir -p "${root}/lifecycle"
	HFL_LIFECYCLE_LOCK_DIR=""
	lock="${root}/lifecycle/install.lock"
	if is_user_mode; then
		printf 'enrollment-flock-inode\n' >"${lock}"
		inode="$(ls -i "${lock}" | awk '{print $1}')"
		lock="${root}/lifecycle/operation.lock"
	fi
	acquire_lifecycle_lock "${root}" upgrade
	[[ "${HFL_LIFECYCLE_LOCK_DIR}" == "${lock}" ]]
	if (HFL_LIFECYCLE_LOCK_DIR=""; acquire_lifecycle_lock "${root}" stop) >"${tmp}/conflict.out" 2>&1; then
		printf 'Concurrent lifecycle command was admitted for %s\n' "${mode}" >&2
		exit 1
	fi
	grep -F 'Another Agent lifecycle operation' "${tmp}/conflict.out" >/dev/null
	release_lifecycle_lock
	[[ ! -e "${lock}" ]]

	# Configure owns the directory; only its nested start/stop may bypass it.
	mkdir "${lock}"
	printf '%s' "$$" >"${lock}/pid"
	printf 'configure' >"${lock}/operation"
	HFL_CONFIGURE_OWNER_PID="$$"
	for action in start stop; do
		acquire_lifecycle_lock "${root}" "${action}"
		[[ -z "${HFL_LIFECYCLE_LOCK_DIR}" ]]
		release_lifecycle_lock
		[[ -d "${lock}" ]]
	done
	if (acquire_lifecycle_lock "${root}" uninstall) >"${tmp}/conflict.out" 2>&1; then
		printf 'Owner bypass admitted uninstall for %s\n' "${mode}" >&2
		exit 1
	fi
	unset HFL_CONFIGURE_OWNER_PID
	if is_user_mode; then
		[[ "$(ls -i "${root}/lifecycle/install.lock" | awk '{print $1}')" == "${inode}" ]]
		grep -Fx 'enrollment-flock-inode' "${root}/lifecycle/install.lock" >/dev/null
	fi
done

printf 'Agent configure lifecycle lock checks passed.\n'
