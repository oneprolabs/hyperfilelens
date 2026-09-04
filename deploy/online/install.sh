#!/usr/bin/env bash
# Bootstrap a verified public Community installation or upgrade.
set -euo pipefail

DEFAULT_GLOBAL_REGISTRY_PREFIX="docker.io/oneprolabs"
DEFAULT_CN_REGISTRY_PREFIX="registry.cn-beijing.aliyuncs.com/oneprolabs"
DEFAULT_GLOBAL_DOCKER_CE_APT_BASE="https://download.docker.com/linux/ubuntu"
DEFAULT_CN_DOCKER_CE_APT_BASE="https://mirrors.aliyun.com/docker-ce/linux/ubuntu"
DOCKER_GPG_PRIMARY_FINGERPRINT="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
MIN_DOCKER_ENGINE_VERSION="24.0.0"
MIN_DOCKER_COMPOSE_VERSION="2.20.0"
GLOBAL_REGISTRY_PREFIX="${HFL_GLOBAL_REGISTRY_PREFIX:-${DEFAULT_GLOBAL_REGISTRY_PREFIX}}"
CN_REGISTRY_PREFIX="${HFL_CN_REGISTRY_PREFIX:-${DEFAULT_CN_REGISTRY_PREFIX}}"
MIRROR=""
TAG=""
ASSUME_YES=0
SESSION_DIR=""
SOURCE_NAME=""
REGION=""
TAGS_API_URL=""
REGISTRY_NAME=""
RELEASE_VERSION=""
RELEASE_COMMIT=""
RECENT_TAGS=""
INSTALL_ROOT="/opt/hyperfilelens"
INSTALL_ACTION="Install"
MAX_TAG_PAGES=100
ONLINE_LOG_FILE=""
ONLINE_INTERACTIVE=0
HOST_UBUNTU_CODENAME=""
DOCKER_CE_APT_BASE=""
DOCKER_CE_GPG_URL=""
DOCKER_CE_SOURCE_NAME=""
DOCKER_RUNTIME_ACTION=""
DOCKER_ENGINE_VERSION=""
DOCKER_COMPOSE_VERSION=""
DOCKER_ENGINE_PACKAGE_VERSION=""
DOCKER_CLI_PACKAGE_VERSION=""
DOCKER_CONTAINERD_PACKAGE_VERSION=""
DOCKER_COMPOSE_PACKAGE_VERSION=""
DOCKER_TARGET_ENGINE_VERSION=""
DOCKER_TARGET_COMPOSE_VERSION=""
DOCKER_PACKAGE_INSTALL_ATTEMPTED=0
DOCKER_BOOTSTRAPPED=0
COMPOSE_PACKAGE_INSTALL_ATTEMPTED=0
COMPOSE_BOOTSTRAPPED=0
APT_FAILURE_DPKG_CLEAN=0
CURL_RETRY_ARGS=()
APT_RETRY_ARGS=(
	-o Acquire::Retries=3
	-o Acquire::http::Timeout=60
	-o Acquire::https::Timeout=60
	-o DPkg::Lock::Timeout=120
)

usage() {
	cat <<'USAGE'
Usage: install.sh --mirror cn|global [--tag vX.Y.Z] [--yes]

Installs the latest HyperFileLens Community tag on a new host.
Running the command again upgrades an existing Community installation through
the normal managed backup and blue/green lifecycle.

--mirror cn|global     Select Gitee + Alibaba Cloud or GitHub + Docker Hub
--tag vX.Y.Z           Install one specific Community tag
--yes                  Skip the interactive confirmation (automation only)
USAGE
}

fail() {
	printf '[FAIL] %s\n' "$*" >&2
	exit 1
}

print_banner() {
	cat <<'BANNER'
 _   _                       _____ _ _      _
| | | |_   _ _ __   ___ _ _|  ___(_) | ___| |    ___ _ __  ___
| |_| | | | | '_ \ / _ \ '__| |_  | | |/ _ \ |   / _ \ '_ \/ __|
|  _  | |_| | |_) |  __/ |  |  _| | | |  __/ |__|  __/ | | \__ \
|_| |_|\__, | .__/ \___|_|  |_|   |_|_|\___|_____\___|_| |_|___/
       |___/|_|                     INSTALLER
BANNER
	printf '\n%s\n%s\n' 'HyperFileLens Community Online Installer' '----------------------------------------------------------------'
}

timestamp_log_stream() {
	local log_file=$1 line timestamp
	local TZ=UTC
	export TZ
	# Strip all CSI terminal controls (colour, cursor movement, erase-line)
	# before persisting output; the live terminal still receives the original
	# stream through tee.
	sed $'s/\033\[[0-9;?]*[ -/]*[@-~]//g' | while IFS= read -r line || [[ -n "${line}" ]]; do
		printf -v timestamp '%(%Y-%m-%dT%H:%M:%S.000Z)T' -1
		printf '[%s] %s\n' "${timestamp}" "${line}" >>"${log_file}"
	done
}

capture_log_stream() {
	local log_file=$1 console_fd=$2
	tee "/dev/fd/${console_fd}" \
		| tr '\r' '\n' \
		| timestamp_log_stream "${log_file}"
}

apt_failure_is_transient() {
	local log_file=${1:-}
	[[ -s "${log_file}" ]] || return 1
	grep -Eiq \
		'failed to fetch.*(timed out|could not connect|connection (reset|failed)|temporary failure resolving|could not resolve|network is unreachable)|connection timed out|could not connect|connection reset|connection failed|temporary failure resolving|could not resolve|network is unreachable|tls.*(error|connection)' \
		"${log_file}"
}

dpkg_state_clean_for_retry() {
	local audit_output
	if ! audit_output="$(dpkg --audit 2>&1)"; then
		printf '[WARN] Ubuntu package state could not be inspected; automatic retry is disabled.\n' >&2
		[[ -z "${audit_output}" ]] || printf '%s\n' "${audit_output}" >&2
		return 1
	fi
	if [[ -n "${audit_output}" ]]; then
		printf '[WARN] Ubuntu package state is incomplete; automatic retry is disabled.\n' >&2
		printf '%s\n' "${audit_output}" >&2
		return 1
	fi
	return 0
}

apt_install_with_network_retry() {
	local install_log=$1
	shift
	local attempt=1
	while :; do
		if DEBIAN_FRONTEND=noninteractive LC_ALL=C apt-get "${APT_RETRY_ARGS[@]}" \
			"$@" >"${install_log}" 2>&1; then
			return 0
		fi
		if ((attempt >= 2)) || ! apt_failure_is_transient "${install_log}"; then
			if ((attempt >= 2)) && apt_failure_is_transient "${install_log}" \
				&& dpkg_state_clean_for_retry; then
				APT_FAILURE_DPKG_CLEAN=1
			fi
			preserve_apt_failure_log "${install_log}"
			return 1
		fi
		if ! dpkg_state_clean_for_retry; then
			preserve_apt_failure_log "${install_log}"
			return 1
		fi
		printf '[ OK ] Ubuntu package state is clean\n' >&2
		printf '[....] APT package download failed; retrying in 60 seconds (2/2)\n' >&2
		sleep 60
		attempt=2
	done
}

