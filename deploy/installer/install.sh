#!/usr/bin/env bash
# HyperFileLens release package control (install / lifecycle / upgrade).
# Layout: <package-root>/install.sh  →  runtime install dir: /opt/hyperfilelens
set -euo pipefail

INSTALL_DIR="/opt/hyperfilelens"
SOURCELENS_INSTALL_DIR="${SOURCELENS_INSTALL_DIR:-${INSTALL_DIR}/sourcelens}"
HFL_BRIDGE_NETWORK="hyperfilelens-bridge"
UPGRADE_TMP="${INSTALL_DIR}/upgrade_tmp"
UPGRADE_YES=0
LANG_PACK_COMPOSE_FILE="docker-compose.yml"
LOG_FILE="${HFL_LOG_FILE:-}"
VERBOSE="${HFL_LOG_VERBOSE:-0}"
PRINT_CONFIG=0
SESSION_STARTED=0
PUBLIC_HOST="${HFL_PUBLIC_HOST:-}"
PUBLIC_URL="${HFL_PUBLIC_URL:-}"
ADMIN_PUBLIC_URL="${HFL_ADMIN_PUBLIC_URL:-}"
RUNTIME_ENV_FILE="${HFL_RUNTIME_ENV_FILE:-}"
SHOW_GENERATED_CREDENTIALS="${HFL_SHOW_GENERATED_CREDENTIALS:-auto}"
UPGRADE_RECOVERY_ARMED=0
UPGRADE_HFL_WAS_RUNNING=0
UPGRADE_SOURCELENS_WAS_RUNNING=0
UPGRADE_PREVIOUS_COLOR=""
UPGRADE_LEGACY_API_CID=""
UPGRADE_TARGET_COLOR=""
UPGRADE_TARGET_VERSION=""
UPGRADE_BACKUP_DIR=""
SOURCELENS_MAINTENANCE_ARMED=0
SOURCELENS_PROXY_GATE_ARMED=0
SOURCELENS_UPGRADE_STARTED=0
SOURCELENS_GATE_ADOPTION_SOURCE=""
UPGRADE_HFL_COMMITTED=0
UPGRADE_HFL_CUTOVER_ATTEMPTED=0
INSTALLER_LOCK_ACQUIRED=0
DRAINED_WS_INSTANCES=""
LOCAL_PLATFORM_AGENT_INSTALL_DIR="/opt/hyperfilelens-agent"
LOCAL_PLATFORM_AGENT_DATA_DIR="/var/lib/hyperfilelens-agent"
LOCAL_PLATFORM_LENSNODE_ENV_FILE="/etc/hyperfilelens/lensnode.env"
LOCAL_PLATFORM_LENSNODE_IMAGE="hyperfilelens-sourcelens-lensnode:latest"

usage() {
	cat <<'USAGE'
Usage: install.sh [command] [options]

When no command is given, equivalent to: install.sh install

Commands:
  install       Fresh install from this package and start services (install dir /opt/hyperfilelens)
  backup        Create and verify one managed backup set; retain the latest three valid sets
  start         docker compose up -d --no-build
  stop          docker compose down
  restart       stop then start
  status        Show version and compose service status
  manage        Run a Django management command in the active API color
  platform-gateway Ensure or verify the installer-managed platform Gateway
  upgrade       In-place upgrade from another release package directory or .tar.gz
  uninstall     Stop and remove Docker containers and app images (does not remove the install dir; see uninstall options)
  lang-pack     Install, list, or remove optional runtime language packs

Options:
  global:
    --log-file FILE        Append runtime logs to FILE
    --verbose              Enable detailed logs
    --print-config         Print effective non-secret configuration and exit

  install:
    --with-sourcelens       Install bundled SourceLens (default when sourcelens/ is present)
    --hfl-only              Skip bundled SourceLens even when sourcelens/ is present
    --direct-host HOST      Direct listener host or IP used for local access URLs
    --public-url URL        Optional canonical browser origin; invalid values only warn
    --admin-public-url URL  Optional Admin Console browser origin; invalid values only warn
    --runtime-env-file FILE Apply staged Turnstile settings from a root-only regular file

  upgrade:
    --from PATH             Path to new package directory or hyperfilelens-*.tar.gz (required)
                            Creates a verified managed backup set under backup/ before upgrade
                            and retains the latest three valid sets
                            Extracts the new package to upgrade_tmp, merges keys from its .env.example into .env,
                            runs a singleton migration, starts the inactive API/Web color, validates and
                            atomically switches stable Nginx, drains Agent WebSockets, then hands off workers;
                            removes upgrade_tmp on success
    --with-sourcelens       Upgrade bundled SourceLens when sourcelens/ is present (default when present)
    --hfl-only              Skip SourceLens upgrade even when sourcelens/ is present
    --remove-sourcelens     Stop and remove installed SourceLens under the HFL install root
    --purge-sourcelens-data Remove SourceLens data/ (with --remove-sourcelens or uninstall --with-sourcelens)
    --yes                   Non-interactive: continue when target version equals installed version
    --direct-host HOST      Direct listener host or IP used for local access URLs
    --public-url URL        Optional canonical browser origin; invalid values only warn
    --admin-public-url URL  Optional Admin Console browser origin; invalid values only warn
    --runtime-env-file FILE Apply staged Turnstile settings from a root-only regular file

  uninstall:
    --with-sourcelens       Stop SourceLens stack and remove its application images
    --purge-sourcelens-data Remove SourceLens data under data/sourcelens/
    --purge-media           Remove published bootstrap and agent artifacts under data/media/
    --purge-config          Remove .env
    --purge-data            Remove data/
    --purge-all             Remove both data/ and .env

  lang-pack:
    install --file PATH     Validate and atomically install a language-pack .tar.gz
    list                    List installed language packs
    remove PACK_ID          Remove an installed language pack

  platform-gateway:
    ensure                  Deploy or repair the local platform Gateway when enabled
    verify [options]        Read-only verification of the local platform Gateway
      --timeout SECONDS     Wait up to 180 seconds by default (maximum 900)
      --required            Fail when local platform Gateway auto-deploy is disabled

  manage:
    COMMAND [ARGS...]       Forward a Django management command to the active API color

    Uninstall never removes the install directory itself (${INSTALL_DIR} by default).
    Application files (install.sh, docker-compose.yml, images/, payload/, backup/, etc.)
    remain on disk. To remove them after uninstall, run manually, for example:
      sudo rm -rf ${INSTALL_DIR}
    Host Docker CE installed from the bundled OS-specific archive is not removed.

Examples:
  sudo ./install.sh
  sudo ./install.sh install
  sudo ./install.sh backup
  sudo ./install.sh upgrade --from /path/to/hyperfilelens-0.1.0.tar.gz
  sudo ./install.sh uninstall
  sudo ./install.sh uninstall --purge-all
  sudo ./install.sh lang-pack install --file /path/to/hyperfilelens-lang-fr-0.1.0.tar.gz
  sudo ./install.sh lang-pack list
  sudo ./install.sh lang-pack remove fr
  sudo rm -rf ${INSTALL_DIR}   # optional: remove install dir after uninstall (not done by install.sh)
USAGE
}

hfl_now() {
	date -u +%Y-%m-%dT%H:%M:%S.000Z 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ
}

hfl_finish_sentence() {
	local msg="$*"
	msg="${msg%"${msg##*[![:space:]]}"}"
	case "${msg}" in
	*.|*.?|*!) printf '%s' "${msg}" ;;
	*) printf '%s.' "${msg}" ;;
	esac
}

log() { printf '[%s] [INFO ] %s\n' "$(hfl_now)" "$(hfl_finish_sentence "$*")" >&2; }
step() { printf '[%s] [STEP ] %s\n' "$(hfl_now)" "$(hfl_finish_sentence "$*")" >&2; }
ok() { printf '[%s] [ OK  ] %s\n' "$(hfl_now)" "$(hfl_finish_sentence "$*")" >&2; }
skip() { printf '[%s] [SKIP ] %s\n' "$(hfl_now)" "$(hfl_finish_sentence "$*")" >&2; }
warn() { printf '[%s] [WARN ] %s\n' "$(hfl_now)" "$(hfl_finish_sentence "$*")" >&2; }
debug() { [[ "${VERBOSE}" == "1" ]] && printf '[%s] [DEBUG] %s\n' "$(hfl_now)" "$(hfl_finish_sentence "$*")" >&2 || true; }
die() { local message=$1 code=${2:-1}; printf '[%s] [FAIL ] %s\n' "$(hfl_now)" "$(hfl_finish_sentence "${message}")" >&2; exit "${code}"; }

configure_logging() {
	if [[ -n "${LOG_FILE}" ]]; then
		mkdir -p "$(dirname "${LOG_FILE}")"
		exec 2> >(tee -a "${LOG_FILE}" >&2)
	fi
}

finish_session() {
	local rc=${1:-$?}
	trap - EXIT
	if [[ "${SESSION_STARTED}" -eq 1 ]]; then
		SESSION_STARTED=0
		if [[ "${rc}" -eq 0 ]]; then
			ok "Installer session completed"
		else
			printf '[%s] [FAIL ] Installer session exited with status %s.\n' "$(hfl_now)" "${rc}" >&2
		fi
	fi
	exit "${rc}"
}

cleanup_upgrade_and_finish() {
	local rc=$?
	local recovery_rc=0
	if [[ "${rc}" -ne 0 && "${UPGRADE_RECOVERY_ARMED}" == "1" ]]; then
		record_deployment_phase failed "${UPGRADE_PREVIOUS_COLOR:-unknown}" \
			"${UPGRADE_TARGET_COLOR:-unknown}" "${UPGRADE_TARGET_VERSION:-unknown}" || true
		recover_upgrade_services || recovery_rc=$?
	fi
	if [[ "${SOURCELENS_MAINTENANCE_ARMED}" == "1" ]]; then
		if [[ "${recovery_rc}" -eq 0 ]]; then
			clear_sourcelens_maintenance_gate || true
		else
			warn "SourceLens recovery did not become healthy; keeping the maintenance gate until its fail-safe lease expires"
		fi
	fi
	# The Nginx gate has no cache lease. Always disarm its persisted file and
	# reload/restart Nginx when possible, even when application recovery failed.
	if [[ "${SOURCELENS_PROXY_GATE_ARMED}" == "1" ]]; then
		clear_sourcelens_proxy_gate \
			|| warn "SourceLens direct Run gate is disabled on disk, but Nginx could not be refreshed"
	fi
	cleanup_upgrade_tmp
	finish_session "${rc}"
}

recover_upgrade_services() {
	local recovered=1
	warn "upgrade failed after the maintenance window began; attempting best-effort service recovery"
	set +e
	if [[ "${UPGRADE_SOURCELENS_WAS_RUNNING}" == "1" ]] && sourcelens_installed; then
		if [[ "${SOURCELENS_UPGRADE_STARTED}" == "1" ]]; then
			sourcelens_compose up -d --no-build
		else
			# Target SourceLens files may already be staged.  Starting existing
			# containers avoids converging an unchanged live runtime before its
			# independent maintenance/drain phase begins.
			sourcelens_compose start
		fi
		[[ $? -eq 0 ]] || recovered=0
		if [[ "${SOURCELENS_MAINTENANCE_ARMED}" == "1" && "${recovered}" == "1" ]]; then
			wait_for_sourcelens_health
			[[ $? -eq 0 ]] || recovered=0
		fi
	fi
	if [[ "${UPGRADE_HFL_WAS_RUNNING}" == "1" && -f "${ROOT}/.env" ]]; then
		local recovery_color
		if [[ "${UPGRADE_HFL_COMMITTED}" == "1" ]]; then
			recovery_color="${UPGRADE_TARGET_COLOR}"
		elif [[ "${UPGRADE_PREVIOUS_COLOR}" != "legacy" ]]; then
			recovery_color="${UPGRADE_PREVIOUS_COLOR}"
		fi
		if [[ "${UPGRADE_HFL_COMMITTED}" != "1" \
			&& "${UPGRADE_PREVIOUS_COLOR}" == "legacy" \
			&& -n "${UPGRADE_LEGACY_API_CID}" \
			&& "$(docker inspect --format '{{.State.Running}}' "${UPGRADE_LEGACY_API_CID}" 2>/dev/null || true)" == "true" ]]; then
			compose_in_root up -d --no-build --no-recreate postgres redis
			# The pre-upgrade singleton containers still carry the previous image.
			# Starting them preserves one coherent rollback release; `up` would
			# recreate them from the already-staged target image metadata.
			compose_in_root start worker scheduler
			[[ $? -eq 0 ]] || recovered=0
			restore_previous_hfl_color legacy "${UPGRADE_TARGET_COLOR}"
			[[ $? -eq 0 ]] || recovered=0
		elif [[ -n "${recovery_color:-}" ]]; then
			# Files and APP_VERSION already describe the target release.  `up`
			# would therefore recreate the still-valid previous-color containers
			# with the target image and destroy the rollback path.  Start only
			# existing stable/color containers. Before commit, worker and scheduler
			# must also remain on the previous release so API and background code do
			# not run different schema/application contracts.
			compose_in_root start postgres redis nginx
			[[ $? -eq 0 ]] || recovered=0
			compose_color "${recovery_color}" start \
				"api-${recovery_color}" "web-${recovery_color}"
			[[ $? -eq 0 ]] || recovered=0
			if [[ "${UPGRADE_HFL_COMMITTED}" == "1" ]]; then
				compose_in_root up -d --no-build worker scheduler
			else
				compose_in_root start worker scheduler
			fi
			[[ $? -eq 0 ]] || recovered=0
			if [[ "${UPGRADE_HFL_COMMITTED}" == "1" ]]; then
				render_active_upstreams "${recovery_color}"
				reload_stable_nginx
				[[ $? -eq 0 ]] || recovered=0
				write_active_color "${recovery_color}"
			else
				restore_previous_hfl_color "${recovery_color}" "${UPGRADE_TARGET_COLOR}"
				[[ $? -eq 0 ]] || recovered=0
			fi
		elif [[ "${UPGRADE_PREVIOUS_COLOR}" != "legacy" ]]; then
			recovered=0
		fi
	fi
	set -e
	if [[ "${recovered}" == "1" ]]; then
		ok "best-effort service recovery command completed; database backups were not restored automatically"
		return 0
	else
		if [[ "${SOURCELENS_UPGRADE_STARTED}" == "1" && -n "${UPGRADE_BACKUP_DIR}" ]]; then
			warn "bundled SourceLens recovery may require restoring sourcelens-postgresql.dump and config-and-data.tar.gz from ${UPGRADE_BACKUP_DIR}"
		fi
		warn "best-effort service recovery was incomplete; inspect container status and the managed backup before manual recovery"
		return 1
	fi
}

print_config() {
	local source_dir
	source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
	cat <<EOF
package_root=${source_dir}
install_dir=${INSTALL_DIR}
sourcelens_install_dir=${SOURCELENS_INSTALL_DIR}
upgrade_tmp=${UPGRADE_TMP}
bridge_network=${HFL_BRIDGE_NETWORK}
public_host=${PUBLIC_HOST:-<auto>}
public_url=${PUBLIC_URL:-<none>}
admin_public_url=${ADMIN_PUBLIC_URL:-<none>}
runtime_env_file=$([[ -n "${RUNTIME_ENV_FILE}" ]] && printf '<provided>' || printf '<none>')
show_generated_credentials=${SHOW_GENERATED_CREDENTIALS}
log_file=${LOG_FILE:-<none>}
verbose=${VERBOSE}
EOF
}

# --- Safe path guards ---

safe_normalize_dir() {
	local path=$1
	path="${path%/}"
	[[ -n "${path}" ]] || die "empty path"
	printf '%s' "${path}"
}

safe_assert_absolute() {
	local path=$1 label=${2:-path}
	[[ "${path}" == /* ]] || die "${label} must be an absolute path: ${path}"
	[[ "${path}" != *$'\n'* && "${path}" != *$'\r'* ]] || die "${label} contains invalid characters"
	[[ "${path}" != *".."* ]] || die "${label} must not contain '..': ${path}"
}

safe_assert_path_under_dir() {
	local path=$1 root=$2 label=$3
	safe_assert_absolute "${path}" "${label}"
	root="$(safe_normalize_dir "${root}")"
	path="$(safe_normalize_dir "${path}")"
	[[ "${path}" == "${root}" || "${path}" == "${root}/"* ]] \
		|| die "${label} must stay under ${root}: ${path}"
}

safe_assert_removable_data_dir() {
	local path=$1 root=$2
	safe_assert_path_under_dir "${path}" "${root}" "data path"
	[[ "$(safe_normalize_dir "${path}")" == "$(safe_normalize_dir "${root}")/data" ]] \
		|| die "refusing to remove unexpected data path: ${path}"
}

safe_assert_env_file() {
	local path=$1 root=$2
	root="$(safe_normalize_dir "${root}")"
	[[ "${path}" == "${root}/.env" ]] || die "refusing to remove unexpected env file: ${path}"
}

safe_assert_package_basename() {
	local name=$1
	[[ "${name}" =~ ^hyperfilelens-([0-9]+\.[0-9]+\.[0-9]+(-ee|-[0-9a-fA-F]{7})?|main-[0-9a-f]{7})\.tar\.gz$ ]] \
		|| die "invalid release package basename: ${name}"
	[[ "${name}" != */* ]] || die "package basename must not contain slashes: ${name}"
}

safe_assert_upgrade_package_file() {
	local path=$1
	safe_assert_absolute "${path}" "upgrade package file"
	safe_assert_package_basename "$(basename "${path}")"
	[[ -f "${path}" ]] || die "upgrade package not found: ${path}"
}

safe_assert_package_root() {
	local root=$1
	safe_assert_absolute "${root}" "package root"
	[[ -f "${root}/docker-compose.yml" && -f "${root}/MANIFEST.json" ]] \
		|| die "path does not look like a HyperFileLens package root: ${root}"
}

safe_rm_file() {
	local file=$1
	[[ -n "${file}" && "${file}" != "/" ]] || die "refusing to remove unsafe file path"
	rm -f "${file}"
}

safe_rm_dir() {
	local dir=$1
	[[ -n "${dir}" && "${dir}" != "/" ]] || die "refusing to remove unsafe directory path"
	rm -rf "${dir}"
}

# --- Host / Docker ---

require_root_or_sudo() {
	if [[ "${EUID}" -eq 0 ]]; then
		return 0
	fi
	if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
		return 0
	fi
	die "this operation requires root or passwordless sudo"
}

run_as_root() {
	if [[ "${EUID}" -eq 0 ]]; then
		"$@"
	else
		sudo "$@"
	fi
}

assert_host_os() {
	local id version_id arch
	[[ -f /etc/os-release ]] || die "missing /etc/os-release (Ubuntu 20.04/22.04/24.04 amd64 only)"
	# shellcheck disable=SC1091
	source /etc/os-release
	id="${ID:-}"
	version_id="${VERSION_ID:-}"
	arch="$(uname -m)"
	[[ "${id}" == "ubuntu" && ( "${version_id}" == "20.04" || "${version_id}" == "22.04" || "${version_id}" == "24.04" ) ]] \
		|| die "host must be Ubuntu 20.04, 22.04, or 24.04 (current: ${id:-unknown} ${version_id:-unknown})"
	[[ "${arch}" == "x86_64" ]] || die "host must be amd64/x86_64 (current: ${arch})"
}

docker_engine_version() {
	docker version --format '{{.Server.Version}}' 2>/dev/null || true
}

docker_compose_version() {
	if docker compose version >/dev/null 2>&1; then
		docker compose version --short 2>/dev/null || docker compose version 2>/dev/null | awk '{print $NF}'
		return 0
	fi
	printf ''
}

docker_version_ge() {
	python3 - "$1" "$2" <<'PY'
import sys

def parse(v):
    parts = []
    for chunk in v.lstrip("vV").replace("-", ".").split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num or 0))
    return tuple(parts)

try:
    sys.exit(0 if parse(sys.argv[1]) >= parse(sys.argv[2]) else 1)
except Exception:
    sys.exit(1)
PY
}

docker_runtime_ready() {
	local min_version="${1:-24.0.0}"
	command -v docker >/dev/null 2>&1 || return 1
	docker info >/dev/null 2>&1 || return 1
	local engine compose
	engine="$(docker_engine_version)"
	[[ -n "${engine}" ]] || return 1
	docker_version_ge "${engine}" "${min_version}" || return 1
	compose="$(docker_compose_version)"
	[[ -n "${compose}" ]] || return 1
	docker_version_ge "${compose}" "2.20.0" || return 1
	return 0
}

manifest_min_engine_version() {
	python3 - "${ROOT}/MANIFEST.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
if not path.is_file():
    print("24.0.0")
    raise SystemExit(0)
data = json.loads(path.read_text(encoding="utf-8"))
host = data.get("host_runtime") or {}
docker = host.get("docker") or {}
print(docker.get("min_engine_version") or "24.0.0")
PY
}

ensure_host_docker() {
	local root=$1
	local min_version installer
	assert_host_os
	require_root_or_sudo
	min_version="$(manifest_min_engine_version)"
	installer="${root}/payload/media/gateway-bootstrap/gateway-install-docker-ubuntu-amd64.sh"
	[[ -x "${installer}" ]] || die "release package is missing the offline Docker installer"
	# The helper normally preserves a healthy sidecar. This installer owns the
	# local Gateway and must refresh its URL, credentials, and image together.
	run_as_root env \
		HFL_GATEWAY_BOOTSTRAP_BASE="file://${root}/payload/media/gateway-bootstrap" \
		HFL_INSECURE_TLS=0 HFL_DOCKER_MIN_ENGINE="${min_version}" \
		bash "${installer}"
	docker_runtime_ready "${min_version}" || die "Docker post-install self-check failed"
}

upgrade_host_docker_from_source() {
	local source_root=$2
	ensure_host_docker "${source_root}"
}

# --- Package layout ---

resolve_source_root() {
	local dir
	dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
	[[ -f "${dir}/docker-compose.yml" ]] || die "release package root not found (missing docker-compose.yml)"
	printf '%s' "${dir}"
}

materialize_to_install_dir() {
	local source=$1
	local tls_state
	source="$(safe_normalize_dir "${source}")"
	INSTALL_DIR="$(safe_normalize_dir "${INSTALL_DIR}")"
	if [[ "${source}" == "${INSTALL_DIR}" ]]; then
		log "already in install directory ${INSTALL_DIR}"
		return 0
	fi
	step "Copying release package ${source} -> ${INSTALL_DIR} ..."
	require_root_or_sudo
	run_as_root mkdir -p "${INSTALL_DIR}"
	tls_state="$(tls_pair_state "${INSTALL_DIR}/deploy/nginx/certs")"
	[[ "${tls_state}" != "incomplete" ]] \
		|| die "existing TLS certificate pair is incomplete under ${INSTALL_DIR}/deploy/nginx/certs"
	if command -v rsync >/dev/null 2>&1; then
		local -a rsync_args=(
			-a
			-H
			--checksum
			--delete
			--exclude '.env'
			--exclude '.installer.lock'
			--exclude 'data/'
			--exclude 'backup/'
			--exclude 'upgrade_tmp/'
		)
		if [[ "${tls_state}" == "complete" ]]; then
			rsync_args+=(--exclude 'deploy/nginx/certs/')
			log "Preserving existing TLS certificate directory"
		fi
		run_as_root rsync "${rsync_args[@]}" "${source}/" "${INSTALL_DIR}/"
	else
		die "rsync is required to copy the release package to ${INSTALL_DIR}"
	fi
	log "Copy complete"
}

