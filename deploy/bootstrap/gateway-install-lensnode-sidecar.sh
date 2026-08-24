#!/usr/bin/env bash
# SourceLens LensNode sidecar install for HyperFileLens Data Gateways.
# Invoked by hfl-enroll gateway-install after agent registration.
set -euo pipefail

ENV_FILE="${HFL_LENS_ENV_FILE:-/etc/hyperfilelens/lensnode.env}"
COMPOSE_DIR="/etc/hyperfilelens/lensnode"
COMPOSE_PROJECT="hyperfilelens-gateway"
DEFAULT_LENSNODE_IMAGE="${LENSNODE_IMAGE:-hyperfilelens-sourcelens-lensnode:latest}"
SENTRY_PRIVACY_FILE="${COMPOSE_DIR}/hfl-sentry-sitecustomize.py"
SIDECAR_LOCK_FILE="${HFL_GATEWAY_SIDECAR_LOCK_FILE:-/run/lock/hyperfilelens-gateway-sidecar.lock}"
MIN_COMPOSE_VERSION="${HFL_COMPOSE_MIN_VERSION:-2.20.0}"
COMPOSE=()

hfl_step() {
	printf '  [....] %s\n' "$1"
}

hfl_ok() {
	printf '  [ OK ] %s\n' "$1"
}

hfl_warn() {
	printf '  [WARN] %s\n' "$1" >&2
}

hfl_fail() {
	printf '  [FAIL] %s\n' "$1" >&2
	exit "${2:-1}"
}

# The sidecar script is downloaded and executed on its own, so keep this
# resolver local instead of assuming the main release installer is present.
compose_version_ge() {
	local have="${1#v}" want="${2#v}"
	have="${have#V}"; want="${want#V}"
	command -v dpkg >/dev/null 2>&1 && dpkg --compare-versions "${have}" ge "${want}" && return 0
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

if [[ "$(id -u)" -ne 0 ]]; then
	hfl_fail "Administrator privileges are required." 1
fi

acquire_sidecar_lock

if [[ ! -f "${ENV_FILE}" ]]; then
	hfl_fail "Missing ${ENV_FILE} (run hfl-enroll gateway-install first)." 2
fi

# shellcheck disable=SC1090
set -a
source "${ENV_FILE}"
set +a

for var in LENS_BASE_URL LENSNODE_TOKEN LENSNODE_UUID HFL_WORKSPACE_ROOT; do
	if [[ -z "${!var:-}" ]]; then
		hfl_fail "Missing ${var} in ${ENV_FILE}." 2
	fi
done

# host.docker.internal only resolves inside Docker; gateway install runs on the host OS.
resolve_lens_host_url() {
	local url="$1"
	url="${url//host.docker.internal/127.0.0.1}"
	printf '%s' "$url"
}

# LensNode containers reach SourceLens on the host via host.docker.internal.
resolve_lens_container_url() {
	local url="$1"
	if [[ "$url" == *127.0.0.1* || "$url" == *localhost* ]]; then
		url="${url//127.0.0.1/host.docker.internal}"
		url="${url//localhost/host.docker.internal}"
	fi
	printf '%s' "$url"
}

lens_url_needs_extra_hosts() {
	local url="$1"
	[[ "$url" == *host.docker.internal* ]]
}

LENS_HOST_URL="$(resolve_lens_host_url "${LENS_BASE_URL}")"
LENS_CONTAINER_URL="$(resolve_lens_container_url "${LENS_BASE_URL}")"
LENSNODE_NAME="${LENSNODE_NAME:-hfl-gw-sidecar}"
HFL_INSECURE_TLS="${HFL_INSECURE_TLS:-1}"

CURL_TLS=(-k)
if [[ "${HFL_INSECURE_TLS}" == "0" ]]; then
	CURL_TLS=()
fi

hfl_step "Verifying SourceLens connectivity at ${LENS_HOST_URL}."
# ${arr[@]+...} keeps empty CURL_TLS safe under `set -u` on Bash < 4.4 (CentOS 7).
curl ${CURL_TLS[@]+"${CURL_TLS[@]}"} -fsSL "${LENS_HOST_URL%/}/health" >/dev/null
hfl_ok "SourceLens health check passed."

hfl_step "Preparing Gateway workspace mounts for the AI engine."
mkdir -p "${HFL_WORKSPACE_ROOT}"
HFL_GATEWAY_STATE_ROOT="$(dirname "${HFL_WORKSPACE_ROOT}")/.hyperfilelens"
HFL_SOURCELENS_STATE_ROOT="${HFL_GATEWAY_STATE_ROOT}/sourcelens"
HFL_SOURCELENS_MOUNTPOINT="${HFL_WORKSPACE_ROOT}/.sourcelens"
HFL_GATEWAY_TRASH_ROOT="${HFL_WORKSPACE_ROOT}/.hyperfilelens-trash"
mkdir -p \
	"${HFL_GATEWAY_STATE_ROOT}/identities" \
	"${HFL_SOURCELENS_STATE_ROOT}" \
	"${HFL_SOURCELENS_MOUNTPOINT}" \
	"${HFL_GATEWAY_TRASH_ROOT}"
chmod 0700 "${HFL_GATEWAY_STATE_ROOT}" \
	"${HFL_GATEWAY_STATE_ROOT}/identities" \
	"${HFL_SOURCELENS_STATE_ROOT}" \
	"${HFL_SOURCELENS_MOUNTPOINT}" \
	"${HFL_GATEWAY_TRASH_ROOT}"

mkdir -p "${COMPOSE_DIR}"
chmod 0700 "${COMPOSE_DIR}"

prepare_sentry_privacy_adapter() {
	case "${SENTRY_ENABLED:-false}" in
	1 | true | TRUE | yes | YES | on | ON) ;;
	*) return 0 ;;
	esac
	local base="${HFL_API_BASE:-}" temporary="${SENTRY_PRIVACY_FILE}.tmp.$$"
	if [[ -z "${base}" ]]; then
		hfl_warn "Sentry privacy adapter URL is unavailable; AI engine reporting is disabled."
		SENTRY_ENABLED=false
		SENTRY_BACKEND_DSN=
		return 0
	fi
	if ! curl ${CURL_TLS[@]+"${CURL_TLS[@]}"} -fsSL \
		"${base%/}/media/gateway-bootstrap/hfl-sentry-sitecustomize.py" \
		-o "${temporary}" \
		|| ! grep -Fx '# HFL_SENTRY_PRIVACY_ADAPTER=1' "${temporary}" >/dev/null; then
		rm -f "${temporary}"
		hfl_warn "Sentry privacy adapter download failed; AI engine reporting is disabled."
		SENTRY_ENABLED=false
		SENTRY_BACKEND_DSN=
		return 0
	fi
	chmod 0644 "${temporary}"
	mv -f "${temporary}" "${SENTRY_PRIVACY_FILE}"
}

