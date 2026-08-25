#!/usr/bin/env bash
# Shared SourceLens helpers for development and release workflows.
set -euo pipefail

_sourcelens_common_loaded="${_sourcelens_common_loaded:-}"
if [[ -n "${_sourcelens_common_loaded}" ]]; then
	return 0 2>/dev/null || exit 0
fi
_sourcelens_common_loaded=1

_sourcelens_common_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HFL_ROOT="$(cd "${_sourcelens_common_dir}/../.." && pwd)"
SOURCELENS_INSTALLER_DIR="${HFL_ROOT}/deploy/installer"
SOURCELENS_BUILD_DIR="${HFL_ROOT}/build/sourcelens"
SOURCELENS_SOURCE_CACHE="${SOURCELENS_BUILD_DIR}/source"
SOURCELENS_BUILD_SOURCE="${SOURCELENS_BUILD_DIR}/worktree"
SOURCELENS_DEV_DIR="${SOURCELENS_BUILD_DIR}/dev"
SOURCELENS_DATA_DIR="${HFL_ROOT}/data/sourcelens"
SOURCELENS_DEV_ENV_FILE="${SOURCELENS_DATA_DIR}/config/.env"
SOURCELENS_BUILD_ENV_FILE="${HFL_ROOT}/tools/sourcelens/defaults.env"
SOURCELENS_PATCH_ROOT="${HFL_ROOT}/tools/sourcelens/patches"
SOURCELENS_COMPOSE_PROJECT="${SOURCELENS_COMPOSE_PROJECT:-hyperfilelens-sourcelens}"
SOURCELENS_SHARED_NETWORK="hyperfilelens-bridge"

SOURCELENS_COMPOSE=()

# shellcheck source=../../deploy/installer/compose-runtime.sh
source "${HFL_ROOT}/deploy/installer/compose-runtime.sh"

# shellcheck source=../lib/logging.sh
source "${HFL_ROOT}/tools/lib/logging.sh"
# shellcheck source=../lib/docker-images.sh
source "${HFL_ROOT}/tools/lib/docker-images.sh"
sourcelens_log() { hfl_log_info "$@"; }
sourcelens_die() { hfl_die "$1" "${2:-1}"; }
# shellcheck source=patch-series.sh
source "${HFL_ROOT}/tools/sourcelens/patch-series.sh"

sourcelens_load_config() {
	local console_port_override="${SOURCELENS_NGINX_HTTPS_PORT:-}"
	if [[ -f "${SOURCELENS_BUILD_ENV_FILE}" ]]; then
		# shellcheck disable=SC1090
		source "${SOURCELENS_BUILD_ENV_FILE}"
	fi
	if [[ -n "${console_port_override}" ]]; then
		SOURCELENS_NGINX_HTTPS_PORT="${console_port_override}"
	fi
	BUILD_SOURCELENS="${BUILD_SOURCELENS:-1}"
	SOURCELENS_GIT_URL="${SOURCELENS_GIT_URL:-https://github.com/oneprolabs/sourcelens.git}"
	SOURCELENS_GIT_REF="${SOURCELENS_GIT_REF:-v0.47.6}"
	SOURCELENS_BUILD_COMPOSE_FILE="${SOURCELENS_BUILD_COMPOSE_FILE:-docker-compose.standalone.yml}"
	SOURCELENS_UPSTREAM_IMAGE_PREFIX="${SOURCELENS_UPSTREAM_IMAGE_PREFIX:-oneprolabs}"
	SOURCELENS_IMAGE_REGISTRY="${SOURCELENS_IMAGE_REGISTRY:-}"
	SOURCELENS_IMAGE_REGISTRY="${SOURCELENS_IMAGE_REGISTRY%/}"
	SOURCELENS_LENSNODE_IMAGE="${SOURCELENS_LENSNODE_IMAGE:-}"
	SOURCELENS_NGINX_HTTPS_PORT="${SOURCELENS_NGINX_HTTPS_PORT:-11445}"
	SOURCELENS_CONSOLE_BIND_ADDRESS="${SOURCELENS_CONSOLE_BIND_ADDRESS:-0.0.0.0}"
	SOURCELENS_CONSOLE_PORT="${SOURCELENS_CONSOLE_PORT:-${SOURCELENS_NGINX_HTTPS_PORT}}"
	SOURCELENS_INSTALL_DIR="${SOURCELENS_INSTALL_DIR:-/opt/hyperfilelens/sourcelens}"
	SOURCELENS_EMBED_LENSNODE="${SOURCELENS_EMBED_LENSNODE:-0}"
	SOURCELENS_DOCKER_PLATFORM="${SOURCELENS_DOCKER_PLATFORM:-${DOCKER_DEFAULT_PLATFORM:-linux/amd64}}"
	SOURCELENS_NGINX_SOURCE_IMAGE="${SOURCELENS_NGINX_SOURCE_IMAGE:-nginx:stable-alpine}"
	SOURCELENS_APT_MIRROR="${SOURCELENS_APT_MIRROR:-${APT_MIRROR:-${BUILD_APT_MIRROR:-}}}"
	SOURCELENS_PIP_INDEX_URL="${SOURCELENS_PIP_INDEX_URL:-${PIP_INDEX_URL:-${BUILD_PIP_INDEX_URL:-}}}"
	SOURCELENS_PIP_TRUSTED_HOST="${SOURCELENS_PIP_TRUSTED_HOST:-${PIP_TRUSTED_HOST:-${BUILD_PIP_TRUSTED_HOST:-}}}"
	SOURCELENS_UV_HTTP_TIMEOUT="${SOURCELENS_UV_HTTP_TIMEOUT:-${UV_HTTP_TIMEOUT:-${BUILD_UV_HTTP_TIMEOUT:-120}}}"
	SOURCELENS_UV_CONCURRENT_DOWNLOADS="${SOURCELENS_UV_CONCURRENT_DOWNLOADS:-${UV_CONCURRENT_DOWNLOADS:-${BUILD_UV_CONCURRENT_DOWNLOADS:-2}}}"
	SOURCELENS_PIP_RETRY_MAX="${SOURCELENS_PIP_RETRY_MAX:-${HFL_PIP_RETRY_MAX:-${BUILD_PIP_RETRY_MAX:-5}}}"
	SOURCELENS_PIP_RETRY_DELAY="${SOURCELENS_PIP_RETRY_DELAY:-${HFL_PIP_RETRY_DELAY:-${BUILD_PIP_RETRY_DELAY:-5}}}"
	SOURCELENS_UV_VERSION="${SOURCELENS_UV_VERSION:-0.10.2}"
	SOURCELENS_NPM_REGISTRY="${SOURCELENS_NPM_REGISTRY:-${NPM_REGISTRY:-${BUILD_NPM_REGISTRY:-}}}"
	SOURCELENS_BUILD_SOURCE_MAPS="${SOURCELENS_BUILD_SOURCE_MAPS:-0}"
	SOURCELENS_DOCKER_MIRROR="${SOURCELENS_DOCKER_MIRROR:-${DOCKER_DOWNLOAD_MIRROR:-}}"
	SOURCELENS_DOCKER_PULL_TIMEOUT="${SOURCELENS_DOCKER_PULL_TIMEOUT:-${DOCKER_PULL_TIMEOUT_SECONDS:-180}}"
	SOURCELENS_DOCKER_PULL_RETRIES="${SOURCELENS_DOCKER_PULL_RETRIES:-${DOCKER_PULL_RETRIES:-2}}"
	SOURCELENS_OFFLINE="${SOURCELENS_OFFLINE:-${DEV_OFFLINE:-0}}"
	SOURCELENS_FORCE_PULL="${SOURCELENS_FORCE_PULL:-0}"
	SOURCELENS_GIT_TIMEOUT_SECONDS="${SOURCELENS_GIT_TIMEOUT_SECONDS:-120}"
	SOURCELENS_GIT_RETRIES="${SOURCELENS_GIT_RETRIES:-2}"
	SOURCELENS_GIT_FALLBACK_TIMEOUT_SECONDS="${SOURCELENS_GIT_FALLBACK_TIMEOUT_SECONDS:-30}"
	GITHUB_DOWNLOAD_MIRROR="${GITHUB_DOWNLOAD_MIRROR:-}"
	GITHUB_DOWNLOAD_MIRROR="${GITHUB_DOWNLOAD_MIRROR%/}"
}

sourcelens_resolve_version() {
	[[ "${SOURCELENS_GIT_REF}" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]] \
		|| sourcelens_die "invalid SourceLens release ref: ${SOURCELENS_GIT_REF} (expected vX.Y.Z)" 2
	SOURCELENS_VERSION="${BASH_REMATCH[1]}"
	if [[ -n "${SOURCELENS_DISTRIBUTION_TAG_OVERRIDE:-}" ]]; then
		[[ "${SOURCELENS_DISTRIBUTION_TAG_OVERRIDE}" =~ ^([0-9]+\.[0-9]+\.[0-9]+|main-[0-9a-f]{7})$ ]] \
			|| sourcelens_die "invalid SourceLens distribution tag override" 2
		SOURCELENS_DISTRIBUTION_TAG="${SOURCELENS_DISTRIBUTION_TAG_OVERRIDE}"
	elif [[ "${SOURCELENS_HFL_VERSION:-}" =~ ^([0-9]+\.[0-9]+\.[0-9]+|main-[0-9a-f]{7})$ ]]; then
		SOURCELENS_DISTRIBUTION_TAG="${SOURCELENS_HFL_VERSION}-sl${SOURCELENS_VERSION}"
	else
		SOURCELENS_DISTRIBUTION_TAG="${SOURCELENS_VERSION}"
	fi
	if [[ -z "${SOURCELENS_LENSNODE_IMAGE}" ]]; then
		SOURCELENS_LENSNODE_IMAGE="$(sourcelens_lensnode_image_ref)"
	fi
}

sourcelens_strip_trailing_slashes() {
	local value="${1:-}"
	while [[ "${value}" == */ ]]; do
		value="${value%/}"
	done
	printf '%s' "${value}"
}

