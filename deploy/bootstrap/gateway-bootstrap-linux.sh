#!/usr/bin/env bash
# HyperFileLens Data Gateway enrollment bootstrap (Linux only).
# Rendered by GET /enrollment/bootstrap-gateway.
#
# Stage order (matches Agent bootstrap):
#   1) local platform gates only (curl / arch / root / systemd)
#   2) download lightweight hfl-enroll
#   3) hfl-enroll runs full preflight (console, SourceLens, packages, …),
#      then Agent package install + register
#   4) hfl-enroll installs Docker (if needed) and LensNode during AI engine setup
# Network checks and Docker CE must not run here — that would mutate or probe
# before the enrollment helper's full preflight.
set -euo pipefail

# Avoid getcwd / job-working-directory noise when the caller cwd was removed
# (common if the user ran the one-liner from a stale /opt/hyperfilelens-agent).
cd / || cd /tmp || true

export HFL_ORG_KEY="__HFL_ORG_KEY__"
export HFL_NODE_ROLE="gateway"
export HFL_NODE_TOKEN="__HFL_NODE_TOKEN__"
export HFL_API_BASE="__HFL_API_BASE__"
export HFL_WSS_URL="__HFL_WSS_URL__"
export HFL_INSECURE_TLS="__HFL_INSECURE_TLS__"

hfl_step() {
	printf '  [....] %s\n' "$1"
}

hfl_ok() {
	printf '  [ OK ] %s\n' "$1"
}

hfl_fail() {
	printf '  [FAIL] %s\n' "$1" >&2
	exit "${2:-1}"
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

hfl_download() {
	local label="$1"
	local url="$2"
	local destination="$3"
	local partial="${destination}.part"
	local started=${SECONDS} elapsed bytes rate
	local -a retry_connrefused=()
	rm -f "${partial}"
	hfl_step "Downloading ${label}."
	# CentOS 7 ships curl 7.29, which predates --retry-connrefused.
	if curl --retry-connrefused --version >/dev/null 2>&1; then
		retry_connrefused=(--retry-connrefused)
	fi
	# ${arr[@]+...} keeps empty CURL_TLS safe under `set -u` on Bash < 4.4 (CentOS 7).
	if ! curl ${CURL_TLS[@]+"${CURL_TLS[@]}"} \
		--fail --silent --show-error --location \
		--retry 3 ${retry_connrefused[@]+"${retry_connrefused[@]}"} --retry-delay 2 \
		"${url}" -o "${partial}"; then
		rm -f "${partial}"
		hfl_fail "Failed to download ${label}." 3
	fi
	mv -f "${partial}" "${destination}"
	bytes="$(wc -c <"${destination}")"
	elapsed=$((SECONDS - started))
	((elapsed > 0)) || elapsed=1
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
	hfl_fail "Unsupported architecture ${RAW_ARCH} (gateway install supports amd64 only today)." 4
	;;
esac

if [[ "${HFL_ARCH}" != "amd64" ]]; then
	hfl_fail "Data Gateway full install (Docker + AI engine) requires amd64 (current: ${RAW_ARCH})." 4
fi

if [[ "$(id -u)" -ne 0 ]]; then
	hfl_fail "Administrator privileges are required. Re-run with sudo." 1
fi

if ! command -v systemctl >/dev/null 2>&1 \
	|| [[ ! -d /run/systemd/system ]] \
	|| ! systemctl show-environment >/dev/null 2>&1; then
	hfl_fail "This release requires a systemd-based Linux distribution. OpenRC, non-systemd, and container deployments are not supported." 2
fi

BIN="${TMPDIR:-/tmp}/hfl-enroll-$$"
cleanup() { rm -f "${BIN}" "${BIN}.part"; }
trap cleanup EXIT

hfl_download \
	"HyperFileLens enrollment helper" \
	"${HFL_API_BASE}/media/enroll-bootstrap/hfl-enroll-linux-${HFL_ARCH}" \
	"${BIN}"
chmod +x "${BIN}"
# Do not exec: trap EXIT must run after gateway-install so /tmp/hfl-enroll-* is removed.
hfl_build_enroll_args "$@"
"${BIN}" gateway-install "${HFL_ENROLL_ARGS[@]}"
