#!/usr/bin/env bash
# Fast release workflow contract checks that do not require Docker or network access.
set -euo pipefail
umask 022

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
online_installer="${ROOT}/deploy/online/install.sh"

grep -F 'configure_curl_retry_options()' "${online_installer}" >/dev/null
grep -F 'curl --retry-all-errors --version' "${online_installer}" >/dev/null
grep -F 'curl --retry-connrefused --version' "${online_installer}" >/dev/null
grep -F 'CURL_RETRY_ARGS=(--retry 3 --retry-delay 2)' "${online_installer}" >/dev/null
grep -F 'local partial="${output}.part"' "${online_installer}" >/dev/null
grep -F 'DEFAULT_CN_DOCKER_CE_APT_BASE="https://mirrors.aliyun.com/docker-ce/linux/ubuntu"' \
	"${online_installer}" >/dev/null
grep -F 'DEFAULT_GLOBAL_DOCKER_CE_APT_BASE="https://download.docker.com/linux/ubuntu"' \
	"${online_installer}" >/dev/null
grep -F 'DOCKER_GPG_PRIMARY_FINGERPRINT="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"' \
	"${online_installer}" >/dev/null
grep -F 'install_online_docker_runtime()' "${online_installer}" >/dev/null
grep -F 'ensure_online_docker_runtime' "${online_installer}" >/dev/null
grep -F 'load_docker_runtime_contract()' "${online_installer}" >/dev/null
grep -F 'assert_docker_service_manager()' "${online_installer}" >/dev/null
grep -F 'DOCKER_PACKAGE_INSTALL_ATTEMPTED' "${online_installer}" >/dev/null
grep -F 'Acquire::Retries=3' "${online_installer}" >/dev/null
grep -F 'DPkg::Lock::Timeout=120' "${online_installer}" >/dev/null
grep -F -- '--no-upgrade' "${online_installer}" >/dev/null
for package in docker-ce docker-ce-cli containerd.io docker-compose-plugin; do
	grep -F "${package}" "${online_installer}" >/dev/null
done
online_docker_versions="${ROOT}/deploy/online/docker-ce-versions.env"
[[ -f "${online_docker_versions}" ]] || {
	printf 'ERROR: missing online Docker CE version contract\n' >&2
	exit 1
}
for release in 2004 2204 2404; do
	for component in ENGINE CLI CONTAINERD COMPOSE_PLUGIN; do
		grep -E "^UBUNTU${release}_${component}_VERSION=[^[:space:]]+$" \
			"${online_docker_versions}" >/dev/null || {
			printf 'ERROR: incomplete online Docker CE version contract: Ubuntu %s %s\n' \
				"${release}" "${component}" >&2
			exit 1
		}
	done
done
if grep -F -- '--retry 3 --retry-all-errors' "${online_installer}" >/dev/null; then
	printf 'ERROR: online installer unconditionally requires curl --retry-all-errors\n' >&2
	exit 1
fi

grep -F './tools/quality/test-docker-image-digest-alias.sh' \
	"${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null
grep -F './tools/quality/test-offline-docker-package-plan.sh' \
	"${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null
grep -F './tools/quality/test-language-pack-runtime-index.sh' \
	"${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null
grep -F './tools/quality/test-bundled-language-pack-lifecycle.sh' \
	"${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null
grep -F 'language-packs/tooling/build-all.sh' \
	"${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null
grep -F 'test_chinese_accept_language_translates_api_error' \
	"${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null
python3 - "${ROOT}/dev/stack.sh" <<'PY'
import pathlib
import re
import sys

stack = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r"(?ms)^prepare_dev_language_packs\(\) \{(?P<body>.*?)^\}", stack)
if match is None:
    raise SystemExit("development language-pack preparation function is missing")
body = match.group("body")
lock = body.find("acquire_installation_lock")
build = body.find('"${LANGUAGE_PACK_BUILDER_IMAGE}" --version')
sync = body.find("sync_bundled_language_packs")
if lock < 0 or build < 0 or sync < 0 or not lock < build < sync:
    raise SystemExit("development language-pack build and synchronization must hold one lock")
PY
grep -F './tools/quality/test-sourcelens-git-mirror.sh' \
	"${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null
grep -F './tools/quality/test-sourcelens-submodule-recovery.sh' \
	"${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null
grep -F './tools/quality/test-dev-stack-upgrade.sh' \
	"${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null
grep -F './tools/quality/test-upgrade-transaction.sh' \
	"${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null

# shellcheck source=../lib/version.sh
source "${ROOT}/tools/lib/version.sh"
actual="$(release_package_basename_for_version v0.1.0 69F809F)"
[[ "${actual}" == "hyperfilelens-0.1.0.tar.gz" ]] || {
	printf 'ERROR: unexpected release package basename: %s\n' "${actual}" >&2
	exit 1
}
enterprise_actual="$(release_package_basename_for_version v0.1.0 69F809F enterprise)"
[[ "${enterprise_actual}" == "hyperfilelens-0.1.0-ee.tar.gz" ]] || {
	printf 'ERROR: unexpected Enterprise package basename: %s\n' "${enterprise_actual}" >&2
	exit 1
}
main_actual="$(release_package_basename_for_version main-69f809f 69F809F)"
[[ "${main_actual}" == "hyperfilelens-main-69f809f.tar.gz" ]] || {
	printf 'ERROR: unexpected Main package basename: %s\n' "${main_actual}" >&2
	exit 1
}

# shellcheck source=../sourcelens/common.sh
source "${ROOT}/tools/sourcelens/common.sh"
sourcelens_load_config
for function_name in \
	sourcelens_build_app_images \
	sourcelens_build_adapter_digest \
	sourcelens_component_build_identity \
	sourcelens_effective_build_inputs_digest \
	sourcelens_prepare_build_source \
	sourcelens_patch_compose_npm_registry \
	sourcelens_patch_runtime_nginx; do
	declare -F "${function_name}" >/dev/null || {
		printf 'ERROR: missing SourceLens function: %s\n' "${function_name}" >&2
		exit 1
	}
done

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

cat >"${tmp}/docker-compose.standalone.yml" <<'YAML'
services:
  frontend:
    build:
      context: ./frontend
      args:
        VITE_GA_ID: ${VITE_GA_ID:-}
    image: example/frontend:latest
  lensnode:
    build:
      context: ./lensnode
      args:
        PIP_INDEX_URL: ${PIP_INDEX_URL:-https://pypi.org/simple}
    image: example/lensnode:latest
YAML
sourcelens_patch_compose_npm_registry "${tmp}"
sourcelens_patch_compose_npm_registry "${tmp}"
[[ "$(grep -Ec '^        NPM_REGISTRY:' "${tmp}/docker-compose.standalone.yml")" -eq 1 ]] || {
	printf 'ERROR: SourceLens frontend npm registry Compose patch is not idempotent\n' >&2
	exit 1
}
grep -F 'NPM_REGISTRY: ${NPM_REGISTRY:-}' \
	"${tmp}/docker-compose.standalone.yml" >/dev/null
[[ "$(grep -Ec '^        CODEGRAPH_REGISTRY:' "${tmp}/docker-compose.standalone.yml")" -eq 1 ]] || {
	printf 'ERROR: SourceLens CodeGraph registry Compose patch is not idempotent\n' >&2
	exit 1
}
grep -F 'CODEGRAPH_REGISTRY: ${NPM_REGISTRY:-https://registry.npmjs.org}' \
	"${tmp}/docker-compose.standalone.yml" >/dev/null

mkdir -p "${tmp}/frontend"
cat >"${tmp}/frontend/Dockerfile" <<'DOCKERFILE'
FROM node:22-alpine
ARG VITE_GA_ID
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --only=production=false
DOCKERFILE
printf '{}\n' >"${tmp}/frontend/package.json"
printf '{}\n' >"${tmp}/frontend/package-lock.json"
sourcelens_patch_frontend_dockerfile_npm_registry "${tmp}"
sourcelens_patch_frontend_dockerfile_npm_registry "${tmp}"
[[ "$(grep -Fc 'ARG NPM_REGISTRY' "${tmp}/frontend/Dockerfile")" -eq 1 ]] || {
	printf 'ERROR: SourceLens frontend npm ARG patch is not idempotent\n' >&2
	exit 1
}
grep -F 'npm config set audit false' "${tmp}/frontend/Dockerfile" >/dev/null
grep -F 'npm config set fund false' "${tmp}/frontend/Dockerfile" >/dev/null
grep -F 'npm config set update-notifier false' "${tmp}/frontend/Dockerfile" >/dev/null
grep -F 'npm config set fetch-retries 5' "${tmp}/frontend/Dockerfile" >/dev/null
grep -F 'npm config set registry "${NPM_REGISTRY}"' "${tmp}/frontend/Dockerfile" >/dev/null

cat >"${tmp}/nginx.conf" <<'NGINX'
server {
    listen 443 ssl;
    ssl_certificate /etc/nginx/certs/nginx-selfsigned.crt;
    ssl_certificate_key /etc/nginx/certs/nginx-selfsigned.key;
    set $ui_upstream http://frontend:80;
    location / {
        proxy_pass $ui_upstream;
    }
}
NGINX
sourcelens_patch_runtime_nginx "${tmp}/nginx.conf"
sourcelens_patch_runtime_nginx "${tmp}/nginx.conf"
grep -F '/etc/nginx/certs/tls.crt' "${tmp}/nginx.conf" >/dev/null
grep -F '/etc/nginx/certs/tls.key' "${tmp}/nginx.conf" >/dev/null
grep -F 'set $ui_upstream http://web:80;' "${tmp}/nginx.conf" >/dev/null
grep -F 'sourcelens_web_service()' "${ROOT}/deploy/installer/install.sh" >/dev/null
grep -F 'grep -Fxq ui' "${ROOT}/deploy/installer/install.sh" >/dev/null
[[ "$(grep -Fc 'include /etc/nginx/hfl-maintenance/run-creation-gate.conf;' "${tmp}/nginx.conf")" -eq 1 ]]
[[ "$(grep -Fc 'if ($hfl_sourcelens_run_creation_blocked)' "${tmp}/nginx.conf")" -eq 1 ]]

adapter_digest="$(sourcelens_build_adapter_digest)"
[[ "${adapter_digest}" =~ ^[0-9a-f]{64}$ ]]
stamp_amd64="$({
	SOURCELENS_VERSION=0.20.0
	SOURCELENS_DOCKER_PLATFORM=linux/amd64
	SOURCELENS_BUILD_COMPOSE_FILE=docker-compose.standalone.yml
	sourcelens_current_build_stamp
})"
stamp_arm64="$({
	SOURCELENS_VERSION=0.20.0
	SOURCELENS_DOCKER_PLATFORM=linux/arm64
	SOURCELENS_BUILD_COMPOSE_FILE=docker-compose.standalone.yml
	sourcelens_current_build_stamp
})"
[[ "${stamp_amd64}" != "${stamp_arm64}" ]]
grep -F ':linux/amd64:docker-compose.standalone.yml:' <<<"${stamp_amd64}" >/dev/null
while IFS='=' read -r input_name input_value; do
	changed_stamp="$(
		(
			export "${input_name}=${input_value}"
			SOURCELENS_VERSION=0.20.0
			SOURCELENS_DOCKER_PLATFORM=linux/amd64
			SOURCELENS_BUILD_COMPOSE_FILE=docker-compose.standalone.yml
			sourcelens_current_build_stamp
		)
	)"
	if [[ "${changed_stamp}" == "${stamp_amd64}" ]]; then
		printf 'ERROR: SourceLens build input does not invalidate the reuse stamp: %s\n' \
			"${input_name}" >&2
		exit 1
	fi
done <<'BUILD_INPUTS'
SOURCELENS_APT_MIRROR=https://mirror.example.invalid/debian
SOURCELENS_PIP_INDEX_URL=https://packages.example.invalid/simple
SOURCELENS_PIP_TRUSTED_HOST=packages.example.invalid
SOURCELENS_UV_HTTP_TIMEOUT=321
SOURCELENS_UV_CONCURRENT_DOWNLOADS=7
SOURCELENS_PIP_RETRY_MAX=9
SOURCELENS_PIP_RETRY_DELAY=11
SOURCELENS_UV_VERSION=0.10.3
SOURCELENS_NPM_REGISTRY=https://npm.example.invalid
SOURCELENS_BUILD_SOURCE_MAPS=1
SOURCELENS_UPSTREAM_IMAGE_PREFIX=example-build-prefix
APP_RELEASE_DATE=2099-01-01
VITE_API_BASE_URL=https://api.example.invalid
VITE_TURNSTILE_SITE_KEY=site-key
VITE_SENTRY_DSN=https://public@example.invalid/1
VITE_SENTRY_ENVIRONMENT=contract
VITE_SENTRY_TRACES_SAMPLE_RATE=0.75
VITE_SENTRY_SEND_DEFAULT_PII=true
VITE_GA_ID=G-CONTRACT
BUILD_INPUTS
backend_inputs="$(sourcelens_effective_build_inputs_digest backend)"
frontend_inputs="$(sourcelens_effective_build_inputs_digest frontend)"
lensnode_inputs="$(sourcelens_effective_build_inputs_digest lensnode)"
npm_backend_inputs="$({
	SOURCELENS_NPM_REGISTRY=https://npm.example.invalid
	sourcelens_effective_build_inputs_digest backend
})"
npm_frontend_inputs="$({
	SOURCELENS_NPM_REGISTRY=https://npm.example.invalid
	sourcelens_effective_build_inputs_digest frontend
})"
npm_lensnode_inputs="$({
	SOURCELENS_NPM_REGISTRY=https://npm.example.invalid
	sourcelens_effective_build_inputs_digest lensnode
})"
pip_backend_inputs="$({
	SOURCELENS_PIP_INDEX_URL=https://packages.example.invalid/simple
	sourcelens_effective_build_inputs_digest backend
})"
pip_frontend_inputs="$({
	SOURCELENS_PIP_INDEX_URL=https://packages.example.invalid/simple
	sourcelens_effective_build_inputs_digest frontend
})"
[[ "${npm_backend_inputs}" == "${backend_inputs}" ]]
[[ "${npm_frontend_inputs}" != "${frontend_inputs}" ]]
[[ "${npm_lensnode_inputs}" != "${lensnode_inputs}" ]]
[[ "${pip_backend_inputs}" != "${backend_inputs}" ]]
[[ "${pip_frontend_inputs}" == "${frontend_inputs}" ]]

