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

# Convert terminal carriage-return refreshes into ordinary log lines without
# turning CRLF into an extra blank line. Perl is available on supported dev
# hosts; the tr fallback keeps the logger usable in minimal environments.
hfl_normalize_native_stream() {
	if command -v perl >/dev/null 2>&1; then
		perl -pe 's/\r\n/\n/g; s/\r/\n/g'
	else
		tr '\r' '\n'
	fi
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

hfl_log_color_enabled_for() {
	local fd="${1:-2}"
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
	[[ -t "${fd}" ]]
}

hfl_log_output_fd() {
	local stream="${1:-stderr}"
	case "${stream}" in
	stdout)
		if [[ -t 3 || "${HFL_LOG_TEE_ACTIVE:-0}" == "1" ]]; then
			printf '3'
		else
			printf '1'
		fi
		;;
	*)
		if [[ -t 4 || "${HFL_LOG_TEE_ACTIVE:-0}" == "1" ]]; then
			printf '4'
		else
			printf '2'
		fi
		;;
	esac
}

hfl_log_color_enabled() {
	local fd
	fd="$(hfl_log_output_fd stderr)"
	hfl_log_color_enabled_for "${fd}"
}

hfl_log_color_status() {
	local token="$1" stream="${2:-stderr}" fd
	fd="$(hfl_log_output_fd "${stream}")"
	if ! hfl_log_color_enabled_for "${fd}"; then
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
			"$(hfl_log_timestamp)" "$(hfl_log_color_status "${tag}" stderr)" "${component}" "$*" >&2
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
			"$(hfl_log_timestamp)" "$(hfl_log_color_status "${tag}" stderr)" "${component}" "${message}" >&2
	else
		legacy_tag="${tag}"
		[[ "${level}" == "INFO" ]] && legacy_tag='INFO '
		printf '[%s] %s\n' "${legacy_tag}" "${message}" >&2
	fi
}

# Emit a native command's multi-line output as one visual block. The first
# line carries the HFL envelope; continuation lines retain the tool's original
# text and align below the message column. This is especially useful for
# `git fetch`, whose `From` header and ref updates are one operation but several
# physical lines. The persisted session log still receives every line.
hfl_log_output_block() {
	local source="${1:-${HFL_LOG_COMPONENT:-hfl}}" line tag raw_prefix prefix prefix_width timestamp
	local first=1
	tag="$(hfl_log_status_token 'OUT ')"
	if [[ "${HFL_LOG_TERMINAL_TIMESTAMPS}" == "1" ]]; then
		timestamp="$(hfl_log_timestamp)"
		printf -v raw_prefix '[%s] [%s] [%s] ' "${timestamp}" "${tag}" "${source}"
		printf -v prefix '[%s] [%s] [%s] ' \
			"${timestamp}" "$(hfl_log_color_status "${tag}" stderr)" "${source}"
	else
		printf -v raw_prefix '[%s] ' "${tag}"
		prefix="${raw_prefix}"
	fi
	prefix_width="${#raw_prefix}"
	while IFS= read -r line || [[ -n "${line}" ]]; do
		# Native tools sometimes emit a blank separator between their logger and
		# the command result. Stage boundaries already provide visual separation;
		# do not surface an otherwise empty line in the HFL output block.
		[[ -n "${line}" ]] || continue
		# Git and extension materialization output use leading spaces for a
		# standalone terminal. The block prefix already provides the alignment;
		# remove that presentation padding so continuation payloads share one
		# start column. Compose also indents its own Container/Network/Volume
		# status rows, but indentation in Django/application diagnostics remains
		# meaningful and is intentionally preserved.
		if [[ -n "${line}" ]]; then
			local trimmed_line="${line#"${line%%[![:space:]]*}"}"
			local original_trimmed_line="${trimmed_line}"
			# Backend/container applications may already prefix records with an
			# ISO timestamp. The HFL block supplies the single timestamp for this
			# output record; remove the nested prefix so it is not shown twice.
			if [[ "${source}" == "backend" || "${source}" == "docker" || "${source}" == "sourcelens" ]] \
				&& [[ "${trimmed_line}" =~ ^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]+Z\][[:space:]]+(.*)$ ]]; then
				trimmed_line="${BASH_REMATCH[1]}"
			fi
			if [[ "${source}" == "backend" || "${source}" == "docker" || "${source}" == "sourcelens" ]]; then
				case "${trimmed_line}" in
				'[INFO] '*) trimmed_line="${trimmed_line#'[INFO] '}" ;;
				'[INFO ] '*) trimmed_line="${trimmed_line#'[INFO ] '}" ;;
				esac
			fi
			if [[ "${source}" == "git" || "${source}" == "extensions" \
				|| "${source}" == "backend" || "${source}" == "sourcelens" ]]; then
				line="${trimmed_line}"
			elif [[ "${source}" == "docker" ]]; then
				case "${original_trimmed_line}" in
				Container\ * | Network\ * | Volume\ * | Image\ * | '[INFO] '* | '[INFO ] '*)
					line="${trimmed_line}"
					;;
				esac
			fi
		fi
		if [[ "${first}" -eq 1 ]]; then
			printf '%s%s\n' "${prefix}" "${line}" >&2
			first=0
		else
			printf '%*s%s\n' "${prefix_width}" '' "${line}" >&2
		fi
	done
}