acquire_installation_lock() {
	[[ "${INSTALLER_LOCK_ACQUIRED}" -eq 0 ]] || return 0
	command -v flock >/dev/null 2>&1 || die "flock is required for serialized installation operations"
	mkdir -p "${ROOT}"
	# shellcheck disable=SC3045
	exec {HFL_INSTALLER_LOCK_FD}>"${ROOT}/.installer.lock"
	flock -n "${HFL_INSTALLER_LOCK_FD}" \
		|| die "another HyperFileLens installer operation is already running"
	INSTALLER_LOCK_ACQUIRED=1
}

init_install_root() {
	local source
	source="$(resolve_source_root)"
	materialize_to_install_dir "${source}"
	ROOT="${INSTALL_DIR}"
	safe_assert_package_root "${ROOT}"
	acquire_installation_lock
}

init_existing_install_root() {
	INSTALL_DIR="$(safe_normalize_dir "${INSTALL_DIR}")"
	ROOT="${INSTALL_DIR}"
	safe_assert_package_root "${ROOT}"
	acquire_installation_lock
}

require_docker() {
	command -v docker >/dev/null 2>&1 || die "docker command not found"
	docker info >/dev/null 2>&1 || die "cannot connect to Docker daemon"
	docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
	COMPOSE=(docker compose)
}

compose_in_root() {
	(
		cd "${ROOT}"
		"${COMPOSE[@]}" --env-file "${ROOT}/.env" -f "${ROOT}/docker-compose.yml" "$@"
	)
}

blue_green_state_dir() { printf '%s' "${ROOT}/deploy/blue-green"; }

active_color_file() { printf '%s' "$(blue_green_state_dir)/active-color"; }

record_deployment_phase() {
	local phase=$1 previous=$2 target=$3 version=$4
	local state_dir="$(blue_green_state_dir)" output temporary
	mkdir -p "${state_dir}"
	output="${state_dir}/deployment-state"
	temporary="${output}.tmp.$$"
	{
		printf 'phase=%s\n' "${phase}"
		printf 'previous_color=%s\n' "${previous}"
		printf 'target_color=%s\n' "${target}"
		printf 'target_version=%s\n' "${version}"
		printf 'updated_at=%s\n' "$(hfl_now)"
	} > "${temporary}"
	mv "${temporary}" "${output}"
}

read_active_color() {
	local color=""
	[[ -f "$(active_color_file)" ]] && color="$(tr -d '[:space:]' < "$(active_color_file)")"
	case "${color}" in blue | green) printf '%s' "${color}" ;; *) return 1 ;; esac
}

opposite_color() {
	case "$1" in blue) printf 'green' ;; green) printf 'blue' ;; *) printf 'green' ;; esac
}

render_active_upstreams() {
	local api_color=$1 web_color=${2:-$1}
	case "${api_color}" in blue | green | legacy) ;; *) die "invalid API color: ${api_color}" ;; esac
	case "${web_color}" in blue | green) ;; *) die "invalid Web color: ${web_color}" ;; esac
	local runtime_dir="${ROOT}/deploy/nginx/snippets"
	local output="${runtime_dir}/hfl-active-upstreams.conf" temporary
	mkdir -p "${runtime_dir}"
	temporary="${output}.tmp.$$"
	local api_service="api-${api_color}"
	[[ "${api_color}" == "legacy" ]] && api_service="api"
	cat > "${temporary}" <<EOF
# Installer-managed execution cache. Do not edit while an upgrade is running.
upstream hfl_api_http { server ${api_service}:8000; keepalive 32; }
upstream hfl_api_ws { server ${api_service}:8001; }
upstream hfl_web_tenant { server web-${web_color}:8080; keepalive 16; }
upstream hfl_web_ops { server web-${web_color}:8081; keepalive 16; }
upstream hfl_website { server web-${web_color}:8082; keepalive 8; }
EOF
	mv "${temporary}" "${output}"
}

write_active_color() {
	local color=$1 state_dir="$(blue_green_state_dir)" temporary
	case "${color}" in blue | green) ;; *) die "invalid active color: ${color}" ;; esac
	mkdir -p "${state_dir}"
	temporary="${state_dir}/active-color.tmp.$$"
	printf '%s\n' "${color}" > "${temporary}"
	mv "${temporary}" "$(active_color_file)"
}

ensure_blue_green_state() {
	local color
	if ! color="$(read_active_color)"; then
		color=blue
		write_active_color "${color}"
	fi
	render_active_upstreams "${color}"
	if [[ ! -f "$(blue_green_state_dir)/deployment-state" ]]; then
		record_deployment_phase complete "${color}" "${color}" "$(read_version)"
	fi
}

compose_color() {
	local color=$1
	shift
	case "${color}" in blue | green) ;; *) die "invalid compose color: ${color}" ;; esac
	compose_in_root --profile "${color}" "$@"
}

compose_all_profiles() {
	compose_in_root --profile blue --profile green --profile tools "$@"
}

active_api_service() {
	local color
	color="$(read_active_color)" || die "active blue/green color is unavailable"
	printf 'api-%s' "${color}"
}

container_health_status() {
	local cid=$1
	docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${cid}" 2>/dev/null || true
}

wait_for_services_health() {
	local timeout_seconds=$1
	shift
	local deadline=$((SECONDS + timeout_seconds))
	while ((SECONDS < deadline)); do
		local ready=1 service cid status service_count
		for service in "$@"; do
			service_count=0
			while IFS= read -r cid; do
				[[ -n "${cid}" ]] || continue
				service_count=$((service_count + 1))
				status="$(container_health_status "${cid}")"
				if [[ "${status}" != "healthy" && "${status}" != "running" ]]; then
					ready=0
					break
				fi
			done < <(compose_in_root ps -q "${service}" 2>/dev/null)
			if [[ "${service_count}" -eq 0 ]]; then
				ready=0
			fi
			[[ "${ready}" -eq 1 ]] || break
		done
		[[ "${ready}" -eq 1 ]] && return 0
		sleep 3
	done
	return 1
}

wait_for_color_health() {
	local color=$1 timeout_seconds="${HFL_HEALTH_TIMEOUT_SECONDS:-600}"
	step "Waiting for ${color} API/Web pool health (timeout ${timeout_seconds}s) ..."
	wait_for_services_health "${timeout_seconds}" "api-${color}" "web-${color}"
}

reload_stable_nginx() {
	compose_in_root exec -T nginx nginx -t
	compose_in_root exec -T nginx nginx -s reload
}

start_hfl_stack() {
	local color
	ensure_blue_green_state
	color="$(read_active_color)"
	compose_in_root up -d --no-build --no-recreate postgres redis
	compose_in_root --profile tools run --rm --no-deps migration
	compose_in_root up -d --no-build worker scheduler
	compose_color "${color}" up -d --no-build "api-${color}" "web-${color}"
	wait_for_color_health "${color}" || return 1
	compose_in_root up -d --no-build nginx
	# Compose may recreate an active-color container with a new address while the
	# stable gateway itself stays running. Reload so Nginx resolves the current
	# service addresses instead of retaining a stale upstream from its last start.
	reload_stable_nginx || return 1
}

drain_api_color() {
	local color=$1
	local service="api-${color}"
	local cid ws_instance
	DRAINED_WS_INSTANCES=""
	if [[ "${color}" == "legacy" ]]; then
		[[ -n "${UPGRADE_LEGACY_API_CID}" ]] || return 0
		ws_instance="$(docker inspect --format '{{.Config.Hostname}}' "${UPGRADE_LEGACY_API_CID}" 2>/dev/null || true)"
		DRAINED_WS_INSTANCES="${ws_instance}"
		docker exec "${UPGRADE_LEGACY_API_CID}" \
			python manage.py ws_recovery_gate drain --grace 3 || true
		return 0
	else
		while IFS= read -r cid; do
			[[ -n "${cid}" ]] || continue
			ws_instance="$(docker inspect --format '{{.Config.Hostname}}' "${cid}" 2>/dev/null || true)"
			if [[ -n "${ws_instance}" ]]; then
				DRAINED_WS_INSTANCES="${DRAINED_WS_INSTANCES:+${DRAINED_WS_INSTANCES},}${ws_instance}"
			fi
			docker exec "${cid}" \
				python manage.py ws_recovery_gate drain --grace 3 || true
		done < <(compose_in_root ps -q "${service}" 2>/dev/null)
	fi
}

remove_retired_color() {
	local color=$1
	if [[ "${color}" == "legacy" ]]; then
		if [[ -n "${UPGRADE_LEGACY_API_CID}" ]] \
			&& container_owned_by_installation "${UPGRADE_LEGACY_API_CID}"; then
			docker stop --time 90 "${UPGRADE_LEGACY_API_CID}" >/dev/null 2>&1 || true
			docker rm "${UPGRADE_LEGACY_API_CID}" >/dev/null 2>&1 || true
		fi
		return 0
	fi
	compose_in_root stop "api-${color}" "web-${color}" || true
	compose_in_root rm -f "api-${color}" "web-${color}" || true
}

wait_for_public_endpoints() {
	local timeout_seconds="${HFL_HEALTH_TIMEOUT_SECONDS:-600}"
	local website_port tenant_port deadline
	website_port="$(read_env_value HFL_WEBSITE_PORT)"
	[[ -n "${website_port}" ]] || website_port=11442
	tenant_port="$(read_env_value HFL_TENANT_PORT)"
	[[ -n "${tenant_port}" ]] || tenant_port=11443
	deadline=$((SECONDS + timeout_seconds))
	while ((SECONDS < deadline)); do
		if curl -kfsS "https://127.0.0.1:${website_port}/en/" >/dev/null 2>&1 \
			&& curl -kfsS "https://127.0.0.1:${tenant_port}/health/ready" >/dev/null 2>&1; then
			return 0
		fi
		sleep 3
	done
	return 1
}

cutover_hfl_color() {
	local previous=$1 target=$2
	UPGRADE_HFL_CUTOVER_ATTEMPTED=1
	render_active_upstreams "${target}"
	if ! reload_stable_nginx || ! wait_for_public_endpoints; then
		warn "${target} cutover health failed; restoring the previous API route"
		restore_previous_hfl_color "${previous}" "${target}" || true
		return 1
	fi
	drain_api_color "${previous}"
	if ! wait_for_active_task_reattach "${target}"; then
		warn "Active task Agents did not reattach; rolling traffic back"
		restore_previous_hfl_color "${previous}" "${target}" || true
		return 1
	fi
	# Nginx reload is graceful: old workers retain already accepted HTTP/SSE
	# connections. Keep the retired upstream alive briefly before removal.
	local http_drain_seconds="${HFL_HTTP_DRAIN_SECONDS:-30}"
	[[ "${http_drain_seconds}" =~ ^[0-9]+$ ]] \
		|| die "HFL_HTTP_DRAIN_SECONDS must be a non-negative integer"
	if [[ "${http_drain_seconds}" -gt 0 ]]; then
		log "Draining existing HTTP/SSE connections for ${http_drain_seconds}s"
		sleep "${http_drain_seconds}"
	fi
	ok "Stable entry switched to ${target}; cutover remains uncommitted through the final HFL gate"
}

wait_for_active_task_reattach() {
	local color=$1 timeout_seconds="${HFL_AGENT_REATTACH_TIMEOUT_SECONDS:-180}"
	local -a args=(reattach --timeout "${timeout_seconds}") drained_instances=()
	local ws_instance
	IFS=',' read -ra drained_instances <<<"${DRAINED_WS_INSTANCES}"
	for ws_instance in "${drained_instances[@]}"; do
		[[ -n "${ws_instance}" ]] && args+=(--exclude-instance "${ws_instance}")
	done
	if [[ "${color}" == "legacy" ]]; then
		[[ -n "${UPGRADE_LEGACY_API_CID}" ]] || return 1
		docker exec "${UPGRADE_LEGACY_API_CID}" \
			python manage.py ws_recovery_gate "${args[@]}"
	else
		compose_in_root exec -T "api-${color}" \
			python manage.py ws_recovery_gate "${args[@]}"
	fi
}

restore_previous_hfl_color() {
	local previous=$1 target=$2 rollback_web=$1
	[[ "${previous}" == "legacy" ]] && rollback_web="${target}"
	render_active_upstreams "${previous}" "${rollback_web}"
	reload_stable_nginx || return 1
	wait_for_public_endpoints || return 1
	if [[ "${UPGRADE_HFL_CUTOVER_ATTEMPTED}" == "1" ]]; then
		# Some Agents may already own long-running work through the candidate
		# Daphne pool. Close those sessions only after the previous route is live,
		# then prove they reattached away from every candidate instance.
		drain_api_color "${target}"
		wait_for_active_task_reattach "${previous}" || return 1
	fi
	if [[ "${previous}" == "legacy" ]]; then
		# The first topology migration has no separately addressable legacy Web
		# pool. Keep the healthy target Web serving the SPA/Website while rolling
		# only API and Agent traffic back to the legacy API container.
		compose_in_root stop "api-${target}" || true
		compose_in_root rm -f "api-${target}" || true
	else
		remove_retired_color "${target}"
		write_active_color "${previous}"
	fi
	return 0
}

begin_sourcelens_maintenance_gate() {
	local timeout_seconds="${SOURCELENS_DRAIN_TIMEOUT_SECONDS:-600}"
	arm_sourcelens_proxy_gate \
		|| die "could not arm the SourceLens direct Run creation gate"
	SOURCELENS_MAINTENANCE_ARMED=1
	compose_in_root exec -T "$(active_api_service)" \
		python manage.py sourcelens_upgrade_gate begin --timeout "${timeout_seconds}"
}

clear_sourcelens_maintenance_gate() {
	compose_in_root exec -T "$(active_api_service)" \
		python manage.py sourcelens_upgrade_gate end
	SOURCELENS_MAINTENANCE_ARMED=0
}

sourcelens_proxy_gate_path() {
	printf '%s/deploy/nginx/hfl-maintenance/run-creation-gate.conf' \
		"${SOURCELENS_INSTALL_DIR}"
}

write_sourcelens_proxy_gate() {
	local state=$1 path directory temporary rule=""
	path="$(sourcelens_proxy_gate_path)"
	directory="$(dirname "${path}")"
	case "${state}" in
	on)
		rule='    "~^POST:/api/lens/sessions/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/runs/$" 1;'
		;;
	off) ;;
	*) return 2 ;;
	esac
	mkdir -p "${directory}"
	temporary="$(mktemp "${directory}/.run-creation-gate.XXXXXX")"
	{
		printf '%s\n' \
			'# HFL-owned SourceLens maintenance gate; updated atomically by install.sh.' \
			'map "$request_method:$uri" $hfl_sourcelens_run_creation_blocked {' \
			'    default 0;'
		[[ -n "${rule}" ]] && printf '%s\n' "${rule}"
		printf '%s\n' '}'
	} >"${temporary}"
	chmod 644 "${temporary}"
	mv -f "${temporary}" "${path}"
}

sourcelens_nginx_running() {
	local container_id
	container_id="$(sourcelens_compose ps -q nginx 2>/dev/null || true)"
	[[ -n "${container_id}" ]] \
		&& [[ "$(docker inspect --format '{{.State.Running}}' "${container_id}" 2>/dev/null || true)" == "true" ]]
}

sourcelens_nginx_has_proxy_gate() {
	sourcelens_compose exec -T nginx nginx -T 2>&1 \
		| grep -F 'if ($hfl_sourcelens_run_creation_blocked)' >/dev/null
}

recreate_sourcelens_nginx_with_proxy_gate() {
	local timeout_seconds="${SOURCELENS_PROXY_GATE_BOOTSTRAP_TIMEOUT_SECONDS:-120}"
	local adoption_dir adoption_config adoption_override
	[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ ]] || return 2
	warn "Legacy SourceLens Nginx has no direct Run gate; recreating only its proxy with the gate armed"
	# Compose stops the old proxy before starting the replacement. During that
	# short first-adoption window no direct client can create a Run, while the
	# API, workers, scheduler, and LensNodes remain online to drain existing work.
	if [[ -n "${SOURCELENS_GATE_ADOPTION_SOURCE}" \
		&& -f "${SOURCELENS_GATE_ADOPTION_SOURCE}/deploy/nginx/default.conf" ]]; then
		adoption_dir="${SOURCELENS_INSTALL_DIR}/deploy/nginx/hfl-maintenance"
		adoption_config="${adoption_dir}/adoption-default.conf"
		adoption_override="${adoption_dir}/adoption-compose.yml"
		cp "${SOURCELENS_GATE_ADOPTION_SOURCE}/deploy/nginx/default.conf" "${adoption_config}"
		cat >"${adoption_override}" <<'YAML'
services:
  nginx:
    volumes:
      - ./deploy/nginx/hfl-maintenance/adoption-default.conf:/etc/nginx/conf.d/default.conf:ro
      - ./deploy/nginx/hfl-maintenance:/etc/nginx/hfl-maintenance:ro
YAML
		sourcelens_compose -f docker-compose.yml -f "${adoption_override}" \
			up -d --no-deps --no-build --pull never --force-recreate nginx || return 1
	else
		sourcelens_compose up -d --no-deps --no-build --pull never --force-recreate nginx \
			|| return 1
	fi
	local deadline=$((SECONDS + timeout_seconds))
	while ((SECONDS < deadline)); do
		if sourcelens_nginx_running && sourcelens_nginx_has_proxy_gate; then
			log "Legacy SourceLens Nginx adopted the HFL direct Run gate"
			return 0
		fi
		sleep 2
	done
	return 1
}

reload_sourcelens_proxy_gate() {
	sourcelens_compose exec -T nginx nginx -t >/dev/null \
		&& sourcelens_compose exec -T nginx nginx -s reload >/dev/null
}

arm_sourcelens_proxy_gate() {
	SOURCELENS_PROXY_GATE_ARMED=1
	write_sourcelens_proxy_gate on || return 1
	sourcelens_nginx_running || return 1
	if ! sourcelens_nginx_has_proxy_gate; then
		recreate_sourcelens_nginx_with_proxy_gate || return 1
	fi
	if ! reload_sourcelens_proxy_gate; then
		write_sourcelens_proxy_gate off || true
		reload_sourcelens_proxy_gate || true
		return 1
	fi
	log "SourceLens direct Run creation gate armed"
}

clear_sourcelens_proxy_gate() {
	# Writing the off state is authoritative for every future container start.
	# Refresh the live process as well so an in-memory on state cannot linger.
	write_sourcelens_proxy_gate off || return 1
	if sourcelens_nginx_running; then
		if ! reload_sourcelens_proxy_gate; then
			warn "SourceLens Nginx reload failed while clearing the direct Run gate; restarting Nginx"
			# Preserve SOURCELENS_PROXY_GATE_ARMED on failure so the upgrade
			# exit trap retries instead of leaving old workers blocking Runs.
			sourcelens_compose restart nginx >/dev/null || return 1
		fi
	fi
	SOURCELENS_PROXY_GATE_ARMED=0
	log "SourceLens direct Run creation gate cleared"
}

container_owned_by_installation() {
	local container_id=$1 project working_dir config_files expected_dir
	project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "${container_id}" 2>/dev/null || true)"
	working_dir="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "${container_id}" 2>/dev/null || true)"
	config_files="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "${container_id}" 2>/dev/null || true)"
	case "${project}" in
	hyperfilelens) expected_dir="${ROOT}" ;;
	hyperfilelens-sourcelens | sourcelens) expected_dir="${ROOT}/sourcelens" ;;
	*) return 1 ;;
	esac
	[[ "${working_dir}" == "${expected_dir}" \
		|| ",${config_files}," == *",${expected_dir}/docker-compose.yml,"* ]]
}

ensure_bridge_network() {
	if docker network inspect "${HFL_BRIDGE_NETWORK}" >/dev/null 2>&1; then
		local container_id project
		while IFS= read -r container_id; do
			[[ -n "${container_id}" ]] || continue
			project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "${container_id}" 2>/dev/null || true)"
			container_owned_by_installation "${container_id}" \
				|| die "network ${HFL_BRIDGE_NETWORK} is attached to non-HFL container ${container_id} (project ${project:-unknown})"
		done < <(docker network inspect --format '{{range $id, $_ := .Containers}}{{println $id}}{{end}}' "${HFL_BRIDGE_NETWORK}")
		return 0
	fi
	log "Creating shared bridge network ${HFL_BRIDGE_NETWORK}"
	docker network create --label com.hyperfilelens.managed=true "${HFL_BRIDGE_NETWORK}" >/dev/null
}

warn_host_resources() {
	local cpu_count mem_total_kib mem_available_kib swap_total_kib
	cpu_count="$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo 0)"
	if [[ ! "${cpu_count}" =~ ^[0-9]+$ || "${cpu_count}" -lt 2 ]]; then
		warn "fewer than 2 CPU cores detected (${cpu_count:-unknown}); installation will continue with reduced throughput"
	fi
	mem_total_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null)"
	mem_available_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null)"
	swap_total_kib="$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo 2>/dev/null)"
	if [[ "${mem_total_kib:-0}" -lt $((4 * 1024 * 1024)) ]]; then
		warn "less than 4 GiB physical memory detected; installation will continue but may be unstable under concurrent load"
	elif [[ "${mem_total_kib:-0}" -lt $((8 * 1024 * 1024)) ]]; then
		warn "less than the recommended 8 GiB physical memory detected; installation will continue"
	fi
	if [[ "${mem_available_kib:-0}" -lt $((2500 * 1024)) ]]; then
		warn "less than 2.5 GiB memory is currently available; installation will continue"
	fi
	if [[ "${swap_total_kib:-0}" -eq 0 ]]; then
		warn "no swap is configured; installation will continue, but memory pressure can invoke the host OOM killer"
	fi
}

