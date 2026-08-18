#!/usr/bin/env bash
# Build SourceLens container images and stage offline runtime files into a release package.
# Invoked from release/build.sh when BUILD_SOURCELENS=1.
set -euo pipefail
export COPYFILE_DISABLE=1
umask 022

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../tools/sourcelens/common.sh
source "${ROOT}/tools/sourcelens/common.sh"
# shellcheck source=../tools/lib/archive.sh
source "${ROOT}/tools/lib/archive.sh"

log() { hfl_log_info "$@"; }
die() { hfl_die "$1" "${2:-1}"; }

PKG_ROOT=""
IMAGES_DIR=""
FORCE_PULL=0
NO_CACHE=0
FORCE_BUILD="${SOURCELENS_FORCE_BUILD:-0}"
PREBUILT=0
LOG_FILE="${HFL_LOG_FILE:-}"
VERBOSE="${HFL_LOG_VERBOSE:-0}"
PRINT_CONFIG=0

require_value() {
	if [[ $# -lt 2 || -z "${2:-}" || "${2:0:1}" == "-" ]]; then
		die "${1} requires a value" 2
	fi
}

usage() {
	cat <<'USAGE'
Usage: ./release/build-sourcelens.sh --pkg-root DIR --images-dir DIR [options]

  --pkg-root DIR       Release package staging root (hyperfilelens-<version>/)
  --images-dir DIR     images/ directory inside the package root
  --pull               Pull nginx:stable-alpine even when present locally
  --no-cache           Rebuild SourceLens Docker layers without BuildKit cache
  --force-build        Rebuild SourceLens images even when the build stamp matches
  --prebuilt           Package normalized images already loaded in Docker
  --sourcelens-git-url URL  Override SOURCELENS_GIT_URL
  --sourcelens-ref REF      SourceLens release tag in vX.Y.Z form
  --github-download-mirror URL  GitHub Git mirror with official fallback
  --github-token TOKEN GitHub token for private repo clone/fetch (env: GITHUB_TOKEN)
  --apt-mirror URL     Ubuntu/Debian apt mirror (env: APT_MIRROR)
  --pip-index-url URL  Python package index (env: PIP_INDEX_URL)
  --pip-trusted-host HOST  Trusted pip host (env: PIP_TRUSTED_HOST)
  --npm-registry URL   npm registry (env: NPM_REGISTRY)
  --log-file FILE      Append runtime logs to FILE
  --verbose            Enable debug logs
  --print-config       Print effective non-secret configuration and exit
  --no-build           Skip when BUILD_SOURCELENS=0 (default: exit 0 immediately)
USAGE
}

print_config() {
	cat <<EOF
package_root=${PKG_ROOT:-<required>}
images_dir=${IMAGES_DIR:-<required>}
source_dir=${SOURCELENS_SOURCE_CACHE}
build_source_dir=${SOURCELENS_BUILD_SOURCE}
git_url=${SOURCELENS_GIT_URL}
git_ref=${SOURCELENS_GIT_REF}
version=${SOURCELENS_VERSION}
upstream_image_prefix=${SOURCELENS_UPSTREAM_IMAGE_PREFIX}
image_registry=${SOURCELENS_IMAGE_REGISTRY:-<local>}
backend_image=$(sourcelens_backend_image_ref)
frontend_image=$(sourcelens_frontend_image_ref)
lensnode_image=$(sourcelens_lensnode_image_ref)
docker_platform=${SOURCELENS_DOCKER_PLATFORM}
build_sourcelens=${BUILD_SOURCELENS}
force_build=${FORCE_BUILD}
force_pull=${FORCE_PULL}
no_cache=${NO_CACHE}
prebuilt=${PREBUILT}
github_download_mirror=${GITHUB_DOWNLOAD_MIRROR:-<official>}
github_fallback_timeout=${SOURCELENS_GIT_FALLBACK_TIMEOUT_SECONDS}
github_token=$(hfl_redact "${GITHUB_TOKEN:-}")
apt_mirror=${SOURCELENS_APT_MIRROR:-<official>}
pip_index_url=${SOURCELENS_PIP_INDEX_URL:-<official>}
pip_trusted_host=${SOURCELENS_PIP_TRUSTED_HOST:-<unset>}
npm_registry=${SOURCELENS_NPM_REGISTRY:-<official>}
log_file=${LOG_FILE:-<none>}
verbose=${VERBOSE}
EOF
}

parse_args() {
	while [[ $# -gt 0 ]]; do
		case "$1" in
		-h | --help)
			usage
			exit 0
			;;
		--pkg-root)
			require_value "$1" "${2:-}"
			PKG_ROOT="$2"
			shift 2
			;;
		--images-dir)
			require_value "$1" "${2:-}"
			IMAGES_DIR="$2"
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
			FORCE_BUILD=1
			shift
			;;
		--prebuilt)
			PREBUILT=1
			shift
			;;
		--sourcelens-git-url | --git-url)
			require_value "$1" "${2:-}"
			SOURCELENS_GIT_URL="$2"
			shift 2
			;;
		--sourcelens-ref | --git-ref)
			require_value "$1" "${2:-}"
			SOURCELENS_GIT_REF="$2"
			shift 2
			;;
		--github-download-mirror)
			require_value "$1" "${2:-}"
			GITHUB_DOWNLOAD_MIRROR="${2%/}"
			shift 2
			;;
		--apt-mirror)
			require_value "$1" "${2:-}"
			SOURCELENS_APT_MIRROR="$2"
			shift 2
			;;
		--pip-index-url)
			require_value "$1" "${2:-}"
			SOURCELENS_PIP_INDEX_URL="$2"
			shift 2
			;;
		--pip-trusted-host)
			require_value "$1" "${2:-}"
			SOURCELENS_PIP_TRUSTED_HOST="$2"
			shift 2
			;;
		--npm-registry)
			require_value "$1" "${2:-}"
			SOURCELENS_NPM_REGISTRY="$2"
			shift 2
			;;
		--no-build)
			BUILD_SOURCELENS=0
			shift
			;;
		--log-file)
			require_value "$1" "${2:-}"
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
		--github-token)
			require_value "$1" "${2:-}"
			GITHUB_TOKEN="$2"
			shift 2
			;;
		*)
			die "unknown argument: $1" 2
			;;
		esac
	done
}