configure_logging() {
	local stamp
	[[ -t 1 || -t 2 ]] && ONLINE_INTERACTIVE=1 || true
	stamp="$(date -u +%Y%m%dT%H%M%SZ)"
	ONLINE_LOG_FILE="${INSTALL_ROOT}/logs/install-${stamp}-$$.log"
	mkdir -p "$(dirname "${ONLINE_LOG_FILE}")" \
		|| fail "could not create the online installation log directory"
	[[ ! -L "${ONLINE_LOG_FILE}" ]] \
		|| fail "refusing to write the online installation log through a symbolic link"
	touch "${ONLINE_LOG_FILE}" \
		|| fail "could not create the online installation log"
	chmod 600 "${ONLINE_LOG_FILE}" \
		|| fail "could not secure the online installation log"
	exec 3>&1
	exec 4>&2
	exec > >(capture_log_stream "${ONLINE_LOG_FILE}" 3) \
		2> >(capture_log_stream "${ONLINE_LOG_FILE}" 4)
}

preserve_apt_failure_log() {
	local source_log=${1:-}
	local preserved_log
	[[ -n "${ONLINE_LOG_FILE}" && -s "${source_log}" ]] || return 0
	preserved_log="${ONLINE_LOG_FILE%.log}-apt.log"
	if [[ -L "${preserved_log}" ]]; then
		printf '[WARN] Refusing to write the APT diagnostic log through a symbolic link: %s\n' \
			"${preserved_log}" >&2
		return 0
	fi
	if ! install -m 0600 "${source_log}" "${preserved_log}"; then
		printf '[WARN] Could not preserve the full APT diagnostic log: %s\n' \
			"${preserved_log}" >&2
		return 0
	fi
	printf '[INFO] Full APT output saved to %s\n' "${preserved_log}" >&2
}

print_target() {
	# Keep the online bootstrap target separate from the child package installer.
	# The child runs in parent-session mode and therefore does not print another
	# banner or duplicate Target block.
	# shellcheck disable=SC1091
	source /etc/os-release
	cat <<EOF

Target
  Version        ${TAG}
  Edition        Community
  Action         ${INSTALL_ACTION}
  Source         ${SOURCE_NAME}
  Registry       ${REGISTRY_NAME}
  Install path   ${INSTALL_ROOT}
  Platform       ${PRETTY_NAME:-Ubuntu} · linux/amd64
  Log file       ${ONLINE_LOG_FILE}
EOF

	printf '\nHost runtime\n'
	case "${DOCKER_RUNTIME_ACTION}" in
	install)
		printf '  Docker Engine  not installed → install %s\n' "${DOCKER_TARGET_ENGINE_VERSION}"
		printf '  Docker Compose not installed → install %s\n' "${DOCKER_TARGET_COMPOSE_VERSION}"
		printf '  Package source %s\n' "${DOCKER_CE_SOURCE_NAME}"
		printf '  Docker service enable and start\n'
		printf '  Lifecycle      retained when HyperFileLens is removed\n'
		;;
	install-compose)
		printf '  Docker Engine  %s · reuse\n' "${DOCKER_ENGINE_VERSION}"
		printf '  Docker Compose not installed → install docker-compose-plugin %s\n' \
			"${DOCKER_COMPOSE_PACKAGE_VERSION}"
		printf '  Package source %s\n' "${DOCKER_CE_SOURCE_NAME}"
		printf '  Install scope  Compose V2 plugin only\n'
		printf '  Docker service active\n'
		printf '  Lifecycle      retained when HyperFileLens is removed\n'
		;;
	reuse)
		printf '  Docker Engine  %s · reuse\n' "${DOCKER_ENGINE_VERSION}"
		printf '  Docker Compose %s · reuse\n' "${DOCKER_COMPOSE_VERSION}"
		printf '  Docker service active\n'
		;;
	*) fail "internal Docker runtime action is invalid" ;;
	esac
}

cleanup() {
	local rc=$?
	trap - EXIT INT TERM
	if ((rc != 0 && APT_FAILURE_DPKG_CLEAN == 1)); then
		printf '\n[WARN] APT package installation failed after the retry.\n' >&2
		printf '[INFO] Ubuntu package state is clean. Run the same HyperFileLens installation command again later.\n' >&2
	elif ((rc != 0 && COMPOSE_PACKAGE_INSTALL_ATTEMPTED == 1 && COMPOSE_BOOTSTRAPPED == 0)); then
		printf '\n[WARN] Docker Compose V2 plugin installation did not complete and may have left a partially installed package.\n' >&2
		printf '[INFO] The existing Docker Engine was not replaced; review dpkg --audit and repair the package state manually before retrying.\n' >&2
	elif ((rc != 0 && COMPOSE_BOOTSTRAPPED == 1)); then
		printf '\n[WARN] Docker Compose V2 was installed, but HyperFileLens installation did not complete.\n' >&2
		printf '[INFO] Docker Compose was retained as a shared host runtime. Run the same command again to retry.\n' >&2
	elif ((rc != 0 && DOCKER_PACKAGE_INSTALL_ATTEMPTED == 1 && DOCKER_BOOTSTRAPPED == 0)); then
		printf '\n[WARN] Docker CE package installation did not complete and may have left partially installed packages.\n' >&2
		printf '[INFO] Review dpkg --audit and repair the Docker package state manually before retrying.\n' >&2
	elif ((rc != 0 && DOCKER_BOOTSTRAPPED == 1)); then
		printf '\n[WARN] Docker Engine and Docker Compose V2 packages were installed, but HyperFileLens installation did not complete.\n' >&2
		printf '[INFO] Docker was retained as a shared host runtime. Run the same command again to retry.\n' >&2
	fi
	if [[ -n "${SESSION_DIR}" && -d "${SESSION_DIR}" ]]; then
		rm -rf -- "${SESSION_DIR}"
	fi
	exit "${rc}"
}

require_value() {
	[[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] \
		|| fail "$1 requires a value"
}

check_host() {
	[[ "${EUID}" -eq 0 ]] || fail "run this command through sudo"
	[[ -f /etc/os-release ]] || fail "missing /etc/os-release"
	# shellcheck disable=SC1091
	source /etc/os-release
	[[ "${ID:-}" == ubuntu ]] || fail "Ubuntu 20.04, 22.04, or 24.04 is required"
	case "${VERSION_ID:-}" in 20.04 | 22.04 | 24.04) ;; *)
		fail "Ubuntu 20.04, 22.04, or 24.04 is required"
		;; esac
	[[ "$(uname -m)" == x86_64 ]] || fail "linux/amd64 is required"
	command -v curl >/dev/null 2>&1 || fail "curl is required to start the online installer"
	command -v python3 >/dev/null 2>&1 || fail "python3 is required to start the online installer"
	command -v tar >/dev/null 2>&1 || fail "tar is required to start the online installer"
	case "${VERSION_ID}" in
	20.04) HOST_UBUNTU_CODENAME="focal" ;;
	22.04) HOST_UBUNTU_CODENAME="jammy" ;;
	24.04) HOST_UBUNTU_CODENAME="noble" ;;
	esac
}