preflight_install_capacity() {
	local disk_available_bytes
	local website_bind website_port tenant_bind tenant_port admin_bind admin_port sourcelens_bind sourcelens_port
	warn_host_resources
	disk_available_bytes="$(df -PB1 "$(dirname "${INSTALL_DIR}")" | awk 'NR == 2 {print $4}')"
	[[ "${disk_available_bytes:-0}" -ge $((20 * 1024 * 1024 * 1024)) ]] \
		|| die "at least 20 GiB free disk space is required under $(dirname "${INSTALL_DIR}")"
	website_bind="$(read_env_value HFL_WEBSITE_BIND_ADDRESS)"
	website_port="$(read_env_value HFL_WEBSITE_PORT)"
	tenant_bind="$(read_env_value HFL_TENANT_BIND_ADDRESS)"
	tenant_port="$(read_env_value HFL_TENANT_PORT)"
	admin_bind="$(read_env_value HFL_ADMIN_BIND_ADDRESS)"
	admin_port="$(read_env_value HFL_ADMIN_PORT)"
	sourcelens_bind="$(read_env_value SOURCELENS_CONSOLE_BIND_ADDRESS)"
	sourcelens_port="$(read_env_value SOURCELENS_CONSOLE_PORT)"
	python3 - \
		"${website_bind:-0.0.0.0}" "${website_port:-11442}" \
		"${tenant_bind:-0.0.0.0}" "${tenant_port:-11443}" \
		"${admin_bind:-0.0.0.0}" "${admin_port:-11444}" \
		"${sourcelens_bind:-0.0.0.0}" "${sourcelens_port:-11445}" <<'PY'
import socket
import sys

arguments = iter(sys.argv[1:])
for bind_address, raw_port in zip(arguments, arguments):
    port = int(raw_port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sock.bind((bind_address, port))
    except OSError as exc:
        raise SystemExit(f"host endpoint {bind_address}:{port} is unavailable: {exc}") from exc
    finally:
        sock.close()
PY
	ok "Host capacity checks completed; CPU, memory, and swap recommendations are advisory"
}

read_version_from_dir() {
	local dir=$1
	if [[ -f "${dir}/VERSION" ]]; then
		tr -d ' \t\r\n' < "${dir}/VERSION"
	elif [[ -f "${dir}/MANIFEST.json" ]]; then
		python3 -c "import json; m=json.load(open('${dir}/MANIFEST.json')); print(m.get('artifact_id') if m.get('channel') == 'main' else m['version'])"
	else
		die "cannot read version from ${dir} (missing VERSION and MANIFEST.json)"
	fi
}

read_channel_from_dir() {
	local dir=$1
	if [[ ! -f "${dir}/MANIFEST.json" ]]; then
		printf 'release'
		return
	fi
	python3 - "${dir}/MANIFEST.json" <<'PY'
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(str(manifest.get("channel") or "release").strip())
PY
}

read_image_version_from_dir() {
	local dir=$1
	python3 - "${dir}" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
manifest_path = root / "MANIFEST.json"
if manifest_path.is_file():
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    value = str(manifest.get("image_version") or "").strip()
    if value:
        print(value)
        raise SystemExit(0)
print((root / "VERSION").read_text(encoding="utf-8").strip())
PY
}

validate_package_identity() {
	local dir=$1
	python3 - "${dir}" <<'PY'
import json
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
channel = str(manifest.get("channel") or "release").strip()
commit = str(manifest.get("git_commit") or "").strip().lower()
artifact_id = str(manifest.get("artifact_id") or "").strip().lower()
version_file = (root / "VERSION").read_text(encoding="utf-8").strip()
edition = str(manifest.get("edition") or "community").strip()
image_version = str(manifest.get("image_version") or version_file).strip()
extension_commit = str(manifest.get("extension_commit") or "").strip().lower()

if not re.fullmatch(r"[0-9a-f]{40}", commit):
    raise SystemExit("release manifest has an invalid git_commit")
if channel == "main":
    if not re.fullmatch(r"main-[0-9a-f]{7}", artifact_id):
        raise SystemExit("main package has an invalid artifact_id")
    if artifact_id != f"main-{commit[:7]}":
        raise SystemExit("main package artifact_id does not match git_commit")
    if "version" in manifest:
        raise SystemExit("main package must not declare a release version")
    expected = artifact_id
elif channel == "release":
    version = str(manifest.get("version") or "").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise SystemExit("release package has an invalid version")
    if artifact_id and artifact_id != f"v{version}":
        raise SystemExit("release package artifact_id does not match version")
    expected = version
else:
    raise SystemExit(f"unsupported release channel: {channel}")
if version_file != expected:
    raise SystemExit("VERSION does not match the release manifest identity")
if edition not in {"community", "enterprise"}:
    raise SystemExit("release package has an invalid edition")
expected_image_version = version_file + ("-ee" if edition == "enterprise" else "")
if image_version != expected_image_version:
    raise SystemExit("release package image_version does not match its edition")
if edition == "enterprise":
    if not re.fullmatch(r"[0-9a-f]{40}", extension_commit):
        raise SystemExit("Enterprise release package has an invalid extension_commit")
elif extension_commit:
    raise SystemExit("Community release package must not declare extension_commit")
PY
}

read_version() {
	read_version_from_dir "${ROOT}"
}

random_hex() {
	if command -v openssl >/dev/null 2>&1; then
		openssl rand -hex 32
	else
		python3 -c "import secrets; print(secrets.token_hex(32))"
	fi
}

ensure_data_dirs() {
	mkdir -p \
		"${ROOT}/data/postgresql" \
		"${ROOT}/data/redis" \
		"${ROOT}/data/logs" \
		"${ROOT}/data/lang-packs" \
		"${ROOT}/data/media/agent-releases" \
		"${ROOT}/data/media/enroll-bootstrap" \
		"${ROOT}/data/media/gateway-bootstrap" \
		"${ROOT}/data/media/snapshot-downloads" \
		"${ROOT}/data/staticfiles" \
		"${ROOT}/data/sourcelens/config"
	chmod 0755 "${ROOT}/data/lang-packs"
	refresh_language_pack_index "${ROOT}/data/lang-packs"
}

sync_runtime_media() {
	local packaged_media="${ROOT}/payload/media"
	[[ -d "${packaged_media}" ]] || return 0
	mkdir -p "${ROOT}/data/media"
	rsync -aH "${packaged_media}/" "${ROOT}/data/media/"
	local dir
	for dir in agent-releases enroll-bootstrap gateway-bootstrap; do
		[[ -d "${ROOT}/data/media/${dir}" ]] || continue
		find "${ROOT}/data/media/${dir}" -type d -exec chmod 755 {} +
		find "${ROOT}/data/media/${dir}" -type f -exec chmod 644 {} +
	done
	find "${ROOT}/data/media/agent-releases" -type f -name '*.sh' \
		-exec chmod 755 {} + 2>/dev/null || true
	if [[ -d "${ROOT}/data/media/enroll-bootstrap" ]]; then
		find "${ROOT}/data/media/enroll-bootstrap" -type f -name 'hfl-enroll-*' \
			-exec chmod 755 {} +
	fi
	find "${ROOT}/data/media/gateway-bootstrap" -type f -name '*.sh' \
		-exec chmod 755 {} + 2>/dev/null || true
	prune_minimal_installer_media
}

prune_minimal_installer_media() {
	local media_root="${ROOT}/data/media/enroll-bootstrap"
	local current_version="" candidate="" name=""
	[[ -d "${media_root}" ]] || return 0
	current_version="$(read_version 2>/dev/null || true)"
	if [[ -n "${current_version}" && -d "${media_root}/${current_version}" ]]; then
		touch "${media_root}/${current_version}"
	fi
	while IFS= read -r -d '' candidate; do
		name="$(basename "${candidate}")"
		[[ "${name}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || continue
		[[ "${name}" != "${current_version}" ]] || continue
		if [[ -n "$(find "${candidate}" -maxdepth 0 -mtime +2 -print -quit)" ]]; then
			safe_assert_path_under_dir "${candidate}" "${media_root}" "minimal installer version"
			safe_rm_dir "${candidate}"
			log "Pruned expired minimal installer version ${name}"
		fi
	done < <(find "${media_root}" -mindepth 1 -maxdepth 1 -type d -print0)
	while IFS= read -r -d '' candidate; do
		safe_assert_path_under_dir "${candidate}" "${media_root}" "legacy minimal installer"
		safe_rm_file "${candidate}"
		log "Pruned expired legacy minimal installer $(basename "${candidate}")"
	done < <(find "${media_root}" -maxdepth 1 -type f -name 'hfl-installer-*' -mtime +2 -print0)
}

prune_agent_release_media() {
	local releases="${ROOT}/data/media/agent-releases"
	local desired="" installed="" marker="${ROOT}/data/.platform-gateway-agent-upgrade"
	local action name target
	[[ -d "${releases}" ]] || return 0
	desired="$(read_version 2>/dev/null || true)"
	if [[ -f "${LOCAL_PLATFORM_AGENT_INSTALL_DIR}/INSTALLED_VERSION" ]]; then
		installed="$(tr -d ' \t\r\n' <"${LOCAL_PLATFORM_AGENT_INSTALL_DIR}/INSTALLED_VERSION")"
	fi

	while IFS=$'\t' read -r action name; do
		[[ -n "${action}" ]] || continue
		case "${action}" in
		REMOVE)
			target="${releases}/${name}"
			if [[ ! -d "${target}" || -L "${target}" ]]; then
				warn "Skipping unsafe Agent release retention candidate ${target}"
				continue
			fi
			safe_assert_path_under_dir "${target}" "${releases}" "Agent release path"
			safe_rm_dir "${target}"
			log "Removed expired Agent release media ${name}"
			;;
		RETAIN_INVALID)
			warn "Retaining unrecognized or unsafe Agent release media entry ${name}"
			;;
		esac
	done < <(python3 - "${releases}" "${desired}" "${installed}" "${marker}" <<'PY'
from __future__ import annotations

import os
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
desired = sys.argv[2].strip()
installed = sys.argv[3].strip()
marker_path = pathlib.Path(sys.argv[4])
main_pattern = re.compile(r"^main-[0-9a-f]{7}$")
semver_pattern = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

protected = {value for value in (desired, installed) if value}
try:
    marker_version = marker_path.read_text(encoding="utf-8").strip()
except OSError:
    marker_version = ""
if marker_version:
    protected.add(marker_version)

main_entries = []
semver_entries = []
invalid_entries = []
with os.scandir(root) as entries:
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                invalid_entries.append(entry.name)
                continue
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            invalid_entries.append(entry.name)
            continue
        if main_pattern.fullmatch(entry.name):
            main_entries.append((stat.st_mtime_ns, entry.name))
            continue
        match = semver_pattern.fullmatch(entry.name)
        if match:
            semver_entries.append((tuple(map(int, match.groups())), entry.name))
            continue
        invalid_entries.append(entry.name)

keep = set(protected)
semver_entries.sort(reverse=True)
keep.update(name for _, name in semver_entries[:3])

main_entries.sort(reverse=True)
ordered_main = [name for _, name in main_entries]
if desired in ordered_main:
    ordered_main.remove(desired)
    ordered_main.insert(0, desired)
keep.update(ordered_main[:3])

for _, name in [*main_entries, *semver_entries]:
    if name not in keep:
        print(f"REMOVE\t{name}")
for name in sorted(invalid_entries):
    print(f"RETAIN_INVALID\t{name}")
PY
	)
}

ensure_tls_certs() {
	validate_tls_pair "${ROOT}/deploy/nginx/certs"
	secure_tls_permissions "${ROOT}/deploy/nginx/certs"
	log "Using existing TLS certificate pair"
}

tls_pair_state() {
	local cert_dir=$1
	local cert="${cert_dir}/tls.crt"
	local key="${cert_dir}/tls.key"
	if [[ -s "${cert}" && -s "${key}" ]]; then
		printf 'complete'
	elif [[ ! -e "${cert}" && ! -e "${key}" ]]; then
		printf 'missing'
	else
		printf 'incomplete'
	fi
}

validate_tls_pair() {
	local cert_dir=$1
	local cert="${cert_dir}/tls.crt"
	local key="${cert_dir}/tls.key"
	local cert_pub key_pub
	[[ "$(tls_pair_state "${cert_dir}")" == "complete" ]] \
		|| die "TLS certificate and key must both exist under ${cert_dir}"
	command -v openssl >/dev/null 2>&1 \
		|| die "openssl is required to validate TLS certificates"
	openssl x509 -in "${cert}" -noout >/dev/null 2>&1 \
		|| die "invalid TLS certificate: ${cert}"
	openssl pkey -in "${key}" -check -noout >/dev/null 2>&1 \
		|| die "invalid TLS private key: ${key}"
	cert_pub="$(openssl x509 -in "${cert}" -pubkey -noout | sha256sum | cut -d' ' -f1)"
	key_pub="$(openssl pkey -in "${key}" -pubout 2>/dev/null | sha256sum | cut -d' ' -f1)"
	[[ "${cert_pub}" == "${key_pub}" ]] \
		|| die "TLS certificate and private key do not match under ${cert_dir}"
	openssl x509 -in "${cert}" -checkend 0 -noout >/dev/null 2>&1 \
		|| die "TLS certificate is expired or not yet valid: ${cert}"
	if ! openssl x509 -in "${cert}" -checkend 2592000 -noout >/dev/null 2>&1; then
		warn "TLS certificate expires within 30 days: ${cert}"
	fi
}

secure_tls_permissions() {
	local cert_dir=$1
	chmod 644 "${cert_dir}/tls.crt"
	chmod 600 "${cert_dir}/tls.key"
	[[ ! -f "${cert_dir}/root-ca.crt" ]] || chmod 644 "${cert_dir}/root-ca.crt"
}

validate_default_tls_bundle() {
	local cert_dir=$1
	for required in tls.crt tls.key root-ca.crt SHA256SUMS README.md; do
		[[ -s "${cert_dir}/${required}" ]] \
			|| die "default TLS bundle is missing ${cert_dir}/${required}"
	done
	(
		cd "${cert_dir}"
		sha256sum --strict --check SHA256SUMS >/dev/null
	) || die "default TLS bundle checksum validation failed under ${cert_dir}"
	validate_tls_pair "${cert_dir}"
	openssl verify -CAfile "${cert_dir}/root-ca.crt" "${cert_dir}/tls.crt" >/dev/null 2>&1 \
		|| die "default TLS certificate is not signed by root-ca.crt"
}

sync_default_tls_bundle() {
	local source_dir=$1
	local target_dir="${ROOT}/deploy/nginx/certs"
	case "$(tls_pair_state "${target_dir}")" in
	complete)
		log "Preserving existing TLS certificate directory"
		validate_tls_pair "${target_dir}"
		secure_tls_permissions "${target_dir}"
		;;
	missing)
		step "Installing repository-pinned default TLS certificates ..."
		mkdir -p "${target_dir}"
		rsync -aH "${source_dir}/" "${target_dir}/"
		validate_tls_pair "${target_dir}"
		secure_tls_permissions "${target_dir}"
		;;
	incomplete)
		die "existing TLS certificate pair is incomplete under ${target_dir}"
		;;
	esac
}

ensure_env_file() {
	local env_file="${ROOT}/.env"
	local example="${ROOT}/.env.example"
	[[ -f "${example}" ]] || die "missing .env.example"

	if [[ -f "${env_file}" ]]; then
		chmod 600 "${env_file}"
		log ".env already exists; synchronizing missing keys"
		sync_env_from_example "${example}"
		return 0
	fi

	local version image_version channel secret db_pass host
	version="$(read_version)"
	image_version="$(read_image_version_from_dir "${ROOT}")"
	channel="$(read_channel_from_dir "${ROOT}")"
	secret="$(random_hex)"
	db_pass="$(random_hex | cut -c1-32)"
	host="${PUBLIC_HOST}"
	if [[ -z "${host}" ]]; then
		host="$(hostname -I 2>/dev/null | awk '{ for (i = 1; i <= NF; i++) if (!found && $i ~ /^[0-9]+\./) { print $i; found = 1 } }' || true)"
	fi
	[[ -n "${host}" ]] || host="127.0.0.1"

	step "Creating .env from .env.example ..."
	cp "${example}" "${env_file}"
	chmod 600 "${env_file}"
	python3 - "${env_file}" "${version}" "${image_version}" "${channel}" "${secret}" "${db_pass}" "${host}" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
version, image_version, channel, secret, db_pass, host = sys.argv[2:8]
text = path.read_text(encoding="utf-8")

def sub_key(name, value):
    global text
    pattern = rf"^({re.escape(name)}=).*$"
    if re.search(pattern, text, flags=re.M):
        text = re.sub(pattern, lambda m, v=value: f"{m.group(1)}{v}", text, count=1, flags=re.M)
    else:
        text = text.rstrip() + f"\n{name}={value}\n"

sub_key("AGENT_VERSION", version)
sub_key("APP_VERSION", image_version)
sub_key("HFL_GATEWAY_VERSION", image_version)
sub_key("HFL_RELEASE_CHANNEL", channel)
sub_key("SECRET_KEY", secret)
sub_key("POSTGRES_PASSWORD", db_pass)
sub_key("DJANGO_DEBUG", "false")
sub_key("SENTRY_ENVIRONMENT", "")
sub_key("HFL_EMAIL_SIGNUP_ENABLED", "false")
sub_key("HFL_EMAIL_CODE_LOGIN_ENABLED", "true")
sub_key("HFL_GOOGLE_OAUTH_ENABLED", "false")
sub_key("HFL_PLATFORM_OPS_ENABLED", "true")
sub_key("TURNSTILE_ENABLED", "false")
tenant_port = "11443"
frontend_url = f"https://{host}:{tenant_port}"
sub_key("FRONTEND_URL", frontend_url)
sub_key("DJANGO_ALLOWED_HOSTS", f"localhost,127.0.0.1,{host}")
sub_key(
    "CSRF_TRUSTED_ORIGINS",
    f"https://localhost:{tenant_port},https://127.0.0.1:{tenant_port},{frontend_url}",
)
sub_key(
    "CORS_ALLOWED_ORIGINS",
    f"https://localhost:{tenant_port},https://127.0.0.1:{tenant_port},{frontend_url}",
)
path.write_text(text, encoding="utf-8")
PY
	chmod 600 "${env_file}"
	reconcile_hfl_extensions_env "${env_file}" "${example}"
	log ".env created (fixed login credentials, generated internal secrets, DJANGO_DEBUG=false)"
}

apply_runtime_configuration() {
	local helper="${ROOT}/apply-runtime-config.py"
	local -a args=(--env-file "${ROOT}/.env" --direct-host "${PUBLIC_HOST}" --public-url "${PUBLIC_URL}" --admin-public-url "${ADMIN_PUBLIC_URL}")
	[[ -f "${helper}" ]] || die "missing runtime configuration helper: ${helper}"
	if [[ -n "${RUNTIME_ENV_FILE}" ]]; then
		args+=(--runtime-env-file "${RUNTIME_ENV_FILE}")
	fi
	step "Applying final runtime configuration before service startup ..."
	python3 "${helper}" "${args[@]}"
	chmod 600 "${ROOT}/.env"
}

preflight_package_layout() {
	local require_blue_green=${1:-1}
	step "Checking release package layout..."
	[[ -f "${ROOT}/MANIFEST.json" ]] || die "missing MANIFEST.json"
	[[ -f "${ROOT}/docker-compose.yml" ]] || die "missing docker-compose.yml"
	if [[ "${require_blue_green}" -eq 1 ]]; then
		[[ -f "${ROOT}/deploy/nginx/web.conf" ]] || die "missing internal Web pool configuration"
		[[ -f "${ROOT}/deploy/nginx/snippets/hfl-active-upstreams.conf" ]] \
			|| die "missing blue/green upstream configuration"
		[[ -f "${ROOT}/deploy/blue-green/active-color" ]] \
			|| die "missing blue/green initial state"
	fi
	[[ -f "${ROOT}/images/00-hyperfilelens.tar.gz" ]] || die "missing HFL image archive"
	[[ -f "${ROOT}/images/01-postgres-17.tar.gz" ]] || die "missing PostgreSQL image archive"
	[[ -f "${ROOT}/images/02-redis-alpine.tar.gz" ]] || die "missing Redis image archive"
	validate_package_identity "${ROOT}"
	log "Release package check passed"
}

preflight_blue_green_source() {
	local source_root=$1
	[[ -f "${source_root}/deploy/nginx/web.conf" ]] \
		|| die "upgrade package is missing internal Web pool configuration"
	[[ -f "${source_root}/deploy/nginx/snippets/hfl-active-upstreams.conf" ]] \
		|| die "upgrade package is missing blue/green upstream configuration"
	[[ -f "${source_root}/deploy/blue-green/active-color" ]] \
		|| die "upgrade package is missing blue/green initial state"
}

stack_containers_present() {
	require_docker
	local count
	count="$(compose_all_profiles ps -q 2>/dev/null | wc -l | tr -d ' ')"
	[[ "${count}" -gt 0 ]]
}

wait_for_hfl_health() {
	local timeout_seconds="${HFL_HEALTH_TIMEOUT_SECONDS:-600}"
	[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ ]] || die "HFL_HEALTH_TIMEOUT_SECONDS must be positive"
	local color
	color="$(read_active_color)" || { warn "active blue/green color is unavailable"; return 1; }
	local -a services=(postgres redis worker scheduler "api-${color}" "web-${color}" nginx)
	step "Waiting for HyperFileLens health checks (timeout ${timeout_seconds}s) ..."
	if wait_for_services_health "${timeout_seconds}" "${services[@]}" \
		&& wait_for_public_endpoints; then
		ok "HyperFileLens health gate passed"
		return 0
	fi
	warn "HyperFileLens health gate timed out"
	compose_all_profiles ps || true
	return 1
}

sourcelens_web_service() {
	local services
	services="$(sourcelens_compose config --services 2>/dev/null || true)"
	if grep -Fxq web <<<"${services}"; then
		printf 'web\n'
	elif grep -Fxq ui <<<"${services}"; then
		# HFL-only upgrades may intentionally retain an older SourceLens runtime.
		printf 'ui\n'
	else
		# New bundles use Web; keep that as the safe default for partial fixtures
		# and for failures that are reported by the normal health loop below.
		printf 'web\n'
	fi
}

wait_for_sourcelens_health() {
	[[ "$(configured_sourcelens_mode)" == "bundled" ]] || return 0
	if ! sourcelens_installed; then
		warn "Bundled SourceLens is configured but its runtime is not installed"
		return 1
	fi
	local timeout_seconds="${SOURCELENS_HEALTH_TIMEOUT_SECONDS:-600}"
	[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ ]] \
		|| die "SOURCELENS_HEALTH_TIMEOUT_SECONDS must be positive"
	local port
	port="$(read_env_value SOURCELENS_CONSOLE_PORT)"
	[[ -n "${port}" ]] || port=11445
	local web_service
	web_service="$(sourcelens_web_service)"
	local -a services=(api "${web_service}" worker scheduler postgres redis nginx)
	step "Waiting for bundled SourceLens service and HTTPS health (timeout ${timeout_seconds}s) ..."
	local deadline=$((SECONDS + timeout_seconds))
	while ((SECONDS < deadline)); do
		local ready=1 service cid status service_count
		for service in "${services[@]}"; do
			service_count=0
			while IFS= read -r cid; do
				[[ -n "${cid}" ]] || continue
				service_count=$((service_count + 1))
				status="$(container_health_status "${cid}")"
				if [[ "${status}" != "healthy" && "${status}" != "running" ]]; then
					ready=0
					break
				fi
			done < <(sourcelens_compose ps -q "${service}" 2>/dev/null)
			if [[ "${service_count}" -eq 0 ]]; then
				ready=0
			fi
			[[ "${ready}" -eq 1 ]] || break
		done
		if [[ "${ready}" -eq 1 ]] \
			&& curl -kfsS "https://127.0.0.1:${port}/" >/dev/null 2>&1; then
			ok "Bundled SourceLens health gate passed"
			return 0
		fi
		sleep 5
	done
	warn "Bundled SourceLens service or HTTPS health gate timed out"
	sourcelens_compose ps || true
	return 1
}