# Emit a nested installer or helper transcript as a compact section. Unlike an
# output block, continuation lines use a small fixed indent instead of aligning
# under the long HFL envelope. This keeps detailed Agent/Gateway transcripts
# readable while preserving their native status markers and hierarchy.
hfl_log_output_section() {
	local source="${1:-${HFL_LOG_COMPONENT:-hfl}}" line tag prefix first=1 timestamp
	tag="$(hfl_log_status_token 'OUT ')"
	if [[ "${HFL_LOG_TERMINAL_TIMESTAMPS}" == "1" ]]; then
		timestamp="$(hfl_log_timestamp)"
		printf -v prefix '[%s] [%s] [%s] ' "${timestamp}" "${tag}" "${source}"
	else
		prefix="[${tag}] "
	fi
	while IFS= read -r line || [[ -n "${line}" ]]; do
		[[ -n "${line}" ]] || continue
		if [[ -n "${line}" ]]; then
			local trimmed_line="${line#"${line%%[![:space:]]*}"}"
			local original_trimmed_line="${trimmed_line}"
			if [[ "${source}" == "backend" || "${source}" == "docker" || "${source}" == "sourcelens" ]] \
				&& [[ "${trimmed_line}" =~ ^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]+Z\][[:space:]]+(.*)$ ]]; then
				trimmed_line="${BASH_REMATCH[1]}"
			fi
			if [[ "${source}" == "backend" || "${source}" == "docker" || "${source}" == "sourcelens" ]]; then
				case "${trimmed_line}" in
				'[INFO] '*) trimmed_line="${trimmed_line#'[INFO] '}" ;;
				'[INFO ] '*) trimmed_line="${trimmed_line#'[INFO ] '}" ;;
				esac
			fi
			if [[ "${source}" == "backend" || "${source}" == "sourcelens" ]]; then
				line="${trimmed_line}"
			fi
			if [[ "${source}" == "docker" ]]; then
				case "${original_trimmed_line}" in
				Container\ * | Network\ * | Volume\ * | Image\ * | '[INFO] '* | '[INFO ] '*)
					line="${trimmed_line}"
					;;
				esac
			fi
		fi
		if [[ "${first}" -eq 1 ]]; then
			printf '%s%s\n' "${prefix}" "${line}" >&2
			first=0
		else
			printf '  %s\n' "${line}" >&2
		fi
	done
}

hfl_log_terminal_columns() {
	local console_fd=$1 columns="${HFL_LOG_TERMINAL_WRAP_COLUMNS:-}"
	if [[ "${columns}" =~ ^[1-9][0-9]*$ ]]; then
		printf '%s' "${columns}"
		return 0
	fi
	if [[ -t "${console_fd}" ]]; then
		columns="$(stty size <"/dev/fd/${console_fd}" 2>/dev/null | awk '{print $2}')"
	fi
	if [[ "${columns}" =~ ^[1-9][0-9]*$ ]]; then
		printf '%s' "${columns}"
	else
		printf '0'
	fi
}

