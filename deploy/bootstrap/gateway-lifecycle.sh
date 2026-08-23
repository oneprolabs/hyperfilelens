#!/usr/bin/env bash
# Data Gateway lifecycle helpers (sidecar upgrade/uninstall).
# Published under /media/gateway-bootstrap/; invoked by hfl-enroll or detached agent scripts.
set -euo pipefail

ENV_FILE="${HFL_AGENT_ENV_FILE:-/opt/hyperfilelens-agent/config/agent.env}"
LENS_ENV_FILE="${HFL_LENS_ENV_FILE:-/etc/hyperfilelens/lensnode.env}"
COMPOSE_DIR="${HFL_GATEWAY_COMPOSE_DIR:-/etc/hyperfilelens/lensnode}"
GATEWAY_BOOTSTRAP_BASE=""
HFL_API_BASE=""
HFL_ORG_KEY=""
HFL_NODE_TOKEN=""
HFL_NODE_ID=""
HFL_INSECURE_TLS="${HFL_INSECURE_TLS:-1}"
PURGE_ALL=0
HFL_LAST_ERROR=""
SIDECAR_LOCK_FILE="${HFL_GATEWAY_SIDECAR_LOCK_FILE:-/run/lock/hyperfilelens-gateway-sidecar.lock}"

DOWNLOAD_MAX_ATTEMPTS="${HFL_GATEWAY_DOWNLOAD_MAX_ATTEMPTS:-5}"
DOWNLOAD_RETRY_DELAY_SECONDS="${HFL_GATEWAY_DOWNLOAD_RETRY_DELAY_SECONDS:-2}"

LENSNODE_IMAGE_ARCHIVE="lensnode-image-linux-amd64.tar.gz"
SIDECAR_INSTALL_SCRIPT="gateway-install-lensnode-sidecar.sh"
COMPOSE_PROJECT="hyperfilelens-gateway"
DEFAULT_LENSNODE_IMAGE="hyperfilelens-sourcelens-lensnode:latest"
OWNED_LENSNODE_IMAGES=("${DEFAULT_LENSNODE_IMAGE}")

hfl_log() {
	printf '  [INFO] %s\n' "$*" >&2
}

hfl_fail() {
	HFL_LAST_ERROR=$1
	printf '  [FAIL] %s\n' "$1" >&2
	exit "${2:-1}"
}

acquire_sidecar_lock() {
	[[ "${HFL_GATEWAY_SIDECAR_LOCK_HELD:-0}" == "1" ]] && return 0
	command -v flock >/dev/null 2>&1 \
		|| hfl_fail "The flock command is required for Gateway lifecycle serialization." 1
	mkdir -p "$(dirname "${SIDECAR_LOCK_FILE}")" \
		|| hfl_fail "Could not create the Gateway lifecycle lock directory." 1
	if ! exec 9>"${SIDECAR_LOCK_FILE}"; then
		hfl_fail "Could not open the Gateway lifecycle lock." 1
	fi
	flock -x 9 || hfl_fail "Could not acquire the Gateway lifecycle lock." 1
	export HFL_GATEWAY_SIDECAR_LOCK_HELD=1
}

curl_tls=(-k)
if [[ "${HFL_INSECURE_TLS}" == "0" ]]; then
	curl_tls=()
fi

usage() {
	cat <<'USAGE'
Usage: gateway-lifecycle.sh <command> [options]

Commands:
  upgrade-sidecar       Reload the AI engine image and restart its container
  uninstall-sidecar     Stop the AI engine; use --purge-all to remove its local data

Options:
  --purge-all           Remove lensnode.env, compose dir, workspace, and local images
USAGE
}

read_env_value() {
	local file=$1 key=$2
	grep -E "^${key}=" "${file}" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'' ]//; s/["'\'' ]$//'
}

load_agent_credentials() {
	load_agent_credentials_optional || hfl_fail "missing or incomplete ${ENV_FILE}" 2
}

load_agent_credentials_optional() {
	[[ -f "${ENV_FILE}" ]] || return 1
	HFL_API_BASE="$(read_env_value "${ENV_FILE}" HFL_API_BASE)"
	HFL_ORG_KEY="$(read_env_value "${ENV_FILE}" HFL_ORG_KEY)"
	HFL_NODE_TOKEN="$(read_env_value "${ENV_FILE}" HFL_NODE_TOKEN)"
	HFL_NODE_ID="$(read_env_value "${ENV_FILE}" HFL_NODE_ID)"
	[[ -n "${HFL_API_BASE}" && -n "${HFL_ORG_KEY}" && -n "${HFL_NODE_TOKEN}" && -n "${HFL_NODE_ID}" ]] \
		|| return 1
	GATEWAY_BOOTSTRAP_BASE="${HFL_API_BASE%/}/media/gateway-bootstrap"
	return 0
}