prepare_sentry_privacy_adapter

resolve_lensnode_image() {
	local candidate
	if [[ -n "${LENSNODE_IMAGE:-}" ]] && docker image inspect "${LENSNODE_IMAGE}" >/dev/null 2>&1; then
		printf '%s' "${LENSNODE_IMAGE}"
		return 0
	fi
	if command -v docker >/dev/null 2>&1; then
		for candidate in \
			hyperfilelens-sourcelens-lensnode:latest \
			sourcelens-lensnode:latest \
			oneprocloud/sourcelens-lensnode:latest \
			"${DEFAULT_LENSNODE_IMAGE}"; do
			if docker image inspect "${candidate}" >/dev/null 2>&1; then
				printf '%s' "${candidate}"
				return 0
			fi
		done
	fi
	printf '%s' "${DEFAULT_LENSNODE_IMAGE}"
}

remove_owned_legacy_gateway_containers() {
	local id project service working_dir config_files
	local removed=0
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
		hfl_step "Migrating owned legacy Gateway container ${id:0:12} from project sourcelens."
		docker rm -f "${id}" >/dev/null
		removed=1
	done < <(docker ps -aq --no-trunc)
	if [[ "${removed}" == "1" ]]; then
		hfl_ok "Owned legacy Gateway container migration completed; unrelated SourceLens projects were not touched."
	fi
}

