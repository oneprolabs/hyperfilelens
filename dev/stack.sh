#!/usr/bin/env bash
# One-shot local development stack: prepare host artifacts + agent publish + Docker Compose.
#
# Usage:
#   ./dev/stack.sh up
#   ./dev/stack.sh down
#   ./dev/stack.sh restart
#   ./dev/stack.sh restart --force
#
# up      — full prepare + build dependency images + start bind-mounted HFL source
# down    — docker compose down
# restart — full prepare with cache + recreate services when configuration changed
# restart --force — clean build caches, rebuild dependency images, force-recreate containers
set -euo pipefail

configure_macos_dev_shell() {
	[[ "$(uname -s)" == "Darwin" ]] || return 0
	if ((BASH_VERSINFO[0] < 5)); then
		command -v brew >/dev/null 2>&1 || {
			printf 'ERROR: macOS development requires Homebrew Bash 5 or newer; install Homebrew and run: brew install bash\n' >&2
			exit 2
		}
		local brew_bash
		brew_bash="$(brew --prefix bash 2>/dev/null)/bin/bash"
		[[ -x "${brew_bash}" ]] || {
			printf 'ERROR: macOS development requires Homebrew Bash 5 or newer; run: brew install bash\n' >&2
			exit 2
		}
		exec "${brew_bash}" "$0" "$@"
	fi
	# Make `#!/usr/bin/env bash` child scripts resolve to Homebrew Bash as well.
	# Without this, macOS falls back to its Bash 3.2 after this script re-execs.
	local brew_bash_dir
	brew_bash_dir="$(brew --prefix bash 2>/dev/null)/bin"
	[[ -x "${brew_bash_dir}/bash" ]] && PATH="${brew_bash_dir}:${PATH}"
	local prefix candidate
	for prefix in coreutils findutils gnu-sed gnu-tar grep; do
		candidate="$(brew --prefix "${prefix}" 2>/dev/null || true)/libexec/gnubin"
		[[ -d "${candidate}" ]] && PATH="${candidate}:${PATH}"
	done
	export PATH
	export DOCKER_DEFAULT_PLATFORM="${DOCKER_DEFAULT_PLATFORM:-linux/amd64}"
}
configure_macos_dev_shell "$@"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../tools/lib/logging.sh
source "${ROOT}/tools/lib/logging.sh"
# shellcheck source=../tools/lib/env-file.sh
source "${ROOT}/tools/lib/env-file.sh"
# shellcheck source=../tools/lib/docker-images.sh
source "${ROOT}/tools/lib/docker-images.sh"
# shellcheck source=../tools/kopia/common.sh
source "${ROOT}/tools/kopia/common.sh"

COMPOSE=()

MIRROR_GITHUB_DOWNLOAD=""
MIRROR_GITHUB_TOKEN=""
MIRROR_DOCKER_DOWNLOAD=""
MIRROR_APT=""
OPT_GO_PROXY=""
OPT_GO_SUMDB=""
OPT_PIP_INDEX_URL=""
OPT_PIP_TRUSTED_HOST=""
OPT_NPM_REGISTRY=""
WITH_SOURCELENS=""
SOURCELENS_GIT_REF="${SOURCELENS_GIT_REF:-}"
SOURCELENS_GIT_URL="${SOURCELENS_GIT_URL:-}"
HFL_ONLY_DOWN=0
LOG_FILE="${HFL_LOG_FILE:-}"
VERBOSE="${HFL_LOG_VERBOSE:-0}"
PRINT_CONFIG=0
FORCE_PULL=0
DEV_OFFLINE="${DEV_OFFLINE:-}"
DOCKER_PULL_TIMEOUT="${DOCKER_PULL_TIMEOUT_SECONDS:-}"
DOCKER_PULL_RETRIES="${DOCKER_PULL_RETRIES:-}"
CLEAN_SCOPE=""
CLEAN_YES=0
STATE_FILE="${ROOT}/build/state/dev-stack.json"
WEBSITE_OUTPUT="${ROOT}/build/website"
WEBSITE_BUILDER_IMAGE="hyperfilelens-website-builder:dev"
WEBSITE_BASE_IMAGE="node:22-alpine"
WEBSITE_ARTIFACT_REBUILT=0
LANGUAGE_PACK_BUILDER_IMAGE="hyperfilelens-language-pack-builder:dev"
LANGUAGE_PACK_BUILD_OUTPUT="${ROOT}/build/language-packs"
# Open Core extensions: CLI --extension-source only (not a .env setting).
EXTENSION_SOURCES=()
EXTENSION_SOURCES_CSV=""
DEV_PUBLIC_URL=""
DEV_ADMIN_PUBLIC_URL=""
UPGRADE_GATEWAY=0

usage() {
	cat <<'USAGE'
Usage: ./dev/stack.sh <command> [options]

Commands:
  up                 Prepare dependencies and start the development stack
  up|restart --upgrade-gateway
                     Force reinstall the local Data Gateway host Agent from the
                     newest published release (env: HFL_UPGRADE_GATEWAY=1)
  down               Stop HyperFileLens + bundled SourceLens
  down --hfl-only    Stop HyperFileLens only; leave SourceLens running
  restart            Refresh dependencies/configuration and recreate changed services
  restart --force    Clean caches and rebuild development dependency images without cache
  status             Show HFL and SourceLens service state and published ports
  doctor             Check host tools, configuration, images, permissions, and ports
  smoke              Run pinned Playwright login/HMR smoke tests against the running stack
  clean --runtime    Remove containers, Compose networks, and the frontend modules volume
  clean --cache      Remove generated build cache and local development images
  clean --data --yes Remove runtime databases, logs, and generated media
  clean --all --yes  Remove runtime, cache, and data

Prepare (up / restart) always includes:
  .env (create from .env.example if missing)
  Repository-pinned default TLS certificates
  Unified Kopia binary matrix for Backend and Agent packages
  backend source bind mount with automatic API/worker/scheduler restart
  frontend source bind mount with Vite HMR and persistent node_modules
  Website static artifact served by the shared Nginx gateway (no Website container)
  agent publish (full bundle) → data/media/agent-releases/
  local public Data Gateway auto-deploy when HFL_PLATFORM_GATEWAY_AUTO_DEPLOY=true (default; Linux amd64)
  SourceLens bundled mode (default): clone/update only as a build input, then run images
  SourceLens external mode: prepare the Gateway LensNode bundle without touching the external stack

SourceLens options (default: enabled for up/restart):
  --no-sourcelens                  Skip SourceLens clone/build/start
  --sourcelens-ref REF             SourceLens release tag in vX.Y.Z form
  --sourcelens-git-url URL         Override SourceLens repository URL (env: SOURCELENS_GIT_URL)

Extensions (optional overlay; community default = empty socket):
  --extension-source SRC           Local path or git/HTTPS URL[+@ref]. Repeatable.
                                     Not read from .env (prepare/packaging input only).
                                     Private HTTPS uses --github-token or
                                     HFL_EXTENSION_GIT_TOKEN (SSH uses your agent).
  stack.sh materializes sources → build/docker-compose.extensions.yml.
  Runtime containers see HFL_EXTENSIONS paths only (never git clone in api/web).

Deployment identity (optional for source deployments reached from another host):
  --public-url URL                 Canonical tenant origin used by remote Agents and links.
  --admin-public-url URL           Canonical Admin Console origin.

Mirror options (Kopia fetch + Agent publishing + SourceLens git clone; env fallback):
  --github-download-mirror URL     GitHub Git/release mirror (env: GITHUB_DOWNLOAD_MIRROR)
  --github-token TOKEN             GitHub token for API/release fetch, private SourceLens
                                     clone, and private extension git sources (env:
                                     GITHUB_TOKEN / HFL_EXTENSION_GIT_TOKEN)
  --docker-download-mirror URL     Docker Hub mirror for builds and runtime images (env: DOCKER_DOWNLOAD_MIRROR)
  --apt-mirror URL                 Ubuntu apt mirror for NAS container (env: APT_MIRROR)
  --ubuntu2404-arch ARCH           NAS deb arch for agent bundle: amd64 | arm64 | all (default: amd64)
  --kopia-mode MODE                build or download
  --kopia-git-url URL              Kopia source repository URL
  --kopia-ref REF                  Kopia release ref in vX.Y.Z form
  --go-proxy URL                   Go module proxy (env: GOPROXY)
  --go-sumdb VALUE                 Go checksum database (env: GOSUMDB)
  --pip-index-url URL              Python package index (env: PIP_INDEX_URL)
  --pip-trusted-host HOST          Trusted pip host (env: PIP_TRUSTED_HOST)
  --npm-registry URL               npm registry (env: NPM_REGISTRY)
  --pull                           Refresh runtime images with valid local fallback
  --offline                        Forbid registry, Git, and dependency network access
  --pull-timeout SECONDS           Per-attempt Docker pull timeout (default: 180)
  --pull-retries COUNT             Docker pull attempts (default: 2)

Output options:
  --log-file FILE                  Write complete stdout/stderr to FILE with timestamps
                                     (default: build/logs/dev-<command>-<time>-<pid>.log)
  --verbose                        Enable debug logs
  --print-config                   Print effective non-secret configuration and exit
  -h, --help                       Show this help

Examples:
  ./dev/stack.sh up
  ./dev/stack.sh up --extension-source ../hyperfilelens-ee
  ./dev/stack.sh up --upgrade-gateway
  ./dev/stack.sh up --public-url https://192.168.8.69:11443 \
    --admin-public-url https://192.168.8.69:11444
  ./dev/stack.sh up --ubuntu2404-arch amd64
  ./dev/stack.sh down
  ./dev/stack.sh restart
  ./dev/stack.sh restart --force
  ./dev/stack.sh up \
    --github-download-mirror https://ghfast.top \
    --docker-download-mirror docker.m.daocloud.io \
    --apt-mirror https://mirrors.tuna.tsinghua.edu.cn \
    --go-proxy https://goproxy.cn,direct \
    --go-sumdb sum.golang.google.cn \
    --pip-index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    --npm-registry https://registry.npmmirror.com
USAGE
}