report_lifecycle_status() {
	local phase=$1 status=$2 message=${3:-}
	[[ -n "${HFL_API_BASE}" ]] || return 0
	local payload
	payload="$(python3 - "${HFL_NODE_ID}" "${phase}" "${status}" "${message}" <<'PY'
import json, sys
node_id, phase, status, message = sys.argv[1:5]
body = {"node_id": node_id, "phase": phase, "status": status}
if message:
    body["error_message"] = message[:2000]
print(json.dumps(body))
PY
)"
	curl "${curl_tls[@]}" -fsS -X POST \
		-H "Content-Type: application/json" \
		-H "X-Org-Key: ${HFL_ORG_KEY}" \
		-H "X-Node-Token: ${HFL_NODE_TOKEN}" \
		-d "${payload}" \
		"${HFL_API_BASE%/}/api/v1/node/enrollment/gateway-install-status" >/dev/null \
		|| hfl_log "warning: failed to report lifecycle status (${phase}/${status})"
}

ensure_docker_ready() {
	command -v docker >/dev/null 2>&1 || hfl_fail "docker not found" 3
	docker info >/dev/null 2>&1 || hfl_fail "docker daemon not reachable" 3
}

remember_owned_lensnode_image() {
	local image=${1:-}
	[[ -n "${image}" ]] || return 0
	OWNED_LENSNODE_IMAGES+=("${image}")
}

remove_owned_legacy_gateway_containers() {
	local id project service working_dir config_files
	while IFS= read -r id; do
		[[ -n "${id}" ]] || continue
		project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "${id}" 2>/dev/null || true)"
		service="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "${id}" 2>/dev/null || true)"
		working_dir="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "${id}" 2>/dev/null || true)"
		config_files="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "${id}" 2>/dev/null || true)"
		[[ "${project}" == "sourcelens" && "${service}" == "lensnode" ]] || continue
		if [[ "${working_dir}" != "${COMPOSE_DIR}" \
			&& ",${config_files}," != *",${COMPOSE_DIR}/docker-compose.yml,"* ]]; then
			continue
		fi
		remember_owned_lensnode_image "$(docker inspect --format '{{.Config.Image}}' "${id}" 2>/dev/null || true)"
		hfl_log "Removing owned legacy Gateway container ${id:0:12} from project sourcelens."
		docker rm -f "${id}" >/dev/null
	done < <(docker ps -aq --no-trunc)
}

compose_down_sidecar() {
	local id
	remove_owned_legacy_gateway_containers
	if [[ -f "${COMPOSE_DIR}/docker-compose.yml" ]]; then
		docker compose version >/dev/null 2>&1 \
			|| hfl_fail "Docker Compose v2 is required to remove the AI engine" 3
		while IFS= read -r image; do
			remember_owned_lensnode_image "${image}"
		done < <(
			cd "${COMPOSE_DIR}"
			docker compose -p "${COMPOSE_PROJECT}" config --images 2>/dev/null || true
		)
		(
			cd "${COMPOSE_DIR}"
			docker compose -p "${COMPOSE_PROJECT}" down --remove-orphans
		)
	fi
	while IFS= read -r id; do
		[[ -n "${id}" ]] || continue
		remember_owned_lensnode_image "$(docker inspect --format '{{.Config.Image}}' "${id}" 2>/dev/null || true)"
		hfl_log "Removing managed AI engine container ${id:0:12}."
		docker rm -f "${id}" >/dev/null
	done < <(
		docker ps -aq \
			--filter 'label=com.hyperfilelens.managed=true' \
			--filter 'label=com.hyperfilelens.component=gateway-lensnode'
	)
	if docker ps -aq \
		--filter 'label=com.hyperfilelens.managed=true' \
		--filter 'label=com.hyperfilelens.component=gateway-lensnode' \
		| grep -q .; then
		hfl_fail "AI engine containers remain after uninstall" 4
	fi
}