component_identity="$({
	SOURCELENS_VERSION=0.20.0
	SOURCELENS_BUILD_COMPOSE_FILE=docker-compose.standalone.yml
	sourcelens_component_build_identity backend 0123456789abcdef
})"
version_identity="$({
	SOURCELENS_VERSION=0.20.1
	SOURCELENS_BUILD_COMPOSE_FILE=docker-compose.standalone.yml
	sourcelens_component_build_identity backend 0123456789abcdef
})"
compose_identity="$({
	SOURCELENS_VERSION=0.20.0
	SOURCELENS_BUILD_COMPOSE_FILE=docker-compose.alternate.yml
	sourcelens_component_build_identity backend 0123456789abcdef
})"
[[ "${version_identity}" != "${component_identity}" ]]
[[ "${compose_identity}" != "${component_identity}" ]]
grep -F 'source_version=0.20.0' <<<"${component_identity}" >/dev/null
grep -F 'build_compose_file=docker-compose.standalone.yml' \
	<<<"${component_identity}" >/dev/null

grep -F 'archive.extractall(destination, members=members)' \
	"${ROOT}/src/agent/scripts/package.sh" >/dev/null
grep -F 'chown postgres:postgres /var/lib/postgresql/data' \
	"${ROOT}/deploy/installer/sourcelens/docker-compose.template.yml" >/dev/null
if grep -R -n 'sourcelens_prepare_source_build_env' \
	"${ROOT}/release" "${ROOT}/dev" "${ROOT}/tools/sourcelens" >/dev/null; then
	printf 'ERROR: obsolete SourceLens prepare helper is still referenced\n' >&2
	exit 1
fi

config="$(${ROOT}/release/build.sh --no-cache --print-config)"
grep -F 'no_cache=1' <<<"${config}" >/dev/null

grep -F -- '--prebuilt' "${ROOT}/release/build-sourcelens.sh" >/dev/null
grep -F 'ln "${source_archive}" "${temporary}"' \
	"${ROOT}/tools/sourcelens/common.sh" >/dev/null
grep -F 'SOURCELENS_GIT_REF="${SOURCELENS_GIT_REF:-v0.47.9}"' \
	"${ROOT}/tools/sourcelens/defaults.env" >/dev/null
grep -F 'SOURCELENS_GIT_REF=v0.47.9' \
	"${ROOT}/.env.example" >/dev/null
grep -F 'SOURCELENS_BUILD_COMPOSE_FILE="${SOURCELENS_BUILD_COMPOSE_FILE:-docker-compose.standalone.yml}"' \
	"${ROOT}/tools/sourcelens/defaults.env" >/dev/null
grep -F 'SOURCELENS_UV_VERSION="${SOURCELENS_UV_VERSION:-0.10.2}"' \
	"${ROOT}/tools/sourcelens/defaults.env" >/dev/null
grep -F 'set_key("DJANGO_DEBUG", "true")' \
	"${ROOT}/deploy/installer/sourcelens/patch-env-runtime.py" >/dev/null
for setting in \
	'LENSNODE_PLANNING_REASONING_EFFORT: "medium"' \
	'LENSNODE_EXECUTION_BACKEND: "trusted_container"' \
	'LENSNODE_MAX_CONCURRENT_RUNS: "1"'; do
	grep -F "${setting}" \
		"${ROOT}/deploy/installer/sourcelens/docker-compose.template.yml" >/dev/null
done
grep -F './deploy/nginx/hfl-maintenance:/etc/nginx/hfl-maintenance:ro' \
	"${ROOT}/deploy/installer/sourcelens/docker-compose.template.yml" >/dev/null
grep -F 'map "$request_method:$uri" $hfl_sourcelens_run_creation_blocked' \
	"${ROOT}/deploy/installer/sourcelens/run-creation-gate-off.conf" >/dev/null
python3 - "${ROOT}/deploy/installer/install.sh" <<'PY'
import pathlib
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
expected = """\
\t\tbegin_sourcelens_maintenance_gate
\t\tstop_bundled_sourcelens
\t\tSOURCELENS_UPGRADE_STARTED=1
\t\tinstall_bundled_sourcelens
"""
if expected not in text:
    raise SystemExit(
        "SourceLens replacement must be marked started only after shutdown succeeds"
    )
upgrade = text[text.index("cmd_upgrade() {") : text.index("\nmain() {")]
if not (
    upgrade.index("apply_upgrade_files")
    < upgrade.index("configure_lens_bridge_env")
    < upgrade.index('compose_color "${target_color}" up')
):
    raise SystemExit(
        "SourceLens bridge environment must be finalized before candidate startup"
    )
PY

sourcelens_common="${ROOT}/tools/sourcelens/common.sh"
sourcelens_build_body="$(sed -n '/^sourcelens_build_app_images()/,/^}/p' "${sourcelens_common}")"
grep -F 'https://deb.debian.org/debian' <<<"${sourcelens_build_body}" >/dev/null
grep -F 'https://pypi.org/simple' <<<"${sourcelens_build_body}" >/dev/null
if grep -F 'mirrors.tuna.tsinghua.edu.cn' <<<"${sourcelens_build_body}" >/dev/null; then
	printf 'ERROR: SourceLens build must not enable a third-party mirror by default\n' >&2
	exit 1
fi

mkdir -p "${tmp}/source-patch/lensnode"
cat >"${tmp}/source-patch/Dockerfile" <<'DOCKERFILE'
FROM ubuntu:24.04
ARG PIP_TRUSTED_HOST=pypi.org
ENV PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}
RUN set -eux; \
    pip install \
        uv; \
    uv pip install --system .
DOCKERFILE
cp "${tmp}/source-patch/Dockerfile" "${tmp}/source-patch/lensnode/Dockerfile"
sourcelens_patch_dockerfile_uv_network "${tmp}/source-patch" 120 2 0.10.2
for dockerfile in "${tmp}/source-patch/Dockerfile" "${tmp}/source-patch/lensnode/Dockerfile"; do
	grep -F 'ARG UV_VERSION=0.10.2' "${dockerfile}" >/dev/null
	grep -F '"uv==${UV_VERSION}"' "${dockerfile}" >/dev/null
done
cat >"${tmp}/source-patch/lensnode/Dockerfile" <<'DOCKERFILE'
# syntax=docker/dockerfile:1.6
FROM python:3.12-slim
ARG PIP_TRUSTED_HOST=pypi.org
ARG UV_HTTP_TIMEOUT=120
ARG UV_CONCURRENT_DOWNLOADS=2
ARG UV_VERSION=0.10.2
ENV UV_HTTP_TIMEOUT=${UV_HTTP_TIMEOUT} \
    UV_CONCURRENT_DOWNLOADS=${UV_CONCURRENT_DOWNLOADS}
RUN pip install "uv==${UV_VERSION}"
RUN uv pip install --system . \
    && rm -rf /root/.cache /tmp/*
DOCKERFILE
sourcelens_patch_dockerfile_pip_resilience "${tmp}/source-patch" 5 5
resilience_digest="$(sha256sum \
	"${tmp}/source-patch/Dockerfile" \
	"${tmp}/source-patch/lensnode/Dockerfile")"
sourcelens_patch_dockerfile_pip_resilience "${tmp}/source-patch" 5 5
[[ "${resilience_digest}" == "$(sha256sum \
	"${tmp}/source-patch/Dockerfile" \
	"${tmp}/source-patch/lensnode/Dockerfile")" ]]
for dockerfile in "${tmp}/source-patch/Dockerfile" "${tmp}/source-patch/lensnode/Dockerfile"; do
	grep -F -- '--mount=type=cache,target=/opt/hfl-build-cache/uv,sharing=locked' \
		"${dockerfile}" >/dev/null
	if grep -F -- '--mount=type=cache,target=/root/.cache/' "${dockerfile}" >/dev/null; then
		printf 'ERROR: SourceLens cache mount conflicts with upstream /root/.cache cleanup\n' >&2
		exit 1
	fi
	grep -F 'hfl_retry uv pip install' "${dockerfile}" >/dev/null
done
grep -F 'rm -rf /root/.cache /tmp/*' \
	"${tmp}/source-patch/lensnode/Dockerfile" >/dev/null

cat >"${tmp}/source-patch/docker-compose.standalone.yml" <<'YAML'
services:
  backend-api:
    build:
      context: .
      args:
        APT_MIRROR_URL: ${APT_MIRROR_URL:-https://mirrors.tuna.tsinghua.edu.cn/debian}
        PIP_INDEX_URL: ${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}
        PIP_TRUSTED_HOST: ${PIP_TRUSTED_HOST:-pypi.tuna.tsinghua.edu.cn}
  frontend:
    build:
      context: ./frontend
      args:
        VITE_GA_ID: ${VITE_GA_ID:-}
    image: example/frontend:latest
  lensnode:
    build:
      context: ./lensnode
      args:
        PIP_INDEX_URL: ${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}
        PIP_TRUSTED_HOST: ${PIP_TRUSTED_HOST:-pypi.tuna.tsinghua.edu.cn}
    image: example/lensnode:latest
YAML
sourcelens_patch_compose_lensnode_apt_mirror \
	"${tmp}/source-patch" https://deb.debian.org/debian
sourcelens_patch_compose_build_sources \
	"${tmp}/source-patch" \
	http://archive.ubuntu.com/ubuntu \
	https://deb.debian.org/debian \
	https://pypi.org/simple \
	pypi.org
sourcelens_patch_compose_uv_network "${tmp}/source-patch" 120 2 0.10.2
sourcelens_patch_compose_npm_registry "${tmp}/source-patch"
if grep -F 'mirrors.tuna.tsinghua.edu.cn' \
	"${tmp}/source-patch/docker-compose.standalone.yml" >/dev/null; then
	printf 'ERROR: patched SourceLens Compose still defaults to a third-party mirror\n' >&2
	exit 1
fi
grep -F 'APT_MIRROR_URL: ${APT_MIRROR_URL:-https://deb.debian.org/debian}' \
	"${tmp}/source-patch/docker-compose.standalone.yml" >/dev/null
grep -F 'APT_MIRROR_URL: ${DEBIAN_APT_MIRROR_URL:-https://deb.debian.org/debian}' \
	"${tmp}/source-patch/docker-compose.standalone.yml" >/dev/null
[[ "$(grep -Fc 'PIP_INDEX_URL: ${PIP_INDEX_URL:-https://pypi.org/simple}' \
	"${tmp}/source-patch/docker-compose.standalone.yml")" -eq 2 ]]
[[ "$(grep -Fc 'UV_VERSION: ${UV_VERSION:-0.10.2}' \
	"${tmp}/source-patch/docker-compose.standalone.yml")" -eq 2 ]]
grep -F 'NPM_REGISTRY: ${NPM_REGISTRY:-}' \
	"${tmp}/source-patch/docker-compose.standalone.yml" >/dev/null
grep -F 'CODEGRAPH_REGISTRY: ${NPM_REGISTRY:-https://registry.npmjs.org}' \
	"${tmp}/source-patch/docker-compose.standalone.yml" >/dev/null

grep -F '# SourceLens v0.47.9 requires no HFL functional patches.' \
	"${ROOT}/tools/sourcelens/patches/series" >/dev/null
[[ -x "${ROOT}/tools/sourcelens/update-runtime-contract.sh" ]]
[[ -x "${ROOT}/tools/quality/test-sourcelens-runtime-contract.sh" ]]
grep -F 'sourcelens_verify_runtime_contract "${SOURCELENS_SOURCE_CACHE}"' \
	"${ROOT}/tools/sourcelens/common.sh" >/dev/null
for workflow in pr_checks.yml release_pipeline.yml enterprise_saas_upgrade.yml; do
	grep -F './tools/sourcelens/update-runtime-contract.sh --check' \
		"${ROOT}/.github/workflows/${workflow}" >/dev/null
done
[[ -f "${ROOT}/tools/sourcelens/patches/retired/lensnode-tls-v0.4.0.patch" ]]
if [[ -e "${ROOT}/deploy/installer/sourcelens/lensnode-tls.patch" \
	|| -e "${ROOT}/tools/sourcelens/lensnode-patch.sh" ]]; then
	printf 'ERROR: retired SourceLens TLS patch remains active\n' >&2
	exit 1
fi
[[ "$(sourcelens_patch_manifest_json)" == "[]" ]]
[[ "$(sourcelens_patchset_digest | wc -c)" -eq 65 ]]

original_build_dir="${SOURCELENS_BUILD_DIR}"
original_source_cache="${SOURCELENS_SOURCE_CACHE}"
original_build_source="${SOURCELENS_BUILD_SOURCE}"
original_patch_root="${SOURCELENS_PATCH_ROOT}"
SOURCELENS_BUILD_DIR="${tmp}/disposable-source"
SOURCELENS_SOURCE_CACHE="${SOURCELENS_BUILD_DIR}/source"
SOURCELENS_BUILD_SOURCE="${SOURCELENS_BUILD_DIR}/worktree"
mkdir -p "${SOURCELENS_SOURCE_CACHE}/nested"
git -C "${SOURCELENS_SOURCE_CACHE}" init -q
printf 'pristine\n' >"${SOURCELENS_SOURCE_CACHE}/tracked.txt"
printf 'submodule payload\n' >"${SOURCELENS_SOURCE_CACHE}/nested/payload.txt"
git -C "${SOURCELENS_SOURCE_CACHE}" add tracked.txt nested/payload.txt
git -C "${SOURCELENS_SOURCE_CACHE}" \
	-c user.name=contract -c user.email=contract@example.invalid \
	commit -qm fixture
sourcelens_prepare_build_source
[[ -z "$(git -C "${SOURCELENS_SOURCE_CACHE}" status --porcelain)" ]]
[[ "$(<"${SOURCELENS_BUILD_SOURCE}/nested/payload.txt")" == "submodule payload" ]]
[[ ! -e "${SOURCELENS_BUILD_SOURCE}/.git" ]]
mkdir -p "${tmp}/patches/active"
printf 'active/fixture.patch\n' >"${tmp}/patches/series"
printf 'patched\n' >"${SOURCELENS_SOURCE_CACHE}/tracked.txt"
git -C "${SOURCELENS_SOURCE_CACHE}" diff --full-index -- tracked.txt \
	>"${tmp}/patches/active/fixture.patch"
git -C "${SOURCELENS_SOURCE_CACHE}" checkout -- tracked.txt
sed -E -i \
	's/^index ([0-9a-f]{12})[0-9a-f]*\.\.([0-9a-f]{12})[0-9a-f]*/index \1..\2/' \
	"${tmp}/patches/active/fixture.patch"