log() { hfl_log_info "$@"; }
warn() { hfl_log_warn "$@"; }
die() { hfl_die "$1" "${2:-1}"; }

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
		APT_MIRROR GOPROXY GOSUMDB PIP_INDEX_URL PIP_TRUSTED_HOST \
		NPM_REGISTRY BUILD_SOURCELENS SOURCELENS_GIT_URL \
		DOCKER_PULL_TIMEOUT_SECONDS DOCKER_PULL_RETRIES DEV_OFFLINE \
		DEV_SMOKE_PLAYWRIGHT_VERSION SOURCELENS_GIT_TIMEOUT_SECONDS \
		SOURCELENS_GIT_RETRIES SOURCELENS_GIT_FALLBACK_TIMEOUT_SECONDS \
		KOPIA_ARTIFACT_MODE KOPIA_GIT_URL KOPIA_GIT_REF; do
		hfl_env_load_default "${key}"
	done
	DOCKER_PULL_TIMEOUT="${DOCKER_PULL_TIMEOUT:-${DOCKER_PULL_TIMEOUT_SECONDS:-180}}"
	DOCKER_PULL_RETRIES="${DOCKER_PULL_RETRIES:-2}"
	DEV_OFFLINE="${DEV_OFFLINE:-0}"
}

apply_mirror_env_defaults() {
	MIRROR_GITHUB_DOWNLOAD="${MIRROR_GITHUB_DOWNLOAD:-${GITHUB_DOWNLOAD_MIRROR:-}}"
	MIRROR_GITHUB_TOKEN="${MIRROR_GITHUB_TOKEN:-${GITHUB_TOKEN:-}}"
	MIRROR_DOCKER_DOWNLOAD="${MIRROR_DOCKER_DOWNLOAD:-${DOCKER_DOWNLOAD_MIRROR:-}}"
	MIRROR_APT="${MIRROR_APT:-${APT_MIRROR:-}}"
	OPT_GO_PROXY="${OPT_GO_PROXY:-${GOPROXY:-}}"
	OPT_GO_SUMDB="${OPT_GO_SUMDB:-${GOSUMDB:-}}"
	OPT_PIP_INDEX_URL="${OPT_PIP_INDEX_URL:-${PIP_INDEX_URL:-}}"
	OPT_PIP_TRUSTED_HOST="${OPT_PIP_TRUSTED_HOST:-${PIP_TRUSTED_HOST:-}}"
	OPT_NPM_REGISTRY="${OPT_NPM_REGISTRY:-${NPM_REGISTRY:-}}"
	export GITHUB_DOWNLOAD_MIRROR="${MIRROR_GITHUB_DOWNLOAD}"
	export GITHUB_TOKEN="${MIRROR_GITHUB_TOKEN}"
	export DOCKER_DOWNLOAD_MIRROR="${MIRROR_DOCKER_DOWNLOAD}"
	export APT_MIRROR="${MIRROR_APT}"
	export PIP_INDEX_URL="${OPT_PIP_INDEX_URL}"
	export PIP_TRUSTED_HOST="${OPT_PIP_TRUSTED_HOST}"
	export NPM_REGISTRY="${OPT_NPM_REGISTRY}"
	hfl_docker_export_build_base_images "${MIRROR_DOCKER_DOWNLOAD}"
	[[ -z "${OPT_GO_PROXY}" ]] || export GOPROXY="${OPT_GO_PROXY}"
	[[ -z "${OPT_GO_SUMDB}" ]] || export GOSUMDB="${OPT_GO_SUMDB}"
}