install_host_tools() {
	local -a missing=()
	local tool plan="${SESSION_DIR}/host-tools-apt-plan.log"
	for tool in ca-certificates openssl python3 rsync tar; do
		case "${tool}" in
		ca-certificates) [[ -f /etc/ssl/certs/ca-certificates.crt ]] || missing+=(ca-certificates) ;;
		*) command -v "${tool}" >/dev/null 2>&1 || missing+=("${tool}") ;;
		esac
	done
	if ((${#missing[@]})); then
		printf '[....] Installing required host tools: %s\n' "${missing[*]}"
		apt-get "${APT_RETRY_ARGS[@]}" update \
			|| fail "could not refresh Ubuntu package metadata for required host tools"
		if ! LC_ALL=C apt-get "${APT_RETRY_ARGS[@]}" --simulate --no-remove --no-upgrade \
			--no-install-recommends install \
			"${missing[@]}" >"${plan}" 2>&1; then
			tail -n 20 "${plan}" >&2 || true
			fail "required host tools could not be resolved"
		fi
		validate_apt_install_plan "${plan}" "required host-tool installation"
		if ! DEBIAN_FRONTEND=noninteractive LC_ALL=C apt-get "${APT_RETRY_ARGS[@]}" \
			install -y --no-remove \
			--no-upgrade --no-install-recommends "${missing[@]}"; then
			fail "required host tools could not be installed"
		fi
	fi
}

configure_mirror() {
	case "${MIRROR}" in
	cn)
		SOURCE_NAME="Gitee"
		REGION="cn"
		TAGS_API_URL="https://gitee.com/api/v5/repos/oneprolabs/hyperfilelens/tags?per_page=100&page=1"
		REGISTRY_NAME="Alibaba Cloud"
		DOCKER_CE_APT_BASE="${HFL_DOCKER_CE_APT_BASE:-${DEFAULT_CN_DOCKER_CE_APT_BASE}}"
		;;
	global)
		SOURCE_NAME="GitHub"
		REGION="global"
		TAGS_API_URL="https://api.github.com/repos/oneprolabs/hyperfilelens/tags?per_page=100&page=1"
		REGISTRY_NAME="Docker Hub"
		DOCKER_CE_APT_BASE="${HFL_DOCKER_CE_APT_BASE:-${DEFAULT_GLOBAL_DOCKER_CE_APT_BASE}}"
		;;
	*) fail "--mirror must be cn or global" ;;
	esac
	DOCKER_CE_APT_BASE="${DOCKER_CE_APT_BASE%/}"
	DOCKER_CE_SOURCE_NAME="Docker CE · ${DOCKER_CE_APT_BASE}"
	DOCKER_CE_GPG_URL="${HFL_DOCKER_CE_GPG_URL:-${DOCKER_CE_APT_BASE}/gpg}"
	[[ "${DOCKER_CE_APT_BASE}" =~ ^https://[^[:space:]]+$ ]] \
		|| fail "HFL_DOCKER_CE_APT_BASE must be an HTTPS URL"
	[[ "${DOCKER_CE_GPG_URL}" =~ ^https://[^[:space:]]+$ ]] \
		|| fail "HFL_DOCKER_CE_GPG_URL must be an HTTPS URL"
}

docker_version_ge() {
	python3 - "$1" "$2" <<'PY'
import re
import sys


def parse(value):
    match = re.search(r"\d+(?:\.\d+)+", value)
    if match is None:
        raise ValueError(value)
    return tuple(int(part) for part in match.group(0).split("."))


try:
    actual = parse(sys.argv[1])
    required = parse(sys.argv[2])
    width = max(len(actual), len(required))
    actual += (0,) * (width - len(actual))
    required += (0,) * (width - len(required))
    raise SystemExit(0 if actual >= required else 1)
except (ValueError, IndexError):
    raise SystemExit(1)
PY
}

docker_engine_version() {
	docker version --format '{{.Server.Version}}' 2>/dev/null || true
}

docker_compose_version() {
	local version
	version="$(docker compose version --short 2>/dev/null || true)"
	if [[ -z "${version}" ]]; then
		version="$(docker compose version 2>/dev/null \
			| grep -Eo '[vV]?[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1 || true)"
	fi
	version="${version#v}"
	version="${version#V}"
	printf '%s' "${version}"
}

docker_packages_present() {
	local package
	for package in \
		docker-ce docker-ce-cli docker-buildx-plugin docker-compose-plugin \
		docker-ce-rootless-extras docker.io docker-compose-v2 docker-compose \
		containerd.io containerd runc moby-engine moby-cli moby-containerd \
		moby-compose podman-docker; do
		docker_package_payload_present "${package}" && return 0
	done
	return 1
}

docker_package_payload_present() {
	local status state
	status="$(dpkg-query -W -f='${db:Status-Abbrev}' "$1" 2>/dev/null || true)"
	state="${status:1:1}"
	[[ -n "${state}" && "${state}" != n && "${state}" != c ]]
}

foreign_docker_runtime_present() {
	local package docker_path
	for package in docker.io moby-engine moby-cli moby-containerd moby-compose podman-docker; do
		docker_package_installed "${package}" && return 0
	done
	docker_path="$(command -v docker 2>/dev/null || true)"
	[[ "${docker_path}" == /snap/* ]] && return 0
	return 1
}

docker_package_installed() {
	local status
	status="$(dpkg-query -W -f='${db:Status-Abbrev}' "$1" 2>/dev/null || true)"
	[[ "${status:1:1}" == i ]]
}

docker_ce_runtime_present() {
	docker_package_installed docker-ce \
		&& docker_package_installed docker-ce-cli
}

docker_residual_state_present() {
	local command
	for command in dockerd containerd containerd-shim runc; do
		command -v "${command}" >/dev/null 2>&1 && return 0
	done
	if [[ -e /snap/bin/docker ]] \
		|| compgen -G '/var/lib/snapd/snaps/docker_*.snap' >/dev/null; then
		return 0
	fi
	[[ -e /etc/docker || -e /var/lib/docker || -e /etc/containerd \
		|| -e /var/lib/containerd || -e /run/docker.sock || -e /var/run/docker.sock \
		|| -e /etc/systemd/system/docker.service || -e /lib/systemd/system/docker.service \
		|| -e /usr/lib/systemd/system/docker.service ]]
}

foreign_docker_apt_source_present() {
	local apt_root="${1:-/etc/apt}"
	local file
	while IFS= read -r file; do
		[[ "${file}" == "${apt_root%/}/sources.list.d/hyperfilelens-docker.list" ]] && continue
		[[ -f "${file}" && -r "${file}" ]] || continue
		if apt_source_has_enabled_value "${file}" "download.docker.com/linux/ubuntu" \
			|| apt_source_has_enabled_value "${file}" "/docker-ce/linux/ubuntu"; then
			return 0
		fi
	done < <(find "${apt_root}" -maxdepth 2 \( -type f -o -type l \) \
		\( -name 'sources.list' -o -name '*.list' -o -name '*.sources' \) \
		-print 2>/dev/null)
	return 1
}

apt_source_has_enabled_value() {
	local file=$1 value=$2
	if [[ "${file}" == *.sources ]]; then
		awk -v value="${value}" '
			BEGIN { RS="" }
			{
				enabled=1
				has_deb=0
				has_uri=0
				field=""
				count=split($0, lines, "\n")
				for (i=1; i<=count; i++) {
					line=lines[i]
					if (line ~ /^[[:space:]]*#/) continue
					lower=tolower(line)
					if (lower ~ /^[[:space:]]*enabled:[[:space:]]*no([[:space:]]|$)/) enabled=0
					if (line ~ /^[A-Za-z][A-Za-z0-9-]*:/) {
						field=lower
						sub(/^[[:space:]]*/, "", field)
						sub(/:.*/, "", field)
						data=line
						sub(/^[^:]*:[[:space:]]*/, "", data)
					} else if (line ~ /^[[:space:]]+/) {
						data=line
						sub(/^[[:space:]]+/, "", data)
					} else {
						field=""
						data=""
					}
					if (field == "types") {
						type_count=split(tolower(data), types, /[[:space:]]+/)
						for (type_index=1; type_index<=type_count; type_index++) {
							if (types[type_index] == "deb") has_deb=1
						}
					}
					if (field == "uris" && index(data, value)) has_uri=1
				}
				if (enabled && has_deb && has_uri) found=1
			}
			END { exit !found }
		' "${file}"
	else
		awk -v value="${value}" \
			'$1 == "deb" {line=$0; sub(/[[:space:]]+#.*/, "", line); if (index(line, value)) found=1} END {exit !found}' "${file}"
	fi
}

docker_apt_source_present() {
	local apt_root="${1:-/etc/apt}"
	local file
	while IFS= read -r file; do
		[[ -f "${file}" && -r "${file}" ]] || continue
		if apt_source_has_enabled_value "${file}" "download.docker.com/linux/ubuntu" \
			|| apt_source_has_enabled_value "${file}" "/docker-ce/linux/ubuntu"; then
			return 0
		fi
	done < <(find "${apt_root}" -maxdepth 2 \( -type f -o -type l \) \
		\( -name 'sources.list' -o -name '*.list' -o -name '*.sources' \) \
		-print 2>/dev/null)
	return 1
}

inspect_docker_runtime() {
	DOCKER_ENGINE_VERSION=""
	DOCKER_COMPOSE_VERSION=""
	if command -v docker >/dev/null 2>&1; then
		if foreign_docker_runtime_present || ! docker_ce_runtime_present; then
			fail "the existing Docker runtime is not a Docker CE installation; install Docker CE and Compose V2 manually, then rerun this installer"
		fi
		docker info >/dev/null 2>&1 \
			|| fail "Docker is installed but its daemon is unavailable; start or repair Docker manually, then rerun this installer"
		DOCKER_ENGINE_VERSION="$(docker_engine_version)"
		[[ -n "${DOCKER_ENGINE_VERSION}" ]] \
			|| fail "Docker Engine version could not be determined; repair Docker manually, then rerun this installer"
		docker_version_ge "${DOCKER_ENGINE_VERSION}" "${MIN_DOCKER_ENGINE_VERSION}" \
			|| fail "Docker Engine ${DOCKER_ENGINE_VERSION} does not meet the minimum required version ${MIN_DOCKER_ENGINE_VERSION}; upgrade Docker manually, then rerun this installer"
		DOCKER_COMPOSE_VERSION="$(docker_compose_version)"
		if [[ -z "${DOCKER_COMPOSE_VERSION}" ]]; then
			if ! selected_docker_apt_source_present && docker_apt_source_present; then
				DOCKER_CE_SOURCE_NAME="Existing Docker CE apt source"
			fi
			DOCKER_RUNTIME_ACTION="install-compose"
			return 0
		fi
		docker_version_ge "${DOCKER_COMPOSE_VERSION}" "${MIN_DOCKER_COMPOSE_VERSION}" \
			|| fail "Docker Compose ${DOCKER_COMPOSE_VERSION} does not meet the minimum required version ${MIN_DOCKER_COMPOSE_VERSION}; upgrade the Compose V2 plugin manually, then rerun this installer"
		DOCKER_RUNTIME_ACTION="reuse"
		return 0
	fi

	command -v docker-compose >/dev/null 2>&1 \
		&& fail "legacy docker-compose is installed without Docker Engine; repair or remove the partial Docker installation manually"
	if docker_packages_present; then
		fail "Docker packages are partially installed but the docker command is unavailable; repair or remove the existing Docker installation manually"
	fi
	if docker_residual_state_present; then
		fail "Docker state exists but the docker command is unavailable; repair or remove the existing Docker installation manually"
	fi
	if foreign_docker_apt_source_present; then
		fail "a Docker apt source is already configured without a usable Docker runtime; complete or remove that setup manually"
	fi
	DOCKER_RUNTIME_ACTION="install"
}

assert_docker_service_manager() {
	command -v systemctl >/dev/null 2>&1 \
		|| fail "systemd is required to install and manage Docker CE automatically"
	[[ -d /run/systemd/system ]] \
		|| fail "systemd is not running; install and start Docker manually, then rerun this installer"
}

load_docker_runtime_contract() {
	local contract="${SESSION_DIR}/source/deploy/online/docker-ce-versions.env"
	local parsed
	local -a values=()
	if [[ ! -f "${contract}" ]]; then
		# Releases published before the per-OS contract was introduced must
		# remain installable. Keep this compatibility map in the bootstrap script;
		# new releases use the versioned file above as the source of truth.
		case "${VERSION_ID}" in
		20.04)
			DOCKER_ENGINE_PACKAGE_VERSION='5:28.1.1-1~ubuntu.20.04~focal'
			DOCKER_CLI_PACKAGE_VERSION='5:28.1.1-1~ubuntu.20.04~focal'
			DOCKER_CONTAINERD_PACKAGE_VERSION='1.7.27-1'
			DOCKER_COMPOSE_PACKAGE_VERSION='2.35.1-1~ubuntu.20.04~focal'
			;;
		22.04)
			DOCKER_ENGINE_PACKAGE_VERSION='5:29.2.1-1~ubuntu.22.04~jammy'
			DOCKER_CLI_PACKAGE_VERSION='5:29.2.1-1~ubuntu.22.04~jammy'
			DOCKER_CONTAINERD_PACKAGE_VERSION='2.2.1-1~ubuntu.22.04~jammy'
			DOCKER_COMPOSE_PACKAGE_VERSION='5.0.2-1~ubuntu.22.04~jammy'
			;;
		24.04)
			DOCKER_ENGINE_PACKAGE_VERSION='5:29.2.1-1~ubuntu.24.04~noble'
			DOCKER_CLI_PACKAGE_VERSION='5:29.2.1-1~ubuntu.24.04~noble'
			DOCKER_CONTAINERD_PACKAGE_VERSION='2.2.1-1~ubuntu.24.04~noble'
			DOCKER_COMPOSE_PACKAGE_VERSION='5.0.2-1~ubuntu.24.04~noble'
			;;
		*) fail "Ubuntu ${VERSION_ID} is not supported by the Docker CE runtime contract" ;;
		esac
		DOCKER_TARGET_ENGINE_VERSION="${DOCKER_ENGINE_PACKAGE_VERSION#*:}"
		DOCKER_TARGET_ENGINE_VERSION="${DOCKER_TARGET_ENGINE_VERSION%%-*}"
		DOCKER_TARGET_COMPOSE_VERSION="${DOCKER_COMPOSE_PACKAGE_VERSION%%-*}"
		return 0
	fi
	parsed="$(python3 - "${contract}" "${VERSION_ID}" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