SOURCELENS_PATCH_ROOT="${tmp}/patches"
sourcelens_apply_hfl_patch_series "${SOURCELENS_BUILD_SOURCE}"
[[ "$(<"${SOURCELENS_BUILD_SOURCE}/tracked.txt")" == "patched" ]]
[[ "$(<"${SOURCELENS_SOURCE_CACHE}/tracked.txt")" == "pristine" ]]
[[ "$(sourcelens_patch_manifest_json)" == *'"file":"active/fixture.patch"'* ]]
SOURCELENS_BUILD_DIR="${original_build_dir}"
SOURCELENS_SOURCE_CACHE="${original_source_cache}"
SOURCELENS_BUILD_SOURCE="${original_build_source}"
SOURCELENS_PATCH_ROOT="${original_patch_root}"

grep -F 'MAX_RELEASE_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024' \
	"${ROOT}/release/ci/report-release-size.py" >/dev/null
for data_dir in document-attachments deliverables; do
	grep -F "./data/${data_dir}:/opt/${data_dir}" \
		"${ROOT}/deploy/installer/sourcelens/docker-compose.template.yml" >/dev/null
done
grep -F 'stop_grace_period: 270s' \
	"${ROOT}/deploy/installer/sourcelens/docker-compose.template.yml" >/dev/null
grep -F 'LENSNODE_DRAIN_TIMEOUT_S: "240"' \
	"${ROOT}/deploy/installer/sourcelens/docker-compose.template.yml" >/dev/null
for setting in \
	'LENSNODE_PLANNING_REASONING_EFFORT: "medium"' \
	'LENSNODE_EXECUTION_BACKEND: "trusted_container"' \
	'LENSNODE_MAX_CONCURRENT_RUNS: "1"'; do
	grep -F "${setting}" \
		"${ROOT}/deploy/bootstrap/gateway-install-lensnode-sidecar.sh" >/dev/null
done
grep -F 'LENSNODE_TLS_SKIP_VERIFY: "${HFL_INSECURE_TLS}"' \
	"${ROOT}/deploy/bootstrap/gateway-install-lensnode-sidecar.sh" >/dev/null

workflow="${ROOT}/.github/workflows/release_pipeline.yml"
release_workflow="${ROOT}/.github/workflows/community_release.yml"
test_workflow="${ROOT}/.github/workflows/enterprise_release.yml"
production_workflow="${ROOT}/.github/workflows/enterprise_prod_promotion.yml"
enterprise_promotion_workflow="${ROOT}/.github/workflows/enterprise_promotion.yml"
agent_certification="${ROOT}/release/ci/certify-agent-candidate.py"
[[ -f "${workflow}" ]] || {
	printf 'ERROR: reusable artifact workflow is missing\n' >&2
	exit 1
}
for entrypoint in \
	"${release_workflow}" "${test_workflow}" "${production_workflow}" \
	"${enterprise_promotion_workflow}"; do
	[[ -f "${entrypoint}" ]] || {
		printf 'ERROR: deployment entrypoint is missing: %s\n' "${entrypoint}" >&2
		exit 1
	}
done
[[ "$(awk '/^  assemble-release:/{job=1} job && /timeout-minutes:/{print $2; exit}' "${workflow}")" == "120" ]] || {
	printf 'ERROR: release assembly timeout must cover package transfer and retries\n' >&2
	exit 1
}
[[ "$(awk '/^  verify-release:/{job=1} job && /timeout-minutes:/{print $2; exit}' "${workflow}")" == "120" ]] || {
	printf 'ERROR: release verification timeout must cover package download and offline install\n' >&2
	exit 1
}
grep -F 'timeout-minutes: 120' "${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'docker_preexisting=0' "${ROOT}/.github/scripts/remote-deploy.sh" >/dev/null
grep -F 'Existing Docker not found; the verified offline release bundle will install Docker CE' \
	"${ROOT}/.github/scripts/remote-deploy.sh" >/dev/null
grep -F 'existing Docker daemon is not reachable' \
	"${ROOT}/.github/scripts/remote-deploy.sh" >/dev/null
grep -F 'turnstile_enabled: false' "${workflow}" >/dev/null
grep -F 'public_url: ${{ vars.COMMUNITY_PUBLIC_URL }}' "${workflow}" >/dev/null
grep -F 'admin_public_url: ${{ vars.COMMUNITY_ADMIN_PUBLIC_URL }}' "${workflow}" >/dev/null
if grep -F 'COMMUNITY_RELEASE_DOWNLOAD_PROXY_URL' "${workflow}" >/dev/null; then
	printf 'ERROR: Community deployment must not read a Release download proxy\n' >&2
	exit 1
fi
grep -F 'turnstile_enabled: ${{ vars.TEST_TURNSTILE_ENABLED' "${workflow}" >/dev/null
grep -F 'public_url: ${{ vars.TEST_PUBLIC_URL }}' "${workflow}" >/dev/null
grep -F 'admin_public_url: ${{ vars.TEST_ADMIN_PUBLIC_URL }}' "${workflow}" >/dev/null
grep -F 'turnstile_enabled: ${{ vars.PROD_TURNSTILE_ENABLED' "${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
grep -F 'public_url: ${{ vars.PROD_PUBLIC_URL }}' "${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
grep -F 'admin_public_url: ${{ vars.PROD_ADMIN_PUBLIC_URL }}' "${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
grep -F 'release_download_proxy_url: ${{ vars.PROD_RELEASE_DOWNLOAD_PROXY_URL }}' \
	"${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
grep -F 'hfl_insecure_tls: ${{ vars.TEST_HFL_INSECURE_TLS }}' "${workflow}" >/dev/null
grep -F 'hfl_insecure_tls: ${{ vars.COMMUNITY_HFL_INSECURE_TLS }}' "${workflow}" >/dev/null
grep -F 'hfl_insecure_tls: ${{ vars.PROD_HFL_INSECURE_TLS }}' "${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
if grep -F 'PROD_PUBLIC_HOST' "${workflow}" "${production_workflow}" >/dev/null; then
	printf 'ERROR: release workflow still uses the ambiguous PROD_PUBLIC_HOST variable\n' >&2
	exit 1
fi
grep -F '"TURNSTILE_ENABLED"' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F '"HFL_INSECURE_TLS"' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'test:1|community:1|prod:0' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
for variable in \
	SMTP_HOST SMTP_PORT SMTP_USERNAME SMTP_SECURITY EMAIL_FROM; do
	grep -F "TEST_${variable}" "${workflow}" >/dev/null
	grep -F "PROD_${variable}" "${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
done
grep -F 'smtp_password: ${{ secrets.TEST_SMTP_PASSWORD }}' "${workflow}" >/dev/null
grep -F 'smtp_password: ${{ secrets.PROD_SMTP_PASSWORD }}' "${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
for variable in AI_MODEL_PROVIDER AI_MODEL_ID AI_MODEL_DISPLAY_NAME; do
	grep -F "TEST_${variable}" "${workflow}" >/dev/null
	grep -F "COMMUNITY_${variable}" "${workflow}" >/dev/null
	grep -F "PROD_${variable}" "${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
done
for variable in \
	AI_MULTIMODAL_MODEL_PROVIDER \
	AI_MULTIMODAL_MODEL_ID \
	AI_MULTIMODAL_MODEL_DISPLAY_NAME; do
	grep -F "TEST_${variable}" "${workflow}" >/dev/null
	grep -F "COMMUNITY_${variable}" "${workflow}" >/dev/null
	grep -F "PROD_${variable}" "${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
done
for secret in AI_MODEL_API_BASE AI_MODEL_API_KEY; do
	grep -F "secrets.TEST_${secret}" "${workflow}" >/dev/null
	grep -F "secrets.COMMUNITY_${secret}" "${workflow}" >/dev/null
	grep -F "secrets.PROD_${secret}" "${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
done
grep -F '/opt/hyperfilelens/install.sh manage ensure_platform_ai_model' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'HFL_AI_MODEL_CONNECTIVITY=failed' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F '"role": "multimodal"' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F '"supports_vision": True' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'payload["supports_vision"] = True' \
	"${ROOT}/.github/scripts/reconcile-saas-ai-model.sh" >/dev/null
grep -F 'exit "$command_status"' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
if grep -F '"AI_MODEL_API_KEY=$AI_MODEL_API_KEY"' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null; then
	printf 'ERROR: AI model API key must not be persisted in the runtime .env\n' >&2
	exit 1
fi
for variable in EMAIL_SIGNUP_ENABLED EMAIL_CODE_LOGIN_ENABLED GOOGLE_OAUTH_ENABLED GOOGLE_CLIENT_ID; do
	grep -F "vars.TEST_${variable}" "${workflow}" >/dev/null
	grep -F "vars.PROD_${variable}" "${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
done
grep -F "email_code_login_enabled: \${{ vars.TEST_EMAIL_CODE_LOGIN_ENABLED == 'true' }}" \
	"${workflow}" >/dev/null
grep -F "email_code_login_enabled: \${{ vars.PROD_EMAIL_CODE_LOGIN_ENABLED == 'true' }}" \
	"${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
grep -F 'secrets.TEST_GOOGLE_CLIENT_SECRET' "${workflow}" >/dev/null
grep -F 'secrets.PROD_GOOGLE_CLIENT_SECRET' "${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
for setting in \
	COMMUNITY_EMAIL_SIGNUP_ENABLED COMMUNITY_EMAIL_CODE_LOGIN_ENABLED \
	COMMUNITY_GOOGLE_OAUTH_ENABLED COMMUNITY_GOOGLE_CLIENT_ID \
	COMMUNITY_TURNSTILE_ENABLED COMMUNITY_TURNSTILE_SITE_KEY \
	COMMUNITY_SMTP_HOST COMMUNITY_SMTP_PORT COMMUNITY_SMTP_USERNAME \
	COMMUNITY_SMTP_SECURITY COMMUNITY_EMAIL_FROM \
	COMMUNITY_GOOGLE_CLIENT_SECRET COMMUNITY_TURNSTILE_SECRET_KEY \
	COMMUNITY_SMTP_PASSWORD; do
	if grep -F "${setting}" "${workflow}" >/dev/null; then
		printf 'ERROR: Community release still reads unsupported auth setting %s\n' \
			"${setting}" >&2
		exit 1
	fi
done
for setting in \
	'turnstile_enabled: false' \
	'email_signup_enabled: false' \
	'email_code_login_enabled: false' \
	'google_oauth_enabled: false'; do
	grep -F "${setting}" "${workflow}" >/dev/null
done
for runtime_key in \
	HFL_EMAIL_SIGNUP_ENABLED HFL_EMAIL_CODE_LOGIN_ENABLED HFL_GOOGLE_OAUTH_ENABLED \
	GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET SMTP_PASSWORD; do
	grep -F "\"${runtime_key}\"" "${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
done
if grep -F 'HFL_EMAIL_SIGNUP_ENABLED=false' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null; then
	printf 'ERROR: reusable deployment must not hardcode email sign-up off\n' >&2
	exit 1
fi
grep -F 'Google OAuth settings are missing or malformed' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'SMTP settings are partial; deployment will preserve' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'No SMTP settings were staged' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F '/opt/hyperfilelens/install.sh manage ensure_deployment_identity_settings' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F '/opt/hyperfilelens/install.sh manage check_google_oauth_readiness' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'python manage.py check_google_oauth_readiness' \
	"${ROOT}/deploy/installer/install.sh" "${ROOT}/dev/stack.sh" >/dev/null
grep -F '::warning title=Google OAuth readiness::' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'HFL_IDENTITY_STATUS=warning' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'umask 077 && cat > /var/tmp/hyperfilelens-runtime-' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
if git -C "${ROOT}" grep -n -E \
	'HFL_SELF_SERVICE_PASSWORD_RESET|HFL_EMAIL_SETTINGS_MODE' -- . \
	':!tools/quality/check-release-contracts.sh'; then
	printf 'ERROR: obsolete email deployment feature flags remain\n' >&2
	exit 1
fi
if git -C "${ROOT}" grep -n 'CAPTCHA_PROVIDER' -- . \
	':!tools/config/sync_env.py' \
	':!tools/quality/check-release-contracts.sh'; then
	printf 'ERROR: CAPTCHA_PROVIDER must not remain in runtime or deployment configuration\n' >&2
	exit 1
fi
grep -F 'Capture failed install diagnostics' "${workflow}" >/dev/null
grep -F 'logs --no-color --tail 200' "${workflow}" >/dev/null
if grep -E '^  (workflow_dispatch|push|schedule):' "${workflow}" >/dev/null; then
	printf 'ERROR: reusable artifact workflow must not expose a direct trigger\n' >&2
	exit 1
