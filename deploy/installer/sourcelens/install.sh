#!/usr/bin/env bash
# SourceLens offline install for HyperFileLens release bundles.
# Installed under /opt/hyperfilelens/sourcelens by default.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=compose-lifecycle.sh
source "${SCRIPT_DIR}/compose-lifecycle.sh"
if [[ -n "${HFL_COMPOSE_RUNTIME_FILE:-}" ]]; then
	COMPOSE_RUNTIME_FILE="${HFL_COMPOSE_RUNTIME_FILE}"
elif [[ -f "${SCRIPT_DIR}/../compose-runtime.sh" ]]; then
	COMPOSE_RUNTIME_FILE="${SCRIPT_DIR}/../compose-runtime.sh"
else
	COMPOSE_RUNTIME_FILE="${SCRIPT_DIR}/../payload/runtime/compose-runtime.sh"
fi
[[ -f "${COMPOSE_RUNTIME_FILE}" ]] || { printf '[FAIL] missing Compose runtime helper: %s\n' "${COMPOSE_RUNTIME_FILE}" >&2; exit 1; }
# shellcheck disable=SC1090
source "${COMPOSE_RUNTIME_FILE}"

SOURCELENS_INSTALL_DIR="${SOURCELENS_INSTALL_DIR:-/opt/hyperfilelens/sourcelens}"
SOURCELENS_DATA_DIR="${SOURCELENS_DATA_DIR:-/opt/hyperfilelens/data/sourcelens}"
SOURCELENS_CONFIG_DIR="${SOURCELENS_CONFIG_DIR:-${SOURCELENS_DATA_DIR}/config}"
SOURCELENS_TLS_CERT_DIR="${SOURCELENS_TLS_CERT_DIR:-}"
SOURCELENS_NGINX_HTTPS_PORT="${SOURCELENS_NGINX_HTTPS_PORT:-11445}"
SOURCELENS_CONSOLE_BIND_ADDRESS="${SOURCELENS_CONSOLE_BIND_ADDRESS:-0.0.0.0}"
SOURCELENS_CONSOLE_PORT="${SOURCELENS_CONSOLE_PORT:-${SOURCELENS_NGINX_HTTPS_PORT}}"
HFL_BRIDGE_NETWORK="hyperfilelens-bridge"
HFL_PARENT_ENV_FILE="${HFL_PARENT_ENV_FILE:-}"
SKIP_IMAGE_LOAD=0
LOG_FILE="${HFL_LOG_FILE:-}"
VERBOSE="${HFL_LOG_VERBOSE:-0}"
PRINT_CONFIG=0
SESSION_STARTED=0

hfl_now() { date -u +%Y-%m-%dT%H:%M:%S.000Z 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ; }
emit() {
	local level=$1 tag
	shift
	if [[ "${HFL_PARENT_SESSION:-0}" == "1" ]]; then
		case "${level}" in
		INFO) tag='INFO ' ;;
		'....') tag='....' ;;
		' OK ') tag=' OK ' ;;
		WARN) tag='WARN' ;;
		SKIP) tag='SKIP' ;;
		FAIL) tag='FAIL' ;;
		DEBUG) tag='DEBUG' ;;
		*) tag="${level}" ;;
		esac
		printf '[%s] %s\n' "${tag}" "$*" >&2
	else
		printf '[%s] [%-5s] %s\n' "$(hfl_now)" "${level}" "$*" >&2
	fi
}
log() { emit INFO "$@"; }
step() { emit '....' "$@"; }
ok() { emit ' OK ' "$@"; }
skip() { emit SKIP "$@"; }
warn() { emit WARN "$@"; }
debug() { [[ "${VERBOSE}" == "1" ]] && emit DEBUG "$@" || true; }
die() { local message=$1 code=${2:-1}; emit FAIL "${message}"; exit "${code}"; }

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
		if [[ "${rc}" -eq 0 ]]; then
			ok "SourceLens installer session completed"
		else
			printf '[%s] [FAIL ] SourceLens installer session exited with status %s\n' "$(hfl_now)" "${rc}" >&2
		fi
	fi
	exit "${rc}"
}

