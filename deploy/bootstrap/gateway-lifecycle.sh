#!/usr/bin/env bash
# Data Gateway lifecycle helpers (sidecar upgrade/uninstall).
# Published under /media/gateway-bootstrap/; invoked by hfl-enroll or detached agent scripts.
set -euo pipefail

AGENT_ROOT="${HFL_AGENT_ROOT:-/opt/hyperfilelens-agent}"
ENV_FILE="${HFL_AGENT_ENV_FILE:-${AGENT_ROOT}/config/agent.env}"
LENS_ENV_FILE="${HFL_LENS_ENV_FILE:-${AGENT_ROOT}/config/lensnode.env}"
COMPOSE_DIR="${HFL_GATEWAY_COMPOSE_DIR:-${AGENT_ROOT}/runtime/lensnode}"
LEGACY_LENS_ENV_FILE="/etc/hyperfilelens/lensnode.env"
LEGACY_COMPOSE_DIR="/etc/hyperfilelens/lensnode"
LEGACY_ADOPTION_MARKER="${COMPOSE_DIR}/.hfl-legacy-layout-adopted"
LEGACY_MIGRATION_ENABLED=0
legacy_migration_allowed_for_agent() {
	[[ "${AGENT_ROOT}" == "/opt/hyperfilelens-agent" \
		&& ("${ENV_FILE}" == "/opt/hyperfilelens-agent/config/agent.env" \
			|| "${ENV_FILE}" == "/var/lib/hyperfilelens-agent/agent.env") ]]
}
if legacy_migration_allowed_for_agent; then
	LEGACY_MIGRATION_ENABLED=1
fi
if [[ "${LENS_ENV_FILE}" != "${AGENT_ROOT}/config/lensnode.env" \
	|| "${COMPOSE_DIR}" != "${AGENT_ROOT}/runtime/lensnode" ]]; then
	LEGACY_MIGRATION_ENABLED=0
fi
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
MIN_COMPOSE_VERSION="${HFL_COMPOSE_MIN_VERSION:-2.20.0}"
COMPOSE=()

hfl_log() {
	printf '  [INFO] %s\n' "$*" >&2
}

hfl_format_bytes() {
	awk -v bytes="$1" 'BEGIN {
		split("B KiB MiB GiB TiB", units, " ")
		value = bytes + 0; unit = 1
		while (value >= 1024 && unit < 5) { value /= 1024; unit++ }
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
	local label="$1" downloaded="$2" total="$3" elapsed="$4" percent=0 filled=0 rate=0 eta=0 bar
	if ((total > 0)); then
		percent=$((downloaded * 100 / total)); ((percent > 100)) && percent=100
		filled=$((percent * 20 / 100))
	fi
	((elapsed > 0)) && rate=$((downloaded / elapsed))
	bar="[$(printf '%*s' "${filled}" '' | tr ' ' '#')$(printf '%*s' "$((20 - filled))" '' | tr ' ' '-') ]"
	bar="${bar/ ]/]}"
	if ((total > 0)); then
		local eta_suffix=""
		if ((rate > 0 && downloaded < total)); then
			eta=$(((total - downloaded) / rate))
			eta_suffix=" | ETA $(hfl_format_duration "${eta}")"
		fi
		printf '  [....] %s %s | %d%% | %s / %s | %s/s%s' "${label}" "${bar}" "${percent}" \
			"$(hfl_format_bytes "${downloaded}")" "$(hfl_format_bytes "${total}")" \
			"$(hfl_format_bytes "${rate}")" "${eta_suffix}"
	else
		printf '  [....] %s %s downloaded | %s/s | elapsed %s' "${label}" "$(hfl_format_bytes "${downloaded}")" \
			"$(hfl_format_bytes "${rate}")" "$(hfl_format_duration "${elapsed}")"
	fi
}

hfl_download_header_size() {
	[[ -f "$1" ]] || { printf '0'; return 0; }
	awk 'BEGIN { IGNORECASE = 1 }
		/^Content-Length:/ { gsub("\r", "", $2); if ($2 ~ /^[0-9]+$/) size = $2 }
		/^Content-Range:/ { split($3, parts, "/"); gsub("\r", "", parts[2]); if (parts[2] ~ /^[0-9]+$/) size = parts[2] }
		END { print size + 0 }' "$1" 2>/dev/null
}

