#!/usr/bin/env bash
# Build a HyperFileLens release install package (air-gap delivery).
# Run from repository root with Docker and Go; SourceLens builds also require Git and Docker Compose.
#
# Usage:
#   ./release/build.sh
#   ./release/build.sh --github-download-mirror https://ghfast.top --docker-download-mirror docker.m.daocloud.io --apt-mirror https://mirrors.tuna.tsinghua.edu.cn
set -euo pipefail
export COPYFILE_DISABLE=1
umask 022

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE_DIR="${ROOT}/release"
RELEASE_BUILD_DIR="${ROOT}/build/release"
STAGING_BASE="${RELEASE_BUILD_DIR}/staging"
DIST_DIR="${RELEASE_BUILD_DIR}/dist"
# shellcheck source=../tools/lib/safe-path.sh
source "${ROOT}/tools/lib/safe-path.sh"
# shellcheck source=../tools/lib/version.sh
source "${ROOT}/tools/lib/version.sh"
# shellcheck source=../tools/lib/logging.sh
source "${ROOT}/tools/lib/logging.sh"
# shellcheck source=../tools/lib/env-file.sh
source "${ROOT}/tools/lib/env-file.sh"
# shellcheck source=../tools/lib/archive.sh
source "${ROOT}/tools/lib/archive.sh"
# shellcheck source=../tools/lib/docker-images.sh
source "${ROOT}/tools/lib/docker-images.sh"
# shellcheck source=../tools/kopia/common.sh
source "${ROOT}/tools/kopia/common.sh"
# shellcheck source=../tools/dependencies/versions/runtime-images.env
source "${ROOT}/tools/dependencies/versions/runtime-images.env"
export SOURCELENS_NGINX_SOURCE_IMAGE="${NGINX_IMAGE}"

MIRROR_GITHUB_DOWNLOAD=""
MIRROR_GITHUB_TOKEN=""
MIRROR_DOCKER_DOWNLOAD=""
MIRROR_DOCKER_APT=""
MIRROR_APT=""
FORCE_PULL=0
NO_CACHE=0
SOURCELENS_FORCE_BUILD="${SOURCELENS_FORCE_BUILD:-0}"
BUILD_SOURCELENS="${BUILD_SOURCELENS:-}"
SOURCELENS_GIT_REF="${SOURCELENS_GIT_REF:-}"
LOG_FILE="${HFL_LOG_FILE:-}"
VERBOSE="${HFL_LOG_VERBOSE:-0}"
PRINT_CONFIG=0
OPT_VERSION=""
SOURCELENS_BUILD_ENV="${ROOT}/tools/sourcelens/defaults.env"
# Open Core extension bake (packaging only). Repeatable --extension-source.
EXTENSION_SOURCES=()
EXTENSION_BAKE_DIR="${ROOT}/build/release/extensions"
HFL_EXTENSIONS_RUNTIME=""

log() { hfl_log_info "$@"; }
die() { hfl_die "$1" "${2:-1}"; }

tar_create_gz() {
	local out=$1
	local base_dir=$2
	local entry=$3

	hfl_tar_create_gz "${out}" "${base_dir}" "${entry}"
}

normalize_release_permissions() {
	local pkg_root=$1
	find "${pkg_root}" -type d -exec chmod 755 {} +
	find "${pkg_root}" -type f -exec chmod 644 {} +
	chmod 755 "${pkg_root}/install.sh" "${pkg_root}/apply-runtime-config.py"
	if [[ -f "${pkg_root}/sync-env.py" ]]; then
		chmod 755 "${pkg_root}/sync-env.py"
	fi
	if [[ -d "${pkg_root}/sourcelens" ]]; then
		chmod 755 "${pkg_root}/sourcelens/install.sh" \
			"${pkg_root}/sourcelens/patch-env-runtime.py" \
			"${pkg_root}/sourcelens/sync-sentry-runtime.py"
		find "${pkg_root}/sourcelens/deploy/postgresql/initdb.d" \
			-type f -name '*.sh' -exec chmod 755 {} +
	fi
	find "${pkg_root}/payload/media" -type f -name '*.sh' -exec chmod 755 {} +
	if [[ -f "${pkg_root}/deploy/nginx/certs/tls.key" ]]; then
		chmod 600 "${pkg_root}/deploy/nginx/certs/tls.key"
	fi
}

validate_release_security() {
	local pkg_root=$1 bad=""
	local cert_dir="${pkg_root}/deploy/nginx/certs"
	local allowed_key="${cert_dir}/tls.key"
	bad="$(find "${pkg_root}" \
		\( -name '.env' -o -name '*.key' -o -name '*.pem' -o -name '*.p12' \
		-o -name '*.pfx' -o -name 'id_rsa' \) ! -path "${allowed_key}" -print -quit)"
	[[ -z "${bad}" ]] || die "release package contains forbidden secret material: ${bad#${pkg_root}/}"
	while IFS= read -r candidate; do
		[[ "${candidate}" == "${allowed_key}" ]] && continue
		bad="${candidate}"
		break
	done < <(
		find "${pkg_root}" -type f -size -2M -exec \
			grep -IlE -- '-----BEGIN ([A-Z0-9]+ )?PRIVATE KEY-----' {} + 2>/dev/null || true
	)
	[[ -z "${bad}" ]] \
		|| die "release package contains an unapproved private key: ${bad#${pkg_root}/}"
	for required in tls.crt tls.key root-ca.crt SHA256SUMS README.md; do
		[[ -s "${cert_dir}/${required}" ]] \
			|| die "release package is missing deploy/nginx/certs/${required}"
	done
	(
		cd "${cert_dir}"
		sha256sum --strict --check SHA256SUMS >/dev/null
	) || die "release default TLS checksum validation failed"
	[[ "$(stat -c '%a' "${allowed_key}")" == "600" ]] \
		|| die "release default TLS private key must have mode 0600"
	openssl verify -CAfile "${cert_dir}/root-ca.crt" "${cert_dir}/tls.crt" >/dev/null 2>&1 \
		|| die "release default TLS certificate chain validation failed"
	local cert_pub key_pub
	cert_pub="$(openssl x509 -in "${cert_dir}/tls.crt" -pubkey -noout | sha256sum | cut -d' ' -f1)"
	key_pub="$(openssl pkey -in "${allowed_key}" -pubout 2>/dev/null | sha256sum | cut -d' ' -f1)"
	[[ "${cert_pub}" == "${key_pub}" ]] \
		|| die "release default TLS certificate and key do not match"
	for public_dir in \
		"${pkg_root}/payload/media/agent-releases" \
		"${pkg_root}/payload/media/enroll-bootstrap" \
		"${pkg_root}/payload/media/gateway-bootstrap"; do
		[[ -d "${public_dir}" ]] || continue
		bad="$(find "${public_dir}" -type f ! -perm -004 -print -quit)"
		[[ -z "${bad}" ]] \
			|| die "release download is not readable by the nginx worker: ${bad#${pkg_root}/}"
	done
	log "Release secret and download permission validation passed"
}

stage_local_language_packs() {
	local pkg_root=$1 version=$2
	local builder_image="hyperfilelens-language-pack-builder:${version}"
	local output="${RELEASE_BUILD_DIR}/language-packs"
	log "Building version-matched language packs"
	docker build --network host \
		-f "${ROOT}/language-packs/tooling/Dockerfile" \
		-t "${builder_image}" \
		--build-arg "NODE_BASE_IMAGE=${HFL_FRONTEND_NODE_BASE_IMAGE:-node:22-alpine}" \
		--build-arg "NPM_REGISTRY=${NPM_REGISTRY:-}" \
		"${ROOT}"
	mkdir -p "${output}" "${pkg_root}/payload/language-packs"
	docker run --rm --platform linux/amd64 \
		--user "$(id -u):$(id -g)" \
		--mount "type=bind,src=${ROOT}/language-packs,dst=/workspace/language-packs,readonly" \
		--mount "type=bind,src=${ROOT}/src/backend,dst=/workspace/src/backend,readonly" \
		--mount "type=bind,src=${ROOT}/src/frontend/src/locales,dst=/workspace/src/frontend/src/locales,readonly" \
		--mount "type=bind,src=${output},dst=/workspace/build/language-packs" \
		"${builder_image}" --version "${version}"
	cp "${output}/dist/"*.tar.gz "${pkg_root}/payload/language-packs/"
}

stage_default_tls_bundle() {
	local pkg_root=$1
	local source_dir="${ROOT}/deploy/nginx/certs"
	local target_dir="${pkg_root}/deploy/nginx/certs"
	mkdir -p "${target_dir}"
	rsync -a --delete "${source_dir}/" "${target_dir}/"
}

