#!/usr/bin/env bash
set -euo pipefail

ROOT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Source stack functions without executing the command dispatcher.
# shellcheck source=../../dev/stack.sh
source "${ROOT_REPO}/dev/stack.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

# A stale runtime metadata value must not pin bundled development or release builds.
mkdir -p "${tmp}/repo"
printf '%s\n' 'SOURCELENS_GIT_REF=v0.4.0' >"${tmp}/repo/.env"
original_root="${ROOT}"
ROOT="${tmp}/repo"
SOURCELENS_GIT_REF=""
load_repo_env_defaults
# shellcheck source=../sourcelens/defaults.env
source "${ROOT_REPO}/tools/sourcelens/defaults.env"
[[ "${SOURCELENS_GIT_REF}" == "v0.47.9" ]]
ROOT="${original_root}"

# Source deployments can persist the same canonical origins used by Release
# and CI installs instead of editing .env by hand.
source_deploy_root="${tmp}/source-deploy"
mkdir -p "${source_deploy_root}/deploy/installer"
cp "${ROOT_REPO}/.env.example" "${source_deploy_root}/.env"
cp "${ROOT_REPO}/deploy/installer/apply-runtime-config.py" \
	"${source_deploy_root}/deploy/installer/apply-runtime-config.py"
ROOT="${source_deploy_root}"
DEV_PUBLIC_URL="https://192.0.2.10:11443"
DEV_ADMIN_PUBLIC_URL="https://192.0.2.10:11444"
apply_dev_public_urls >/dev/null
grep -Fx 'FRONTEND_URL=https://192.0.2.10:11443' "${ROOT}/.env" >/dev/null
grep -Fx 'LENS_GATEWAY_BASE_URL=https://192.0.2.10:11443/sourcelens' "${ROOT}/.env" >/dev/null
grep -Fx 'HFL_ADMIN_PUBLIC_URL=https://192.0.2.10:11444' "${ROOT}/.env" >/dev/null
ROOT="${original_root}"
DEV_PUBLIC_URL=""
DEV_ADMIN_PUBLIC_URL=""

dev_env_loader="$(sed -n '/^load_repo_env_defaults()/,/^}/p' "${ROOT_REPO}/dev/stack.sh")"
release_env_loader="$(sed -n '/^load_repo_env_defaults()/,/^}/p' "${ROOT_REPO}/release/build.sh")"
if grep -qw SOURCELENS_GIT_REF <<<"${dev_env_loader}${release_env_loader}"; then
	printf 'ERROR: runtime .env must not select the bundled SourceLens build ref\n' >&2
	exit 1
fi

# CLI mirror options must reach HFL Compose builds even when SourceLens is skipped.
MIRROR_GITHUB_DOWNLOAD=""
MIRROR_GITHUB_TOKEN=""
MIRROR_DOCKER_DOWNLOAD=""
MIRROR_APT=""
OPT_GO_PROXY=""
OPT_GO_SUMDB=""
OPT_PIP_INDEX_URL=""
OPT_PIP_TRUSTED_HOST=""
OPT_NPM_REGISTRY=""
parse_common_option --github-download-mirror https://github-mirror.example.test
parse_common_option --github-token test-token
parse_common_option --docker-download-mirror docker-mirror.example.test
parse_common_option --apt-mirror https://apt-mirror.example.test
parse_common_option --go-proxy https://go-proxy.example.test,direct
parse_common_option --go-sumdb sumdb.example.test
parse_common_option --pip-index-url https://pip-mirror.example.test/simple
parse_common_option --pip-trusted-host pip-mirror.example.test
parse_common_option --npm-registry https://npm-mirror.example.test
parse_common_option --no-sourcelens
apply_mirror_env_defaults
prepare_sourcelens_dev 0

[[ "${WITH_SOURCELENS}" == "0" ]]
[[ "${GITHUB_DOWNLOAD_MIRROR}" == "https://github-mirror.example.test" ]]
[[ "${GITHUB_TOKEN}" == "test-token" ]]
[[ "${DOCKER_DOWNLOAD_MIRROR}" == "docker-mirror.example.test" ]]
[[ "${HFL_BACKEND_BASE_IMAGE}" == "docker-mirror.example.test/library/ubuntu:24.04" ]]
[[ "${HFL_FRONTEND_NODE_BASE_IMAGE}" == "docker-mirror.example.test/library/node:22-alpine" ]]
[[ "${HFL_FRONTEND_NGINX_BASE_IMAGE}" == "docker-mirror.example.test/library/nginx:stable-alpine" ]]
[[ "${HFL_WEBSITE_BASE_IMAGE}" == "${HFL_FRONTEND_NODE_BASE_IMAGE}" ]]
[[ "${APT_MIRROR}" == "https://apt-mirror.example.test" ]]
[[ "${GOPROXY}" == "https://go-proxy.example.test,direct" ]]
[[ "${GOSUMDB}" == "sumdb.example.test" ]]
[[ "${PIP_INDEX_URL}" == "https://pip-mirror.example.test/simple" ]]
[[ "${PIP_TRUSTED_HOST}" == "pip-mirror.example.test" ]]
[[ "${NPM_REGISTRY}" == "https://npm-mirror.example.test" ]]