fi
grep -F 'workflow_call:' "${workflow}" >/dev/null
grep -F 'workflow_dispatch:' "${release_workflow}" >/dev/null
grep -F 'name: HFL - Enterprise Release & Deploy' "${test_workflow}" >/dev/null
grep -F 'name: HFL - Community Release & Deploy' "${release_workflow}" >/dev/null
grep -F 'name: HFL - Enterprise PROD Promotion' "${production_workflow}" >/dev/null
grep -F 'name: HFL - Release Pipeline (Reusable)' "${workflow}" >/dev/null
grep -F 'uses: ./.github/workflows/release_pipeline.yml' "${release_workflow}" >/dev/null
grep -F 'channel: release' "${release_workflow}" >/dev/null
grep -F 'workflow_dispatch:' "${test_workflow}" >/dev/null
grep -F 'edition: enterprise' "${test_workflow}" >/dev/null
grep -F 'workflow_dispatch:' "${production_workflow}" >/dev/null
for job in \
	prepare quality build-hfl-images build-sourcelens-images build-agent \
	certify-source-host agent-release-gate \
	build-host-debs export-hfl-images export-sourcelens-bundle \
	export-runtime-images assemble-release verify-release publish-release \
	deploy-test deploy-community promote-production; do
	grep -F "  ${job}:" "${workflow}" >/dev/null || {
		printf 'ERROR: artifact workflow job is missing: %s\n' "${job}" >&2
		exit 1
	}
done
grep -F "vars.TEST_AUTO_DEPLOY != 'false'" "${workflow}" >/dev/null
grep -F "vars.COMMUNITY_AUTO_DEPLOY != 'false'" "${workflow}" >/dev/null
grep -F "vars.PROD_AUTO_DEPLOY != 'false'" "${workflow}" >/dev/null

for job in build-hfl-images build-sourcelens-images build-host-debs export-runtime-images; do
	body="$(sed -n "/^  ${job}:/,/^  [a-zA-Z0-9_-]*:/p" "${workflow}")"
	grep -F 'needs: [prepare, quality]' <<<"${body}" >/dev/null || {
		printf 'ERROR: %s must start after quality without waiting for Agent certification\n' "${job}" >&2
		exit 1
	}
done
grep -F "format('hyperfilelens-package-{0}', inputs.release_tag)" "${workflow}" >/dev/null
grep -F 'cancel-in-progress: false' "${workflow}" >/dev/null
grep -F 'git merge-base --is-ancestor "$COMMIT" origin/main' "${workflow}" >/dev/null
grep -F 'cleanup-enterprise-candidate:' "${workflow}" >/dev/null
grep -F 'gh release delete "$ARTIFACT_ID"' "${workflow}" >/dev/null
grep -F 'make_latest=legacy' "${workflow}" >/dev/null
grep -F -- '--json apiUrl --jq '\''.apiUrl'\''' "${workflow}" >/dev/null
grep -F 'release_api_prefix="https://api.github.com/repos/${GITHUB_REPOSITORY}/releases/"' \
	"${workflow}" >/dev/null
if grep -F 'releases/tags/${ARTIFACT_ID}' "${workflow}" >/dev/null; then
	printf 'ERROR: formal publishing cannot resolve a draft Release through the tag endpoint\n' >&2
	exit 1
fi
grep -F "needs.publish-release.outputs.deployable == 'true'" "${workflow}" >/dev/null
grep -F 'ubuntu_release: "22.04"' "${workflow}" >/dev/null
grep -F 'asset: ubuntu2204' "${workflow}" >/dev/null

grep -F 'actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38 # v6' "${workflow}" >/dev/null
grep -F 'actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6' "${workflow}" >/dev/null
grep -F 'actions/setup-go@924ae3a1cded613372ab5595356fb5720e22ba16 # v6' "${workflow}" >/dev/null
grep -F 'docker/setup-buildx-action@bb05f3f5519dd87d3ba754cc423b652a5edd6d2c # v4.2.0' \
	"${workflow}" >/dev/null
grep -F 'docker/login-action@dbcb813823bdd20940b903addbd779551569679f # v4.6.0' \
	"${workflow}" >/dev/null
grep -F 'docker/build-push-action@53b7df96c91f9c12dcc8a07bcb9ccacbed38856a # v7.3.0' \
	"${workflow}" >/dev/null
[[ "$(grep -c 'uses: docker/setup-buildx-action@' "${workflow}")" -eq \
	"$(grep -c 'uses: docker/setup-buildx-action@bb05f3f5519dd87d3ba754cc423b652a5edd6d2c # v4.2.0' "${workflow}")" ]]
[[ "$(grep -c 'uses: docker/login-action@' "${workflow}")" -eq \
	"$(grep -c 'uses: docker/login-action@dbcb813823bdd20940b903addbd779551569679f # v4.6.0' "${workflow}")" ]]
[[ "$(grep -c 'uses: docker/build-push-action@' "${workflow}")" -eq \
	"$(grep -c 'uses: docker/build-push-action@53b7df96c91f9c12dcc8a07bcb9ccacbed38856a # v7.3.0' "${workflow}")" ]]

for export_script in \
	"${ROOT}/release/ci/export-hfl-images.sh" \
	"${ROOT}/release/ci/export-runtime-images.sh" \
	"${ROOT}/release/ci/export-sourcelens-bundle.sh"; do
	grep -F 'hfl_docker_start_pull_budget "${HFL_DOCKER_PULL_BUDGET_SECONDS:-480}"' \
		"${export_script}" >/dev/null
	grep -F 'hfl_docker_pull_with_retry' "${export_script}" >/dev/null
	if grep -E '^[[:space:]]*docker pull ' "${export_script}" >/dev/null; then
		printf 'ERROR: release export bypasses bounded Docker pull retry: %s\n' \
			"${export_script}" >&2
		exit 1
	fi
done
grep -F -- '--kill-after="${kill_after_seconds}s"' \
	"${ROOT}/tools/lib/docker-images.sh" >/dev/null

grep -F '_internal-hfl-images.tar' "${workflow}" >/dev/null
if grep -E '_internal-[^[:space:]"'\'']*\.tar\.gz' "${workflow}" >/dev/null; then
	printf 'ERROR: CI-only envelopes must not recompress already-compressed payloads\n' >&2
	exit 1
fi

runtime_pins="${ROOT}/tools/dependencies/versions/runtime-images.env"
for image in POSTGRES_IMAGE REDIS_IMAGE NGINX_IMAGE; do
	grep -E "^${image}=[^[:space:]]+@sha256:[0-9a-f]{64}$" "${runtime_pins}" >/dev/null
done
grep -F 'slcache-${fingerprint}' "${ROOT}/release/ci/build-sourcelens-image.sh" >/dev/null
grep -F 'docker buildx imagetools create --tag "${target_ref}" "${cache_ref}"' \
	"${ROOT}/release/ci/build-sourcelens-image.sh" >/dev/null

if grep -R -n 'docker-buildx-plugin\|BUILDX_PLUGIN_VERSION' \
	"${ROOT}/tools/dependencies" "${ROOT}/deploy/bootstrap" "${ROOT}/release/ci" >/dev/null; then
	printf 'ERROR: runtime Docker bundles must not include the Buildx plugin\n' >&2
	exit 1
fi

grep -F 'configure_macos_dev_shell' "${ROOT}/dev/stack.sh" >/dev/null
grep -F 'verify_amd64_runtime' "${ROOT}/dev/stack.sh" >/dev/null
grep -F 'DOCKER_DEFAULT_PLATFORM="${DOCKER_DEFAULT_PLATFORM:-linux/amd64}"' \
	"${ROOT}/dev/stack.sh" >/dev/null
[[ -x "${ROOT}/dev/bootstrap-macos.sh" && -f "${ROOT}/dev/Brewfile" ]]
deploy_workflow="${ROOT}/.github/workflows/deploy_target.yml"
deploy_ssh_calls="$(grep -c 'ssh -i ~/.ssh/hyperfilelens_deploy' "${deploy_workflow}")"
[[ "$(grep -c -- '-o ServerAliveInterval=30' "${deploy_workflow}")" -eq "${deploy_ssh_calls}" ]] || {
	printf 'ERROR: every deployment SSH call must enable ServerAliveInterval\n' >&2
	exit 1
}
[[ "$(grep -c -- '-o ServerAliveCountMax=20' "${deploy_workflow}")" -eq "${deploy_ssh_calls}" ]] || {
	printf 'ERROR: every deployment SSH call must set ServerAliveCountMax\n' >&2
	exit 1
}
grep -F 'repository: hyperfilelens-backend' "${workflow}" >/dev/null
grep -F 'repository: hyperfilelens-frontend' "${workflow}" >/dev/null
grep -F '"$REGISTRY_PREFIX"' "${workflow}" >/dev/null
grep -F "select(.name | startswith(\"_internal-\") | not)" "${workflow}" >/dev/null
grep -F 'gh release delete-asset' "${workflow}" >/dev/null
grep -F -- '--repo "${GITHUB_REPOSITORY}"' "${workflow}" >/dev/null
grep -F "awk '\$2 ~ /^hyperfilelens-.*\\.tar\\.gz\$/" "${workflow}" >/dev/null
grep -F 'uv run python src/backend/manage.py test' "${workflow}" >/dev/null
grep -F 'uv run --isolated --no-project --python 3.8 python tools/quality/check-python38-runtime.py' \
	"${workflow}" >/dev/null
grep -F 'npm run test:ci' "${workflow}" >/dev/null
grep -F './tools/quality/test-ci-release-assembly.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-installer-image-refresh.sh' "${workflow}" >/dev/null
grep -F "'.register-form-box, .login-form-box'" \
	"${ROOT}/tools/dev/browser-smoke.mjs" >/dev/null
grep -F "'.dashboard-page, .main-content, .platform-ops-main, .login-form-box, .register-form-box'" \
	"${ROOT}/tools/dev/browser-smoke.mjs" >/dev/null
grep -F 'verifyResponsivePlatformPrimaryAction' \
	"${ROOT}/tools/dev/browser-smoke.mjs" >/dev/null
grep -F 'resolveSmokeContract' "${ROOT}/tools/dev/browser-smoke.mjs" >/dev/null
grep -F 'HFL_RELEASE_EDITION' "${ROOT}/tools/dev/browser-smoke.sh" >/dev/null
grep -F 'docker inspect --format' "${ROOT}/tools/dev/browser-smoke.sh" >/dev/null
grep -F 'HFL_EXTENSIONS=' "${ROOT}/tools/dev/browser-smoke.sh" >/dev/null
grep -F 'HFL_RELEASE_EDITION="${smoke_edition}"' \
	"${ROOT}/tools/dev/browser-smoke.sh" >/dev/null
grep -F 'release_edition' "${ROOT}/release/ci/verify-release.sh" >/dev/null
grep -F 'release/ci/certify-agent-candidate.py' "${workflow}" >/dev/null
grep -F '"KOPIA_USE_KEYRING": "false"' "${agent_certification}" >/dev/null
grep -F '"KOPIA_PERSIST_CREDENTIALS_ON_CONNECT": "false"' "${agent_certification}" >/dev/null
# Enrollment preflight treats 403 on /ws/ as reachable; mock must not answer 404.
grep -F 'path.startswith("/ws/")' "${agent_certification}" >/dev/null
grep -F 'self.send_error(403, "authentication required")' "${agent_certification}" >/dev/null
# Installer overhaul (#316) opens an installation session and waits for node-status.
grep -F '/api/v1/node/enrollment/session' "${agent_certification}" >/dev/null
grep -F 'installation_session' "${agent_certification}" >/dev/null
grep -F '/api/v1/node/enrollment/node-status' "${agent_certification}" >/dev/null
grep -F '"routable": True' "${agent_certification}" >/dev/null
# Installer overhaul (#316) opens an installation session and waits for node-status.
grep -F '/api/v1/node/enrollment/session' "${agent_certification}" >/dev/null
grep -F 'installation_session' "${agent_certification}" >/dev/null
grep -F '/api/v1/node/enrollment/node-status' "${agent_certification}" >/dev/null
grep -F '"routable": True' "${agent_certification}" >/dev/null
grep -F 'apt source: official Ubuntu (HTTPS after CA bootstrap)' \
	"${ROOT}/src/agent/scripts/fetch-deps.sh" >/dev/null
grep -F 'using official Ubuntu sources (HTTPS after CA bootstrap)' \
	"${ROOT}/tools/dependencies/fetch-docker-ce-debs.sh" >/dev/null
grep -F 'NAS_DOCKER_TIMEOUT=900' "${ROOT}/src/agent/scripts/fetch-deps.sh" >/dev/null
for dependency_fetcher in \
	"${ROOT}/src/agent/scripts/fetch-deps.sh" \
	"${ROOT}/tools/dependencies/fetch-docker-ce-debs.sh"; do
	grep -F 'Acquire::https::Verify-Peer=false' "${dependency_fetcher}" >/dev/null
	grep -F 'Acquire::https::Verify-Host=false' "${dependency_fetcher}" >/dev/null
	grep -F 'Acquire::Retries=5' "${dependency_fetcher}" >/dev/null
	grep -F 'for attempt in 1 2 3' "${dependency_fetcher}" >/dev/null
	grep -F 'if [[ -z "${apt_mirror_url}" ]]; then' "${dependency_fetcher}" >/dev/null
	grep -F 'Dir::State::status="${baseline_status}"' "${dependency_fetcher}" >/dev/null
done
if grep -E -n 'apt_mirror_http|default Ubuntu HTTP sources' \
	"${ROOT}/src/agent/scripts/fetch-deps.sh" \
	"${ROOT}/tools/dependencies/fetch-docker-ce-debs.sh" >/dev/null; then
	printf 'ERROR: offline dependency fetchers must not force apt mirrors to HTTP\n' >&2
	exit 1