ubuntu_release = sys.argv[2]
prefix = "UBUNTU" + ubuntu_release.replace(".", "") + "_"
allowed = {"ENGINE_VERSION", "CLI_VERSION", "CONTAINERD_VERSION", "COMPOSE_PLUGIN_VERSION"}
values = {}
for raw_line in path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        raise SystemExit("invalid Docker CE runtime contract")
    key, value = line.split("=", 1)
    if not re.fullmatch(r"UBUNTU[0-9]{4}_(ENGINE|CLI|CONTAINERD|COMPOSE_PLUGIN)_VERSION", key):
        raise SystemExit("invalid Docker CE runtime contract")
    if not key.startswith(prefix):
        continue
    if not re.fullmatch(r"[A-Za-z0-9.+:~_-]+", value):
        raise SystemExit("invalid Docker CE runtime contract")
    component = key[len(prefix):]
    if component in values:
        raise SystemExit("duplicate Docker CE runtime contract entry")
    values[component] = value

missing = allowed - values.keys()
if missing:
    raise SystemExit("incomplete Docker CE runtime contract")

def product_version(value):
    value = value.split(":", 1)[-1]
    return value.split("-", 1)[0]


print(values["ENGINE_VERSION"])
print(values["CLI_VERSION"])
print(values["CONTAINERD_VERSION"])
print(values["COMPOSE_PLUGIN_VERSION"])
print(product_version(values["ENGINE_VERSION"]))
print(product_version(values["COMPOSE_PLUGIN_VERSION"]))
PY
	)" || fail "Community tag ${TAG} has an invalid Docker CE runtime contract"
	mapfile -t values <<<"${parsed}"
	[[ "${#values[@]}" -eq 6 ]] \
		|| fail "Community tag ${TAG} has an incomplete Docker CE runtime contract"
	DOCKER_ENGINE_PACKAGE_VERSION="${values[0]}"
	DOCKER_CLI_PACKAGE_VERSION="${values[1]}"
	DOCKER_CONTAINERD_PACKAGE_VERSION="${values[2]}"
	DOCKER_COMPOSE_PACKAGE_VERSION="${values[3]}"
	DOCKER_TARGET_ENGINE_VERSION="${values[4]}"
	DOCKER_TARGET_COMPOSE_VERSION="${values[5]}"
	docker_version_ge "${DOCKER_TARGET_ENGINE_VERSION}" "${MIN_DOCKER_ENGINE_VERSION}" \
		|| fail "Community tag ${TAG} selects Docker Engine ${DOCKER_TARGET_ENGINE_VERSION}, below the required ${MIN_DOCKER_ENGINE_VERSION}"
	docker_version_ge "${DOCKER_TARGET_COMPOSE_VERSION}" "${MIN_DOCKER_COMPOSE_VERSION}" \
		|| fail "Community tag ${TAG} selects Docker Compose ${DOCKER_TARGET_COMPOSE_VERSION}, below the required ${MIN_DOCKER_COMPOSE_VERSION}"
}