load_images_from_manifest() {
	local skip_sourcelens=${1:-0}
	local package_root=${2:-${ROOT}}
	local sourcelens_mode
	sourcelens_mode="$(configured_sourcelens_mode)"
	if [[ "${sourcelens_mode}" == "external" ]]; then
		skip_sourcelens=1
	fi
	python3 - "${package_root}" "${skip_sourcelens}" <<'PY'
import hashlib
import json
import pathlib
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
skip_sourcelens = sys.argv[2] == "1"
with (root / "MANIFEST.json").open(encoding="utf-8") as fh:
    manifest = json.load(fh)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_image(ref: str) -> dict:
    tag = ref.split("@", 1)[0]
    completed = subprocess.run(
        ["docker", "image", "inspect", tag],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return {}
    try:
        inspected = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(inspected, list) or len(inspected) != 1:
        return {}
    return inspected[0] if isinstance(inspected[0], dict) else {}

for entry in manifest.get("images", []):
    if skip_sourcelens and str(entry.get("role", "")).startswith("sourcelens"):
        continue
    rel = entry.get("file", "")
    path = root / rel
    refs = entry.get("refs", [])
    if not path.is_file():
        print(f"[install.sh] ERROR: missing image archive {path}", file=sys.stderr)
        sys.exit(1)
    expected = str(entry.get("sha256", ""))
    if expected and sha256_file(path) != expected:
        print(f"[install.sh] ERROR: sha256 mismatch for {rel}", file=sys.stderr)
        sys.exit(1)
    print(f"[install.sh] loading image {rel} ...")
    subprocess.run(["docker", "load", "-i", str(path)], check=True)
    missing = [ref for ref in refs if not inspect_image(ref)]
    if missing:
        print(
            f"[install.sh] ERROR: archive {rel} did not load expected refs: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        sys.exit(1)
    if entry.get("role") == "hyperfilelens":
        expected_revision = str(manifest.get("git_commit", "")).strip()
        if not expected_revision:
            print("[install.sh] ERROR: release manifest has no git_commit", file=sys.stderr)
            sys.exit(1)
        for ref in refs:
            config = inspect_image(ref).get("Config") or {}
            labels = config.get("Labels") or {}
            actual_revision = str(labels.get("org.opencontainers.image.revision", ""))
            if actual_revision != expected_revision:
                print(
                    f"[install.sh] ERROR: image {ref} revision {actual_revision or '<missing>'} "
                    f"does not match release {expected_revision}",
                    file=sys.stderr,
                )
                sys.exit(1)
PY
}

sync_env_from_example() {
	local example=$1
	local env_file="${ROOT}/.env"
	local sync_script="$(dirname "${example}")/sync-env.py"
	[[ -f "${example}" ]] || return 0
	[[ -f "${sync_script}" ]] || die "missing environment sync script: ${sync_script}"
	[[ -f "${env_file}" ]] && chmod 600 "${env_file}"
	step "Merging missing keys from .env.example into .env ..."
	python3 "${sync_script}" --env-file "${env_file}" --example "${example}"
	chmod 600 "${env_file}"
	reconcile_hfl_extensions_env "${env_file}" "${example}"
}

reconcile_hfl_extensions_env() {
	# Empty HFL_EXTENSIONS= in .env overrides Dockerfile ENV and disables baked plugins.
	# - Example has a non-empty value → fill missing/empty .env from example (Enterprise).
	# - Example omits the key (Community) → remove any previous extension setting.
	# Non-empty Enterprise operator overrides are preserved.
	local env_file=$1
	local example=$2
	[[ -f "${env_file}" ]] || return 0
	python3 - "${env_file}" "${example}" <<'PY'
import pathlib
import re
import sys

env_path = pathlib.Path(sys.argv[1])
example_path = pathlib.Path(sys.argv[2])
env_text = env_path.read_text(encoding="utf-8")
example_text = example_path.read_text(encoding="utf-8") if example_path.is_file() else ""

def read_key(text, key):
    m = re.search(rf"(?m)^[ \t]*{re.escape(key)}=(.*)$", text)
    if not m:
        return None
    return m.group(1).strip().strip("'\"")

example_val = read_key(example_text, "HFL_EXTENSIONS")
env_val = read_key(env_text, "HFL_EXTENSIONS")

if example_val:
    if env_val is None:
        env_text = env_text.rstrip() + f"\nHFL_EXTENSIONS={example_val}\n"
    elif env_val == "":
        env_text = re.sub(
            r"(?m)^[ \t]*HFL_EXTENSIONS=.*$",
            f"HFL_EXTENSIONS={example_val}",
            env_text,
            count=1,
        )
    else:
        raise SystemExit(0)
elif env_val is not None:
    env_text = re.sub(r"(?m)^[ \t]*HFL_EXTENSIONS=.*\n?", "", env_text)
else:
    raise SystemExit(0)

env_path.write_text(env_text, encoding="utf-8")
PY
}

pin_gateway_version_if_missing() {
	local fallback_version=$1 env_file="${ROOT}/.env"
	[[ -f "${env_file}" ]] || return 0
	python3 - "${env_file}" "${fallback_version}" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
fallback = sys.argv[2]
text = path.read_text(encoding="utf-8")
match = re.search(r"^HFL_GATEWAY_VERSION=(.*)$", text, flags=re.M)
if match and match.group(1).strip():
    raise SystemExit(0)
if match:
    text = re.sub(
        r"^HFL_GATEWAY_VERSION=.*$",
        f"HFL_GATEWAY_VERSION={fallback}",
        text,
        count=1,
        flags=re.M,
    )
else:
    text = text.rstrip() + f"\nHFL_GATEWAY_VERSION={fallback}\n"
path.write_text(text, encoding="utf-8")
PY
}

update_env_versions() {
	local version=$1
	local channel=$2
	local image_version=${3:-$1}
	python3 - "${ROOT}" "${version}" "${channel}" "${image_version}" <<'PY'
import pathlib, re, sys
root = pathlib.Path(sys.argv[1])
version = sys.argv[2]
channel = sys.argv[3]
image_version = sys.argv[4]
env = root / ".env"
if not env.exists():
    raise SystemExit(0)
text = env.read_text(encoding="utf-8")
for key, value in (("AGENT_VERSION", version), ("APP_VERSION", image_version)):
    pattern = rf"^({re.escape(key)}=).*$"
    if re.search(pattern, text, flags=re.M):
        text = re.sub(pattern, lambda m, v=value: f"{m.group(1)}{v}", text, count=1, flags=re.M)
    else:
        text = text.rstrip() + f"\n{key}={value}\n"
pattern = r"^(HFL_RELEASE_CHANNEL=).*$"
if re.search(pattern, text, flags=re.M):
    text = re.sub(pattern, rf"\g<1>{channel}", text, count=1, flags=re.M)
else:
    text = text.rstrip() + f"\nHFL_RELEASE_CHANNEL={channel}\n"
env.write_text(text, encoding="utf-8")
PY
}

backup_env_and_data() {
	local target_dir=$1 archive rc
	archive="${target_dir}/config-and-data.tar.gz"
	step "Backing up config and non-database data -> ${archive} ..."
	local -a items=()
	[[ -f "${ROOT}/.env" ]] && items+=(".env")
	[[ -d "${ROOT}/deploy/nginx/certs" ]] && items+=("deploy/nginx/certs")
	[[ -d "${ROOT}/data" ]] && items+=("data")
	if ((${#items[@]} == 0)); then
		warn "nothing to back up (.env, TLS certificates, and data/ missing); skipping"
		return 0
	fi
	set +e
	tar -czf "${archive}.part" \
		--exclude='data/postgresql' \
		--exclude='data/sourcelens/postgresql' \
		--exclude='data/redis' \
		--exclude='data/sourcelens/redis' \
		--exclude='data/logs' \
		--exclude='data/sourcelens/logs' \
		-C "${ROOT}" "${items[@]}"
	rc=$?
	set -e
	if [[ "${rc}" -gt 1 ]]; then
		return "${rc}"
	fi
	if [[ "${rc}" -eq 1 ]]; then
		warn "backup completed with warnings (live files such as nginx logs changed during archive)"
	fi
	mv "${archive}.part" "${archive}" || return 1
	chmod 600 "${archive}" || return 1
	log "Backup complete: ${archive}"
}

backup_postgresql_dump() {
	local target_dir=$1
	[[ -f "${ROOT}/.env" ]] || return 0
	if ! command -v docker >/dev/null 2>&1 \
		|| ! docker info >/dev/null 2>&1 \
		|| ! docker compose version >/dev/null 2>&1; then
		warn "Docker Compose is unavailable; a complete managed backup cannot be created"
		BACKUP_DATABASES_COMPLETE=0
		return 0
	fi
	COMPOSE=(docker compose)
	local cid
	if ! cid="$(compose_in_root ps -q postgres 2>/dev/null | head -1)"; then
		warn "Unable to inspect PostgreSQL; a complete managed backup cannot be created"
		BACKUP_DATABASES_COMPLETE=0
		return 0
	fi
	if [[ -z "${cid}" ]]; then
		warn "PostgreSQL is not running; no new managed backup will replace existing valid backups"
		BACKUP_DATABASES_COMPLETE=0
		return 0
	fi
	local dump globals
	dump="${target_dir}/hyperfilelens-postgresql.dump"
	globals="${target_dir}/hyperfilelens-postgresql-globals.sql"
	step "Creating consistent PostgreSQL logical backup ..."
	compose_in_root exec -T postgres sh -ec \
		'exec pg_dump -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-hyperfilelens}" -Fc' \
		>"${dump}.part" || return 1
	compose_in_root exec -T postgres sh -ec \
		'exec pg_dumpall -U "${POSTGRES_USER:-postgres}" --globals-only' \
		>"${globals}.part" || return 1
	[[ -s "${dump}.part" && -s "${globals}.part" ]] || return 1
	mv "${dump}.part" "${dump}" || return 1
	mv "${globals}.part" "${globals}" || return 1
	chmod 600 "${dump}" "${globals}" || return 1
	ok "PostgreSQL logical backup created"

	if sourcelens_installed; then
		local sl_cid sl_dump sl_globals
		if ! sl_cid="$(sourcelens_compose ps -q postgres 2>/dev/null | head -1)"; then
			warn "Unable to inspect bundled SourceLens PostgreSQL; no new managed backup will replace existing valid backups"
			BACKUP_DATABASES_COMPLETE=0
			return 0
		fi
		if [[ -n "${sl_cid}" ]]; then
			sl_dump="${target_dir}/sourcelens-postgresql.dump"
			sl_globals="${target_dir}/sourcelens-postgresql-globals.sql"
			step "Creating consistent bundled SourceLens PostgreSQL backup ..."
			sourcelens_compose exec -T postgres sh -ec \
				'exec pg_dump -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-backend}" -Fc' \
				>"${sl_dump}.part" || return 1
			sourcelens_compose exec -T postgres sh -ec \
				'exec pg_dumpall -U "${POSTGRES_USER:-postgres}" --globals-only' \
				>"${sl_globals}.part" || return 1
			[[ -s "${sl_dump}.part" && -s "${sl_globals}.part" ]] || return 1
			mv "${sl_dump}.part" "${sl_dump}" || return 1
			mv "${sl_globals}.part" "${sl_globals}" || return 1
			chmod 600 "${sl_dump}" "${sl_globals}" || return 1
			ok "Bundled SourceLens PostgreSQL logical backup created"
		else
			warn "Bundled SourceLens PostgreSQL is not running; no new managed backup will replace existing valid backups"
			BACKUP_DATABASES_COMPLETE=0
		fi
	fi
}

backup_redis_data() {
	local target_dir=$1
	if [[ -f "${ROOT}/data/redis/dump.rdb" ]]; then
		install -m 0600 "${ROOT}/data/redis/dump.rdb" "${target_dir}/hyperfilelens-redis.rdb" || return 1
	fi
	if [[ -f "${ROOT}/data/sourcelens/redis/dump.rdb" ]]; then
		install -m 0600 "${ROOT}/data/sourcelens/redis/dump.rdb" "${target_dir}/sourcelens-redis.rdb" || return 1
	fi
}

backup_running_image_metadata() {
	local target_dir=$1 id name ref image_id
	local -a ids=()
	if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
		mapfile -t ids < <(
			{
				compose_in_root ps -aq 2>/dev/null || true
				sourcelens_installed && sourcelens_compose ps -aq 2>/dev/null || true
				docker ps -aq --no-trunc \
					--filter 'label=com.hyperfilelens.managed=true' \
					--filter 'label=com.hyperfilelens.component=gateway-lensnode' 2>/dev/null || true
			} | awk 'NF && !seen[$0]++'
		)
	fi
	((${#ids[@]})) || return 0
	: >"${target_dir}/running-images.tsv.part" || return 1
	for id in "${ids[@]}"; do
		name="$(docker inspect --format '{{.Name}}' "${id}" 2>/dev/null | sed 's#^/##' || true)"
		ref="$(docker inspect --format '{{.Config.Image}}' "${id}" 2>/dev/null || true)"
		image_id="$(docker inspect --format '{{.Image}}' "${id}" 2>/dev/null || true)"
		[[ -n "${name}" && -n "${ref}" && -n "${image_id}" ]] || continue
		printf '%s\t%s\t%s\n' "${name}" "${ref}" "${image_id}" >>"${target_dir}/running-images.tsv.part"
	done
	if [[ -s "${target_dir}/running-images.tsv.part" ]]; then
		mv "${target_dir}/running-images.tsv.part" "${target_dir}/running-images.tsv" || return 1
		chmod 600 "${target_dir}/running-images.tsv" || return 1
	else
		rm -f "${target_dir}/running-images.tsv.part"
	fi
}

write_backup_manifest() {
	local target_dir=$1 stamp=$2
	python3 - "${target_dir}" "${stamp}" "$(read_version 2>/dev/null || true)" <<'PY' || return 1
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
files = []
for path in sorted(root.iterdir()):
    if not path.is_file() or path.name == "backup-manifest.json" or path.name.endswith(".part"):
        continue
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    files.append({"name": path.name, "size": path.stat().st_size, "sha256": digest.hexdigest()})
manifest = {
    "format": 1,
    "created_at": sys.argv[2],
    "version": sys.argv[3],
    "complete": True,
    "files": files,
}
(root / "backup-manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
	chmod 600 "${target_dir}/backup-manifest.json" || return 1
}

prune_upgrade_backups() {
	python3 - "${ROOT}/backup" <<'PY' || return 1
import pathlib
import re
import shutil
import sys
import time

root = pathlib.Path(sys.argv[1])
if not root.is_dir():
    raise SystemExit(0)

now = time.time()
for path in root.glob(".partial-*"):
    if now - path.stat().st_mtime <= 24 * 60 * 60:
        continue
    print(f"[install.sh] pruning incomplete backup {path.name}")
    shutil.rmtree(path) if path.is_dir() else path.unlink(missing_ok=True)

directory_pattern = re.compile(r"^upgrade-(?P<stamp>\d{8}-\d{6})$")
legacy_pattern = re.compile(
    r"^(?:hyperfilelens-backup|hyperfilelens-postgresql(?:-globals)?|"
    r"sourcelens-postgresql(?:-globals)?)-"
    r"(?P<stamp>\d{8}-\d{6})\.(?:tar\.gz|dump|sql)$"
)
groups = {}
for path in root.iterdir():
    match = directory_pattern.fullmatch(path.name) if path.is_dir() else legacy_pattern.fullmatch(path.name)
    if match:
        groups.setdefault(match.group("stamp"), []).append(path)

for stamp in sorted(groups, reverse=True)[3:]:
    for path in groups[stamp]:
        print(f"[install.sh] pruning expired managed backup {path.name}")
        shutil.rmtree(path) if path.is_dir() else path.unlink(missing_ok=True)
PY
}

create_managed_backup() {
	local stamp=${1:-$(date +%Y%m%d-%H%M%S)} strict=${2:-0}
	local backup_root="${ROOT}/backup" partial final
	backup_root="$(safe_normalize_dir "${backup_root}")"
	partial="${backup_root}/.partial-${stamp}-$$"
	final="${backup_root}/upgrade-${stamp}"
	mkdir -p "${partial}"
	chmod 700 "${backup_root}" "${partial}"
	BACKUP_DATABASES_COMPLETE=1
	if ! backup_postgresql_dump "${partial}"; then
		warn "database backup command failed; existing valid backups were not changed"
		rm -rf "${partial}"
		return 1
	fi
	if ! backup_env_and_data "${partial}"; then
		warn "configuration backup command failed; existing valid backups were not changed"
		rm -rf "${partial}"
		return 1
	fi
	if ! backup_redis_data "${partial}" || ! backup_running_image_metadata "${partial}"; then
		warn "runtime backup command failed; existing valid backups were not changed"
		rm -rf "${partial}"
		return 1
	fi
	for metadata in VERSION MANIFEST.json docker-compose.yml; do
		if [[ -f "${ROOT}/${metadata}" ]] && ! install -m 0600 "${ROOT}/${metadata}" "${partial}/${metadata}"; then
			rm -rf "${partial}"
			return 1
		fi
	done
	mkdir -p "${partial}/blue-green"
	for runtime_state in \
		"deploy/blue-green/active-color" \
		"deploy/blue-green/deployment-state" \
		"deploy/nginx/snippets/hfl-active-upstreams.conf"; do
		if [[ -f "${ROOT}/${runtime_state}" ]]; then
			install -m 0600 "${ROOT}/${runtime_state}" \
				"${partial}/blue-green/$(basename "${runtime_state}")" \
				|| { rm -rf "${partial}"; return 1; }
		fi
	done
	if [[ -f "${ROOT}/sourcelens/docker-compose.yml" ]]; then
		install -m 0600 "${ROOT}/sourcelens/docker-compose.yml" "${partial}/sourcelens-docker-compose.yml" \
			|| { rm -rf "${partial}"; return 1; }
	fi
	if [[ -f "${ROOT}/sourcelens/BUILD_INFO.json" ]]; then
		install -m 0600 "${ROOT}/sourcelens/BUILD_INFO.json" "${partial}/sourcelens-BUILD_INFO.json" \
			|| { rm -rf "${partial}"; return 1; }
	fi
	if [[ "${BACKUP_DATABASES_COMPLETE}" != "1" ]]; then
		warn "managed backup is incomplete because a required PostgreSQL service was unavailable"
		rm -rf "${partial}"
		if [[ "${strict}" == "1" ]]; then
			return 1
		fi
		return 0
	fi
	if ! write_backup_manifest "${partial}" "${stamp}"; then
		rm -rf "${partial}"
		return 1
	fi
	if [[ -e "${final}" ]] || ! mv "${partial}" "${final}"; then
		rm -rf "${partial}"
		return 1
	fi
	prune_upgrade_backups || return 1
	ok "Managed backup created and verified: ${final}"
}

preflight_redis_recovery() {
	local rdb="${ROOT}/data/redis/dump.rdb" cid output creation_bytes safe_bytes
	local warn_bytes=$((768 * 1024 * 1024)) limit_bytes=$((1024 * 1024 * 1024))
	[[ -s "${rdb}" ]] || { skip "Redis has no persisted RDB to validate"; return 0; }
	if ! cid="$(compose_in_root ps -q redis 2>/dev/null | head -1)" || [[ -z "${cid}" ]]; then
		warn "Redis is not running; persisted RDB recovery memory could not be measured"
		return 0
	fi
	step "Checking Redis RDB integrity and recovery memory requirement ..."
	if ! output="$(compose_in_root exec -T redis redis-check-rdb /data/dump.rdb 2>&1)"; then
		printf '%s\n' "${output}" >&2
		die "Redis RDB integrity check failed before the maintenance window"
	fi
	creation_bytes="$(printf '%s\n' "${output}" | python3 -c '
import re, sys
units = {"k": 1024, "kb": 1024, "kib": 1024, "m": 1024**2, "mb": 1024**2, "mib": 1024**2, "g": 1024**3, "gb": 1024**3, "gib": 1024**3}
for line in sys.stdin:
    lowered = line.lower()
    if "memory" not in lowered or ("creation" not in lowered and "created" not in lowered):
        continue
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([kmgt]i?b?|bytes?)", lowered)
    if match:
        unit = match.group(2)
        multiplier = 1 if unit.startswith("byte") else units.get(unit, 0)
        if multiplier:
            print(int(float(match.group(1)) * multiplier))
            break
')"
	if [[ ! "${creation_bytes}" =~ ^[0-9]+$ ]]; then
		creation_bytes="$(compose_in_root exec -T redis redis-cli --raw INFO memory 2>/dev/null \
			| tr -d '\r' | awk -F: '$1 == "used_memory" {print $2; exit}' || true)"
	fi
	if [[ ! "${creation_bytes}" =~ ^[0-9]+$ ]]; then
		warn "Redis RDB is valid, but its creation memory could not be measured; deployment will continue"
		return 0
	fi
	safe_bytes=$((creation_bytes * 2))
	if ((safe_bytes > limit_bytes)); then
		die "Redis RDB needs approximately $((safe_bytes / 1024 / 1024)) MiB to recover, exceeding the fixed 1 GiB container limit; current services were not stopped"
	elif ((safe_bytes >= warn_bytes)); then
		warn "Redis RDB recovery may need approximately $((safe_bytes / 1024 / 1024)) MiB of the 1 GiB limit"
	else
		ok "Redis RDB recovery estimate is $((safe_bytes / 1024 / 1024)) MiB within the 1 GiB limit"
	fi
}

managed_image_ref_is_in_use() {
	local ref=$1 image_id container_id container_image_id
	local -a containers=()
	image_id="$(docker image inspect "${ref}" --format '{{.Id}}' 2>/dev/null || true)"
	[[ -n "${image_id}" ]] || return 1
	mapfile -t containers < <(docker ps -aq --no-trunc 2>/dev/null || true)
	for container_id in "${containers[@]}"; do
		[[ -n "${container_id}" ]] || continue
		container_image_id="$(docker inspect --format '{{.Image}}' "${container_id}" 2>/dev/null || true)"
		if [[ "${container_image_id}" == "${image_id}" ]]; then
			return 0
		fi
	done
	return 1
}

prune_old_managed_image_refs() {
	local backup_manifest="" ref output
	local -a removable=()
	command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 || return 0
	backup_manifest="$(find "${ROOT}/backup" -mindepth 2 -maxdepth 2 -type f \
		-path '*/upgrade-*/MANIFEST.json' -print 2>/dev/null | sort -r | head -1)"
	if ! output="$(python3 - "${ROOT}/MANIFEST.json" "${backup_manifest}" <<'PY'
import json
import pathlib
import subprocess
import sys

managed_repositories = {
    "hyperfilelens-backend",
    "hyperfilelens-frontend",
    "hyperfilelens-sourcelens-backend",
    "hyperfilelens-sourcelens-frontend",
    "hyperfilelens-sourcelens-lensnode",
}
protected = set()
for raw in sys.argv[1:]:
    path = pathlib.Path(raw) if raw else None
    if not path or not path.is_file():
        continue
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    for image in manifest.get("images", []):
        protected.update(str(ref) for ref in image.get("refs", []) if ref)

containers = subprocess.run(
    ["docker", "ps", "-aq", "--no-trunc"],
    check=False,
    text=True,
    stdout=subprocess.PIPE,
).stdout.split()
if containers:
    inspected = subprocess.run(
        ["docker", "inspect", "--format", "{{.Config.Image}}", *containers],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
    )
    protected.update(line.strip() for line in inspected.stdout.splitlines() if line.strip())

listed = subprocess.run(
    ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"],
    check=True,
    text=True,
    stdout=subprocess.PIPE,
).stdout.splitlines()
for ref in sorted(set(line.strip() for line in listed if line.strip())):
    repository, separator, tag = ref.rpartition(":")
    if not separator or repository not in managed_repositories or tag == "<none>":
        continue
    if ref not in protected:
        print(ref)
PY
	)"; then
		warn "unable to resolve old HFL image references; skipping image cleanup"
		return 0
	fi
	if [[ -n "${output}" ]]; then
		mapfile -t removable <<<"${output}"
	fi
	for ref in "${removable[@]}"; do
		[[ -n "${ref}" ]] || continue
		if managed_image_ref_is_in_use "${ref}"; then
			log "Retained in-use old HFL image tag ${ref}"
		elif docker image rm "${ref}" >/dev/null 2>&1; then
			log "Removed unreferenced old HFL image tag ${ref}"
		else
			warn "Unable to remove old HFL image tag ${ref}; deployment remains healthy"
		fi
	done
}

repair_sourcelens_runtime_bindings() {
	local runtime_env="${SOURCELENS_INSTALL_DIR}/.env"
	local runtime_data="${SOURCELENS_INSTALL_DIR}/data"
	local persisted_env="${ROOT}/data/sourcelens/config/.env"
	local persisted_data="${ROOT}/data/sourcelens"
	local repaired=0

	[[ -f "${SOURCELENS_INSTALL_DIR}/docker-compose.yml" ]] || return 0
	# A staged bundle is not an installed SourceLens runtime.  Only repair the
	# bindings when the persistent configuration proves that this host already
	# owns (or previously owned) a bundled installation.
	[[ -f "${persisted_env}" ]] || return 0

	if [[ -L "${runtime_env}" ]]; then
		if [[ "$(readlink "${runtime_env}")" != "${persisted_env}" ]]; then
			ln -sfn "${persisted_env}" "${runtime_env}"
			repaired=1
		fi
	elif [[ ! -e "${runtime_env}" ]]; then
		ln -s "${persisted_env}" "${runtime_env}"
		repaired=1
	fi

	if [[ -L "${runtime_data}" ]]; then
		if [[ "$(readlink "${runtime_data}")" != "${persisted_data}" ]]; then
			ln -sfn "${persisted_data}" "${runtime_data}"
			repaired=1
		fi
	elif [[ ! -e "${runtime_data}" ]]; then
		ln -s "${persisted_data}" "${runtime_data}"
		repaired=1
	fi

	if [[ "${repaired}" -eq 1 ]]; then
		log "Repaired bundled SourceLens runtime bindings before its maintenance gate"
	fi
}

apply_upgrade_files() {
	local from_root=$1
	local remove_sourcelens=${2:-0}
	local sync_sourcelens=${3:-0}
	step "Overwriting application files and release payload ..."
	mkdir -p "${ROOT}/deploy/nginx" "${ROOT}/images" "${ROOT}/host" "${ROOT}/payload"
	sync_default_tls_bundle "${from_root}/deploy/nginx/certs"
	rsync -aH --delete "${from_root}/payload/" "${ROOT}/payload/"
	rsync -aH --delete "${from_root}/images/" "${ROOT}/images/"
	if [[ -d "${ROOT}/src" ]]; then
		safe_assert_path_under_dir "${ROOT}/src" "${ROOT}" "legacy runtime source path"
		safe_rm_dir "${ROOT}/src"
		log "Removed legacy host source tree; application code now ships only in images"
	fi
	if [[ "${remove_sourcelens}" -eq 1 && -d "${ROOT}/sourcelens" ]]; then
		safe_assert_path_under_dir "${ROOT}/sourcelens" "${ROOT}" "SourceLens runtime path"
		safe_rm_dir "${ROOT}/sourcelens"
	elif [[ "${sync_sourcelens}" -eq 1 && -d "${from_root}/sourcelens" ]]; then
		# SourceLens application files are replaceable, while these two root
		# bindings point at installation-owned persistent configuration/data.
		# Protect them from --delete so the still-running legacy proxy remains
		# addressable until its independent maintenance gate has been armed.
		rsync -aH --delete --exclude '/.env' --exclude '/data' \
			"${from_root}/sourcelens/" "${ROOT}/sourcelens/"
		repair_sourcelens_runtime_bindings
	fi
	cp "${from_root}/docker-compose.yml" "${ROOT}/docker-compose.yml"
	mkdir -p "${ROOT}/deploy/nginx/snippets"
	if [[ -d "${from_root}/deploy/nginx/snippets" ]]; then
		rsync -aH --exclude 'hfl-active-upstreams.conf' \
			"${from_root}/deploy/nginx/snippets/" "${ROOT}/deploy/nginx/snippets/"
		if [[ ! -f "${ROOT}/deploy/nginx/snippets/hfl-active-upstreams.conf" ]]; then
			cp "${from_root}/deploy/nginx/snippets/hfl-active-upstreams.conf" \
				"${ROOT}/deploy/nginx/snippets/hfl-active-upstreams.conf"
		fi
	fi
	cp "${from_root}/deploy/nginx/default.conf" "${ROOT}/deploy/nginx/default.conf"
	cp "${from_root}/deploy/nginx/web.conf" "${ROOT}/deploy/nginx/web.conf"
	mkdir -p "${ROOT}/deploy/blue-green"
	# active-color and the active upstream snippet are runtime execution cache.
	# Preserve an existing cutover state; seed blue only on first install.
	if [[ ! -f "${ROOT}/deploy/blue-green/active-color" ]]; then
		cp "${from_root}/deploy/blue-green/active-color" \
			"${ROOT}/deploy/blue-green/active-color"
	fi
	if [[ -f "${from_root}/deploy/logrotate/hyperfilelens.conf" ]]; then
		mkdir -p "${ROOT}/deploy/logrotate"
		cp "${from_root}/deploy/logrotate/hyperfilelens.conf" "${ROOT}/deploy/logrotate/hyperfilelens.conf"
	fi
	read_version_from_dir "${from_root}" > "${ROOT}/VERSION"
	cp "${from_root}/MANIFEST.json" "${ROOT}/MANIFEST.json"
	[[ -f "${from_root}/.env.example" ]] && cp "${from_root}/.env.example" "${ROOT}/.env.example"
	[[ -f "${from_root}/sync-env.py" ]] && cp "${from_root}/sync-env.py" "${ROOT}/sync-env.py" && chmod +x "${ROOT}/sync-env.py"
	[[ -f "${from_root}/apply-runtime-config.py" ]] && cp "${from_root}/apply-runtime-config.py" "${ROOT}/apply-runtime-config.py" && chmod +x "${ROOT}/apply-runtime-config.py"
	[[ -f "${from_root}/LICENSE" ]] && cp "${from_root}/LICENSE" "${ROOT}/LICENSE"
	[[ -f "${from_root}/install.sh" ]] && cp "${from_root}/install.sh" "${ROOT}/install.sh" && chmod +x "${ROOT}/install.sh"
	if [[ -d "${from_root}/host" ]]; then
		rsync -aH "${from_root}/host/" "${ROOT}/host/"
	fi
	log "File overwrite complete"
}

prepare_upgrade_source() {
	local from=$1
	if [[ -d "${from}" ]]; then
		local resolved
		resolved="$(cd "${from}" && pwd)"
		safe_assert_package_root "${resolved}"
		printf '%s' "${resolved}"
		return 0
	fi
	if [[ -f "${from}" && "${from}" == *.tar.gz ]]; then
		safe_assert_upgrade_package_file "${from}"
		safe_rm_dir "${UPGRADE_TMP}"
		mkdir -p "${UPGRADE_TMP}"
		step "Extracting ${from} -> ${UPGRADE_TMP} ..."
		tar -xzf "${from}" -C "${UPGRADE_TMP}"
		local inner
		inner="$(find "${UPGRADE_TMP}" -mindepth 1 -maxdepth 1 -type d | head -1)"
		[[ -n "${inner}" && -f "${inner}/MANIFEST.json" ]] || die "invalid tar.gz package layout"
		printf '%s' "${inner}"
		return 0
	fi
	die "upgrade --from must be a directory or hyperfilelens-*.tar.gz: ${from}"
}

cleanup_upgrade_tmp() {
	if [[ -d "${UPGRADE_TMP}" ]]; then
		step "Cleaning up ${UPGRADE_TMP} ..."
		safe_rm_dir "${UPGRADE_TMP}"
	fi
}

remove_manifest_images() {
	[[ -f "${ROOT}/MANIFEST.json" ]] || return 0
	step "Removing application Docker images..."
	python3 - "${ROOT}" <<'PY'
import json, subprocess, sys
with open(f"{sys.argv[1]}/MANIFEST.json", encoding="utf-8") as fh:
    manifest = json.load(fh)
seen = set()
for entry in manifest.get("images", []):
    if str(entry.get("role", "")).startswith("sourcelens"):
        continue
    for ref in entry.get("refs", []):
        tag = ref.split("@", 1)[0]
        if tag in seen:
            continue
        seen.add(tag)
        print(f"[install.sh] removing image {tag}")
        subprocess.run(["docker", "image", "rm", "-f", tag], check=False)
PY
}

version_lt() {
	python3 - "$1" "$2" <<'PY'
import sys
def parse(v):
    return tuple(int(x) for x in v.split("."))
try:
    sys.exit(0 if parse(sys.argv[1]) < parse(sys.argv[2]) else 1)
except Exception:
    sys.exit(1)
PY
}

confirm_same_version_upgrade() {
	local version=$1
	if [[ "${UPGRADE_YES}" -eq 1 ]]; then
		warn "new package version matches current (${version}); continuing upgrade (--yes)"
		return 0
	fi
	if [[ -t 0 ]]; then
		local ans
		printf 'Package version is already %s. Continue upgrade? [y/N] ' "${version}" >&2
		read -r ans
		case "${ans}" in
		y | Y | yes | YES) return 0 ;;
		esac
		die "upgrade aborted (same version)"
	fi
	die "same version upgrade requires a TTY or --yes"
}

read_env_value() {
	local key=$1
	local env_file="${ROOT}/.env"
	[[ -f "${env_file}" ]] || return 0
	grep -E "^${key}=" "${env_file}" 2>/dev/null \
		| head -1 | cut -d= -f2- | tr -d ' "' || true
}

read_env_boolean() {
	local key=$1 default_value=${2:-false} raw
	raw="$(read_env_value "${key}" | tr '[:upper:]' '[:lower:]')"
	[[ -n "${raw}" ]] || raw="${default_value}"
	case "${raw}" in
	1 | true | yes | on) printf 'true' ;;
	0 | false | no | off) printf 'false' ;;
	*) return 2 ;;
	esac
}

resolve_console_host() {
	local frontend_url host
	frontend_url="$(read_env_value FRONTEND_URL)"
	if [[ -n "${frontend_url}" ]]; then
		host="$(python3 - "${frontend_url}" <<'PY'
import sys
from urllib.parse import urlsplit

print(urlsplit(sys.argv[1]).hostname or "")
PY
)"
		if [[ -n "${host}" ]]; then
			printf '%s' "${host}"
			return 0
		fi
	fi
	host="$(hostname -I 2>/dev/null | awk '{print $1}')"
	if [[ -n "${host}" ]]; then
		printf '%s' "${host}"
		return 0
	fi
	printf '%s' "<host>"
}

print_console_access_summary() {
	local env_file="${ROOT}/.env"
	[[ -f "${env_file}" ]] || return 0

	local host seed seed_email seed_pass seed_org sourcelens_mode sourcelens_console_port
	local website_bind website_port tenant_bind tenant_port admin_bind admin_port sourcelens_console_bind
	host="$(resolve_console_host)"
	seed="$(read_env_value SEED_INITIAL_DATA)"
	seed_email="$(read_env_value SEED_ADMIN_EMAIL)"
	seed_pass="$(read_env_value SEED_ADMIN_PASSWORD)"
	seed_org="$(read_env_value SEED_ORG_NAME)"
	sourcelens_mode="$(read_env_value SOURCELENS_MODE | tr 'A-Z' 'a-z')"
	[[ -n "${sourcelens_mode}" ]] || sourcelens_mode="bundled"
	website_bind="$(read_env_value HFL_WEBSITE_BIND_ADDRESS)"
	[[ -n "${website_bind}" ]] || website_bind="0.0.0.0"
	website_port="$(read_env_value HFL_WEBSITE_PORT)"
	[[ -n "${website_port}" ]] || website_port="11442"
	tenant_bind="$(read_env_value HFL_TENANT_BIND_ADDRESS)"
	[[ -n "${tenant_bind}" ]] || tenant_bind="0.0.0.0"
	tenant_port="$(read_env_value HFL_TENANT_PORT)"
	[[ -n "${tenant_port}" ]] || tenant_port="11443"
	admin_bind="$(read_env_value HFL_ADMIN_BIND_ADDRESS)"
	[[ -n "${admin_bind}" ]] || admin_bind="0.0.0.0"
	admin_port="$(read_env_value HFL_ADMIN_PORT)"
	[[ -n "${admin_port}" ]] || admin_port="11444"
	sourcelens_console_bind="$(read_env_value SOURCELENS_CONSOLE_BIND_ADDRESS)"
	[[ -n "${sourcelens_console_bind}" ]] || sourcelens_console_bind="0.0.0.0"
	sourcelens_console_port="$(read_env_value SOURCELENS_CONSOLE_PORT)"
	[[ -n "${sourcelens_console_port}" ]] || sourcelens_console_port="11445"

	log "Website URL: https://${host}:${website_port}/en/ (bind ${website_bind})"
	log "Tenant URL: https://${host}:${tenant_port}/ (bind ${tenant_bind})"
	log "Platform Operations URL: https://${host}:${admin_port}/ (bind ${admin_bind})"
	log "Django Admin URL: https://${host}:${admin_port}/admin/"
	if [[ "${sourcelens_mode}" == "bundled" ]] && { package_has_sourcelens || sourcelens_installed; }; then
		log "SourceLens Console: https://${host}:${sourcelens_console_port}/ (bind ${sourcelens_console_bind})"
		log "SourceLens Gateway API: https://${host}:${tenant_port}/sourcelens/api/"
		log "SourceLens network: ${HFL_BRIDGE_NETWORK} (private)"
	elif [[ "${sourcelens_mode}" == "external" ]]; then
		log "SourceLens mode: external (not managed by HyperFileLens)"
		log "SourceLens base URL: $(read_env_value LENS_BASE_URL)"
	fi
	log "Configuration file: ${env_file}."

	if [[ "${seed}" == "1" ]]; then
		[[ -n "${seed_email}" ]] || seed_email="admin@hyperfilelens.com"
		[[ -n "${seed_pass}" ]] || seed_pass="Admin@123"
		[[ -n "${seed_org}" ]] || seed_org="HyperFileLens"
		local show_credentials=0
		case "${SHOW_GENERATED_CREDENTIALS}" in
		1 | true | yes | on) show_credentials=1 ;;
		0 | false | no | off) show_credentials=0 ;;
		auto) [[ -t 2 ]] && show_credentials=1 || true ;;
		*) die "invalid HFL_SHOW_GENERATED_CREDENTIALS=${SHOW_GENERATED_CREDENTIALS}" ;;
		esac
		if [[ "${show_credentials}" -eq 1 ]]; then
			log "Default admin email: ${seed_email} (environment variable SEED_ADMIN_EMAIL)."
			log "Default admin password: ${seed_pass} (environment variable SEED_ADMIN_PASSWORD)."
		else
			log "Initial admin credentials are stored in ${env_file}; values are hidden in non-interactive logs."
		fi
		log "Default organization: ${seed_org} (environment variable SEED_ORG_NAME)."
		log "Initial seeding is enabled (SEED_INITIAL_DATA=1); the singleton migration job creates this account on first startup."
		log "Change the default password after your first login."
	else
		warn "Initial seeding is disabled (SEED_INITIAL_DATA=${seed:-0}); no default admin account will be created automatically."
		log "To create a seeded admin, set SEED_INITIAL_DATA=1, SEED_ADMIN_EMAIL, and SEED_ADMIN_PASSWORD in ${env_file}, then run: docker compose --profile tools run --rm migration."
	fi
}