fi
grep -F 'release/ci/verify-agent-certifications.py' "${workflow}" >/dev/null
grep -F 'runner: ubuntu-24.04-arm' "${workflow}" >/dev/null
grep -F 'runner: macos-15-intel' "${workflow}" >/dev/null
grep -F 'runner: macos-15' "${workflow}" >/dev/null
grep -F 'runner: windows-2022' "${workflow}" >/dev/null
[[ "$(grep -c 'APT_MIRROR: \${{ vars.CI_UBUNTU_APT_MIRROR }}' "${workflow}")" -eq 2 ]]
[[ "$(awk '/^  build-host-debs:/{job=1} job && /timeout-minutes:/{print $2; exit}' "${workflow}")" == "60" ]]
grep -F 'bootstrap_tools_ok=0' "${ROOT}/tools/dependencies/fetch-docker-ce-debs.sh" >/dev/null
grep -F 'Dir::Etc::sourcelist=/etc/apt/sources.list.d/docker.list' \
	"${ROOT}/tools/dependencies/fetch-docker-ce-debs.sh" >/dev/null
grep -F -- '--required-target linux:arm64' "${workflow}" >/dev/null
grep -F -- '--required-target darwin:arm64' "${workflow}" >/dev/null
grep -F -- '--required-target windows:amd64' "${workflow}" >/dev/null
if grep -F 'uv run pytest src/backend' "${workflow}" >/dev/null; then
	printf 'ERROR: backend CI must initialize Django through manage.py\n' >&2
	exit 1
fi

worker_healthcheck="$(sed -n '/^  worker:/,/^  scheduler:/p' "${ROOT}/deploy/docker-compose.yml")"
grep -F "/proc/1/cmdline" <<<"${worker_healthcheck}" >/dev/null
grep -F "s.connect((pg_host,pg_port))" <<<"${worker_healthcheck}" >/dev/null
grep -F "s.connect(('redis',6379))" <<<"${worker_healthcheck}" >/dev/null
if grep -F 'celery -A common inspect ping' <<<"${worker_healthcheck}" >/dev/null; then
	printf 'ERROR: worker healthcheck must not start another Django/Celery process\n' >&2
	exit 1
fi

# Production blue/green topology: stable entry owns every host port, while
# scalable API/Web colors remain profile-scoped and container-name agnostic.
release_compose="${ROOT}/deploy/docker-compose.yml"
for service in api-blue api-green web-blue web-green migration; do
	grep -Eq "^  ${service}:" "${release_compose}"
done
grep -F 'profiles: ["blue"]' "${release_compose}" >/dev/null
grep -F 'profiles: ["green"]' "${release_compose}" >/dev/null
grep -F 'stop_grace_period: 600s' "${release_compose}" >/dev/null
grep -F "pgrep -f '[c]elery.* beat'" "${release_compose}" >/dev/null
production_worker_entrypoint="$(sed -n '/^  worker)/,/^  worker-dev)/p' "${ROOT}/deploy/docker/backend-entrypoint.sh")"
if grep -F 'run_migrations_and_register' <<<"${production_worker_entrypoint}" >/dev/null; then
	printf 'ERROR: production worker must not run singleton migrations\n' >&2
	exit 1
fi
if grep -F 'container_name:' "${release_compose}" >/dev/null; then
	printf 'ERROR: scalable release services must not use fixed container_name values\n' >&2
	exit 1
fi
[[ "$(grep -Fc '${HFL_TENANT_PORT:-11443}:11443' "${release_compose}")" -eq 1 ]]
grep -F 'include /etc/nginx/snippets/hfl-active-upstreams.conf;' \
	"${ROOT}/deploy/nginx/default.conf" >/dev/null
grep -F 'resolver 127.0.0.11 valid=10s ipv6=off;' \
	"${ROOT}/deploy/nginx/default.conf" >/dev/null
grep -F 'zone hfl_api_http 64k;' \
	"${ROOT}/deploy/nginx/snippets/hfl-active-upstreams.conf" >/dev/null
grep -F 'server api-blue:8000 resolve;' \
	"${ROOT}/deploy/nginx/snippets/hfl-active-upstreams.conf" >/dev/null
[[ "$(grep -Ec 'server (api-blue|web-blue):[0-9]+ resolve;' \
	"${ROOT}/deploy/nginx/snippets/hfl-active-upstreams.conf")" -eq 5 ]]
[[ "$(grep -Fc 'zone hfl_' \
	"${ROOT}/deploy/nginx/snippets/hfl-active-upstreams.conf")" -eq 5 ]]
for expected in \
	'zone hfl_api_http 64k;' \
	'zone hfl_api_ws 64k;' \
	'zone hfl_web_tenant 64k;' \
	'zone hfl_web_ops 64k;' \
	'zone hfl_website 64k;' \
	'server ${api_service}:8000 resolve;' \
	'server ${api_service}:8001 resolve;' \
	'server web-${web_color}:8080 resolve;' \
	'server web-${web_color}:8081 resolve;' \
	'server web-${web_color}:8082 resolve;'; do
	grep -F "${expected}" "${ROOT}/deploy/installer/install.sh" >/dev/null || {
		printf 'ERROR: release upstream renderer is missing: %s\n' "${expected}" >&2
		exit 1
	}
done
grep -F 'location ~ ^/api/v1/lens/copilot/sessions/[0-9]+/attachments/?$ {' \
	"${ROOT}/deploy/nginx/snippets/hfl-tenant-locations.conf" >/dev/null
grep -F 'client_max_body_size 26m;' \
	"${ROOT}/deploy/nginx/snippets/hfl-tenant-locations.conf" >/dev/null
grep -F 'cmd_manage()' "${ROOT}/deploy/installer/install.sh" >/dev/null
if grep -F 'docker compose exec -T api python manage.py' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null; then
	printf 'ERROR: deployment workflow must use the active-color management command entrypoint\n' >&2
	exit 1
fi
grep -F '/opt/hyperfilelens/install.sh manage ensure_platform_ai_model' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'deploy/nginx/web.conf' "${ROOT}/deploy/installer/install.sh" >/dev/null
grep -F 'deploy/blue-green/active-color' "${ROOT}/release/build.sh" >/dev/null
grep -F 'deploy/blue-green/active-color' "${ROOT}/release/ci/assemble-release.sh" >/dev/null
grep -F 'release package missing blue/green initial state' "${ROOT}/release/build.sh" >/dev/null
sourcelens_installed_body="$(
	sed -n '/^sourcelens_installed()/,/^}/p' "${ROOT}/deploy/installer/install.sh"
)"
grep -F 'SOURCELENS_INSTALL_DIR}/docker-compose.yml' <<<"${sourcelens_installed_body}" >/dev/null
grep -F 'SOURCELENS_INSTALL_DIR}/.env' <<<"${sourcelens_installed_body}" >/dev/null
grep -F 'api:8000' "${ROOT}/deploy/nginx/development-upstreams.conf" >/dev/null
grep -F 'ws_recovery_gate drain' "${ROOT}/deploy/installer/install.sh" >/dev/null
grep -F 'args=(reattach --timeout' "${ROOT}/deploy/installer/install.sh" >/dev/null
grep -F 'cutover_hfl_color' "${ROOT}/deploy/installer/install.sh" >/dev/null
rollback_body="$(sed -n '/^restore_previous_hfl_color()/,/^begin_sourcelens_maintenance_gate()/p' "${ROOT}/deploy/installer/install.sh")"
grep -F 'drain_api_color "${target}"' <<<"${rollback_body}" >/dev/null
grep -F 'wait_for_active_task_reattach "${previous}"' <<<"${rollback_body}" >/dev/null
grep -F 'remove_retired_color "${target}"' <<<"${rollback_body}" >/dev/null
upgrade_body="$(sed -n '/^cmd_upgrade()/,/^main()/p' "${ROOT}/deploy/installer/install.sh")"
if grep -F 'compose_in_root down' <<<"${upgrade_body}" >/dev/null; then
	printf 'ERROR: blue/green upgrade must not stop the complete HFL stack\n' >&2
	exit 1
fi
worker_stop_line="$(grep -n -F 'compose_in_root stop --timeout 600 worker' <<<"${upgrade_body}" | head -1 | cut -d: -f1)"
worker_stopped_line="$(grep -n -F 'compose_in_root ps --status running -q worker' <<<"${upgrade_body}" | head -1 | cut -d: -f1)"
migration_line="$(grep -n -F 'compose_in_root --profile tools run --rm --no-deps migration' <<<"${upgrade_body}" | head -1 | cut -d: -f1)"
if [[ -z "${worker_stop_line}" \
	|| -z "${worker_stopped_line}" \
	|| -z "${migration_line}" \
	|| "${worker_stop_line}" -ge "${worker_stopped_line}" \
	|| "${worker_stopped_line}" -ge "${migration_line}" ]]; then
	printf 'ERROR: upgrade must stop and verify the old worker before applying task-state migrations\n' >&2
	exit 1
fi
if grep -F 'run --rm --no-deps --pull never migration' \
	"${ROOT}/deploy/installer/install.sh" >/dev/null; then
	printf 'ERROR: migration must not use the Docker Compose v2.27-incompatible run --pull flag\n' >&2
	exit 1
fi
release_backend_image_block="$(sed -n '/^x-backend-image:/,/^x-backend-volumes:/p' "${release_compose}")"
grep -F 'pull_policy: never' <<<"${release_backend_image_block}" >/dev/null || {
	printf 'ERROR: release backend services must remain offline through pull_policy: never\n' >&2
	exit 1
}
if sed -n "${worker_stop_line}p" <<<"${upgrade_body}" | grep -F '|| true' >/dev/null; then
	printf 'ERROR: upgrade must fail closed when the old worker cannot be stopped\n' >&2
	exit 1
fi
worker_verify_body="$(sed -n "${worker_stop_line},${migration_line}p" <<<"${upgrade_body}")"
if grep -F 'compose_in_root ps --status running -q worker 2>/dev/null || true' \
	<<<"${worker_verify_body}" >/dev/null; then
	printf 'ERROR: upgrade must fail closed when old-worker verification fails\n' >&2
	exit 1
fi
recovery_body="$(sed -n '/^recover_upgrade_services()/,/^print_config()/p' "${ROOT}/deploy/installer/install.sh")"
if grep -E 'compose_color .* up .*api-' <<<"${recovery_body}" >/dev/null; then
	printf 'ERROR: recovery must not recreate the previous color from target image metadata\n' >&2
	exit 1
fi
grep -F 'compose_color "${recovery_color}" start' <<<"${recovery_body}" >/dev/null
grep -F 'compose_in_root start worker scheduler' <<<"${recovery_body}" >/dev/null
grep -F './tools/quality/test-blue-green-recovery.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-compose-lifecycle-reconcile.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-nginx-startup-readiness.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-sourcelens-runtime-sync.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-sourcelens-runtime-fingerprint.sh' "${workflow}" >/dev/null

backend_dockerfile="${ROOT}/deploy/docker/backend.Dockerfile"
frontend_dockerfile="${ROOT}/deploy/docker/frontend.Dockerfile"
grep -F 'ARG KOPIA_BINARY=build/kopia/dist/linux/amd64/kopia' "${backend_dockerfile}" >/dev/null
grep -F 'COPY --chmod=0755 ${KOPIA_BINARY} /usr/local/bin/kopia' "${backend_dockerfile}" >/dev/null
grep -F '/etc/apt/sources.list.d/ubuntu.sources' "${backend_dockerfile}" >/dev/null
grep -F 'uv export --quiet --locked --no-dev --no-emit-project --output-file /tmp/runtime-requirements.txt' \
	"${backend_dockerfile}" >/dev/null
grep -F -- '--require-hashes -r /tmp/runtime-requirements.txt' "${backend_dockerfile}" >/dev/null
if grep -F 'UV_DEFAULT_INDEX' "${backend_dockerfile}" >/dev/null; then
	printf 'ERROR: backend build must not bind the official uv.lock to a download mirror\n' >&2
	exit 1
fi
if grep -F 'PIP_NO_CACHE_DIR' "${backend_dockerfile}" >/dev/null; then
	printf 'ERROR: backend build must not disable its BuildKit-managed pip cache\n' >&2
	exit 1
fi
grep -F 'org.opencontainers.image.revision="${IMAGE_REVISION}"' "${backend_dockerfile}" >/dev/null
grep -F 'org.opencontainers.image.revision="${IMAGE_REVISION}"' "${frontend_dockerfile}" >/dev/null
grep -F 'IMAGE_REVISION=${{ needs.prepare.outputs.commit }}' "${workflow}" >/dev/null

installer="${ROOT}/deploy/installer/install.sh"
grep -F 'Loading container image archive' "${installer}" >/dev/null
grep -F 'org.opencontainers.image.revision' "${installer}" >/dev/null
grep -F 'does not match release' "${installer}" >/dev/null
if grep -F 'image already loaded' "${installer}" >/dev/null; then
	printf 'ERROR: installer must refresh verified release images even when tags already exist\n' >&2
	exit 1
fi

sourcelens_image_builder="${ROOT}/release/ci/build-sourcelens-image.sh"
grep -F 'SOURCELENS_HFL_VERSION="${hfl_version}"' \
	"${sourcelens_image_builder}" >/dev/null
grep -F 'target_ref="${registry_prefix}/hyperfilelens-sourcelens-${component}:${SOURCELENS_DISTRIBUTION_TAG}"' \
	"${sourcelens_image_builder}" >/dev/null
grep -F 'registry prefix must include host and namespace' \
	"${sourcelens_image_builder}" >/dev/null

agent_publisher="${ROOT}/tools/agent/publish.sh"
if grep -R -n --include='*.go' '"hyperfilelens/agent/internal/remote"' \
	"${ROOT}/src/agent/internal/enroll" >/dev/null; then
	printf 'ERROR: first-stage enrollment code must use the lightweight enrollment client\n' >&2
	exit 1