validate_staging_paths() {
	local pkg_abs images_abs allowed
	pkg_abs="$(realpath -m "${PKG_ROOT}")"
	images_abs="$(realpath -m "${IMAGES_DIR}")"
	allowed="$(realpath -m "${ROOT}/build/release/staging")/"
	[[ "${pkg_abs}/" == "${allowed}"* ]] || die "--pkg-root must be below ${allowed%/}" 2
	[[ "${images_abs}" == "${pkg_abs}/images" ]] || die "--images-dir must be <pkg-root>/images" 2
	PKG_ROOT="${pkg_abs}"
	IMAGES_DIR="${images_abs}"
}

save_image_archive() {
	local archive=$1
	shift
	local part="${archive}.part"
	rm -f "${part}"
	hfl_docker_save_gz "${part}" "$@"
	mv -f "${part}" "${archive}"
}

save_image_archives() {
	local archive backend_ref frontend_ref lensnode_ref
	local -a lensnode_refs
	backend_ref="$(sourcelens_backend_image_ref)"
	frontend_ref="$(sourcelens_frontend_image_ref)"
	lensnode_ref="$(sourcelens_lensnode_image_ref)"
	mkdir -p "${IMAGES_DIR}"

	log "Saving SourceLens application images..."
	archive="${IMAGES_DIR}/10-sourcelens-app.tar.gz"
	save_image_archive "${archive}" \
		"${backend_ref}" "$(sourcelens_backend_image_ref latest)" \
		"${frontend_ref}" "$(sourcelens_frontend_image_ref latest)"
	log "  wrote $(du -h "${archive}" | awk '{print $1}') ${archive##*/}"

	log "Saving SourceLens LensNode image..."
	archive="${IMAGES_DIR}/11-sourcelens-lensnode.tar.gz"
	lensnode_refs=("${lensnode_ref}" "$(sourcelens_lensnode_image_ref latest)")
	if [[ "${SOURCELENS_LENSNODE_IMAGE}" != "${lensnode_ref}" \
		&& "${SOURCELENS_LENSNODE_IMAGE}" != "$(sourcelens_lensnode_image_ref latest)" ]]; then
		lensnode_refs+=("${SOURCELENS_LENSNODE_IMAGE}")
	fi
	save_image_archive "${archive}" "${lensnode_refs[@]}"
	log "  wrote $(du -h "${archive}" | awk '{print $1}') ${archive##*/}"

	log "Saving hyperfilelens-sourcelens-nginx:stable-alpine..."
	archive="${IMAGES_DIR}/12-nginx-stable-alpine.tar.gz"
	docker tag nginx:stable-alpine hyperfilelens-sourcelens-nginx:stable-alpine
	save_image_archive "${archive}" hyperfilelens-sourcelens-nginx:stable-alpine
	log "  wrote $(du -h "${archive}" | awk '{print $1}') ${archive##*/}"
}