assert_clean_dpkg_state() {
	local audit_output
	command -v dpkg >/dev/null 2>&1 || fail "dpkg is required to install Docker CE"
	command -v dpkg-query >/dev/null 2>&1 || fail "dpkg-query is required to inspect Docker CE packages"
	if ! audit_output="$(dpkg --audit 2>&1)"; then
		[[ -z "${audit_output}" ]] || printf '[WARN] dpkg audit failed:\n%s\n' "${audit_output}" >&2
		fail "the host package state could not be inspected; repair dpkg manually before installing Docker CE"
	fi
	if [[ -n "${audit_output}" ]]; then
		printf '[WARN] dpkg reports incomplete packages on this host:\n%s\n' "${audit_output}" >&2
		fail "repair the host package state manually before installing Docker CE"
	fi
}

install_docker_prerequisites() {
	local -a missing=()
	local plan="${SESSION_DIR}/docker-prerequisites.plan"
	local install_log="${SESSION_DIR}/docker-prerequisites-install.log"
	command -v apt-get >/dev/null 2>&1 || fail "apt-get is required to install Docker CE"
	command -v gpg >/dev/null 2>&1 || missing+=(gnupg)
	[[ -f /etc/ssl/certs/ca-certificates.crt ]] || missing+=(ca-certificates)
	if ((${#missing[@]})); then
		printf '[....] Installing Docker CE source prerequisites: %s\n' "${missing[*]}"
		apt-get "${APT_RETRY_ARGS[@]}" update \
			|| fail "could not refresh Ubuntu package metadata for Docker CE prerequisites"
		if ! LC_ALL=C apt-get "${APT_RETRY_ARGS[@]}" --simulate --no-remove --no-upgrade \
			--no-install-recommends install \
			"${missing[@]}" >"${plan}" 2>&1; then
			preserve_apt_failure_log "${plan}"
			tail -n 20 "${plan}" >&2 || true
			fail "Docker CE source prerequisites could not be resolved"
		fi
		validate_apt_install_plan "${plan}" "Docker CE prerequisite installation"
		if ! apt_install_with_network_retry "${install_log}" \
			install -y --no-remove \
			--no-upgrade --no-install-recommends "${missing[@]}"; then
			tail -n 20 "${install_log}" >&2 || true
			fail "Docker CE source prerequisites could not be installed"
		fi
	fi
	command -v gpg >/dev/null 2>&1 || fail "GnuPG is required to verify the Docker CE signing key"
}

configure_docker_apt_source() {
	local key_ascii="${SESSION_DIR}/docker-ce.asc"
	local key_binary="${SESSION_DIR}/docker-ce.gpg"
	local gnupg_home="${SESSION_DIR}/gnupg"
	local fingerprint source_file="${SESSION_DIR}/hyperfilelens-docker.list"
	mkdir -m 0700 "${gnupg_home}"
	printf '[....] Configuring %s\n' "${DOCKER_CE_SOURCE_NAME}"
	download_file "${DOCKER_CE_GPG_URL}" "${key_ascii}" 120 \
		|| fail "could not download the Docker CE signing key from ${DOCKER_CE_GPG_URL}"
	fingerprint="$(GNUPGHOME="${gnupg_home}" gpg --batch --show-keys --with-colons "${key_ascii}" 2>/dev/null \
		| awk -F: '$1 == "fpr" {print $10; exit}')"
	[[ "${fingerprint}" == "${DOCKER_GPG_PRIMARY_FINGERPRINT}" ]] \
		|| fail "the Docker CE signing key fingerprint is invalid"
	GNUPGHOME="${gnupg_home}" gpg --batch --yes --dearmor --output "${key_binary}" "${key_ascii}" \
		|| fail "could not prepare the Docker CE signing key"
	install -d -m 0755 /etc/apt/keyrings
	install -m 0644 "${key_binary}" /etc/apt/keyrings/hyperfilelens-docker.gpg
	printf 'deb [arch=amd64 signed-by=/etc/apt/keyrings/hyperfilelens-docker.gpg] %s %s stable\n' \
		"${DOCKER_CE_APT_BASE}" "${HOST_UBUNTU_CODENAME}" >"${source_file}"
	install -m 0644 "${source_file}" /etc/apt/sources.list.d/hyperfilelens-docker.list
	printf '[ OK ] Docker CE package source is ready\n'
}

selected_docker_apt_source_present() {
	local apt_root="${1:-/etc/apt}"
	local file
	[[ -n "${DOCKER_CE_APT_BASE}" ]] || return 1
	while IFS= read -r file; do
		[[ -f "${file}" && -r "${file}" ]] || continue
		apt_source_has_enabled_value "${file}" "${DOCKER_CE_APT_BASE}" && return 0
	done < <(find "${apt_root}" -maxdepth 2 \( -type f -o -type l \) \
		\( -name 'sources.list' -o -name '*.list' -o -name '*.sources' \) \
		-print 2>/dev/null)
	return 1
}

ensure_docker_apt_source() {
	local apt_root="${1:-/etc/apt}"
	if selected_docker_apt_source_present "${apt_root}"; then
		printf '[ OK ] Existing %s will be reused\n' "${DOCKER_CE_SOURCE_NAME}"
		return 0
	fi
	if docker_apt_source_present "${apt_root}"; then
		printf '[ OK ] Existing Docker CE apt source will be reused\n'
		return 0
	fi
	install_docker_prerequisites
	configure_docker_apt_source
}

validate_apt_install_plan() {
	local plan=$1 operation=${2:-"automatic host-package installation"}
	if grep -Eq '^The following packages will be (REMOVED|DOWNGRADED):|^[[:space:]]*[1-9][0-9]* upgraded,| [1-9][0-9]* to remove' "${plan}"; then
		cat "${plan}" >&2
		fail "${operation} would upgrade, downgrade, or remove existing host packages; install the required packages manually"
	fi
}

validate_compose_only_install_plan() {
	local plan=$1
	local package
	local -a packages=()
	mapfile -t packages < <(awk '$1 == "Inst" {print $2}' "${plan}")
	package="${packages[0]:-}"
	package="${package%%:*}"
	if [[ "${#packages[@]}" -ne 1 || "${package}" != docker-compose-plugin ]]; then
		cat "${plan}" >&2
		fail "Docker Compose V2 cannot be installed safely without changing the existing Docker runtime; install a compatible Compose V2 plugin manually, then rerun this installer"
	fi
}

install_online_docker_runtime() {
	local plan="${SESSION_DIR}/docker-apt-plan.log"
	local install_log="${SESSION_DIR}/docker-apt-install.log"
	local attempt
	local -a packages=(
		"docker-ce=${DOCKER_ENGINE_PACKAGE_VERSION}"
		"docker-ce-cli=${DOCKER_CLI_PACKAGE_VERSION}"
		"containerd.io=${DOCKER_CONTAINERD_PACKAGE_VERSION}"
		"docker-compose-plugin=${DOCKER_COMPOSE_PACKAGE_VERSION}"
	)
	[[ -n "${DOCKER_ENGINE_PACKAGE_VERSION}" && -n "${DOCKER_CLI_PACKAGE_VERSION}" \
		&& -n "${DOCKER_CONTAINERD_PACKAGE_VERSION}" \
		&& -n "${DOCKER_COMPOSE_PACKAGE_VERSION}" ]] \
		|| fail "Docker CE runtime package versions were not resolved"
	assert_clean_dpkg_state
	install_docker_prerequisites
	configure_docker_apt_source
	printf '[....] Resolving Docker Engine and Docker Compose V2 packages\n'
	apt-get "${APT_RETRY_ARGS[@]}" update \
		|| fail "could not update the selected Docker CE package source"
	if ! LC_ALL=C apt-get "${APT_RETRY_ARGS[@]}" --simulate --no-remove --no-upgrade \
		--no-install-recommends install \
		"${packages[@]}" >"${plan}" 2>&1; then
		preserve_apt_failure_log "${plan}"
		tail -n 20 "${plan}" >&2 || true
		fail "Docker CE package dependencies could not be resolved"
	fi
	validate_apt_install_plan "${plan}" "Docker CE installation"
	printf '[....] Installing Docker Engine and Docker Compose V2\n'
	DOCKER_PACKAGE_INSTALL_ATTEMPTED=1
	if ! apt_install_with_network_retry "${install_log}" \
		install -y --no-remove \
		--no-upgrade --no-install-recommends \
		"${packages[@]}"; then
		tail -n 20 "${install_log}" >&2 || true
		fail "Docker Engine and Docker Compose V2 installation failed"
	fi
	DOCKER_BOOTSTRAPPED=1
	printf '[....] Enabling and starting Docker service\n'
	systemctl enable --now docker >/dev/null 2>&1 \
		|| fail "Docker was installed but docker.service could not be enabled and started"
	for attempt in {1..30}; do
		docker info >/dev/null 2>&1 && break
		sleep 1
	done
	docker info >/dev/null 2>&1 \
		|| fail "Docker was installed but its daemon did not become ready"
	systemctl is-active --quiet docker \
		|| fail "Docker was installed but docker.service is not active"
	systemctl is-enabled --quiet docker \
		|| fail "Docker was installed but docker.service is not enabled"
	DOCKER_ENGINE_VERSION="$(docker_engine_version)"
	DOCKER_COMPOSE_VERSION="$(docker_compose_version)"
	docker_version_ge "${DOCKER_ENGINE_VERSION}" "${MIN_DOCKER_ENGINE_VERSION}" \
		|| fail "installed Docker Engine ${DOCKER_ENGINE_VERSION:-unknown} does not meet the minimum required version ${MIN_DOCKER_ENGINE_VERSION}"
	docker_version_ge "${DOCKER_COMPOSE_VERSION}" "${MIN_DOCKER_COMPOSE_VERSION}" \
		|| fail "installed Docker Compose ${DOCKER_COMPOSE_VERSION:-unknown} does not meet the minimum required version ${MIN_DOCKER_COMPOSE_VERSION}"
	DOCKER_RUNTIME_ACTION="reuse"
	printf '[ OK ] Docker Engine %s and Docker Compose %s are ready\n' \
		"${DOCKER_ENGINE_VERSION}" "${DOCKER_COMPOSE_VERSION}"
}

install_online_compose_plugin() {
	local plan="${SESSION_DIR}/compose-apt-plan.log"
	local install_log="${SESSION_DIR}/compose-apt-install.log"
	local original_engine_version="${DOCKER_ENGINE_VERSION}"
	[[ -n "${DOCKER_COMPOSE_PACKAGE_VERSION}" ]] \
		|| fail "Docker Compose V2 package version was not resolved"
	assert_clean_dpkg_state
	if ! selected_docker_apt_source_present; then
		if ! docker_apt_source_present; then
			command -v gpg >/dev/null 2>&1 \
				|| fail "GnuPG is required to add the Docker CE source for Compose V2; install it manually, then rerun this installer"
			[[ -f /etc/ssl/certs/ca-certificates.crt ]] \
				|| fail "CA certificates are required to add the Docker CE source for Compose V2; install them manually, then rerun this installer"
		fi
	fi
	ensure_docker_apt_source
	printf '[....] Resolving Docker Compose V2 package\n'
	apt-get "${APT_RETRY_ARGS[@]}" update \
		|| fail "could not update the selected Docker CE package source"
	if ! LC_ALL=C apt-get "${APT_RETRY_ARGS[@]}" --simulate --no-remove --no-upgrade \
		--no-install-recommends install \
		"docker-compose-plugin=${DOCKER_COMPOSE_PACKAGE_VERSION}" >"${plan}" 2>&1; then
		preserve_apt_failure_log "${plan}"
		tail -n 20 "${plan}" >&2 || true
		fail "Docker Compose V2 package dependencies could not be resolved without changing the existing Docker runtime"
	fi
	validate_apt_install_plan "${plan}" "Docker Compose V2 installation"
	validate_compose_only_install_plan "${plan}"
	printf '[ OK ] Docker Compose V2 package plan is safe\n'
	printf '[....] Installing Docker Compose V2 plugin\n'
	COMPOSE_PACKAGE_INSTALL_ATTEMPTED=1
	if ! apt_install_with_network_retry "${install_log}" \
		install -y --no-remove --no-upgrade --no-install-recommends \
		"docker-compose-plugin=${DOCKER_COMPOSE_PACKAGE_VERSION}"; then
		tail -n 20 "${install_log}" >&2 || true
		fail "Docker Compose V2 plugin installation failed"
	fi
	COMPOSE_BOOTSTRAPPED=1
	docker info >/dev/null 2>&1 \
		|| fail "Docker daemon became unavailable after the Compose V2 plugin installation"
	DOCKER_ENGINE_VERSION="$(docker_engine_version)"
	[[ "${DOCKER_ENGINE_VERSION}" == "${original_engine_version}" ]] \
		|| fail "Docker Engine changed unexpectedly while installing the Compose V2 plugin"
	DOCKER_COMPOSE_VERSION="$(docker_compose_version)"
	docker_version_ge "${DOCKER_COMPOSE_VERSION}" "${MIN_DOCKER_COMPOSE_VERSION}" \
		|| fail "installed Docker Compose ${DOCKER_COMPOSE_VERSION:-unknown} does not meet the minimum required version ${MIN_DOCKER_COMPOSE_VERSION}"
	DOCKER_RUNTIME_ACTION="reuse"
	printf '[ OK ] Docker Compose %s is ready; Docker Engine %s was reused unchanged\n' \
		"${DOCKER_COMPOSE_VERSION}" "${DOCKER_ENGINE_VERSION}"
}

ensure_online_docker_runtime() {
	case "${DOCKER_RUNTIME_ACTION}" in
	reuse)
		printf '[ OK ] Existing Docker Engine %s and Docker Compose %s are supported\n' \
			"${DOCKER_ENGINE_VERSION}" "${DOCKER_COMPOSE_VERSION}"
		;;
	install-compose) install_online_compose_plugin ;;
	install) install_online_docker_runtime ;;
	*) fail "internal Docker runtime action is invalid" ;;
	esac
}

