#!/usr/bin/env bash
# Bounded recovery for a Docker Compose stop/recreate exit-event race.

_hfl_compose_lifecycle_loaded="${_hfl_compose_lifecycle_loaded:-}"
if [[ -n "${_hfl_compose_lifecycle_loaded}" ]]; then
	return 0 2>/dev/null || exit 0
fi
_hfl_compose_lifecycle_loaded=1

hfl_compose_lifecycle_warn() {
	if declare -F hfl_log_warn >/dev/null 2>&1; then
		hfl_log_warn "$@"
	elif declare -F warn >/dev/null 2>&1; then
		warn "$@"
	else
		printf '[WARN] %s\n' "$*" >&2
	fi
}

hfl_compose_lifecycle_log() {
	if declare -F hfl_log_info >/dev/null 2>&1; then
		hfl_log_info "$@"
	elif declare -F log >/dev/null 2>&1; then
		log "$@"
	else
		printf '[INFO] %s\n' "$*" >&2
	fi
}

hfl_compose_exit_event_container_ids() {
	local error_file=$1
	grep -E \
		'cannot stop container: [0-9a-f]{12,64}: tried to kill container, but did not receive an exit event' \
		"${error_file}" \
		| sed -E 's/.*cannot stop container: ([0-9a-f]{12,64}):.*/\1/' \
		| sort -u
}

hfl_compose_wait_for_container_exit_events() {
	local container_ids=$1
	local wait_seconds="${HFL_COMPOSE_EXIT_EVENT_WAIT_SECONDS:-60}"
	local poll_seconds="${HFL_COMPOSE_EXIT_EVENT_POLL_SECONDS:-2}"
	local deadline container_id inspect_output inspect_status inspect_timeout
	local all_stopped remaining_seconds sleep_seconds
	[[ "${wait_seconds}" =~ ^[1-9][0-9]*$ && "${wait_seconds}" -le 60 ]] || {
		hfl_compose_lifecycle_warn \
			"Invalid HFL_COMPOSE_EXIT_EVENT_WAIT_SECONDS=${wait_seconds}; use 1-60 seconds"
		return 1
	}
	[[ "${poll_seconds}" =~ ^[1-9][0-9]*$ && "${poll_seconds}" -le "${wait_seconds}" ]] || {
		hfl_compose_lifecycle_warn \
			"Invalid HFL_COMPOSE_EXIT_EVENT_POLL_SECONDS=${poll_seconds}; use 1-${wait_seconds} seconds"
		return 1
	}

	deadline=$((SECONDS + wait_seconds))
	while true; do
		remaining_seconds=$((deadline - SECONDS))
		if ((remaining_seconds <= 0)); then
			hfl_compose_lifecycle_warn \
				"Docker did not settle the delayed container exit within ${wait_seconds}s; not retrying Compose"
			return 1
		fi
		all_stopped=1
		while IFS= read -r container_id; do
			[[ -n "${container_id}" ]] || continue
			remaining_seconds=$((deadline - SECONDS))
			if ((remaining_seconds <= 0)); then
				hfl_compose_lifecycle_warn \
					"Docker did not settle the delayed container exit within ${wait_seconds}s; not retrying Compose"
				return 1
			fi
			inspect_timeout=5
			((inspect_timeout <= remaining_seconds)) || inspect_timeout=${remaining_seconds}
			if inspect_output="$(timeout "${inspect_timeout}s" \
				docker inspect --format '{{.State.Running}}' "${container_id}" 2>&1)"; then
				case "${inspect_output}" in
				false) ;;
				true) all_stopped=0 ;;
				*)
					hfl_compose_lifecycle_warn \
						"Docker returned an unknown running state for container ${container_id}: ${inspect_output}"
					return 1
					;;
				esac
			else
				inspect_status=$?
				if [[ "${inspect_status}" -eq 124 ]]; then
					hfl_compose_lifecycle_warn \
						"Docker inspect did not respond within ${inspect_timeout}s while waiting for container ${container_id}"
					return 1
				fi
				if grep -Eq 'No such (object|container)' <<<"${inspect_output}"; then
					# Compose may remove the old container while the daemon catches up.
					continue
				fi
				hfl_compose_lifecycle_warn \
					"Could not inspect container ${container_id} while waiting for its exit event: ${inspect_output}"
				return 1
			fi
		done <<<"${container_ids}"

		if [[ "${all_stopped}" -eq 1 ]]; then
			return 0
		fi
		remaining_seconds=$((deadline - SECONDS))
		((remaining_seconds > 0)) || continue
		sleep_seconds=${poll_seconds}
		((sleep_seconds <= remaining_seconds)) || sleep_seconds=${remaining_seconds}
		sleep "${sleep_seconds}"
	done
}

hfl_compose_command_with_exit_event_recovery() {
	local compose_callback=${1:-}
	shift || true
	local error_file status container_ids capture_fd capture_pid
	[[ -n "${compose_callback}" ]] || return 2
	error_file="$(mktemp "${TMPDIR:-/tmp}/hfl-compose-lifecycle.XXXXXX")" || return 1

	# Keep the callback in the current shell (some callers maintain transaction
	# state) while teeing Docker/Compose stderr live for classification.
	exec {capture_fd}> >(tee "${error_file}" >&2)
	capture_pid=$!
	if "${compose_callback}" "$@" 2>&${capture_fd}; then
		status=0
	else
		status=$?
	fi
	exec {capture_fd}>&-
	wait "${capture_pid}" || true
	if [[ "${status}" -eq 0 ]]; then
		rm -f -- "${error_file}"
		return 0
	fi

	container_ids="$(hfl_compose_exit_event_container_ids "${error_file}" || true)"
	rm -f -- "${error_file}"
	if [[ -z "${container_ids}" ]]; then
		return "${status}"
	fi

	hfl_compose_lifecycle_warn \
		"Docker delayed a container exit event; waiting only for the affected container before one Compose retry"
	if ! hfl_compose_wait_for_container_exit_events "${container_ids}"; then
		return "${status}"
	fi

	hfl_compose_lifecycle_log \
		"Docker container exit is settled; reconciling Compose once"
	if "${compose_callback}" "$@"; then
		return 0
	else
		status=$?
		hfl_compose_lifecycle_warn \
			"Compose reconciliation failed after the single lifecycle retry"
		return "${status}"
	fi
}