require_value() {
	if [[ $# -lt 2 || -z "${2:-}" || "${2:0:1}" == "-" ]]; then
		die "${1} requires a value" 2
	fi
}

load_repo_env_defaults() {
	hfl_env_select_repo_file "${ROOT}"
	local key
	for key in \
		GITHUB_DOWNLOAD_MIRROR GITHUB_TOKEN DOCKER_DOWNLOAD_MIRROR \
		DOCKER_APT_MIRROR APT_MIRROR PIP_INDEX_URL PIP_TRUSTED_HOST \
		PIP_TIMEOUT NPM_REGISTRY GOPROXY GOSUMDB BUILD_SOURCELENS \
		DOCKER_PULL_TIMEOUT_SECONDS \
		KOPIA_ARTIFACT_MODE KOPIA_GIT_URL KOPIA_GIT_REF \
		SOURCELENS_GIT_URL SOURCELENS_GIT_TIMEOUT_SECONDS \
		SOURCELENS_GIT_RETRIES SOURCELENS_GIT_FALLBACK_TIMEOUT_SECONDS \
		VITE_SHOW_EULA; do
		hfl_env_load_default "${key}"
	done
}

apply_mirror_env_defaults() {
	MIRROR_GITHUB_DOWNLOAD="${MIRROR_GITHUB_DOWNLOAD:-${GITHUB_DOWNLOAD_MIRROR:-${BUILD_GITHUB_DOWNLOAD_MIRROR:-}}}"
	MIRROR_GITHUB_TOKEN="${MIRROR_GITHUB_TOKEN:-${GITHUB_TOKEN:-}}"
	MIRROR_DOCKER_DOWNLOAD="${MIRROR_DOCKER_DOWNLOAD:-${DOCKER_DOWNLOAD_MIRROR:-${BUILD_DOCKER_DOWNLOAD_MIRROR:-}}}"
	MIRROR_DOCKER_APT="${MIRROR_DOCKER_APT:-${DOCKER_APT_MIRROR:-${BUILD_DOCKER_APT_MIRROR:-}}}"
	MIRROR_APT="${MIRROR_APT:-${APT_MIRROR:-${BUILD_APT_MIRROR:-}}}"
}

apply_go_proxy_env_defaults() {
	export GOPROXY="${GOPROXY:-${BUILD_GOPROXY:-https://proxy.golang.org,direct}}"
	export GOSUMDB="${GOSUMDB:-${BUILD_GOSUMDB:-sum.golang.org}}"
}

validate_docker_pull_timeout() {
	DOCKER_PULL_TIMEOUT_SECONDS="${DOCKER_PULL_TIMEOUT_SECONDS:-180}"
	[[ "${DOCKER_PULL_TIMEOUT_SECONDS}" =~ ^[1-9][0-9]*$ ]] \
		|| die "DOCKER_PULL_TIMEOUT_SECONDS must be a positive integer" 2
	export DOCKER_PULL_TIMEOUT_SECONDS
}

export_build_mirror_env() {
	apply_mirror_env_defaults
	export APT_MIRROR="${MIRROR_APT:-${APT_MIRROR:-${BUILD_APT_MIRROR:-}}}"
	export PIP_INDEX_URL="${PIP_INDEX_URL:-${BUILD_PIP_INDEX_URL:-}}"
	export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-${BUILD_PIP_TRUSTED_HOST:-}}"
	export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-${BUILD_UV_HTTP_TIMEOUT:-120}}"
	export UV_CONCURRENT_DOWNLOADS="${UV_CONCURRENT_DOWNLOADS:-${BUILD_UV_CONCURRENT_DOWNLOADS:-2}}"
	export NPM_REGISTRY="${NPM_REGISTRY:-${BUILD_NPM_REGISTRY:-}}"
	hfl_docker_export_build_base_images "${MIRROR_DOCKER_DOWNLOAD}"
}