inspect_existing_installation() {
	local existing_edition
	if [[ ! -e "${INSTALL_ROOT}/.env" && ! -e "${INSTALL_ROOT}/MANIFEST.json" ]]; then
		return 0
	fi
	INSTALL_ACTION="Upgrade"
	[[ -f "${INSTALL_ROOT}/.env" && -f "${INSTALL_ROOT}/MANIFEST.json" ]] \
		|| fail "${INSTALL_ROOT} contains an incomplete installation; recover or remove it before continuing"
	existing_edition="$(python3 - "${INSTALL_ROOT}/MANIFEST.json" <<'PY'
import json
import pathlib
import sys

try:
    manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
print(str(manifest.get("edition") or "community").strip().lower())
PY
	)" || fail "the existing installation manifest is invalid"
	[[ "${existing_edition}" == community ]] \
		|| fail "this public installer upgrades Community only; the existing edition is ${existing_edition}"
}

configure_curl_retry_options() {
	local version_output curl_version="unknown"
	version_output="$(curl --version 2>/dev/null || true)"
	version_output="${version_output%%$'\n'*}"
	if [[ "${version_output}" =~ ^curl[[:space:]]+([^[:space:]]+) ]]; then
		curl_version="${BASH_REMATCH[1]}"
	fi

	CURL_RETRY_ARGS=(--retry 3 --retry-delay 2)
	if curl --retry-all-errors --version >/dev/null 2>&1; then
		CURL_RETRY_ARGS+=(--retry-all-errors)
	elif curl --retry-connrefused --version >/dev/null 2>&1; then
		CURL_RETRY_ARGS+=(--retry-connrefused)
		printf '[INFO] curl %s detected; using Ubuntu-compatible retry options.\n' \
			"${curl_version}"
	else
		printf '[WARN] curl %s lacks enhanced retry options; using standard retries.\n' \
			"${curl_version}"
	fi
}