download_bootstrap_file() {
	local name=$1 dest=$2 partial="${2}.part"
	local attempt curl_rc delay
	[[ "${DOWNLOAD_MAX_ATTEMPTS}" =~ ^[1-9][0-9]*$ ]] \
		|| hfl_fail "HFL_GATEWAY_DOWNLOAD_MAX_ATTEMPTS must be a positive integer" 2
	[[ "${DOWNLOAD_RETRY_DELAY_SECONDS}" =~ ^[0-9]+$ ]] \
		|| hfl_fail "HFL_GATEWAY_DOWNLOAD_RETRY_DELAY_SECONDS must be a non-negative integer" 2
	mkdir -p "$(dirname "${dest}")"
	hfl_log "Downloading ${name} from console."
	for ((attempt = 1; attempt <= DOWNLOAD_MAX_ATTEMPTS; attempt++)); do
		curl_rc=0
		if curl "${curl_tls[@]}" \
			--fail --silent --show-error --location \
			--continue-at - \
			"${GATEWAY_BOOTSTRAP_BASE}/${name}" -o "${partial}"; then
			if [[ ! -s "${partial}" ]]; then
				hfl_log "warning: ${name} download produced an empty file (attempt ${attempt}/${DOWNLOAD_MAX_ATTEMPTS})"
			else
				mv -f "${partial}" "${dest}"
				hfl_log "Downloaded ${name} ($(wc -c <"${dest}") bytes)."
				chmod +x "${dest}" 2>/dev/null || true
				return 0
			fi
		else
			curl_rc=$?
			hfl_log "warning: ${name} download interrupted (attempt ${attempt}/${DOWNLOAD_MAX_ATTEMPTS}, curl=${curl_rc}, partial=$(wc -c <"${partial}" 2>/dev/null || printf 0) bytes)"
		fi

		if [[ ("${curl_rc}" -eq 33 || "${curl_rc}" -eq 36) && -s "${partial}" ]]; then
			hfl_log "warning: server rejected the resume request for ${name}; retrying once from byte zero"
			rm -f "${partial}"
		fi
		if ((attempt < DOWNLOAD_MAX_ATTEMPTS)); then
			delay=$((DOWNLOAD_RETRY_DELAY_SECONDS * attempt))
			((delay == 0)) || sleep "${delay}"
		fi
	done
	hfl_fail "failed to download ${name} after ${DOWNLOAD_MAX_ATTEMPTS} attempts" 3
}

lensnode_image_supports_insecure_tls() {
	local ref=$1
	docker run --rm \
		-e LENSNODE_TLS_SKIP_VERIFY=1 \
		-e LENSNODE_INSECURE_TLS=1 \
		"${ref}" python -c \
		'import ssl; import lensnode.tls as tls; from lensnode.config import load_config; config = load_config(); native = getattr(config, "tls_skip_verify", False) and hasattr(tls, "create_ssl_context") and tls.create_ssl_context(skip_verify=True).verify_mode == ssl.CERT_NONE; legacy = hasattr(tls, "tls_insecure_enabled") and tls.tls_insecure_enabled(); raise SystemExit(0 if native or legacy else 1)' \
		>/dev/null 2>&1
}

load_lensnode_image() {
	local work_dir=$1 ref
	local archive="${work_dir}/${LENSNODE_IMAGE_ARCHIVE}"
	download_bootstrap_file "${LENSNODE_IMAGE_ARCHIVE}" "${archive}"
	hfl_log "Loading AI engine container image."
	docker load -i "${archive}"
	for ref in \
		"${DEFAULT_LENSNODE_IMAGE}" \
		sourcelens-lensnode:latest \
		oneprocloud/sourcelens-lensnode:latest; do
		if docker image inspect "${ref}" >/dev/null 2>&1; then
			if ! lensnode_image_supports_insecure_tls "${ref}"; then
				hfl_fail "AI engine image ${ref} is missing configurable TLS verification support" 5
			fi
			return 0
		fi
	done
	hfl_fail "AI engine image not present after docker load (expected ${DEFAULT_LENSNODE_IMAGE})" 5
}

run_sidecar_install_script() {
	local script="${1:-}"
	[[ -n "${script}" && -f "${script}" ]] || hfl_fail "sidecar install script missing" 3
	[[ -f "${LENS_ENV_FILE}" ]] || hfl_fail "missing ${LENS_ENV_FILE}" 2
	HFL_LENS_ENV_FILE="${LENS_ENV_FILE}" \
		HFL_INSECURE_TLS="${HFL_INSECURE_TLS}" \
		LENSNODE_IMAGE="${DEFAULT_LENSNODE_IMAGE}" \
		bash "${script}"
}

