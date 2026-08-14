#!/usr/bin/env bash
# Shared logging helpers for repository orchestration scripts.

if [[ -n "${HFL_LOGGING_LOADED:-}" ]]; then
	return 0 2>/dev/null || exit 0
fi
HFL_LOGGING_LOADED=1

HFL_LOG_COMPONENT="${HFL_LOG_COMPONENT:-hfl}"
HFL_LOG_VERBOSE="${HFL_LOG_VERBOSE:-0}"
HFL_LOG_FILE="${HFL_LOG_FILE:-}"
HFL_LOG_TERMINAL_TIMESTAMPS="${HFL_LOG_TERMINAL_TIMESTAMPS:-1}"
HFL_PARENT_SESSION="${HFL_PARENT_SESSION:-0}"
HFL_LOG_SESSION_MESSAGES="${HFL_LOG_SESSION_MESSAGES:-1}"
HFL_LOG_CAPTURE_STDOUT="${HFL_LOG_CAPTURE_STDOUT:-0}"
HFL_LOG_SESSION_STARTED=0

hfl_log_timestamp() {
	date -u '+%Y-%m-%dT%H:%M:%S.000Z'
}

hfl_log_emit() {
	local level=$1 tag
	shift
	if [[ "${HFL_LOG_TERMINAL_TIMESTAMPS}" == "1" ]]; then
		printf '[%s] [%-5s] %s\n' "$(hfl_log_timestamp)" "${level}" "$*" >&2
	else
		case "${level}" in
		INFO) tag='INFO ' ;;
		STEP) tag='....' ;;
		OK) tag=' OK ' ;;
		WARN) tag='WARN' ;;
		SKIP) tag='SKIP' ;;
		ERROR | FAIL) tag='FAIL' ;;
		DEBUG) tag='DEBUG' ;;
		*) tag="${level}" ;;
		esac
		printf '[%s] %s\n' "${tag}" "$*" >&2
	fi
}

hfl_log_info() { hfl_log_emit INFO "$@"; }
hfl_log_step() { hfl_log_emit STEP "$@"; }
hfl_log_ok() { hfl_log_emit ' OK ' "$@"; }
hfl_log_skip() { hfl_log_emit SKIP "$@"; }
hfl_log_warn() { hfl_log_emit WARN "$@"; }
hfl_log_debug() {
	[[ "${HFL_LOG_VERBOSE}" == "1" ]] || return 0
	hfl_log_emit DEBUG "$@"
}
hfl_log_fail() { hfl_log_emit FAIL "$@"; }

hfl_die() {
	local message=${1:-"operation failed"}
	local code=${2:-1}
	hfl_log_fail "${message}"
	exit "${code}"
}

hfl_require_value() {
	if [[ $# -lt 2 || -z "${2:-}" || "${2:0:1}" == "-" ]]; then
		hfl_die "${1} requires a value" 2
	fi
}

hfl_log_timestamp_stream() {
	local log_file=$1 line timestamp
	local TZ=UTC
	export TZ
	while IFS= read -r line || [[ -n "${line}" ]]; do
		printf -v timestamp '%(%Y-%m-%dT%H:%M:%S.000Z)T' -1
		printf '[%s] %s\n' "${timestamp}" "${line}" >>"${log_file}"
	done
}

hfl_log_capture_stream() {
	local log_file=$1 console_fd=$2
	# tee keeps carriage-return progress output live on the terminal; the second
	# stream is normalized into complete timestamped lines for the session log.
	tee "/dev/fd/${console_fd}" \
		| tr '\r' '\n' \
		| hfl_log_timestamp_stream "${log_file}"
}

hfl_logging_configure() {
	local component=${1:-hfl}
	local log_file=${2:-${HFL_LOG_FILE:-}}
	local verbose=${3:-${HFL_LOG_VERBOSE:-0}}
	local fallback_component
	HFL_LOG_COMPONENT="${component}"
	HFL_LOG_VERBOSE="${verbose}"
	HFL_LOG_FILE="${log_file}"
	if [[ -n "${HFL_LOG_FILE}" && "${HFL_LOG_TEE_ACTIVE:-0}" != "1" ]]; then
		if ! mkdir -p "$(dirname "${HFL_LOG_FILE}")" 2>/dev/null \
			|| [[ -L "${HFL_LOG_FILE}" ]] \
			|| ! touch "${HFL_LOG_FILE}" 2>/dev/null \
			|| ! chmod 600 "${HFL_LOG_FILE}" 2>/dev/null; then
			fallback_component="${component//[^A-Za-z0-9_.-]/-}"
			[[ -n "${fallback_component}" ]] || fallback_component=hfl
			HFL_LOG_FILE="$(mktemp "${TMPDIR:-/tmp}/hyperfilelens-${fallback_component}-XXXXXX.log")"
		fi
		chmod 600 "${HFL_LOG_FILE}"
		export HFL_LOG_TEE_ACTIVE=1
		if [[ "${HFL_LOG_CAPTURE_STDOUT}" == "1" ]]; then
			# Dev orchestration keeps complete child-process stdout/stderr while
			# preserving the caller's stdout/stderr contract.
			exec 3>&1
			exec 4>&2
			exec > >(hfl_log_capture_stream "${HFL_LOG_FILE}" 3) \
			2> >(hfl_log_capture_stream "${HFL_LOG_FILE}" 4)
		else
			exec 2> >(tee -a "${HFL_LOG_FILE}" >&2)
		fi
	fi
}

hfl_logging_start() {
	[[ "${HFL_PARENT_SESSION}" != "1" ]] || return 0
	HFL_LOG_SESSION_STARTED=1
	[[ "${HFL_LOG_SESSION_MESSAGES}" == "1" ]] || return 0
	hfl_log_info "Session started"
}

hfl_logging_finish() {
	local code=${1:-0}
	[[ "${HFL_LOG_SESSION_STARTED}" == "1" ]] || return 0
	HFL_LOG_SESSION_STARTED=0
	if [[ "${HFL_LOG_SESSION_MESSAGES}" != "1" ]]; then
		if [[ "${code}" -ne 0 ]]; then
			hfl_log_fail "Session exited with status ${code}; full log: ${HFL_LOG_FILE:-not configured}"
		fi
		return 0
	fi
	if [[ "${code}" -eq 0 ]]; then
		hfl_log_ok "Session completed"
	else
		hfl_log_fail "Session exited with status ${code}"
	fi
}

hfl_print_banner() {
	local title=${1:-"HyperFileLens"}
	[[ "${HFL_NO_BANNER:-0}" != "1" ]] || return 0
	cat <<'BANNER'
 _   _                       _____ _ _      _
| | | |_   _ _ __   ___ _ _|  ___(_) | ___| |    ___ _ __  ___
| |_| | | | | '_ \ / _ \ '__| |_  | | |/ _ \ |   / _ \ '_ \/ __|
|  _  | |_| | |_) |  __/ |  |  _| | | |  __/ |__|  __/ | | \__ \
|_| |_|\__, | .__/ \___|_|  |_|   |_|_|\___|_____\___|_| |_|___/
       |___/|_|                     INSTALLER
BANNER
	printf '\n%s\n%s\n' "${title}" '----------------------------------------------------------------'
}

hfl_print_section() {
	printf '\n%s\n' "$1"
}

hfl_print_value() {
	local label=$1 value=${2:-}
	[[ -n "${value}" ]] || return 0
	printf '  %-14s %s\n' "${label}" "${value}"
}

hfl_redact() {
	[[ -n "${1:-}" ]] && printf '<set>' || printf '<unset>'
}