hfl_fail() {
	HFL_LAST_ERROR=$1
	printf '  [FAIL] %s\n' "$1" >&2
	exit "${2:-1}"
}

validate_lifecycle_paths() {
	[[ "${AGENT_ROOT}" == /* && "${AGENT_ROOT}" != "/" ]] \
		|| hfl_fail "HFL_AGENT_ROOT must be an absolute non-root path" 2
	[[ "${ENV_FILE}" == /* && "${ENV_FILE}" != "/" ]] \
		|| hfl_fail "HFL_AGENT_ENV_FILE must be an absolute file path" 2
	[[ "${LENS_ENV_FILE}" == /* && "${LENS_ENV_FILE}" != "/" ]] \
		|| hfl_fail "HFL_LENS_ENV_FILE must be an absolute file path" 2
	[[ "${COMPOSE_DIR}" == /* && "${COMPOSE_DIR}" != "/" ]] \
		|| hfl_fail "HFL_GATEWAY_COMPOSE_DIR must be an absolute non-root path" 2
}

compose_version_ge() {
	local have="${1#v}" want="${2#v}"
	have="${have#V}"; want="${want#V}"
	if command -v dpkg >/dev/null 2>&1; then
		dpkg --compare-versions "${have}" ge "${want}"; return
	fi
	[[ "${have}" =~ ^([0-9]+)\.([0-9]+)(\.([0-9]+))?$ ]] || return 1
	local h1=${BASH_REMATCH[1]} h2=${BASH_REMATCH[2]} h3=${BASH_REMATCH[4]:-0}
	[[ "${want}" =~ ^([0-9]+)\.([0-9]+)(\.([0-9]+))?$ ]] || return 1
	local w1=${BASH_REMATCH[1]} w2=${BASH_REMATCH[2]} w3=${BASH_REMATCH[4]:-0}
	((h1 > w1 || (h1 == w1 && (h2 > w2 || (h2 == w2 && h3 >= w3)))))
}

compose_candidate_version() {
	local -a candidate=("$@")
	local output version
	version="$("${candidate[@]}" version --short 2>/dev/null || true)"
	if [[ -z "${version}" ]]; then
		output="$("${candidate[@]}" version 2>/dev/null || true)"
		version="$(grep -Eo '[vV]?[0-9]+\.[0-9]+(\.[0-9]+)?' <<<"${output}" | head -1 || true)"
	fi
	version="${version#v}"; version="${version#V}"
	printf '%s' "${version}"
}

resolve_compose() {
	local version
	COMPOSE=()
	if command -v docker >/dev/null 2>&1; then
		version="$(compose_candidate_version docker compose)"
		if [[ -n "${version}" ]] && compose_version_ge "${version}" "${MIN_COMPOSE_VERSION}"; then
			COMPOSE=(docker compose); return 0
		fi
	fi
	if command -v docker-compose >/dev/null 2>&1; then
		version="$(compose_candidate_version docker-compose)"
		if [[ -n "${version}" ]] && compose_version_ge "${version}" "${MIN_COMPOSE_VERSION}"; then
			COMPOSE=(docker-compose); return 0
		fi
	fi
	return 1
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
	local persisted_agent_root=""
	[[ -f "${ENV_FILE}" ]] || return 1
	# Recompute legacy authority from the persisted installation identity. A
	# custom Agent Root must never inherit permission to mutate the global old
	# layout merely because this process initially used the default path.
	LEGACY_MIGRATION_ENABLED=0
	HFL_API_BASE="$(read_env_value "${ENV_FILE}" HFL_API_BASE)"
	HFL_ORG_KEY="$(read_env_value "${ENV_FILE}" HFL_ORG_KEY)"
	HFL_NODE_TOKEN="$(read_env_value "${ENV_FILE}" HFL_NODE_CREDENTIAL)"
	[[ -n "${HFL_NODE_TOKEN}" ]] \
		|| HFL_NODE_TOKEN="$(read_env_value "${ENV_FILE}" HFL_NODE_TOKEN)"
	HFL_NODE_ID="$(read_env_value "${ENV_FILE}" HFL_NODE_ID)"
	persisted_agent_root="$(read_env_value "${ENV_FILE}" HFL_AGENT_ROOT)"
	if [[ -n "${persisted_agent_root}" ]]; then
		[[ "${persisted_agent_root}" == /* ]] \
			|| hfl_fail "invalid relative HFL_AGENT_ROOT in ${ENV_FILE}" 2
		AGENT_ROOT="$(readlink -m -- "${persisted_agent_root}")" || return 1
		[[ "${AGENT_ROOT}" != "/" ]] \
			|| hfl_fail "invalid root HFL_AGENT_ROOT in ${ENV_FILE}" 2
	elif [[ "${ENV_FILE}" == */config/agent.env ]]; then
		AGENT_ROOT="$(cd -P -- "$(dirname -- "${ENV_FILE}")/.." 2>/dev/null && pwd -P || true)"
	fi
	[[ -n "${AGENT_ROOT}" ]] || AGENT_ROOT="/opt/hyperfilelens-agent"
	[[ "${AGENT_ROOT}" == /* && "${AGENT_ROOT}" != "/" ]] || return 1
	if legacy_migration_allowed_for_agent; then
		LEGACY_MIGRATION_ENABLED=1
	fi
	LENS_ENV_FILE="${HFL_LENS_ENV_FILE:-${AGENT_ROOT}/config/lensnode.env}"
	COMPOSE_DIR="${HFL_GATEWAY_COMPOSE_DIR:-${AGENT_ROOT}/runtime/lensnode}"
	LEGACY_ADOPTION_MARKER="${COMPOSE_DIR}/.hfl-legacy-layout-adopted"
	if [[ "${LENS_ENV_FILE}" != "${AGENT_ROOT}/config/lensnode.env" \
		|| "${COMPOSE_DIR}" != "${AGENT_ROOT}/runtime/lensnode" ]]; then
		LEGACY_MIGRATION_ENABLED=0
	fi
	[[ -n "${HFL_API_BASE}" && -n "${HFL_ORG_KEY}" && -n "${HFL_NODE_TOKEN}" && -n "${HFL_NODE_ID}" ]] \
		|| return 1
	GATEWAY_BOOTSTRAP_BASE="${HFL_API_BASE%/}/media/gateway-bootstrap"
	return 0
}

migrate_legacy_layout() {
	local adopted=0
	[[ "${LEGACY_MIGRATION_ENABLED}" == "1" ]] || return 0
	[[ "${LENS_ENV_FILE}" == "${LEGACY_LENS_ENV_FILE}" ]] && return 0
	if [[ -f "${LEGACY_LENS_ENV_FILE}" ]]; then
		if [[ -e "${LENS_ENV_FILE}" ]]; then
			if [[ -f "${LEGACY_ADOPTION_MARKER}" ]] || cmp -s "${LEGACY_LENS_ENV_FILE}" "${LENS_ENV_FILE}"; then
				adopted=1
			else
				hfl_fail "Legacy LensNode configuration conflicts with ${LENS_ENV_FILE}; resolve it before continuing" 2
			fi
		else
			mkdir -p "$(dirname "${LENS_ENV_FILE}")"
			install -m 0600 "${LEGACY_LENS_ENV_FILE}" "${LENS_ENV_FILE}"
			hfl_log "adopted legacy LensNode configuration into ${LENS_ENV_FILE}"
			adopted=1
		fi
	fi
	if [[ "${COMPOSE_DIR}" != "${LEGACY_COMPOSE_DIR}" && -d "${LEGACY_COMPOSE_DIR}" ]]; then
		if [[ -e "${COMPOSE_DIR}" ]]; then
			if [[ -f "${LEGACY_ADOPTION_MARKER}" ]]; then
				adopted=1
			elif [[ ! -e "${COMPOSE_DIR}/docker-compose.yml" ]]; then
				cp -a "${LEGACY_COMPOSE_DIR}/." "${COMPOSE_DIR}/"
				hfl_log "adopted legacy LensNode Compose files into ${COMPOSE_DIR}"
				adopted=1
			elif [[ -f "${LEGACY_COMPOSE_DIR}/docker-compose.yml" && -f "${COMPOSE_DIR}/docker-compose.yml" ]]; then
				cmp -s "${LEGACY_COMPOSE_DIR}/docker-compose.yml" "${COMPOSE_DIR}/docker-compose.yml" \
					|| hfl_fail "Legacy LensNode Compose configuration conflicts with ${COMPOSE_DIR}; resolve it before continuing" 2
				adopted=1
			fi
		else
			mkdir -p "${COMPOSE_DIR}"
			cp -a "${LEGACY_COMPOSE_DIR}/." "${COMPOSE_DIR}/"
			chmod 0700 "${COMPOSE_DIR}"
			hfl_log "adopted legacy LensNode Compose files into ${COMPOSE_DIR}"
			adopted=1
		fi
	fi
	if [[ "${adopted}" == "1" ]]; then
		mkdir -p "${COMPOSE_DIR}"
		chmod 0700 "${COMPOSE_DIR}"
		: >"${LEGACY_ADOPTION_MARKER}"
		chmod 0600 "${LEGACY_ADOPTION_MARKER}"
	fi
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
	local id project service working_dir config_files current_owned legacy_owned
	while IFS= read -r id; do
		[[ -n "${id}" ]] || continue
		project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "${id}" 2>/dev/null || true)"
		service="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "${id}" 2>/dev/null || true)"
		working_dir="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "${id}" 2>/dev/null || true)"
		config_files="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "${id}" 2>/dev/null || true)"
		[[ "${project}" == "sourcelens" && "${service}" == "lensnode" ]] || continue
		current_owned=0
		if [[ "${working_dir}" == "${COMPOSE_DIR}" \
			|| ",${config_files}," == *",${COMPOSE_DIR}/docker-compose.yml,"* ]]; then
			current_owned=1
		fi
		legacy_owned=0
		if [[ "${LEGACY_MIGRATION_ENABLED}" == "1" \
			&& ("${working_dir}" == "${LEGACY_COMPOSE_DIR}" \
				|| ",${config_files}," == *",${LEGACY_COMPOSE_DIR}/docker-compose.yml,"*) ]]; then
			legacy_owned=1
		fi
		[[ "${current_owned}" == "1" || "${legacy_owned}" == "1" ]] || continue
		remember_owned_lensnode_image "$(docker inspect --format '{{.Config.Image}}' "${id}" 2>/dev/null || true)"
		hfl_log "Removing owned legacy Gateway container ${id:0:12} from project sourcelens."
		docker rm -f "${id}" >/dev/null
	done < <(docker ps -aq --no-trunc)
}

compose_down_sidecar() {
	local id
	remove_owned_legacy_gateway_containers
	if [[ -f "${COMPOSE_DIR}/docker-compose.yml" ]]; then
		resolve_compose \
			|| hfl_fail "Docker Compose v2 >= ${MIN_COMPOSE_VERSION} is required to remove the AI engine" 3
		while IFS= read -r image; do
			remember_owned_lensnode_image "${image}"
		done < <(
			cd "${COMPOSE_DIR}"
			"${COMPOSE[@]}" -p "${COMPOSE_PROJECT}" config --images 2>/dev/null || true
		)
		(
			cd "${COMPOSE_DIR}"
			"${COMPOSE[@]}" -p "${COMPOSE_PROJECT}" down --remove-orphans
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
	local label="${name}" attempt curl_rc delay headers curl_pid started elapsed bytes total last_report
	case "${name}" in
		"${LENSNODE_IMAGE_ARCHIVE}") label="AI engine image bundle" ;;
		"${SIDECAR_INSTALL_SCRIPT}") label="AI engine installer" ;;
	esac
	[[ "${DOWNLOAD_MAX_ATTEMPTS}" =~ ^[1-9][0-9]*$ ]] \
		|| hfl_fail "HFL_GATEWAY_DOWNLOAD_MAX_ATTEMPTS must be a positive integer" 2
	[[ "${DOWNLOAD_RETRY_DELAY_SECONDS}" =~ ^[0-9]+$ ]] \
		|| hfl_fail "HFL_GATEWAY_DOWNLOAD_RETRY_DELAY_SECONDS must be a non-negative integer" 2
	mkdir -p "$(dirname "${dest}")"
	for ((attempt = 1; attempt <= DOWNLOAD_MAX_ATTEMPTS; attempt++)); do
		curl_rc=0
		headers="${partial}.headers.$$"
		rm -f "${headers}"
		started=${SECONDS}; last_report=0
		curl "${curl_tls[@]}" \
			--fail --silent --show-error --location \
			--continue-at - \
			--dump-header "${headers}" "${GATEWAY_BOOTSTRAP_BASE}/${name}" -o "${partial}" &
		curl_pid=$!
		while kill -0 "${curl_pid}" 2>/dev/null; do
			total="$(hfl_download_header_size "${headers}")"
			if [[ -f "${partial}" ]]; then
				bytes="$(wc -c <"${partial}")"
			else
				bytes=0
			fi
			elapsed=$((SECONDS - started))
			if ((elapsed > last_report)); then
				if [[ -t 1 && "${TERM:-}" != "dumb" && -z "${NO_COLOR:-}" ]]; then
					printf '\r%s\033[K' "$(hfl_download_progress_line "${label}" "${bytes}" "${total}" "${elapsed}")"
				else
					printf '%s\n' "$(hfl_download_progress_line "${label}" "${bytes}" "${total}" "${elapsed}")"
				fi
				last_report=${elapsed}
			fi
			sleep 1
		done
		if wait "${curl_pid}"; then
			curl_rc=0
		else
			curl_rc=$?
		fi
		if ((curl_rc == 0)); then
			if [[ ! -s "${partial}" ]]; then
				hfl_log "warning: ${name} download produced an empty file (attempt ${attempt}/${DOWNLOAD_MAX_ATTEMPTS})"
			else
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
				mv -f "${partial}" "${dest}"
				hfl_log "Downloaded ${label} ($(hfl_format_bytes "${bytes}"))."
				chmod +x "${dest}" 2>/dev/null || true
				return 0
			fi
		else
			hfl_log "warning: ${name} download interrupted (attempt ${attempt}/${DOWNLOAD_MAX_ATTEMPTS}, curl=${curl_rc}, partial=$(wc -c <"${partial}" 2>/dev/null || printf 0) bytes)"
		fi
		rm -f "${headers}"

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
	HFL_GATEWAY_COMPOSE_DIR="${COMPOSE_DIR}" \
	HFL_AGENT_ROOT="${AGENT_ROOT}" \
		HFL_INSECURE_TLS="${HFL_INSECURE_TLS}" \
		LENSNODE_IMAGE="${DEFAULT_LENSNODE_IMAGE}" \
		bash "${script}"
}

cmd_upgrade_sidecar() {
	local tmp="" script
	validate_lifecycle_paths
	acquire_sidecar_lock
	load_agent_credentials
	validate_lifecycle_paths
	report_lifecycle_status "sidecar_upgrade" "running"
	trap 'gateway_upgrade_exit "$?" "${tmp}"' EXIT
	tmp="$(mktemp -d)"
	ensure_docker_ready
	migrate_legacy_layout
	script="${tmp}/${SIDECAR_INSTALL_SCRIPT}"
	download_bootstrap_file "${SIDECAR_INSTALL_SCRIPT}" "${script}"
	load_lensnode_image "${tmp}"
	# Keep the existing container available while the downloaded installer
	# validates and starts its replacement. The installer removes an owned
	# legacy container only after the replacement is confirmed running and
	# restores the previous Compose configuration when startup fails.
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
	local candidate=${1:-} canonical normalized root_prefix
	[[ -n "${candidate}" ]] || return 1
	canonical="$(readlink -m -- "${candidate}")" || return 1
	normalized="${candidate%/}"
	[[ "${normalized}" == "${canonical}" ]] || return 1
	if [[ "${canonical}" =~ ^/workspace/org-[1-9][0-9]*/data$ ]]; then
		printf '%s\n' "${canonical}"
		return 0
	fi
	root_prefix="$(readlink -m -- "${AGENT_ROOT}/workspace")" || return 1
	if [[ "${canonical}" == "${root_prefix}"/* ]]; then
		local relative="${canonical#"${root_prefix}"/}"
		[[ "${relative}" =~ ^org-[1-9][0-9]*/data$ ]] || return 1
	else
		return 1
	fi
	printf '%s\n' "${canonical}"
}

