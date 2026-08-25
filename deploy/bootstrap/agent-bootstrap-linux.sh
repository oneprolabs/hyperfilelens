#!/usr/bin/env bash
# HyperFileLens Agent enrollment bootstrap (Linux). Rendered by GET /enrollment/bootstrap.
set -euo pipefail

# Avoid getcwd / job-working-directory noise when the caller cwd was removed
# (common if the user ran the one-liner from a stale /opt/hyperfilelens-agent).
cd / || cd /tmp || true

export HFL_ORG_KEY="__HFL_ORG_KEY__"
export HFL_NODE_ROLE="__HFL_NODE_ROLE__"
export HFL_NODE_TOKEN="__HFL_NODE_TOKEN__"
export HFL_INSTALLATION_MODE="__HFL_INSTALLATION_MODE__"
export HFL_API_BASE="__HFL_API_BASE__"
export HFL_WSS_URL="__HFL_WSS_URL__"
export HFL_INSECURE_TLS="__HFL_INSECURE_TLS__"

hfl_fail() {
	printf '  [FAIL] %s\n' "$1" >&2
	exit "${2:-1}"
}

hfl_step() {
	printf '  [....] %s\n' "$1"
}

hfl_ok() {
	printf '  [ OK ] %s\n' "$1"
}

# Keep compatibility with both old systemd (key/value output) and newer
# loginctl output. CentOS 7 ships systemd 219, where `--value` is unavailable.
hfl_user_linger_state() {
	local raw
	raw="$(loginctl show-user "$(id -u)" --property=Linger 2>/dev/null)" || return 1
	printf '%s\n' "${raw}" | sed -n 's/^Linger=//p' | tr -d '[:space:]'
}

hfl_format_bytes() {
	awk -v bytes="$1" 'BEGIN {
		split("B KiB MiB GiB TiB", units, " ")
		value = bytes + 0
		unit = 1
		while (value >= 1024 && unit < 5) {
			value /= 1024
			unit++
		}
		if (unit == 1) printf "%.0f %s", value, units[unit]
		else printf "%.1f %s", value, units[unit]
	}'
}

hfl_format_duration() {
	local seconds="$1"
	if ((seconds >= 3600)); then
		printf '%dh %dm' "$((seconds / 3600))" "$(((seconds % 3600) / 60))"
	elif ((seconds >= 60)); then
		printf '%dm %ds' "$((seconds / 60))" "$((seconds % 60))"
	else
		printf '%ds' "$seconds"
	fi
}

hfl_download_progress_line() {
	local label="$1" downloaded="$2" total="$3" elapsed="$4"
	local percent=0 filled=0 rate=0 eta=0 bar
	if ((total > 0)); then
		percent=$((downloaded * 100 / total))
		((percent > 100)) && percent=100
		filled=$((percent * 20 / 100))
	fi
	if ((elapsed > 0)); then
		rate=$((downloaded / elapsed))
	fi
	bar="[$(printf '%*s' "${filled}" '' | tr ' ' '#')$(printf '%*s' "$((20 - filled))" '' | tr ' ' '-') ]"
	bar="${bar/ ]/]}"
	if ((total > 0)); then
		if ((rate > 0 && downloaded < total)); then
			eta=$(((total - downloaded) / rate))
			printf '  [....] %s %s | %d%% | %s / %s | %s/s | ETA %s' \
				"${label}" "${bar}" "${percent}" \
				"$(hfl_format_bytes "${downloaded}")" "$(hfl_format_bytes "${total}")" \
				"$(hfl_format_bytes "${rate}")" "$(hfl_format_duration "${eta}")"
		else
			printf '  [....] %s %s | %d%% | %s / %s | %s/s' \
				"${label}" "${bar}" "${percent}" \
				"$(hfl_format_bytes "${downloaded}")" "$(hfl_format_bytes "${total}")" \
				"$(hfl_format_bytes "${rate}")"
		fi
	else
		printf '  [....] %s %s downloaded | %s/s | elapsed %s' \
			"${label}" "$(hfl_format_bytes "${downloaded}")" \
			"$(hfl_format_bytes "${rate}")" "$(hfl_format_duration "${elapsed}")"
	fi
}

