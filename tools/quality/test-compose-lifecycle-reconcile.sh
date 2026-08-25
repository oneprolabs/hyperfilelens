#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

warn() { printf '[WARN] %s\n' "$*"; }
log() { printf '[INFO] %s\n' "$*"; }

# shellcheck source=../../deploy/installer/sourcelens/compose-lifecycle.sh
source "${ROOT}/deploy/installer/sourcelens/compose-lifecycle.sh"

mkdir -p "${TMP}/bin"
cat >"${TMP}/bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "inspect" ]] || exit 90
count_file="${HFL_TEST_DOCKER_COUNT_FILE}"
count=0
[[ ! -f "${count_file}" ]] || count="$(cat "${count_file}")"
count=$((count + 1))
printf '%s\n' "${count}" >"${count_file}"
case "${HFL_TEST_DOCKER_MODE:-stopped}" in
stopped) printf 'false\n' ;;
settle)
	if [[ "${count}" -lt 2 ]]; then printf 'true\n'; else printf 'false\n'; fi
	;;
stuck) printf 'true\n' ;;
missing)
	printf 'Error: No such object: %s\n' "${*: -1}" >&2
	exit 1
	;;
error)
	printf 'Cannot connect to the Docker daemon\n' >&2
	exit 1
	;;
hang)
	sleep 5
	printf 'true\n'
	;;
*) exit 91 ;;
esac
SH
chmod +x "${TMP}/bin/docker"
export PATH="${TMP}/bin:${PATH}"
export HFL_TEST_DOCKER_COUNT_FILE="${TMP}/docker-count"
export HFL_COMPOSE_EXIT_EVENT_WAIT_SECONDS=3
export HFL_COMPOSE_EXIT_EVENT_POLL_SECONDS=1

increment_compose_count() {
	local count=0
	[[ ! -f "${TMP}/compose-count" ]] || count="$(cat "${TMP}/compose-count")"
	printf '%s\n' "$((count + 1))" >"${TMP}/compose-count"
}

compose_success() {
	increment_compose_count
	printf 'compose ready\n'
}

compose_configuration_failure() {
	increment_compose_count
	printf 'Bind for 0.0.0.0:11445 failed: port is already allocated\n' >&2
	return 17
}

compose_non_transient_stop_failure() {
	increment_compose_count
	printf 'Error response from daemon: cannot stop container: abcdef1234567890: permission denied\n' >&2
	return 18
}

compose_transient_then_success() {
	increment_compose_count
	if [[ "$(cat "${TMP}/compose-count")" -eq 1 ]]; then
		printf 'Error response from daemon: cannot stop container: abcdef1234567890: tried to kill container, but did not receive an exit event\n' >&2
		return 1
	fi
	printf 'compose reconciled\n'
}

compose_transient_then_failure() {
	increment_compose_count
	if [[ "$(cat "${TMP}/compose-count")" -eq 1 ]]; then
		printf 'Error response from daemon: cannot stop container: abcdef1234567890: tried to kill container, but did not receive an exit event\n' >&2
		return 1
	fi
	printf 'second compose attempt failed\n' >&2
	return 23
}

compose_running_then_success() {
	increment_compose_count
	if [[ "$(cat "${TMP}/compose-count")" -eq 1 ]]; then
		printf 'Error response from daemon: cannot remove container "abcdef1234567890": container is running: stop the container before removing or force remove\n' >&2
		return 1
	fi
	printf 'compose reconciled\n'
}

reset_case() {
	rm -f "${TMP}/compose-count" "${HFL_TEST_DOCKER_COUNT_FILE}"
}

reset_case
hfl_compose_command_with_exit_event_recovery compose_success up -d
[[ "$(cat "${TMP}/compose-count")" == "1" ]]
[[ ! -e "${HFL_TEST_DOCKER_COUNT_FILE}" ]]

reset_case
if hfl_compose_command_with_exit_event_recovery compose_configuration_failure up -d; then
	printf 'non-retryable Compose failure unexpectedly succeeded\n' >&2
	exit 1