collect_mount_targets() {
	local targets
	if command -v findmnt >/dev/null 2>&1; then
		if targets="$(LC_ALL=C findmnt -rn -o TARGET 2>/dev/null)" && [[ -n "${targets}" ]]; then
			printf '%s\n' "${targets}"
			return 0
		fi
	fi
	if [[ -r /proc/mounts ]]; then
		awk '{ print $2 }' /proc/mounts
		return 0
	fi
	return 1
}

gateway_workspace_mounts() {
	local workspace=${1:-} managed_root target canonical_target canonical_root protected_root targets
	managed_root="$(readlink -m -- "${AGENT_ROOT}/workspace")" || return 1
	targets="$(collect_mount_targets)" || return 1
	while IFS= read -r target; do
		[[ -n "${target}" ]] || continue
		canonical_target="$(readlink -m -- "${target}")" || continue
		if [[ "${canonical_target}" == "${managed_root}" || "${canonical_target}" == "${managed_root}"/* ]]; then
			printf '%s\n' "${canonical_target}"
			continue
		fi
		[[ -n "${workspace}" ]] || continue
		canonical_root="$(readlink -m -- "${workspace}")" || continue
		# The Agent keeps LensNode state beside data/ under the same org
		# directory. Protect both data and .hyperfilelens for legacy
		# /workspace layouts; the unified managed_root check already covers
		# the equivalent Agent Root tree.
		protected_root="$(dirname -- "${canonical_root}")"
		if [[ "${canonical_target}" == "${protected_root}" || "${canonical_target}" == "${protected_root}"/* ]]; then
			printf '%s\n' "${canonical_target}"
		fi
	done <<<"${targets}"
}

assert_gateway_workspace_not_mounted() {
	local workspace=${1:-} mounts
	mounts="$(gateway_workspace_mounts "${workspace}")" \
		|| hfl_fail "could not verify Gateway workspace mounts; refusing purge-all" 6
	mounts="$(printf '%s\n' "${mounts}" | sort -u)"
	[[ -z "${mounts}" ]] || hfl_fail \
		"refusing to purge mounted Gateway workspace data (${mounts//$'\n'/, }); unmount it manually and retry" 6
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
	# This check must precede container, image, configuration, and Compose
	# removal. A mounted workspace is user-owned storage, even for --purge-all.
	assert_gateway_workspace_not_mounted "${workspace}"
	compose_down_sidecar
	remove_lensnode_images
	rm -f "${LENS_ENV_FILE}"
	rm -rf "${COMPOSE_DIR}"
	if [[ "${LEGACY_MIGRATION_ENABLED}" == "1" ]]; then
		[[ "${LENS_ENV_FILE}" == "${LEGACY_LENS_ENV_FILE}" ]] || rm -f "${LEGACY_LENS_ENV_FILE}"
		[[ "${COMPOSE_DIR}" == "${LEGACY_COMPOSE_DIR}" ]] || rm -rf "${LEGACY_COMPOSE_DIR}"
	fi
	if [[ -n "${workspace}" ]]; then
		hfl_log "Removing gateway workspace ${workspace}."
		rm -rf "${workspace}"
		state_root="$(dirname "${workspace}")/.hyperfilelens"
		hfl_log "Removing protected gateway state ${state_root}."
		rm -rf "${state_root}"
	fi
}

cmd_uninstall_sidecar() {
	validate_lifecycle_paths
	acquire_sidecar_lock
	if ! load_agent_credentials_optional; then
		hfl_log "Agent credentials are unavailable; continuing local AI engine cleanup without status reporting."
		# Local uninstall must still remove the known legacy LensNode layout
		# when a standard Agent credential file is unavailable. Explicit custom
		# lifecycle paths remain isolated from the system legacy directories.
		if legacy_migration_allowed_for_agent; then
			LEGACY_MIGRATION_ENABLED=1
		fi
	fi
	if [[ "${LENS_ENV_FILE}" != "${AGENT_ROOT}/config/lensnode.env" \
		|| "${COMPOSE_DIR}" != "${AGENT_ROOT}/runtime/lensnode" ]]; then
		LEGACY_MIGRATION_ENABLED=0
	fi
	validate_lifecycle_paths
	ensure_docker_ready
	migrate_legacy_layout
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