stage_runtime_tree() {
	local src="${SOURCELENS_BUILD_SOURCE}"
	local sl_root="${PKG_ROOT}/sourcelens"
	local backend_ref frontend_ref lensnode_ref
	local upstream_backend_ref upstream_frontend_ref upstream_lensnode_ref
	local commit_full commit7 patchset_sha256 patch_manifest build_adapter_sha256
	local nginx_config

	commit_full="$(git -C "${SOURCELENS_SOURCE_CACHE}" rev-parse HEAD)"
	commit7="$(git -C "${SOURCELENS_SOURCE_CACHE}" rev-parse --short HEAD)"
	patchset_sha256="$(sourcelens_patchset_digest)"
	patch_manifest="$(sourcelens_patch_manifest_json)"
	build_adapter_sha256="$(sourcelens_build_adapter_digest)"
	backend_ref="$(sourcelens_backend_image_ref)"
	frontend_ref="$(sourcelens_frontend_image_ref)"
	lensnode_ref="$(sourcelens_lensnode_image_ref)"
	upstream_backend_ref="$(sourcelens_upstream_image_ref backend)"
	upstream_frontend_ref="$(sourcelens_upstream_image_ref frontend)"
	upstream_lensnode_ref="$(sourcelens_upstream_image_ref lensnode)"

	mkdir -p "${sl_root}/deploy/nginx/certs" "${sl_root}/deploy/nginx/hfl-maintenance" \
		"${sl_root}/deploy/postgresql/initdb.d" \
		"${sl_root}/deploy/sentry"

	log "Using upstream SourceLens nginx configuration"
	nginx_config="$(sourcelens_upstream_nginx_config "${src}")"
	cp "${nginx_config}" "${sl_root}/deploy/nginx/default.conf"
	sourcelens_patch_runtime_nginx "${sl_root}/deploy/nginx/default.conf"
	cp "${SOURCELENS_INSTALLER_DIR}/sourcelens/run-creation-gate-off.conf" \
		"${sl_root}/deploy/nginx/hfl-maintenance/run-creation-gate.conf"
	cp "${SOURCELENS_INSTALLER_DIR}/sourcelens/hfl-sentry-loader.js" \
		"${sl_root}/deploy/nginx/hfl-sentry-loader.js"
	printf '%s\n' 'window.__HFL_SOURCELENS_SENTRY__ = Object.freeze({ enabled: false })' \
		>"${sl_root}/deploy/nginx/hfl-sentry-config.js"
	if [[ -d "${src}/docker/postgresql" ]]; then
		rsync -a "${src}/docker/postgresql/" "${sl_root}/deploy/postgresql/"
	fi

	if [[ -f "${src}/env.sample" ]]; then
		cp "${src}/env.sample" "${sl_root}/.env.example"
		sourcelens_patch_env_runtime_defaults "${sl_root}/.env.example"
	fi

	cp "${SOURCELENS_INSTALLER_DIR}/sourcelens/install.sh" "${sl_root}/install.sh"
	cp "${SOURCELENS_INSTALLER_DIR}/sourcelens/compose-lifecycle.sh" \
		"${sl_root}/compose-lifecycle.sh"
	cp "${SOURCELENS_INSTALLER_DIR}/sourcelens/patch-env-runtime.py" "${sl_root}/patch-env-runtime.py"
	cp "${SOURCELENS_INSTALLER_DIR}/sourcelens/sync-sentry-runtime.py" "${sl_root}/sync-sentry-runtime.py"
	cp "${SOURCELENS_INSTALLER_DIR}/sourcelens/hfl-sentry-sitecustomize.py" \
		"${sl_root}/deploy/sentry/hfl-sentry-sitecustomize.py"
	chmod 644 "${sl_root}/deploy/sentry/hfl-sentry-sitecustomize.py"
	chmod +x "${sl_root}/install.sh" "${sl_root}/compose-lifecycle.sh" \
		"${sl_root}/patch-env-runtime.py" "${sl_root}/sync-sentry-runtime.py"

	sourcelens_write_runtime_compose "${sl_root}/docker-compose.yml"

	python3 - "${sl_root}/BUILD_INFO.json" \
		"${SOURCELENS_GIT_URL}" "${SOURCELENS_GIT_REF}" "${commit_full}" "${commit7}" \
		"${SOURCELENS_VERSION}" "${patchset_sha256}" "${patch_manifest}" \
		"${build_adapter_sha256}" "${SOURCELENS_BUILD_COMPOSE_FILE}" \
		"${SOURCELENS_INSTALL_DIR}" \
		"${SOURCELENS_LENSNODE_IMAGE}" "${SOURCELENS_EMBED_LENSNODE}" \
		"${backend_ref}" "${frontend_ref}" "${lensnode_ref}" \
		"${upstream_backend_ref}" "${upstream_frontend_ref}" "${upstream_lensnode_ref}" \
		"$(sourcelens_image_digest "${backend_ref}")" \
		"$(sourcelens_image_digest "${frontend_ref}")" \
		"$(sourcelens_image_digest "${lensnode_ref}")" \
		"$(sourcelens_image_digest nginx:stable-alpine)" <<'PY'
import json
import pathlib
import sys

out = pathlib.Path(sys.argv[1])
(
    git_url,
    git_ref,
    git_commit,
    git_commit_short,
    version,
    patchset_sha256,
    patch_manifest,
    build_adapter_sha256,
    build_compose_file,
    install_dir,
    lensnode_image,
    embed_local_lensnode,
    backend_ref,
    frontend_ref,
    lensnode_ref,
    upstream_backend_ref,
    upstream_frontend_ref,
    upstream_lensnode_ref,
    backend_digest,
    frontend_digest,
    lensnode_digest,
    nginx_digest,
) = sys.argv[2:24]
embed = str(embed_local_lensnode).strip().lower() in {"1", "true", "yes", "on"}
payload = {
    "enabled": True,
    "git_url": git_url,
    "git_ref": git_ref,
    "git_commit": git_commit,
    "git_commit_short": git_commit_short,
    "version": version,
    "patchset_sha256": patchset_sha256,
    "patches": json.loads(patch_manifest),
    "build_adapter_sha256": build_adapter_sha256,
    "build_compose_file": build_compose_file,
    "network": "hyperfilelens-bridge",
    "install_dir": install_dir,
    "lensnode_image": lensnode_image,
    "embed_local_lensnode": embed,
    "images": {
        "backend": {"ref": backend_ref, "upstream_ref": upstream_backend_ref, "digest": backend_digest},
        "frontend": {"ref": frontend_ref, "upstream_ref": upstream_frontend_ref, "digest": frontend_digest},
        "lensnode": {"ref": lensnode_ref, "upstream_ref": upstream_lensnode_ref, "digest": lensnode_digest},
        "nginx": {"ref": "hyperfilelens-sourcelens-nginx:stable-alpine", "digest": nginx_digest},
    },
}
out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
}