# SourceLens management must use its image virtualenv without a login shell.
# shellcheck source=../sourcelens/common.sh
source "${ROOT_REPO}/tools/sourcelens/common.sh"
sourcelens_log() { :; }
compose_calls=()
migration_status=0
sourcelens_dev_compose() {
	compose_calls+=("$*")
	if [[ "$*" == *"manage.py migrate --check"* ]]; then
		return "${migration_status}"
	fi
}

sourcelens_ensure_database_initialized
[[ " ${compose_calls[*]} " == *" exec -T --workdir /opt/backend api /opt/venv/bin/python manage.py migrate --check "* ]]
[[ " ${compose_calls[*]} " == *" exec -T --workdir /opt/backend api /opt/venv/bin/python manage.py collectstatic --noinput "* ]]
[[ " ${compose_calls[*]} " != *" sh -lc "* ]]

compose_calls=()
migration_status=1
sourcelens_ensure_database_initialized
[[ " ${compose_calls[*]} " == *" exec -T --workdir /opt/backend api /opt/venv/bin/python manage.py sourcelens_init --skip-collectstatic "* ]]

# The runtime metadata must be synchronized to the version that was actually built.
HFL_ROOT="${tmp}/hfl"
mkdir -p "${HFL_ROOT}"
printf '%s\n' \
	'SOURCELENS_GIT_REF=v0.4.0' \
	'FRONTEND_URL=https://127.0.0.1:11443' \
	'NO_PROXY=localhost' >"${HFL_ROOT}/.env"
SOURCELENS_GIT_REF=v0.20.0
sourcelens_configure_hfl_env >/dev/null
grep -Fx 'SOURCELENS_GIT_REF=v0.20.0' "${HFL_ROOT}/.env" >/dev/null

# Fresh runtime trees are created under a restrictive umask but remain readable
# by non-root processes inside the generated SourceLens containers.
runtime_root="${tmp}/runtime"
mkdir -p "${runtime_root}/deploy/postgresql/initdb.d"
printf '%s\n' 'SELECT 1;' >"${runtime_root}/deploy/postgresql/initdb.d/000-init.sql"
printf '%s\n' '#!/usr/bin/env bash' >"${runtime_root}/deploy/postgresql/initdb.d/001-init.sh"
printf '%s\n' 'services: {}' >"${runtime_root}/docker-compose.yml"
chmod -R 0700 "${runtime_root}"
sourcelens_normalize_dev_runtime_permissions "${runtime_root}"
[[ "$(stat -c '%a' "${runtime_root}/deploy/postgresql/initdb.d")" == "755" ]]
[[ "$(stat -c '%a' "${runtime_root}/deploy/postgresql/initdb.d/000-init.sql")" == "644" ]]
[[ "$(stat -c '%a' "${runtime_root}/deploy/postgresql/initdb.d/001-init.sh")" == "755" ]]
[[ "$(stat -c '%a' "${runtime_root}/docker-compose.yml")" == "644" ]]

grep -F 'find "${temporary}" -type d -exec chmod 0755 {} +' \
	"${ROOT_REPO}/website/build.sh" >/dev/null
grep -F 'find "${temporary}" -type f -exec chmod 0644 {} +' \
	"${ROOT_REPO}/website/build.sh" >/dev/null
grep -F 'chmod 0755 "${temporary}/runtime-config.sh"' \
	"${ROOT_REPO}/website/build.sh" >/dev/null

# Replacing the Website artifact directory leaves an existing Docker bind mount
# attached to the old inode. Recreate only Web when a rebuild occurred.
compose_calls=()
compose() { compose_calls+=("$*"); }
WEBSITE_ARTIFACT_REBUILT=0
refresh_website_web_mount
[[ "${#compose_calls[@]}" -eq 0 ]]

WEBSITE_ARTIFACT_REBUILT=1
refresh_website_web_mount
[[ "${#compose_calls[@]}" -eq 1 ]]
[[ "${compose_calls[0]}" == "up -d --no-deps --no-build --pull never --force-recreate web" ]]