hfl_log_terminal_wrap_stream() {
	local console_fd=$1 columns
	columns="$(hfl_log_terminal_columns "${console_fd}")"
	if [[ ! "${columns}" =~ ^[1-9][0-9]*$ || "${columns}" -lt 40 ]]; then
		cat >&"${console_fd}"
		return 0
	fi
	awk -v width="${columns}" '
function strip_ansi(value) {
	gsub(/\033\[[0-9;]*m/, "", value)
	return value
}
function emit_wrapped(prefix, message,    prefix_width, available, indent, rest, part, cut, i, first) {
	prefix_width = length(strip_ansi(prefix))
	available = width - prefix_width
	# Do not corrupt embedded ANSI sequences in a message. Such output is
	# uncommon for structured records and remains available in the full log.
	if (available < 8 || length(strip_ansi(prefix message)) <= width || index(message, "\033") > 0) {
		print prefix message
		return
	}
	indent = sprintf("%*s", prefix_width, "")
	rest = message
	first = 1
	while (length(rest) > 0) {
		part = substr(rest, 1, available)
		if (length(rest) <= available) {
			if (first) print prefix part
			else print indent part
			break
		}
		cut = 0
		for (i = length(part); i > 0; i--) {
			if (substr(part, i, 1) ~ /[[:space:]]/) {
				cut = i
				break
			}
		}
		if (cut > 0) {
			part = substr(rest, 1, cut)
			rest = substr(rest, cut + 1)
			sub(/^[[:space:]]+/, "", rest)
		} else {
			rest = substr(rest, available + 1)
		}
		if (first) print prefix part
		else print indent part
		first = 0
	}
}
{
	structured = "^\\[[^]]+\\] \\[[^]]+\\] \\[[^]]+\\] "
	if (match($0, structured)) {
		prefix = substr($0, 1, RLENGTH)
		emit_wrapped(prefix, substr($0, RLENGTH + 1))
	} else {
		print
	}
}
' >&"${console_fd}"
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
	local log_file=$1 console_fd=$2 terminal_dir terminal_fifo terminal_pid status
	# A FIFO keeps both copies streaming: the terminal renderer may wrap long
	# structured messages, while the log-file copy remains one complete,
	# lossless record. This avoids process-substitution races at stream end.
	terminal_dir="$(mktemp -d "${TMPDIR:-/tmp}/hyperfilelens-log-terminal.XXXXXX")" || return 1
	terminal_fifo="${terminal_dir}/stream"
	if ! mkfifo "${terminal_fifo}"; then
		rmdir "${terminal_dir}" 2>/dev/null || true
		return 1
	fi
	hfl_log_terminal_wrap_stream "${console_fd}" <"${terminal_fifo}" >&"${console_fd}" &
	terminal_pid=$!
	if tee "${terminal_fifo}" \
		| hfl_normalize_native_stream \
		| hfl_log_timestamp_stream "${log_file}"; then
		status=0
	else
		status="${PIPESTATUS[0]}"
	fi
	wait "${terminal_pid}" 2>/dev/null || true
	rm -f -- "${terminal_fifo}"
	rmdir "${terminal_dir}" 2>/dev/null || true
	return "${status}"
}

hfl_native_terminal_available() {
	[[ -t 3 || -t 4 || ( -t 1 && -t 2 ) ]]
}

# Run a long-lived native tool through a real pseudo-terminal when the caller
# started from an interactive terminal. Docker BuildKit (and tools such as
# npm running inside a Docker build) use the terminal capability to select
# their compact, live progress renderer. The normal dev log capture redirects
# stdout/stderr through pipes, which otherwise makes BuildKit fall back to its
# verbose plain renderer. The command output is copied byte-for-byte to the
# original terminal and normalized only in the persisted log file.
hfl_run_native_command() {
	local console_fd="" command status errexit_was_set=0
	local -a pipeline_status

	if [[ -t 3 ]]; then
		console_fd=3
	elif [[ -t 4 ]]; then
		console_fd=4
	elif [[ -t 1 && -t 2 ]]; then
		"$@"
		return $?
	else
		"$@"
		return $?
	fi

	# Without a configured session log, the caller is already interactive and
	# does not need a PTY proxy. This also keeps standalone helper usage simple.
	if [[ -z "${HFL_LOG_FILE:-}" ]] || ! command -v script >/dev/null 2>&1; then
		"$@"
		return $?
	fi

	printf -v command '%q ' "$@"
	command="${command% }"
	case "$-" in
	*e*) errexit_was_set=1 ;;
	esac
	set +e
	# Linux util-linux `script` supports -f/-c, while macOS ships the BSD
	# implementation which rejects both options. Keep the Linux invocation
	# unchanged and use its portable command-after-output-file form on Darwin.
	if [[ "$(uname -s)" == "Darwin" ]]; then
		script -q /dev/null bash -c "${command}" 2>&1 \
			| tee "/dev/fd/${console_fd}" \
			| hfl_normalize_native_stream \
			| hfl_log_timestamp_stream "${HFL_LOG_FILE}"
	else
		script -qefc "${command}" /dev/null 2>&1 \
			| tee "/dev/fd/${console_fd}" \
			| hfl_normalize_native_stream \
			| hfl_log_timestamp_stream "${HFL_LOG_FILE}"
	fi
	pipeline_status=("${PIPESTATUS[@]}")
	if [[ "${errexit_was_set}" -eq 1 ]]; then
		set -e
	else
		set +e
	fi

	# The native command is authoritative. A logging-side failure must not mask
	# its exit code, but a successful command still reports a broken log stream.
	status="${pipeline_status[0]:-1}"
	if [[ "${status}" -eq 0 ]]; then
		for status in "${pipeline_status[@]}"; do
			[[ "${status}" -eq 0 ]] || return "${status}"
		done
	fi
	return "${status}"
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
	if hfl_log_color_enabled_for "$(hfl_log_output_fd stdout)"; then
		printf '\033[1;35m'
	fi
	cat <<'BANNER'
 _   _                       _____ _ _      _
| | | |_   _ _ __   ___ _ _|  ___(_) | ___| |    ___ _ __  ___
| |_| | | | | '_ \ / _ \ '__| |_  | | |/ _ \ |   / _ \ '_ \/ __|
|  _  | |_| | |_) |  __/ |  |  _| | | |  __/ |__|  __/ | | \__ \
|_| |_|\__, | .__/ \___|_|  |_|   |_|_|\___|_____\___|_| |_|___/
       |___/|_|                     INSTALLER
BANNER
	if hfl_log_color_enabled_for "$(hfl_log_output_fd stdout)"; then
		printf '\033[0m'
	fi
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