package_has_sourcelens() {
	[[ -f "${ROOT}/sourcelens/install.sh" && -f "${ROOT}/sourcelens/docker-compose.yml" ]]
}

package_has_sourcelens_dir() {
	local dir=$1
	[[ -f "${dir}/sourcelens/install.sh" && -f "${dir}/sourcelens/docker-compose.yml" ]]
}

sourcelens_installed() {
	# Release packages stage the SourceLens Compose file before the bundled
	# installer has created its runtime configuration.  Treat that bundle as an
	# installation only after the Compose env symlink (or legacy regular file)
	# exists; otherwise first install would try to stop a stack that has never
	# been configured.
	[[ -f "${SOURCELENS_INSTALL_DIR}/docker-compose.yml" \
		&& -f "${SOURCELENS_INSTALL_DIR}/.env" ]]
}

sourcelens_compose() {
	if ! sourcelens_installed; then
		return 0
	fi
	require_docker
	(
		cd "${SOURCELENS_INSTALL_DIR}"
		docker compose "$@"
	)
}

stop_bundled_sourcelens() {
	if ! sourcelens_installed; then
		return 0
	fi
	step "Stopping SourceLens stack ..."
	sourcelens_compose down
}

remove_sourcelens_images() {
	local manifest="${ROOT}/MANIFEST.json"
	[[ -f "${manifest}" ]] || return 0
	step "Removing SourceLens Docker images ..."
	python3 - "${manifest}" <<'PY'
import json
import subprocess
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    manifest = json.load(fh)
seen = set()
for entry in manifest.get("images", []):
    role = entry.get("role", "")
    if not role.startswith("sourcelens"):
        continue
    for ref in entry.get("refs", []):
        tag = ref.split("@", 1)[0]
        if tag in seen:
            continue
        seen.add(tag)
        print(f"[install.sh] removing SourceLens image {tag}")
        subprocess.run(["docker", "image", "rm", "-f", tag], check=False)
PY
}

purge_sourcelens_data_dir() {
	local data_dir="${ROOT}/data/sourcelens"
	[[ -d "${data_dir}" ]] || return 0
	safe_assert_path_under_dir "${data_dir}" "${ROOT}/data" "SourceLens data path"
	step "Removing SourceLens data/ ..."
	safe_rm_dir "${data_dir}"
	log "Removed SourceLens data/"
}

uninstall_bundled_sourcelens() {
	local purge_data=${1:-0}
	if ! sourcelens_installed; then
		log "SourceLens not installed at ${SOURCELENS_INSTALL_DIR}; skipping"
		return 0
	fi
	step "Uninstalling SourceLens ..."
	sourcelens_compose down || true
	remove_sourcelens_images
	if [[ "${purge_data}" -eq 1 ]]; then
		purge_sourcelens_data_dir
	fi
	log "SourceLens uninstall complete (install dir kept: ${SOURCELENS_INSTALL_DIR})"
}

tree_sha256() {
	local dir=$1
	(
		export LC_ALL=C
		cd "${dir}"
		find . -type f ! -path './__pycache__/*' ! -name '*.pyc' -print0 \
			| sort -z \
			| xargs -0 sha256sum \
			| sha256sum \
			| awk '{print $1}'
	)
}

validate_publish_artifacts() {
	local src_root=$1
	local releases="${src_root}/payload/media/agent-releases"
	local gb="${src_root}/payload/media/gateway-bootstrap"
	local enroll="${src_root}/payload/media/enroll-bootstrap"
	local expected_payload_sha actual_payload_sha
	step "Checking publish artifacts in release package ..."
	[[ -d "${releases}" && -n "$(ls -A "${releases}" 2>/dev/null)" ]] \
		|| die "release package missing agent-releases artifacts"
	[[ -d "${enroll}" && -n "$(ls -A "${enroll}" 2>/dev/null)" ]] \
		|| die "release package missing enroll-bootstrap artifacts"
	expected_payload_sha="$(python3 - "${src_root}/MANIFEST.json" <<'PY'
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print((manifest.get("artifacts") or {}).get("payload_tree_sha256", ""))
PY
)"
	if [[ -n "${expected_payload_sha}" ]]; then
		actual_payload_sha="$(tree_sha256 "${src_root}/payload")"
		[[ "${actual_payload_sha}" == "${expected_payload_sha}" ]] \
			|| die "release package payload sha256 mismatch"
	fi
	if package_has_sourcelens_dir "${src_root}"; then
		local -a required=(
			gateway-bootstrap-linux.sh
			gateway-install-lensnode-sidecar.sh
			gateway-lifecycle.sh
			gateway-install-docker-ubuntu-amd64.sh
			hfl-sentry-sitecustomize.py
			docker-debs-ubuntu2004-amd64.tar.gz
			docker-debs-ubuntu2204-amd64.tar.gz
			docker-debs-ubuntu2404-amd64.tar.gz
			lensnode-image-linux-amd64.tar.gz
		)
		local name
		for name in "${required[@]}"; do
			[[ -f "${gb}/${name}" ]] || die "release package missing gateway-bootstrap/${name}"
		done
	fi
	log "Publish artifact check passed"
}

preflight_sourcelens_bundle() {
	local src_root=$1
	step "Checking SourceLens bundle in upgrade package ..."
	local -a runtime_files=(
		sourcelens/BUILD_INFO.json
		sourcelens/.env.example
		sourcelens/docker-compose.yml
		sourcelens/install.sh
		sourcelens/patch-env-runtime.py
		sourcelens/sync-sentry-runtime.py
		sourcelens/deploy/nginx/default.conf
		sourcelens/deploy/nginx/hfl-sentry-loader.js
		sourcelens/deploy/nginx/hfl-maintenance/run-creation-gate.conf
		sourcelens/deploy/sentry/hfl-sentry-sitecustomize.py
	)
	local rel
	for rel in "${runtime_files[@]}"; do
		[[ -f "${src_root}/${rel}" ]] || die "missing ${rel}"
	done
	local -a images=(
		images/10-sourcelens-app.tar.gz
		images/11-sourcelens-lensnode.tar.gz
		images/12-nginx-stable-alpine.tar.gz
	)
	for rel in "${images[@]}"; do
		[[ -f "${src_root}/${rel}" ]] || die "missing SourceLens image archive ${rel}"
	done
	log "SourceLens bundle check passed"
}

should_upgrade_sourcelens() {
	local mode=$1 src_root=$2
	case "${mode}" in
	0) return 1 ;;
	1)
		if package_has_sourcelens_dir "${src_root}"; then
			return 0
		fi
		warn "SourceLens upgrade requested but upgrade package has no sourcelens/; skipping"
		return 1
		;;
	esac
	[[ "$(configured_sourcelens_mode)" == "bundled" ]] \
		&& package_has_sourcelens_dir "${src_root}" \
		&& sourcelens_bundle_changed "${src_root}"
}

