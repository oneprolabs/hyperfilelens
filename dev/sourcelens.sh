#!/usr/bin/env bash
# SourceLens stack for local HyperFileLens development.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../tools/sourcelens/common.sh
source "${SCRIPT_DIR}/../tools/sourcelens/common.sh"
# shellcheck source=../deploy/installer/sourcelens/compose-lifecycle.sh
source "${SCRIPT_DIR}/../deploy/installer/sourcelens/compose-lifecycle.sh"

FORCE_BUILD="${SOURCELENS_FORCE_BUILD:-0}"
CMD=""
LOG_FILE="${HFL_LOG_FILE:-}"
VERBOSE="${HFL_LOG_VERBOSE:-0}"
PRINT_CONFIG=0

usage() {
	cat <<'USAGE'
Usage: ./dev/sourcelens.sh <command> [options]

SourceLens source is only an image build input. Runtime containers always use
images and never bind-mount the SourceLens source cache.

Commands:
  up        Sync source, build images if missing, start SourceLens compose stack
  down      Stop SourceLens compose stack
  prepare   Sync source + runtime tree + images (no compose up)
  gateway   Sync source + build images + publish the Gateway LensNode bundle only

Options:
  --force-build       Rebuild SourceLens app images even when present locally
  --pull              Refresh runtime images, with valid local fallback
  --offline           Forbid registry and SourceLens Git network access
  --pull-timeout SECONDS  Per-attempt Docker pull timeout
  --pull-retries COUNT    Docker pull attempts (default: 2)
  --sourcelens-git-url URL  Override SOURCELENS_GIT_URL
  --sourcelens-ref REF      SourceLens release tag in vX.Y.Z form
  --github-download-mirror URL  GitHub Git mirror with official fallback
  --github-token TOKEN  GitHub token for private repo clone/fetch (env: GITHUB_TOKEN)
  --apt-mirror URL     Ubuntu/Debian apt mirror (env: APT_MIRROR)
  --pip-index-url URL  Python package index (env: PIP_INDEX_URL)
  --pip-trusted-host HOST  Trusted pip host (env: PIP_TRUSTED_HOST)
  --npm-registry URL   npm registry (env: NPM_REGISTRY)
  --log-file FILE      Append runtime logs to FILE
  --verbose            Enable debug logs
  --print-config       Print effective non-secret configuration and exit
  --prepare-only      Alias for prepare command

Examples:
  ./dev/sourcelens.sh up
  ./dev/sourcelens.sh prepare --sourcelens-ref v0.30.0
  ./dev/sourcelens.sh up --apt-mirror https://mirrors.tuna.tsinghua.edu.cn --pip-index-url https://pypi.tuna.tsinghua.edu.cn/simple --pip-trusted-host pypi.tuna.tsinghua.edu.cn --npm-registry https://registry.npmmirror.com
USAGE
}

require_value() {
	hfl_require_value "$1" "${2:-}"
}