sourcelens_platform_uses_ubuntu_ports() {
	local platform="${1:-}"
	case "${platform}" in
	linux/arm64 | linux/arm64/* | arm64 | aarch64 | linux/arm/v* | armhf | armv*) return 0 ;;
	*) return 1 ;;
	esac
}

sourcelens_normalize_apt_mirror_url() {
	local distro=$1
	local platform="${3:-}"
	local mirror
	mirror="$(sourcelens_strip_trailing_slashes "${2:-}")"
	[[ -n "${mirror}" ]] || return 0

	case "${distro}" in
	ubuntu)
		if sourcelens_platform_uses_ubuntu_ports "${platform}"; then
			case "${mirror}" in
			*/ubuntu-ports) printf '%s' "${mirror}" ;;
			*/ubuntu) printf '%s-ports' "${mirror}" ;;
			*) printf '%s/ubuntu-ports' "${mirror}" ;;
			esac
		else
			case "${mirror}" in
			*/ubuntu) printf '%s' "${mirror}" ;;
			*/ubuntu-ports) printf '%s' "${mirror%-ports}" ;;
			*) printf '%s/ubuntu' "${mirror}" ;;
			esac
		fi
		;;
	debian)
		case "${mirror}" in
		*/debian) printf '%s' "${mirror}" ;;
		*) printf '%s/debian' "${mirror}" ;;
		esac
		;;
	*)
		sourcelens_die "unsupported apt mirror distro: ${distro}"
		;;
	esac
}

sourcelens_restore_source_dockerfiles() {
	local src=$1
	[[ -d "${src}/.git" ]] || return 0
	git -C "${src}" checkout -- \
		Dockerfile \
		lensnode/Dockerfile \
		frontend/Dockerfile \
		frontend/Dockerfile.dev 2>/dev/null || true
}

# GitHub HTTPS auth for private SourceLens clone/fetch (env GITHUB_TOKEN or --github-token).
sourcelens_git_needs_github_token() {
	local url="${1:-${SOURCELENS_GIT_URL}}"
	[[ "${url}" =~ ^https://([^/@]+@)?github\.com/ ]]
}

sourcelens_git() {
	local token="${GITHUB_TOKEN:-}"
	if [[ -n "${token}" ]]; then
		GIT_TERMINAL_PROMPT=0 \
			git \
			-c "url.https://x-access-token:${token}@github.com/.insteadOf=https://github.com/" \
			-c "url.https://x-access-token:${token}@github.com/.insteadOf=git@github.com:" \
			"$@"
	else
		GIT_TERMINAL_PROMPT=0 \
			git \
			-c "url.https://github.com/.insteadOf=https://github.com/" \
			"$@"
	fi
}

sourcelens_git_output_command() {
	local status=0
	# Git/Submodule output remains verbatim, but gets the same stream envelope
	# as other native tools. Callers use this only for human-facing commands;
	# machine-readable git queries continue to use sourcelens_git directly.
	"$@" 2>&1 | hfl_normalize_native_stream | hfl_log_output_block git \
		|| status="${PIPESTATUS[0]}"
	return "${status}"
}

sourcelens_git_mirror_enabled() {
	[[ -n "${GITHUB_DOWNLOAD_MIRROR:-}" && "${SOURCELENS_GIT_URL}" == https://github.com/* ]]
}

sourcelens_git_network_once() {
	local route=$1 timeout_seconds=$2
	shift 2
	case "${route}" in
	mirror)
		sourcelens_git_output_command timeout --foreground "${timeout_seconds}s" env GIT_TERMINAL_PROMPT=0 git \
			-c "url.${GITHUB_DOWNLOAD_MIRROR}/https://github.com/.insteadOf=https://github.com/" "$@"
		;;
	official)
		if [[ -n "${GITHUB_TOKEN:-}" ]]; then
			sourcelens_git_output_command timeout --foreground "${timeout_seconds}s" env GIT_TERMINAL_PROMPT=0 git \
				-c "url.https://x-access-token:${GITHUB_TOKEN}@github.com/.insteadOf=https://github.com/" \
				-c "url.https://x-access-token:${GITHUB_TOKEN}@github.com/.insteadOf=git@github.com:" "$@"
		else
			sourcelens_git_output_command timeout --foreground "${timeout_seconds}s" env GIT_TERMINAL_PROMPT=0 git \
				-c "url.https://github.com/.insteadOf=https://github.com/" "$@"
		fi
		;;
	*) sourcelens_die "invalid SourceLens Git network route: ${route}" 2 ;;
	esac
}

sourcelens_git_network() {
	local attempt timeout_seconds="${SOURCELENS_GIT_TIMEOUT_SECONDS}" retries="${SOURCELENS_GIT_RETRIES}"
	local fallback_timeout_seconds="${SOURCELENS_GIT_FALLBACK_TIMEOUT_SECONDS}"
	local clone_dest=""
	[[ "${1:-}" != "clone" ]] || clone_dest="${!#}"
	[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ \
		&& "${retries}" =~ ^[1-9][0-9]*$ \
		&& "${fallback_timeout_seconds}" =~ ^[1-9][0-9]*$ ]] \
		|| sourcelens_die "SourceLens Git timeout and retries must be positive integers" 2
	if sourcelens_git_mirror_enabled; then
		sourcelens_log "Using SourceLens Git mirror ${GITHUB_DOWNLOAD_MIRROR}"
		for attempt in $(seq 1 "${retries}"); do
			if [[ -n "${clone_dest}" && "${attempt}" -gt 1 ]]; then
				rm -rf "${clone_dest}"
			fi
			if sourcelens_git_network_once mirror "${timeout_seconds}" "$@"; then
				return 0
			fi
			sourcelens_log "SourceLens Git mirror command failed or timed out (attempt ${attempt}/${retries})"
		done
		[[ -z "${clone_dest}" ]] || rm -rf "${clone_dest}"
		sourcelens_log "SourceLens Git mirror unavailable; retrying official GitHub once (timeout=${fallback_timeout_seconds}s)"
		if sourcelens_git_network_once official "${fallback_timeout_seconds}" "$@"; then
			return 0
		fi
		sourcelens_log "SourceLens official GitHub command failed or timed out"
		[[ -z "${clone_dest}" ]] || rm -rf "${clone_dest}"
		return 1
	fi
	for attempt in $(seq 1 "${retries}"); do
		if [[ -n "${clone_dest}" && "${attempt}" -gt 1 ]]; then
			rm -rf "${clone_dest}"
		fi
		if sourcelens_git_network_once official "${timeout_seconds}" "$@"; then
			return 0
		fi
		sourcelens_log "SourceLens Git command failed or timed out (attempt ${attempt}/${retries})"
	done
	[[ -z "${clone_dest}" ]] || rm -rf "${clone_dest}"
	return 1
}

sourcelens_sync_submodules() {
	sourcelens_log "Synchronizing SourceLens submodules with forced worktree recovery"
	sourcelens_git_output_command sourcelens_git submodule sync --recursive
	if [[ "${SOURCELENS_OFFLINE}" -eq 1 ]]; then
		sourcelens_git_output_command sourcelens_git \
			submodule update --init --recursive --force --no-fetch
	else
		sourcelens_git_network submodule update --init --recursive --force
	fi
}

sourcelens_image_exists() {
	docker image inspect "$1" >/dev/null 2>&1
}

sourcelens_image_digest() {
	local image=$1 digest
	digest="$(docker image inspect "${image}" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
	[[ -n "${digest}" ]] \
		|| digest="$(docker image inspect "${image}" --format '{{.Id}}' 2>/dev/null || true)"
	[[ -n "${digest}" ]] || digest="${image}"
	printf '%s' "${digest}"
}

sourcelens_lensnode_image_id() {
	local ref=$1
	docker image inspect "${ref}" --format '{{.Id}}' 2>/dev/null || true
}

sourcelens_lensnode_supports_insecure_tls() {
	local ref=$1
	docker run --rm \
		-e LENSNODE_TLS_SKIP_VERIFY=1 \
		-e LENSNODE_INSECURE_TLS=1 \
		"${ref}" python -c \
		'import ssl; import lensnode.tls as tls; from lensnode.config import load_config; config = load_config(); native = getattr(config, "tls_skip_verify", False) and hasattr(tls, "create_ssl_context") and tls.create_ssl_context(skip_verify=True).verify_mode == ssl.CERT_NONE; legacy = hasattr(tls, "tls_insecure_enabled") and tls.tls_insecure_enabled(); raise SystemExit(0 if native or legacy else 1)' \
		>/dev/null 2>&1
}

sourcelens_gateway_lensnode_bundle_image_id() {
	local archive=$1
	python3 - "${archive}" <<'PY'
import gzip
import json
import sys
import tarfile

path = sys.argv[1]
try:
    with gzip.open(path, "rb") as gz:
        with tarfile.open(fileobj=gz, mode="r:") as tar:
            member = tar.getmember("index.json")
            data = json.load(tar.extractfile(member))
except (
    EOFError,
    OSError,
    KeyError,
    gzip.BadGzipFile,
    json.JSONDecodeError,
    tarfile.TarError,
):
    sys.exit(0)

manifests = data.get("manifests") or []
if not manifests:
    sys.exit(0)
digest = manifests[0].get("digest", "")
if digest.startswith("sha256:"):
    print(digest)
PY
}