else
	rc=$?
fi
[[ "${rc}" -eq 17 ]]
[[ "$(cat "${TMP}/compose-count")" == "1" ]]
[[ ! -e "${HFL_TEST_DOCKER_COUNT_FILE}" ]]

reset_case
if hfl_compose_command_with_exit_event_recovery compose_non_transient_stop_failure down; then
	printf 'non-transient stop failure unexpectedly succeeded\n' >&2
	exit 1
else
	rc=$?
fi
[[ "${rc}" -eq 18 ]]
[[ "$(cat "${TMP}/compose-count")" == "1" ]]
[[ ! -e "${HFL_TEST_DOCKER_COUNT_FILE}" ]]

reset_case
export HFL_TEST_DOCKER_MODE=stopped
hfl_compose_command_with_exit_event_recovery compose_transient_then_success up -d
[[ "$(cat "${TMP}/compose-count")" == "2" ]]
[[ "$(cat "${HFL_TEST_DOCKER_COUNT_FILE}")" == "1" ]]

reset_case
export HFL_TEST_DOCKER_MODE=stopped
hfl_compose_command_with_exit_event_recovery compose_running_then_success up -d
[[ "$(cat "${TMP}/compose-count")" == "2" ]]
[[ "$(cat "${HFL_TEST_DOCKER_COUNT_FILE}")" == "1" ]]

reset_case
export HFL_TEST_DOCKER_MODE=settle
hfl_compose_command_with_exit_event_recovery compose_transient_then_success up -d
[[ "$(cat "${TMP}/compose-count")" == "2" ]]
[[ "$(cat "${HFL_TEST_DOCKER_COUNT_FILE}")" == "2" ]]

reset_case
export HFL_TEST_DOCKER_MODE=missing
hfl_compose_command_with_exit_event_recovery compose_transient_then_success up -d
[[ "$(cat "${TMP}/compose-count")" == "2" ]]

reset_case
export HFL_TEST_DOCKER_MODE=error
if hfl_compose_command_with_exit_event_recovery compose_transient_then_success up -d; then
	printf 'Docker inspect failure unexpectedly retried Compose\n' >&2
	exit 1
fi
[[ "$(cat "${TMP}/compose-count")" == "1" ]]

reset_case
export HFL_TEST_DOCKER_MODE=stopped
if hfl_compose_command_with_exit_event_recovery compose_transient_then_failure up -d; then
	printf 'second Compose failure unexpectedly succeeded\n' >&2
	exit 1
else
	rc=$?
fi
[[ "${rc}" -eq 23 ]]
[[ "$(cat "${TMP}/compose-count")" == "2" ]]

reset_case
export HFL_TEST_DOCKER_MODE=stuck
export HFL_COMPOSE_EXIT_EVENT_WAIT_SECONDS=1
if hfl_compose_command_with_exit_event_recovery compose_transient_then_success up -d; then
	printf 'stuck container unexpectedly retried Compose\n' >&2
	exit 1
fi
[[ "$(cat "${TMP}/compose-count")" == "1" ]]

reset_case
export HFL_TEST_DOCKER_MODE=hang
export HFL_COMPOSE_EXIT_EVENT_WAIT_SECONDS=1
started_at=${SECONDS}
if hfl_compose_command_with_exit_event_recovery compose_transient_then_success up -d; then
	printf 'unresponsive Docker inspect unexpectedly retried Compose\n' >&2
	exit 1
fi
[[ "$((SECONDS - started_at))" -le 2 ]]
[[ "$(cat "${TMP}/compose-count")" == "1" ]]

reset_case
export HFL_TEST_DOCKER_MODE=stopped
export HFL_COMPOSE_EXIT_EVENT_WAIT_SECONDS=61
if hfl_compose_command_with_exit_event_recovery compose_transient_then_success up -d; then
	printf 'overlong lifecycle window unexpectedly retried Compose\n' >&2
	exit 1
fi
[[ "$(cat "${TMP}/compose-count")" == "1" ]]

printf 'Compose lifecycle reconciliation checks passed.\n'