usage() {
	cat <<'USAGE'
Usage: ./sourcelens/install.sh install [options]

Options:
  --skip-image-load   Images were already loaded by HyperFileLens install.sh
  --install-dir DIR   Target install directory (default: /opt/hyperfilelens/sourcelens)
  --log-file FILE     Append runtime logs to FILE
  --verbose           Enable detailed logs
  --print-config      Print effective non-secret configuration and exit
USAGE
}

print_config() {
	cat <<EOF
install_dir=${SOURCELENS_INSTALL_DIR}
data_dir=${SOURCELENS_DATA_DIR}
config_dir=${SOURCELENS_CONFIG_DIR}
tls_cert_dir=${SOURCELENS_TLS_CERT_DIR}
console_bind_address=${SOURCELENS_CONSOLE_BIND_ADDRESS}
console_port=${SOURCELENS_CONSOLE_PORT}
bridge_network=${HFL_BRIDGE_NETWORK}
skip_image_load=${SKIP_IMAGE_LOAD}
log_file=${LOG_FILE:-<none>}
verbose=${VERBOSE}
hfl_parent_env_file=${HFL_PARENT_ENV_FILE:-<none>}
EOF
}

sync_hfl_sentry_configuration() {
	local root=$1 env_file="${SOURCELENS_CONFIG_DIR}/.env"
	local parent_env="${HFL_PARENT_ENV_FILE:-$(dirname "${SOURCELENS_INSTALL_DIR}")/.env}"
	local build_info="${root}/BUILD_INFO.json"
	local output="${root}/deploy/nginx/hfl-sentry-config.js"
	local sync_script="${root}/sync-sentry-runtime.py"
	[[ -f "${parent_env}" && ! -L "${parent_env}" ]] || {
		warn "HFL parent environment is unavailable; bundled SourceLens Sentry remains disabled"
		printf '%s\n' 'window.__HFL_SOURCELENS_SENTRY__ = Object.freeze({ enabled: false })' \
			| run_as_root tee "${output}" >/dev/null
		return 0
	}
	[[ -f "${sync_script}" ]] || die "missing ${sync_script}"
	run_as_root python3 "${sync_script}" \
		--parent-env "${parent_env}" \
		--sourcelens-env "${env_file}" \
		--build-info "${build_info}" \
		--frontend-config "${output}"
	run_as_root chmod 600 "${env_file}"
	run_as_root chmod 644 "${output}"
	log "Synchronized HFL Sentry policy for bundled SourceLens (credentials redacted)"
}

resolve_bundle_root() {
	local dir
	dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
	[[ -f "${dir}/docker-compose.yml" && -f "${dir}/BUILD_INFO.json" ]] || die "SourceLens bundle layout not found"
	printf '%s' "${dir}"
}

resolve_package_root() {
	local bundle=$1
	if [[ -f "${bundle}/../MANIFEST.json" ]]; then
		cd "${bundle}/.." && pwd
		return 0
	fi
	printf '%s' "${bundle}"
}

resolve_tls_cert_dir() {
	if [[ -z "${SOURCELENS_TLS_CERT_DIR}" ]]; then
		SOURCELENS_TLS_CERT_DIR="$(dirname "${SOURCELENS_INSTALL_DIR}")/deploy/nginx/certs"
	fi
}

ensure_tls_certs() {
	local cert="${SOURCELENS_TLS_CERT_DIR}/tls.crt"
	local key="${SOURCELENS_TLS_CERT_DIR}/tls.key"
	[[ -s "${cert}" && -s "${key}" ]] \
		|| die "shared HyperFileLens TLS certificate and key must both exist under ${SOURCELENS_TLS_CERT_DIR}"
	command -v openssl >/dev/null 2>&1 \
		|| die "openssl is required to validate shared TLS certificates"
	local cert_pub key_pub
	cert_pub="$(openssl x509 -in "${cert}" -pubkey -noout 2>/dev/null | sha256sum | cut -d' ' -f1)"
	key_pub="$(openssl pkey -in "${key}" -pubout 2>/dev/null | sha256sum | cut -d' ' -f1)"
	[[ -n "${cert_pub}" && "${cert_pub}" == "${key_pub}" ]] \
		|| die "shared HyperFileLens TLS certificate and key do not match"
	run_as_root chmod 644 "${cert}"
	run_as_root chmod 600 "${key}"
	log "Using existing shared TLS certificates from ${SOURCELENS_TLS_CERT_DIR}"
}