install_docker_sidecar() {
	local image="$1"
	local ssl_verify="${LENSNODE_SSL_VERIFY:-}"
	local sentry_volume_block=""
	local compose_file="${COMPOSE_DIR}/docker-compose.yml"
	local compose_temporary="${COMPOSE_DIR}/.docker-compose.yml.tmp.$$"
	if [[ -z "${ssl_verify}" ]]; then
		if [[ "${HFL_INSECURE_TLS}" == "1" ]]; then
			ssl_verify="false"
		else
			ssl_verify="true"
		fi
	fi
	if ! docker image inspect "${image}" >/dev/null 2>&1; then
		hfl_fail "AI engine image ${image} is not available locally. Load the bundled image archive before running gateway-install." 3
	fi
	hfl_step "Installing AI engine via Docker (${image})."
	EXTRA_HOSTS_BLOCK=""
	if lens_url_needs_extra_hosts "${LENS_CONTAINER_URL}"; then
		EXTRA_HOSTS_BLOCK=$'    extra_hosts:\n      - "host.docker.internal:host-gateway"\n'
	fi
	if [[ -f "${SENTRY_PRIVACY_FILE}" ]]; then
		sentry_volume_block="      - ${SENTRY_PRIVACY_FILE}:/opt/hfl-sentry/sitecustomize.py:ro"
	fi
	(
		umask 077
		cat >"${compose_temporary}" <<EOF
name: ${COMPOSE_PROJECT}

services:
  lensnode:
    image: ${image}
    restart: unless-stopped
    stop_grace_period: 270s
    labels:
      com.hyperfilelens.managed: "true"
      com.hyperfilelens.component: "gateway-lensnode"
${EXTRA_HOSTS_BLOCK}    environment:
      LENSNODE_NAME: ${LENSNODE_NAME}
      LENSNODE_TOKEN: ${LENSNODE_TOKEN}
      LENSNODE_SERVER_URL: ${LENS_CONTAINER_URL}
      LENSNODE_WORKSPACE_PATH: ${HFL_WORKSPACE_ROOT}
      LENSNODE_CHECKPOINT_DIR: ${HFL_SOURCELENS_MOUNTPOINT}/checkpoints
      HFL_INSECURE_TLS: "${HFL_INSECURE_TLS}"
      LENSNODE_TLS_SKIP_VERIFY: "${HFL_INSECURE_TLS}"
      LENSNODE_INSECURE_TLS: "${HFL_INSECURE_TLS}"
      LENSNODE_SSL_VERIFY: "${ssl_verify}"
      LENSNODE_DRAIN_TIMEOUT_S: "240"
      PYTHONPATH: /opt/hfl-sentry
      SENTRY_COMPONENT: sourcelens-lensnode
      SENTRY_DEPLOYMENT_MODE: gateway
      SENTRY_ENABLED: "${SENTRY_ENABLED:-false}"
      SENTRY_DSN: "${SENTRY_BACKEND_DSN:-}"
      SENTRY_ENVIRONMENT: "${SENTRY_ENVIRONMENT:-}"
      SENTRY_RELEASE: "${HFL_SENTRY_LENSNODE_RELEASE:-}"
      SENTRY_TRACES_SAMPLE_RATE: "${SENTRY_TRACES_SAMPLE_RATE:-0}"
      SENTRY_SEND_DEFAULT_PII: "false"
      SENTRY_SERVICE: lensnode
    volumes:
${sentry_volume_block}
      # Workspace must be writable: LensNode document conversion writes
      # "*.sourcelens" sidecars beside restored source files. The host Agent
      # remains lifecycle owner; checkpoints/cache stay on the isolated state
      # mount below, and the Agent trash path stays hidden via tmpfs.
      - ${HFL_WORKSPACE_ROOT}:${HFL_WORKSPACE_ROOT}:rw
      - ${HFL_SOURCELENS_STATE_ROOT}:${HFL_SOURCELENS_MOUNTPOINT}:rw
    tmpfs:
      # Hide the host Agent's same-filesystem deletion quarantine from LensNode.
      - ${HFL_GATEWAY_TRASH_ROOT}:mode=0700
    mem_limit: 1536m
    cpus: 0.50
EOF
	)
	chmod 0600 "${compose_temporary}"
	mv -f "${compose_temporary}" "${compose_file}"
	chmod 0600 "${compose_file}"
	if resolve_compose; then
		remove_owned_legacy_gateway_containers
		(
			cd "${COMPOSE_DIR}"
			current_container="$("${COMPOSE[@]}" -p "${COMPOSE_PROJECT}" ps -q lensnode 2>/dev/null || true)"
			current_image_id=""
			desired_image_id="$(docker image inspect --format '{{.Id}}' "${image}")"
			if [[ -n "${current_container}" ]]; then
				current_image_id="$(docker inspect --format '{{.Image}}' "${current_container}" 2>/dev/null || true)"
			fi
			compose_args=(up -d --pull never)
			if [[ -n "${current_container}" && "${current_image_id}" != "${desired_image_id}" ]]; then
				hfl_step "Recreating the AI engine because its loaded image ID changed."
				compose_args+=(--force-recreate)
			fi
			"${COMPOSE[@]}" -p "${COMPOSE_PROJECT}" "${compose_args[@]}"
		)
	else
		hfl_fail "Docker Compose v2 >= ${MIN_COMPOSE_VERSION} is required when using the AI engine container image." 3
	fi
	hfl_ok "AI engine container started."
}

SIDECAR_MODE=""
if ! command -v docker >/dev/null 2>&1; then
	hfl_fail "Docker is required to install the AI engine." 3
fi
RESOLVED_IMAGE="$(resolve_lensnode_image)"
install_docker_sidecar "${RESOLVED_IMAGE}"
SIDECAR_MODE="docker"

hfl_ok "AI engine installation completed (${SIDECAR_MODE})."