sourcelens_ensure_compose() {
	if ((${#SOURCELENS_COMPOSE[@]})); then
		return 0
	fi
	hfl_compose_resolve 2.20.0 \
		|| sourcelens_die "supported Docker Compose command not found; $(hfl_compose_failure_detail 2.20.0)"
	SOURCELENS_COMPOSE=("${HFL_COMPOSE[@]}")
}

sourcelens_ensure_shared_network() {
	if docker network inspect "${SOURCELENS_SHARED_NETWORK}" >/dev/null 2>&1; then
		return 0
	fi
	sourcelens_log "Creating shared bridge network ${SOURCELENS_SHARED_NETWORK}"
	docker network create "${SOURCELENS_SHARED_NETWORK}" >/dev/null
}

sourcelens_ensure_tls_certs() {
	local cert_dir=$1
	local cert="${cert_dir}/tls.crt"
	local key="${cert_dir}/tls.key"
	[[ -s "${cert}" && -s "${key}" ]] \
		|| sourcelens_die "shared HyperFileLens TLS certificate and key are required under ${cert_dir#${HFL_ROOT}/}"
	command -v openssl >/dev/null 2>&1 \
		|| sourcelens_die "openssl is required to validate shared TLS certificates"
	local cert_pub key_pub
	cert_pub="$(openssl x509 -in "${cert}" -pubkey -noout 2>/dev/null | sha256sum | cut -d' ' -f1)"
	key_pub="$(openssl pkey -in "${key}" -pubout 2>/dev/null | sha256sum | cut -d' ' -f1)"
	[[ -n "${cert_pub}" && "${cert_pub}" == "${key_pub}" ]] \
		|| sourcelens_die "shared HyperFileLens TLS certificate and key do not match"
	chmod 644 "${cert}"
	chmod 600 "${key}"
}

sourcelens_sync_source() {
	command -v git >/dev/null 2>&1 || sourcelens_die "git not found (required to fetch SourceLens)"
	if sourcelens_git_needs_github_token && [[ -z "${GITHUB_TOKEN:-}" ]]; then
		sourcelens_log "Note: SOURCELENS_GIT_URL uses GitHub HTTPS; set GITHUB_TOKEN or pass --github-token for private repo clone"
	elif sourcelens_git_needs_github_token && [[ -n "${GITHUB_TOKEN:-}" ]]; then
		sourcelens_log "Using GITHUB_TOKEN for GitHub HTTPS auth"
	fi
	mkdir -p "$(dirname "${SOURCELENS_SOURCE_CACHE}")"
	if [[ -d "${SOURCELENS_SOURCE_CACHE}" && ! -d "${SOURCELENS_SOURCE_CACHE}/.git" ]]; then
		sourcelens_log "Removing incomplete SourceLens clone at ${SOURCELENS_SOURCE_CACHE#${HFL_ROOT}/}"
		rm -rf "${SOURCELENS_SOURCE_CACHE}"
	fi
	if [[ "${SOURCELENS_OFFLINE}" -eq 1 && ! -d "${SOURCELENS_SOURCE_CACHE}/.git" ]]; then
		sourcelens_die "SourceLens source cache is missing and offline mode forbids cloning"
	fi
	if [[ ! -d "${SOURCELENS_SOURCE_CACHE}/.git" ]]; then
		sourcelens_log "Cloning ${SOURCELENS_GIT_URL} (${SOURCELENS_GIT_REF})..."
		sourcelens_git_network clone "${SOURCELENS_GIT_URL}" "${SOURCELENS_SOURCE_CACHE}"
	fi
	git -C "${SOURCELENS_SOURCE_CACHE}" reset --hard HEAD >/dev/null
	rm -f "${SOURCELENS_SOURCE_CACHE}/.hfl-built-commit"
	git -C "${SOURCELENS_SOURCE_CACHE}" clean -fdx >/dev/null
	sourcelens_log "Checking out SourceLens ref ${SOURCELENS_GIT_REF}..."
	(
		cd "${SOURCELENS_SOURCE_CACHE}"
		if [[ "${SOURCELENS_OFFLINE}" -eq 1 ]]; then
			fetch_succeeded=0
			sourcelens_log "Offline mode: using the existing SourceLens source cache"
		elif sourcelens_git_network fetch origin --tags --prune; then
			fetch_succeeded=1
		else
			fetch_succeeded=0
			sourcelens_log "SourceLens fetch failed; using the existing local source cache"
		fi
		if sourcelens_git show-ref --verify --quiet "refs/heads/${SOURCELENS_GIT_REF}"; then
			sourcelens_git_output_command sourcelens_git checkout "${SOURCELENS_GIT_REF}"
			if [[ "${fetch_succeeded}" -eq 1 ]]; then
				sourcelens_git_network pull --ff-only origin "${SOURCELENS_GIT_REF}" || true
			fi
		elif sourcelens_git show-ref --verify --quiet "refs/tags/${SOURCELENS_GIT_REF}"; then
			sourcelens_git_output_command sourcelens_git checkout "${SOURCELENS_GIT_REF}"
		else
			sourcelens_git_output_command sourcelens_git checkout "${SOURCELENS_GIT_REF}"
		fi
		sourcelens_sync_submodules
		sourcelens_git_output_command sourcelens_git submodule foreach --recursive \
			'git reset --hard HEAD >/dev/null && git clean -fdx >/dev/null'
	)
	sourcelens_restore_source_dockerfiles "${SOURCELENS_SOURCE_CACHE}"
	if [[ -n "$(git -C "${SOURCELENS_SOURCE_CACHE}" status --porcelain)" ]]; then
		sourcelens_die "SourceLens source cache is not pristine after synchronization"
	fi
	sourcelens_prepare_build_source
}

sourcelens_prepare_build_source() {
	[[ "${SOURCELENS_BUILD_SOURCE}" == "${SOURCELENS_BUILD_DIR}/worktree" ]] \
		|| sourcelens_die "unsafe SourceLens disposable build path"
	rm -rf "${SOURCELENS_BUILD_SOURCE}"
	mkdir -p "${SOURCELENS_BUILD_SOURCE}"
	rsync -a --delete --exclude='.git' \
		"${SOURCELENS_SOURCE_CACHE}/" "${SOURCELENS_BUILD_SOURCE}/"
	sourcelens_apply_hfl_patch_series "${SOURCELENS_BUILD_SOURCE}"
}

sourcelens_built_commit_stamp() {
	printf '%s/.build-stamp' "${SOURCELENS_BUILD_DIR}"
}

sourcelens_read_built_commit() {
	local stamp
	stamp="$(sourcelens_built_commit_stamp)"
	[[ -f "${stamp}" ]] || return 0
	tr -d ' \n\r' <"${stamp}"
}

sourcelens_write_built_commit() {
	local commit=$1
	local stamp
	stamp="$(sourcelens_built_commit_stamp)"
	mkdir -p "$(dirname "${stamp}")"
	printf '%s\n' "${commit}" >"${stamp}"
}

sourcelens_current_build_stamp() {
	local commit patch_digest adapter_digest build_inputs_digest
	commit="$(git -C "${SOURCELENS_SOURCE_CACHE}" rev-parse HEAD 2>/dev/null || true)"
	patch_digest="$(sourcelens_patchset_digest)"
	adapter_digest="$(sourcelens_build_adapter_digest)"
	build_inputs_digest="$(sourcelens_effective_build_inputs_digest)"
	printf 'v3:%s:%s:%s:%s:%s:%s' \
		"${SOURCELENS_VERSION}" "${commit}" "${patch_digest}" \
		"${SOURCELENS_DOCKER_PLATFORM}" "${SOURCELENS_BUILD_COMPOSE_FILE}:${adapter_digest}" \
		"${build_inputs_digest}"
}

sourcelens_effective_build_inputs_digest() {
	local scope="${1:-all}"
	case "${scope}" in all | backend | frontend | lensnode) ;; *) return 2 ;; esac
	local debian_apt_mirror_url="https://deb.debian.org/debian"
	local pip_index_url="${SOURCELENS_PIP_INDEX_URL:-https://pypi.org/simple}"
	local pip_trusted_host="${SOURCELENS_PIP_TRUSTED_HOST:-pypi.org}"
	if [[ -n "${SOURCELENS_APT_MIRROR:-}" ]]; then
		debian_apt_mirror_url="$(sourcelens_normalize_apt_mirror_url \
			debian "${SOURCELENS_APT_MIRROR}" "${SOURCELENS_DOCKER_PLATFORM}")"
	fi
	python3 - \
		"${scope}" \
		"${debian_apt_mirror_url}" \
		"${pip_index_url}" \
		"${pip_trusted_host}" \
		"${SOURCELENS_UV_HTTP_TIMEOUT}" \
		"${SOURCELENS_UV_CONCURRENT_DOWNLOADS}" \
		"${SOURCELENS_PIP_RETRY_MAX}" \
		"${SOURCELENS_PIP_RETRY_DELAY}" \
		"${SOURCELENS_UV_VERSION}" \
		"${SOURCELENS_NPM_REGISTRY}" \
		"${SOURCELENS_BUILD_SOURCE_MAPS}" \
		"${SOURCELENS_UPSTREAM_IMAGE_PREFIX}" \
		"${APP_RELEASE_DATE:-}" \
		"${VITE_API_BASE_URL:-}" \
		"${VITE_TURNSTILE_SITE_KEY:-}" \
		"${VITE_SENTRY_DSN:-}" \
		"${VITE_SENTRY_ENVIRONMENT:-}" \
		"${VITE_SENTRY_TRACES_SAMPLE_RATE:-}" \
		"${VITE_SENTRY_SEND_DEFAULT_PII:-}" \
		"${VITE_GA_ID:-}" <<'PY'
import hashlib
import json
import sys

scope = sys.argv[1]
names = (
    "debian_apt_mirror_url",
    "pip_index_url",
    "pip_trusted_host",
    "uv_http_timeout",
    "uv_concurrent_downloads",
    "pip_retry_max",
    "pip_retry_delay",
    "uv_version",
    "npm_registry",
    "build_source_maps",
    "upstream_image_prefix",
    "app_release_date",
    "vite_api_base_url",
    "vite_turnstile_site_key",
    "vite_sentry_dsn",
    "vite_sentry_environment",
    "vite_sentry_traces_sample_rate",
    "vite_sentry_send_default_pii",
    "vite_ga_id",
)
values = sys.argv[2:]
if len(values) != len(names):
    raise SystemExit("unexpected SourceLens build input count")
payload = dict(zip(names, values))
scoped_names = {
    "backend": {
        "debian_apt_mirror_url",
        "pip_index_url",
        "pip_trusted_host",
        "uv_http_timeout",
        "uv_concurrent_downloads",
        "pip_retry_max",
        "pip_retry_delay",
        "uv_version",
        "upstream_image_prefix",
    },
    "lensnode": {
        "debian_apt_mirror_url",
        "pip_index_url",
        "pip_trusted_host",
        "uv_http_timeout",
        "uv_concurrent_downloads",
        "pip_retry_max",
        "pip_retry_delay",
        "uv_version",
        "npm_registry",
        "upstream_image_prefix",
    },
    "frontend": {
        "npm_registry",
        "build_source_maps",
        "upstream_image_prefix",
        "app_release_date",
        "vite_api_base_url",
        "vite_turnstile_site_key",
        "vite_sentry_dsn",
        "vite_sentry_environment",
        "vite_sentry_traces_sample_rate",
        "vite_sentry_send_default_pii",
        "vite_ga_id",
    },
}
if scope != "all":
    payload = {name: payload[name] for name in scoped_names[scope]}
canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
print(hashlib.sha256(canonical.encode("utf-8")).hexdigest())
PY
}

sourcelens_component_build_identity() {
	local component=$1 source_commit=$2
	case "${component}" in backend | frontend | lensnode) ;; *) return 2 ;; esac
	printf '%s\n' \
		"component=${component}" \
		"platform=${SOURCELENS_DOCKER_PLATFORM}" \
		"source_commit=${source_commit}" \
		"source_version=${SOURCELENS_VERSION}" \
		"build_compose_file=${SOURCELENS_BUILD_COMPOSE_FILE}" \
		"effective_build_inputs=$(sourcelens_effective_build_inputs_digest "${component}")"
}

sourcelens_build_adapter_digest() {
	python3 - "${HFL_ROOT}" <<'PY'
import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
files = (
    "tools/sourcelens/common.sh",
    "tools/sourcelens/patch-series.sh",
    "tools/sourcelens/defaults.env",
    "release/build-sourcelens.sh",
    "release/ci/build-sourcelens-image.sh",
)
digest = hashlib.sha256()
for relative in files:
    path = root / relative
    digest.update(relative.encode("utf-8"))
    digest.update(b"\0")
    digest.update(path.read_bytes())
    digest.update(b"\0")
print(digest.hexdigest())
PY
}