cmd_upgrade_sidecar() {
	local tmp="" script
	acquire_sidecar_lock
	load_agent_credentials
	report_lifecycle_status "sidecar_upgrade" "running"
	trap 'gateway_upgrade_exit "$?" "${tmp}"' EXIT
	tmp="$(mktemp -d)"
	ensure_docker_ready
	script="${tmp}/${SIDECAR_INSTALL_SCRIPT}"
	download_bootstrap_file "${SIDECAR_INSTALL_SCRIPT}" "${script}"
	load_lensnode_image "${tmp}"
	compose_down_sidecar
	run_sidecar_install_script "${script}"
	rm -rf "${tmp}"
	report_lifecycle_status "sidecar_upgrade" "success"
	trap - EXIT
	hfl_log "AI engine upgrade completed."
}

gateway_upgrade_exit() {
	local rc=$1 tmp=${2:-}
	trap - EXIT
	if [[ "${rc}" -ne 0 ]]; then
		report_lifecycle_status \
			"sidecar_upgrade" \
			"failed" \
			"${HFL_LAST_ERROR:-AI engine upgrade failed (exit ${rc})}"
	fi
	[[ -z "${tmp}" ]] || rm -rf "${tmp}"
	return "${rc}"
}

remove_lensnode_images() {
	local image
	local -A seen=()
	for image in "${OWNED_LENSNODE_IMAGES[@]}"; do
		[[ -n "${image}" && -z "${seen[${image}]:-}" ]] || continue
		seen["${image}"]=1
		if docker ps -aq --filter "ancestor=${image}" | grep -q .; then
			hfl_log "Keeping shared AI engine image ${image}; another container still references it."
			continue
		fi
		docker image rm "${image}" >/dev/null 2>&1 \
			|| hfl_log "AI engine image ${image} was absent or retained by Docker."
	done
}

validate_gateway_workspace_path() {
	local candidate=${1:-} canonical normalized
	[[ -n "${candidate}" ]] || return 1
	canonical="$(readlink -m -- "${candidate}")" || return 1
	normalized="${candidate%/}"
	[[ "${normalized}" == "${canonical}" ]] || return 1
	[[ "${canonical}" =~ ^/workspace/org-[1-9][0-9]*/data$ ]] || return 1
	printf '%s\n' "${canonical}"
}

purge_sidecar_artifacts() {
	local workspace="" state_root=""
	if [[ -f "${LENS_ENV_FILE}" ]]; then
		workspace="$(read_env_value "${LENS_ENV_FILE}" HFL_WORKSPACE_ROOT)"
	fi
	if [[ -n "${workspace}" ]]; then
		workspace="$(validate_gateway_workspace_path "${workspace}")" \
			|| hfl_fail "refusing to purge unsafe Gateway workspace path from ${LENS_ENV_FILE}" 6
	fi
	compose_down_sidecar
	remove_lensnode_images
	rm -f "${LENS_ENV_FILE}"
	rm -rf "${COMPOSE_DIR}"
	if [[ -n "${workspace}" ]]; then
		hfl_log "Removing gateway workspace ${workspace}."
		rm -rf "${workspace}"
		state_root="$(dirname "${workspace}")/.hyperfilelens"
		hfl_log "Removing protected gateway state ${state_root}."
		rm -rf "${state_root}"
	fi
}

cmd_uninstall_sidecar() {
	acquire_sidecar_lock
	if ! load_agent_credentials_optional; then
		hfl_log "Agent credentials are unavailable; continuing local AI engine cleanup without status reporting."
	fi
	ensure_docker_ready
	report_lifecycle_status "sidecar_uninstall" "running"
	if [[ "${PURGE_ALL}" -eq 1 ]]; then
		purge_sidecar_artifacts
	else
		compose_down_sidecar
	fi
	report_lifecycle_status "sidecar_uninstall" "success"
	hfl_log "AI engine uninstall completed."
}

main() {
	local cmd=${1:-}
	shift || true
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--purge-all) PURGE_ALL=1 ;;
		*) hfl_fail "unknown option: $1" 2 ;;
		esac
		shift
	done
	case "${cmd}" in
	upgrade-sidecar) cmd_upgrade_sidecar ;;
	uninstall-sidecar) cmd_uninstall_sidecar ;;
	-h | --help | help | "") usage ;;
	*) hfl_fail "unknown command: ${cmd}" 2 ;;
	esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	main "$@"
fi