print_config() {
	local sourcelens_version
	apply_mirror_env_defaults
	apply_ubuntu2404_arch_default
	[[ "${SOURCELENS_GIT_REF}" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]] \
		|| die "invalid SourceLens release ref: ${SOURCELENS_GIT_REF} (expected vX.Y.Z)" 2
	sourcelens_version="${BASH_REMATCH[1]}"
	cat <<EOF
command=${CMD:-<none>}
host_platform=$(uname -s)/$(uname -m)
docker_platform=${DOCKER_DEFAULT_PLATFORM:-linux/amd64}
compose_file=${ROOT}/docker-compose.yml
data_dir=${ROOT}/data
source_check=${ROOT}/tools/quality/check-english-source.py
backend_source_mount=${ROOT}/src/backend:/opt/backend
frontend_source_mount=${ROOT}/src/frontend:/app
frontend_modules_volume=frontend-node-modules
website_source=${ROOT}/website
website_artifact=${WEBSITE_OUTPUT}
website_builder_image=${WEBSITE_BUILDER_IMAGE}
website_runtime=static-nginx
with_sourcelens=${WITH_SOURCELENS}
sourcelens_runtime=image-only
sourcelens_ref=${SOURCELENS_GIT_REF}
sourcelens_version=${sourcelens_version}
sourcelens_git_url=${SOURCELENS_GIT_URL:-<default>}
sourcelens_git_timeout=${SOURCELENS_GIT_TIMEOUT_SECONDS}
sourcelens_git_retries=${SOURCELENS_GIT_RETRIES}
sourcelens_git_fallback_timeout=${SOURCELENS_GIT_FALLBACK_TIMEOUT_SECONDS}
sourcelens_upstream_image_prefix=${SOURCELENS_UPSTREAM_IMAGE_PREFIX}
sourcelens_image_registry=${SOURCELENS_IMAGE_REGISTRY:-<local>}
ubuntu2404_arch=${UBUNTU2404_ARCH}
kopia_mode=${KOPIA_ARTIFACT_MODE}
kopia_git_url=${KOPIA_GIT_URL}
kopia_ref=${KOPIA_GIT_REF}
kopia_version=${KOPIA_VERSION}
github_download_mirror=${MIRROR_GITHUB_DOWNLOAD:-<official>}
github_token=$(hfl_redact "${MIRROR_GITHUB_TOKEN}")
docker_download_mirror=${MIRROR_DOCKER_DOWNLOAD:-<official>}
apt_mirror=${MIRROR_APT:-<official>}
go_proxy=${GOPROXY:-<official>}
go_sumdb=${GOSUMDB:-<official>}
pip_index_url=${OPT_PIP_INDEX_URL:-<official>}
pip_trusted_host=${OPT_PIP_TRUSTED_HOST:-<unset>}
npm_registry=${OPT_NPM_REGISTRY:-<official>}
extension_sources=${EXTENSION_SOURCES_CSV:-<none>}
public_url=${DEV_PUBLIC_URL:-<preserve>}
admin_public_url=${DEV_ADMIN_PUBLIC_URL:-<preserve>}
docker_pull_timeout=${DOCKER_PULL_TIMEOUT}
docker_pull_retries=${DOCKER_PULL_RETRIES}
offline=${DEV_OFFLINE}
force_pull=${FORCE_PULL}
upgrade_gateway=${UPGRADE_GATEWAY}
state_file=${STATE_FILE#${ROOT}/}
log_file=${LOG_FILE:-<none>}
verbose=${VERBOSE}
EOF
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

version_ge() {
	python3 - "$1" "$2" <<'PY'
import re
import sys

def parts(value):
    return tuple(int(item) for item in re.findall(r"\d+", value))

raise SystemExit(0 if parts(sys.argv[1]) >= parts(sys.argv[2]) else 1)
PY
}

require_dev_build_tools() {
	command -v python3 >/dev/null 2>&1 || die "python3 not found in PATH" 2
	command -v go >/dev/null 2>&1 || die "go not found in PATH (required for Kopia and Agent builds)" 2
}

require_docker() {
	command -v python3 >/dev/null 2>&1 || die "python3 not found in PATH" 2
	command -v docker >/dev/null 2>&1 || die "docker not found in PATH" 2
	docker info >/dev/null 2>&1 || die "docker daemon is not reachable"
	docker compose version >/dev/null 2>&1 \
		|| die "Docker Compose v2.20 or newer is required" 2
	local engine compose
	engine="$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
	compose="$(docker compose version --short 2>/dev/null || true)"
	[[ -n "${engine}" ]] && version_ge "${engine}" 24.0.0 \
		|| die "Docker Engine 24.0 or newer is required (current: ${engine:-unknown})" 2
	[[ -n "${compose}" ]] && version_ge "${compose#v}" 2.20.0 \
		|| die "Docker Compose v2.20 or newer is required (current: ${compose:-unknown})" 2
	COMPOSE=(docker compose)
}

compose() {
	local files=(-f docker-compose.yml)
	local compose_overlay ext_token
	# CLI --extension-source only; never read prepare sources from .env.
	compose_overlay="${ROOT}/build/docker-compose.extensions.yml"
	if [[ -n "${EXTENSION_SOURCES_CSV:-}" ]]; then
		ext_token="${HFL_EXTENSION_GIT_TOKEN:-${MIRROR_GITHUB_TOKEN:-${GITHUB_TOKEN:-}}}"
		HFL_EXTENSION_GIT_TOKEN="${ext_token}" \
			python3 "${ROOT}/tools/extensions/materialize_extensions.py" \
			--repo-root "${ROOT}" \
			--sources "${EXTENSION_SOURCES_CSV}" \
			--compose-out "${compose_overlay}" \
			|| die "failed to materialize --extension-source"
		[[ -f "${compose_overlay}" ]] \
			|| die "extension compose overlay missing after materialize: ${compose_overlay}"
		files+=(-f "${compose_overlay}")
	elif [[ -f "${compose_overlay}" ]]; then
		rm -f "${compose_overlay}"
	fi
	(
		cd "${ROOT}"
		"${COMPOSE[@]}" --env-file "${ROOT}/.env" "${files[@]}" "$@"
	)
}

ensure_bridge_network() {
	local network="hyperfilelens-bridge"
	if docker network inspect "${network}" >/dev/null 2>&1; then
		return 0
	fi
	log "Creating shared bridge network ${network}"
	docker network create "${network}" >/dev/null
}

ensure_env_file() {
	local env_file="${ROOT}/.env"
	local example="${ROOT}/.env.example"
	local sync_script="${ROOT}/tools/config/sync_env.py"
	[[ -f "${example}" ]] || die ".env.example not found"
	if [[ -f "${env_file}" ]]; then
		chmod 600 "${env_file}"
		[[ -f "${sync_script}" ]] || die "environment sync script not found"
		python3 "${sync_script}" --env-file "${env_file}" --example "${example}"
		chmod 600 "${env_file}"
		log ".env exists; missing keys synchronized"
	else
		cp "${example}" "${env_file}"
		chmod 600 "${env_file}"
		log "Created .env from .env.example"
	fi
}

apply_dev_public_urls() {
	[[ -n "${DEV_PUBLIC_URL}" || -n "${DEV_ADMIN_PUBLIC_URL}" ]] || return 0
	local -a args=(--env-file "${ROOT}/.env")
	[[ -z "${DEV_PUBLIC_URL}" ]] || args+=(--public-url "${DEV_PUBLIC_URL}")
	[[ -z "${DEV_ADMIN_PUBLIC_URL}" ]] || args+=(--admin-public-url "${DEV_ADMIN_PUBLIC_URL}")
	python3 "${ROOT}/deploy/installer/apply-runtime-config.py" "${args[@]}"
	log "Source deployment public origins synchronized"
}

ensure_tls_certs() {
	local cert="${ROOT}/deploy/nginx/certs/tls.crt"
	local key="${ROOT}/deploy/nginx/certs/tls.key"
	[[ -s "${cert}" && -s "${key}" ]] \
		|| die "repository-pinned TLS certificate and key are required under deploy/nginx/certs"
	command -v openssl >/dev/null 2>&1 || die "openssl required to validate dev TLS certificates"
	local cert_pub key_pub
	cert_pub="$(openssl x509 -in "${cert}" -pubkey -noout 2>/dev/null | sha256sum | cut -d' ' -f1)"
	key_pub="$(openssl pkey -in "${key}" -pubout 2>/dev/null | sha256sum | cut -d' ' -f1)"
	[[ -n "${cert_pub}" && "${cert_pub}" == "${key_pub}" ]] \
		|| die "repository-pinned TLS certificate and key do not match"
	chmod 644 "${cert}"
	chmod 600 "${key}"
	log "Repository-pinned TLS certificates validated"
}

read_project_version() {
	python3 - "${ROOT}/pyproject.toml" <<'PY'
import pathlib
import re
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
project = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", text)
version = re.search(r'(?m)^\s*version\s*=\s*["\']([^"\']+)["\']', project.group(1)) if project else None
if version is None:
    raise SystemExit("pyproject.toml has no static project version")
print(version.group(1))
PY
}

sync_dev_product_version() {
	local version
	version="$(read_project_version)"
	python3 - "${ROOT}/.env" "${version}" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
version = sys.argv[2]
text = path.read_text(encoding="utf-8")
pattern = r"^(HFL_PRODUCT_VERSION=).*$"
if re.search(pattern, text, flags=re.M):
    text = re.sub(pattern, lambda match: f"{match.group(1)}{version}", text, count=1, flags=re.M)
else:
    text = text.rstrip() + f"\nHFL_PRODUCT_VERSION={version}\n"
path.write_text(text, encoding="utf-8")
PY
}

ensure_data_dirs() {
	local language_root
	language_root="${ROOT}/data/lang-packs/versions/$(read_project_version)"
	mkdir -p "${ROOT}/data/postgresql" "${ROOT}/data/redis"
	install -d -m 0755 \
		"${ROOT}/data/logs" \
		"${language_root}" \
		"${ROOT}/data/media/agent-releases" \
		"${ROOT}/data/media/enroll-bootstrap" \
		"${ROOT}/data/media/gateway-bootstrap" \
		"${ROOT}/data/media/snapshot-downloads" \
		"${ROOT}/data/staticfiles"
	chmod 0755 \
		"${ROOT}/data/lang-packs" \
		"${ROOT}/data/lang-packs/versions" \
		"${language_root}" \
		"${ROOT}/data/media" \
		"${ROOT}/data/media/agent-releases" \
		"${ROOT}/data/media/enroll-bootstrap" \
		"${ROOT}/data/media/gateway-bootstrap" \
		"${ROOT}/data/media/snapshot-downloads" \
		"${ROOT}/data/staticfiles"
	local manifest="${language_root}/installed.json"
	local legacy_empty_manifest=0
	if [[ -f "${manifest}" ]] \
		&& [[ "$(tr -d '[:space:]' <"${manifest}")" == '{"packs":[]}' ]]; then
		legacy_empty_manifest=1
	fi
	if [[ ! -f "${manifest}" || "${legacy_empty_manifest}" -eq 1 ]]; then
		local temporary="${manifest}.tmp.$$"
		printf '{"schema":1,"app_version":"%s","packs":[]}\n' \
			"$(read_project_version)" >"${temporary}"
		chmod 0644 "${temporary}"
		mv -f "${temporary}" "${manifest}"
		log "Initialized empty runtime language-pack manifest schema"
	fi
}

validate_network_policy() {
	[[ "${DEV_OFFLINE}" =~ ^[01]$ ]] || die "DEV_OFFLINE must be 0 or 1" 2
	hfl_docker_validate_pull_settings "${DOCKER_PULL_TIMEOUT}" "${DOCKER_PULL_RETRIES}" \
		|| die "Docker pull timeout/retries must be positive integers and timeout must be installed" 2
}

ensure_runtime_images() {
	local image
	validate_network_policy
	for image in nginx:stable-alpine postgres:17 redis:alpine; do
		log "Resolving runtime image ${image} (offline=${DEV_OFFLINE}, force_pull=${FORCE_PULL})"
		if ! hfl_docker_ensure_image "${image}" "${MIRROR_DOCKER_DOWNLOAD}" \
			"${FORCE_PULL}" "${DEV_OFFLINE}" "linux/amd64" \
			"${DOCKER_PULL_TIMEOUT}" "${DOCKER_PULL_RETRIES}"; then
			die "unable to prepare ${image}: ${HFL_DOCKER_LAST_ERROR}"
		fi
		log "Runtime image ${image} ready (source=${HFL_DOCKER_IMAGE_SOURCE})"
	done
}

verify_amd64_runtime() {
	log "Verifying linux/amd64 container execution"
	docker run --rm --pull=never --platform linux/amd64 nginx:stable-alpine /bin/true \
		>/dev/null 2>&1 \
		|| die "linux/amd64 containers cannot run; enable amd64 emulation in Docker Desktop or Colima" 2
}

cache_fingerprint() {
	local args=(fingerprint --root "${ROOT}") item
	while [[ $# -gt 0 && "$1" != "--" ]]; do
		args+=(--path "$1")
		shift
	done
	[[ $# -eq 0 ]] || shift
	for item in "$@"; do
		args+=(--value "${item}")
	done
	python3 "${ROOT}/tools/dev/cache_state.py" "${args[@]}"
}

cache_matches() {
	python3 "${ROOT}/tools/dev/cache_state.py" check \
		--state "${STATE_FILE}" --key "$1" --fingerprint "$2"
}

cache_update() {
	python3 "${ROOT}/tools/dev/cache_state.py" update \
		--state "${STATE_FILE}" --key "$1" --fingerprint "$2"
}

prepare_website_static() {
	local force=$1 fingerprint app_url
	WEBSITE_ARTIFACT_REBUILT=0
	fingerprint="$(cache_fingerprint website -- "npm=${OPT_NPM_REGISTRY}" \
		"platform=linux/amd64" "base=${HFL_WEBSITE_BASE_IMAGE}")"
	if [[ "${force}" -eq 1 ]] || [[ ! -f "${WEBSITE_OUTPUT}/public/en/index.html" ]] \
		|| ! cache_matches website-static "${fingerprint}"; then
		[[ "${DEV_OFFLINE}" -eq 0 ]] \
			|| die "Website static artifact is missing or stale in offline mode"
		log "Resolving Website build image ${WEBSITE_BASE_IMAGE}"
		if ! hfl_docker_ensure_image "${WEBSITE_BASE_IMAGE}" "${MIRROR_DOCKER_DOWNLOAD}" \
			"${FORCE_PULL}" "${DEV_OFFLINE}" "linux/amd64" \
			"${DOCKER_PULL_TIMEOUT}" "${DOCKER_PULL_RETRIES}"; then
			die "unable to prepare ${WEBSITE_BASE_IMAGE}: ${HFL_DOCKER_LAST_ERROR}"
		fi
		log "Building standalone Website static artifact"
		local -a args=(
			--output "${WEBSITE_OUTPUT}"
			--image-tag "${WEBSITE_BUILDER_IMAGE}"
			--platform linux/amd64
			--base-image "${HFL_WEBSITE_BASE_IMAGE}"
		)
		[[ -z "${OPT_NPM_REGISTRY}" ]] || args+=(--npm-registry "${OPT_NPM_REGISTRY}")
		[[ "${force}" -eq 0 ]] || args+=(--no-cache)
		[[ "${FORCE_PULL}" -eq 0 ]] || args+=(--pull)
		"${ROOT}/website/build.sh" "${args[@]}"
		WEBSITE_ARTIFACT_REBUILT=1
		cache_update website-static "${fingerprint}"
	else
		log "Website static fingerprint unchanged; reusing build/website"
	fi

	app_url="$(read_env_value FRONTEND_URL)"
	HFL_WEBSITE_CONFIG_OUTPUT="${WEBSITE_OUTPUT}/public/website-runtime-config.js" \
		HFL_WEBSITE_APP_URL="${app_url}" \
		sh "${WEBSITE_OUTPUT}/runtime-config.sh"
}

refresh_website_web_mount() {
	[[ "${WEBSITE_ARTIFACT_REBUILT}" -eq 1 ]] || return 0
	log "Website artifact directory replaced; recreating Web to refresh its bind mount"
	compose up -d --no-deps --no-build --pull never --force-recreate web
}

build_dev_images() {
	local force=$1 backend_fingerprint frontend_fingerprint
	backend_fingerprint="$(cache_fingerprint \
		.dockerignore deploy/docker/backend.Dockerfile deploy/docker/backend-entrypoint.sh \
		deploy/docker/dev-process-supervisor.py deploy/bootstrap \
		pyproject.toml uv.lock build/kopia/KOPIA_INFO.json build/kopia/dist/linux/amd64/kopia -- \
		"apt=${MIRROR_APT}" "pip=${OPT_PIP_INDEX_URL}" \
		"pip_host=${OPT_PIP_TRUSTED_HOST}" "kopia=${KOPIA_GIT_REF}" \
		"kopia_mode=${KOPIA_ARTIFACT_MODE}" "base=${HFL_BACKEND_BASE_IMAGE}")"
	frontend_fingerprint="$(cache_fingerprint \
		.dockerignore deploy/docker/frontend.Dockerfile deploy/docker/frontend-dev-entrypoint.sh \
		deploy/nginx/development-web.conf \
		src/frontend/package.json \
		src/frontend/package-lock.json -- "npm=${OPT_NPM_REGISTRY}" \
		"node_base=${HFL_FRONTEND_NODE_BASE_IMAGE}" \
		"nginx_base=${HFL_FRONTEND_NGINX_BASE_IMAGE}")"

	if [[ "${force}" -eq 1 ]] || ! docker image inspect hyperfilelens-backend:dev >/dev/null 2>&1 \
		|| ! cache_matches backend-image "${backend_fingerprint}"; then
		[[ "${DEV_OFFLINE}" -eq 0 ]] \
			|| die "backend development image is missing or stale in offline mode"
		log "Building backend development dependency image"
		if [[ "${force}" -eq 1 ]]; then
			compose build --no-cache worker
		else
			compose build worker
		fi
		cache_update backend-image "${backend_fingerprint}"
	else
		log "Backend development image fingerprint unchanged; reusing local image"
	fi

	if [[ "${force}" -eq 1 ]] || ! docker image inspect hyperfilelens-frontend:dev >/dev/null 2>&1 \
		|| ! cache_matches frontend-image "${frontend_fingerprint}"; then
		[[ "${DEV_OFFLINE}" -eq 0 ]] \
			|| die "frontend development image is missing or stale in offline mode"
		log "Building frontend development dependency image"
		if [[ "${force}" -eq 1 ]]; then
			compose build --no-cache web
		else
			compose build web
		fi
		cache_update frontend-image "${frontend_fingerprint}"
	else
		log "Frontend development image fingerprint unchanged; reusing local image"
	fi
}

prepare_dev_language_packs() {
	local force=$1 fingerprint version
	fingerprint="$(cache_fingerprint \
		language-packs src/backend src/frontend/src/locales \
		src/frontend/package.json src/frontend/package-lock.json -- \
		"npm=${OPT_NPM_REGISTRY}" \
		"node_base=${HFL_FRONTEND_NODE_BASE_IMAGE}")"
	if [[ "${force}" -eq 1 ]] \
		|| ! docker image inspect "${LANGUAGE_PACK_BUILDER_IMAGE}" >/dev/null 2>&1 \
		|| ! cache_matches language-pack-builder "${fingerprint}"; then
		[[ "${DEV_OFFLINE}" -eq 0 ]] \
			|| die "language-pack builder is missing or stale in offline mode"
		local -a build_args=(
			--network host
			--file "${ROOT}/language-packs/tooling/Dockerfile"
			--tag "${LANGUAGE_PACK_BUILDER_IMAGE}"
			--build-arg "NODE_BASE_IMAGE=${HFL_FRONTEND_NODE_BASE_IMAGE}"
			--build-arg "NPM_REGISTRY=${OPT_NPM_REGISTRY}"
		)
		[[ "${force}" -eq 0 ]] || build_args+=(--no-cache)
		build_args+=("${ROOT}")
		log "Building language-pack toolchain"
		docker build "${build_args[@]}"
		cache_update language-pack-builder "${fingerprint}"
	fi

	version="$(read_project_version)"
	(
		source "${ROOT}/deploy/installer/install.sh"
		ROOT="${ROOT}"
		HFL_BUNDLED_LANGUAGE_PACKS_DIR="${LANGUAGE_PACK_BUILD_OUTPUT}/dist"
		acquire_installation_lock
		mkdir -p "${LANGUAGE_PACK_BUILD_OUTPUT}"
		log "Building development language packs for application ${version}"
		docker run --rm --platform linux/amd64 \
			--user "$(id -u):$(id -g)" \
			--mount "type=bind,src=${ROOT}/language-packs,dst=/workspace/language-packs,readonly" \
			--mount "type=bind,src=${ROOT}/src/backend,dst=/workspace/src/backend,readonly" \
			--mount "type=bind,src=${ROOT}/src/frontend/src/locales,dst=/workspace/src/frontend/src/locales,readonly" \
			--mount "type=bind,src=${LANGUAGE_PACK_BUILD_OUTPUT},dst=/workspace/build/language-packs" \
			"${LANGUAGE_PACK_BUILDER_IMAGE}" --version "${version}"
		sync_bundled_language_packs
	)
}

prepare_kopia_artifacts() {
	local force=$1
	local args=(
		--kopia-mode "${KOPIA_ARTIFACT_MODE}"
		--kopia-git-url "${KOPIA_GIT_URL}"
		--kopia-ref "${KOPIA_GIT_REF}"
	)
	[[ "${force}" -eq 1 ]] && args+=(--force)
	[[ "${DEV_OFFLINE}" -eq 0 ]] || args+=(--offline)
	if [[ -n "${MIRROR_GITHUB_DOWNLOAD}" ]]; then
		args+=(--github-download-mirror "${MIRROR_GITHUB_DOWNLOAD}")
	fi
	[[ -z "${MIRROR_GITHUB_TOKEN}" ]] || args+=(--github-token "${MIRROR_GITHUB_TOKEN}")
	log "Preparing unified Kopia artifacts (mode=${KOPIA_ARTIFACT_MODE}, force=${force})"
	"${ROOT}/tools/kopia/prepare.sh" "${args[@]}"
}

# Remove artifacts left by older multilingual development builds.
strip_bundled_lang_packs() {
	local frontend=$1
	local removed=0
	local path
	for path in \
		"${frontend}/public/locales/installed.json" \
		"${frontend}/dist/locales/installed.json"; do
		if [[ -f "${path}" ]]; then
			rm -f "${path}"
			removed=1
		fi
	done
	shopt -s nullglob
	local -a message_bundles=(
		"${frontend}/public/locales/"*.messages.js
		"${frontend}/dist/locales/"*.messages.js
	)
	shopt -u nullglob
	if ((${#message_bundles[@]})); then
		rm -f "${message_bundles[@]}"
		removed=1
	fi
	if [[ "${removed}" -eq 1 ]]; then
		log "Removed generated language pack files from frontend public/dist"
	fi
}

prepare_sourcelens_dev() {
	local force=$1
	[[ "${WITH_SOURCELENS}" -eq 1 ]] || return 0
	local mode
	mode="$(read_env_value_or SOURCELENS_MODE bundled "${ROOT}/.env" | tr 'A-Z' 'a-z')"
	case "${mode}" in
	bundled | external) ;;
	*) die "invalid SOURCELENS_MODE=${mode} (use bundled or external)" ;;
	esac
	local args
	if [[ "${mode}" == "bundled" ]]; then
		args=(up)
	else
		args=(gateway)
	fi
	[[ "${force}" -eq 1 ]] && args+=(--force-build)
	[[ "${FORCE_PULL}" -eq 1 ]] && args+=(--pull)
	[[ "${DEV_OFFLINE}" -eq 1 ]] && args+=(--offline)
	args+=(--pull-timeout "${DOCKER_PULL_TIMEOUT}" --pull-retries "${DOCKER_PULL_RETRIES}")
	[[ -n "${SOURCELENS_GIT_REF}" ]] && args+=(--sourcelens-ref "${SOURCELENS_GIT_REF}")
	[[ -n "${SOURCELENS_GIT_URL}" ]] && args+=(--sourcelens-git-url "${SOURCELENS_GIT_URL}")
	[[ -n "${MIRROR_GITHUB_DOWNLOAD}" ]] && args+=(--github-download-mirror "${MIRROR_GITHUB_DOWNLOAD}")
	export DEV_OFFLINE="${DEV_OFFLINE}"
	export DOCKER_PULL_TIMEOUT_SECONDS="${DOCKER_PULL_TIMEOUT}"
	export DOCKER_PULL_RETRIES
	export SOURCELENS_GIT_TIMEOUT_SECONDS SOURCELENS_GIT_RETRIES SOURCELENS_GIT_FALLBACK_TIMEOUT_SECONDS
	export SOURCELENS_CONSOLE_BIND_ADDRESS SOURCELENS_CONSOLE_PORT SOURCELENS_NGINX_HTTPS_PORT
	SOURCELENS_CONSOLE_BIND_ADDRESS="$(read_env_value_or SOURCELENS_CONSOLE_BIND_ADDRESS 0.0.0.0 "${ROOT}/.env")"
	SOURCELENS_CONSOLE_PORT="$(read_env_value_or SOURCELENS_CONSOLE_PORT 11445 "${ROOT}/.env")"
	SOURCELENS_NGINX_HTTPS_PORT="${SOURCELENS_CONSOLE_PORT}"
	log "Preparing SourceLens ${mode} integration (force_build=${force})"
	HFL_PARENT_SESSION=1 "${ROOT}/dev/sourcelens.sh" "${args[@]}"
}

stop_sourcelens_dev() {
	[[ "${WITH_SOURCELENS}" -eq 1 ]] || return 0
	[[ "${HFL_ONLY_DOWN}" -eq 1 ]] && return 0
	[[ "$(read_env_value_or SOURCELENS_MODE bundled "${ROOT}/.env" | tr 'A-Z' 'a-z')" == "bundled" ]] || return 0
	if [[ -x "${ROOT}/dev/sourcelens.sh" ]]; then
		log "Stopping SourceLens dev stack"
		"${ROOT}/dev/sourcelens.sh" down || true
	fi
}

publish_agent() {
	local force=$1
	local fingerprint
	fingerprint="$(cache_fingerprint src/agent tools/agent tools/kopia deploy/bootstrap \
		tools/lib/version.sh tools/lib/logging.sh -- \
		"arch=${UBUNTU2404_ARCH}" "kopia=${KOPIA_GIT_REF}" "kopia_mode=${KOPIA_ARTIFACT_MODE}" \
		"commit=$(git -C "${ROOT}" rev-parse HEAD)" \
		"github_mirror=${MIRROR_GITHUB_DOWNLOAD}" \
		"docker_mirror=${MIRROR_DOCKER_DOWNLOAD}" "apt=${MIRROR_APT}" \
		"goproxy=${OPT_GO_PROXY}" "gosumdb=${OPT_GO_SUMDB}")"
	if [[ "${force}" -eq 0 ]] \
		&& cache_matches agent-publish "${fingerprint}" \
		&& find "${ROOT}/data/media/agent-releases" -type f \
			\( -name '*.tar.gz' -o -name '*.zip' \) -print -quit 2>/dev/null | grep -q .; then
		log "Agent publish fingerprint unchanged; reusing published packages"
		return 0
	fi
	if [[ "${DEV_OFFLINE}" -eq 1 ]]; then
		die "Agent packages are missing or stale and offline mode forbids rebuilding network dependencies"
	fi
	if [[ "${force}" -eq 1 ]]; then
		log "Cleaning Agent build output"
		"${ROOT}/src/agent/scripts/build.sh" --clean
	fi
	local args=(--bundle all --ubuntu2404-arch "${UBUNTU2404_ARCH}")
	args+=(--kopia-mode "${KOPIA_ARTIFACT_MODE}")
	args+=(--kopia-git-url "${KOPIA_GIT_URL}")
	args+=(--kopia-ref "${KOPIA_GIT_REF}")
	[[ "${force}" -eq 1 ]] && args+=(--force-fetch)
	local mirror
	while IFS= read -r -d '' mirror; do
		args+=("${mirror}")
	done < <(mirror_args || true)
	[[ -n "${OPT_GO_PROXY}" ]] && args+=(--go-proxy "${OPT_GO_PROXY}")
	[[ -n "${OPT_GO_SUMDB}" ]] && args+=(--go-sumdb "${OPT_GO_SUMDB}")
	log "Publishing Agent packages (bundle=all, ubuntu2404-arch=${UBUNTU2404_ARCH}, force_fetch=${force})"
	HFL_PARENT_SESSION=1 "${ROOT}/tools/agent/publish.sh" "${args[@]}"
	cache_update agent-publish "${fingerprint}"
}

prepare_dev() {
	local force=$1
	apply_mirror_env_defaults
	apply_ubuntu2404_arch_default
	log "Checking the English source boundary"
	python3 "${ROOT}/tools/quality/check-english-source.py"
	require_docker
	ensure_env_file
	sync_dev_product_version
	ensure_tls_certs
	ensure_data_dirs
	prepare_kopia_artifacts "${force}"
	strip_bundled_lang_packs "${ROOT}/src/frontend"
	publish_agent "${force}"
	prepare_website_static "${force}"
	build_dev_images "${force}"
	prepare_dev_language_packs "${force}"
}

read_env_value() {
	local key=$1
	local env_file="${2:-${ROOT}/.env}"
	[[ -f "${env_file}" ]] || return 0
	grep -E "^${key}=" "${env_file}" 2>/dev/null | head -1 | cut -d= -f2- | tr -d ' "'
}

read_env_value_or() {
	local key=$1
	local default=$2
	local env_file="${3:-${ROOT}/.env}"
	local val
	val="$(read_env_value "${key}" "${env_file}")"
	if [[ -n "${val}" ]]; then
		printf '%s' "${val}"
	else
		printf '%s' "${default}"
	fi
}

print_dev_target() {
	local edition="Community" branch commit source display
	[[ ${#EXTENSION_SOURCES[@]} -eq 0 ]] || edition="Enterprise"
	branch="$(git -C "${ROOT}" branch --show-current 2>/dev/null || true)"
	commit="$(git -C "${ROOT}" rev-parse --short=12 HEAD 2>/dev/null || true)"
	hfl_print_section "Target"
	hfl_print_value "Edition" "${edition}"
	hfl_print_value "OSS source" "${ROOT}"
	hfl_print_value "OSS revision" "${branch:-detached} (${commit:-unknown})"
	if [[ ${#EXTENSION_SOURCES[@]} -eq 0 ]]; then
		hfl_print_value "Extensions" "none"
	else
		for source in "${EXTENSION_SOURCES[@]}"; do
			if [[ "${source}" == http://* || "${source}" == https://* ]]; then
				display="remote Git source configured"
			else
				display="${source}"
			fi
			hfl_print_value "Extension" "${display}"
		done
	fi
	hfl_print_value "SourceLens" "$([[ "${WITH_SOURCELENS}" -eq 1 ]] && printf '%s' "${SOURCELENS_GIT_REF}" || printf 'disabled')"
	hfl_print_value "Platform" "$(uname -s | tr '[:upper:]' '[:lower:]')/$(uname -m)"
	hfl_print_value "Log file" "${LOG_FILE#${ROOT}/}"
}

print_urls() {
	local env_file="${ROOT}/.env"
	local sl_env="${ROOT}/data/sourcelens/config/.env"
	local seed seed_email seed_pass seed_org sourcelens_mode sourcelens_console_port
	local website_bind website_port tenant_bind tenant_port admin_bind admin_port sourcelens_console_bind
	local pg_user pg_pass pg_db frontend_url lens_base lens_gw lens_email lens_pass insight_access
	local sl_user sl_email sl_pass

	seed="$(read_env_value_or SEED_INITIAL_DATA 1 "${env_file}")"
	seed_email="$(read_env_value_or SEED_ADMIN_EMAIL admin@hyperfilelens.com "${env_file}")"
	seed_pass="$(read_env_value_or SEED_ADMIN_PASSWORD 'Admin@123' "${env_file}")"
	seed_org="$(read_env_value_or SEED_ORG_NAME HyperFileLens "${env_file}")"
	sourcelens_mode="$(read_env_value_or SOURCELENS_MODE bundled "${env_file}" | tr 'A-Z' 'a-z')"
	website_bind="$(read_env_value_or HFL_WEBSITE_BIND_ADDRESS 0.0.0.0 "${env_file}")"
	website_port="$(read_env_value_or HFL_WEBSITE_PORT 11442 "${env_file}")"
	tenant_bind="$(read_env_value_or HFL_TENANT_BIND_ADDRESS 0.0.0.0 "${env_file}")"
	tenant_port="$(read_env_value_or HFL_TENANT_PORT 11443 "${env_file}")"
	admin_bind="$(read_env_value_or HFL_ADMIN_BIND_ADDRESS 0.0.0.0 "${env_file}")"
	admin_port="$(read_env_value_or HFL_ADMIN_PORT 11444 "${env_file}")"
	sourcelens_console_bind="$(read_env_value_or SOURCELENS_CONSOLE_BIND_ADDRESS 0.0.0.0 "${env_file}")"
	sourcelens_console_port="$(read_env_value_or SOURCELENS_CONSOLE_PORT 11445 "${env_file}")"
	pg_user="$(read_env_value_or POSTGRES_USER postgres "${env_file}")"
	pg_pass="$(read_env_value_or POSTGRES_PASSWORD postgres "${env_file}")"
	pg_db="$(read_env_value_or POSTGRES_DB hyperfilelens "${env_file}")"
	frontend_url="$(read_env_value_or FRONTEND_URL "https://127.0.0.1:${tenant_port}" "${env_file}")"
	lens_base="$(read_env_value_or LENS_BASE_URL http://sourcelens-nginx "${env_file}")"
	if [[ "${sourcelens_mode}" == "external" ]]; then
		lens_gw="$(read_env_value_or LENS_GATEWAY_BASE_URL "${lens_base}" "${env_file}")"
	else
		lens_gw="$(read_env_value_or LENS_GATEWAY_BASE_URL "${frontend_url%/}/sourcelens" "${env_file}")"
	fi
	lens_email="$(read_env_value_or LENS_BRIDGE_EMAIL admin@example.com "${env_file}")"
	lens_pass="$(read_env_value_or LENS_BRIDGE_PASSWORD adminpassword "${env_file}")"
	if [[ "${WITH_SOURCELENS}" -eq 1 && "${sourcelens_mode}" == "bundled" ]]; then
		insight_access="https://localhost:${sourcelens_console_port}/  (${sourcelens_console_bind})"
	elif [[ "${WITH_SOURCELENS}" -eq 1 && "${sourcelens_mode}" == "external" ]]; then
		insight_access="${lens_base} (external)"
	else
		insight_access="not started"
	fi

	cat <<EOF

================================================================
Development stack is ready
================================================================

Access
  Website          https://localhost:${website_port}/en/  (${website_bind})
  Tenant           https://localhost:${tenant_port}/  (${tenant_bind})
  Platform Ops     https://localhost:${admin_port}/  (${admin_bind})
  Django Admin     https://localhost:${admin_port}/admin/
  Insight Console  ${insight_access}
  API / Swagger    https://localhost:${tenant_port}/swagger
EOF

	if [[ "${seed}" == "1" ]]; then
		cat <<EOF

Development credentials
  HyperFileLens
    Email          ${seed_email}
    Password       ${seed_pass}
    Applies to     Tenant, Platform Ops and Django Admin
    Organization   ${seed_org}
    Seeding        enabled (worker creates admin on first startup)
EOF
	else
		cat <<EOF

Development credentials
  HyperFileLens    no seeded account (SEED_INITIAL_DATA=${seed})
EOF
	fi

	cat <<EOF

Development services
  PostgreSQL       postgres:5432/${pg_db} (private)
  Database user    ${pg_user}
  Database pass    ${pg_pass}
  Agent releases   https://localhost:${tenant_port}/media/agent-releases/
  AI engine bundle https://localhost:${tenant_port}/media/gateway-bootstrap/lensnode-image-linux-amd64.tar.gz
  Config           ${env_file#${ROOT}/}
  Log file         ${LOG_FILE#${ROOT}/}
EOF

	if [[ "${WITH_SOURCELENS}" -eq 1 && "${sourcelens_mode}" == "bundled" ]]; then
		if [[ -f "${sl_env}" ]]; then
			sl_user="$(read_env_value_or DJANGO_SUPERUSER_USERNAME admin "${sl_env}")"
			sl_email="$(read_env_value_or DJANGO_SUPERUSER_EMAIL admin@example.com "${sl_env}")"
			sl_pass="$(read_env_value_or DJANGO_SUPERUSER_PASSWORD adminpassword "${sl_env}")"
		else
			sl_user=admin
			sl_email=admin@example.com
			sl_pass=adminpassword
		fi

		cat <<EOF

  Insight Console
    Username       ${sl_user}
    Email          ${sl_email}
    Password       ${sl_pass}

Service endpoints
  Insight API      https://localhost:${tenant_port}/sourcelens/api/
  Insight WSS      wss://localhost:${tenant_port}/sourcelens/ws/lens/lensnodes/
  Insight network  hyperfilelens-bridge (private)
  HFL bridge       ${lens_base}
  Gateway URL      ${lens_gw}
  Bridge account   ${lens_email} / ${lens_pass}  (LENS_BRIDGE_* in .env)
EOF
		if [[ -f "${sl_env}" ]]; then
			echo "  SL config        data/sourcelens/config/.env"
		fi
	elif [[ "${WITH_SOURCELENS}" -eq 1 && "${sourcelens_mode}" == "external" ]]; then
		cat <<EOF

Service endpoints
  Insight mode     external (not managed by stack.sh)
  Insight base URL ${lens_base}
  Gateway URL      ${lens_gw}
  Bridge account   ${lens_email} / ${lens_pass}  (LENS_BRIDGE_* in .env)
EOF
	fi

	cat <<EOF

Useful commands
  Status           ./dev/stack.sh status
  Doctor           ./dev/stack.sh doctor
  Smoke test       ./dev/stack.sh smoke
  Restart          ./dev/stack.sh restart
  Force rebuild    ./dev/stack.sh restart --force
  Stop HFL         ./dev/stack.sh down --hfl-only
  Stop all         ./dev/stack.sh down

Notes
  - Backend and frontend source changes reload automatically
  - Website source changes are rebuilt by ./dev/stack.sh restart
  - Accept the self-signed TLS warning for localhost
  - Change default passwords after first login
EOF
}

wait_for_api_healthy() {
	local api_container="" health="" attempt
	for ((attempt = 1; attempt <= 90; attempt++)); do
		api_container="$(compose ps -q api 2>/dev/null || true)"
		health=""
		if [[ -n "${api_container}" ]]; then
			health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${api_container}" 2>/dev/null || true)"
		fi
		[[ "${health}" == "healthy" ]] && return 0
		sleep 1
	done
	return 1
}

platform_gateway_auto_deploy_enabled() {
	local raw
	raw="$(read_env_value_or HFL_PLATFORM_GATEWAY_AUTO_DEPLOY true "${ROOT}/.env" | tr '[:upper:]' '[:lower:]')"
	case "${raw}" in
	1 | true | yes | on) return 0 ;;
	0 | false | no | off) return 1 ;;
	*)
		warn "invalid HFL_PLATFORM_GATEWAY_AUTO_DEPLOY=${raw}; treating as enabled"
		return 0
		;;
	esac
}

# Ensure the installer-managed local public Data Gateway (host Agent + LensNode).
# Mirrors deploy/installer/install.sh ensure_local_platform_gateway for the dev stack.
# Failures warn instead of aborting stack up so Darwin/non-root hosts stay usable.
ensure_local_platform_gateway_dev() {
	if ! platform_gateway_auto_deploy_enabled; then
		if [[ "${UPGRADE_GATEWAY}" -eq 1 ]]; then
			warn "--upgrade-gateway ignored: local platform Gateway auto-deploy is disabled (HFL_PLATFORM_GATEWAY_AUTO_DEPLOY=false)"
		else
			log "Local platform Gateway auto-deploy is disabled (HFL_PLATFORM_GATEWAY_AUTO_DEPLOY=false)"
		fi
		return 0
	fi
	if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
		warn "Skipping local platform Gateway auto-deploy (requires Linux amd64/x86_64)"
		return 0
	fi
	if ! wait_for_api_healthy; then
		warn "Skipping local platform Gateway auto-deploy because the API is not healthy yet"
		return 0
	fi

	local helper="${ROOT}/data/media/enroll-bootstrap/hfl-enroll-linux-amd64"
	if [[ ! -x "${helper}" ]]; then
		warn "Skipping local platform Gateway auto-deploy; enrollment helper missing: ${helper}"
		return 0
	fi

	log "Ensuring local public Data Gateway (platform Gateway auto-deploy)"
	# Sidecar install expects :latest; versioned tags alone leave gateway-install
	# downloading the bootstrap archive (or failing when that archive is stale).
	# Re-tag whenever the newest versioned image differs from :latest so the
	# sidecar (which compares image IDs) is recreated after a SourceLens rebuild.
	local latest_id versioned_ref versioned_id
	latest_id="$(docker image inspect --format '{{.Id}}' hyperfilelens-sourcelens-lensnode:latest 2>/dev/null || true)"
	versioned_ref="$(
		docker images --format '{{.Repository}}:{{.Tag}}' \
			| grep -E '^hyperfilelens-sourcelens-lensnode:[0-9]' \
			| while read -r ref; do
				printf '%s %s\n' "$(docker image inspect --format '{{.Created}}' "${ref}" 2>/dev/null || true)" "${ref}"
			done \
			| sort -r \
			| head -1 \
			| awk '{print $NF}'
	)"
	if [[ -n "${versioned_ref}" ]]; then
		versioned_id="$(docker image inspect --format '{{.Id}}' "${versioned_ref}" 2>/dev/null || true)"
		if [[ -z "${latest_id}" || "${latest_id}" != "${versioned_id}" ]]; then
			docker tag "${versioned_ref}" hyperfilelens-sourcelens-lensnode:latest
			log "Refreshed hyperfilelens-sourcelens-lensnode:latest from ${versioned_ref} for Gateway sidecar"
		fi
	fi

	local command_output parsed org_key token api_base wss_url managed_node_ids
	local agent_env="/var/lib/hyperfilelens-agent/agent.env"
	set +e
	command_output="$(compose exec -T api python manage.py ensure_local_platform_gateway_enrollment 2>&1)"
	local command_status=$?
	set -e
	if [[ "${command_status}" -ne 0 ]]; then
		warn "Local platform Gateway enrollment failed; the dev stack remains available"
		[[ -n "${command_output}" ]] && printf '%s\n' "${command_output}"
		return 0
	fi
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
	)" || {
		warn "Local platform Gateway enrollment payload could not be parsed; the dev stack remains available"
		return 0
	}
	IFS=$'\t' read -r org_key token api_base wss_url managed_node_ids <<<"${parsed}"
	if [[ "${org_key}" != "__platform_lens__" ]]; then
		warn "Local platform Gateway enrollment returned unexpected org ${org_key}; skipping install"
		return 0
	fi

	local tenant_port existing_org existing_role existing_node_id existing_token
	tenant_port="$(read_env_value_or HFL_TENANT_PORT 11443 "${ROOT}/.env")"
	api_base="https://127.0.0.1:${tenant_port}"
	wss_url="wss://127.0.0.1:${tenant_port}/ws/node/agent/"

	existing_org=""
	existing_role=""
	existing_node_id=""
	existing_token=""
	if [[ -f "${agent_env}" ]]; then
		existing_org="$(grep -E '^HFL_ORG_KEY=' "${agent_env}" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' || true)"
		existing_role="$(grep -E '^HFL_NODE_ROLE=' "${agent_env}" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' || true)"
		existing_node_id="$(grep -E '^HFL_NODE_ID=' "${agent_env}" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' || true)"
		existing_token="$(grep -E '^HFL_NODE_TOKEN=' "${agent_env}" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' || true)"
	fi
	if [[ -n "${existing_org}${existing_role}${existing_node_id}${existing_token}" ]]; then
		if [[ "${existing_org}" != "${org_key}" || "${existing_role}" != "gateway" ]]; then
			warn "Skipping local platform Gateway auto-deploy: existing host Agent conflicts (org=${existing_org:-unset} role=${existing_role:-unset})"
			return 0
		fi
		if [[ -n "${existing_node_id}" ]]; then
			case ",${managed_node_ids}," in
			*",${existing_node_id},"*) ;;
			*)
				warn "Skipping local platform Gateway auto-deploy: host Agent node ${existing_node_id} is not installer-managed"
				return 0
				;;
			esac
		elif [[ -n "${existing_token}" && "${existing_token}" != "${token}" ]]; then
			warn "Skipping local platform Gateway auto-deploy: partially enrolled Agent token is not installer-managed"
			return 0
		fi
	fi

	local gateway_args=(--yes --no-banner)
	# --reinstall mirrors helper InstallState.Installed, which stat()s the agent
	# binary (not agent.env), so gate on the same path (-e) to avoid a misleading error.
	local agent_bin="/opt/hyperfilelens-agent/hfl-agent"
	if [[ "${UPGRADE_GATEWAY}" -eq 1 && -e "${agent_bin}" ]]; then
		gateway_args+=(--reinstall)
		log "Forcing local Data Gateway host Agent upgrade to the newest published release"
	fi

	set +e
	env \
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
		HFL_NO_BANNER=1 \
		"${helper}" gateway-install "${gateway_args[@]}"
	command_status=$?
	set -e
	if [[ "${command_status}" -ne 0 ]]; then
		warn "Local platform Gateway host install failed (exit ${command_status}); the dev stack remains available"
		return 0
	fi
	if ! wait_for_local_platform_gateway_ready_dev 180; then
		warn "Local public Data Gateway installed but is not Copilot-ready yet; the dev stack remains available"
		return 0
	fi
	log "Local public Data Gateway auto-deploy finished (online and usable)"
}

wait_for_local_platform_gateway_ready_dev() {
	local timeout_seconds=${1:-180} deadline node_id
	deadline=$((SECONDS + timeout_seconds))
	node_id="$(grep -E '^HFL_NODE_ID=' /var/lib/hyperfilelens-agent/agent.env 2>/dev/null \
		| head -1 | cut -d= -f2- | tr -d '\r' || true)"
	if [[ ! "${node_id}" =~ ^[0-9]+$ ]]; then
		return 1
	fi
	local query
	query="from apps.lens_bridge.models import LensGatewayLink; from apps.lens_bridge.services.gateway_readiness import gateway_runtime_state; from apps.lens_bridge.services.provisioning import sync_gateway_lensnode_status; link = LensGatewayLink.objects.select_related('gateway').filter(gateway_id=${node_id}, scope='platform').first(); link = sync_gateway_lensnode_status(link) if link is not None else None; state = gateway_runtime_state(link); raise SystemExit(0 if link is not None and state['hfl_usable'] and state['copilot_eligible'] else 1)"
	while true; do
		if compose exec -T api python manage.py shell -c "${query}" >/dev/null 2>&1; then
			return 0
		fi
		if ((SECONDS >= deadline)); then
			return 1
		fi
		sleep 2
	done
}

sync_optional_identity_settings() {
	local output command_status
	if ! wait_for_api_healthy; then
		warn "Skipping optional identity settings sync because the API is not healthy yet"
		return 0
	fi

	log "Synchronizing optional identity and email settings"
	set +e
	output="$(compose exec -T api python manage.py ensure_deployment_identity_settings 2>&1)"
	command_status=$?
	set -e
	if [[ -n "${output}" ]]; then
		printf '%s\n' "${output}"
	fi
	if [[ "${command_status}" -ne 0 ]]; then
		warn "Optional identity or email settings could not be synchronized; the dev stack remains available"
		return 0
	elif grep -F 'HFL_IDENTITY_STATUS=warning' <<<"${output}" >/dev/null; then
		warn "Invalid optional identity or email settings were preserved"
	else
		log "Optional identity and email settings synchronized"
	fi

	set +e
	output="$(compose exec -T api python manage.py check_google_oauth_readiness 2>&1)"
	command_status=$?
	set -e
	if [[ -n "${output}" ]]; then
		printf '%s\n' "${output}"
	fi
	if [[ "${command_status}" -ne 0 ]]; then
		warn "Google OAuth local route or generated callback is not ready; the dev stack remains available"
	fi
}

run_dev_migration_gate() {
	log "Stopping backend services before database migration"
	compose stop api worker scheduler
	log "Starting development data services"
	compose up -d --wait --no-build --pull never postgres redis
	log "Applying backend database migrations"
	compose --profile tools run --rm --no-deps migration
}

cmd_up() {
	hfl_print_section "[1/8] Checking development environment"
	apply_mirror_env_defaults
	require_dev_build_tools
	ensure_env_file
	apply_dev_public_urls
	require_docker
	ensure_runtime_images
	verify_amd64_runtime
	ensure_bridge_network
	hfl_log_ok "Development tools, runtime images, and shared network are ready"
	hfl_print_section "[2/8] Preparing insight services"
	prepare_sourcelens_dev 0
	if [[ "${WITH_SOURCELENS}" -eq 1 ]]; then
		hfl_log_ok "Insight services are prepared"
	else
		hfl_log_skip "Insight services are disabled for this run"
	fi
	hfl_print_section "[3/8] Preparing HyperFileLens artifacts and images"
	prepare_dev 0
	hfl_log_ok "HyperFileLens development artifacts and images are ready"
	hfl_print_section "[4/8] Applying database migrations"
	run_dev_migration_gate
	hfl_log_ok "Database migrations and singleton initialization completed"
	hfl_print_section "[5/8] Starting development services"
	log "Starting hot-reload HFL stack from explicitly prepared images"
	compose up -d --no-build --pull never --remove-orphans
	refresh_website_web_mount
	hfl_log_ok "Hot-reload HyperFileLens services started"
	hfl_print_section "[6/8] Applying identity and email configuration"
	sync_optional_identity_settings
	hfl_print_section "[7/8] Preparing Platform Data Gateway"
	ensure_local_platform_gateway_dev
	hfl_print_section "[8/8] Development environment summary"
	print_urls
}

cmd_down() {
	require_docker
	[[ -f "${ROOT}/.env" ]] || warn ".env missing; using compose defaults where applicable"
	log "Stopping stack: docker compose down"
	compose down
	stop_sourcelens_dev
	log "Stopped"
}

cmd_restart() {
	local force=$1

	hfl_print_section "[1/8] Checking development environment"
	apply_mirror_env_defaults
	require_dev_build_tools
	ensure_env_file
	apply_dev_public_urls
	require_docker
	ensure_runtime_images
	verify_amd64_runtime
	ensure_bridge_network
	hfl_log_ok "Development tools, runtime images, and shared network are ready"
	hfl_print_section "[2/8] Preparing insight services"
	prepare_sourcelens_dev "${force}"
	if [[ "${WITH_SOURCELENS}" -eq 1 ]]; then
		hfl_log_ok "Insight services are prepared"
	else
		hfl_log_skip "Insight services are disabled for this run"
	fi
	hfl_print_section "[3/8] Preparing HyperFileLens artifacts and images"
	prepare_dev "${force}"
	hfl_log_ok "HyperFileLens development artifacts and images are ready"
	hfl_print_section "[4/8] Applying database migrations"
	run_dev_migration_gate
	hfl_log_ok "Database migrations and singleton initialization completed"
	hfl_print_section "[5/8] Restarting development services"

	if [[ "${force}" -eq 1 ]]; then
		log "Force restart: recreating services from freshly rebuilt images"
		compose up -d --no-build --pull never --force-recreate --remove-orphans
	else
		log "Restarting only services whose image or configuration changed"
		compose up -d --no-build --pull never --remove-orphans
		refresh_website_web_mount
	fi
	hfl_log_ok "HyperFileLens development services restarted"
	hfl_print_section "[6/8] Applying identity and email configuration"
	sync_optional_identity_settings
	hfl_print_section "[7/8] Preparing Platform Data Gateway"
	ensure_local_platform_gateway_dev
	hfl_print_section "[8/8] Development environment summary"
	print_urls
}

cmd_status() {
	ensure_env_file
	require_docker
	log "HyperFileLens services"
	compose ps
	if [[ -f "${ROOT}/build/sourcelens/dev/docker-compose.yml" ]]; then
		log "SourceLens services"
		# SourceLens helper resolves the generated Compose project without changing it.
		(
			cd "${ROOT}/build/sourcelens/dev"
			docker compose --env-file "${ROOT}/data/sourcelens/config/.env" \
				-p "${SOURCELENS_COMPOSE_PROJECT:-sourcelens}" -f docker-compose.yml ps
		)
	else
		log "SourceLens runtime has not been prepared"
	fi
	if [[ -f "${WEBSITE_OUTPUT}/public/en/index.html" ]]; then
		printf 'ok      Website static artifact %s\n' "${WEBSITE_OUTPUT}"
	else
		printf 'pending Website static artifact (created by up)\n'
	fi
}

cmd_doctor() {
	local failures=0 command image mode invalid_language_path
	ensure_env_file
	apply_mirror_env_defaults
	for command in docker go python3 openssl timeout flock gzip sha256sum realpath; do
		if command -v "${command}" >/dev/null 2>&1; then
			printf 'ok      command %s\n' "${command}"
		else
			printf 'missing command %s\n' "${command}"
			failures=$((failures + 1))
		fi
	done
	validate_network_policy || failures=$((failures + 1))
	if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
		printf 'ok      Docker daemon reachable\n'
		for image in nginx:stable-alpine postgres:17 redis:alpine \
			hyperfilelens-backend:dev hyperfilelens-frontend:dev \
			"${LANGUAGE_PACK_BUILDER_IMAGE}"; do
			if docker image inspect "${image}" >/dev/null 2>&1; then
				printf 'ok      image %s\n' "${image}"
			else
				printf 'pending image %s (prepared by up)\n' "${image}"
			fi
		done
	else
		printf 'missing Docker daemon is not reachable\n'
		failures=$((failures + 1))
	fi
	if [[ -d "${ROOT}/data/lang-packs" ]]; then
		local language_root="${ROOT}/data/lang-packs/versions/$(read_project_version)"
		mode="$(stat -c '%a' "${ROOT}/data/lang-packs")"
		invalid_language_path="$(
			find "${ROOT}/data/lang-packs" "${ROOT}/data/lang-packs/versions" \
				"${language_root}" -maxdepth 0 -type d ! -perm 0755 \
				-print -quit 2>/dev/null || true
		)"
		if [[ -z "${invalid_language_path}" ]]; then
			invalid_language_path="$(
				find "${language_root}" -mindepth 1 \
				\( \( -type d ! -perm 0755 \) -o \( -type f ! -perm 0644 \) \) \
					-print -quit 2>/dev/null || true
			)"
		fi
		if [[ "${mode}" == "755" \
			&& -r "${language_root}/installed.json" \
			&& -z "${invalid_language_path}" ]]; then
			printf 'ok      language packs mode=%s manifest=readable\n' "${mode}"
		else
			printf 'invalid language packs mode=%s, manifest, or catalog permissions (run up)\n' \
				"${mode}"
			[[ -z "${invalid_language_path}" ]] \
				|| printf 'invalid language pack path %s\n' "${invalid_language_path}"
			failures=$((failures + 1))
		fi
	else
		printf 'pending data directories (created by up)\n'
	fi
	if [[ "${failures}" -ne 0 ]]; then
		die "doctor found ${failures} blocking issue(s)"
	fi
	printf 'doctor: no blocking issues found\n'
}

clean_runtime() {
	require_docker
	ensure_env_file
	compose down --volumes --remove-orphans || true
	if [[ -x "${ROOT}/dev/sourcelens.sh" ]]; then
		"${ROOT}/dev/sourcelens.sh" down || true
	fi
	docker network rm hyperfilelens-bridge >/dev/null 2>&1 || true
	log "Removed development containers, Compose networks, and modules volume"
}

clean_cache() {
	require_docker
	local image
	for image in hyperfilelens-backend:dev hyperfilelens-frontend:dev \
		"${WEBSITE_BUILDER_IMAGE}" "${LANGUAGE_PACK_BUILDER_IMAGE}"; do
		docker image rm "${image}" >/dev/null 2>&1 || true
	done
	rm -rf "${ROOT}/build/agent" "${ROOT}/build/state" \
		"${ROOT}/build/sourcelens" "${WEBSITE_OUTPUT}" "${LANGUAGE_PACK_BUILD_OUTPUT}"
	log "Removed generated build caches and local HFL development images"
}

clean_data() {
	[[ "${CLEAN_YES}" -eq 1 ]] \
		|| die "clean --data and clean --all require --yes because databases and runtime data are deleted" 2
	rm -rf "${ROOT}/data"
	log "Removed runtime data"
}

cmd_clean() {
	if [[ "${CLEAN_SCOPE}" == "data" || "${CLEAN_SCOPE}" == "all" ]]; then
		[[ "${CLEAN_YES}" -eq 1 ]] \
			|| die "clean --data and clean --all require --yes because databases and runtime data are deleted" 2
	fi
	case "${CLEAN_SCOPE}" in
	runtime) clean_runtime ;;
	cache) clean_cache ;;
	data) clean_runtime; clean_data ;;
	all) clean_runtime; clean_cache; clean_data ;;
	*) die "clean requires exactly one of --runtime, --cache, --data, or --all" 2 ;;
	esac
}

cmd_smoke() {
	ensure_env_file
	require_docker
	"${ROOT}/tools/dev/browser-smoke.sh"
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
	--kopia-mode)
		require_value "$1" "${2:-}"
		KOPIA_ARTIFACT_MODE="$2"
		case "${KOPIA_ARTIFACT_MODE}" in build | download) ;; *) die "invalid Kopia mode: ${KOPIA_ARTIFACT_MODE}" 2 ;; esac
		return 0
		;;
	--kopia-git-url)
		require_value "$1" "${2:-}"
		KOPIA_GIT_URL="$2"
		return 0
		;;
	--kopia-ref)
		require_value "$1" "${2:-}"
		KOPIA_GIT_REF="$2"
		[[ "${KOPIA_GIT_REF}" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]] || die "invalid Kopia ref: ${KOPIA_GIT_REF}" 2
		KOPIA_VERSION="${BASH_REMATCH[1]}"
		return 0
		;;
	--no-sourcelens)
		WITH_SOURCELENS=0
		return 0
		;;
	--sourcelens-ref)
		require_value "$1" "${2:-}"
		SOURCELENS_GIT_REF="$2"
		return 0
		;;
	--sourcelens-git-url)
		require_value "$1" "${2:-}"
		SOURCELENS_GIT_URL="$2"
		return 0
		;;
	--go-proxy)
		require_value "$1" "${2:-}"
		OPT_GO_PROXY="$2"
		return 0
		;;
	--go-sumdb)
		require_value "$1" "${2:-}"
		OPT_GO_SUMDB="$2"
		return 0
		;;
	--pip-index-url)
		require_value "$1" "${2:-}"
		OPT_PIP_INDEX_URL="$2"
		return 0
		;;
	--pip-trusted-host)
		require_value "$1" "${2:-}"
		OPT_PIP_TRUSTED_HOST="$2"
		return 0
		;;
	--npm-registry)
		require_value "$1" "${2:-}"
		OPT_NPM_REGISTRY="$2"
		return 0
		;;
	--hfl-only)
		HFL_ONLY_DOWN=1
		return 0
		;;
	--pull)
		FORCE_PULL=1
		return 0
		;;
	--offline)
		DEV_OFFLINE=1
		return 0
		;;
	--pull-timeout)
		require_value "$1" "${2:-}"
		DOCKER_PULL_TIMEOUT="$2"
		return 0
		;;
	--pull-retries)
		require_value "$1" "${2:-}"
		DOCKER_PULL_RETRIES="$2"
		return 0
		;;
	esac
	return 1
}