hfl_download_header_size() {
	local headers="$1"
	[[ -f "${headers}" ]] || { printf '0'; return 0; }
	awk '
		BEGIN { IGNORECASE = 1 }
		/^Content-Length:/ { gsub("\r", "", $2); if ($2 ~ /^[0-9]+$/) size = $2 }
		/^Content-Range:/ { split($3, parts, "/"); gsub("\r", "", parts[2]); if (parts[2] ~ /^[0-9]+$/) size = parts[2] }
		END { print size + 0 }
	' "${headers}" 2>/dev/null
}

hfl_download() {
	local label="$1"
	local url="$2"
	local destination="$3"
	local partial="${destination}.part"
	local headers="${partial}.headers.$$" started=${SECONDS} elapsed bytes total=0
	local curl_pid last_report=0 curl_rc=0
	local -a retry_connrefused=()
	rm -f "${partial}"
	rm -f "${headers}"
	# CentOS 7 ships curl 7.29, which predates --retry-connrefused.
	if curl --retry-connrefused --version >/dev/null 2>&1; then
		retry_connrefused=(--retry-connrefused)
	fi
	# ${arr[@]+...} keeps empty CURL_TLS safe under `set -u` on Bash < 4.4 (CentOS 7).
	curl ${CURL_TLS[@]+"${CURL_TLS[@]}"} \
		--fail --silent --show-error --location \
		--retry 3 ${retry_connrefused[@]+"${retry_connrefused[@]}"} --retry-delay 2 \
		--dump-header "${headers}" "${url}" -o "${partial}" &
	curl_pid=$!
	while kill -0 "${curl_pid}" 2>/dev/null; do
		total="$(hfl_download_header_size "${headers}")"
		if [[ -f "${partial}" ]]; then bytes="$(wc -c <"${partial}")"; else bytes=0; fi
		elapsed=$((SECONDS - started))
		if ((elapsed > last_report)); then
			if [[ -t 1 && "${TERM:-}" != "dumb" && -z "${NO_COLOR:-}" ]]; then
				printf '\r%s\033[K' "$(hfl_download_progress_line "${label}" "${bytes}" "${total}" "${elapsed}")"
			else
				printf '%s\n' "$(hfl_download_progress_line "${label}" "${bytes}" "${total}" "${elapsed}")"
			fi
			last_report="${elapsed}"
		fi
		sleep 1
	done
	if wait "${curl_pid}"; then
		curl_rc=0
	else
		curl_rc=$?
	fi
	if ((curl_rc != 0)); then
		[[ -t 1 && "${TERM:-}" != "dumb" && -z "${NO_COLOR:-}" ]] && printf '\n'
		rm -f "${partial}" "${headers}"
		hfl_fail "Failed to download ${label}." 3
	fi
	bytes="$(wc -c <"${partial}")"
	total="$(hfl_download_header_size "${headers}")"
	elapsed=$((SECONDS - started))
	((elapsed > 0)) || elapsed=1
	if [[ -t 1 && "${TERM:-}" != "dumb" && -z "${NO_COLOR:-}" ]]; then
		printf '\r%s\033[K\n' "$(hfl_download_progress_line "${label}" "${bytes}" "${total}" "${elapsed}")"
	else
		printf '%s\n' "$(hfl_download_progress_line "${label}" "${bytes}" "${total}" "${elapsed}")"
	fi
	rm -f "${headers}"
	mv -f "${partial}" "${destination}"
	rate="$(hfl_format_bytes "$((bytes / elapsed))")/s"
	hfl_ok "${label} downloaded ($(hfl_format_bytes "${bytes}") in ${elapsed}s, average ${rate})."
}

hfl_build_enroll_args() {
	HFL_ENROLL_ARGS=()
	local has_yes=0
	local arg
	for arg in "$@"; do
		case "${arg}" in
		--yes | -y) has_yes=1 ;;
		esac
		HFL_ENROLL_ARGS+=("${arg}")
	done
	if [[ "${HFL_ASSUME_YES:-1}" != "0" && "${has_yes}" -eq 0 ]]; then
		HFL_ENROLL_ARGS=(--yes "$@")
	fi
}

CURL_TLS=(-k)
if [[ "${HFL_INSECURE_TLS}" == "0" ]]; then
	CURL_TLS=()
fi

if ! command -v curl >/dev/null 2>&1; then
	hfl_fail "curl is required but not installed." 2
fi