run_as_root() {
	if [[ "$(id -u)" -eq 0 ]]; then
		"$@"
	else
		command -v sudo >/dev/null 2>&1 || die "root or sudo required"
		sudo "$@"
	fi
}

materialize_install_dir() {
	local source=$1
	local target=$2
	local source_abs target_abs
	source_abs="$(cd "${source}" && pwd)"
	target_abs="$(readlink -m "${target}")"
	if [[ "${source_abs}" == "${target_abs}" ]]; then
		log "already installed at ${target}"
		return 0
	fi
	log "Copying SourceLens bundle ${source} -> ${target}"
	run_as_root mkdir -p "${target}"
	run_as_root rsync -aH \
		--exclude '.env' \
		--exclude 'data/' \
		"${source}/" "${target}/"
}

ensure_env_file() {
	local root=$1
	local example="${root}/.env.example"
	local env_file="${SOURCELENS_CONFIG_DIR}/.env"
	local patch_script="${root}/patch-env-runtime.py"
	[[ -f "${example}" ]] || die "missing ${example}"
	run_as_root mkdir -p "${SOURCELENS_CONFIG_DIR}"
	if [[ -f "${root}/.env" && ! -L "${root}/.env" && ! -f "${env_file}" ]]; then
		run_as_root cp "${root}/.env" "${env_file}"
		log "Migrated existing SourceLens configuration to ${env_file}"
	fi
	if [[ ! -f "${env_file}" ]]; then
		run_as_root cp "${example}" "${env_file}"
		run_as_root chmod 600 "${env_file}"
		log "Created ${env_file} from .env.example"
	else
		run_as_root chmod 600 "${env_file}"
		python3 - "${env_file}" "${example}" <<'PY'
import pathlib
import re
import sys

env_path = pathlib.Path(sys.argv[1])
example_path = pathlib.Path(sys.argv[2])
text = env_path.read_text(encoding="utf-8")
existing = set(re.findall(r"^([A-Z0-9_]+)=", text, flags=re.M))
added = []
for line in example_path.read_text(encoding="utf-8").splitlines():
    if not line or line.lstrip().startswith("#") or "=" not in line:
        continue
    key = line.split("=", 1)[0].strip()
    if key and key not in existing:
        text = text.rstrip() + f"\n{line}\n"
        added.append(key)
if added:
    env_path.write_text(text, encoding="utf-8")
    print("[sourcelens-install] merged env keys:", ", ".join(added))
PY
	fi
	[[ -f "${patch_script}" ]] || die "missing ${patch_script}"
	run_as_root python3 "${patch_script}" "${env_file}"
	run_as_root python3 - "${env_file}" \
		"${SOURCELENS_CONSOLE_BIND_ADDRESS}" \
		"${SOURCELENS_CONSOLE_PORT}" \
		"${SOURCELENS_TLS_CERT_DIR}" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
bind_address = sys.argv[2]
port = sys.argv[3]
tls_cert_dir = sys.argv[4]
text = path.read_text(encoding="utf-8")

for name, value in {
    "SOURCELENS_CONSOLE_BIND_ADDRESS": bind_address,
    "SOURCELENS_CONSOLE_PORT": port,
    "NGINX_HTTPS_PORT": port,
    "SOURCELENS_TLS_CERT_DIR": tls_cert_dir,
}.items():
    pattern = rf"^{re.escape(name)}=.*$"
    line = f"{name}={value}"
    if re.search(pattern, text, flags=re.M):
        text = re.sub(pattern, line, text, count=1, flags=re.M)
    else:
        text = text.rstrip() + f"\n{line}\n"
path.write_text(text, encoding="utf-8")
PY
	run_as_root chmod 600 "${env_file}"
	if [[ -e "${root}/.env" && ! -L "${root}/.env" ]]; then
		run_as_root rm -f "${root}/.env"
	fi
	run_as_root ln -sfn "${env_file}" "${root}/.env"
	sync_hfl_sentry_configuration "${root}"
	log "Applied SourceLens runtime env defaults (DJANGO_DEBUG=true)"
}

