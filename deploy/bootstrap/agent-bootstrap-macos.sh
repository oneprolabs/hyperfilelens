#!/usr/bin/env bash
# HyperFileLens Agent enrollment bootstrap (macOS). Rendered by GET /enrollment/bootstrap.
set -euo pipefail

# Avoid getcwd / job-working-directory noise when the caller cwd was removed
# (common if the user ran the one-liner from a stale /opt/hyperfilelens-agent).
cd / || cd /tmp || true

export HFL_ORG_KEY="__HFL_ORG_KEY__"
export HFL_NODE_ROLE="__HFL_NODE_ROLE__"
export HFL_NODE_TOKEN="__HFL_NODE_TOKEN__"
export HFL_API_BASE="__HFL_API_BASE__"
export HFL_WSS_URL="__HFL_WSS_URL__"
export HFL_INSECURE_TLS="__HFL_INSECURE_TLS__"

hfl_now() {
	date -u +%Y-%m-%dT%H:%M:%S.000Z 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ
}

hfl_fail() {
	printf '[%s] [FAIL ] %s\n' "$(hfl_now)" "$1" >&2
	exit "${2:-1}"
}

hfl_step() {
	printf '[%s] [....] %s\n' "$(hfl_now)" "$1"
}

hfl_ok() {
	printf '[%s] [ OK  ] %s\n' "$(hfl_now)" "$1"
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
	# Older system curl builds may predate --retry-connrefused.
	if curl --retry-connrefused --version >/dev/null 2>&1; then
		retry_connrefused=(--retry-connrefused)
	fi
	# ${arr[@]+...} keeps empty CURL_TLS safe under `set -u` on Bash < 4.4.
	if ! curl ${CURL_TLS[@]+"${CURL_TLS[@]}"} \
		--fail --show-error --location --progress-bar \
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

CURL_TLS=(-k)
if [[ "${HFL_INSECURE_TLS}" == "0" ]]; then
	CURL_TLS=()
fi

if ! command -v curl >/dev/null 2>&1; then
	hfl_fail "curl is required but not installed." 2
fi

if [[ "$(id -u)" -ne 0 ]]; then
	hfl_fail "Administrator privileges are required. Re-run with sudo." 1
fi

if ! command -v launchctl >/dev/null 2>&1; then
	hfl_fail "launchd is required to install the agent service on macOS." 2
fi

RAW_ARCH="$(uname -m)"
case "${RAW_ARCH}" in
x86_64 | amd64) HFL_ARCH=amd64 ;;
aarch64 | arm64) HFL_ARCH=arm64 ;;
*)
	hfl_fail "Unsupported architecture ${RAW_ARCH} (only amd64/arm64)." 4
	;;
esac

BIN="${TMPDIR:-/tmp}/hfl-enroll-$$"
cleanup() { rm -f "${BIN}" "${BIN}.part"; }
trap cleanup EXIT

hfl_download \
	"HyperFileLens enrollment helper" \
	"${HFL_API_BASE}/media/enroll-bootstrap/hfl-enroll-darwin-${HFL_ARCH}" \
	"${BIN}"
chmod +x "${BIN}"
# Do not exec: trap EXIT must run after install so /tmp/hfl-enroll-* is removed on success or failure.
"${BIN}" install "$@"