sourcelens_bundle_fingerprint() {
	local root=$1
	python3 - "${root}" <<'PY'
import hashlib
import json
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
paths = [
    "docker-compose.yml",
    ".env.example",
    "install.sh",
    "patch-env-runtime.py",
    "sync-sentry-runtime.py",
]
deploy = root / "deploy"
if deploy.is_dir():
    paths.extend(
        path.relative_to(root).as_posix()
        for path in sorted(deploy.rglob("*"))
        if path.is_file()
        and "certs" not in path.parts
        and path.name != "hfl-sentry-config.js"
    )
digest = hashlib.sha256()

# Registry transit references and rebuilt image IDs can change for every HFL
# tag even while the bundled SourceLens release remains pinned. Only semantic
# SourceLens identity belongs in the bundle fingerprint; runtime files below
# capture HFL-owned integration changes.
build_info_path = root / "BUILD_INFO.json"
if build_info_path.is_file():
    info = json.loads(build_info_path.read_text(encoding="utf-8"))
    identity = {
        "git_url": info.get("git_url", ""),
        "git_ref": info.get("git_ref", ""),
        "git_commit": info.get("git_commit", ""),
        "version": info.get("version", ""),
        "patchset_sha256": info.get(
            "patchset_sha256", info.get("patch_sha256", "")
        ),
        "patches": info.get("patches", []),
        "build_adapter_sha256": info.get("build_adapter_sha256", ""),
        "build_compose_file": info.get("build_compose_file", ""),
        "embed_local_lensnode": info.get("embed_local_lensnode", False),
    }
    digest.update(b"BUILD_INFO.identity\0")
    digest.update(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    )
else:
    digest.update(b"missing:BUILD_INFO.json\0")

for rel in sorted(set(paths)):
    path = root / rel
    if not path.is_file():
        digest.update(f"missing:{rel}\0".encode())
        continue
    digest.update(rel.encode() + b"\0")
    if rel == "docker-compose.yml":
        text = path.read_text(encoding="utf-8")
        text = re.sub(
            r"(?m)^(\s*image:\s*)((?:[^\s\"']*/)?"
            r"hyperfilelens-sourcelens-(?:backend|frontend|lensnode))"
            r"(?::|@)[^\s\"']+\s*$",
            r"\1\2:<distribution-tag>",
            text,
        )
        digest.update(text.encode("utf-8"))
        continue
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
print(digest.hexdigest())
PY
}

sourcelens_bundle_version() {
	local root=$1
	python3 - "${root}/BUILD_INFO.json" <<'PY'
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(1)
version = str(json.loads(path.read_text(encoding="utf-8")).get("version") or "")
if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
    raise SystemExit(1)
print(version)
PY
}

sourcelens_installed_fingerprint_path() {
	printf '%s/data/sourcelens/config/installed-bundle-fingerprint' "${ROOT}"
}

sourcelens_installed_runtime_path() {
	printf '%s/data/sourcelens/config/installed-runtime-images.tsv' "${ROOT}"
}

read_sourcelens_installed_fingerprint() {
	local path
	path="$(sourcelens_installed_fingerprint_path)"
	[[ -f "${path}" ]] || return 0
	tr -d ' \t\r\n' <"${path}"
}

sourcelens_runtime_matches_bundle() {
	local bundle_root=$1 version service container_id image
	version="$(sourcelens_bundle_version "${bundle_root}" 2>/dev/null || true)"
	[[ -n "${version}" ]] || return 1
	for service in api worker scheduler web; do
		# Inspect stopped/restarting containers too. A transient process state is
		# handled by health/recovery gates and is not itself an identity drift.
		container_id="$(sourcelens_compose ps --all -q "${service}" 2>/dev/null | head -1)"
		[[ -n "${container_id}" ]] || return 1
		image="$(docker inspect --format '{{.Config.Image}}' "${container_id}" 2>/dev/null || true)"
		case "${image}" in
		*-sl"${version}" | *-sl"${version}"@*) ;;
		*) return 1 ;;
		esac
	done
	return 0
}

sourcelens_runtime_matches_recorded() {
	local path service expected_image container_id actual_image count=0 seen=' '
	path="$(sourcelens_installed_runtime_path)"
	[[ -f "${path}" ]] || return 1
	while IFS=$'\t' read -r service expected_image; do
		case "${service}" in api | worker | scheduler | web) ;; *) return 1 ;; esac
		[[ -n "${expected_image}" && "${seen}" != *" ${service} "* ]] || return 1
		seen+="${service} "
		count=$((count + 1))
		container_id="$(sourcelens_compose ps --all -q "${service}" 2>/dev/null | head -1)"
		[[ -n "${container_id}" ]] || return 1
		actual_image="$(docker inspect --format '{{.Image}}' "${container_id}" 2>/dev/null || true)"
		[[ "${actual_image}" == "${expected_image}" ]] || return 1
	done <"${path}"
	[[ "${count}" -eq 4 ]]
}

record_sourcelens_installed_bundle() {
	local bundle_root=$1 fingerprint fingerprint_path runtime_path directory
	local fingerprint_tmp runtime_tmp service container_id image_id
	sourcelens_runtime_matches_bundle "${bundle_root}" || return 1
	fingerprint="$(sourcelens_bundle_fingerprint "${bundle_root}")"
	[[ "${fingerprint}" =~ ^[0-9a-f]{64}$ ]] || return 1
	fingerprint_path="$(sourcelens_installed_fingerprint_path)"
	runtime_path="$(sourcelens_installed_runtime_path)"
	directory="$(dirname "${fingerprint_path}")"
	mkdir -p "${directory}"
	fingerprint_tmp="$(mktemp "${directory}/.installed-bundle-fingerprint.XXXXXX")"
	runtime_tmp="$(mktemp "${directory}/.installed-runtime-images.XXXXXX")"
	for service in api worker scheduler web; do
		container_id="$(sourcelens_compose ps --all -q "${service}" 2>/dev/null | head -1)"
		image_id="$(docker inspect --format '{{.Image}}' "${container_id}" 2>/dev/null || true)"
		if [[ -z "${container_id}" || ! "${image_id}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
			rm -f "${fingerprint_tmp}" "${runtime_tmp}"
			return 1
		fi
		printf '%s\t%s\n' "${service}" "${image_id}" >>"${runtime_tmp}"
	done
	printf '%s\n' "${fingerprint}" >"${fingerprint_tmp}"
	chmod 600 "${fingerprint_tmp}" "${runtime_tmp}"
	# The fingerprint is the commit marker: publish exact runtime identities
	# first, then atomically make the semantic bundle record authoritative.
	mv -f "${runtime_tmp}" "${runtime_path}"
	mv -f "${fingerprint_tmp}" "${fingerprint_path}"
	log "Recorded installed SourceLens bundle ${fingerprint:0:12}"
}

sourcelens_bundle_changed() {
	local src_root=$1
	if ! sourcelens_installed || [[ ! -f "${ROOT}/sourcelens/BUILD_INFO.json" ]]; then
		return 0
	fi
	local current target installed previous
	current="$(sourcelens_bundle_fingerprint "${ROOT}/sourcelens")"
	target="$(sourcelens_bundle_fingerprint "${src_root}/sourcelens")"
	installed="$(read_sourcelens_installed_fingerprint)"
	if [[ "${installed}" == "${target}" ]] \
		&& sourcelens_runtime_matches_recorded; then
		log "Bundled SourceLens is unchanged (${target:0:12}); keeping 11445 online"
		return 1
	fi
	if [[ "${installed}" == "${target}" ]]; then
		warn "Bundled SourceLens files match ${target:0:12}, but running containers drifted; reconciling the stack"
	elif [[ -z "${installed}" && "${current}" == "${target}" ]]; then
		warn "Bundled SourceLens target files have no successful runtime record; reconciling the stack"
	else
		previous="${installed:-${current}}"
		log "Bundled SourceLens changed (${previous:0:12} -> ${target:0:12})"
	fi
	return 0
}

should_remove_sourcelens() {
	local flag=$1
	[[ "${flag}" -eq 1 ]] || return 1
	sourcelens_installed
}

configure_lens_bridge_env() {
	local host tenant_port build_info
	host="$(resolve_console_host)"
	tenant_port="$(read_env_value HFL_TENANT_PORT)"
	[[ -n "${tenant_port}" ]] || tenant_port="11443"
	build_info="${ROOT}/sourcelens/BUILD_INFO.json"
	python3 - "${ROOT}/.env" "${host}" "${tenant_port}" "${build_info}" <<'PY'
import json
import pathlib
import re
import sys

env_path = pathlib.Path(sys.argv[1])
host = sys.argv[2]
tenant_port = sys.argv[3]
build_info_path = pathlib.Path(sys.argv[4])
if not env_path.exists():
    raise SystemExit(0)
text = env_path.read_text(encoding="utf-8")

def read_key(name: str, default: str = "") -> str:
    match = re.search(rf"^{re.escape(name)}=(.*)$", text, flags=re.M)
    if not match:
        return default
    return match.group(1).strip().strip('"').strip("'")

frontend = read_key("FRONTEND_URL", "").rstrip("/")
if not frontend:
    frontend = f"https://{host}:{tenant_port}"
no_proxy = [item.strip() for item in read_key("NO_PROXY").split(",") if item.strip()]
if "sourcelens-nginx" not in no_proxy:
    no_proxy.append("sourcelens-nginx")

updates = {
    "LENS_BASE_URL": "http://sourcelens-nginx",
    "LENS_GATEWAY_BASE_URL": f"{frontend}/sourcelens",
    "NO_PROXY": ",".join(no_proxy),
}
if build_info_path.is_file():
    build_info = json.loads(build_info_path.read_text(encoding="utf-8"))
    git_ref = str(build_info.get("git_ref") or "").strip()
    if re.fullmatch(r"v\d+\.\d+\.\d+", git_ref):
        updates["SOURCELENS_GIT_REF"] = git_ref

def set_key(name: str, value: str) -> None:
    global text
    pattern = rf"^{re.escape(name)}=.*$"
    replacement = f"{name}={value}"
    if re.search(pattern, text, flags=re.M):
        text = re.sub(pattern, replacement, text, count=1, flags=re.M)
    else:
        text = text.rstrip() + f"\n{replacement}\n"

for key, value in updates.items():
    set_key(key, value)
env_path.write_text(text, encoding="utf-8")
print(f"[install.sh] configured {', '.join(updates.keys())} for bundled SourceLens")
PY
}

install_bundled_sourcelens() {
	local script="${ROOT}/sourcelens/install.sh"
	local console_bind console_port
	[[ -f "${script}" ]] || die "missing bundled SourceLens installer: ${script}"
	console_bind="$(read_env_value SOURCELENS_CONSOLE_BIND_ADDRESS)"
	[[ -n "${console_bind}" ]] || console_bind="0.0.0.0"
	console_port="$(read_env_value SOURCELENS_CONSOLE_PORT)"
	[[ -n "${console_port}" ]] || console_port="11445"
	stop_bundled_sourcelens
	step "Installing bundled SourceLens ..."
	SOURCELENS_INSTALL_DIR="${ROOT}/sourcelens" \
		SOURCELENS_DATA_DIR="${ROOT}/data/sourcelens" \
		SOURCELENS_CONFIG_DIR="${ROOT}/data/sourcelens/config" \
		HFL_PARENT_ENV_FILE="${ROOT}/.env" \
		SOURCELENS_TLS_CERT_DIR="${ROOT}/deploy/nginx/certs" \
		SOURCELENS_CONSOLE_BIND_ADDRESS="${console_bind}" \
		SOURCELENS_CONSOLE_PORT="${console_port}" \
		SOURCELENS_NGINX_HTTPS_PORT="${console_port}" \
		bash "${script}" install --skip-image-load
}

should_install_sourcelens() {
	local mode=$1
	case "${mode}" in
	0) return 1 ;;
	1) return 0 ;;
	esac
	[[ "$(configured_sourcelens_mode)" == "bundled" ]] && package_has_sourcelens
}

configured_sourcelens_mode() {
	local mode
	mode="$(read_env_value SOURCELENS_MODE | tr 'A-Z' 'a-z')"
	[[ -n "${mode}" ]] || mode="bundled"
	case "${mode}" in
	bundled | external) printf '%s' "${mode}" ;;
	*) die "invalid SOURCELENS_MODE=${mode} (use bundled or external)" ;;
	esac
}

platform_gateway_auto_deploy_enabled() {
	local enabled
	enabled="$(read_env_boolean HFL_PLATFORM_GATEWAY_AUTO_DEPLOY true)" \
		|| die "invalid HFL_PLATFORM_GATEWAY_AUTO_DEPLOY value (use true or false)"
	[[ "${enabled}" == "true" ]]
}

check_local_platform_gateway_continuity() {
	if ! platform_gateway_auto_deploy_enabled; then
		return 0
	fi
	if [[ ! -f "${LOCAL_PLATFORM_AGENT_DATA_DIR}/agent.env" ]]; then
		skip "Installer-managed platform Gateway is not installed; bootstrap follows the control-plane upgrade"
		return 0
	fi
	# A control-plane release must not mutate independently running Gateway,
	# Agent, or LensNode workloads. Wait only for their existing control channels
	# to recover; their upgrade remains a separate lifecycle operation.
	if wait_for_local_platform_gateway_readiness 180; then
		ok "Installer-managed platform Gateway recovered after the control-plane upgrade"
	else
		warn "Installer-managed platform Gateway is not ready after the control-plane upgrade: ${LOCAL_PLATFORM_GATEWAY_READINESS_REASON:-unknown reason}"
	fi
}

read_agent_env_value() {
	local key=$1 env_file="${LOCAL_PLATFORM_AGENT_DATA_DIR}/agent.env"
	[[ -f "${env_file}" ]] || return 0
	grep -E "^${key}=" "${env_file}" 2>/dev/null \
		| head -1 | cut -d= -f2- | tr -d '\r' || true
}

local_platform_gateway_readiness_once() {
	LOCAL_PLATFORM_GATEWAY_READINESS_REASON=""
	if ! run_as_root systemctl is-active --quiet hyperfilelens-agent.service; then
		LOCAL_PLATFORM_GATEWAY_READINESS_REASON="Agent service is not active"
		return 1
	fi

	local node_id container_id running api_service query
	node_id="$(read_agent_env_value HFL_NODE_ID)"
	if [[ ! "${node_id}" =~ ^[0-9]+$ ]]; then
		LOCAL_PLATFORM_GATEWAY_READINESS_REASON="managed Agent node ID is missing or invalid"
		return 1
	fi
	container_id="$(docker ps -aq --no-trunc \
		--filter 'label=com.hyperfilelens.managed=true' \
		--filter 'label=com.hyperfilelens.component=gateway-lensnode' \
		--filter 'label=com.docker.compose.project=hyperfilelens-gateway' \
		--filter 'label=com.docker.compose.service=lensnode' | head -1)"
	if [[ -z "${container_id}" ]]; then
		LOCAL_PLATFORM_GATEWAY_READINESS_REASON="managed LensNode container is missing"
		return 1
	fi
	running="$(docker inspect --format '{{.State.Running}}' "${container_id}" 2>/dev/null || true)"
	if [[ "${running}" != "true" ]]; then
		LOCAL_PLATFORM_GATEWAY_READINESS_REASON="managed LensNode container is not running"
		return 1
	fi
	api_service="$(active_api_service 2>/dev/null)" || {
		LOCAL_PLATFORM_GATEWAY_READINESS_REASON="active HFL API color is unavailable"
		return 1
	}
	query="from apps.lens_bridge.models import LensGatewayLink; from apps.lens_bridge.services.gateway_readiness import gateway_runtime_state; from apps.lens_bridge.services.provisioning import sync_gateway_lensnode_status; link = LensGatewayLink.objects.select_related('gateway').filter(gateway_id=${node_id}, scope='platform').first(); link = sync_gateway_lensnode_status(link) if link is not None else None; state = gateway_runtime_state(link); raise SystemExit(0 if link is not None and state['hfl_usable'] and state['copilot_eligible'] else 1)"
	if ! compose_in_root exec -T "${api_service}" python manage.py shell -c "${query}" \
		>/dev/null 2>&1; then
		LOCAL_PLATFORM_GATEWAY_READINESS_REASON="managed platform Gateway link, Agent WebSocket, or LensNode sidecar is not online and usable"
		return 1
	fi
	return 0
}

wait_for_local_platform_gateway_readiness() {
	local timeout_seconds=${1:-180} deadline
	if [[ ! "${timeout_seconds}" =~ ^(0|[1-9][0-9]*)$ ]] \
		|| ((timeout_seconds > 900)); then
		LOCAL_PLATFORM_GATEWAY_READINESS_REASON="readiness timeout must be between 0 and 900 seconds"
		return 2
	fi
	deadline=$((SECONDS + timeout_seconds))
	while true; do
		if local_platform_gateway_readiness_once; then
			ok "Installer-managed platform Gateway is online and usable"
			return 0
		fi
		if ((SECONDS >= deadline)); then
			return 1
		fi
		sleep 2
	done
}

local_platform_gateway_installed_agent_version() {
	local version_file="${LOCAL_PLATFORM_AGENT_INSTALL_DIR}/INSTALLED_VERSION"
	[[ -f "${version_file}" ]] || return 0
	tr -d ' \t\r\n' <"${version_file}"
}

local_platform_gateway_agent_archive() {
	local version=$1 release_dir="${ROOT}/data/media/agent-releases/${1}"
	local ubuntu_release="" candidate
	local -a candidates=()
	if [[ -f /etc/os-release ]]; then
		ubuntu_release="$(awk -F= '$1 == "VERSION_ID" {gsub(/"/, "", $2); print $2; exit}' /etc/os-release)"
	fi
	case "${ubuntu_release}" in
	20.04) ubuntu_release=ubuntu2004 ;;
	22.04) ubuntu_release=ubuntu2204 ;;
	24.04) ubuntu_release=ubuntu2404 ;;
	*) ubuntu_release="" ;;
	esac
	if [[ -n "${ubuntu_release}" ]]; then
		candidates+=("${release_dir}/hfl-agent-${version}-linux-amd64-${ubuntu_release}.tar.gz")
	fi
	candidates+=("${release_dir}/hfl-agent-${version}-linux-amd64.tar.gz")
	for candidate in "${candidates[@]}"; do
		if [[ -f "${candidate}" && ! -L "${candidate}" ]]; then
			printf '%s' "${candidate}"
			return 0
		fi
	done
	return 1
}

upgrade_local_platform_gateway_agent() {
	local desired=$1 archive install_script marker
	archive="$(local_platform_gateway_agent_archive "${desired}")" \
		|| die "no exact local platform Gateway Agent archive exists for ${desired}"
	install_script="${LOCAL_PLATFORM_AGENT_INSTALL_DIR}/install.sh"
	marker="${ROOT}/data/.platform-gateway-agent-upgrade"
	[[ -f "${install_script}" && ! -L "${install_script}" ]] \
		|| die "installer-managed local platform Gateway has no trusted Agent installer"

	step "Upgrading installer-managed local platform Gateway Agent to ${desired}"
	printf '%s\n' "${desired}" | run_as_root tee "${marker}" >/dev/null
	if ! run_as_root /bin/bash "${install_script}" upgrade \
		--from "${archive}" --yes --quiet-footer; then
		die "installer-managed local platform Gateway Agent upgrade failed; its rollback was requested"
	fi
	if [[ "$(local_platform_gateway_installed_agent_version)" != "${desired}" ]]; then
		die "installer-managed local platform Gateway Agent did not converge to ${desired}"
	fi
	run_as_root rm -f "${marker}"
	ok "Installer-managed local platform Gateway Agent upgraded to ${desired}"
}

verify_local_platform_gateway_agent() {
	local desired=$1 installed
	installed="$(local_platform_gateway_installed_agent_version)"
	[[ "${installed}" == "${desired}" ]] \
		|| die "installer-managed local platform Gateway Agent is ${installed:-unknown}, expected ${desired}"
	run_as_root systemctl is-active --quiet hyperfilelens-agent.service \
		|| die "installer-managed local platform Gateway Agent service is not active"
}

converge_local_platform_gateway_lensnode() {
	local desired_id current_id container_id running script
	desired_id="$(docker image inspect --format '{{.Id}}' \
		"${LOCAL_PLATFORM_LENSNODE_IMAGE}" 2>/dev/null || true)"
	[[ -n "${desired_id}" ]] \
		|| die "local platform Gateway LensNode image is unavailable: ${LOCAL_PLATFORM_LENSNODE_IMAGE}"
	container_id="$(docker ps -aq --no-trunc \
		--filter 'label=com.hyperfilelens.managed=true' \
		--filter 'label=com.hyperfilelens.component=gateway-lensnode' \
		--filter 'label=com.docker.compose.project=hyperfilelens-gateway' \
		--filter 'label=com.docker.compose.service=lensnode' | head -1)"
	[[ -n "${container_id}" ]] \
		|| die "installer-managed local platform Gateway LensNode container is missing"
	current_id="$(docker inspect --format '{{.Image}}' "${container_id}" 2>/dev/null || true)"
	if [[ "${current_id}" == "${desired_id}" ]]; then
		skip "Local platform Gateway LensNode already uses the desired image"
	else
		script="${ROOT}/data/media/gateway-bootstrap/gateway-install-lensnode-sidecar.sh"
		[[ -f "${script}" && ! -L "${script}" ]] \
			|| die "local platform Gateway LensNode installer is missing: ${script}"
		step "Recreating local platform Gateway LensNode for a changed image"
		run_as_root env \
			HFL_LENS_ENV_FILE="${LOCAL_PLATFORM_LENSNODE_ENV_FILE}" \
			HFL_INSECURE_TLS=1 \
			LENSNODE_IMAGE="${LOCAL_PLATFORM_LENSNODE_IMAGE}" \
			/bin/bash "${script}"
		container_id="$(docker ps -aq --no-trunc \
			--filter 'label=com.hyperfilelens.managed=true' \
			--filter 'label=com.hyperfilelens.component=gateway-lensnode' \
			--filter 'label=com.docker.compose.project=hyperfilelens-gateway' \
			--filter 'label=com.docker.compose.service=lensnode' | head -1)"
		current_id="$(docker inspect --format '{{.Image}}' "${container_id}" 2>/dev/null || true)"
		[[ "${current_id}" == "${desired_id}" ]] \
			|| die "local platform Gateway LensNode did not converge to the desired image"
	fi
	running="$(docker inspect --format '{{.State.Running}}' "${container_id}" 2>/dev/null || true)"
	[[ "${running}" == "true" ]] \
		|| die "installer-managed local platform Gateway LensNode is not running"
}