mirror_args() {
	local args=()
	[[ -n "${MIRROR_GITHUB_DOWNLOAD}" ]] && args+=(--github-download-mirror "${MIRROR_GITHUB_DOWNLOAD}")
	[[ -n "${MIRROR_GITHUB_TOKEN}" ]] && args+=(--github-token "${MIRROR_GITHUB_TOKEN}")
	[[ -n "${MIRROR_DOCKER_DOWNLOAD}" ]] && args+=(--docker-download-mirror "${MIRROR_DOCKER_DOWNLOAD}")
	[[ -n "${MIRROR_APT}" ]] && args+=(--apt-mirror "${MIRROR_APT}")
	((${#args[@]})) && printf '%s\0' "${args[@]}"
}

validate_ubuntu2404_arch() {
	case "${UBUNTU2404_ARCH}" in
	amd64 | arm64 | all) ;;
	*)
		die "invalid --ubuntu2404-arch ${UBUNTU2404_ARCH} (use amd64, arm64, or all)" 2
		;;
	esac
}

apply_ubuntu2404_arch_default() {
	UBUNTU2404_ARCH="${UBUNTU2404_ARCH:-amd64}"
	validate_ubuntu2404_arch
}

usage() {
	cat <<'USAGE'
Usage: release/build.sh [options]

Build one package into build/release/dist/:
  Community:       hyperfilelens-<version>.tar.gz
  Enterprise:      hyperfilelens-<version>-ee.tar.gz
  main channel:    hyperfilelens-main-<commit7>.tar.gz

Version:
  --version VERSION                  X.Y.Z or main-<commit7> (default: 0.1.0)
                                     A matching exact Git tag is authoritative when present.

  - Full agent bundle (all platforms)
  - Host Docker CE debs for Ubuntu 20.04/22.04/24.04 amd64 (offline install)
  - Control-plane Docker images + postgres/redis
  - Image-only runtime package: images, payload/media, compose, and deploy config

Mirror options (Kopia fetch + Agent publishing + SourceLens Git + runtime image pull; env fallback):
  --github-download-mirror URL     GitHub Git/release mirror (env: GITHUB_DOWNLOAD_MIRROR)
  --github-token TOKEN             GitHub token for API/release fetch, private SourceLens clone,
                                     and private extension git sources (env: GITHUB_TOKEN /
                                     HFL_EXTENSION_GIT_TOKEN)
  --docker-download-mirror URL     Docker Hub mirror for ubuntu:24.04, postgres, redis (env: DOCKER_DOWNLOAD_MIRROR)
  --docker-pull-timeout SECONDS    Timeout for each Docker pull attempt (env: DOCKER_PULL_TIMEOUT_SECONDS)
  --docker-apt-mirror URL          Docker CE apt repo base URL (env: DOCKER_APT_MIRROR / BUILD_DOCKER_APT_MIRROR)
  --apt-mirror URL                 Ubuntu apt mirror for NAS container (env: APT_MIRROR)
  --ubuntu2404-arch ARCH           NAS deb arch for agent bundle: amd64 | arm64 | all (default: amd64)
  --go-proxy URL                   Go module proxy (env: GOPROXY)
  --go-sumdb VALUE                 Go checksum database (env: GOSUMDB)
  --pip-index-url URL              Python package index (env: PIP_INDEX_URL)
  --pip-trusted-host HOST          Trusted pip host (env: PIP_TRUSTED_HOST)
  --npm-registry URL               npm registry (env: NPM_REGISTRY)

Open Core extensions (bake into control-plane images at packaging time):
  --extension-source SRC           Local path or git/HTTPS URL[+@ref]. Repeatable.
                                     Empty = Community (no plugin). Not read from
                                     .env. CI may set process env
                                     HFL_EXTENSION_SOURCES / HFL_EXTENSION_GIT_TOKEN.
                                     Private HTTPS: --github-token or
                                     HFL_EXTENSION_GIT_TOKEN (SSH uses your agent).

Kopia artifacts (tools/kopia/defaults.env; default: build patched source):
  --kopia-mode MODE               build or download
  --kopia-git-url URL             Kopia source repository URL
  --kopia-ref REF                 Kopia release ref in vX.Y.Z form

  --pull                           Re-check registry and pull runtime images (default: use local if present)
  --no-cache                       Rebuild HFL and SourceLens Docker layers without BuildKit cache

SourceLens bundle (tools/sourcelens/defaults.env; default: enabled):
  --no-sourcelens                  Skip SourceLens clone/build/bundle
  --sourcelens-ref REF             SourceLens release tag in vX.Y.Z form
  --sourcelens-git-url URL         Override SourceLens repository URL (env: SOURCELENS_GIT_URL)
  --force-build                    Rebuild SourceLens images even when the build stamp matches

Output options:
  --log-file FILE                  Append runtime logs to FILE
  --verbose                        Enable debug logs
  --print-config                   Print effective non-secret configuration and exit
  -h, --help                       Show this help

Examples:
  ./release/build.sh
  ./release/build.sh --extension-source ../hyperfilelens-ee
  ./release/build.sh --extension-source https://github.com/org/hyperfilelens-ee.git@main --github-token "$TOKEN"
  ./release/build.sh --ubuntu2404-arch amd64
  ./release/build.sh --github-download-mirror https://ghfast.top --docker-download-mirror docker.m.daocloud.io --apt-mirror https://mirrors.tuna.tsinghua.edu.cn
USAGE
}

print_config() {
	local resolved_version sourcelens_version
	resolved_version="$(read_version)" || return $?
	[[ "${SOURCELENS_GIT_REF}" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]] \
		|| die "invalid SourceLens release ref: ${SOURCELENS_GIT_REF} (expected vX.Y.Z)" 2
	sourcelens_version="${BASH_REMATCH[1]}"
	apply_mirror_env_defaults
	apply_go_proxy_env_defaults
	validate_docker_pull_timeout
	apply_ubuntu2404_arch_default
	export_build_mirror_env
	cat <<EOF
release_dir=${DIST_DIR}
staging_dir=${STAGING_BASE}
hfl_version=${resolved_version}
agent_version=${resolved_version}
kopia_mode=${KOPIA_ARTIFACT_MODE}
kopia_git_url=${KOPIA_GIT_URL}
kopia_ref=${KOPIA_GIT_REF}
kopia_version=${KOPIA_VERSION}
with_sourcelens=${BUILD_SOURCELENS:-1}
sourcelens_ref=${SOURCELENS_GIT_REF}
sourcelens_version=${sourcelens_version}
sourcelens_git_url=${SOURCELENS_GIT_URL:-<default>}
sourcelens_git_timeout=${SOURCELENS_GIT_TIMEOUT_SECONDS:-120}
sourcelens_git_retries=${SOURCELENS_GIT_RETRIES:-2}
sourcelens_git_fallback_timeout=${SOURCELENS_GIT_FALLBACK_TIMEOUT_SECONDS:-30}
sourcelens_upstream_image_prefix=${SOURCELENS_UPSTREAM_IMAGE_PREFIX}
sourcelens_image_registry=${SOURCELENS_IMAGE_REGISTRY:-<local>}
ubuntu2404_arch=${UBUNTU2404_ARCH}
force_pull=${FORCE_PULL}
no_cache=${NO_CACHE}
force_sourcelens_build=${SOURCELENS_FORCE_BUILD}
github_download_mirror=${MIRROR_GITHUB_DOWNLOAD:-<official>}
github_token=$(hfl_redact "${MIRROR_GITHUB_TOKEN}")
docker_download_mirror=${MIRROR_DOCKER_DOWNLOAD:-<official>}
docker_pull_timeout_seconds=${DOCKER_PULL_TIMEOUT_SECONDS:-180}
docker_apt_mirror=${MIRROR_DOCKER_APT:-https://download.docker.com/linux/ubuntu}
apt_mirror=${MIRROR_APT:-<official>}
go_proxy=${GOPROXY}
go_sumdb=${GOSUMDB}
pip_index_url=${PIP_INDEX_URL:-<official>}
pip_trusted_host=${PIP_TRUSTED_HOST:-<unset>}
npm_registry=${NPM_REGISTRY:-<official>}
extension_sources=${EXTENSION_SOURCES_CSV:-<none>}
hfl_extensions=<resolved at image bake>
log_file=${LOG_FILE:-<none>}
verbose=${VERBOSE}
EOF
}

load_sourcelens_build_config() {
	if [[ -f "${SOURCELENS_BUILD_ENV}" ]]; then
		# shellcheck disable=SC1090
		source "${SOURCELENS_BUILD_ENV}"
	fi
	if [[ -n "${BUILD_SOURCELENS}" ]]; then
		export BUILD_SOURCELENS
	elif [[ -z "${BUILD_SOURCELENS:-}" ]]; then
		export BUILD_SOURCELENS=1
	fi
}

parse_common_option() {
	case "$1" in
	--github-download-mirror)
		require_value "$1" "${2:-}"
		MIRROR_GITHUB_DOWNLOAD="$2"
		return 0
		;;
	--github-token)
		require_value "$1" "${2:-}"
		MIRROR_GITHUB_TOKEN="$2"
		return 0
		;;
	--docker-download-mirror)
		require_value "$1" "${2:-}"
		MIRROR_DOCKER_DOWNLOAD="$2"
		return 0
		;;
	--docker-pull-timeout)
		require_value "$1" "${2:-}"
		[[ "$2" =~ ^[1-9][0-9]*$ ]] || die "$1 requires a positive integer" 2
		export DOCKER_PULL_TIMEOUT_SECONDS="$2"
		return 0
		;;
	--docker-apt-mirror)
		require_value "$1" "${2:-}"
		MIRROR_DOCKER_APT="$2"
		return 0
		;;
	--apt-mirror)
		require_value "$1" "${2:-}"
		MIRROR_APT="$2"
		return 0
		;;
	--ubuntu2404-arch)
		require_value "$1" "${2:-}"
		UBUNTU2404_ARCH="$2"
		return 0
		;;
	--go-proxy)
		require_value "$1" "${2:-}"
		export GOPROXY="$2"
		return 0
		;;
	--go-sumdb)
		require_value "$1" "${2:-}"
		export GOSUMDB="$2"
		return 0
		;;
	--pip-index-url)
		require_value "$1" "${2:-}"
		export PIP_INDEX_URL="$2"
		return 0
		;;
	--pip-trusted-host)
		require_value "$1" "${2:-}"
		export PIP_TRUSTED_HOST="$2"
		return 0
		;;
	--npm-registry)
		require_value "$1" "${2:-}"
		export NPM_REGISTRY="$2"
		return 0
		;;
	esac
	return 1
}