ensure_data_dirs() {
	local root=$1
	local data_dir="${SOURCELENS_DATA_DIR}"
	local root_data_abs data_abs
	root_data_abs="$(readlink -m "${root}/data")"
	data_abs="$(readlink -m "${data_dir}")"
	if [[ -d "${root}/data" && ! -L "${root}/data" && "${root_data_abs}" != "${data_abs}" ]]; then
		local legacy="${root}/data.pre-hfl-bridge.$(date +%Y%m%d%H%M%S)"
		log "Migrating existing SourceLens data to ${data_dir}"
		run_as_root rsync -aH "${root}/data/" "${data_dir}/"
		run_as_root mv "${root}/data" "${legacy}"
		log "Preserved previous data directory at ${legacy}"
	fi
	run_as_root mkdir -p "${data_dir}"
	if [[ "${root_data_abs}" != "${data_abs}" ]]; then
		if [[ ! -L "${root}/data" ]]; then
			run_as_root ln -s "${data_dir}" "${root}/data"
		else
			run_as_root ln -sfn "${data_dir}" "${root}/data"
		fi
	fi
	run_as_root mkdir -p \
		"${data_dir}/postgresql/data" \
		"${data_dir}/redis" \
		"${data_dir}/logs/api" \
		"${data_dir}/logs/worker" \
		"${data_dir}/logs/scheduler" \
		"${data_dir}/logs/nginx" \
		"${data_dir}/logs/postgresql" \
		"${data_dir}/logs/redis" \
		"${data_dir}/storage" \
		"${data_dir}/document-attachments" \
		"${data_dir}/deliverables" \
		"${data_dir}/workspace" \
		"${data_dir}/django/staticfiles"
}

image_present() {
	local ref=$1
	docker image inspect "${ref%%@*}" >/dev/null 2>&1
}