cmd_up_body="$(sed -n '/^cmd_up()/,/^}/p' "${ROOT_REPO}/dev/stack.sh")"
cmd_restart_body="$(sed -n '/^cmd_restart()/,/^}/p' "${ROOT_REPO}/dev/stack.sh")"
run_dev_migration_gate_body="$(sed -n '/^run_dev_migration_gate()/,/^}/p' "${ROOT_REPO}/dev/stack.sh")"
grep -F 'refresh_website_web_mount' <<<"${cmd_up_body}" >/dev/null
grep -F 'refresh_website_web_mount' <<<"${cmd_restart_body}" >/dev/null
for command_body in "${cmd_up_body}" "${cmd_restart_body}"; do
	gate_line="$(grep -nF 'run_dev_migration_gate' <<<"${command_body}" | cut -d: -f1)"
	application_line="$(grep -nE 'compose(_logged)? up -d --no-build --pull never' <<<"${command_body}" | cut -d: -f1 | tail -n 1)"
	[[ -n "${gate_line}" ]]
	[[ -n "${application_line}" ]]
	((gate_line < application_line))
	model_repair_line="$(grep -nF 'repair_existing_multimodal_model' <<<"${command_body}" | cut -d: -f1)"
	[[ -n "${model_repair_line}" ]]
	((application_line < model_repair_line))
done
repair_model_body="$(sed -n '/^repair_existing_multimodal_model()/,/^}/p' "${ROOT_REPO}/dev/stack.sh")"
grep -F '{"role":"multimodal","repair_existing":true}' <<<"${repair_model_body}" >/dev/null
grep -F 'ensure_platform_ai_model' <<<"${repair_model_body}" >/dev/null

# Source upgrades must invoke the idempotent repair through the running API,
# and a temporarily unavailable insight API must not take down the dev stack.
model_repair_args="${tmp}/model-repair.args"
model_repair_input="${tmp}/model-repair.input"
wait_for_api_healthy() { return 0; }
compose() {
	printf '%s\n' "$*" >"${model_repair_args}"
	cat >"${model_repair_input}"
}
WITH_SOURCELENS=1
repair_existing_multimodal_model >/dev/null
grep -Fx 'exec -T api python manage.py ensure_platform_ai_model' "${model_repair_args}" >/dev/null
grep -Fx '{"role":"multimodal","repair_existing":true}' "${model_repair_input}" >/dev/null
compose() {
	cat >/dev/null
	return 42
}
repair_existing_multimodal_model >/dev/null
backend_stop_line="$(grep -nE 'compose(_logged)? stop api worker scheduler' <<<"${run_dev_migration_gate_body}" | cut -d: -f1)"
data_services_line="$(grep -nE 'compose(_logged)? up -d --wait --no-build --pull never postgres redis' <<<"${run_dev_migration_gate_body}" | cut -d: -f1)"
migration_line="$(grep -nF 'compose --profile tools run --rm --no-deps migration' <<<"${run_dev_migration_gate_body}" | cut -d: -f1)"
[[ -n "${backend_stop_line}" ]]
[[ -n "${data_services_line}" ]]
[[ -n "${migration_line}" ]]
((backend_stop_line < data_services_line))
((data_services_line < migration_line))

# The singleton migration must load the same backend extensions as runtime services.
grep -F 'for svc in ("migration", "api", "worker", "scheduler"):' \
	"${ROOT_REPO}/tools/extensions/materialize_extensions.py" >/dev/null

# Runtime artifacts are mounted below the backend source root in development.
# Publishing Python helpers into media must not restart API or Celery processes.
backend_entrypoint="${ROOT_REPO}/deploy/docker/backend-entrypoint.sh"
grep -F 'DEV_WATCH_IGNORE_PATHS="/opt/backend/media,/opt/backend/staticfiles,/opt/backend/lang-packs"' \
	"${backend_entrypoint}" >/dev/null
[[ "$(grep -Fc 'python /dev-process-supervisor.py' "${backend_entrypoint}")" -eq 3 ]]
grep -F 'deploy/docker/dev-process-supervisor.py deploy/bootstrap' \
	"${ROOT_REPO}/dev/stack.sh" >/dev/null
[[ "$(grep -Fc -- '--watch /opt/backend' "${backend_entrypoint}")" -eq 3 ]]
if grep -F 'exec watchfiles' "${backend_entrypoint}" >/dev/null; then
	echo 'Development processes must be supervised through their real child process' >&2
	exit 1
fi
worker_dev_entrypoint="$(sed -n '/^run_worker_dev()/,/^}/p' "${backend_entrypoint}")"
if grep -F 'run_migrations_and_register' <<<"${worker_dev_entrypoint}" >/dev/null; then
	echo 'Development worker must not run singleton migrations' >&2
	exit 1
fi

# Development Nginx must re-resolve API/Web after Compose recreates containers.
dev_upstreams="${ROOT_REPO}/deploy/nginx/development-upstreams.conf"
[[ "$(grep -Ec 'server (api|web):[0-9]+ resolve;' "${dev_upstreams}")" -eq 5 ]]
grep -F 'zone hfl_api_http 64k' "${dev_upstreams}" >/dev/null
grep -F 'server web:8080 resolve;' "${dev_upstreams}" >/dev/null
grep -F 'server web:8081 resolve;' "${dev_upstreams}" >/dev/null
grep -F 'server web:8082 resolve;' "${dev_upstreams}" >/dev/null

printf 'Development stack upgrade regression checks passed.\n'
