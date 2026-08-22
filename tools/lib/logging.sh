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
HFL_LOG_COLOR="${HFL_LOG_COLOR:-auto}"
export HFL_LOG_COLOR
HFL_LOG_SESSION_STARTED=0

hfl_log_timestamp() {
	date -u '+%Y-%m-%dT%H:%M:%S.000Z'
}

hfl_log_component_name() {
	local component="${HFL_LOG_COMPONENT:-hfl}"
	case "${component}" in
	dev*) printf '%s' "dev" ;;
	docker*) printf '%s' "docker" ;;
	sourcelens* | sl*) printf '%s' "sourcelens" ;;
	agent*) printf '%s' "agent" ;;
	gateway*) printf '%s' "gateway" ;;
	*) printf '%s' "${component}" ;;
	esac
}

hfl_log_status_token() {
	case "${1}" in
	OK | ' OK ' | ' OK  ') printf '%s' ' OK ' ;;
	STEP | ....) printf '%s' '....' ;;
	WARN) printf '%s' 'WARN' ;;
	FAIL | ERROR) printf '%s' 'FAIL' ;;
	SKIP) printf '%s' 'SKIP' ;;
	OUT | 'OUT ') printf '%s' 'OUT ' ;;
	INFO) printf '%s' 'INFO' ;;
	DEBUG) printf '%s' 'DBG ' ;;
	*) printf '%s' "${1}" ;;
	esac
}

hfl_log_color_enabled() {
	[[ "${HFL_LOG_COLOR}" != "0" && -z "${NO_COLOR:-}" ]] || return 1
	if [[ "${HFL_LOG_COLOR}" == "1" || "${HFL_LOG_COLOR}" == "always" ]]; then
		return 0
	fi
	# Respect terminals that explicitly advertise that ANSI styling is not
	# supported. This keeps piped/non-interactive output portable even when the
	# caller has inherited a terminal descriptor from a parent process.
	[[ "${TERM:-}" != "dumb" ]] || return 1
	# Dev logging mirrors stderr through a process substitution, so the live
	# terminal is kept on fd 4. Fall back to stderr for standalone callers.
	[[ -t 4 || -t 2 ]]
}

hfl_log_color_status() {
	local token="$1"
	if ! hfl_log_color_enabled; then
		printf '%s' "${token}"
		return 0
	fi
	case "${token}" in
	' OK ') printf '\033[32m%s\033[0m' "${token}" ;;
	'....') printf '\033[35m%s\033[0m' "${token}" ;;
	WARN) printf '\033[33m%s\033[0m' "${token}" ;;
	FAIL) printf '\033[31m%s\033[0m' "${token}" ;;
	SKIP | INFO) printf '\033[36m%s\033[0m' "${token}" ;;
	*) printf '%s' "${token}" ;;
	esac
}

hfl_log_emit() {
	local level=$1 tag component legacy_tag
	shift
	tag="$(hfl_log_status_token "${level}")"
	component="$(hfl_log_component_name)"
	if [[ "${HFL_LOG_TERMINAL_TIMESTAMPS}" == "1" ]]; then
		printf '[%s] [%s] [%s] %s\n' \
			"$(hfl_log_timestamp)" "$(hfl_log_color_status "${tag}")" "${component}" "$*" >&2
	else
		case "${level}" in
		INFO) legacy_tag='INFO ' ;;
		STEP | ....) legacy_tag='....' ;;
		OK | ' OK ' | ' OK  ') legacy_tag=' OK ' ;;
		WARN) legacy_tag='WARN' ;;
		SKIP) legacy_tag='SKIP' ;;
		ERROR | FAIL) legacy_tag='FAIL' ;;
		DEBUG) legacy_tag='DEBUG' ;;
		*) legacy_tag="${tag}" ;;
		esac
		printf '[%s] %s\n' "${legacy_tag}" "$*" >&2
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

hfl_log_output_line() {
	local source="${1:-${HFL_LOG_COMPONENT:-hfl}}" line
	while IFS= read -r line || [[ -n "${line}" ]]; do
		# Keep the live stream readable; blank separators carry no diagnostic
		# information and are already represented by the timestamp sequence.
		[[ -n "${line}" ]] || continue
		hfl_log_emit_with_component 'OUT ' "${source}" "${line}"
	done
}

hfl_log_emit_with_component() {
	local level="$1" component="$2" message="$3" tag legacy_tag
	tag="$(hfl_log_status_token "${level}")"
	if [[ "${HFL_LOG_TERMINAL_TIMESTAMPS}" == "1" ]]; then
		printf '[%s] [%s] [%s] %s\n' \
			"$(hfl_log_timestamp)" "$(hfl_log_color_status "${tag}")" "${component}" "${message}" >&2
	else
		legacy_tag="${tag}"
		[[ "${level}" == "INFO" ]] && legacy_tag='INFO '
		printf '[%s] %s\n' "${legacy_tag}" "${message}" >&2
	fi
}

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
	local log_file=$1 line timestamp structured_prefix
	local TZ=UTC
	export TZ
	structured_prefix='^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.000Z\] \[[^]]+\] \[[^]]+\] '
	# Strip ANSI once for the whole stream rather than spawning sed once per
	# line during large Docker/BuildKit logs.
	sed $'s/\\033\\[[0-9;]*m//g' | while IFS= read -r line || [[ -n "${line}" ]]; do
		# Structured terminal lines already carry their own timestamp. Do not
		# prepend a second timestamp when the capture stream mirrors them.
		if [[ "${line}" =~ ${structured_prefix} ]]; then
			printf '%s\n' "${line}" >>"${log_file}"
			continue
		fi
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