sourcelens_build_compose_path() {
	local src=$1 relative="${SOURCELENS_BUILD_COMPOSE_FILE}"
	case "${relative}" in
	/* | *..*) sourcelens_die "invalid SourceLens build Compose path: ${relative}" ;;
	esac
	[[ -f "${src}/${relative}" ]] \
		|| sourcelens_die "missing SourceLens build Compose file: ${relative}"
	printf '%s' "${src}/${relative}"
}

sourcelens_restore_compose_file() {
	local src=$1 compose
	[[ -d "${src}/.git" ]] || return 0
	compose="$(sourcelens_build_compose_path "${src}")"
	git -C "${src}" checkout -- "${compose#${src}/}" 2>/dev/null || true
}

sourcelens_patch_compose_lensnode_apt_mirror() {
	local src=$1
	local debian_default=$2
	local compose
	compose="$(sourcelens_build_compose_path "${src}")"
	python3 - "${compose}" "${debian_default}" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
debian_default = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
out = []
service = None
in_lensnode_build = False
in_lensnode_args = False
has_apt = False
insert_after_idx = None

for idx, line in enumerate(lines):
    stripped = line.strip()
    if line.startswith("  ") and not line.startswith("    ") and stripped.endswith(":"):
        service = stripped[:-1]
        in_lensnode_build = False
        in_lensnode_args = False
    if service == "lensnode" and stripped == "build:":
        in_lensnode_build = True
    if service == "lensnode" and in_lensnode_build and stripped == "args:":
        in_lensnode_args = True
        has_apt = False
        insert_after_idx = None
    if service == "lensnode" and in_lensnode_args:
        if stripped.startswith("APT_MIRROR_URL:"):
            has_apt = True
            indent = line[: len(line) - len(line.lstrip())]
            line = (
                f"{indent}APT_MIRROR_URL: "
                f"${{DEBIAN_APT_MIRROR_URL:-{debian_default}}}\n"
            )
        elif line.startswith("        ") and ":" in stripped:
            insert_after_idx = len(out)
        elif stripped != "args:" and stripped and not line.startswith("        "):
            in_lensnode_args = False
    out.append(line)

if not has_apt and insert_after_idx is not None:
    out.insert(
        insert_after_idx + 1,
        f"        APT_MIRROR_URL: ${{DEBIAN_APT_MIRROR_URL:-{debian_default}}}\n",
    )

path.write_text("".join(out), encoding="utf-8")
PY
}

sourcelens_patch_compose_build_sources() {
	local src=$1
	local ubuntu_default=$2
	local debian_default=$3
	local pip_index_default=$4
	local pip_trusted_host_default=$5
	local compose
	compose="$(sourcelens_build_compose_path "${src}")"
	python3 - "${compose}" "${ubuntu_default}" "${debian_default}" \
		"${pip_index_default}" "${pip_trusted_host_default}" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
_ubuntu_default, debian_default, pip_index_default, pip_trusted_host_default = (
    sys.argv[2:6]
)
service_settings = {
    "backend-api": {
        "APT_MIRROR_URL": ("APT_MIRROR_URL", debian_default),
        "PIP_INDEX_URL": ("PIP_INDEX_URL", pip_index_default),
        "PIP_TRUSTED_HOST": ("PIP_TRUSTED_HOST", pip_trusted_host_default),
    },
    "lensnode": {
        "APT_MIRROR_URL": ("DEBIAN_APT_MIRROR_URL", debian_default),
        "PIP_INDEX_URL": ("PIP_INDEX_URL", pip_index_default),
        "PIP_TRUSTED_HOST": ("PIP_TRUSTED_HOST", pip_trusted_host_default),
    },
}
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
out = []
service = None
in_build = False
in_args = False
seen = set()

for line in lines:
    stripped = line.strip()
    if line.startswith("  ") and not line.startswith("    ") and stripped.endswith(":"):
        service = stripped[:-1]
        in_build = False
        in_args = False
    if service in service_settings and stripped == "build:":
        in_build = True
    if service in service_settings and in_build and stripped == "args:":
        in_args = True
    elif in_args and stripped and not line.startswith("        "):
        in_args = False
    if service in service_settings and in_args:
        for name, (environment_name, default) in service_settings[service].items():
            if stripped.startswith(f"{name}:"):
                indent = line[: len(line) - len(line.lstrip())]
                line = f"{indent}{name}: ${{{environment_name}:-{default}}}\n"
                seen.add((service, name))
                break
    out.append(line)

expected = {
    (service_name, setting_name)
    for service_name, settings in service_settings.items()
    for setting_name in settings
}
missing = sorted(expected - seen)
if missing:
    details = ", ".join(f"{service}.{name}" for service, name in missing)
    raise SystemExit(f"ERROR: could not patch SourceLens Compose build sources: {details}")

path.write_text("".join(out), encoding="utf-8")
PY
}

sourcelens_patch_dockerfile_uv_network() {
	local src=$1
	local timeout=$2
	local concurrent=$3
	local uv_version=$4
	local dockerfile
	for dockerfile in "${src}/Dockerfile" "${src}/lensnode/Dockerfile"; do
		[[ -f "${dockerfile}" ]] || continue
		python3 - "${dockerfile}" "${timeout}" "${concurrent}" "${uv_version}" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
timeout, concurrent, uv_version = sys.argv[2:5]
settings = [
    ("UV_HTTP_TIMEOUT", timeout),
    ("UV_CONCURRENT_DOWNLOADS", concurrent),
    ("UV_VERSION", uv_version),
]
text = path.read_text(encoding="utf-8")
if "ARG UV_VERSION" not in text:
    marker = re.search(r"(?m)^(?P<indent>[ \t]*)ARG PIP_TRUSTED_HOST[^\n]*\n", text)
    if marker is None:
        raise SystemExit(f"ERROR: could not find pip build arguments in {path}")
    indent = marker.group("indent")
    additions = "".join(
        f"{indent}ARG {name}={default}\n" for name, default in settings
    )
    additions += (
        f"{indent}ENV UV_HTTP_TIMEOUT=${{UV_HTTP_TIMEOUT}} \\\n"
        f"{indent}    UV_CONCURRENT_DOWNLOADS=${{UV_CONCURRENT_DOWNLOADS}}\n"
    )
    text = text[: marker.end()] + additions + text[marker.end() :]

updated = re.sub(
    r'(?m)^(?P<indent>[ \t]*)uv(?P<suffix>;?[ \t]*\\)$',
    r'\g<indent>"uv==${UV_VERSION}"\g<suffix>',
    text,
    count=1,
)
for name, _default in settings:
    if f"ARG {name}" not in updated:
        raise SystemExit(f"ERROR: could not patch {name} in {path}")
for name in ("UV_HTTP_TIMEOUT", "UV_CONCURRENT_DOWNLOADS"):
    if f"{name}=${{{name}}}" not in updated:
        raise SystemExit(f"ERROR: could not expose {name} in {path}")
if '"uv==${UV_VERSION}"' not in updated:
    raise SystemExit(f"ERROR: could not pin uv in {path}")

path.write_text(updated, encoding="utf-8")
PY
	done
}

sourcelens_patch_dockerfile_pip_resilience() {
	local src=$1
	local retry_max=$2
	local retry_delay=$3
	local dockerfile
	for dockerfile in "${src}/Dockerfile" "${src}/lensnode/Dockerfile"; do
		[[ -f "${dockerfile}" ]] || continue
		python3 - "${dockerfile}" "${retry_max}" "${retry_delay}" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
retry_max, retry_delay = sys.argv[2:4]
text = path.read_text(encoding="utf-8")
if "hfl-pip-resilience" in text:
    sys.exit(0)

if not text.lstrip().startswith("# syntax="):
    text = "# syntax=docker/dockerfile:1.4\n# hfl-pip-resilience\n" + text

mount_run = (
    "RUN --mount=type=cache,target=/opt/hfl-build-cache/uv,sharing=locked "
    "set -eux; \\\n"
)
inject = (
    f"    export UV_CACHE_DIR=/opt/hfl-build-cache/uv; \\\n"
    f"    export HFL_PIP_RETRY_MAX={retry_max}; \\\n"
    f"    export HFL_PIP_RETRY_DELAY={retry_delay}; \\\n"
    "    hfl_retry() { max=${HFL_PIP_RETRY_MAX}; delay=${HFL_PIP_RETRY_DELAY}; n=1; "
    'while [ "$n" -le "$max" ]; do if "$@"; then return 0; fi; '
    'echo "[hfl] pip/uv attempt ${n}/${max} failed, retrying..."; '
    'n=$((n+1)); [ "$n" -le "$max" ] && sleep "$delay"; done; return 1; }; \\\n'
)

def is_run_start(line: str) -> bool:
    return line.lstrip().startswith("RUN ")


def instruction_end(lines, start):
    """Return the exclusive end of one non-heredoc Docker instruction."""

    idx = start
    while idx < len(lines):
        if not lines[idx].rstrip().endswith("\\"):
            return idx + 1
        idx += 1
    return len(lines)

lines = text.splitlines(keepends=True)
out = []
idx = 0
while idx < len(lines):
    line = lines[idx]
    if is_run_start(line):
        j = instruction_end(lines, idx)
        block = lines[idx:j]
        block_text = "".join(block)
        if "hfl-pip-resilience-applied" in block_text:
            out.append(block_text)
            idx = j
            continue
        if "uv pip install" in block_text or "uv pip compile" in block_text:
            if block[0].lstrip().startswith("RUN --mount="):
                block.insert(1, inject)
            elif block[0].lstrip().startswith("RUN set -eux;"):
                block[0] = mount_run
                block.insert(1, inject)
            else:
                command = block[0].lstrip()[len("RUN ") :]
                block[0] = mount_run
                block.insert(1, inject)
                block.insert(2, f"    {command}")
            block_text = "".join(block)
            block_text = block_text.replace("uv pip compile", "hfl_retry uv pip compile")
            block_text = block_text.replace("uv pip install", "hfl_retry uv pip install")
            if not block_text.rstrip().endswith("# hfl-pip-resilience-applied"):
                block_text = block_text.rstrip("\n") + "\n# hfl-pip-resilience-applied\n"
            out.append(block_text)
            idx = j
            continue
    out.append(line)
    idx += 1

path.write_text("".join(out), encoding="utf-8")
PY
	done
}

sourcelens_patch_compose_uv_network() {
	local src=$1
	local timeout=$2
	local concurrent=$3
	local uv_version=$4
	local compose
	compose="$(sourcelens_build_compose_path "${src}")"
	python3 - "${compose}" "${timeout}" "${concurrent}" "${uv_version}" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
timeout, concurrent, uv_version = sys.argv[2:5]
services = {"backend-api", "lensnode"}
settings = [
    ("UV_HTTP_TIMEOUT", timeout),
    ("UV_CONCURRENT_DOWNLOADS", concurrent),
    ("UV_VERSION", uv_version),
]
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
out = []
service = None
in_build = False
in_args = False
present = set()
insert_after_idx = None

for line in lines:
    stripped = line.strip()
    if line.startswith("  ") and not line.startswith("    ") and stripped.endswith(":"):
        if in_args and insert_after_idx is not None:
            offset = 1
            for name, default in (item for item in settings if item[0] not in present):
                out.insert(
                    insert_after_idx + offset,
                    f"        {name}: ${{{name}:-{default}}}\n",
                )
                offset += 1
        service = stripped[:-1]
        in_build = False
        in_args = False
        present = set()
    if service in services and stripped == "build:":
        in_build = True
    if service in services and in_build and stripped == "args:":
        in_args = True
        present = set()
        insert_after_idx = None
    if service in services and in_args:
        for name, default in settings:
            if stripped.startswith(f"{name}:"):
                present.add(name)
                indent = line[: len(line) - len(line.lstrip())]
                line = f"{indent}{name}: ${{{name}:-{default}}}\n"
        if line.startswith("        ") and ":" in stripped:
            insert_after_idx = len(out)
        elif stripped != "args:" and stripped and not line.startswith("        "):
            missing = [item for item in settings if item[0] not in present]
            if missing and insert_after_idx is not None:
                offset = 1
                for name, default in missing:
                    out.insert(
                        insert_after_idx + offset,
                        f"        {name}: ${{{name}:-{default}}}\n",
                    )
                    offset += 1
            in_args = False
    out.append(line)

if in_args and insert_after_idx is not None:
    offset = 1
    for name, default in (item for item in settings if item[0] not in present):
        out.insert(
            insert_after_idx + offset,
            f"        {name}: ${{{name}:-{default}}}\n",
        )
        offset += 1

path.write_text("".join(out), encoding="utf-8")
PY
}

sourcelens_patch_frontend_dockerfile_npm_registry() {
	local src=$1
	local dockerfile="${src}/frontend/Dockerfile"
	[[ -f "${dockerfile}" ]] || return 0
	python3 - "${dockerfile}" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)
out = []
inserted_arg = "ARG NPM_REGISTRY" in text
inserted_run = "fetch-retries" in text
for line in lines:
    stripped = line.strip()
    if not inserted_arg and stripped.startswith("ARG VITE_GA_ID"):
        out.append(line)
        out.append("ARG NPM_REGISTRY\n")
        inserted_arg = True
        continue
    if (
        not inserted_run
        and stripped.startswith("RUN npm ci")
        and "npm config set registry" not in text
    ):
        out.append(
            'RUN npm config set audit false; \\\n'
            '    npm config set fund false; \\\n'
            '    npm config set update-notifier false; \\\n'
            '    npm config set fetch-retries 5; \\\n'
            '    npm config set fetch-retry-mintimeout 20000; \\\n'
            '    npm config set fetch-retry-maxtimeout 120000; \\\n'
            '    if [ -n "${NPM_REGISTRY}" ]; then \\\n'
            '      npm config set registry "${NPM_REGISTRY}"; \\\n'
            '    fi\n'
        )
        inserted_run = True
    elif (
        not inserted_run
        and "npm config set registry" in stripped
        and "fetch-retries" not in text
    ):
        out.append(line.rstrip("\n").rstrip("\\").rstrip() + " ; \\\n")
        out.append("      npm config set audit false; \\\n")
        out.append("      npm config set fund false; \\\n")
        out.append("      npm config set update-notifier false; \\\n")
        out.append("      npm config set fetch-retries 5; \\\n")
        out.append("      npm config set fetch-retry-mintimeout 20000; \\\n")
        out.append("      npm config set fetch-retry-maxtimeout 120000; \\\n")
        out.append("    fi\n")
        inserted_run = True
        continue
    out.append(line)

if not inserted_arg:
    sys.stderr.write(f"ERROR: could not patch NPM_REGISTRY in {path}\n")
    sys.exit(1)

path.write_text("".join(out), encoding="utf-8")
PY
}

sourcelens_patch_frontend_dockerfile_source_maps() {
	local src=$1
	local dockerfile="${src}/frontend/Dockerfile"
	[[ "${SOURCELENS_BUILD_SOURCE_MAPS:-0}" == "1" ]] || return 0
	[[ -f "${dockerfile}" ]] || return 0
	python3 - "${dockerfile}" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
if "# hfl-hidden-source-maps" in text:
    raise SystemExit(0)
build = "RUN npm run build"
if text.count(build) != 1:
    raise SystemExit(f"expected one SourceLens frontend build command in {path}")
text = text.replace(
    build,
    "RUN npm run build -- --sourcemap hidden \\\n"
    " && cp -a dist dist-runtime \\\n"
    " && find dist-runtime -type f -name '*.map' -delete \\\n"
    " && printf '%s\\n' '# hfl-hidden-source-maps' > /app/.hfl-source-maps",
)
source = "COPY --from=builder /app/dist /usr/share/nginx/html"
target = "COPY --from=builder /app/dist-runtime /usr/share/nginx/html"
if source not in text:
    raise SystemExit(f"SourceLens frontend runtime copy command not found in {path}")
path.write_text(text.replace(source, target), encoding="utf-8")
PY
}

sourcelens_patch_compose_npm_registry() {
	local src=$1
	local compose
	compose="$(sourcelens_build_compose_path "${src}")"
	python3 - "${compose}" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")


def patch_build_arg(source, service, name, value):
    pattern = re.compile(
        rf"(?ms)(^  {re.escape(service)}:\n.*?^    build:\n.*?^      args:\n)"
        rf"(.*?)(?=^    \S)"
    )
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise SystemExit(
            f"expected one {service} build args block in {path}, "
            f"found {len(matches)}"
        )

    match = matches[0]
    args = match.group(2)
    line = f"        {name}: {value}\n"
    if re.search(rf"(?m)^        {re.escape(name)}:.*$", args):
        args = re.sub(
            rf"(?m)^        {re.escape(name)}:.*$",
            line.rstrip(),
            args,
            count=1,
        )
    else:
        args = args.rstrip("\n") + "\n" + line
    return source[: match.start(2)] + args + source[match.end(2) :]


text = patch_build_arg(
    text,
    "frontend",
    "NPM_REGISTRY",
    "${NPM_REGISTRY:-}",
)
text = patch_build_arg(
    text,
    "lensnode",
    "CODEGRAPH_REGISTRY",
    "${NPM_REGISTRY:-https://registry.npmjs.org}",
)
path.write_text(text, encoding="utf-8")
PY
}

sourcelens_distribution_image_ref() {
	local component=$1 tag="${2:-${SOURCELENS_DISTRIBUTION_TAG:-${SOURCELENS_VERSION}}}"
	local repository="hyperfilelens-sourcelens-${component}"
	if [[ -n "${SOURCELENS_IMAGE_REGISTRY}" ]]; then
		printf '%s/%s:%s' "${SOURCELENS_IMAGE_REGISTRY}" "${repository}" "${tag}"
	else
		printf '%s:%s' "${repository}" "${tag}"
	fi
}

sourcelens_upstream_image_ref() {
	local component=$1 tag="${2:-${SOURCELENS_VERSION}}"
	printf '%s/sourcelens-%s:%s' "${SOURCELENS_UPSTREAM_IMAGE_PREFIX}" "${component}" "${tag}"
}

sourcelens_backend_image_ref() {
	sourcelens_distribution_image_ref backend "${1:-${SOURCELENS_DISTRIBUTION_TAG:-${SOURCELENS_VERSION}}}"
}

sourcelens_frontend_image_ref() {
	sourcelens_distribution_image_ref frontend "${1:-${SOURCELENS_DISTRIBUTION_TAG:-${SOURCELENS_VERSION}}}"
}

sourcelens_lensnode_image_ref() {
	sourcelens_distribution_image_ref lensnode "${1:-${SOURCELENS_DISTRIBUTION_TAG:-${SOURCELENS_VERSION}}}"
}

sourcelens_app_images_ready() {
	sourcelens_image_exists "$(sourcelens_backend_image_ref)" \
		&& sourcelens_image_exists "$(sourcelens_frontend_image_ref)" \
		&& sourcelens_image_exists "$(sourcelens_lensnode_image_ref)"
}

sourcelens_upstream_images_ready() {
	sourcelens_image_exists "$(sourcelens_upstream_image_ref backend)" \
		&& sourcelens_image_exists "$(sourcelens_upstream_image_ref frontend)" \
		&& sourcelens_image_exists "$(sourcelens_upstream_image_ref lensnode)"
}

sourcelens_tag_app_images_latest() {
	local component upstream_ref distribution_ref latest_ref
	for component in backend frontend lensnode; do
		upstream_ref="$(sourcelens_upstream_image_ref "${component}")"
		distribution_ref="$(sourcelens_distribution_image_ref "${component}")"
		latest_ref="$(sourcelens_distribution_image_ref "${component}" latest)"
		docker tag "${upstream_ref}" "${distribution_ref}"
		docker tag "${upstream_ref}" "${latest_ref}"
		sourcelens_log "Tagged ${upstream_ref} -> ${distribution_ref} (alias: ${latest_ref})"
	done
}

sourcelens_tag_lensnode_alias() {
	local lensnode_ref
	lensnode_ref="$(sourcelens_lensnode_image_ref)"
	if [[ "${lensnode_ref}" != "${SOURCELENS_LENSNODE_IMAGE}" ]]; then
		docker tag "${lensnode_ref}" "${SOURCELENS_LENSNODE_IMAGE}"
	fi
}

sourcelens_build_skippable() {
	[[ "$(sourcelens_read_built_commit)" == "$(sourcelens_current_build_stamp)" ]] \
		&& sourcelens_app_images_ready
}

# Export LensNode image for Gateway hfl-enroll.
# Optional source_archive: copy an existing release image tar instead of docker save.
sourcelens_publish_gateway_lensnode_bundle() {
	local force=${1:-0}
	local source_archive="${2:-}"
	local dest_dir="${HFL_GATEWAY_BOOTSTRAP_DIR:-${HFL_ROOT}/data/media/gateway-bootstrap}"
	local dest="${dest_dir}/lensnode-image-linux-amd64.tar.gz"
	local refs=()

	mkdir -p "${dest_dir}"
	command -v flock >/dev/null 2>&1 \
		|| sourcelens_die "flock is required for atomic LensNode bundle publishing"

	if [[ -n "${source_archive}" && -f "${source_archive}" ]]; then
		(
			local temporary="${dest}.tmp.$$"
			exec 9>"${dest}.lock"
			flock 9
			trap 'rm -f "${temporary}"' EXIT INT TERM
			# Release staging exposes the same LensNode archive at two public paths.
			# A hard link keeps both contracts without storing the 250+ MiB blob
			# twice; copying remains the safe fallback across filesystems.
			if ! ln "${source_archive}" "${temporary}" 2>/dev/null; then
				cp "${source_archive}" "${temporary}"
			fi
			gzip -t "${temporary}"
			[[ -n "$(sourcelens_gateway_lensnode_bundle_image_id "${temporary}")" ]] \
				|| sourcelens_die "source LensNode archive is missing a valid Docker image index"
			chmod 0644 "${temporary}"
			mv -f "${temporary}" "${dest}"
			trap - EXIT INT TERM
		)
		sourcelens_log "Published gateway LensNode bundle -> ${dest#${HFL_ROOT}/}"
		return 0
	fi

	for ref in \
		"$(sourcelens_lensnode_image_ref)" \
		"$(sourcelens_lensnode_image_ref latest)" \
		"${SOURCELENS_LENSNODE_IMAGE}"; do
		if sourcelens_image_exists "${ref}"; then
			refs+=("${ref}")
		fi
	done
	if ((${#refs[@]} == 0)); then
		sourcelens_log "No SourceLens LensNode image found; skipping gateway bundle export"
		return 0
	fi

	local primary_ref="${refs[0]}"
	local bundle_id="" current_id=""
	if [[ "${force}" -eq 0 && -f "${dest}" ]]; then
		current_id="$(sourcelens_lensnode_image_id "${primary_ref}")"
		bundle_id="$(sourcelens_gateway_lensnode_bundle_image_id "${dest}")"
		if [[ -n "${current_id}" && -n "${bundle_id}" && "${current_id}" == "${bundle_id}" ]] \
			&& sourcelens_lensnode_supports_insecure_tls "${primary_ref}"; then
			sourcelens_log "Gateway LensNode bundle up to date (${bundle_id}); skipping export"
			return 0
		fi
		if [[ -n "${current_id}" && -n "${bundle_id}" && "${current_id}" != "${bundle_id}" ]]; then
			sourcelens_log "Gateway LensNode bundle image ${bundle_id} differs from local ${current_id}; refreshing export"
		else
			sourcelens_log "Gateway LensNode bundle stale or missing configurable TLS support; refreshing export"
		fi
	fi

	if ! sourcelens_lensnode_supports_insecure_tls "${primary_ref}"; then
		sourcelens_die "LensNode image ${primary_ref} lacks configurable TLS verification support"
	fi

	sourcelens_log "Saving gateway LensNode bundle atomically -> ${dest#${HFL_ROOT}/}"
	(
		local temporary="${dest}.tmp.$$"
		exec 9>"${dest}.lock"
		flock 9
		trap 'rm -f "${temporary}"' EXIT INT TERM
		docker save "${refs[@]}" | gzip -c >"${temporary}"
		gzip -t "${temporary}"
		local exported_id
		exported_id="$(sourcelens_gateway_lensnode_bundle_image_id "${temporary}")"
		[[ -n "${exported_id}" && "${exported_id}" == "$(sourcelens_lensnode_image_id "${primary_ref}")" ]] \
			|| sourcelens_die "exported LensNode archive failed image identity validation"
		chmod 0644 "${temporary}"
		mv -f "${temporary}" "${dest}"
		trap - EXIT INT TERM
	)
	bundle_id="$(sourcelens_gateway_lensnode_bundle_image_id "${dest}")"
	sourcelens_log "Published gateway LensNode bundle ($(du -h "${dest}" | awk '{print $1}'), ${bundle_id:-unknown id})"
}

sourcelens_build_app_images() {
	local force=${1:-0}
	local no_cache=${2:-0}
	local requested_services="${SOURCELENS_BUILD_SERVICES:-backend-api frontend lensnode}"
	local full_build=1
	if [[ "${requested_services}" != "backend-api frontend lensnode" ]]; then
		full_build=0
		local service
		for service in ${requested_services}; do
			case "${service}" in
			backend-api | frontend | lensnode) ;;
			*) sourcelens_die "unsupported SourceLens build service: ${service}" 2 ;;
			esac
		done
	fi
	sourcelens_ensure_compose
	if [[ "${full_build}" -eq 1 && "${force}" -eq 0 && "${no_cache}" -eq 0 ]] && sourcelens_build_skippable; then
		sourcelens_log "SourceLens app images already built for source stamp $(sourcelens_read_built_commit); skipping compose build"
		sourcelens_tag_lensnode_alias
		return 0
	fi
	if [[ "${full_build}" -eq 1 && "${force}" -eq 0 && "${no_cache}" -eq 0 \
		&& "$(sourcelens_read_built_commit)" == "$(sourcelens_current_build_stamp)" ]] \
		&& sourcelens_upstream_images_ready; then
		sourcelens_log "SourceLens upstream images match the build stamp; applying normalized distribution tags"
		sourcelens_tag_app_images_latest
		sourcelens_tag_lensnode_alias
		return 0
	fi
	if [[ "${full_build}" -eq 1 && "${force}" -eq 0 && "${no_cache}" -eq 0 ]] && sourcelens_app_images_ready; then
		sourcelens_log "SourceLens app images present but source commit changed; rebuilding"
	fi

	local src="${SOURCELENS_BUILD_SOURCE}"
	local version="${SOURCELENS_VERSION}"
	local debian_apt_mirror_url="https://deb.debian.org/debian"
	local pip_index_url="${SOURCELENS_PIP_INDEX_URL:-https://pypi.org/simple}"
	local pip_trusted_host="${SOURCELENS_PIP_TRUSTED_HOST:-pypi.org}"

	if [[ -n "${SOURCELENS_APT_MIRROR:-}" ]]; then
		debian_apt_mirror_url="$(sourcelens_normalize_apt_mirror_url debian "${SOURCELENS_APT_MIRROR}" "${SOURCELENS_DOCKER_PLATFORM}")"
	fi
	sourcelens_restore_source_dockerfiles "${src}"
	sourcelens_restore_compose_file "${src}"
	sourcelens_patch_dockerfile_uv_network "${src}" "${SOURCELENS_UV_HTTP_TIMEOUT}" \
		"${SOURCELENS_UV_CONCURRENT_DOWNLOADS}" "${SOURCELENS_UV_VERSION}"
	sourcelens_patch_dockerfile_pip_resilience "${src}" \
		"${SOURCELENS_PIP_RETRY_MAX}" "${SOURCELENS_PIP_RETRY_DELAY}"
	sourcelens_patch_frontend_dockerfile_npm_registry "${src}"
	sourcelens_patch_frontend_dockerfile_source_maps "${src}"
	sourcelens_patch_compose_lensnode_apt_mirror "${src}" "${debian_apt_mirror_url}"
	sourcelens_patch_compose_build_sources "${src}" \
		"${debian_apt_mirror_url}" "${debian_apt_mirror_url}" \
		"${pip_index_url}" "${pip_trusted_host}"
	sourcelens_patch_compose_uv_network "${src}" "${SOURCELENS_UV_HTTP_TIMEOUT}" \
		"${SOURCELENS_UV_CONCURRENT_DOWNLOADS}" "${SOURCELENS_UV_VERSION}"
	sourcelens_patch_compose_npm_registry "${src}" "${SOURCELENS_NPM_REGISTRY}"
	sourcelens_log "Building SourceLens Docker images (APP_VERSION=${version}, base images from docker.io like HFL)..."
	(
		cd "${src}"
		export APP_VERSION="${version}"
		export DOCKER_DEFAULT_PLATFORM="${SOURCELENS_DOCKER_PLATFORM}"
		sourcelens_log "Using SourceLens Docker platform: ${DOCKER_DEFAULT_PLATFORM}"
		export APT_MIRROR_URL="${debian_apt_mirror_url}"
		export DEBIAN_APT_MIRROR_URL="${debian_apt_mirror_url}"
		sourcelens_log "Using SourceLens Debian apt source: ${APT_MIRROR_URL}"
		export PIP_INDEX_URL="${pip_index_url}"
		export PIP_TRUSTED_HOST="${pip_trusted_host}"
		export UV_HTTP_TIMEOUT="${SOURCELENS_UV_HTTP_TIMEOUT}"
		export UV_CONCURRENT_DOWNLOADS="${SOURCELENS_UV_CONCURRENT_DOWNLOADS}"
		export UV_VERSION="${SOURCELENS_UV_VERSION}"
		export NPM_REGISTRY="${SOURCELENS_NPM_REGISTRY}"
		sourcelens_log "Using SourceLens pip index: ${PIP_INDEX_URL} (uv=${UV_VERSION})"
		sourcelens_log "Using SourceLens pip resilience: BuildKit uv/pip cache + retry max=${SOURCELENS_PIP_RETRY_MAX} delay=${SOURCELENS_PIP_RETRY_DELAY}s"
		sourcelens_log "Using SourceLens uv network: timeout=${UV_HTTP_TIMEOUT}s concurrent=${UV_CONCURRENT_DOWNLOADS} (uv retries=default)"
		sourcelens_log "Using SourceLens npm registry: ${NPM_REGISTRY}"
		local -a build_args=(build)
		[[ "${no_cache}" -eq 1 ]] && build_args+=(--no-cache)
		# shellcheck disable=SC2206
		local -a services=(${requested_services})
		build_args+=("${services[@]}")
		hfl_run_native_command env \
			DOCKER_BUILDKIT=1 \
			BUILDKIT_PROGRESS="${BUILDKIT_PROGRESS:-auto}" \
			"${SOURCELENS_COMPOSE[@]}" \
			-f "${SOURCELENS_BUILD_COMPOSE_FILE}" "${build_args[@]}"
	)
	if [[ "${full_build}" -eq 0 ]]; then
		sourcelens_log "Built requested SourceLens service(s): ${requested_services}"
		return 0
	fi
	sourcelens_tag_app_images_latest
	sourcelens_tag_lensnode_alias
	sourcelens_write_built_commit "$(sourcelens_current_build_stamp)"
}

sourcelens_ensure_nginx_image() {
	local force_pull=${1:-0}
	sourcelens_ensure_runtime_image "${SOURCELENS_NGINX_SOURCE_IMAGE}" "${force_pull}"
	if [[ "${SOURCELENS_NGINX_SOURCE_IMAGE}" != "nginx:stable-alpine" ]]; then
		docker tag "${SOURCELENS_NGINX_SOURCE_IMAGE%@*}" nginx:stable-alpine \
			|| sourcelens_die "unable to tag pinned nginx runtime image"
	fi
}

sourcelens_ensure_runtime_image() {
	local image=$1 force_pull=${2:-${SOURCELENS_FORCE_PULL}}
	sourcelens_log "Resolving runtime image ${image} (offline=${SOURCELENS_OFFLINE}, force_pull=${force_pull})"
	if ! hfl_docker_ensure_image "${image}" "${SOURCELENS_DOCKER_MIRROR}" \
		"${force_pull}" "${SOURCELENS_OFFLINE}" "${SOURCELENS_DOCKER_PLATFORM}" \
		"${SOURCELENS_DOCKER_PULL_TIMEOUT}" "${SOURCELENS_DOCKER_PULL_RETRIES}"; then
		sourcelens_die "unable to prepare ${image}: ${HFL_DOCKER_LAST_ERROR}"
	fi
	sourcelens_log "Runtime image ${image} ready (source=${HFL_DOCKER_IMAGE_SOURCE})"
}

sourcelens_ensure_runtime_images() {
	local force_pull=${1:-${SOURCELENS_FORCE_PULL}}
	local image source_ref target_ref
	for image in nginx:stable-alpine postgres:17 redis:alpine; do
		sourcelens_ensure_runtime_image "${image}" "${force_pull}"
	done
	for image in \
		"nginx:stable-alpine hyperfilelens-sourcelens-nginx:stable-alpine" \
		"postgres:17 hyperfilelens-postgres:17" \
		"redis:alpine hyperfilelens-redis:alpine"; do
		source_ref=${image%% *}
		target_ref=${image#* }
		docker tag "${source_ref}" "${target_ref}" \
			|| sourcelens_die "unable to tag runtime image ${source_ref} as ${target_ref}"
		sourcelens_log "Tagged runtime image ${source_ref} -> ${target_ref}"
	done
}

sourcelens_patch_env_runtime_defaults() {
	local path=$1
	local script="${SOURCELENS_INSTALLER_DIR}/sourcelens/patch-env-runtime.py"
	[[ -f "${script}" ]] || sourcelens_die "missing ${script}"
	python3 "${script}" "${path}"
	python3 - "${path}" \
		"${SOURCELENS_CONSOLE_BIND_ADDRESS}" \
		"${SOURCELENS_CONSOLE_PORT}" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
bind_address = sys.argv[2]
port = sys.argv[3]
text = path.read_text(encoding="utf-8")

for name, value in {
    "SOURCELENS_CONSOLE_BIND_ADDRESS": bind_address,
    "SOURCELENS_CONSOLE_PORT": port,
    "NGINX_HTTPS_PORT": port,
}.items():
    pattern = rf"^{re.escape(name)}=.*$"
    line = f"{name}={value}"
    if re.search(pattern, text, flags=re.M):
        text = re.sub(pattern, line, text, count=1, flags=re.M)
    else:
        text = text.rstrip() + f"\n{line}\n"
path.write_text(text, encoding="utf-8")
PY
}

sourcelens_upstream_nginx_config() {
	local src=$1 candidate
	for candidate in \
		"${src}/docker/nginx/default.standalone.conf" \
		"${src}/docker/nginx/default.conf"; do
		if [[ -f "${candidate}" ]]; then
			printf '%s' "${candidate}"
			return 0
		fi
	done
	sourcelens_die "SourceLens standalone Nginx configuration is missing"
}

sourcelens_patch_runtime_nginx() {
	local path=$1
	python3 - "${path}" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
maintenance_include = "include /etc/nginx/hfl-maintenance/run-creation-gate.conf;"
if maintenance_include not in text:
    text = maintenance_include + "\n\n" + text
sources = (
    "set $ui_upstream http://frontend:80;",
    "set $ui_upstream http://sourcelens-ui:80;",
    "set $ui_upstream http://ui:80;",
)
target = "set $ui_upstream http://web:80;"
if target not in text and not any(source in text for source in sources):
    raise SystemExit(f"SourceLens UI upstream declaration not found in {path}")
for source in sources:
    text = text.replace(source, target)
text = text.replace(
    "/etc/nginx/certs/nginx-selfsigned.crt",
    "/etc/nginx/certs/tls.crt",
)
text = text.replace(
    "/etc/nginx/certs/nginx-selfsigned.key",
    "/etc/nginx/certs/tls.key",
)
if "/etc/nginx/certs/tls.crt" not in text or "/etc/nginx/certs/tls.key" not in text:
    raise SystemExit(f"SourceLens TLS certificate declarations not found in {path}")
assets = """    # HFL-owned runtime observability adapter; SourceLens source remains unchanged.
    location = /hfl-sentry-config.js {
        alias /etc/nginx/hfl-sentry-config.js;
        add_header Cache-Control "no-store" always;
    }
    location = /hfl-sentry-loader.js {
        alias /etc/nginx/hfl-sentry-loader.js;
        add_header Cache-Control "no-store" always;
    }

"""
location_marker = """    location / {
        proxy_pass $ui_upstream;
"""
location_replacement = """    location / {
        proxy_set_header Accept-Encoding "";
        sub_filter_once on;
        sub_filter '</head>' '<script src="/hfl-sentry-config.js"></script><script src="/hfl-sentry-loader.js"></script></head>';
        proxy_pass $ui_upstream;
"""
frontend_location_count = text.count("    location / {\n")
location_count = text.count(location_marker)
if not frontend_location_count:
    raise SystemExit(f"SourceLens frontend location marker not found in {path}")
if "location = /hfl-sentry-config.js" not in text:
    if location_count != frontend_location_count:
        raise SystemExit(f"SourceLens frontend proxy layout is unsupported in {path}")
    text = text.replace(location_marker, assets + location_marker)
if "hfl-sentry-loader.js\"></script></head>" not in text:
    if location_count != frontend_location_count:
        raise SystemExit(f"SourceLens frontend proxy layout is unsupported in {path}")
    text = text.replace(location_marker, location_replacement)
if text.count("location = /hfl-sentry-config.js") != frontend_location_count:
    raise SystemExit(f"SourceLens Sentry asset routes were not injected into {path}")
if text.count("hfl-sentry-loader.js\"></script></head>") != frontend_location_count:
    raise SystemExit(f"SourceLens Sentry loader was not injected into {path}")
server_marker = "server {\n"
server_guard = """server {
    # HFL-owned upgrade barrier for direct SourceLens Run creation.
    if ($hfl_sourcelens_run_creation_blocked) {
        return 503;
    }
"""
server_count = text.count(server_marker)
if not server_count:
    raise SystemExit(f"SourceLens server blocks not found in {path}")
if "$hfl_sourcelens_run_creation_blocked" not in text.replace(maintenance_include, ""):
    text = text.replace(server_marker, server_guard)
if text.count("if ($hfl_sourcelens_run_creation_blocked)") != server_count:
    raise SystemExit(f"SourceLens maintenance guards were not injected into every server block in {path}")
path.write_text(text, encoding="utf-8")
PY
}

sourcelens_write_runtime_compose() {
	local compose_path=$1
	local template="${SOURCELENS_INSTALLER_DIR}/sourcelens/docker-compose.template.yml"
	local backend_image frontend_image lensnode_image
	backend_image="$(sourcelens_backend_image_ref)"
	frontend_image="$(sourcelens_frontend_image_ref)"
	lensnode_image="$(sourcelens_lensnode_image_ref)"
	[[ -f "${template}" ]] || sourcelens_die "missing SourceLens Compose template: ${template}"

	python3 - "${template}" "${compose_path}" \
		"${backend_image}" "${frontend_image}" "${lensnode_image}" \
		"${SOURCELENS_CONSOLE_BIND_ADDRESS}" \
		"${SOURCELENS_CONSOLE_PORT}" \
		"${SOURCELENS_EMBED_LENSNODE}" <<'PY'
import pathlib
import re
import sys

template_path, compose_path, backend_image, frontend_image, lensnode_image, bind_address, https_port, embed_raw = sys.argv[1:9]
embed_lensnode = str(embed_raw).strip().lower() in {"1", "true", "yes", "on"}
text = pathlib.Path(template_path).read_text(encoding="utf-8")


def render_optional_block(value: str, name: str, enabled: bool) -> str:
    pattern = re.compile(
        rf"(?ms)^# HFL_{re.escape(name)}_BEGIN\n(.*?)^# HFL_{re.escape(name)}_END\n"
    )
    matches = pattern.findall(value)
    if len(matches) != 1:
        raise SystemExit(f"expected one {name} block in {template_path}, found {len(matches)}")
    return pattern.sub(lambda match: match.group(1) if enabled else "", value)


text = render_optional_block(text, "EMBED_BACKEND_ENV", embed_lensnode)
text = render_optional_block(text, "EMBED_LENSNODE_SERVICE", embed_lensnode)
replacements = {
    "__SOURCELENS_BACKEND_IMAGE__": backend_image,
    "__SOURCELENS_FRONTEND_IMAGE__": frontend_image,
    "__SOURCELENS_LENSNODE_IMAGE__": lensnode_image,
    "__SOURCELENS_CONSOLE_BIND_ADDRESS__": bind_address,
    "__SOURCELENS_CONSOLE_PORT__": https_port,
}
for token, value in replacements.items():
    text = text.replace(token, value)
if "__SOURCELENS_" in text or "HFL_EMBED_" in text:
    raise SystemExit(f"unresolved SourceLens Compose template marker in {template_path}")

destination = pathlib.Path(compose_path)
temporary = destination.with_suffix(destination.suffix + ".tmp")
temporary.write_text(text, encoding="utf-8")
temporary.replace(destination)
PY
}

sourcelens_ensure_dev_data_dirs() {
	mkdir -p \
		"${SOURCELENS_DATA_DIR}/postgresql/data" \
		"${SOURCELENS_DATA_DIR}/redis" \
		"${SOURCELENS_DATA_DIR}/logs/api" \
		"${SOURCELENS_DATA_DIR}/logs/worker" \
		"${SOURCELENS_DATA_DIR}/logs/scheduler" \
		"${SOURCELENS_DATA_DIR}/logs/nginx" \
		"${SOURCELENS_DATA_DIR}/logs/postgresql" \
		"${SOURCELENS_DATA_DIR}/logs/redis" \
		"${SOURCELENS_DATA_DIR}/storage" \
		"${SOURCELENS_DATA_DIR}/document-attachments" \
		"${SOURCELENS_DATA_DIR}/deliverables" \
		"${SOURCELENS_DATA_DIR}/workspace" \
		"${SOURCELENS_DATA_DIR}/django/staticfiles"
}

sourcelens_normalize_dev_runtime_permissions() {
	local dev_root=$1
	find "${dev_root}/deploy" -type d -exec chmod 0755 {} +
	find "${dev_root}/deploy" -type f -exec chmod 0644 {} +
	if [[ -d "${dev_root}/deploy/postgresql/initdb.d" ]]; then
		find "${dev_root}/deploy/postgresql/initdb.d" -type f -name '*.sh' \
			-exec chmod 0755 {} +
	fi
	chmod 0644 "${dev_root}/docker-compose.yml"
}

sourcelens_prepare_dev_runtime_tree() {
	local src="${SOURCELENS_BUILD_SOURCE}"
	local dev_root="${SOURCELENS_DEV_DIR}"
	local nginx_config
	nginx_config="$(sourcelens_upstream_nginx_config "${src}")"

	mkdir -p "${dev_root}/deploy/nginx/hfl-maintenance" "${dev_root}/deploy/postgresql" \
		"${dev_root}/deploy/sentry"
	cp "${nginx_config}" "${dev_root}/deploy/nginx/default.conf"
	sourcelens_patch_runtime_nginx "${dev_root}/deploy/nginx/default.conf"
	cp "${SOURCELENS_INSTALLER_DIR}/sourcelens/run-creation-gate-off.conf" \
		"${dev_root}/deploy/nginx/hfl-maintenance/run-creation-gate.conf"
	cp "${SOURCELENS_INSTALLER_DIR}/sourcelens/hfl-sentry-loader.js" \
		"${dev_root}/deploy/nginx/hfl-sentry-loader.js"
	printf '%s\n' 'window.__HFL_SOURCELENS_SENTRY__ = Object.freeze({ enabled: false })' \
		>"${dev_root}/deploy/nginx/hfl-sentry-config.js"
	cp "${SOURCELENS_INSTALLER_DIR}/sourcelens/hfl-sentry-sitecustomize.py" \
		"${dev_root}/deploy/sentry/hfl-sentry-sitecustomize.py"
	chmod 644 "${dev_root}/deploy/sentry/hfl-sentry-sitecustomize.py"
	sourcelens_ensure_tls_certs "${HFL_ROOT}/deploy/nginx/certs"
	rm -rf "${dev_root}/deploy/nginx/certs"
	ln -s "${HFL_ROOT}/deploy/nginx/certs" "${dev_root}/deploy/nginx/certs"
	if [[ -d "${src}/docker/postgresql" ]]; then
		rsync -a "${src}/docker/postgresql/" "${dev_root}/deploy/postgresql/"
	fi

	sourcelens_ensure_dev_data_dirs
	mkdir -p "${dev_root}/data"
	for subdir in postgresql redis logs storage document-attachments deliverables workspace django; do
		ln -sfn "${SOURCELENS_DATA_DIR}/${subdir}" "${dev_root}/data/${subdir}"
	done

	local env_file="${SOURCELENS_DEV_ENV_FILE}"
	local legacy_env_file="${dev_root}/.env"
	local env_sample="${src}/env.sample"
	[[ -f "${env_sample}" ]] || sourcelens_die "SourceLens env.sample not found"
	mkdir -p "$(dirname "${env_file}")"
	if [[ -f "${legacy_env_file}" && ! -L "${legacy_env_file}" && ! -f "${env_file}" ]]; then
		cp "${legacy_env_file}" "${env_file}"
		sourcelens_log "Migrated SourceLens dev config to ${env_file#${HFL_ROOT}/}"
	fi
	if [[ ! -f "${env_file}" ]]; then
		cp "${env_sample}" "${env_file}"
		chmod 600 "${env_file}"
		sourcelens_patch_env_runtime_defaults "${env_file}"
		sourcelens_log "Created ${env_file#${HFL_ROOT}/}"
	else
		chmod 600 "${env_file}"
		python3 - "${env_file}" "${env_sample}" <<'PY'
import pathlib
import re
import sys

env_path = pathlib.Path(sys.argv[1])
example_path = pathlib.Path(sys.argv[2])
text = env_path.read_text(encoding="utf-8")
existing = set(re.findall(r"^([A-Z0-9_]+)=", text, flags=re.M))
for line in example_path.read_text(encoding="utf-8").splitlines():
    if not line or line.lstrip().startswith("#") or "=" not in line:
        continue
    key = line.split("=", 1)[0].strip()
    if key and key not in existing:
        text = text.rstrip() + f"\n{line}\n"
env_path.write_text(text, encoding="utf-8")
PY
	fi
	sourcelens_patch_env_runtime_defaults "${env_file}"
	chmod 600 "${env_file}"
	if [[ -e "${legacy_env_file}" && ! -L "${legacy_env_file}" ]]; then
		rm -f "${legacy_env_file}"
	fi
	ln -sfn "${env_file}" "${dev_root}/.env"

	sourcelens_write_runtime_compose "${dev_root}/docker-compose.yml"
	sourcelens_normalize_dev_runtime_permissions "${dev_root}"
}

sourcelens_migrate_legacy_dev_project() {
	local id project working_dir config_files removed=0
	while IFS= read -r id; do
		[[ -n "${id}" ]] || continue
		project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "${id}" 2>/dev/null || true)"
		working_dir="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "${id}" 2>/dev/null || true)"
		config_files="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "${id}" 2>/dev/null || true)"
		[[ "${project}" == "sourcelens" ]] || continue
		if [[ "${working_dir}" != "${SOURCELENS_DEV_DIR}" \
			&& ",${config_files}," != *",${SOURCELENS_DEV_DIR}/docker-compose.yml,"* ]]; then
			continue
		fi
		sourcelens_log "Migrating owned legacy SourceLens dev container ${id:0:12}"
		docker rm -f "${id}" >/dev/null
		removed=1
	done < <(docker ps -aq --no-trunc)
	if [[ "${removed}" == "1" ]]; then
		sourcelens_log "Legacy dev project migration completed; unrelated SourceLens projects were not touched"
	fi
}

sourcelens_dev_compose() {
	sourcelens_ensure_compose
	(
		cd "${SOURCELENS_DEV_DIR}"
		"${SOURCELENS_COMPOSE[@]}" --env-file "${SOURCELENS_DEV_ENV_FILE}" \
			-p "${SOURCELENS_COMPOSE_PROJECT}" -f docker-compose.yml "$@"
	)
}

sourcelens_dev_compose_native() {
	sourcelens_ensure_compose
	(
		cd "${SOURCELENS_DEV_DIR}"
		hfl_run_native_command env \
			DOCKER_BUILDKIT=1 \
			BUILDKIT_PROGRESS="${BUILDKIT_PROGRESS:-auto}" \
			"${SOURCELENS_COMPOSE[@]}" --env-file "${SOURCELENS_DEV_ENV_FILE}" \
			-p "${SOURCELENS_COMPOSE_PROJECT}" -f docker-compose.yml "$@"
	)
}

sourcelens_manage() {
	sourcelens_dev_compose exec -T --workdir /opt/backend \
		api /opt/venv/bin/python manage.py "$@"
}

sourcelens_manage_logged() {
	local status=0 output_file
	output_file="$(mktemp "${TMPDIR:-/tmp}/hfl-sourcelens-manage.XXXXXX")" || return 1
	# Keep the command invocation in the current shell. Besides preserving the
	# exact exit code, this matters for callers that provide a compose wrapper
	# (and avoids hiding its bookkeeping in a pipeline subshell).
	sourcelens_manage "$@" >"${output_file}" 2>&1 || status=$?
	hfl_normalize_native_stream <"${output_file}" | hfl_log_output_block sourcelens
	rm -f -- "${output_file}"
	return "${status}"
}

sourcelens_ensure_database_initialized() {
	if ! sourcelens_manage migrate --check >/dev/null 2>&1; then
		sourcelens_log "Initializing SourceLens database (missing or pending migrations)"
		sourcelens_manage_logged sourcelens_init --skip-collectstatic \
			|| sourcelens_die "SourceLens database initialization failed"
	else
		sourcelens_log "SourceLens database migrations are current"
	fi

	sourcelens_log "Collecting SourceLens static assets"
	sourcelens_dev_compose exec -T api mkdir -p /opt/backend/core/staticfiles
	sourcelens_manage_logged collectstatic --noinput \
		|| sourcelens_die "SourceLens static asset collection failed"
	hfl_log_ok "SourceLens static assets are ready"
}

sourcelens_configure_hfl_env() {
	local hfl_env="${HFL_ROOT}/.env"
	[[ -f "${hfl_env}" ]] || return 0
	python3 - "${hfl_env}" "${SOURCELENS_GIT_REF}" <<'PY'
import pathlib
import re
import sys

env_path = pathlib.Path(sys.argv[1])
git_ref = sys.argv[2].strip()
if not re.fullmatch(r"v\d+\.\d+\.\d+", git_ref):
    raise SystemExit(f"invalid SourceLens Git ref: {git_ref}")
text = env_path.read_text(encoding="utf-8")

def read_key(name: str, default: str = "") -> str:
    match = re.search(rf"^{re.escape(name)}=(.*)$", text, flags=re.M)
    if not match:
        return default
    return match.group(1).strip().strip('"').strip("'")

frontend = read_key("FRONTEND_URL", "https://127.0.0.1:11443").rstrip("/")
no_proxy = [item.strip() for item in read_key("NO_PROXY").split(",") if item.strip()]
if "sourcelens-nginx" not in no_proxy:
    no_proxy.append("sourcelens-nginx")
updates = {
    "LENS_BASE_URL": "http://sourcelens-nginx",
    "LENS_GATEWAY_BASE_URL": f"{frontend}/sourcelens",
    "NO_PROXY": ",".join(no_proxy),
    "SOURCELENS_GIT_REF": git_ref,
}

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
PY
	sourcelens_log "Configured LENS_BASE_URL, LENS_GATEWAY_BASE_URL, NO_PROXY, SOURCELENS_GIT_REF in ${hfl_env}"
}

sourcelens_wait_for_health() {
	local attempt
	for attempt in $(seq 1 60); do
		if sourcelens_dev_compose exec -T nginx \
			curl -fsS --connect-timeout 3 -m 5 \
			http://127.0.0.1/health >/dev/null 2>&1; then
			sourcelens_log "Health check OK (sourcelens-nginx-1:/health)"
			return 0
		fi
		sleep 5
	done
	sourcelens_die "SourceLens did not become healthy on its private network"
}