download_file() {
	local url=$1
	local output=$2
	local max_time=${3:-120}
	local partial="${output}.part"
	rm -f -- "${partial}"
	if curl --fail --show-error --silent --location \
		"${CURL_RETRY_ARGS[@]}" \
		--connect-timeout 15 --max-time "${max_time}" \
		-H 'Cache-Control: no-cache' "${url}" -o "${partial}"; then
		if mv -f -- "${partial}" "${output}"; then
			return 0
		fi
	fi
	rm -f -- "${partial}"
	return 1
}

fail_with_tag_guidance() {
	local reason=$1
	local recommended_tag
	if [[ -z "${RECENT_TAGS}" ]]; then
		fail "${reason}; no fallback tags are available; retry later or use --mirror cn or --mirror global"
	fi
	recommended_tag="${RECENT_TAGS%%,*}"
	fail "${reason}; recent fallback tags: ${RECENT_TAGS}; recommended retry: --mirror ${MIRROR} --tag ${recommended_tag}"
}

tag_page_url() {
	local page=$1
	printf '%s' "${TAGS_API_URL%page=1}page=${page}"
}

resolve_tag() {
	local parsed requested_tag page count page_url page_fingerprint
	local -a values=()
	local -A seen_page_fingerprints=()
	requested_tag="${TAG}"
	printf '[....] Resolving Community tags from %s\n' "${SOURCE_NAME}"
	page=1
	while :; do
		page_url="$(tag_page_url "${page}")"
		if ! download_file "${page_url}" "${SESSION_DIR}/tag-page-${page}.json"; then
			fail "could not read Community tags from ${SOURCE_NAME}; retry later or use --mirror cn or --mirror global"
		fi
		if ! read -r count page_fingerprint < <(python3 - "${SESSION_DIR}/tag-page-${page}.json" <<'PY'
import hashlib
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
try:
    raw = path.read_bytes()
    payload = json.loads(raw)
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
if not isinstance(payload, list):
    raise SystemExit(1)
print(len(payload), hashlib.sha256(raw).hexdigest())
PY
		); then
			fail "the Community tag response from ${SOURCE_NAME} is invalid"
		fi
		if [[ -n "${seen_page_fingerprints[${page_fingerprint}]:-}" ]]; then
			fail "the Community tag response from ${SOURCE_NAME} repeated page ${page}; retry later"
		fi
		seen_page_fingerprints["${page_fingerprint}"]=1
		if ((page == 1)); then
			cp "${SESSION_DIR}/tag-page-${page}.json" "${SESSION_DIR}/tags.json"
		else
			if ! python3 - "${SESSION_DIR}/tags.json" "${SESSION_DIR}/tag-page-${page}.json" <<'PY'
import json
import pathlib
import sys

target = pathlib.Path(sys.argv[1])
current = json.loads(target.read_text(encoding="utf-8"))
additional = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
if not isinstance(current, list) or not isinstance(additional, list):
    raise SystemExit(1)
target.write_text(json.dumps(current + additional), encoding="utf-8")
PY
			then
				fail "the Community tag response from ${SOURCE_NAME} is invalid"
			fi
		fi
		((count < 100)) && break
		if ((page >= MAX_TAG_PAGES)); then
			fail "the Community tag catalog from ${SOURCE_NAME} exceeds ${MAX_TAG_PAGES} pages"
		fi
		page=$((page + 1))
	done

	if ! parsed="$(python3 - "${SESSION_DIR}/tags.json" "${requested_tag}" <<'PY'
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
requested = sys.argv[2]
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid tag response: {exc}")
if not isinstance(payload, list):
    raise SystemExit("tag response is not a list")

tags = {}
for entry in payload:
    if not isinstance(entry, dict):
        continue
    tag = str(entry.get("name") or "")
    commit_info = entry.get("commit")
    commit = str(commit_info.get("sha") or "").lower() if isinstance(commit_info, dict) else ""
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        continue
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        continue
    if tag in tags and tags[tag] != commit:
        raise SystemExit(f"tag {tag} resolves to multiple commits")
    tags[tag] = commit

ordered = sorted(
    tags,
    key=lambda value: tuple(int(part) for part in value[1:].split(".")),
    reverse=True,
)
if not ordered:
    raise SystemExit("tag response contains no semantic release tags")

selected = requested if requested in tags else (ordered[0] if not requested else "")
if selected:
    selected_version = tuple(int(part) for part in selected[1:].split("."))
    fallback = [
        tag
        for tag in ordered
        if tuple(int(part) for part in tag[1:].split(".")) < selected_version
    ][:10]
else:
    fallback = ordered[:10]

print(selected or "-")
print(selected[1:] if selected else "-")
print(tags.get(selected, "-") if selected else "-")
print(", ".join(fallback) or "-")
PY
	)"; then
		fail "the Community tag response from ${SOURCE_NAME} is invalid"
	fi
	mapfile -t values <<<"${parsed}"
	[[ "${#values[@]}" -eq 4 ]] || fail "the Community tag response from ${SOURCE_NAME} is incomplete"
	RECENT_TAGS="${values[3]}"
	[[ "${RECENT_TAGS}" != - ]] || RECENT_TAGS=""
	if [[ -n "${requested_tag}" && "${values[0]}" == - ]]; then
		fail_with_tag_guidance "Community tag ${requested_tag} does not exist on ${SOURCE_NAME}"
	fi
	TAG="${values[0]}"
	RELEASE_VERSION="${values[1]}"
	RELEASE_COMMIT="${values[2]}"
	printf '[ OK ] Community release resolved · %s · commit %s\n' \
		"${TAG}" "${RELEASE_COMMIT:0:12}"
}