publish_gateway_lensnode_bundle() {
	HFL_GATEWAY_BOOTSTRAP_DIR="${PKG_ROOT}/payload/media/gateway-bootstrap" \
		sourcelens_publish_gateway_lensnode_bundle 1 "${IMAGES_DIR}/11-sourcelens-lensnode.tar.gz"
}

main() {
	sourcelens_load_config
	parse_args "$@"
	sourcelens_resolve_version
	hfl_logging_configure sourcelens-release "${LOG_FILE}" "${VERBOSE}"
	if [[ "${PRINT_CONFIG}" -eq 1 ]]; then
		print_config
		return 0
	fi
	hfl_logging_start
	if [[ "${BUILD_SOURCELENS}" != "1" ]]; then
		hfl_log_skip "BUILD_SOURCELENS=${BUILD_SOURCELENS}; skipping SourceLens bundle"
		return 0
	fi
	[[ -n "${PKG_ROOT}" && -n "${IMAGES_DIR}" ]] || die "--pkg-root and --images-dir are required" 2
	command -v realpath >/dev/null 2>&1 || die "realpath not found" 2
	command -v rsync >/dev/null 2>&1 || die "rsync not found" 2
	command -v gzip >/dev/null 2>&1 || die "gzip not found" 2
	validate_staging_paths

	command -v docker >/dev/null 2>&1 || die "docker not found" 2
	docker info >/dev/null 2>&1 || die "docker daemon not reachable"
	command -v python3 >/dev/null 2>&1 || die "python3 not found" 2

	sourcelens_sync_source
	if [[ "${PREBUILT}" -eq 1 ]]; then
		sourcelens_app_images_ready \
			|| die "--prebuilt requires normalized SourceLens backend, frontend, and LensNode images"
		sourcelens_tag_lensnode_alias
		log "Using prebuilt SourceLens application images"
	else
		sourcelens_build_app_images "${FORCE_BUILD}" "${NO_CACHE}"
	fi
	sourcelens_ensure_nginx_image "${FORCE_PULL}"
	save_image_archives
	stage_runtime_tree
	publish_gateway_lensnode_bundle
	log "SourceLens bundle staged under ${PKG_ROOT}/sourcelens"
}

hfl_logging_configure sourcelens-release
trap 'rc=$?; hfl_logging_finish "${rc}"' EXIT
trap 'exit 130' INT TERM
main "$@"
