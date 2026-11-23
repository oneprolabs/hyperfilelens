#!/usr/bin/env bash
# Source-build references must follow upstream Compose, not runtime aliases.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${ROOT}/tools/sourcelens/common.sh"
sourcelens_load_config
temporary="$(mktemp -d)"
trap 'rm -rf "${temporary}"' EXIT
SOURCELENS_BUILD_SOURCE="${temporary}"
SOURCELENS_BUILD_COMPOSE_FILE=compose.yml
SOURCELENS_VERSION=0.63.8
SOURCELENS_IMAGE_REGISTRY=example.invalid/hfl
SOURCELENS_DISTRIBUTION_TAG=1.2.3-sl0.63.8
SOURCELENS_UPSTREAM_IMAGE_PREFIX=oneprolabs
for prefix in oneprolabs registry.cn-beijing.aliyuncs.com/oneprolabs; do
	cat >"${temporary}/compose.yml" <<YAML
services:
  backend-api:
    image: ${prefix}/sourcelens-backend:\${APP_VERSION:-latest}
    env_file: ./.env
  frontend:
    image: '${prefix}/sourcelens-frontend:\${APP_VERSION:-latest}'
  lensnode:
    image: "${prefix}/sourcelens-lensnode:\${APP_VERSION:-latest}"
YAML
	for component in backend frontend lensnode; do
		[[ "$(sourcelens_build_image_ref "${component}")" == "${prefix}/sourcelens-${component}:0.63.8" ]]
	done
	# Runtime aliases remain independent of the Compose namespace.
	[[ "$(sourcelens_upstream_image_ref backend)" == oneprolabs/sourcelens-backend:0.63.8 ]]
	sourcelens_image_exists() { [[ "$1" == "${prefix}/sourcelens-"*":0.63.8" ]]; }
	docker() { printf '%s\n' "$*" >>"${temporary}/docker.log"; }
	: >"${temporary}/docker.log"
	sourcelens_upstream_images_ready
	sourcelens_tag_app_images_latest
	[[ "$(wc -l <"${temporary}/docker.log")" -eq 6 ]]
	grep -Fq "tag ${prefix}/sourcelens-backend:0.63.8 example.invalid/hfl/hyperfilelens-sourcelens-backend:1.2.3-sl0.63.8" "${temporary}/docker.log"
done

# Exercise the actual full/partial build orchestration without a Docker daemon.
# The fixture still has all three Compose services and no runtime .env file.
(
	sourcelens_ensure_compose() { SOURCELENS_COMPOSE=(docker compose); }
	sourcelens_current_build_stamp() { printf 'fixture-stamp'; }
	sourcelens_read_built_commit() { printf 'fixture-stamp'; }
	sourcelens_write_built_commit() { printf '%s' "$1" >"${temporary}/stamp"; }
	sourcelens_app_images_ready() { return 1; }
	sourcelens_tag_lensnode_alias() { :; }
	sourcelens_image_exists() { [[ "${missing_output:-0}" == 0 ]]; }
	for helper in \
		sourcelens_restore_source_dockerfiles sourcelens_restore_compose_file \
		sourcelens_patch_dockerfile_uv_network sourcelens_patch_dockerfile_pip_resilience \
		sourcelens_patch_frontend_dockerfile_npm_registry sourcelens_patch_frontend_dockerfile_source_maps \
		sourcelens_patch_compose_lensnode_apt_mirror sourcelens_patch_compose_build_sources \
		sourcelens_patch_compose_uv_network sourcelens_patch_compose_npm_registry; do
		eval "${helper}() { :; }"
	done
	hfl_run_native_command() {
		printf '%s\n' "$*" >>"${temporary}/build.log"
		[[ "${fail_build:-0}" == 0 ]]
	}
	: >"${temporary}/build.log"
	sourcelens_build_app_images 0 0
	[[ ! -s "${temporary}/build.log" ]] # matching stamp: retag only
	[[ ! -f "${temporary}/stamp" ]]
	if (docker() { return 1; }; sourcelens_build_app_images 0 0) >/dev/null 2>&1; then
		printf 'ERROR: cached build accepted a failed image tag\n' >&2
		exit 1
	fi
	SOURCELENS_BUILD_SERVICES=frontend sourcelens_build_app_images 1 1
	grep -Fq 'build --no-cache frontend' "${temporary}/build.log"
	[[ ! -f "${temporary}/stamp" ]] # partial CI builds must not stamp all components
	: >"${temporary}/build.log"
	if fail_build=1 sourcelens_build_app_images 1 0; then
		printf 'ERROR: failed Compose build was accepted\n' >&2
		exit 1
	fi
	[[ ! -f "${temporary}/stamp" ]]
	if (missing_output=1 SOURCELENS_BUILD_SERVICES=frontend sourcelens_build_app_images 1 0) >/dev/null 2>&1; then
		printf 'ERROR: partial build accepted a missing output\n' >&2
		exit 1
	fi
	sourcelens_build_app_images 1 0
	[[ "$(cat "${temporary}/stamp")" == fixture-stamp ]]
)

# Missing build outputs cannot fall back to official or stale distribution tags.
sourcelens_image_exists() { return 1; }
if sourcelens_upstream_images_ready; then
	printf 'ERROR: missing Compose build output was accepted\n' >&2
	exit 1
fi
if (sourcelens_tag_app_images_latest) >/dev/null 2>&1; then
	printf 'ERROR: missing build output was tagged\n' >&2
	exit 1
fi
for declaration in 'image: example/${UNKNOWN}:latest' 'build: .' 'image: ${APP_VERSION:+wrong}'; do
	printf 'services:\n  backend-api:\n    %s\n' "${declaration}" >"${temporary}/compose.yml"
	if sourcelens_build_image_ref backend >/dev/null 2>&1; then
		printf 'ERROR: unsupported Compose image declaration was accepted\n' >&2
		exit 1
	fi
done
printf 'SourceLens source-build image reference tests passed.\n'