main() {
	[[ $# -ge 1 ]] || {
		usage
		exit 2
	}
	load_repo_env_defaults
	kopia_load_config
	# shellcheck source=../tools/sourcelens/defaults.env
	source "${ROOT}/tools/sourcelens/defaults.env"
	WITH_SOURCELENS="${BUILD_SOURCELENS:-1}"

	local cmd=""
	local restart_force=0
	if [[ "${HFL_UPGRADE_GATEWAY:-0}" == "1" ]]; then
		UPGRADE_GATEWAY=1
	fi

	while [[ $# -gt 0 ]]; do
		case "$1" in
		up | down | restart | status | doctor | smoke | clean)
			[[ -z "${cmd}" ]] || die "multiple commands specified"
			cmd="$1"
			shift
			;;
		--force)
			restart_force=1
			shift
			;;
		--upgrade-gateway)
			UPGRADE_GATEWAY=1
			shift
			;;
		--runtime | --cache | --data | --all)
			[[ -z "${CLEAN_SCOPE}" ]] || die "multiple clean scopes specified" 2
			CLEAN_SCOPE="${1#--}"
			shift
			;;
		--yes)
			CLEAN_YES=1
			shift
			;;
		-h | --help | help)
			usage
			exit 0
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
		--extension-source)
			require_value "$1" "${2:-}"
			EXTENSION_SOURCES+=("$2")
			shift 2
			;;
		--public-url)
			require_value "$1" "${2:-}"
			DEV_PUBLIC_URL="$2"
			shift 2
			;;
		--admin-public-url)
			require_value "$1" "${2:-}"
			DEV_ADMIN_PUBLIC_URL="$2"
			shift 2
			;;
		--github-download-mirror | --github-token | --docker-download-mirror | --apt-mirror | --ubuntu2404-arch | --kopia-mode | --kopia-git-url | --kopia-ref | --sourcelens-ref | --sourcelens-git-url | --go-proxy | --go-sumdb | --pip-index-url | --pip-trusted-host | --npm-registry | --pull-timeout | --pull-retries | --no-sourcelens | --hfl-only | --pull | --offline)
			parse_common_option "$@" || die "failed to parse option: $1"
			if [[ "$1" == "--no-sourcelens" || "$1" == "--hfl-only" \
				|| "$1" == "--pull" || "$1" == "--offline" ]]; then
				shift
			else
				shift 2
			fi
			;;
		*)
			die "unknown argument: $1 (try --help)" 2
			;;
		esac
	done
	if [[ ${#EXTENSION_SOURCES[@]} -gt 0 ]]; then
		local IFS=','
		EXTENSION_SOURCES_CSV="${EXTENSION_SOURCES[*]}"
	else
		EXTENSION_SOURCES_CSV=""
	fi
	CMD="${cmd}"
	if [[ -z "${LOG_FILE}" ]]; then
		LOG_FILE="${ROOT}/build/logs/dev-${cmd:-command}-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
	fi
	if [[ "${PRINT_CONFIG}" -eq 1 ]]; then
		print_config
		return 0
	fi
	HFL_LOG_TERMINAL_TIMESTAMPS=0
	HFL_LOG_SESSION_MESSAGES=0
	HFL_LOG_CAPTURE_STDOUT=1
	export HFL_LOG_TERMINAL_TIMESTAMPS HFL_LOG_SESSION_MESSAGES \
		HFL_LOG_CAPTURE_STDOUT HFL_LOG_FILE="${LOG_FILE}"
	hfl_logging_configure dev "${LOG_FILE}" "${VERBOSE}"
	LOG_FILE="${HFL_LOG_FILE}"
	hfl_logging_start

	[[ -n "${cmd}" ]] || {
		usage
		exit 2
	}

	if [[ "${restart_force}" -eq 1 && "${cmd}" != "restart" ]]; then
		die "--force is only valid with restart" 2
	fi
	if [[ "${UPGRADE_GATEWAY}" -eq 1 && "${cmd}" != "up" && "${cmd}" != "restart" ]]; then
		die "--upgrade-gateway is only valid with up or restart" 2
	fi
	if [[ "${HFL_ONLY_DOWN}" -eq 1 && "${cmd}" != "down" ]]; then
		die "--hfl-only is only valid with down" 2
	fi
	if [[ -n "${DEV_PUBLIC_URL}${DEV_ADMIN_PUBLIC_URL}" \
		&& "${cmd}" != "up" && "${cmd}" != "restart" ]]; then
		die "--public-url and --admin-public-url are only valid with up or restart" 2
	fi
	if [[ -n "${CLEAN_SCOPE}" && "${cmd}" != "clean" ]]; then
		die "clean scope options are only valid with clean" 2
	fi
	if [[ "${CLEAN_YES}" -eq 1 && "${cmd}" != "clean" ]]; then
		die "--yes is only valid with clean" 2
	fi

	local title="HyperFileLens Development Stack"
	case "${cmd}" in
	down) title="HyperFileLens Development Stack Shutdown" ;;
	status) title="HyperFileLens Development Stack Status" ;;
	doctor) title="HyperFileLens Development Environment Doctor" ;;
	smoke) title="HyperFileLens Development Smoke Test" ;;
	clean) title="HyperFileLens Development Cleanup" ;;
	esac
	hfl_print_banner "${title}"
	if [[ "${cmd}" == "up" || "${cmd}" == "restart" ]]; then
		print_dev_target
	fi

	case "${cmd}" in
	up) cmd_up ;;
	down) cmd_down ;;
	restart) cmd_restart "${restart_force}" ;;
	status) cmd_status ;;
	doctor) cmd_doctor ;;
	smoke) cmd_smoke ;;
	clean) cmd_clean ;;
	esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	trap 'rc=$?; hfl_logging_finish "${rc}"' EXIT
	trap 'exit 130' INT TERM
	main "$@"
fi