parse_args() {
	load_repo_env_defaults
	load_sourcelens_build_config
	kopia_load_config
	while [[ $# -gt 0 ]]; do
		case "$1" in
		-h | --help)
			usage
			exit 0
			;;
		--version)
			require_value "$1" "${2:-}"
			OPT_VERSION="${2#v}"
			export RELEASE_VERSION="${OPT_VERSION}"
			shift 2
			;;
		--kopia-mode)
			require_value "$1" "${2:-}"
			export KOPIA_ARTIFACT_MODE="$2"
			case "${KOPIA_ARTIFACT_MODE}" in build | download) ;; *) die "invalid Kopia mode: ${KOPIA_ARTIFACT_MODE}" 2 ;; esac
			shift 2
			;;
		--kopia-git-url)
			require_value "$1" "${2:-}"
			export KOPIA_GIT_URL="$2"
			shift 2
			;;
		--kopia-ref)
			require_value "$1" "${2:-}"
			export KOPIA_GIT_REF="$2"
			[[ "${KOPIA_GIT_REF}" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]] || die "invalid Kopia ref: ${KOPIA_GIT_REF}" 2
			KOPIA_VERSION="${BASH_REMATCH[1]}"
			shift 2
			;;
		--no-sourcelens)
			export BUILD_SOURCELENS=0
			shift
			;;
		--sourcelens-ref)
			require_value "$1" "${2:-}"
			export SOURCELENS_GIT_REF="$2"
			shift 2
			;;
		--sourcelens-git-url)
			require_value "$1" "${2:-}"
			export SOURCELENS_GIT_URL="$2"
			shift 2
			;;
		--print-config)
			PRINT_CONFIG=1
			shift
			;;
		--verbose)
			VERBOSE=1
			shift
			;;
		--log-file)
			require_value "$1" "${2:-}"
			LOG_FILE="$2"
			shift 2
			;;
		--github-download-mirror | --github-token | --docker-download-mirror | --docker-pull-timeout | --docker-apt-mirror | --apt-mirror | --ubuntu2404-arch | --go-proxy | --go-sumdb | --pip-index-url | --pip-trusted-host | --npm-registry)
			parse_common_option "$@" || die "failed to parse option: $1"
			shift 2
			;;
		--pull)
			FORCE_PULL=1
			shift
			;;
		--no-cache)
			NO_CACHE=1
			shift
			;;
		--force-build)
			SOURCELENS_FORCE_BUILD=1
			shift
			;;
		--extension-source)
			require_value "$1" "${2:-}"
			EXTENSION_SOURCES+=("$2")
			shift 2
			;;
		*)
			die "unknown argument: $1 (try --help)" 2
			;;
		esac
	done
	# Process-env / CI fallback only (never loaded from repo .env).
	if [[ ${#EXTENSION_SOURCES[@]} -eq 0 && -n "${HFL_EXTENSION_SOURCES:-}" ]]; then
		local _src
		IFS=',' read -r -a _src <<<"${HFL_EXTENSION_SOURCES}"
		local _item
		for _item in "${_src[@]}"; do
			_item="${_item#"${_item%%[![:space:]]*}"}"
			_item="${_item%"${_item##*[![:space:]]}"}"
			[[ -n "${_item}" ]] && EXTENSION_SOURCES+=("${_item}")
		done
	fi
	if [[ ${#EXTENSION_SOURCES[@]} -gt 0 ]]; then
		local IFS=','
		EXTENSION_SOURCES_CSV="${EXTENSION_SOURCES[*]}"
	else
		EXTENSION_SOURCES_CSV=""
	fi
}

stage_release_env_example() {
	# Package .env.example must not ship HFL_EXTENSIONS= (empty) — compose env_file
	# would clear the baked image ENV and disable Enterprise plugins at runtime.
	local pkg_root=$1
	local example="${pkg_root}/.env.example"
	cp "${ROOT}/.env.example" "${example}"
	HFL_EXTENSIONS_RUNTIME="${HFL_EXTENSIONS_RUNTIME:-}" \
		HFL_IMAGE_VERSION="${HFL_IMAGE_VERSION:-}" \
		HFL_RELEASE_EDITION="${HFL_RELEASE_EDITION:-community}" \
		HFL_VERSION="${HFL_VERSION:-}" python3 - "${example}" <<'PY'
import os
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = re.sub(r"(?m)^[ \t]*HFL_EXTENSIONS=.*\n?", "", text)
runtime = os.environ.get("HFL_EXTENSIONS_RUNTIME", "").strip()
image_version = os.environ.get("HFL_IMAGE_VERSION", "").strip()
product_version = os.environ.get("HFL_VERSION", "").strip()
edition = os.environ.get("HFL_RELEASE_EDITION", "community").strip()
if image_version:
    replacements = {
        "APP_VERSION": image_version,
        "HFL_PRODUCT_VERSION": product_version,
        "HFL_EDITION": edition,
        "HFL_BACKEND_IMAGE": f"hyperfilelens-backend:{image_version}",
        "HFL_FRONTEND_IMAGE": f"hyperfilelens-frontend:{image_version}",
    }
    for key, value in replacements.items():
        text = re.sub(
            rf"(?m)^{re.escape(key)}=.*$", f"{key}={value}", text, count=1
        )
if runtime:
    block = (
        "\n# Baked into this release's control-plane images (Open Core).\n"
        f"HFL_EXTENSIONS={runtime}\n"
    )
    text = text.rstrip() + "\n" + block
path.write_text(text, encoding="utf-8")
PY
}

prepare_extension_bake() {
	# Stage extensions into docker context for COPY; Community leaves an empty tree.
	rm -rf "${EXTENSION_BAKE_DIR}"
	mkdir -p "${EXTENSION_BAKE_DIR}"
	: >"${EXTENSION_BAKE_DIR}/.gitkeep"
	HFL_EXTENSIONS_RUNTIME=""
	if [[ ${#EXTENSION_SOURCES[@]} -eq 0 ]]; then
		log "Extension bake: Community (no HFL_EXTENSION_SOURCES)"
		return 0
	fi
	local sources_csv
	local IFS=','
	sources_csv="${EXTENSION_SOURCES[*]}"
	unset IFS
	# Scope extension token to the materialize subprocess only — never overwrite
	# global GITHUB_TOKEN (SourceLens / GitHub API may use a different credential).
	apply_mirror_env_defaults
	local ext_token="${HFL_EXTENSION_GIT_TOKEN:-${MIRROR_GITHUB_TOKEN:-}}"
	log "Extension bake: materializing ${sources_csv}"
	HFL_EXTENSIONS_RUNTIME="$(
		HFL_EXTENSION_GIT_TOKEN="${ext_token}" \
			python3 "${ROOT}/tools/extensions/materialize_extensions.py" \
			--repo-root "${ROOT}" \
			--sources "${sources_csv}" \
			--bake-dir "${EXTENSION_BAKE_DIR}" \
			--print-extensions
	)"
	export HFL_EXTENSIONS_RUNTIME
	log "Extension bake: HFL_EXTENSIONS=${HFL_EXTENSIONS_RUNTIME:-<empty>}"
}

prepare_kopia_artifacts() {
	local args=(
		--kopia-mode "${KOPIA_ARTIFACT_MODE}"
		--kopia-git-url "${KOPIA_GIT_URL}"
		--kopia-ref "${KOPIA_GIT_REF}"
	)
	if [[ -n "${MIRROR_GITHUB_DOWNLOAD}" ]]; then
		args+=(--github-download-mirror "${MIRROR_GITHUB_DOWNLOAD}")
	fi
	if [[ -n "${MIRROR_GITHUB_TOKEN}" ]]; then
		args+=(--github-token "${MIRROR_GITHUB_TOKEN}")
	fi
	log "Preparing unified Kopia artifact matrix"
	"${ROOT}/release/build-kopia.sh" "${args[@]}"
}

fetch_host_docker_debs() {
	local args=()
	[[ -n "${MIRROR_APT}" ]] && args+=(--apt-mirror "${MIRROR_APT}")
	[[ -n "${MIRROR_DOCKER_APT}" ]] && args+=(--docker-apt-mirror "${MIRROR_DOCKER_APT}")
	local ubuntu_release
	for ubuntu_release in 20.04 22.04 24.04; do
		log "Fetching host Docker CE debs (ubuntu ${ubuntu_release} amd64)"
		"${ROOT}/tools/dependencies/fetch-docker-ce-debs.sh" \
			--ubuntu-release "${ubuntu_release}" "${args[@]}"
	done
}

stage_host_docker_bundles() {
	local pkg_root=$1 ubuntu_release release_id source_dir destination
	local gateway_dir="${pkg_root}/payload/media/gateway-bootstrap"
	mkdir -p "${gateway_dir}"
	for ubuntu_release in 20.04 22.04 24.04; do
		case "${ubuntu_release}" in
		20.04) release_id=2004 ;;
		22.04) release_id=2204 ;;
		24.04) release_id=2404 ;;
		esac
		source_dir="${ROOT}/build/dependencies/docker/ubuntu-${ubuntu_release}/amd64"
		destination="${gateway_dir}/docker-debs-ubuntu${release_id}-amd64.tar.gz"
		[[ -d "${source_dir}" ]] || die "missing Docker deb cache ${source_dir}"
		tar -C "${source_dir}" -czf "${destination}" .
	done
}

publish_agent() {
	local pkg_root=$1
	local args=(
		--bundle all
		--ubuntu2404-arch "${UBUNTU2404_ARCH}"
		--releases-dir "${pkg_root}/payload/media/agent-releases"
	)
	args+=(--commit "${RELEASE_COMMIT}")
	args+=(--version "${HFL_VERSION}")
	args+=(--kopia-mode "${KOPIA_ARTIFACT_MODE}")
	args+=(--kopia-git-url "${KOPIA_GIT_URL}")
	args+=(--kopia-ref "${KOPIA_GIT_REF}")
	[[ "${FORCE_PULL}" -eq 1 ]] && args+=(--pull)
	[[ -n "${GOPROXY:-}" ]] && args+=(--go-proxy "${GOPROXY}")
	[[ -n "${GOSUMDB:-}" ]] && args+=(--go-sumdb "${GOSUMDB}")
	local mirror
	while IFS= read -r -d '' mirror; do
		args+=("${mirror}")
	done < <(mirror_args || true)
	log "Publishing Agent packages (full bundle, ubuntu2404-arch=${UBUNTU2404_ARCH})"
	"${ROOT}/tools/agent/publish.sh" "${args[@]}"
}

build_sourcelens_bundle() {
	local pkg_root=$1
	local images_dir=$2
	local args=(--pkg-root "${pkg_root}" --images-dir "${images_dir}")
	[[ "${FORCE_PULL}" -eq 1 ]] && args+=(--pull)
	[[ "${NO_CACHE}" -eq 1 ]] && args+=(--no-cache)
	[[ "${SOURCELENS_FORCE_BUILD}" -eq 1 ]] && args+=(--force-build)
	[[ -n "${SOURCELENS_GIT_REF:-}" ]] && args+=(--sourcelens-ref "${SOURCELENS_GIT_REF}")
	[[ -n "${MIRROR_GITHUB_DOWNLOAD}" ]] && args+=(--github-download-mirror "${MIRROR_GITHUB_DOWNLOAD}")
	if [[ -n "${SOURCELENS_GIT_URL:-}" ]]; then
		args+=(--sourcelens-git-url "${SOURCELENS_GIT_URL}")
	fi
	APT_MIRROR="${MIRROR_APT:-}" \
		SOURCELENS_HFL_VERSION="${HFL_VERSION}" \
		SOURCELENS_GIT_TIMEOUT_SECONDS="${SOURCELENS_GIT_TIMEOUT_SECONDS:-120}" \
		SOURCELENS_GIT_RETRIES="${SOURCELENS_GIT_RETRIES:-2}" \
		SOURCELENS_GIT_FALLBACK_TIMEOUT_SECONDS="${SOURCELENS_GIT_FALLBACK_TIMEOUT_SECONDS:-30}" \
		GITHUB_TOKEN="${MIRROR_GITHUB_TOKEN:-${GITHUB_TOKEN:-}}" \
		PIP_INDEX_URL="${PIP_INDEX_URL:-${BUILD_PIP_INDEX_URL:-}}" \
		PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-${BUILD_PIP_TRUSTED_HOST:-}}" \
		BUILD_SOURCELENS="${BUILD_SOURCELENS:-1}" \
		"${RELEASE_DIR}/build-sourcelens.sh" "${args[@]}"
}

read_version() {
	if [[ -n "${OPT_VERSION:-}" ]]; then
		normalize_artifact_id "${OPT_VERSION}"
		return
	fi
	resolve_release_version
}

git_commit_short() {
	if command -v git >/dev/null 2>&1 && git -C "${ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
		git -C "${ROOT}" rev-parse --short HEAD 2>/dev/null || echo "unknown"
	else
		echo "unknown"
	fi
}

preflight() {
	command -v docker >/dev/null 2>&1 || die "docker not found" 2
	docker info >/dev/null 2>&1 || die "docker daemon not reachable"
	command -v python3 >/dev/null 2>&1 || die "python3 not found" 2
	command -v rsync >/dev/null 2>&1 || die "rsync not found" 2
	command -v sha256sum >/dev/null 2>&1 || die "sha256sum not found" 2
	command -v tar >/dev/null 2>&1 || die "tar not found" 2
	command -v gzip >/dev/null 2>&1 || die "gzip not found" 2
	load_sourcelens_build_config
	if [[ "${BUILD_SOURCELENS}" == "1" ]]; then
		command -v git >/dev/null 2>&1 || die "git not found (required for SourceLens bundle)" 2
		if ! docker compose version >/dev/null 2>&1 \
			&& ! command -v docker-compose >/dev/null 2>&1; then
			die "Docker Compose not found (required for SourceLens bundle)" 2
		fi
	fi
}

build_control_plane_images() {
	[[ -n "${HFL_VERSION:-}" ]] || die "HFL_VERSION is not resolved" 2
	local image_version="${HFL_IMAGE_VERSION:-${HFL_VERSION}}"
	local -a common_args=(
		--platform linux/amd64
		--network host
	)
	if [[ "${FORCE_PULL}" -eq 1 ]]; then
		common_args+=(--pull)
	fi
	if [[ "${NO_CACHE}" -eq 1 ]]; then
		common_args+=(--no-cache)
	fi

	log "Building hyperfilelens-backend:${image_version} (alias: latest)"
	docker build "${common_args[@]}" \
		-f "${ROOT}/deploy/docker/backend.Dockerfile" \
		-t "hyperfilelens-backend:${image_version}" \
		-t hyperfilelens-backend:latest \
		--build-arg "APT_MIRROR=${APT_MIRROR:-}" \
		--build-arg "BACKEND_BASE_IMAGE=${HFL_BACKEND_BASE_IMAGE}" \
		--build-arg "PIP_INDEX_URL=${PIP_INDEX_URL:-}" \
		--build-arg "PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST:-}" \
		--build-arg "PIP_TIMEOUT=${PIP_TIMEOUT:-600}" \
		--build-arg "KOPIA_BINARY=${KOPIA_BINARY:-build/kopia/dist/linux/amd64/kopia}" \
		--build-arg "IMAGE_VERSION=${image_version}" \
		--build-arg "IMAGE_REVISION=${RELEASE_COMMIT}" \
		--build-arg "HFL_EXTENSIONS=${HFL_EXTENSIONS_RUNTIME:-}" \
		"${ROOT}"

	log "Building standalone Website artifact for hyperfilelens-frontend:${HFL_VERSION}"
	local -a website_args=(
		--output "${ROOT}/build/website"
		--image-tag "hyperfilelens-website-builder:${HFL_VERSION}"
		--platform linux/amd64
		--base-image "${HFL_WEBSITE_BASE_IMAGE}"
	)
	[[ -z "${NPM_REGISTRY:-}" ]] || website_args+=(--npm-registry "${NPM_REGISTRY}")
	[[ "${NO_CACHE}" -eq 0 ]] || website_args+=(--no-cache)
	[[ "${FORCE_PULL}" -eq 0 ]] || website_args+=(--pull)
	"${ROOT}/website/build.sh" "${website_args[@]}"

	log "Building hyperfilelens-frontend:${image_version} (alias: latest)"
	docker build "${common_args[@]}" \
		-f "${ROOT}/deploy/docker/frontend.Dockerfile" \
		-t "hyperfilelens-frontend:${image_version}" \
		-t hyperfilelens-frontend:latest \
		--build-arg "NPM_REGISTRY=${NPM_REGISTRY:-}" \
		--build-arg "FRONTEND_NODE_BASE_IMAGE=${HFL_FRONTEND_NODE_BASE_IMAGE}" \
		--build-arg "FRONTEND_NGINX_BASE_IMAGE=${HFL_FRONTEND_NGINX_BASE_IMAGE}" \
		--build-arg "VITE_SHOW_EULA=${VITE_SHOW_EULA:-false}" \
		--build-arg "IMAGE_VERSION=${image_version}" \
		--build-arg "IMAGE_REVISION=${RELEASE_COMMIT}" \
		--build-arg "HFL_EXTENSIONS=${HFL_EXTENSIONS_RUNTIME:-}" \
		"${ROOT}"
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

normalize_docker_mirror_host() {
	local mirror="${1:-}"
	mirror="${mirror#https://}"
	mirror="${mirror#http://}"
	mirror="${mirror%/}"
	printf '%s' "${mirror}"
}

docker_mirror_image_ref() {
	local image=$1
	local mirror_host=$2
	if [[ -z "${mirror_host}" ]]; then
		printf '%s' "${image}"
		return 0
	fi
	if [[ "${image}" == */* ]]; then
		printf '%s/%s' "${mirror_host}" "${image}"
	else
		printf '%s/library/%s' "${mirror_host}" "${image}"
	fi
}

image_exists_locally() {
	docker image inspect "$1" >/dev/null 2>&1
}

pull_image() {
	local image=$1
	local mirror_host mirrored

	if [[ "${FORCE_PULL}" -eq 0 ]] && image_exists_locally "${image}"; then
		log "Using local ${image} (skip pull; pass --pull to refresh from registry)"
		return 0
	fi

	mirror_host="$(normalize_docker_mirror_host "${MIRROR_DOCKER_DOWNLOAD}")"
	if [[ -n "${mirror_host}" ]]; then
		mirrored="$(docker_mirror_image_ref "${image}" "${mirror_host}")"
		if [[ "${FORCE_PULL}" -eq 0 ]] && image_exists_locally "${mirrored}"; then
			log "Using local ${mirrored}, tagging as ${image}"
			docker tag "${mirrored}" "${image%@*}"
			return 0
		fi
		log "Pulling ${mirrored} via mirror ${mirror_host}..."
		if docker pull --help 2>&1 | grep -q -- '--progress'; then
			if docker pull --progress=plain "${mirrored}"; then
				docker tag "${mirrored}" "${image%@*}"
				return 0
			fi
		elif docker pull "${mirrored}"; then
			docker tag "${mirrored}" "${image%@*}"
			return 0
		fi
		log "Mirror pull failed, trying docker.io ${image}..."
	fi

	log "Pulling ${image} from docker.io..."
	if docker pull --help 2>&1 | grep -q -- '--progress'; then
		docker pull --progress=plain "${image}"
	else
		docker pull "${image}"
	fi
}

log_image_digest() {
	local image=$1
	local digest
	digest="$(image_digest "${image}")"
	log "  ${image} → ${digest}"
	printf '%s' "${digest}"
}

image_digest() {
	local image=$1
	local digest
	digest="$(docker image inspect "${image}" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
	if [[ -z "${digest}" ]]; then
		digest="$(docker image inspect "${image}" --format '{{.Id}}' 2>/dev/null || true)"
	fi
	[[ -n "${digest}" ]] || digest="${image}"
	printf '%s' "${digest}"
}

save_image_archive() {
	local archive=$1
	shift
	local part="${archive}.part"
	rm -f "${part}"
	hfl_docker_save_gz "${part}" "$@"
	mv -f "${part}" "${archive}"
}

save_images() {
	local images_dir=$1
	local archive image_version="${HFL_IMAGE_VERSION:-${HFL_VERSION}}"
	mkdir -p "${images_dir}"

	log "Saving hyperfilelens backend + frontend images..."
	archive="${images_dir}/00-hyperfilelens.tar.gz"
	save_image_archive "${archive}" \
		"hyperfilelens-backend:${image_version}" hyperfilelens-backend:latest \
		"hyperfilelens-frontend:${image_version}" hyperfilelens-frontend:latest
	log "  wrote $(du -h "${archive}" | awk '{print $1}') ${archive##*/}"

	log "Saving hyperfilelens-postgres:17..."
	archive="${images_dir}/01-postgres-17.tar.gz"
	docker tag postgres:17 hyperfilelens-postgres:17
	save_image_archive "${archive}" hyperfilelens-postgres:17
	log "  wrote $(du -h "${archive}" | awk '{print $1}') ${archive##*/}"

	log "Saving hyperfilelens-redis:alpine..."
	archive="${images_dir}/02-redis-alpine.tar.gz"
	docker tag redis:alpine hyperfilelens-redis:alpine
	save_image_archive "${archive}" hyperfilelens-redis:alpine
	log "  wrote $(du -h "${archive}" | awk '{print $1}') ${archive##*/}"
}

write_manifest() {
	local pkg_root=$1
	local version=$2
	local commit=$3
	local payload_sha=$4
	local backend_digest=$5
	local frontend_digest=$6
	local postgres_digest=$7
	local redis_digest=$8
	local built_at version_file build_info
	built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	version_file="${ROOT}/tools/dependencies/versions/docker-ce.env"
	build_info="${pkg_root}/sourcelens/BUILD_INFO.json"

	python3 - "${pkg_root}/MANIFEST.json" "${version}" "${built_at}" "${commit}" \
		"${payload_sha}" \
		"${backend_digest}" "${frontend_digest}" "${postgres_digest}" "${redis_digest}" \
		"${version_file}" "${build_info}" \
		"${HFL_RELEASE_EDITION:-community}" "${HFL_IMAGE_VERSION:-${version}}" \
		"${HFL_EXTENSION_EXPECTED_COMMIT:-}" <<'PY'
import hashlib
import json
import pathlib
import re
import sys
import tarfile

(
    out_path,
    version,
    built_at,
    commit,
    payload_sha,
    backend_d,
    frontend_d,
    postgres_d,
    redis_d,
    version_file,
    build_info_path,
    edition,
    image_version,
    extension_commit,
) = sys.argv[1:15]
pkg_root = pathlib.Path(out_path).parent

pins = {}
for line in pathlib.Path(version_file).read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    pins[key.strip()] = value.strip()

def display_engine_version(raw: str) -> str:
    m = re.search(r"(\d+\.\d+\.\d+)", raw)
    return m.group(1) if m else raw


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


docker_bundles = []
for ubuntu_release, release_id in (("20.04", "2004"), ("22.04", "2204"), ("24.04", "2404")):
    relative = pathlib.Path(
        f"payload/media/gateway-bootstrap/docker-debs-ubuntu{release_id}-amd64.tar.gz"
    )
    archive = pkg_root / relative
    if not archive.is_file():
        raise SystemExit(f"missing Docker offline bundle: {relative}")
    with tarfile.open(archive, "r:gz") as bundle_archive:
        manifest_member = next(
            (member for member in bundle_archive.getmembers() if member.name.lstrip("./") == "MANIFEST.json"),
            None,
        )
        if manifest_member is None:
            raise SystemExit(f"Docker offline bundle has no MANIFEST.json: {relative}")
        stream = bundle_archive.extractfile(manifest_member)
        if stream is None:
            raise SystemExit(f"Docker offline bundle manifest cannot be read: {relative}")
        bundle_manifest = json.load(stream)
    docker_bundles.append(
        {
            "ubuntu_release": ubuntu_release,
            "arch": "amd64",
            "file": relative.as_posix(),
            "size": archive.stat().st_size,
            "sha256": sha256_file(archive),
            "versions": bundle_manifest.get("versions", {}),
        }
    )

language_packs = []
language_pack_ids = set()
language_pack_dir = pkg_root / "payload/language-packs"
if not language_pack_dir.is_dir():
    raise SystemExit("missing bundled language-pack directory: payload/language-packs")
for archive in sorted(language_pack_dir.glob("*.tar.gz")):
    relative = archive.relative_to(pkg_root)
    with tarfile.open(archive, "r:gz") as pack_archive:
        members = pack_archive.getmembers()
        for member in members:
            member_path = pathlib.PurePosixPath(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise SystemExit(f"language pack has an unsafe path: {relative}")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise SystemExit(f"language pack has an unsupported entry: {relative}")
        manifest_members = [
            member
            for member in members
            if member.name in {"manifest.json", "./manifest.json"} and member.isfile()
        ]
        if len(manifest_members) != 1:
            raise SystemExit(f"language pack has no unique manifest.json: {relative}")
        stream = pack_archive.extractfile(manifest_members[0])
        if stream is None:
            raise SystemExit(f"language-pack manifest cannot be read: {relative}")
        pack_manifest = json.load(stream)
    pack_id = str(pack_manifest.get("id") or "")
    display_name = str(pack_manifest.get("display_name") or "").strip()
    pack_version = str(pack_manifest.get("version") or "")
    compatible_app = str(pack_manifest.get("compatible_app") or "")
    expected_name = f"hyperfilelens-lang-{pack_id}-{version}.tar.gz"
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", pack_id):
        raise SystemExit(f"language pack has an invalid id: {relative}")
    if pack_manifest.get("schema") != 2 or not display_name:
        raise SystemExit(f"bundled language pack must use schema 2 and a display name: {relative}")
    if pack_id in language_pack_ids:
        raise SystemExit(f"duplicate bundled language pack id: {pack_id}")
    if pack_version != version or compatible_app != f"=={version}":
        raise SystemExit(f"language pack does not exactly match application {version}: {relative}")
    if archive.name != expected_name:
        raise SystemExit(
            f"language-pack filename mismatch: expected {expected_name}, got {archive.name}"
        )
    language_pack_ids.add(pack_id)
    language_packs.append(
        {
            "id": pack_id,
            "display_name": display_name,
            "version": pack_version,
            "file": relative.as_posix(),
            "size": archive.stat().st_size,
            "sha256": sha256_file(archive),
        }
    )
if "zh-hans" not in language_pack_ids:
    raise SystemExit("required bundled language pack is missing: zh-hans")

images = [
    {
        "file": "images/00-hyperfilelens.tar.gz",
        "refs": [
            f"hyperfilelens-backend:{image_version}",
            "hyperfilelens-backend:latest",
            f"hyperfilelens-frontend:{image_version}",
            "hyperfilelens-frontend:latest",
        ],
        "digests": [backend_d, frontend_d],
        "role": "hyperfilelens",
    },
    {
        "file": "images/01-postgres-17.tar.gz",
        "refs": ["hyperfilelens-postgres:17"],
        "digests": [postgres_d],
        "role": "shared",
    },
    {
        "file": "images/02-redis-alpine.tar.gz",
        "refs": ["hyperfilelens-redis:alpine"],
        "digests": [redis_d],
        "role": "shared",
    },
]

sourcelens = {"enabled": False}
build_info = pathlib.Path(build_info_path)
if build_info.is_file():
    info = json.loads(build_info.read_text(encoding="utf-8"))
    sourcelens = {
        "enabled": True,
        "git_url": info.get("git_url", ""),
        "git_ref": info.get("git_ref", ""),
        "git_commit": info.get("git_commit", ""),
        "git_commit_short": info.get("git_commit_short", ""),
        "version": info.get("version", ""),
        "patchset_sha256": info.get(
            "patchset_sha256", info.get("patch_sha256", "")
        ),
        "patches": info.get("patches", []),
        "build_adapter_sha256": info.get("build_adapter_sha256", ""),
        "build_compose_file": info.get("build_compose_file", ""),
        "network": info.get("network", "hyperfilelens-bridge"),
        "install_dir": info.get("install_dir", "/opt/hyperfilelens/sourcelens"),
        "lensnode_image": info.get("lensnode_image", "sourcelens-lensnode:latest"),
    }
    images.extend(
        [
            {
                "file": "images/10-sourcelens-app.tar.gz",
                "refs": [
                    info["images"]["backend"]["ref"],
                    f"{info['images']['backend']['ref'].rsplit(':', 1)[0]}:latest",
                    info["images"]["frontend"]["ref"],
                    f"{info['images']['frontend']['ref'].rsplit(':', 1)[0]}:latest",
                ],
                "digests": [
                    info["images"]["backend"]["digest"],
                    info["images"]["frontend"]["digest"],
                ],
                "source_refs": [
                    info["images"]["backend"].get("upstream_ref", ""),
                    info["images"]["frontend"].get("upstream_ref", ""),
                ],
                "role": "sourcelens-app",
            },
            {
                "file": "images/11-sourcelens-lensnode.tar.gz",
                "refs": [
                    info["images"]["lensnode"]["ref"],
                    f"{info['images']['lensnode']['ref'].rsplit(':', 1)[0]}:latest",
                    info.get("lensnode_image", "sourcelens-lensnode:latest"),
                ],
                "digests": [info["images"]["lensnode"]["digest"]],
                "source_refs": [info["images"]["lensnode"].get("upstream_ref", "")],
                "role": "sourcelens-lensnode",
            },
            {
                "file": "images/12-nginx-stable-alpine.tar.gz",
                "refs": ["hyperfilelens-sourcelens-nginx:stable-alpine"],
                "digests": [info["images"]["nginx"]["digest"]],
                "role": "sourcelens-nginx",
            },
        ]
    )

for image in images:
    archive = pkg_root / image["file"]
    if not archive.is_file():
        raise SystemExit(f"missing image archive: {image['file']}")
    image["sha256"] = sha256_file(archive)

tls_artifacts = {}
for role, relative in (
    ("server_certificate", "deploy/nginx/certs/tls.crt"),
    ("server_private_key", "deploy/nginx/certs/tls.key"),
    ("root_ca", "deploy/nginx/certs/root-ca.crt"),
):
    path = pkg_root / relative
    if not path.is_file():
        raise SystemExit(f"missing default TLS artifact: {relative}")
    tls_artifacts[role] = {
        "file": relative,
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }

manifest = {
    "schema_version": 2,
    "product": "hyperfilelens",
    "edition": edition,
    "image_version": image_version,
    "runtime_images": {
        "backend": f"hyperfilelens-backend:{image_version}",
        "frontend": f"hyperfilelens-frontend:{image_version}",
    },
    "channel": "main" if version.startswith("main-") else "release",
    "artifact_id": version if version.startswith("main-") else f"v{version}",
    "built_at": built_at,
    "minimum_upgrade_version": "0.1.34",
    "git_commit": commit,
    "host_runtime": {
        "os_id": "ubuntu",
        "os_versions": ["20.04", "22.04", "24.04"],
        "arch": "amd64",
        "docker": {
            "engine_version": display_engine_version(pins.get("ENGINE_VERSION", "")),
            "compose_plugin_version": display_engine_version(pins.get("COMPOSE_PLUGIN_VERSION", "")),
            "min_engine_version": pins.get("MIN_ENGINE_VERSION", "24.0.0"),
            "min_compose_version": "2.20.0",
            "bundles": docker_bundles,
        },
    },
    "sourcelens": sourcelens,
    "language_packs": language_packs,
    "images": images,
    "artifacts": {
        "payload_tree_sha256": payload_sha,
        "agent_version": version,
        "default_tls": tls_artifacts,
    },
}
if edition not in {"community", "enterprise"}:
    raise SystemExit(f"invalid release edition: {edition}")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", image_version):
    raise SystemExit(f"invalid image version: {image_version}")
if edition == "enterprise":
    if not re.fullmatch(r"[0-9a-f]{40}", extension_commit):
        raise SystemExit("Enterprise release requires an immutable extension commit")
    manifest["extension_commit"] = extension_commit
elif extension_commit:
    raise SystemExit("Community release must not identify an Enterprise extension commit")
if manifest["channel"] == "release":
    manifest["version"] = version
pathlib.Path(out_path).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY
}

validate_release_publish_artifacts() {
	local pkg_root=$1
	local releases="${pkg_root}/payload/media/agent-releases"
	local enroll="${pkg_root}/payload/media/enroll-bootstrap"
	[[ -f "${pkg_root}/deploy/nginx/web.conf" ]] \
		|| die "release package missing internal Web pool configuration"
	[[ -f "${pkg_root}/deploy/nginx/snippets/hfl-active-upstreams.conf" ]] \
		|| die "release package missing blue/green upstream configuration"
	[[ -f "${pkg_root}/deploy/nginx/snippets/check-language-packs.sh" ]] \
		|| die "release package missing language-pack health check"
	[[ -f "${pkg_root}/deploy/blue-green/active-color" ]] \
		|| die "release package missing blue/green initial state"
	[[ -d "${releases}" && -n "$(ls -A "${releases}" 2>/dev/null)" ]] \
		|| die "release package missing agent-releases artifacts"
	[[ -d "${enroll}" && -n "$(ls -A "${enroll}" 2>/dev/null)" ]] \
		|| die "release package missing enroll-bootstrap artifacts"
	[[ -s "${enroll}/INSTALLER_MANIFEST.json" ]] \
		|| die "release package missing minimal installer manifest"
	local installer_count
	installer_count="$(jq -r 'select(.schema_version == 1) | .artifacts | length' \
		"${enroll}/INSTALLER_MANIFEST.json")"
	[[ "${installer_count}" == "5" ]] \
		|| die "release package minimal installer manifest must contain five platforms"
	while IFS=$'\t' read -r installer_file installer_sha installer_size; do
		[[ -s "${enroll}/${installer_file}" ]] \
			|| die "release package missing minimal installer ${installer_file}"
		[[ "$(stat -c '%s' "${enroll}/${installer_file}")" == "${installer_size}" ]] \
			|| die "release package minimal installer size mismatch: ${installer_file}"
		((installer_size <= 3670016)) \
			|| die "release package minimal installer exceeds 3.5 MiB: ${installer_file}"
		[[ "$(sha256sum "${enroll}/${installer_file}" | cut -d' ' -f1)" == "${installer_sha}" ]] \
			|| die "release package minimal installer checksum mismatch: ${installer_file}"
	done < <(jq -r '.artifacts[] | [.filename, .sha256, (.size | tostring)] | @tsv' \
		"${enroll}/INSTALLER_MANIFEST.json")
	if [[ ! -d "${pkg_root}/sourcelens" ]]; then
		return 0
	fi
	local gb="${pkg_root}/payload/media/gateway-bootstrap"
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
		[[ -f "${gb}/${name}" ]] || die "BUILD_SOURCELENS=1 but missing gateway-bootstrap/${name}"
	done
	log "Publish artifact validation passed"
}

write_package_readme() {
	local pkg_root=$1
	local version=$2
	local edition=${3:-community} edition_suffix=""
	[[ "${edition}" == community ]] || edition_suffix="-ee"
	cat > "${pkg_root}/README.md" <<EOF
# HyperFileLens ${version}

Release installation package for **Ubuntu 20.04/22.04/24.04 amd64** air-gap hosts.
Includes OS-specific offline Docker CE archives plus application container images.
When bundled, SourceLens runs from \`/opt/hyperfilelens/sourcelens\` on the private
\`hyperfilelens-bridge\` Docker network. External Agent and LensNode traffic
enters through the configured HyperFileLens Tenant HTTPS endpoint.

## Host requirements

### Minimum (PoC / lab)

- Ubuntu **20.04, 22.04, or 24.04 amd64**
- 2 CPU cores · 4GB RAM · 100GB system disk
- Docker Engine ≥ 24.0 with Compose v2 ≥ 2.20, or no existing Docker installation
- The configured Tenant, Platform Operations, and bundled SourceLens Console host ports

### Recommended (production)

- Ubuntu 20.04 / 22.04 / 24.04 amd64
- 4 CPU cores · 8GB–16GB RAM · 200GB+ SSD

### Notes

- **amd64 only**; ARM64 is not supported for the control-plane host.
- OS-specific Docker packages target Ubuntu 20.04, 22.04, and 24.04 amd64.
- Existing healthy Docker is reused; an existing but unsuitable installation causes a safe failure.
- When Docker is absent, the installer uses the matching offline archive without network access.
- The 2 CPU / 4GB baseline is intended for light workloads; use 8GB+ for sustained production scans.
- CPU, memory, and Swap recommendations warn but do not block installation.
- Runtime containers use the same fixed resource ceilings in every environment.

## Install

\`\`\`bash
tar xzf hyperfilelens-${version}${edition_suffix}.tar.gz
cd hyperfilelens-${version}${edition_suffix}
sudo ./install.sh install
\`\`\`

## Backup

Create a verified managed backup set at any time:

\`\`\`bash
sudo ./install.sh backup
\`\`\`

Upgrades create the same backup automatically. The installer retains the latest
three valid backup sets and never prunes existing valid backups when a new backup
cannot be completed.

The default \`SOURCELENS_MODE=bundled\` deploys the packaged SourceLens stack and configures its private bridge URL.
Set \`SOURCELENS_MODE=external\` and \`LENS_BASE_URL=https://sourcelens.example.com\` to use an existing platform without installing, stopping, or upgrading it. Use \`sudo ./install.sh install --hfl-only\` for a one-time SourceLens skip.

The installer prints the effective Tenant, Platform Operations, Django Admin, and bundled SourceLens Console URLs from the six host-publishing settings in \`.env\`.

After \`install\`, the script prints the console URL and fixed initial login credentials from \`.env\`:

- HFL defaults to \`admin@hyperfilelens.com\` / \`Admin@123\`
- bundled SourceLens defaults to \`admin\` / \`adminpassword\`
- \`SEED_INITIAL_DATA=1\` enables first-run seeding via the worker service

Passwords changed in either database are not reset by upgrades. Change the public defaults after first login unless external access controls provide the required protection.

The package includes the repository-pinned TLS identity under \`deploy/nginx/certs/\`.
Install \`root-ca.crt\` into a client trust store to remove warnings for covered local names.
Existing complete \`tls.crt\` / \`tls.key\` pairs are preserved during upgrade; an incomplete pair stops the upgrade before services are changed.

## Upgrade

\`\`\`bash
sudo ./install.sh upgrade --from /path/to/hyperfilelens-<version>.tar.gz
sudo ./install.sh upgrade --from /path/to/new.tar.gz --hfl-only
sudo ./install.sh upgrade --from /path/to/new.tar.gz --remove-sourcelens
\`\`\`

When the package includes \`sourcelens/\`, upgrade also refreshes SourceLens under \`/opt/hyperfilelens/sourcelens\` and updates
\`data/media/gateway-bootstrap/\` / \`data/media/enroll-bootstrap/\` publish artifacts on this host. User-managed Data Gateways
are not upgraded automatically; new enrollments and offline DG installs use the updated files. The installer-managed local
Platform Gateway converges to the control-plane Agent version and only recreates LensNode when its image changes.
Agent release media retention keeps the desired and locally installed versions, the latest three Main builds, and the latest
three formal releases. An interrupted local Agent upgrade is also protected until a later successful deployment.

## Language packs

Bundled language packs are installed with the application, while English remains the default and fallback language.

\`\`\`bash
sudo ./install.sh lang-pack list
sudo ./install.sh lang-pack install --id zh-hans
sudo ./install.sh lang-pack uninstall zh-hans
\`\`\`

An explicitly uninstalled bundled pack remains disabled across upgrades. Use \`install --id\` to enable it again.

## Uninstall

\`\`\`bash
sudo ./install.sh uninstall
sudo ./install.sh uninstall --with-sourcelens
sudo ./install.sh uninstall --purge-all
\`\`\`

Plain uninstall removes only the HyperFileLens runtime and preserves its data, bundled SourceLens, and the installer-managed
local Platform Data Gateway. \`--purge-all\` removes the complete installer-managed runtime and data while retaining the
release directory, managed backup sets, and host Docker CE.

## Commands

\`install\` | \`start\` | \`stop\` | \`restart\` | \`status\` | \`lang-pack\` | \`uninstall\` | \`upgrade\`
EOF
}

main() {
	parse_args "$@"
	hfl_logging_configure release "${LOG_FILE}" "${VERBOSE}"
	apply_mirror_env_defaults
	apply_go_proxy_env_defaults
	validate_docker_pull_timeout
	apply_ubuntu2404_arch_default
	if [[ "${PRINT_CONFIG}" -eq 1 ]]; then
		print_config
		return 0
	fi
	hfl_logging_start
	preflight
	log "Checking the English source boundary"
	python3 "${ROOT}/tools/quality/check-english-source.py"
	local version commit_full commit7 release_commit pkg_name pkg_root images_dir tar_path tar_basename edition edition_suffix
	version="$(read_version)"
	HFL_VERSION="${version}"
	commit_full="$(resolve_commit_full "${ROOT}")"
	commit7="$(resolve_commit7 "${ROOT}")"
	release_commit="${commit_full}"
	RELEASE_COMMIT="${release_commit}"
	edition=community
	[[ ${#EXTENSION_SOURCES[@]} -eq 0 ]] || edition=enterprise
	edition_suffix=""
	[[ "${edition}" == community ]] || edition_suffix="-ee"
	pkg_name="hyperfilelens-${version}${edition_suffix}"
	if [[ -n "${PACKAGE_BASENAME:-}" ]]; then
		tar_basename="${PACKAGE_BASENAME}"
	else
		tar_basename="$(release_package_basename_for_version "${version}" "${commit7}" "${edition}")"
	fi
	safe_assert_package_basename "${tar_basename}"
	pkg_root="${STAGING_BASE}/${pkg_name}"
	images_dir="${pkg_root}/images"

	HFL_RELEASE_EDITION="${edition}"
	HFL_IMAGE_VERSION="${version}${edition_suffix}"
	export HFL_RELEASE_EDITION HFL_IMAGE_VERSION
	log "Version ${version} (${edition}, git ${commit7}, ${commit_full})"
	safe_assert_staging_pkg_root "${pkg_root}" "${STAGING_BASE}"
	safe_rm_dir "${pkg_root}"
	mkdir -p "${images_dir}"
	mkdir -p "${pkg_root}/deploy/nginx/certs"

	log "Preparing Kopia artifacts for Backend and Agent packaging"
	prepare_kopia_artifacts

	log "Fetching host Docker CE debs (ubuntu 20.04/22.04/24.04 amd64)"
	fetch_host_docker_debs

	log "Preparing Open Core extension bake for control-plane images"
	prepare_extension_bake

	log "Building control-plane Docker images"
	export_build_mirror_env
	build_control_plane_images

	log "Building SourceLens bundle (BUILD_SOURCELENS=${BUILD_SOURCELENS:-1})"
	build_sourcelens_bundle "${pkg_root}" "${images_dir}"

	publish_agent "${pkg_root}"
	stage_host_docker_bundles "${pkg_root}"

	log "Pulling third-party runtime images"
	local postgres_digest redis_digest backend_digest frontend_digest
	pull_image "${POSTGRES_IMAGE}"
	docker tag "${POSTGRES_IMAGE%@*}" postgres:17
	postgres_digest="${POSTGRES_IMAGE%%:*}@${POSTGRES_IMAGE##*@}"
	log "  postgres:17 → ${postgres_digest}"
	pull_image "${REDIS_IMAGE}"
	docker tag "${REDIS_IMAGE%@*}" redis:alpine
	redis_digest="${REDIS_IMAGE%%:*}@${REDIS_IMAGE##*@}"
	log "  redis:alpine → ${redis_digest}"
	backend_digest="$(log_image_digest hyperfilelens-backend:latest)"
	frontend_digest="$(log_image_digest hyperfilelens-frontend:latest)"

	save_images "${images_dir}"

	log "Staging package files"
	printf '%s\n' "${version}" > "${pkg_root}/VERSION"
	cp "${ROOT}/deploy/docker-compose.yml" "${pkg_root}/docker-compose.yml"
	stage_release_env_example "${pkg_root}"
	cp "${ROOT}/LICENSE" "${pkg_root}/LICENSE"
	stage_default_tls_bundle "${pkg_root}"
	cp "${ROOT}/deploy/nginx/default.conf" "${pkg_root}/deploy/nginx/default.conf"
	cp "${ROOT}/deploy/nginx/web.conf" "${pkg_root}/deploy/nginx/web.conf"
	mkdir -p "${pkg_root}/deploy/nginx/snippets"
	rsync -a "${ROOT}/deploy/nginx/snippets/" "${pkg_root}/deploy/nginx/snippets/"
	mkdir -p "${pkg_root}/deploy/blue-green"
	cp "${ROOT}/deploy/blue-green/active-color" "${pkg_root}/deploy/blue-green/active-color"
	cp "${ROOT}/deploy/installer/install.sh" "${pkg_root}/install.sh"
	cp "${ROOT}/deploy/installer/apply-runtime-config.py" "${pkg_root}/apply-runtime-config.py"
	cp "${ROOT}/tools/config/sync_env.py" "${pkg_root}/sync-env.py"
	chmod +x "${pkg_root}/install.sh" "${pkg_root}/apply-runtime-config.py" "${pkg_root}/sync-env.py"
	mkdir -p "${pkg_root}/deploy/logrotate"
	cp "${ROOT}/deploy/logrotate/hyperfilelens.conf" "${pkg_root}/deploy/logrotate/hyperfilelens.conf"
	stage_local_language_packs "${pkg_root}" "${version}"

	normalize_release_permissions "${pkg_root}"

	local payload_sha
	payload_sha="$(tree_sha256 "${pkg_root}/payload")"

	write_manifest "${pkg_root}" "${version}" "${release_commit}" "${payload_sha}" \
		"${backend_digest}" "${frontend_digest}" "${postgres_digest}" "${redis_digest}"
	validate_release_publish_artifacts "${pkg_root}"
	write_package_readme "${pkg_root}" "${version}" "${edition}"
	validate_release_security "${pkg_root}"

	mkdir -p "${DIST_DIR}"
	tar_path="${DIST_DIR}/${tar_basename}"
	local tar_tmp="${tar_path}.part"
	rm -f "${tar_tmp}"
	log "Creating ${tar_path}"
	tar_create_gz "${tar_tmp}" "${STAGING_BASE}" "${pkg_name}"
	mv -f "${tar_tmp}" "${tar_path}"
	chmod 644 "${tar_path}"
	python3 "${ROOT}/release/ci/report-release-size.py" "${pkg_root}" "${tar_path}"
	cp "${ROOT}/deploy/nginx/certs/root-ca.crt" "${DIST_DIR}/hyperfilelens-root-ca.crt"
	chmod 644 "${DIST_DIR}/hyperfilelens-root-ca.crt"
	(
		cd "${DIST_DIR}"
		sha256sum "$(basename "${tar_path}")" hyperfilelens-root-ca.crt >SHA256SUMS
	)

	log "Package sizes:"
	du -sh "${images_dir}" "${pkg_root}/payload" "${tar_path}" || true
	log "Done: ${tar_path}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	hfl_logging_configure release
	trap 'rc=$?; hfl_logging_finish "${rc}"' EXIT
	trap 'exit 130' INT TERM
	main "$@"
fi