fi
grep -F 'all | standard | ubuntu2004 | ubuntu2204 | ubuntu2404' "${agent_publisher}" >/dev/null
grep -F 'for ubuntu_flavor in ubuntu2004 ubuntu2204 ubuntu2404' "${agent_publisher}" >/dev/null
grep -F 'build/dependencies/docker/ubuntu-${ubuntu_release}/amd64' "${agent_publisher}" >/dev/null
grep -F 'Publishing compressed minimal installers' "${agent_publisher}" >/dev/null
grep -F 'minimal installer exceeds 3.5 MiB' "${agent_publisher}" >/dev/null
grep -F 'minimal installer matrix mismatch' "${ROOT}/release/ci/assemble-release.sh" >/dev/null
grep -F 'minimal installer exceeds 3.5 MiB' "${ROOT}/release/ci/assemble-release.sh" >/dev/null
grep -F 'minimal installer checksum mismatch' "${ROOT}/release/ci/verify-release.sh" >/dev/null
grep -F 'minimal installer exceeds 3.5 MiB' "${ROOT}/release/ci/verify-release.sh" >/dev/null
grep -F 'bundled language-pack checksum mismatch' "${ROOT}/release/ci/verify-release.sh" >/dev/null
grep -F 'verify_installed_language_packs' "${ROOT}/release/ci/verify-release.sh" >/dev/null

agent_bootstrap_linux="${ROOT}/deploy/bootstrap/agent-bootstrap-linux.sh"
agent_bootstrap_macos="${ROOT}/deploy/bootstrap/agent-bootstrap-macos.sh"
agent_bootstrap_windows="${ROOT}/deploy/bootstrap/agent-bootstrap-windows.ps1"
gateway_bootstrap_linux="${ROOT}/deploy/bootstrap/gateway-bootstrap-linux.sh"
gateway_docker_installer="${ROOT}/deploy/bootstrap/gateway-install-docker-ubuntu-amd64.sh"
gateway_lifecycle="${ROOT}/deploy/bootstrap/gateway-lifecycle.sh"
gateway_sidecar_installer="${ROOT}/deploy/bootstrap/gateway-install-lensnode-sidecar.sh"
agent_bundle_installer="${ROOT}/src/agent/packaging/install/install.sh"
agent_windows_installer="${ROOT}/src/agent/packaging/install/install.ps1"
agent_output="${ROOT}/src/agent/internal/enroll/output.go"
agent_gateway_lifecycle="${ROOT}/src/agent/internal/enroll/gateway_lifecycle.go"
grep -F 'requires a systemd-based Linux distribution' "${agent_bootstrap_linux}" >/dev/null
grep -F 'systemctl show-environment' "${agent_bootstrap_linux}" >/dev/null
grep -F 'launchd is required to install the agent service on macOS' "${agent_bootstrap_macos}" >/dev/null
grep -F 'Windows ARM64 is not supported by this release' "${agent_bootstrap_windows}" >/dev/null
for output_source in "${agent_bundle_installer}" "${agent_windows_installer}" "${agent_output}"; do
	grep -F '|_| |_|\__, | .__/ \___|_|  |_|   |_|_|\___|_____\___|_| |_|___/' \
		"${output_source}" >/dev/null
done
for phase in 'Preflight checks' 'Installing Agent' 'Verifying' 'Installation summary'; do
	grep -F "${phase}" "${agent_bundle_installer}" >/dev/null
done
for phase in 'Target' 'Upgrading Agent' 'Uninstalling' 'Upgrade summary' 'Uninstallation summary'; do
	grep -F "${phase}" "${agent_bundle_installer}" >/dev/null
	grep -F "${phase}" "${agent_windows_installer}" >/dev/null
done
grep -F 'hfl_detail_log_stream' "${agent_bundle_installer}" >/dev/null
grep -F "printf '[%s] [DETAIL] %s\\n'" "${agent_bundle_installer}" >/dev/null
grep -F 'Write-HflDisplayLine' "${agent_windows_installer}" >/dev/null
grep -F 'Installation failed: $($_.Exception.Message)' "${agent_windows_installer}" >/dev/null
grep -F 'Upgrade failed: $($_.Exception.Message)' "${agent_windows_installer}" >/dev/null
grep -F 'Uninstallation failed: $($_.Exception.Message)' "${agent_windows_installer}" >/dev/null
grep -F "if ((-not \$QuietFooter) -or (\$Level -eq 'FAIL '))" \
	"${agent_windows_installer}" >/dev/null
grep -F 'printLifecycleBanner(gatewayName, "Upgrade")' \
	"${agent_gateway_lifecycle}" >/dev/null
grep -F 'printGatewayUpgradeSuccess(gatewayName, version, service)' \
	"${agent_gateway_lifecycle}" >/dev/null
grep -F 'printUninstallSuccess(state, purgeAll)' \
	"${agent_gateway_lifecycle}" >/dev/null
if grep -E 'Write-HflInstallLogLine "(Success|  )' "${agent_windows_installer}" >/dev/null; then
	printf 'ERROR: Windows Agent lifecycle output must use the timestamping display logger\n' >&2
	exit 1
fi
for bootstrap in "${agent_bootstrap_linux}" "${agent_bootstrap_macos}"; do
	grep -F -- '--fail --silent --show-error --location' "${bootstrap}" >/dev/null
	grep -F 'curl --retry-connrefused --version' "${bootstrap}" >/dev/null
	grep -F -- '--retry 3 ${retry_connrefused[@]+"${retry_connrefused[@]}"} --retry-delay 2' "${bootstrap}" >/dev/null
	grep -F 'HyperFileLens enrollment helper' "${bootstrap}" >/dev/null
	grep -F 'partial="${destination}.part"' "${bootstrap}" >/dev/null
done
for bootstrap in "${gateway_bootstrap_linux}" "${gateway_docker_installer}"; do
	grep -F -- '--fail --silent --show-error --location' "${bootstrap}" >/dev/null
	grep -F 'curl --retry-connrefused --version' "${bootstrap}" >/dev/null
	grep -F -- '--retry 3 ${retry_connrefused[@]+"${retry_connrefused[@]}"} --retry-delay 2' "${bootstrap}" >/dev/null
	grep -F 'partial="${destination}.part"' "${bootstrap}" >/dev/null
done
# CentOS 7 / Bash < 4.4: empty CURL_TLS + set -u requires ${arr[@]+"${arr[@]}"} (not "${arr[@]}").
for bootstrap in \
	"${agent_bootstrap_linux}" \
	"${agent_bootstrap_macos}" \
	"${gateway_bootstrap_linux}" \
	"${gateway_docker_installer}" \
	"${gateway_sidecar_installer}"; do
	safe_count="$(grep -cF '${CURL_TLS[@]+"${CURL_TLS[@]}"}' "${bootstrap}" || true)"
	quoted_count="$(grep -oE '"\$\{CURL_TLS\[@\]\}"' "${bootstrap}" | wc -l | tr -d ' ')"
	if [[ "${safe_count}" -lt 1 || "${safe_count}" -ne "${quoted_count}" ]]; then
		printf 'ERROR: %s must expand CURL_TLS via \${CURL_TLS[@]+\"\${CURL_TLS[@]}\"} only (safe=%s quoted=%s)\n' \
			"${bootstrap}" "${safe_count}" "${quoted_count}" >&2
		exit 1
	fi
done
# Quality must run the Bash 4.2 probe (not host-smoke-only).
# Require an active run line; a commented-out copy must not satisfy this gate.
if ! grep -E '^[[:space:]]+HFL_TEST_BASH42=1 \./tools/quality/test-bootstrap-curl-tls-nounset\.sh[[:space:]]*$' \
	"${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null; then
	printf 'ERROR: release_pipeline Quality must run HFL_TEST_BASH42=1 ./tools/quality/test-bootstrap-curl-tls-nounset.sh\n' >&2
	exit 1
fi

grep -F 'HyperFileLens enrollment helper' "${gateway_bootstrap_linux}" >/dev/null
grep -F 'gateway-install' "${gateway_bootstrap_linux}" >/dev/null
grep -F 'requires a systemd-based Linux distribution' "${gateway_bootstrap_linux}" >/dev/null
# Gateway bootstrap must stay lightweight: no console/SourceLens probes and no Docker
# install before downloading the enrollment helper (matches Agent bootstrap staging).
if grep -E 'Checking console connectivity|Checking SourceLens health|hfl_sourcelens_health_retry|ensure_docker_for_gateway|Installing Docker CE from console offline bundle' \
	"${gateway_bootstrap_linux}" >/dev/null; then
	printf 'ERROR: gateway bootstrap must not probe console/SourceLens or install Docker before enrollment preflight\n' >&2
	exit 1
fi
grep -F 'ensureGatewayDocker' \
	"${ROOT}/src/agent/internal/enroll/gateway_install.go" >/dev/null
grep -F 'checkSourceLensHealthViaConsole' \
	"${ROOT}/src/agent/internal/enroll/sidecar_install_unix.go" >/dev/null
grep -F 'isPublicGatewayScope' \
	"${ROOT}/src/agent/internal/enroll/download_progress.go" >/dev/null
grep -F -- '--fail --silent --show-error --location' "${gateway_lifecycle}" >/dev/null
grep -F -- '--continue-at -' "${gateway_lifecycle}" >/dev/null
grep -F 'HFL_GATEWAY_DOWNLOAD_MAX_ATTEMPTS:-5' "${gateway_lifecycle}" >/dev/null
grep -F 'partial="${2}.part"' "${gateway_lifecycle}" >/dev/null
grep -F 'HFL_SOURCELENS_STATE_ROOT="${HFL_GATEWAY_STATE_ROOT}/sourcelens"' \
	"${gateway_sidecar_installer}" >/dev/null
grep -F 'HFL_SOURCELENS_MOUNTPOINT="${HFL_WORKSPACE_ROOT}/.sourcelens"' \
	"${gateway_sidecar_installer}" >/dev/null
grep -F '${HFL_SOURCELENS_STATE_ROOT}:${HFL_SOURCELENS_MOUNTPOINT}:rw' \
	"${gateway_sidecar_installer}" >/dev/null
grep -F 'LENSNODE_CHECKPOINT_DIR: ${HFL_SOURCELENS_MOUNTPOINT}/checkpoints' \
	"${gateway_sidecar_installer}" >/dev/null
# Conversion writes "*.sourcelens" beside sources; workspace cannot stay :ro.
grep -F '${HFL_WORKSPACE_ROOT}:${HFL_WORKSPACE_ROOT}:rw' \
	"${gateway_sidecar_installer}" >/dev/null
if grep -F '${HFL_WORKSPACE_ROOT}:${HFL_WORKSPACE_ROOT}:ro' \
	"${gateway_sidecar_installer}" >/dev/null; then
	printf 'ERROR: gateway LensNode workspace mount must be :rw for document conversion\n' >&2
	exit 1
fi
grep -F 'script="${INSTALL_SH%/install.sh}/libexec/gateway-lifecycle.sh"' \
	"${ROOT}/src/agent/internal/platform/install/gateway_hooks_unix.go" >/dev/null
grep -F 'Docker CE offline bundle' "${gateway_docker_installer}" >/dev/null
grep -F -- "'--silent'" "${agent_bootstrap_windows}" >/dev/null
if grep -F -- '--progress-bar' "${agent_bootstrap_windows}" >/dev/null; then
	printf 'ERROR: Windows bootstrap must not expose curl native progress output\n' >&2
	exit 1
fi
grep -F 'Write-HflDownloadProgress' "${agent_bootstrap_windows}" >/dev/null
grep -F 'Download size mismatch' "${agent_bootstrap_windows}" >/dev/null
if grep -F 'hfl-enroll-windows-$archRel.exe' "${agent_bootstrap_windows}" >/dev/null \
	&& ! grep -F '"ARM64" {' "${agent_bootstrap_windows}" >/dev/null; then
	printf 'ERROR: Windows bootstrap may request an unsupported ARM64 enrollment binary\n' >&2
	exit 1
fi

remote_deploy="${ROOT}/.github/scripts/remote-deploy.sh"
[[ -x "${remote_deploy}" ]] || {
	printf 'ERROR: remote deployment script is missing or not executable\n' >&2
	exit 1
}
grep -F 'browser_download_url' "${remote_deploy}" >/dev/null
grep -F -- '--progress-bar' "${remote_deploy}" >/dev/null
grep -F 'partial="${output}.part"' "${remote_deploy}" >/dev/null
grep -F 'bash "${package_root}/install.sh" "${install_args[@]}"' \
	"${remote_deploy}" >/dev/null
if grep -F 'install.sh" platform-gateway ensure' "${remote_deploy}" >/dev/null; then
	printf 'ERROR: remote deployment must not repeat installer-owned Gateway ensure\n' >&2
	exit 1
fi
grep -F -- '--public-url) PUBLIC_URL=' "${remote_deploy}" >/dev/null
grep -F -- '--admin-public-url) ADMIN_PUBLIC_URL=' "${remote_deploy}" >/dev/null
grep -F -- '--direct-host) DIRECT_HOST=' "${remote_deploy}" >/dev/null
grep -F -- '--runtime-env-file "${RUNTIME_ENV_FILE}"' "${remote_deploy}" >/dev/null
grep -F 'Download and Install Release' "${deploy_workflow}" >/dev/null
grep -F 'download_proxy_args=(--download-proxy-url "$RELEASE_DOWNLOAD_PROXY_URL")' \
	"${deploy_workflow}" >/dev/null
grep -F '"${download_proxy_args[@]}"' "${deploy_workflow}" >/dev/null
grep -F -- '--download-proxy-url) DOWNLOAD_PROXY_URL=' "${remote_deploy}" >/dev/null
grep -F 'Target-side Release download proxy is enabled' "${remote_deploy}" >/dev/null
grep -F 'retrying directly' "${remote_deploy}" >/dev/null
grep -F -- '--proxy "${DOWNLOAD_PROXY_URL}"' "${remote_deploy}" >/dev/null
grep -F 'DOWNLOAD_PROXY_URL}" == "UNCONFIGURED"' "${remote_deploy}" >/dev/null
[[ "$(grep -c 'RELEASE_DOWNLOAD_PROXY_URL.*!=.*UNCONFIGURED' "${deploy_workflow}")" -eq 2 ]] || {
	printf 'ERROR: UNCONFIGURED Release proxy placeholders must select direct target downloads\n' >&2
	exit 1
}
if grep -E 'gh release download|(^|[[:space:]])scp([[:space:]]|$)|staged-assets-dir|STAGED_ASSETS_DIR' \
	"${deploy_workflow}" "${remote_deploy}" >/dev/null; then
	printf 'ERROR: deployment must download the complete Release package on the target host\n' >&2
	exit 1