RAW_ARCH="$(uname -m)"
case "${RAW_ARCH}" in
x86_64 | amd64) HFL_ARCH=amd64 ;;
aarch64 | arm64) HFL_ARCH=arm64 ;;
*)
	hfl_fail "Unsupported architecture ${RAW_ARCH} (only amd64/arm64)." 4
	;;
esac

if [[ "${HFL_INSTALLATION_MODE}" == "auto" ]]; then
	if [[ "$(id -u)" -eq 0 ]]; then
		export HFL_INSTALLATION_MODE="system"
		hfl_ok "Execution identity resolved to root; host-level continuous protection will be installed."
	else
		export HFL_INSTALLATION_MODE="user_continuous"
		hfl_ok "Execution identity resolved to $(id -un) (UID $(id -u)); user-level continuous protection will be installed."
	fi
fi

if [[ "${HFL_INSTALLATION_MODE}" == "user" || "${HFL_INSTALLATION_MODE}" == "user_continuous" ]]; then
	if [[ "$(id -u)" -eq 0 ]]; then
		hfl_fail "User-level installation must run as the current user without sudo." 1
	fi
	command -v systemctl >/dev/null 2>&1 \
		|| hfl_fail "systemctl is required for user-level installation." 2
	command -v loginctl >/dev/null 2>&1 \
		|| hfl_fail "loginctl is required for user-level installation." 2
	HFL_USER_LINGER="$(hfl_user_linger_state)" \
		|| hfl_fail "Unable to verify the current user's systemd sign-out behavior." 2
	[[ "${HFL_USER_LINGER}" == "yes" || "${HFL_USER_LINGER}" == "no" ]] \
		|| hfl_fail "Unable to parse the current user's systemd linger state." 2
	if [[ "${HFL_INSTALLATION_MODE}" == "user_continuous" && "${HFL_USER_LINGER}" != "yes" ]]; then
		command -v sudo >/dev/null 2>&1 \
			|| hfl_fail "Administrator authorization is required once to enable systemd user lingering (sudo is not available)." 2
		hfl_step "Enabling systemd user lingering for $(id -un)."
		sudo loginctl enable-linger "$(id -un)" \
			|| hfl_fail "Could not enable systemd user lingering. Ask an administrator to run: sudo loginctl enable-linger $(id -un)" 2
		HFL_USER_LINGER="$(hfl_user_linger_state)" \
			|| hfl_fail "Unable to verify the current user's systemd linger state after authorization." 2
		[[ "${HFL_USER_LINGER}" == "yes" || "${HFL_USER_LINGER}" == "no" ]] \
			|| hfl_fail "Unable to parse the current user's systemd linger state after authorization." 2
		[[ "${HFL_USER_LINGER}" == "yes" ]] \
			|| hfl_fail "systemd user lingering is still disabled after administrator authorization." 2
		hfl_ok "systemd user lingering is enabled."
	fi
	if ! systemctl --user show-environment >/dev/null 2>&1; then
		hfl_fail "A working systemd user service manager is required for user-level installation." 2
	fi
	if [[ "${HFL_INSTALLATION_MODE}" == "user" && "${HFL_USER_LINGER}" == "yes" ]]; then
		hfl_fail "Current-user protection must pause after sign-out, but systemd user lingering is enabled. Choose User files continuous protection, or disable linger only if no other user services depend on it." 2
	fi
else
	if [[ "$(id -u)" -ne 0 ]]; then
		hfl_fail "Administrator privileges are required. Re-run with sudo." 1
	fi
	if ! command -v systemctl >/dev/null 2>&1 \
		|| [[ ! -d /run/systemd/system ]] \
		|| ! systemctl show-environment >/dev/null 2>&1; then
		hfl_fail "This release requires a systemd-based Linux distribution. OpenRC, non-systemd, and container deployments are not supported." 2
	fi
fi

BIN="${TMPDIR:-/tmp}/hfl-enroll-$$"
cleanup() { rm -f "${BIN}" "${BIN}.part"; }
trap cleanup EXIT

hfl_download \
	"HyperFileLens enrollment helper" \
	"${HFL_API_BASE}/media/enroll-bootstrap/hfl-enroll-linux-${HFL_ARCH}" \
	"${BIN}"
chmod +x "${BIN}"
# Do not exec: trap EXIT must run after install so /tmp/hfl-enroll-* is removed on success or failure.
hfl_build_enroll_args "$@"
"${BIN}" install "${HFL_ENROLL_ARGS[@]}"