confirm_installation() {
	local answer=""
	((ASSUME_YES == 1)) && return 0
	[[ -r /dev/tty ]] \
		|| fail "interactive confirmation requires a terminal; use --yes only for automation"
	read -r -p 'Continue? [y/N] ' answer </dev/tty
	case "${answer}" in y | Y | yes | YES | Yes) ;; *) fail "installation cancelled" ;; esac
}

download_source_archive() {
	local url
	case "${MIRROR}" in
	global) url="https://codeload.github.com/oneprolabs/hyperfilelens/tar.gz/${RELEASE_COMMIT}" ;;
	cn) url="https://gitee.com/oneprolabs/hyperfilelens/repository/archive/${RELEASE_COMMIT}.tar.gz" ;;
	esac
	printf '\nRelease contract\n'
	printf '[....] Downloading %s installation contract from %s (commit %s)\n' \
		"${TAG}" "${SOURCE_NAME}" "${RELEASE_COMMIT:0:12}"
	if ! download_file "${url}" "${SESSION_DIR}/source.tar.gz" 300; then
		fail_with_tag_guidance "Community release ${TAG} cannot be downloaded from ${SOURCE_NAME}"
	fi
	mkdir -p "${SESSION_DIR}/source"
	tar -xzf "${SESSION_DIR}/source.tar.gz" -C "${SESSION_DIR}/source" --strip-components=1 \
		|| fail_with_tag_guidance "Community tag ${TAG} installation contract could not be extracted"
	[[ -x "${SESSION_DIR}/source/deploy/online/install.sh" \
		&& -f "${SESSION_DIR}/source/deploy/online/prepare.py" ]] \
		|| fail_with_tag_guidance "Community tag ${TAG} does not provide the online installation contract"
	printf '[ OK ] Downloaded installation contract from %s\n' "${SOURCE_NAME}"
}

verify_candidate_release() {
	python3 - "${candidate}/MANIFEST.json" "${TAG}" "${RELEASE_COMMIT}" <<'PY'
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
expected_tag = sys.argv[2]
expected_commit = sys.argv[3]
try:
    manifest = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"prepared Community manifest is invalid: {exc}")
version = str(manifest.get("version") or "")
commit = str(manifest.get("git_commit") or "").lower()
if expected_tag != f"v{version}":
    raise SystemExit("prepared Community version does not match the published release")
if not re.fullmatch(r"[0-9a-f]{40}", commit) or commit != expected_commit:
    raise SystemExit("prepared Community image revision does not match the published release")
if manifest.get("edition") != "community" or manifest.get("channel") != "release":
    raise SystemExit("prepared package is not a Community release")
PY
}

while (($#)); do
	case "$1" in
	--mirror)
		require_value "$@"
		MIRROR=$2
		shift 2
		;;
	--tag)
		require_value "$@"
		[[ -z "${TAG}" ]] || fail "--tag may only be supplied once"
		TAG=$2
		shift 2
		;;
	--yes)
		ASSUME_YES=1
		shift
		;;
	-h | --help)
		usage
		exit 0
		;;
	*) fail "unknown argument: $1" ;;
	esac
done

[[ -z "${TAG}" || "${TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
	|| fail "--tag must use vX.Y.Z; choose a published version with --mirror cn|global --tag vX.Y.Z"
configure_mirror
check_host
configure_logging
print_banner

SESSION_DIR="$(mktemp -d /var/tmp/hyperfilelens-online.XXXXXX)"
chmod 0700 "${SESSION_DIR}"
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

inspect_existing_installation
configure_curl_retry_options
resolve_tag
inspect_docker_runtime
if [[ "${DOCKER_RUNTIME_ACTION}" == install ]]; then
	assert_docker_service_manager
fi
if [[ "${DOCKER_RUNTIME_ACTION}" != reuse ]]; then
	download_source_archive
	load_docker_runtime_contract
fi
print_target
confirm_installation
install_host_tools
if [[ "${DOCKER_RUNTIME_ACTION}" == reuse ]]; then
	download_source_archive
fi
ensure_online_docker_runtime

export HFL_GLOBAL_REGISTRY_PREFIX="${GLOBAL_REGISTRY_PREFIX}"
export HFL_CN_REGISTRY_PREFIX="${CN_REGISTRY_PREFIX}"
export HFL_REGISTRY_REGION="${REGION}"
export HFL_ONLINE_NATIVE_PROGRESS="${ONLINE_INTERACTIVE}"
candidate="${SESSION_DIR}/hyperfilelens-${RELEASE_VERSION}-online"
if ! python3 "${SESSION_DIR}/source/deploy/online/prepare.py" \
	--source-root "${SESSION_DIR}/source" \
	--version "${TAG}" \
	--region "${REGION}" \
	--output "${candidate}"; then
	fail_with_tag_guidance "Community tag ${TAG} is incomplete or unavailable"
fi
if ! verify_candidate_release; then
	fail_with_tag_guidance "Community tag ${TAG} failed release identity validation"
fi
printf '[ OK ] Release package and installation assets are ready\n'

if [[ "${INSTALL_ACTION}" == Upgrade ]]; then
	printf '[....] Upgrading the existing installation to %s\n' "${TAG}"
	HFL_REGISTRY_REGION="${REGION}" HFL_ONLINE_CHILD=1 HFL_PARENT_LOGGING=1 \
		HFL_PARENT_INTERACTIVE="${ONLINE_INTERACTIVE}" HFL_LOG_FILE="${ONLINE_LOG_FILE}" \
		HFL_NO_BANNER=1 bash "${candidate}/install.sh" \
		upgrade --from "${candidate}" --yes --with-sourcelens
else
	printf '[....] Installing HyperFileLens Community %s\n' "${TAG}"
	HFL_REGISTRY_REGION="${REGION}" HFL_ONLINE_CHILD=1 HFL_PARENT_LOGGING=1 \
		HFL_PARENT_INTERACTIVE="${ONLINE_INTERACTIVE}" HFL_LOG_FILE="${ONLINE_LOG_FILE}" \
		HFL_NO_BANNER=1 bash "${candidate}/install.sh" \
		install --with-sourcelens --yes
fi