fi
if grep -F -- '--force-recreate' "${remote_deploy}" >/dev/null; then
	printf 'ERROR: production deployment must apply runtime configuration before startup\n' >&2
	exit 1
fi
if grep -E 'docker (pull|compose pull)' "${remote_deploy}" >/dev/null; then
	printf 'ERROR: production deployment must consume the complete Release package\n' >&2
	exit 1
fi
for incompatible in removeprefix retry-all-errors; do
	if grep -F "${incompatible}" "${remote_deploy}" >/dev/null; then
		printf 'ERROR: remote deployment uses Ubuntu 20.04-incompatible feature: %s\n' "${incompatible}" >&2
		exit 1
	fi
done
grep -F 'Verified that unrelated Docker containers, networks, and volumes are unchanged' "${remote_deploy}" >/dev/null
grep -F 'project in {"hyperfilelens-sourcelens", "sourcelens"}' "${remote_deploy}" >/dev/null
grep -F './tools/quality/test-shared-host-guard.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-release-download-proxy.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-default-certificates.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-gh-release-upload-retry.sh' "${workflow}" >/dev/null
grep -F './release/ci/gh-release-upload.sh' "${workflow}" >/dev/null
grep -F 'HFL_RELEASE_UPLOAD_ATTEMPTS:-5' \
	"${ROOT}/release/ci/gh-release-upload.sh" >/dev/null
grep -F 'HFL_RELEASE_UPLOAD_DELAY_S:-3' \
	"${ROOT}/release/ci/gh-release-upload.sh" >/dev/null
if grep -E '(^|[[:space:]])gh release upload[[:space:]]' "${workflow}" >/dev/null; then
	printf 'ERROR: artifact pipeline must upload Release assets through gh-release-upload.sh\n' >&2
	exit 1
fi
grep -F './tools/quality/test-gateway-bootstrap-health.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-gateway-lifecycle-upgrade.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-platform-gateway-auto-deploy.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-agent-release-retention.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-agent-gateway-uninstall.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-agent-installer-output.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-lifecycle-output-contract.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-release-purge-all.sh' "${workflow}" >/dev/null
grep -F 'HFL_PLATFORM_GATEWAY_AUTO_DEPLOY=true' "${ROOT}/.env.example" >/dev/null
grep -F 'com.hyperfilelens.component: "gateway-lensnode"' \
	"${gateway_sidecar_installer}" >/dev/null
grep -F './tools/quality/test-deployment-optional-config.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-ga-runtime-config.sh' "${workflow}" >/dev/null
grep -F './tools/quality/test-sentry-runtime-config.sh' "${workflow}" >/dev/null
grep -F 'hfl-sentry-sitecustomize.py' \
	"${ROOT}/deploy/installer/sourcelens/docker-compose.template.yml" >/dev/null
grep -F './tools/quality/test-payload-tree-hash.sh' "${workflow}" >/dev/null
grep -F 'Verify Internal Health' "${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'Ensure Platform Gateway' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'platform-gateway ensure' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'Verify Platform Gateway Readiness' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'platform-gateway verify --required --timeout 180' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
platform_gateway_ensure_line="$(grep -nF 'platform-gateway ensure' \
	"${ROOT}/.github/workflows/deploy_target.yml" | head -1 | cut -d: -f1)"
platform_gateway_verify_line="$(grep -nF 'platform-gateway verify --required --timeout 180' \
	"${ROOT}/.github/workflows/deploy_target.yml" | head -1 | cut -d: -f1)"
[[ "${platform_gateway_ensure_line}" -lt "${platform_gateway_verify_line}" ]] || {
	printf 'ERROR: deployment must ensure the Platform Gateway before readiness verification\n' >&2
	exit 1
}
grep -F 'https://127.0.0.1:11443/health/ready' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'https://127.0.0.1:11442/en/' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'run: ./.github/scripts/check-public-endpoint.sh' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'address.is_global' \
	"${ROOT}/.github/scripts/check-public-endpoint.sh" >/dev/null
grep -F '::warning title=Public endpoint check::Public endpoint is not ready' \
	"${ROOT}/.github/scripts/check-public-endpoint.sh" >/dev/null
if grep -F 'APP_PUBLIC_HOST' "${ROOT}/.github/workflows/deploy_target.yml" >/dev/null; then
	printf 'ERROR: deployment checks must not append internal ports to a public hostname\n' >&2
	exit 1
fi

installer="${ROOT}/deploy/installer/install.sh"
grep -F 'platform-gateway ensure' "${installer}" >/dev/null
grep -F 'platform-gateway verify' "${installer}" >/dev/null
materialize_body="$(sed -n '/^materialize_to_install_dir()/,/^}/p' "${installer}")"
grep -F -- '--checksum' <<<"${materialize_body}" >/dev/null
grep -F -- '--delete' <<<"${materialize_body}" >/dev/null
if grep -F -- '--delete-excluded' <<<"${materialize_body}" >/dev/null; then
	printf 'ERROR: release materialization must preserve excluded runtime state\n' >&2
	exit 1
fi
grep -F 'PUBLIC_HOST="${HFL_PUBLIC_HOST:-}"' "${installer}" >/dev/null
grep -F 'values are hidden in non-interactive logs' "${installer}" >/dev/null
grep -F 'validate_default_tls_bundle "${src_root}/deploy/nginx/certs"' "${installer}" >/dev/null
grep -F 'sync_default_tls_bundle "${from_root}/deploy/nginx/certs"' "${installer}" >/dev/null
grep -F 'Preserving existing TLS certificate directory' "${installer}" >/dev/null
grep -F 'apply_upgrade_files "${src_root}" "${remove_sourcelens}" "${upgrade_sourcelens}"' "${installer}" >/dev/null
grep -F 'apply_runtime_configuration' "${installer}" >/dev/null
backup_body="$(sed -n '/^backup_postgresql_dump()/,/^}/p' "${installer}")"
grep -F 'COMPOSE=("${HFL_COMPOSE[@]}")' <<<"${backup_body}" >/dev/null
grep -F 'a complete managed backup cannot be created' <<<"${backup_body}" >/dev/null
[[ "$(grep -Fc 'sourcelens_compose exec -T postgres' <<<"${backup_body}")" -eq 2 ]]
if grep -E 'sourcelens_compose (ps -q|exec -T) postgresql' <<<"${backup_body}" >/dev/null; then
	printf 'ERROR: bundled SourceLens PostgreSQL Compose service is named postgres\n' >&2
	exit 1
fi
file_backup_body="$(sed -n '/^backup_env_and_data()/,/^}/p' "${installer}")"
grep -F -- "--exclude='data/postgresql'" <<<"${file_backup_body}" >/dev/null
grep -F -- "--exclude='data/sourcelens/postgresql'" <<<"${file_backup_body}" >/dev/null
grep -F 'prune_upgrade_backups' "${installer}" >/dev/null
grep -F 'create_managed_backup' "${installer}" >/dev/null
grep -F 'backup-manifest.json' "${installer}" >/dev/null
grep -F 'sorted(groups, reverse=True)[3:]' "${installer}" >/dev/null
grep -F 'preflight_redis_recovery' "${installer}" >/dev/null
grep -F 'HFL_REDIS_MEMORY_LIMIT=${configured_limit} is invalid' "${installer}" >/dev/null
grep -F 'exceeding the configured ${limit_mib} MiB container limit' "${installer}" >/dev/null
grep -F 'HFL_REDIS_MEMORY_LIMIT=1g' "${ROOT}/.env.example" >/dev/null
grep -F 'mem_limit: ${HFL_REDIS_MEMORY_LIMIT:-1g}' "${ROOT}/deploy/docker-compose.yml" >/dev/null
grep -F 'recover_upgrade_services' "${installer}" >/dev/null
grep -F 'prune_old_managed_image_refs' "${installer}" >/dev/null
grep -F 'docker image rm -f "${image_id}"' "${installer}" >/dev/null
grep -F 'protected_ids.update(image_id(line.strip()) for line in inspected.stdout.splitlines()' "${installer}" >/dev/null
grep -F 'protected release manifest is invalid' "${installer}" >/dev/null
grep -F 'python3 "${sync_script}" --env-file "${env_file}" --example "${example}"' "${installer}" >/dev/null
grep -F 'host must be Ubuntu 20.04, 22.04, or 24.04' "${installer}" >/dev/null
grep -F 'gateway-install-docker-ubuntu-amd64.sh' "${installer}" >/dev/null
grep -F 'docker-debs-ubuntu2004-amd64.tar.gz' "${installer}" >/dev/null
grep -F 'docker-debs-ubuntu2204-amd64.tar.gz' "${installer}" >/dev/null
grep -F 'docker-debs-ubuntu2404-amd64.tar.gz' "${installer}" >/dev/null
if grep -E 'tomllib|extractall\([^)]*filter=' "${installer}" >/dev/null; then
	printf 'ERROR: installer contains Python APIs unavailable on Ubuntu 20.04\n' >&2
	exit 1
fi

grep -F 'verify-host-debs-asset.sh' \
	"${workflow}" >/dev/null
grep -F 'verify-ubuntu-agent-bundle.sh' \
	"${workflow}" >/dev/null
grep -F 'Offline NAS dependency install pass ${attempt}/3' \
	"${ROOT}/release/ci/verify-ubuntu-agent-bundle.sh" >/dev/null
grep -F 'Offline NAS dependency install pass ${attempt}/3' \
	"${ROOT}/src/agent/packaging/install/install.sh" >/dev/null
for verification_script in \
	"${ROOT}/release/ci/verify-host-debs-asset.sh" \
	"${ROOT}/release/ci/verify-ubuntu-agent-bundle.sh"; do
	[[ -x "${verification_script}" ]] || {
		printf 'ERROR: Ubuntu verification script is not executable: %s\n' "${verification_script}" >&2
		exit 1
	}
done

if grep -ER 'uses:[[:space:]]+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@' \
	"${ROOT}/.github/workflows" \
	| grep -Ev '@[0-9a-f]{40}([[:space:]]|$)' >/dev/null; then
	printf 'ERROR: external GitHub Actions must be pinned to full commit SHAs\n' >&2
	exit 1
fi

release_verifier="${ROOT}/release/ci/verify-release.sh"
grep -F 'smoke_host="${SMOKE_HOST:-host.docker.internal}"' "${release_verifier}" >/dev/null
grep -F 'HFL_PUBLIC_HOST="${smoke_host}"' "${release_verifier}" >/dev/null
grep -F 'export SMOKE_HOST="${smoke_host}"' "${release_verifier}" >/dev/null
grep -F 'SEED_ADMIN_EMAIL="$(sudo sed' "${release_verifier}" >/dev/null
grep -F 'upgrade --from "${pkg_root}" --yes' "${release_verifier}" >/dev/null
grep -F 'Full release install, upgrade, and login verification passed' "${release_verifier}" >/dev/null
grep -F 'sudo env \' "${release_verifier}" >/dev/null
grep -F 'cp "${ROOT}/tools/config/sync_env.py" "${pkg_root}/sync-env.py"' \
	"${ROOT}/release/ci/assemble-release.sh" >/dev/null
grep -F 'cp "${ROOT}/tools/config/sync_env.py" "${pkg_root}/sync-env.py"' \
	"${ROOT}/release/build.sh" >/dev/null
grep -F 'cp "${ROOT}/deploy/installer/compose-runtime.sh" "${pkg_root}/payload/runtime/compose-runtime.sh"' \
	"${ROOT}/release/ci/assemble-release.sh" "${ROOT}/release/ci/assemble-saas-candidate.sh" \
	"${ROOT}/release/build.sh" >/dev/null
grep -F '"deploy/installer/compose-runtime.sh": "payload/runtime/compose-runtime.sh"' \
	"${ROOT}/deploy/online/prepare.py" >/dev/null
grep -F 'source "${COMPOSE_RUNTIME_FILE}"' \
	"${ROOT}/deploy/installer/install.sh" "${ROOT}/deploy/installer/sourcelens/install.sh" >/dev/null
grep -F './tools/quality/test-compose-command-compatibility.sh' \
	"${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null
grep -F 'cp "${ROOT}/deploy/installer/apply-runtime-config.py" "${pkg_root}/apply-runtime-config.py"' \
	"${ROOT}/release/ci/assemble-release.sh" >/dev/null
grep -F 'stage_default_tls_bundle "${pkg_root}"' \
	"${ROOT}/release/ci/assemble-release.sh" >/dev/null
grep -F 'hyperfilelens-root-ca.crt' "${workflow}" >/dev/null
fingerprint_body="$(sed -n '/^sourcelens_bundle_fingerprint()/,/^}/p' "${installer}")"
grep -F 'BUILD_INFO.identity' <<<"${fingerprint_body}" >/dev/null
if grep -F 'upstream_ref' <<<"${fingerprint_body}" >/dev/null; then
	printf 'ERROR: SourceLens fingerprint must ignore per-release registry transit refs\n' >&2
	exit 1
fi