ensure_local_platform_gateway() {
	if ! platform_gateway_auto_deploy_enabled; then
		skip "Local platform Gateway auto-deploy is disabled"
		return 0
	fi

	require_root_or_sudo
	require_docker
	[[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" ]] \
		|| die "local platform Gateway auto-deploy requires Linux amd64/x86_64"
	local helper="${ROOT}/data/media/enroll-bootstrap/hfl-enroll-linux-amd64"
	[[ -x "${helper}" ]] \
		|| die "local platform Gateway enrollment helper is missing: ${helper}"

	step "Ensuring installer-managed local platform Gateway"
	local command_output parsed org_key token api_base wss_url managed_node_ids
	command_output="$(
		compose_in_root exec -T "$(active_api_service)" \
			python manage.py ensure_local_platform_gateway_enrollment
	)" || die "failed to issue local platform Gateway enrollment credentials"
	parsed="$(
		printf '%s\n' "${command_output}" | python3 -c '
import json
import sys

prefix = "HFL_LOCAL_PLATFORM_GATEWAY_ENROLLMENT="
matches = [line[len(prefix):] for line in sys.stdin.read().splitlines() if line.startswith(prefix)]
if len(matches) != 1:
    raise SystemExit("expected one local platform Gateway enrollment payload")
payload = json.loads(matches[0])
required = ("org_key", "token", "api_base", "wss_url")
if any(not str(payload.get(key, "")).strip() for key in required):
    raise SystemExit("local platform Gateway enrollment payload is incomplete")
node_ids = ",".join(str(value) for value in payload.get("managed_node_ids", []))
print("\t".join([*(str(payload[key]).strip() for key in required), node_ids]))
'
	)" || die "failed to parse local platform Gateway enrollment credentials"
	IFS=$'\t' read -r org_key token api_base wss_url managed_node_ids <<<"${parsed}"
	[[ "${org_key}" == "__platform_lens__" ]] \
		|| die "local platform Gateway enrollment returned an unexpected organization"

	local tenant_port
	tenant_port="$(read_env_value HFL_TENANT_PORT)"
	[[ -n "${tenant_port}" ]] || tenant_port=11443
	[[ "${tenant_port}" =~ ^[0-9]+$ ]] && ((tenant_port >= 1 && tenant_port <= 65535)) \
		|| die "invalid HFL_TENANT_PORT for local platform Gateway: ${tenant_port}"
	# This installer owns both endpoints on the same host. Keep its Agent off
	# public/NAT paths and scope the TLS exception to this local managed Gateway.
	api_base="https://127.0.0.1:${tenant_port}"
	wss_url="wss://127.0.0.1:${tenant_port}/ws/node/agent/"

	local existing_org existing_role existing_node_id existing_token desired_version installed_version
	existing_org="$(read_agent_env_value HFL_ORG_KEY)"
	existing_role="$(read_agent_env_value HFL_NODE_ROLE)"
	existing_node_id="$(read_agent_env_value HFL_NODE_ID)"
	existing_token="$(read_agent_env_value HFL_NODE_TOKEN)"
	if [[ -n "${existing_org}${existing_role}${existing_node_id}${existing_token}" ]]; then
		[[ "${existing_org}" == "${org_key}" && "${existing_role}" == "gateway" ]] \
			|| die "an existing HFL Agent on this host conflicts with local platform Gateway auto-deploy"
		if [[ -n "${existing_node_id}" ]]; then
			case ",${managed_node_ids}," in
			*",${existing_node_id},"*) ;;
			*) die "the existing platform Gateway Agent is not managed by the HFL installer" ;;
			esac
		elif [[ "${existing_token}" != "${token}" ]]; then
			die "the partially enrolled platform Gateway Agent was not created by the HFL installer"
		fi
		desired_version="$(read_version)"
		installed_version="$(local_platform_gateway_installed_agent_version)"
		if [[ "${installed_version}" == "${desired_version}" ]]; then
			skip "Local platform Gateway Agent already uses ${desired_version}"
		else
			upgrade_local_platform_gateway_agent "${desired_version}"
		fi
	fi

	run_as_root env \
		-u SENTRY_ENABLED \
		-u SENTRY_BACKEND_DSN \
		-u SENTRY_ENVIRONMENT \
		-u SENTRY_RELEASE \
		-u SENTRY_TRACES_SAMPLE_RATE \
		-u HFL_SENTRY_LENSNODE_RELEASE \
		-u HFL_SENTRY_POLICY_MANAGED \
		HFL_ORG_KEY="${org_key}" \
		HFL_NODE_ROLE="gateway" \
		HFL_NODE_TOKEN="${token}" \
		HFL_API_BASE="${api_base}" \
		HFL_WSS_URL="${wss_url}" \
		HFL_INSECURE_TLS=1 \
		HFL_FORCE_SIDECAR_INSTALL=1 \
		"${helper}" gateway-install --yes
	desired_version="$(read_version)"
	verify_local_platform_gateway_agent "${desired_version}"
	converge_local_platform_gateway_lensnode
	if ! wait_for_local_platform_gateway_readiness 180; then
		die "installer-managed local platform Gateway readiness failed: ${LOCAL_PLATFORM_GATEWAY_READINESS_REASON:-unknown reason}"
	fi
}

sync_optional_identity_settings() {
	step "Synchronizing optional identity and email settings"
	local output command_status
	set +e
	output="$(
		compose_in_root exec -T "$(active_api_service)" \
			python manage.py ensure_deployment_identity_settings 2>&1
	)"
	command_status=$?
	set -e
	if [[ -n "${output}" ]]; then
		printf '%s\n' "${output}"
	fi
	if [[ "${command_status}" -ne 0 ]]; then
		warn "Optional identity or email settings could not be synchronized; core services remain available"
		return 0
	fi
	if grep -F 'HFL_IDENTITY_STATUS=warning' <<<"${output}" >/dev/null; then
		warn "Invalid optional identity or email settings were preserved"
	else
		ok "Optional identity and email settings synchronized"
	fi

	set +e
	output="$(
		compose_in_root exec -T "$(active_api_service)" \
			python manage.py check_google_oauth_readiness 2>&1
	)"
	command_status=$?
	set -e
	if [[ -n "${output}" ]]; then
		printf '%s\n' "${output}"
	fi
	if [[ "${command_status}" -ne 0 ]]; then
		warn "Google OAuth local route or generated callback is not ready; core services remain available"
	fi
}

# --- Commands ---

cmd_install() {
	local sourcelens_mode=-1 allow_main_build=0
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--with-sourcelens) sourcelens_mode=1 ;;
		--hfl-only) sourcelens_mode=0 ;;
		--direct-host) [[ $# -ge 2 && -n "${2:-}" ]] || die "--direct-host requires a value" 2; PUBLIC_HOST="$2"; shift ;;
		--public-url) [[ $# -ge 2 ]] || die "--public-url requires a value" 2; PUBLIC_URL="$2"; shift ;;
		--admin-public-url) [[ $# -ge 2 ]] || die "--admin-public-url requires a value" 2; ADMIN_PUBLIC_URL="$2"; shift ;;
		--runtime-env-file) [[ $# -ge 2 && -n "${2:-}" ]] || die "--runtime-env-file requires a path" 2; RUNTIME_ENV_FILE="$2"; shift ;;
		--allow-main-build) allow_main_build=1 ;;
		*) die "unknown install option: $1" 2 ;;
		esac
		shift
	done

	init_install_root
	local version
	version="$(read_version)"
	if [[ "$(read_channel_from_dir "${ROOT}")" == "main" && "${allow_main_build}" -ne 1 ]]; then
		die "main channel packages require --allow-main-build"
	fi
	log "======== HyperFileLens install ${version} ========"
	log "Install dir: ${ROOT}"

	preflight_package_layout
	validate_publish_artifacts "${ROOT}"
	if package_has_sourcelens; then
		preflight_sourcelens_bundle "${ROOT}"
	fi

	if [[ -f "${ROOT}/.env" ]] && stack_containers_present; then
		log "app containers already running under ${ROOT}; skipping duplicate install"
		log "To upgrade run: sudo ${ROOT}/install.sh upgrade --from <package.tar.gz>"
		print_console_access_summary
		return 0
	fi

	step "[1/6] Checking host capacity, ports, and Docker ..."
	preflight_install_capacity
	ensure_host_docker "${ROOT}"

	step "[2/6] Preparing config and directories ..."
	require_docker
	ensure_bridge_network
	ensure_env_file
	apply_runtime_configuration
	ensure_tls_certs
	ensure_data_dirs
	sync_runtime_media

	step "[3/6] Loading container images ..."
	load_images_from_manifest "$([[ "${sourcelens_mode}" -eq 0 ]] && echo 1 || echo 0)"

	step "[4/6] Post-install checks ..."
	log "Version: $(read_version)"
	log "Docker: $(docker_engine_version) / compose $(docker_compose_version)"

	if should_install_sourcelens "${sourcelens_mode}"; then
		install_bundled_sourcelens
	fi

	if [[ "$(configured_sourcelens_mode)" == "bundled" ]] && sourcelens_installed; then
		configure_lens_bridge_env
	fi

	step "[5/6] Starting services ..."
	log "Log rotation: built into nginx container (hourly; daily or 500M; keep 30)"
	start_hfl_stack || die "HyperFileLens active color failed to start"
	wait_for_hfl_health || die "HyperFileLens failed its post-install health gate"
	if [[ "${sourcelens_mode}" -eq 0 ]]; then
		skip "Bundled SourceLens health gate skipped by --hfl-only"
	else
		wait_for_sourcelens_health || die "bundled SourceLens failed its post-install health gate"
		if [[ "$(configured_sourcelens_mode)" == "bundled" ]] && sourcelens_installed; then
			record_sourcelens_installed_bundle "${ROOT}/sourcelens" \
				|| die "could not record the installed SourceLens bundle identity"
		fi
	fi
	sync_optional_identity_settings
	ensure_local_platform_gateway
	prune_agent_release_media

	step "[6/6] Done"
	log "Install and startup complete"
	compose_all_profiles ps
	print_console_access_summary
}

cmd_platform_gateway() {
	local action=${1:-} timeout_seconds=180 required=0
	[[ -n "${action}" ]] || die "usage: install.sh platform-gateway {ensure|verify}" 2
	shift
	init_install_root
	require_docker
	[[ -f "${ROOT}/.env" ]] || die "missing .env; run install first"
	case "${action}" in
	ensure)
		[[ $# -eq 0 ]] || die "usage: install.sh platform-gateway ensure" 2
		wait_for_hfl_health || die "HyperFileLens failed its platform Gateway health gate"
		wait_for_sourcelens_health || die "bundled SourceLens failed its platform Gateway health gate"
		ensure_local_platform_gateway
		;;
	verify)
		require_root_or_sudo
		while [[ $# -gt 0 ]]; do
			case "$1" in
			--timeout)
				shift
				timeout_seconds=${1:-}
				[[ -n "${timeout_seconds}" && "${timeout_seconds:0:1}" != "-" ]] \
					|| die "--timeout requires seconds" 2
				;;
			--required) required=1 ;;
			*) die "unknown platform-gateway verify option: $1" 2 ;;
			esac
			shift
		done
		if ! platform_gateway_auto_deploy_enabled; then
			if [[ "${required}" -eq 1 ]]; then
				die "local platform Gateway is required but auto-deploy is disabled"
			fi
			skip "Local platform Gateway auto-deploy is disabled"
			return 0
		fi
		if ! wait_for_local_platform_gateway_readiness "${timeout_seconds}"; then
			die "installer-managed local platform Gateway readiness failed: ${LOCAL_PLATFORM_GATEWAY_READINESS_REASON:-unknown reason}"
		fi
		;;
	*) die "usage: install.sh platform-gateway {ensure|verify}" 2 ;;
	esac
}

cmd_start() {
	init_install_root
	require_docker
	[[ -f "${ROOT}/.env" ]] || die "missing .env; run install first"
	ensure_bridge_network
	ensure_data_dirs
	sync_runtime_media
	if [[ "$(configured_sourcelens_mode)" == "bundled" ]] && sourcelens_installed; then
		step "Starting bundled SourceLens ..."
		sourcelens_compose up -d --no-build --pull never --remove-orphans
	fi
	step "Starting services (docker compose up -d --no-build) ..."
	start_hfl_stack || die "HyperFileLens active color failed to start"
	wait_for_hfl_health || die "HyperFileLens failed its startup health gate"
	wait_for_sourcelens_health || die "bundled SourceLens failed its startup health gate"
	sync_optional_identity_settings
	log "Services started"
	compose_all_profiles ps
}

cmd_stop() {
	init_install_root
	require_docker
	compose_in_root stop scheduler worker || true
	step "Stopping services (docker compose down) ..."
	compose_all_profiles down
	if [[ "$(configured_sourcelens_mode)" == "bundled" ]] && sourcelens_installed; then
		stop_bundled_sourcelens
	fi
	log "Services stopped"
}

cmd_restart() {
	cmd_stop
	cmd_start
}

cmd_status() {
	init_install_root
	local version
	version="$(read_version)"
	printf 'Version: %s\n' "${version}"
	printf 'Install dir: %s\n' "${ROOT}"
	local active_color deployment_phase
	if active_color="$(read_active_color)"; then
		printf 'Active color: %s\n' "${active_color}"
	else
		printf 'Active color: legacy/unset\n'
	fi
	deployment_phase="$(grep -E '^phase=' "$(blue_green_state_dir)/deployment-state" 2>/dev/null | head -1 | cut -d= -f2- || true)"
	printf 'Deployment phase: %s\n' "${deployment_phase:-unknown}"
	if sourcelens_installed; then
		printf 'SourceLens: installed at %s (network %s)\n' \
			"${SOURCELENS_INSTALL_DIR}" "${HFL_BRIDGE_NETWORK}"
	else
		printf 'SourceLens: not installed\n'
	fi
	if [[ -f "${ROOT}/data/media/gateway-bootstrap/lensnode-image-linux-amd64.tar.gz" ]]; then
		printf 'gateway-bootstrap: lensnode image bundle present\n'
	else
		printf 'gateway-bootstrap: lensnode image bundle missing\n'
	fi
	if [[ -d "${ROOT}/data/media/agent-releases" ]]; then
		local versions
		versions="$(find "${ROOT}/data/media/agent-releases" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort -V | tr '\n' ' ')"
		printf 'agent-releases: %s\n' "${versions:-none}"
	fi
	if [[ -f "${ROOT}/.env" ]]; then
		require_docker
		compose_all_profiles ps
		print_console_access_summary
	else
		warn "missing .env; install has not been run"
	fi
}