load_images_from_package() {
	local package_root=$1
	local install_root=$2
	[[ -f "${install_root}/BUILD_INFO.json" ]] || die "missing ${install_root}/BUILD_INFO.json"
	python3 - "${package_root}" "${install_root}/BUILD_INFO.json" <<'PY'
import json
import os
import subprocess
import sys

root = sys.argv[1]
build_info_path = sys.argv[2]
with open(os.path.join(root, "MANIFEST.json"), encoding="utf-8") as fh:
    manifest = json.load(fh)
build_info = json.loads(open(build_info_path, encoding="utf-8").read())
embed = build_info.get("embed_local_lensnode")
if embed is None:
    embed = False

def present(ref: str) -> bool:
    tag = ref.split("@", 1)[0]
    return subprocess.run(
        ["docker", "image", "inspect", tag],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0

wanted = {
    "images/10-sourcelens-app.tar.gz",
    "images/12-nginx-stable-alpine.tar.gz",
    "images/01-postgres-17.tar.gz",
    "images/02-redis-alpine.tar.gz",
}
if embed:
    wanted.add("images/11-sourcelens-lensnode.tar.gz")

for entry in manifest.get("images", []):
    rel = entry.get("file", "")
    if rel not in wanted:
        continue
    refs = entry.get("refs", [])
    path = os.path.join(root, rel)
    if refs and all(present(ref) for ref in refs):
        print(f"[sourcelens-install] skipping {rel} (already loaded)")
        continue
    if not os.path.isfile(path):
        print(f"[sourcelens-install] ERROR: missing image archive {path}", file=sys.stderr)
        sys.exit(1)
    print(f"[sourcelens-install] loading {rel} ...")
    subprocess.run(["docker", "load", "-i", path], check=True)
if not embed:
    print("[sourcelens-install] skipping images/11-sourcelens-lensnode.tar.gz (embed_local_lensnode=false)")
PY
}

compose_cmd() {
	local root=$1
	shift
	hfl_compose_resolve 2.20.0 \
		|| die "supported Docker Compose command not found; $(hfl_compose_failure_detail 2.20.0)"
	(
		cd "${root}"
		"${HFL_COMPOSE[@]}" -p hyperfilelens-sourcelens "$@"
	)
}

ensure_bridge_network() {
	if docker network inspect "${HFL_BRIDGE_NETWORK}" >/dev/null 2>&1; then
		return 0
	fi
	log "Creating shared bridge network ${HFL_BRIDGE_NETWORK}"
	docker network create "${HFL_BRIDGE_NETWORK}" >/dev/null
}

wait_for_health() {
	local root=$1
	local attempt
	for attempt in $(seq 1 60); do
		if compose_cmd "${root}" exec -T nginx \
			curl -fsS http://127.0.0.1/health >/dev/null 2>&1; then
			log "SourceLens health check OK (sourcelens-nginx-1:/health)"
			return 0
		fi
		sleep 5
	done
	die "SourceLens did not become healthy on its private network"
}

cmd_install() {
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--skip-image-load) SKIP_IMAGE_LOAD=1 ;;
		--install-dir)
			[[ $# -ge 2 ]] || die "--install-dir requires a value"
			SOURCELENS_INSTALL_DIR="$2"
			shift
			;;
		*) die "unknown option: $1" 2 ;;
		esac
		shift
	done

	local bundle_root package_root install_root
	bundle_root="$(resolve_bundle_root)"
	package_root="$(resolve_package_root "${bundle_root}")"
	install_root="${SOURCELENS_INSTALL_DIR}"

	command -v docker >/dev/null 2>&1 || die "docker not found"
	docker info >/dev/null 2>&1 || die "cannot connect to Docker daemon"

	materialize_install_dir "${bundle_root}" "${install_root}"
	ensure_tls_certs
	ensure_env_file "${install_root}"
	ensure_data_dirs "${install_root}"

	if [[ "${SKIP_IMAGE_LOAD}" -eq 0 ]]; then
		load_images_from_package "${package_root}" "${install_root}"
	fi

	ensure_bridge_network
	log "Starting SourceLens stack in ${install_root}"
	hfl_compose_command_with_exit_event_recovery \
		compose_cmd "${install_root}" up -d --no-build --pull never --remove-orphans

	wait_for_health "${install_root}"
	log "SourceLens install complete on private network ${HFL_BRIDGE_NETWORK}"
}

main() {
	local -a args=()
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--skip-image-load)
			SKIP_IMAGE_LOAD=1
			shift
			;;
		--install-dir)
			[[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || die "--install-dir requires a value" 2
			SOURCELENS_INSTALL_DIR="$2"
			shift 2
			;;
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
		*) args+=("$1"); shift ;;
		esac
	done
	set -- "${args[@]}"
	resolve_tls_cert_dir
	if [[ "${PRINT_CONFIG}" -eq 1 ]]; then
		print_config
		return 0
	fi
	if [[ $# -eq 0 ]]; then
		configure_logging
		if [[ "${HFL_PARENT_SESSION:-0}" != "1" ]]; then
			SESSION_STARTED=1
			trap finish_session EXIT
			log "SourceLens installer session started"
		fi
		trap 'exit 130' INT TERM
		cmd_install
		return 0
	fi
	case "$1" in
	install)
		shift
		configure_logging
		if [[ "${HFL_PARENT_SESSION:-0}" != "1" ]]; then
			SESSION_STARTED=1
			trap finish_session EXIT
			log "SourceLens installer session started"
		fi
		trap 'exit 130' INT TERM
		cmd_install "$@"
		;;
	-h | --help)
		usage
		;;
	*)
		die "unknown command: $1" 2
		;;
	esac
}

main "$@"