for executable in \
	"${ROOT}/.github/scripts/check-main-freshness.sh" \
	"${ROOT}/.github/scripts/check-public-endpoint.sh" \
	"${ROOT}/.github/scripts/cleanup-main-builds.sh" \
	"${ROOT}/.github/scripts/promote-enterprise-release.sh" \
	"${ROOT}/.github/scripts/stop-enterprise-promotion.sh" \
	"${ROOT}/.github/scripts/remote-deploy.sh" \
	"${ROOT}/.github/scripts/store-enterprise-release.sh" \
	"${ROOT}/deploy/installer/sourcelens/compose-lifecycle.sh" \
	"${ROOT}/tools/quality/check-python38-runtime.py" \
	"${ROOT}/tools/quality/test-docker-pull-retry.sh" \
	"${ROOT}/tools/quality/test-compose-lifecycle-reconcile.sh" \
	"${ROOT}/tools/quality/test-bootstrap-curl-tls-nounset.sh" \
	"${ROOT}/tools/quality/test-gh-release-upload-retry.sh" \
	"${ROOT}/release/ci/gh-release-upload.sh" \
	"${ROOT}/tools/quality/test-main-release-freshness.sh" \
	"${ROOT}/tools/quality/test-main-release-cleanup.sh" \
	"${ROOT}/tools/quality/test-enterprise-release-flow.sh" \
	"${ROOT}/tools/quality/test-enterprise-promotion-transfer.sh" \
	"${ROOT}/tools/quality/test-bundled-language-pack-lifecycle.sh" \
	"${ROOT}/tools/quality/test-language-pack-runtime-index.sh" \
	"${ROOT}/tools/quality/test-upgrade-backup-retention.sh" \
	"${ROOT}/tools/quality/test-redis-rdb-preflight.sh" \
	"${ROOT}/tools/quality/test-agent-release-retention.sh" \
	"${ROOT}/tools/quality/test-managed-image-retention.sh" \
	"${ROOT}/tools/quality/test-shared-host-guard.sh" \
	"${ROOT}/tools/quality/test-release-download-proxy.sh" \
	"${ROOT}/tools/quality/test-public-endpoint-check.sh" \
	"${ROOT}/tools/quality/test-sourcelens-git-mirror.sh" \
	"${ROOT}/tools/quality/test-sourcelens-submodule-recovery.sh" \
	"${ROOT}"/release/ci/*.sh \
	"${ROOT}/release/ci/write-sbom.py"; do
	[[ -x "${executable}" ]] || {
		printf 'ERROR: CI entry point is not executable: %s\n' "${executable}" >&2
		exit 1
	}
done

grep -F 'compose-lifecycle.sh' "${ROOT}/release/build-sourcelens.sh" >/dev/null
grep -F 'hfl_compose_command_with_exit_event_recovery' \
	"${ROOT}/dev/sourcelens.sh" \
	"${ROOT}/deploy/installer/sourcelens/install.sh" \
	"${ROOT}/deploy/installer/install.sh" >/dev/null

grep -F 'HFL_TENANT_PORT=11443' "${ROOT}/.env.example" >/dev/null
grep -F 'HFL_WEBSITE_PORT=11442' "${ROOT}/.env.example" >/dev/null
grep -F 'HFL_ADMIN_PORT=11444' "${ROOT}/.env.example" >/dev/null
grep -F 'FRONTEND_URL=https://127.0.0.1:11443' "${ROOT}/.env.example" >/dev/null
grep -F 'SOURCELENS_CONSOLE_PORT=11445' "${ROOT}/.env.example" >/dev/null
for compose_file in "${ROOT}/docker-compose.yml" "${ROOT}/deploy/docker-compose.yml"; do
	grep -F '${HFL_WEBSITE_PORT:-11442}:11442' "${compose_file}" >/dev/null
	grep -F '${HFL_TENANT_PORT:-11443}:11443' "${compose_file}" >/dev/null
	grep -F '${HFL_ADMIN_PORT:-11444}:11444' "${compose_file}" >/dev/null
done
dev_web_block="$(sed -n '/^  web:/,/^  nginx:/p' "${ROOT}/docker-compose.yml")"
dev_nginx_block="$(sed -n '/^  nginx:/,/^networks:/p' "${ROOT}/docker-compose.yml")"
grep -F './build/website/public:/usr/share/nginx/website:ro' <<<"${dev_web_block}" >/dev/null
if grep -F './build/website/public:/usr/share/nginx/website:ro' <<<"${dev_nginx_block}" >/dev/null; then
	printf 'ERROR: development gateway must reach Website through Web, not a direct bind mount\n' >&2
	exit 1
fi
for upstream in 'web:8080' 'web:8081' 'web:8082'; do
	grep -F "${upstream}" "${ROOT}/deploy/nginx/development-upstreams.conf" >/dev/null
done
if grep -Eq '^  website:|website-node-modules|development-website-locations' \
	"${ROOT}/docker-compose.yml"; then
	printf 'ERROR: development Website must be a static Nginx artifact, not a service\n' >&2
	exit 1
fi
release_worker_block="$(sed -n '/^  worker:/,/^  scheduler:/p' "${ROOT}/deploy/docker-compose.yml")"
release_nginx_block="$(sed -n '/^  nginx:/,/^networks:/p' "${ROOT}/deploy/docker-compose.yml")"
if grep -F 'HFL_WEBSITE_APP_URL:' <<<"${release_worker_block}" >/dev/null; then
	printf 'ERROR: Website runtime URL must not be injected into the worker\n' >&2
	exit 1
fi
grep -F 'HFL_WEBSITE_APP_URL: ${FRONTEND_URL:-}' <<<"${release_nginx_block}" >/dev/null
for listener in 11442 11443 11444; do
	grep -F "listen ${listener} ssl;" "${ROOT}/deploy/nginx/default.conf" >/dev/null
done
grep -F 'COPY build/website/public /usr/share/nginx/website' \
	"${ROOT}/deploy/docker/frontend.Dockerfile" >/dev/null
grep -F 'COPY deploy/docker/frontend-runtime-config.sh /docker-entrypoint.d/20-hfl-frontend-runtime-config.sh' \
	"${ROOT}/deploy/docker/frontend.Dockerfile" >/dev/null
if grep -F 'COPY website/' "${ROOT}/deploy/docker/frontend.Dockerfile" >/dev/null; then
	printf 'ERROR: HFL frontend image must consume the standalone Website artifact only\n' >&2
	exit 1
fi
grep -F 'Prepare standalone Website artifact' "${workflow}" >/dev/null
grep -F '${ROOT}/website/build.sh' "${ROOT}/release/build.sh" >/dev/null
[[ -f "${ROOT}/website/en/index.md" && -f "${ROOT}/website/package-lock.json" ]]
[[ -f "${ROOT}/website/Dockerfile" && -x "${ROOT}/website/build.sh" \
	&& -x "${ROOT}/website/runtime-config.sh" ]]
grep -F 'SEED_ADMIN_PASSWORD=Admin@123' "${ROOT}/.env.example" >/dev/null
if grep -E 'HFL_TLS_SAN_(IP|DNS)' "${ROOT}/.env.example" "${installer}" >/dev/null; then
	printf 'ERROR: runtime-generated TLS SAN configuration must not remain\n' >&2
	exit 1
fi
for runtime_tls_script in \
	"${installer}" \
	"${ROOT}/deploy/installer/sourcelens/install.sh" \
	"${ROOT}/dev/stack.sh" \
	"${ROOT}/tools/sourcelens/common.sh"; do
	if grep -F 'openssl req -x509' "${runtime_tls_script}" >/dev/null; then
		printf 'ERROR: runtime TLS generation remains in %s\n' "${runtime_tls_script}" >&2
		exit 1
	fi
done
for cert_file in tls.crt tls.key root-ca.crt SHA256SUMS README.md; do
	[[ -s "${ROOT}/deploy/nginx/certs/${cert_file}" ]] || {
		printf 'ERROR: default TLS file is missing: %s\n' "${cert_file}" >&2
		exit 1
	}
done
[[ ! -e "${ROOT}/deploy/nginx/certs/.gitignore" ]]
legacy_public_port_pattern='104(42|43|44|45|46)'
if git -C "${ROOT}" grep -n -E "${legacy_public_port_pattern}" -- .; then
	printf 'ERROR: tracked HFL files must not reference legacy 104xx public ports\n' >&2
	exit 1
fi
for runtime_alias in \
	'nginx:stable-alpine hyperfilelens-sourcelens-nginx:stable-alpine' \
	'postgres:17 hyperfilelens-postgres:17' \
	'redis:alpine hyperfilelens-redis:alpine'; do
	grep -F "${runtime_alias}" "${ROOT}/tools/sourcelens/common.sh" >/dev/null
done
smoke_runner="${ROOT}/tools/dev/browser-smoke.sh"
grep -F -- '--add-host host.docker.internal:host-gateway' "${smoke_runner}" >/dev/null
grep -F 'SMOKE_HOST' "${smoke_runner}" >/dev/null
grep -F 'HFL_LOGIN_PORT="${login_port}"' "${smoke_runner}" >/dev/null
grep -F 'DJANGO_SUPERUSER_EMAIL' "${smoke_runner}" >/dev/null
grep -F 'DJANGO_SUPERUSER_USERNAME' "${smoke_runner}" >/dev/null
smoke_script="${ROOT}/tools/dev/browser-smoke.mjs"
grep -F 'host.docker.internal' "${smoke_script}" >/dev/null
grep -F "submit.waitFor({ state: 'visible'" "${smoke_script}" >/dev/null
grep -F 'input[autocomplete="email"]' "${smoke_script}" >/dev/null
grep -F 'input[type="email"]' "${smoke_script}" >/dev/null
grep -F 'if (!(await password.isVisible()))' "${smoke_script}" >/dev/null
if grep -F 'captchaImage' "${smoke_script}" >/dev/null; then
	printf 'ERROR: local browser smoke must not depend on image captcha\n' >&2
	exit 1
fi
grep -F 'waitForPlatformOps' "${smoke_script}" >/dev/null
grep -F 'const hfl = await browser.newContext' "${smoke_script}" >/dev/null
grep -F "allowedHosts: ['host.docker.internal']" "${ROOT}/src/frontend/vite.config.ts" >/dev/null
if grep -F -- '--network host' "${smoke_runner}" >/dev/null; then
	printf 'ERROR: browser smoke must reach published ports through host-gateway\n' >&2
	exit 1
fi
grep -F 'image: hyperfilelens-postgres:17' "${ROOT}/deploy/docker-compose.yml" >/dev/null
grep -F 'absolute_redirect off;' "${ROOT}/deploy/nginx/default.conf" >/dev/null
# Website pool (:8082) must keep / → /en/ relative; absolute redirects leak the
# unpublished internal listen port through the public :11442 gateway.
grep -F 'absolute_redirect off;' "${ROOT}/deploy/nginx/web.conf" >/dev/null
grep -F 'map $server_port $hfl_site {' \
	"${ROOT}/deploy/nginx/snippets/hfl-log-format.conf" >/dev/null
grep -E '^[[:space:]]*11442[[:space:]]+website;' \
	"${ROOT}/deploy/nginx/snippets/hfl-log-format.conf" >/dev/null
grep -E '^[[:space:]]*11444[[:space:]]+ops;' \
	"${ROOT}/deploy/nginx/snippets/hfl-log-format.conf" >/dev/null
grep -F 'proxy_set_header X-HFL-Site-Role $hfl_site;' \
	"${ROOT}/deploy/nginx/snippets/hfl-backend-proxy-headers.inc" >/dev/null
for resource in \
	'mem_limit: 128m' 'mem_limit: 256m' 'mem_limit: 512m' \
	'cpus: 0.125' 'cpus: 0.25' 'cpus: 0.50' 'cpus: 1.00'; do
	grep -F "${resource}" "${ROOT}/deploy/docker-compose.yml" \
		"${ROOT}/deploy/installer/sourcelens/docker-compose.template.yml" >/dev/null
done
sourcelens_compose_template="${ROOT}/deploy/installer/sourcelens/docker-compose.template.yml"
[[ "$(grep -Fc '    mem_limit: 2g' "${sourcelens_compose_template}" || true)" -eq 2 ]] \
	|| { printf 'ERROR: bundled SourceLens API and LensNode must both use a 2 GiB limit\n' >&2; exit 1; }
grep -F '    mem_limit: 2g' \
	"${ROOT}/deploy/bootstrap/gateway-install-lensnode-sidecar.sh" >/dev/null
if grep -E 'mem_limit: (64|320|384|448)m|cpus: (0\.05|0\.10|0\.15|0\.20|0\.30)' \
	"${ROOT}/deploy/docker-compose.yml" \
	"${ROOT}/deploy/installer/sourcelens/docker-compose.template.yml" \
	"${ROOT}/deploy/bootstrap/gateway-install-lensnode-sidecar.sh" >/dev/null; then
	printf 'ERROR: deployment resources must use the normalized human-readable limits\n' >&2
	exit 1
fi
grep -F 'MemoryHigh=512M' "${ROOT}/src/agent/packaging/install/install.sh" >/dev/null
grep -F 'CPUQuota=50%' "${ROOT}/src/agent/packaging/install/install.sh" >/dev/null
grep -F 'name: hyperfilelens-sourcelens' \
	"${ROOT}/deploy/installer/sourcelens/docker-compose.template.yml" >/dev/null
grep -F 'target: prod' "${enterprise_promotion_workflow}" >/dev/null
grep -F 'channel: release' "${enterprise_promotion_workflow}" >/dev/null
grep -F 'Production deployment requires a manual workflow_dispatch event' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'if ! SOURCELENS_BUILD_SOURCE_MAPS=1' \
	"${ROOT}/release/ci/build-sourcelens-image.sh" >/dev/null
grep -F 'chmod 0700 "${COMPOSE_DIR}"' \
	"${ROOT}/deploy/bootstrap/gateway-install-lensnode-sidecar.sh" >/dev/null
grep -F 'chmod 0600 "${compose_file}"' \
	"${ROOT}/deploy/bootstrap/gateway-install-lensnode-sidecar.sh" >/dev/null
grep -F 'config/lensnode.env' \
	"${ROOT}/deploy/bootstrap/gateway-lifecycle.sh" \
	"${ROOT}/deploy/installer/install.sh" >/dev/null
grep -F 'runtime/lensnode' \
	"${ROOT}/deploy/bootstrap/gateway-install-lensnode-sidecar.sh" \
	"${ROOT}/deploy/bootstrap/gateway-lifecycle.sh" >/dev/null

printf 'Release contract checks passed.\n'