cmd_manage() {
	[[ $# -ge 1 ]] || die "manage requires a Django management command" 2
	init_existing_install_root
	require_docker
	[[ -f "${ROOT}/.env" ]] || die "missing .env; run install first"
	local color api_service
	if color="$(read_active_color)"; then
		api_service="api-${color}"
		compose_in_root --profile "${color}" exec -T "${api_service}" \
			python manage.py "$@"
		return
	fi
	# Compatibility for installations created before the blue/green topology.
	compose_in_root exec -T api python manage.py "$@"
}

init_language_pack_root() {
	local script_dir repository_root
	script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
	repository_root="$(cd "${script_dir}/../.." 2>/dev/null && pwd || true)"

	if [[ -f "${script_dir}/docker-compose.yml" ]]; then
		ROOT="${script_dir}"
		LANG_PACK_COMPOSE_FILE="docker-compose.yml"
	elif [[ -n "${repository_root}" && -f "${repository_root}/docker-compose.yml" ]]; then
		ROOT="${repository_root}"
		LANG_PACK_COMPOSE_FILE="docker-compose.yml"
	elif [[ -f "${INSTALL_DIR}/docker-compose.yml" ]]; then
		ROOT="${INSTALL_DIR}"
		LANG_PACK_COMPOSE_FILE="docker-compose.yml"
	else
		die "cannot find a HyperFileLens root for language-pack management"
	fi
	acquire_installation_lock
}

read_language_pack_app_version() {
	python3 - "${ROOT}" <<'PY'
from __future__ import annotations

import json
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
version_path = root / "VERSION"
manifest_path = root / "MANIFEST.json"
pyproject_path = root / "pyproject.toml"

if version_path.is_file():
    print(version_path.read_text(encoding="utf-8").strip())
elif manifest_path.is_file():
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("channel") == "main":
        print(str(manifest["artifact_id"]).strip())
    else:
        print(str(manifest["version"]).strip())
elif pyproject_path.is_file():
    text = pyproject_path.read_text(encoding="utf-8")
    project_match = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", text)
    if project_match is None:
        raise SystemExit("pyproject.toml has no [project] table")
    version_match = re.search(
        r'(?m)^\s*version\s*=\s*["\']([^"\']+)["\']\s*$',
        project_match.group(1),
    )
    if version_match is None:
        raise SystemExit("pyproject.toml [project] has no static version")
    print(version_match.group(1).strip())
else:
    raise SystemExit("cannot determine the HyperFileLens application version")
PY
}

validate_and_extract_language_pack() {
	local archive=$1 destination=$2 app_version=$3
	python3 - "${archive}" "${destination}" "${app_version}" <<'PY'
from __future__ import annotations

import json
import pathlib
import re
import sys
import tarfile
from typing import Any

archive = pathlib.Path(sys.argv[1]).resolve()
destination = pathlib.Path(sys.argv[2]).resolve()
app_version = sys.argv[3]
pack_id_pattern = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
language_code_pattern = re.compile(
    r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$",
    re.IGNORECASE,
)
semver_pattern = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")


def fail(message: str) -> None:
    """Stop validation with a concise package error."""
    raise SystemExit(f"invalid language pack: {message}")


def required_string(manifest: dict[str, Any], field: str) -> str:
    """Read a required, non-empty manifest string."""
    value = manifest.get(field)
    if not isinstance(value, str) or not value.strip():
        fail(f"{field!r} must be a non-empty string")
    return value.strip()


def parse_version(value: str) -> tuple[int, int, int]:
    """Parse the supported semantic-version core."""
    match = semver_pattern.fullmatch(value)
    if match is None:
        fail(f"unsupported semantic version {value!r}")
    return tuple(int(part) for part in match.groups())


def check_compatibility(specifier: str, current_version: str) -> None:
    """Check a comma-separated set of simple semantic-version constraints."""
    current = parse_version(current_version)
    operators = {
        ">=": lambda left, right: left >= right,
        "<=": lambda left, right: left <= right,
        ">": lambda left, right: left > right,
        "<": lambda left, right: left < right,
        "==": lambda left, right: left == right,
    }
    clauses = [clause.strip() for clause in specifier.split(",") if clause.strip()]
    if not clauses:
        fail("'compatible_app' must contain at least one version constraint")
    for clause in clauses:
        match = re.fullmatch(r"(>=|<=|==|>|<)\s*(.+)", clause)
        if match is None:
            fail(f"unsupported compatible_app constraint {clause!r}")
        operator, expected_text = match.groups()
        expected = parse_version(expected_text)
        if not operators[operator](current, expected):
            fail(
                f"application {current_version} does not satisfy "
                f"compatible_app {specifier!r}"
            )


def django_locale_name(language_code: str) -> str:
    """Convert a Django language code to its gettext locale directory name."""
    language, separator, territory = language_code.lower().partition("-")
    if not separator:
        return language
    normalized_territory = territory.title() if len(territory) > 2 else territory.upper()
    return f"{language}_{normalized_territory}"


if not archive.is_file():
    fail(f"archive not found: {archive}")
destination.mkdir(parents=True, exist_ok=True)

with tarfile.open(archive, mode="r:*") as package:
    members = package.getmembers()
    if not members:
        fail("archive is empty")
    if len(members) > 10_000:
        fail("archive contains too many entries")
    total_size = 0
    for member in members:
        member_path = pathlib.PurePosixPath(member.name)
        if member_path.is_absolute() or ".." in member_path.parts:
            fail(f"unsafe archive path: {member.name!r}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            fail(f"unsupported archive entry: {member.name!r}")
        total_size += member.size
    if total_size > 100 * 1024 * 1024:
        fail("archive expands beyond the 100 MiB limit")
    for member in members:
        package.extract(member, destination)

manifest_candidates = sorted(destination.glob("manifest.json"))
manifest_candidates.extend(sorted(destination.glob("*/manifest.json")))
if len(manifest_candidates) != 1:
    fail("archive must contain exactly one manifest.json at its root or first level")

manifest_path = manifest_candidates[0]
pack_root = manifest_path.parent.resolve()
if destination not in pack_root.parents and pack_root != destination:
    fail("manifest escaped the extraction directory")

try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    fail(f"cannot read manifest.json: {exc}")
if not isinstance(manifest, dict):
    fail("manifest.json must contain an object")
if manifest.get("schema") != 1:
    fail("'schema' must be 1")

pack_id = required_string(manifest, "id")
if pack_id_pattern.fullmatch(pack_id) is None:
    fail("'id' must use lowercase letters, digits, and hyphens")
required_string(manifest, "display_name")
parse_version(required_string(manifest, "version"))
compatible_app = required_string(manifest, "compatible_app")
frontend_code = required_string(manifest, "frontend_code")
backend_code = required_string(manifest, "backend_code")
if language_code_pattern.fullmatch(frontend_code) is None:
    fail("'frontend_code' is invalid")
if language_code_pattern.fullmatch(backend_code) is None:
    fail("'backend_code' is invalid")
if frontend_code == "en" or backend_code == "en":
    fail("optional packs cannot replace the built-in English locale")

aliases = manifest.get("aliases", [])
if not isinstance(aliases, list) or not all(
    isinstance(alias, str)
    and alias == alias.lower()
    and language_code_pattern.fullmatch(alias)
    for alias in aliases
):
    fail("'aliases' must be an array of valid language codes")

element_plus_locale = manifest.get("element_plus_locale")
if element_plus_locale is not None and (
    not isinstance(element_plus_locale, str)
    or re.fullmatch(r"[A-Za-z0-9_-]+", element_plus_locale) is None
):
    fail("'element_plus_locale' is invalid")

frontend_messages = pack_root / "frontend" / "messages.json"
backend_messages = (
    pack_root
    / "backend"
    / "locale"
    / django_locale_name(backend_code)
    / "LC_MESSAGES"
    / "django.mo"
)
if not frontend_messages.is_file():
    fail("frontend/messages.json is required")
try:
    message_catalog = json.loads(frontend_messages.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    fail(f"cannot read frontend/messages.json: {exc}")
if not isinstance(message_catalog, dict):
    fail("frontend/messages.json must contain an object")
if not backend_messages.is_file() or backend_messages.stat().st_size == 0:
    fail(f"compiled backend catalog is required: {backend_messages.relative_to(pack_root)}")

allowed_files = {
    pathlib.Path("manifest.json"),
    pathlib.Path("frontend/messages.json"),
    backend_messages.relative_to(pack_root),
}
installed_files = {
    path.relative_to(pack_root) for path in pack_root.rglob("*") if path.is_file()
}
unexpected_files = sorted(installed_files - allowed_files)
if unexpected_files:
    fail(
        "unsupported files in runtime package: "
        + ", ".join(str(path) for path in unexpected_files)
    )

check_compatibility(compatible_app, app_version)
print(pack_id)
print(pack_root)
PY
}

refresh_language_pack_index() {
	local language_root=$1
	python3 - "${language_root}" <<'PY'
from __future__ import annotations

import json
import os
import pathlib
import tempfile
import sys
from typing import Any

root = pathlib.Path(sys.argv[1])
packs: list[dict[str, Any]] = []
for pack_dir in sorted(root.iterdir()):
    if not pack_dir.is_dir() or pack_dir.name.startswith("."):
        continue
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = {
        "id": manifest["id"],
        "display_name": manifest["display_name"],
        "version": manifest["version"],
        "frontend_code": manifest["frontend_code"],
        "backend_code": manifest["backend_code"],
    }
    if manifest.get("element_plus_locale"):
        entry["element_plus_locale"] = manifest["element_plus_locale"]
    packs.append(entry)

payload = {"schema": 1, "packs": packs}
payload_text = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
index_path = root / "installed.json"
try:
    if index_path.read_text(encoding="utf-8") == payload_text:
        index_path.chmod(0o644)
        raise SystemExit(0)
except (FileNotFoundError, OSError, UnicodeDecodeError):
    pass

with tempfile.NamedTemporaryFile(
    mode="w",
    encoding="utf-8",
    dir=root,
    prefix=".installed-",
    suffix=".json",
    delete=False,
) as index_file:
    index_file.write(payload_text)
    index_file.flush()
    os.fsync(index_file.fileno())
    temporary_path = pathlib.Path(index_file.name)
temporary_path.chmod(0o644)
os.replace(temporary_path, index_path)
PY
}

restart_language_pack_services() {
	if [[ ! -f "${ROOT}/.env" ]] || ! command -v docker >/dev/null 2>&1 \
		|| ! docker info >/dev/null 2>&1; then
		warn "language pack updated; restart HyperFileLens services before using it"
		return 0
	fi
	require_docker
	step "Restarting services to load language-pack changes ..."
	local api_service color
	if color="$(read_active_color)"; then
		api_service="api-${color}"
		drain_api_color "${color}"
	else
		api_service="api"
	fi
	if ! (
		cd "${ROOT}"
		"${COMPOSE[@]}" --env-file "${ROOT}/.env" \
			-f "${ROOT}/${LANG_PACK_COMPOSE_FILE}" \
			restart "${api_service}" worker scheduler nginx
	); then
		warn "language pack updated, but automatic service restart failed"
	fi
}

cmd_language_pack_install() {
	local archive="" temp_dir language_root app_version pack_id pack_source
	local validation_output
	local incoming target backup
	local -a validation_result=()
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--file)
			shift
			archive="${1:-}"
			[[ -n "${archive}" ]] || die "--file requires a package path"
			;;
		*) die "unknown lang-pack install option: $1" 2 ;;
		esac
		shift
	done
	[[ -n "${archive}" ]] || die "lang-pack install requires --file <package.tar.gz>"
	archive="$(realpath "${archive}")"
	[[ -f "${archive}" ]] || die "language-pack archive not found: ${archive}"

	init_language_pack_root
	language_root="${ROOT}/data/lang-packs"
	mkdir -p "${language_root}"
	temp_dir="$(mktemp -d "${language_root}/.extract-XXXXXX")"
	app_version="$(read_language_pack_app_version)"
	if ! validation_output="$(
		validate_and_extract_language_pack "${archive}" "${temp_dir}" "${app_version}"
	)"; then
		safe_rm_dir "${temp_dir}"
		die "language-pack validation failed"
	fi
	mapfile -t validation_result <<< "${validation_output}"
	[[ "${#validation_result[@]}" -eq 2 ]] \
		|| die "language-pack validator returned an unexpected result"
	pack_id="${validation_result[0]}"
	pack_source="${validation_result[1]}"
	incoming="${language_root}/.incoming-${pack_id}-$$"
	target="${language_root}/${pack_id}"
	backup="${language_root}/.backup-${pack_id}-$$"

	safe_rm_dir "${incoming}"
	mkdir -p "${incoming}"
	cp -a "${pack_source}/." "${incoming}/"
	if [[ -e "${target}" ]]; then
		mv "${target}" "${backup}"
	fi
	if ! mv "${incoming}" "${target}"; then
		[[ -e "${backup}" ]] && mv "${backup}" "${target}"
		safe_rm_dir "${temp_dir}"
		die "failed to activate language pack ${pack_id}"
	fi
	safe_rm_dir "${backup}"
	safe_rm_dir "${temp_dir}"
	refresh_language_pack_index "${language_root}"
	log "Installed language pack ${pack_id} for HyperFileLens ${app_version}"
	restart_language_pack_services
}

cmd_language_pack_list() {
	local language_root
	init_language_pack_root
	language_root="${ROOT}/data/lang-packs"
	mkdir -p "${language_root}"
	refresh_language_pack_index "${language_root}"
	python3 - "${language_root}/installed.json" <<'PY'
from __future__ import annotations

import json
import pathlib
import sys

index = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
packs = index.get("packs", [])
if not packs:
    print("No optional language packs installed.")
else:
    for pack in packs:
        print(
            f"{pack['id']}\t{pack['version']}\t{pack['display_name']}\t"
            f"frontend={pack['frontend_code']}\tbackend={pack['backend_code']}"
        )
PY
}

cmd_language_pack_remove() {
	local pack_id=${1:-} language_root target
	[[ $# -eq 1 ]] || die "usage: install.sh lang-pack remove <pack-id>"
	[[ "${pack_id}" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] \
		|| die "invalid language-pack id: ${pack_id}"
	init_language_pack_root
	language_root="${ROOT}/data/lang-packs"
	target="${language_root}/${pack_id}"
	[[ -d "${target}" ]] || die "language pack is not installed: ${pack_id}"
	safe_assert_path_under_dir "${target}" "${language_root}" "language-pack path"
	safe_rm_dir "${target}"
	refresh_language_pack_index "${language_root}"
	log "Removed language pack ${pack_id}"
	restart_language_pack_services
}

cmd_language_pack() {
	local action=${1:-}
	case "${action}" in
	install)
		shift
		cmd_language_pack_install "$@"
		;;
	list)
		shift
		[[ $# -eq 0 ]] || die "usage: install.sh lang-pack list"
		cmd_language_pack_list
		;;
	remove)
		shift
		cmd_language_pack_remove "$@"
		;;
	*) die "usage: install.sh lang-pack {install --file PATH|list|remove PACK_ID}" ;;
	esac
}

cmd_uninstall() {
	local purge_config=0 purge_data=0 purge_all=0
	local with_sourcelens=0 purge_sourcelens_data=0 purge_media=0
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--purge-config) purge_config=1 ;;
		--purge-data) purge_data=1 ;;
		--purge-all) purge_all=1 ;;
		--with-sourcelens) with_sourcelens=1 ;;
		--purge-sourcelens-data) purge_sourcelens_data=1 ;;
		--purge-media) purge_media=1 ;;
		*) die "unknown uninstall option: $1" 2 ;;
		esac
		shift
	done
	[[ "${purge_all}" -eq 1 ]] && purge_config=1 && purge_data=1

	init_install_root
	log "======== HyperFileLens uninstall ========"

	if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
		require_docker
		if [[ -f "${ROOT}/.env" ]]; then
			step "Stopping and removing HyperFileLens containers ..."
			compose_all_profiles down || true
		fi
		if [[ "${with_sourcelens}" -eq 1 ]]; then
			uninstall_bundled_sourcelens "${purge_sourcelens_data}"
		fi
		remove_manifest_images
	else
		warn "Docker unavailable; skipping container/image cleanup"
	fi

	if [[ "${purge_media}" -eq 1 ]]; then
		step "Removing media publish artifacts ..."
		safe_rm_dir "${ROOT}/data/media/agent-releases"
		safe_rm_dir "${ROOT}/data/media/enroll-bootstrap"
		safe_rm_dir "${ROOT}/data/media/gateway-bootstrap"
		log "Removed agent-releases, enroll-bootstrap, and gateway-bootstrap"
	fi

	if [[ "${purge_data}" -eq 1 ]]; then
		step "Removing data/ ..."
		safe_assert_removable_data_dir "${ROOT}/data" "${ROOT}"
		safe_rm_dir "${ROOT}/data"
		log "Removed data/"
	fi

	if [[ "${purge_config}" -eq 1 ]]; then
		step "Removing .env ..."
		safe_assert_env_file "${ROOT}/.env" "${ROOT}"
		safe_rm_file "${ROOT}/.env"
		log "Removed .env"
	fi

	log "Uninstall complete (services and images removed)"
	log "Install directory kept: ${ROOT}"
	log "  Remaining: install.sh, docker-compose.yml, deploy/, images/, payload/, backup/, and other package files"
	if [[ "${with_sourcelens}" -eq 0 ]] && sourcelens_installed; then
		log "  SourceLens still installed at ${SOURCELENS_INSTALL_DIR} (use --with-sourcelens to remove)"
	fi
	if [[ "${purge_data}" -eq 0 ]]; then
		log "  data/ was preserved (use --purge-data or --purge-all to remove)"
	fi
	if [[ "${purge_config}" -eq 0 ]]; then
		log "  .env was preserved (use --purge-config or --purge-all to remove)"
	fi
	if [[ "${purge_media}" -eq 0 ]]; then
		log "  media publish artifacts were preserved (use --purge-media to remove)"
	fi
	log "To remove the install directory manually after you no longer need this copy:"
	log "  sudo rm -rf ${ROOT}"
	log "Host Docker CE (if installed from the bundled archive) is not removed by uninstall."
}

cmd_backup() {
	[[ $# -eq 0 ]] || die "backup does not accept options" 2
	init_existing_install_root
	log "======== HyperFileLens managed backup ========"
	if ! create_managed_backup "$(date +%Y%m%d-%H%M%S)" 1; then
		die "managed backup was not created; existing valid backups were preserved"
	fi
}

cmd_upgrade() {
	local from="" allow_main_build=0
	local sourcelens_mode=-1 remove_sourcelens=0 purge_sourcelens_data=0
	local src_root new_version cur_version new_channel cur_channel upgrade_sourcelens=0 backup_stamp
	local target_color running_worker_ids
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--from)
			shift
			from="${1:-}"
			[[ -n "${from}" ]] || die "--from requires a path"
			;;
		--with-sourcelens) sourcelens_mode=1 ;;
		--hfl-only) sourcelens_mode=0 ;;
		--remove-sourcelens) remove_sourcelens=1 ;;
		--purge-sourcelens-data) purge_sourcelens_data=1 ;;
		--yes) UPGRADE_YES=1 ;;
		--direct-host) [[ $# -ge 2 && -n "${2:-}" ]] || die "--direct-host requires a value" 2; PUBLIC_HOST="$2"; shift ;;
		--public-url) [[ $# -ge 2 ]] || die "--public-url requires a value" 2; PUBLIC_URL="$2"; shift ;;
		--admin-public-url) [[ $# -ge 2 ]] || die "--admin-public-url requires a value" 2; ADMIN_PUBLIC_URL="$2"; shift ;;
		--runtime-env-file) [[ $# -ge 2 && -n "${2:-}" ]] || die "--runtime-env-file requires a path" 2; RUNTIME_ENV_FILE="$2"; shift ;;
		--allow-main-build) allow_main_build=1 ;;
		*) die "unknown upgrade option: $1" 2 ;;
		esac
		shift
	done
	[[ -n "${from}" ]] || die "upgrade requires --from <directory-or.tar.gz>"

	init_existing_install_root
	preflight_package_layout 0
	warn_host_resources
	cur_version="$(read_version)"
	log "======== HyperFileLens upgrade ${cur_version} ========"

	trap cleanup_upgrade_and_finish EXIT
	src_root="$(prepare_upgrade_source "${from}")"
	validate_package_identity "${src_root}"
	new_version="$(read_version_from_dir "${src_root}")"
	cur_channel="$(read_channel_from_dir "${ROOT}")"
	new_channel="$(read_channel_from_dir "${src_root}")"
	if [[ "${new_channel}" == "main" && "${allow_main_build}" -ne 1 ]]; then
		die "main channel packages require --allow-main-build"
	fi

	if [[ "${new_version}" == "${cur_version}" ]]; then
		confirm_same_version_upgrade "${cur_version}"
	elif [[ "${new_channel}" == "release" && "${cur_channel}" == "release" ]] \
		&& version_lt "${new_version}" "${cur_version}"; then
		die "downgrade not supported (${new_version} < ${cur_version})"
	fi

	log "Upgrading: ${cur_version} -> ${new_version}"

	step "[1/8] Validating persisted Redis recovery requirements ..."
	require_docker
	# Heal runtime links before backup/running-state discovery. This also makes a
	# host recoverable when an older failed upgrade already removed the links.
	repair_sourcelens_runtime_bindings
	preflight_redis_recovery

	step "[2/8] Backing up current config and data ..."
	backup_stamp="$(date +%Y%m%d-%H%M%S)"
	create_managed_backup "${backup_stamp}" 0 \
		|| die "managed upgrade backup failed; existing services were not stopped"
	UPGRADE_BACKUP_DIR="${ROOT}/backup/upgrade-${backup_stamp}"

	step "[3/8] Validating upgrade package ..."
	preflight_blue_green_source "${src_root}"
	validate_publish_artifacts "${src_root}"
	validate_default_tls_bundle "${src_root}/deploy/nginx/certs"
	case "$(tls_pair_state "${ROOT}/deploy/nginx/certs")" in
	complete)
		validate_tls_pair "${ROOT}/deploy/nginx/certs"
		secure_tls_permissions "${ROOT}/deploy/nginx/certs"
		;;
	missing) log "No installed TLS pair exists; package defaults will be installed" ;;
	incomplete) die "existing TLS certificate pair is incomplete under ${ROOT}/deploy/nginx/certs" ;;
	esac
	if [[ "$(configured_sourcelens_mode)" == "bundled" || "${sourcelens_mode}" -eq 1 ]] \
		&& package_has_sourcelens_dir "${src_root}"; then
		preflight_sourcelens_bundle "${src_root}"
	fi
	if should_upgrade_sourcelens "${sourcelens_mode}" "${src_root}"; then
		upgrade_sourcelens=1
		SOURCELENS_GATE_ADOPTION_SOURCE="${src_root}/sourcelens"
	fi
	if [[ "${remove_sourcelens}" -eq 1 ]]; then
		upgrade_sourcelens=0
	fi

	step "[4/8] Checking/upgrading Docker ..."
	upgrade_host_docker_from_source "${ROOT}" "${src_root}"
	require_docker
	ensure_bridge_network

	step "Preloading verified target images before the maintenance window ..."
	load_images_from_manifest "$([[ "${upgrade_sourcelens}" -eq 0 ]] && echo 1 || echo 0)" "${src_root}"

	step "[5/8] Selecting the inactive color and pausing background execution ..."
	if compose_in_root ps -q 2>/dev/null | grep -q .; then
		UPGRADE_HFL_WAS_RUNNING=1
	fi
	if sourcelens_installed && sourcelens_compose ps -q 2>/dev/null | grep -q .; then
		UPGRADE_SOURCELENS_WAS_RUNNING=1
	fi
	if ! UPGRADE_PREVIOUS_COLOR="$(read_active_color)"; then
		UPGRADE_LEGACY_API_CID="$(compose_in_root ps -q api 2>/dev/null | head -1)"
		if [[ -n "${UPGRADE_LEGACY_API_CID}" ]]; then
			UPGRADE_PREVIOUS_COLOR="legacy"
		else
			UPGRADE_PREVIOUS_COLOR="blue"
		fi
	fi
	target_color="$(opposite_color "${UPGRADE_PREVIOUS_COLOR}")"
	UPGRADE_TARGET_COLOR="${target_color}"
	UPGRADE_TARGET_VERSION="${new_version}"
	log "Blue/green plan: ${UPGRADE_PREVIOUS_COLOR} -> ${target_color}"
	record_deployment_phase prepared "${UPGRADE_PREVIOUS_COLOR}" "${target_color}" "${new_version}"
	UPGRADE_RECOVERY_ARMED=1
	compose_in_root stop scheduler || true
	# Let the old worker finish or return late-acknowledged work before migrations
	# change task state contracts. This prevents old code from advancing a
	# deferred source deregistration while the new release ends that behavior.
	compose_in_root stop --timeout 600 worker \
		|| die "old worker did not stop; refusing to apply task-state migrations"
	if ! running_worker_ids="$(compose_in_root ps --status running -q worker 2>/dev/null)"; then
		die "could not verify old worker state; refusing to apply task-state migrations"
	fi
	[[ -z "${running_worker_ids}" ]] \
		|| die "old worker is still running; refusing to apply task-state migrations"

	step "[6/8] Applying target files, configuration, and singleton migration ..."
	# Existing releases used APP_VERSION for stable Nginx. Pin that currently
	# running image before the new environment template is merged.
	pin_gateway_version_if_missing "${cur_version}"
	sync_env_from_example "${src_root}/.env.example"
	apply_upgrade_files "${src_root}" "${remove_sourcelens}" "${upgrade_sourcelens}"
	if [[ "${UPGRADE_PREVIOUS_COLOR}" == "legacy" ]]; then
		# Do not claim a blue active pool while the legacy API still owns traffic.
		safe_rm_file "$(active_color_file)"
	fi
	update_env_versions "${new_version}" "${new_channel}" "$(read_image_version_from_dir "${src_root}")"
	if [[ "$(configured_sourcelens_mode)" == "bundled" ]] \
		&& sourcelens_installed \
		&& [[ "${remove_sourcelens}" -eq 0 ]]; then
		configure_lens_bridge_env
	fi
	apply_runtime_configuration
	validate_tls_pair "${ROOT}/deploy/nginx/certs"

	ensure_data_dirs
	sync_runtime_media
	compose_in_root up -d --no-recreate postgres redis
	compose_in_root --profile tools run --rm --no-deps migration
	record_deployment_phase migrated "${UPGRADE_PREVIOUS_COLOR}" "${target_color}" "${new_version}"

	step "[7/8] Starting ${target_color}, switching stable traffic, and handing off workers ..."
	compose_color "${target_color}" up -d --no-build \
		"api-${target_color}" "web-${target_color}"
	wait_for_color_health "${target_color}" \
		|| die "inactive ${target_color} API/Web pool failed its readiness gate"
	record_deployment_phase candidate_ready "${UPGRADE_PREVIOUS_COLOR}" "${target_color}" "${new_version}"
	cutover_hfl_color "${UPGRADE_PREVIOUS_COLOR}" "${target_color}" \
		|| die "blue/green cutover failed; previous traffic route was restored"
	record_deployment_phase switched "${UPGRADE_PREVIOUS_COLOR}" "${target_color}" "${new_version}"
	# The active API/Web pool is now authoritative; start singleton consumers
	# from the target release and recover any returned in-flight work.
	compose_in_root up -d --no-build worker scheduler
	wait_for_services_health "${HFL_HEALTH_TIMEOUT_SECONDS:-600}" \
		postgres redis worker scheduler "api-${target_color}" "web-${target_color}" nginx \
		&& wait_for_public_endpoints \
		|| die "HyperFileLens failed its post-upgrade health gate"
	write_active_color "${target_color}"
	UPGRADE_HFL_COMMITTED=1
	record_deployment_phase committed "${UPGRADE_PREVIOUS_COLOR}" "${target_color}" "${new_version}"
	remove_retired_color "${UPGRADE_PREVIOUS_COLOR}"

	# SourceLens is a separate lifecycle. An unchanged bundle is never touched;
	# when it changes, upgrade it only after HFL traffic has safely switched.
	if should_remove_sourcelens "${remove_sourcelens}"; then
		stop_bundled_sourcelens
		remove_sourcelens_images
		if [[ "${purge_sourcelens_data}" -eq 1 ]]; then
			purge_sourcelens_data_dir
		fi
		log "SourceLens application runtime removed from this host"
	elif [[ "${upgrade_sourcelens}" -eq 1 ]]; then
		begin_sourcelens_maintenance_gate
		stop_bundled_sourcelens
		SOURCELENS_UPGRADE_STARTED=1
		install_bundled_sourcelens
	fi
	if [[ "$(configured_sourcelens_mode)" == "bundled" ]] \
		&& sourcelens_installed \
		&& [[ "${remove_sourcelens}" -eq 0 ]]; then
		configure_lens_bridge_env
		wait_for_sourcelens_health || die "bundled SourceLens failed its independent post-upgrade health gate"
		if [[ "${upgrade_sourcelens}" -eq 1 ]]; then
			record_sourcelens_installed_bundle "${ROOT}/sourcelens" \
				|| die "could not record the installed SourceLens bundle identity"
		fi
	fi
	if [[ "${SOURCELENS_MAINTENANCE_ARMED}" == "1" ]]; then
		clear_sourcelens_maintenance_gate
	fi
	if [[ "${SOURCELENS_PROXY_GATE_ARMED}" == "1" ]]; then
		clear_sourcelens_proxy_gate
	fi
	sync_optional_identity_settings
	check_local_platform_gateway_continuity
	prune_agent_release_media
	prune_old_managed_image_refs
	record_deployment_phase complete "${UPGRADE_PREVIOUS_COLOR}" "${target_color}" "${new_version}"
	UPGRADE_RECOVERY_ARMED=0

	step "[8/8] Cleaning up temporary directory ..."
	cleanup_upgrade_tmp
	trap finish_session EXIT

	log "Upgrade complete: ${new_version}"
	compose_all_profiles ps
	print_console_access_summary
}

main() {
	local -a args=()
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--log-file)
			[[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || die "--log-file requires a value" 2
			LOG_FILE="$2"
			shift 2
			;;
		--verbose)
			VERBOSE=1
			shift
			;;
		--print-config)
			PRINT_CONFIG=1
			shift
			;;
		*)
			args+=("$1")
			shift
			;;
		esac
	done
	set -- "${args[@]}"

	case "${VERBOSE}" in
	0 | 1) ;;
	*) die "--verbose/HFL_LOG_VERBOSE must resolve to 0 or 1" 2 ;;
	esac
	if [[ "${PRINT_CONFIG}" -eq 1 ]]; then
		print_config
		return 0
	fi
	if [[ $# -gt 0 && ( "$1" == "-h" || "$1" == "--help" || "$1" == "help" ) ]]; then
		usage
		return 0
	fi
	configure_logging
	SESSION_STARTED=1
	trap finish_session EXIT
	trap 'exit 130' INT TERM
	log "Installer session started"

	if [[ $# -eq 0 ]]; then
		cmd_install
		return 0
	fi

	local cmd=$1
	case "${cmd}" in
	install | backup | start | stop | restart | status | manage | platform-gateway | uninstall | upgrade | lang-pack)
		shift
		;;
	-*)
		cmd_install "$@"
		return 0
		;;
	*)
		die "unknown command: ${cmd} (use --help)" 2
		;;
	esac

	case "${cmd}" in
	install) cmd_install "$@" ;;
	backup) cmd_backup "$@" ;;
	start) cmd_start "$@" ;;
	stop) cmd_stop "$@" ;;
	restart) cmd_restart "$@" ;;
	status) cmd_status "$@" ;;
	manage) cmd_manage "$@" ;;
	platform-gateway) cmd_platform_gateway "$@" ;;
	uninstall) cmd_uninstall "$@" ;;
	upgrade) cmd_upgrade "$@" ;;
	lang-pack) cmd_language_pack "$@" ;;
	esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	main "$@"
fi