print_config() {
	cat <<EOF
command=${CMD:-<none>}
source_dir=${SOURCELENS_SOURCE_CACHE}
runtime_dir=${SOURCELENS_DEV_DIR}
data_dir=${SOURCELENS_DATA_DIR}
runtime_mode=image-only
git_url=${SOURCELENS_GIT_URL}
git_ref=${SOURCELENS_GIT_REF}
version=${SOURCELENS_VERSION}
upstream_image_prefix=${SOURCELENS_UPSTREAM_IMAGE_PREFIX}
image_registry=${SOURCELENS_IMAGE_REGISTRY:-<local>}
backend_image=$(sourcelens_backend_image_ref)
frontend_image=$(sourcelens_frontend_image_ref)
lensnode_image=$(sourcelens_lensnode_image_ref)
docker_platform=${SOURCELENS_DOCKER_PLATFORM}
force_build=${FORCE_BUILD}
offline=${SOURCELENS_OFFLINE}
force_pull=${SOURCELENS_FORCE_PULL}
docker_pull_timeout=${SOURCELENS_DOCKER_PULL_TIMEOUT}
docker_pull_retries=${SOURCELENS_DOCKER_PULL_RETRIES}
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
	CMD=""
	while [[ $# -gt 0 ]]; do
		case "$1" in
		-h | --help)
			usage
			exit 0
			;;
		up | down | prepare | gateway)
			[[ -z "${CMD}" ]] || sourcelens_die "multiple commands specified"
			CMD="$1"
			shift
			;;
		--force-build)
			FORCE_BUILD=1
			shift
			;;
		--pull)
			SOURCELENS_FORCE_PULL=1
			shift
			;;
		--offline)
			SOURCELENS_OFFLINE=1
			shift
			;;
		--pull-timeout)
			require_value "$1" "${2:-}"
			SOURCELENS_DOCKER_PULL_TIMEOUT="$2"
			shift 2
			;;
		--pull-retries)
			require_value "$1" "${2:-}"
			SOURCELENS_DOCKER_PULL_RETRIES="$2"
			shift 2
			;;
		--prepare-only)
			CMD="prepare"
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
		--github-token)
			require_value "$1" "${2:-}"
			GITHUB_TOKEN="$2"
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
		*)
			sourcelens_die "unknown argument: $1" 2
			;;
		esac
	done
	[[ -n "${CMD}" || "${PRINT_CONFIG}" -eq 1 ]] || {
		usage
		exit 2
	}
}

require_docker() {
	command -v docker >/dev/null 2>&1 || sourcelens_die "docker not found" 2
	docker info >/dev/null 2>&1 || sourcelens_die "docker daemon is not reachable"
}

cmd_prepare() {
	require_docker
	hfl_docker_validate_pull_settings "${SOURCELENS_DOCKER_PULL_TIMEOUT}" \
		"${SOURCELENS_DOCKER_PULL_RETRIES}" \
		|| sourcelens_die "invalid Docker pull timeout/retry settings"
	sourcelens_sync_source
	sourcelens_build_app_images "${FORCE_BUILD}"
	sourcelens_ensure_runtime_images "${SOURCELENS_FORCE_PULL}"
	sourcelens_prepare_dev_runtime_tree
	sourcelens_publish_gateway_lensnode_bundle "${FORCE_BUILD}"
	sourcelens_configure_hfl_env
}

cmd_gateway() {
	require_docker
	sourcelens_sync_source
	sourcelens_build_app_images "${FORCE_BUILD}"
	sourcelens_publish_gateway_lensnode_bundle "${FORCE_BUILD}"
}

cmd_up() {
	sourcelens_migrate_legacy_dev_project
	cmd_prepare
	sourcelens_ensure_shared_network
	sourcelens_log "Starting SourceLens stack (project=${SOURCELENS_COMPOSE_PROJECT})"
	hfl_compose_command_with_exit_event_recovery \
		sourcelens_dev_compose up -d --pull never --remove-orphans
	sourcelens_ensure_database_initialized
	if command -v curl >/dev/null 2>&1; then
		sourcelens_wait_for_health || true
	fi
}

cmd_down() {
	require_docker
	sourcelens_migrate_legacy_dev_project
	[[ -f "${SOURCELENS_DEV_DIR}/docker-compose.yml" ]] || {
		sourcelens_log "SourceLens dev compose not found; nothing to stop"
		return 0
	}
	sourcelens_log "Stopping SourceLens stack (project=${SOURCELENS_COMPOSE_PROJECT})"
	hfl_compose_command_with_exit_event_recovery sourcelens_dev_compose down
}

main() {
	sourcelens_load_config
	parse_args "$@"
	sourcelens_resolve_version
	hfl_logging_configure sourcelens-dev "${LOG_FILE}" "${VERBOSE}"
	if [[ "${PRINT_CONFIG}" -eq 1 ]]; then
		print_config
		return 0
	fi
	hfl_logging_start
	case "${CMD}" in
	up) cmd_up ;;
	down) cmd_down ;;
	prepare) cmd_prepare ;;
	gateway) cmd_gateway ;;
	esac
}

hfl_logging_configure sourcelens-dev
trap 'rc=$?; hfl_logging_finish "${rc}"' EXIT
trap 'exit 130' INT TERM
main "$@"
