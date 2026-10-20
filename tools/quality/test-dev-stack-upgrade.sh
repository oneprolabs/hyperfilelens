#!/usr/bin/env bash
set -euo pipefail

ROOT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Source stack functions without executing the command dispatcher.
# shellcheck source=../../dev/stack.sh
source "${ROOT_REPO}/dev/stack.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

# Only the explicit consent failure gets one Bake retry. Normal builds, other
# errors, cancellation and unsupported Compose versions retain the old path.
(
	calls="${tmp}/build-calls"
	hint='additional privileges requested: pass "--allow=network.host" to grant requested privileges'
	prepare_compose_files() { :; }
	docker() {
		[[ "$*" == "compose build --help" ]] || return 90
		printf '%s\n' 'help' >>"${calls}"
		[[ "${fixture_bake_supported}" == 0 ]] || printf '%s\n' '  --print Print equivalent bake file'
	}
	compose_logged() {
		printf '%s\n' "legacy:$*" >>"${calls}"
		if [[ "${fixture_native_log}" == 1 ]]; then
			printf '%s\n' "${fixture_message}" >>"${HFL_LOG_FILE}"
		else
			printf '%s\n' "${fixture_message}"
		fi
		return "${fixture_status}"
	}
	compose() {
		printf '%s\n' "definition:$*" >>"${calls}"
		[[ "${fixture_definition_status}" == 0 ]] || return "${fixture_definition_status}"
		printf '%s\n' '{"group":{"default":{"targets":["worker"]}}}'
	}
	hfl_run_native_command() {
		[[ "$1 $2 $3 $4 $5" == "docker buildx bake --allow=network.host --file" ]]
		[[ -s "$6" ]]
		printf '%s\n' "bake-file:$6" >>"${calls}"
		return "${fixture_bake_status}"
	}
	fixture_bake_supported=1
	fixture_definition_status=0
	fixture_bake_status=0
	fixture_native_log=0
	HFL_LOG_FILE="${tmp}/build-session.log"
	for fixture_status in 0 7 130; do
		# Even a stale consent error or successful output containing the hint
		# must not change the original build result or invoke Bake.
		printf '%s\n' "${hint}" >"${HFL_LOG_FILE}"
		fixture_message="${hint}"
		: >"${calls}"
		actual=0
		build_dev_image --no-cache worker || actual=$?
		[[ "${actual}" -eq "${fixture_status}" ]]
		[[ "$(wc -l <"${calls}")" -eq 1 ]]
	done
	fixture_status=1
	fixture_message="dependency download failed"
	: >"${calls}"
	actual=0
	build_dev_image web || actual=$?
	[[ "${actual}" -eq 1 && "$(wc -l <"${calls}")" -eq 1 ]]
	fixture_message="${hint}"
	for fixture_bake_supported in 0 1; do
		for fixture_native_log in 0 1; do
			: >"${calls}"
			actual=0
			build_dev_image --no-cache worker || actual=$?
			grep -Fx 'legacy:build --no-cache worker' "${calls}" >/dev/null
			if [[ "${fixture_bake_supported}" == 0 ]]; then
				[[ "${actual}" -eq 1 ]]
				[[ "$(wc -l <"${calls}")" -eq 2 ]]
				continue
			fi
			[[ "${actual}" -eq 0 ]]
			grep -Fx 'definition:--progress=plain build --print --no-cache worker' "${calls}" >/dev/null
			bake_file="$(sed -n 's/^bake-file://p' "${calls}")"
			[[ -n "${bake_file}" && ! -e "${bake_file}" ]]
		done
	done
	fixture_definition_status=7
	: >"${calls}"
	actual=0
	build_dev_image web || actual=$?
	[[ "${actual}" -eq 7 ]]
	! grep -q '^bake-file:' "${calls}"
	fixture_definition_status=0
	fixture_bake_status=9
	: >"${calls}"
	actual=0
	build_dev_image web || actual=$?
	[[ "${actual}" -eq 9 && "$(grep -c '^bake-file:' "${calls}")" -eq 1 ]]
	bake_file="$(sed -n 's/^bake-file://p' "${calls}")"
	[[ ! -e "${bake_file}" ]]
)

# An OSS worktree changes only the runtime source bind mounts; the current
# repository remains the Compose/configuration and data root.
grep -F '${WORKTREE_DIR:-.}/src/backend:/opt/hyperfilelens/backend' \
	"${ROOT_REPO}/docker-compose.yml" >/dev/null
grep -F '${WORKTREE_DIR:-.}/src/frontend:/opt/hyperfilelens/frontend' \
	"${ROOT_REPO}/docker-compose.yml" >/dev/null
grep -F -- '--worktree-dir PATH' "${ROOT_REPO}/dev/stack.sh" >/dev/null
grep -F 'worktree_recreate_args+=(--force-recreate)' "${ROOT_REPO}/dev/stack.sh" >/dev/null

worktree_root="${tmp}/oss-worktree"
mkdir -p "${worktree_root}/src/backend" "${worktree_root}/src/frontend"
original_invocation_dir="${INVOCATION_DIR}"
original_worktree_dir="${WORKTREE_DIR}"
original_oss_worktree_dir="${OSS_WORKTREE_DIR}"
original_worktree_explicit="${WORKTREE_DIR_EXPLICIT}"
INVOCATION_DIR="${tmp}"
OSS_WORKTREE_DIR="${ROOT_REPO}"
WORKTREE_DIR="${ROOT_REPO}"
WORKTREE_DIR_EXPLICIT=0
resolve_worktree_dir oss-worktree
[[ "${WORKTREE_DIR}" == "${worktree_root}" ]]
[[ "${OSS_WORKTREE_DIR}" == "${worktree_root}" ]]
[[ "${WORKTREE_DIR_EXPLICIT}" -eq 1 ]]
INVOCATION_DIR="${original_invocation_dir}"
WORKTREE_DIR="${original_worktree_dir}"
OSS_WORKTREE_DIR="${original_oss_worktree_dir}"
WORKTREE_DIR_EXPLICIT="${original_worktree_explicit}"
export WORKTREE_DIR

# A stale runtime metadata value must not pin bundled development or release builds.
mkdir -p "${tmp}/repo"
printf '%s\n' 'SOURCELENS_GIT_REF=v0.4.0' >"${tmp}/repo/.env"
original_root="${ROOT}"
ROOT="${tmp}/repo"
SOURCELENS_GIT_REF=""
load_repo_env_defaults
# shellcheck source=../sourcelens/defaults.env
source "${ROOT_REPO}/tools/sourcelens/defaults.env"
[[ "${SOURCELENS_GIT_REF}" == "v0.61.3" ]]
ROOT="${original_root}"

# Gateway refresh must honor the requested ref even if a newer LensNode image
# remains cached. An absent requested version must never fall back to :latest.
(
	SOURCELENS_GIT_REF=v0.61.2
	tagged_ref=""
	docker() {
		case "$*" in
		"image inspect --format {{.Id}} hyperfilelens-sourcelens-lensnode:0.61.2")
			[[ "${IMAGE_AVAILABLE:-1}" == 1 ]] && printf '%s\n' sha256:requested
			;;
		"image inspect --format {{.Id}} hyperfilelens-sourcelens-lensnode:latest")
			printf '%s\n' sha256:newer
			;;
		"tag hyperfilelens-sourcelens-lensnode:0.61.2 hyperfilelens-sourcelens-lensnode:latest")
			tagged_ref=$2
			;;
		*) return 90 ;;
		esac
	}
	refresh_local_platform_lensnode_image_dev
	[[ "${tagged_ref}" == "hyperfilelens-sourcelens-lensnode:0.61.2" ]]
	IMAGE_AVAILABLE=0
	tagged_ref=""
	if refresh_local_platform_lensnode_image_dev; then
		printf 'ERROR: Gateway accepted an unavailable requested LensNode image\n' >&2
		exit 1
	fi
	[[ -z "${tagged_ref}" ]]
)

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

(
	calls="${tmp}/exclusive-dev-calls"
	log() { :; }
	warn() { :; }
	hfl_log_step() { :; }
	hfl_normalize_native_stream() { cat; }
	render_migration_stream() { cat; }
	compose_logged() {
		printf '%s\n' "$*" >>"${calls}"
		[[ "$*" != "stop api worker scheduler" ]] || return "${stop_status}"
	}
	compose() {
		printf '%s\n' "$*" >>"${calls}"
		case "$*" in
		"ps --status running -q api worker scheduler")
			printf '%s' "${backend_running}"
			return "${ps_status}"
			;;
		"--profile tools run --rm --no-deps -e HFL_EXCLUSIVE_PLATFORM_MIGRATION=1 migration")
			printf 'migration fixture\n'
			;;
		*) return 91 ;;
		esac
	}
	wait_for_data_services_healthy() { return "${data_status}"; }
	for scenario in stop-failed still-running inspect-failed data-unhealthy ready; do
		stop_status=0 ps_status=0 data_status=0 backend_running=""
		case "${scenario}" in
		stop-failed) stop_status=1 ;;
		still-running) backend_running=old-api ;;
		inspect-failed) ps_status=1 ;;
		data-unhealthy) data_status=1 ;;
		esac
		: >"${calls}"
		if run_dev_migration_gate >/dev/null; then
			[[ "${scenario}" == ready ]]
			grep -F 'HFL_EXCLUSIVE_PLATFORM_MIGRATION=1 migration' "${calls}" >/dev/null
		else
			[[ "${scenario}" != ready ]]
			! grep -F 'HFL_EXCLUSIVE_PLATFORM_MIGRATION=1 migration' "${calls}" >/dev/null
		fi
	done
)

cmd_up_body="$(sed -n '/^cmd_up()/,/^}/p' "${ROOT_REPO}/dev/stack.sh")"
cmd_restart_body="$(sed -n '/^cmd_restart()/,/^}/p' "${ROOT_REPO}/dev/stack.sh")"
run_dev_migration_gate_body="$(sed -n '/^run_dev_migration_gate()/,/^}/p' "${ROOT_REPO}/dev/stack.sh")"
gateway_refresh_body="$(sed -n '/^ensure_local_platform_gateway_dev()/,/^}/p' "${ROOT_REPO}/dev/stack.sh")"
grep -F 'refresh_website_web_mount' <<<"${cmd_up_body}" >/dev/null
grep -F 'refresh_website_web_mount' <<<"${cmd_restart_body}" >/dev/null
for command_body in "${cmd_up_body}" "${cmd_restart_body}"; do
	gate_line="$(grep -nF 'run_dev_migration_gate' <<<"${command_body}" | cut -d: -f1)"
	application_line="$(grep -nE 'compose(_logged)? up -d --no-build --pull never' <<<"${command_body}" | cut -d: -f1 | tail -n 1)"
	[[ -n "${gate_line}" ]]
	[[ -n "${application_line}" ]]
	((gate_line < application_line))
	grep -F 'ensure_local_platform_gateway_dev' <<<"${command_body}" >/dev/null
done

# An enabled development Gateway always consumes the current Agent and LensNode
# artifacts. The auto-deploy setting remains the only public control: when it is
# disabled, the function returns before inspecting or changing the host runtime.
if grep -En 'UPGRADE_GATEWAY|HFL_UPGRADE_GATEWAY|upgrade-gateway' \
	"${ROOT_REPO}/dev/stack.sh" >/dev/null; then
	printf 'ERROR: obsolete manual Platform Gateway refresh control remains\n' >&2
	exit 1
fi
grep -F 'if [[ -e "${agent_bin}" ]]; then' <<<"${gateway_refresh_body}" >/dev/null
grep -F 'gateway_args+=(--reinstall)' <<<"${gateway_refresh_body}" >/dev/null
grep -F 'HFL_FORCE_SIDECAR_RECREATE=1 \' <<<"${gateway_refresh_body}" >/dev/null
disabled_line="$(grep -nF 'if ! platform_gateway_auto_deploy_enabled; then' <<<"${gateway_refresh_body}" | cut -d: -f1)"
disabled_return_line="$(grep -nF 'return 0' <<<"${gateway_refresh_body}" | cut -d: -f1 | head -n 1)"
helper_line="$(grep -nF 'local helper=' <<<"${gateway_refresh_body}" | cut -d: -f1)"
[[ -n "${disabled_line}" && -n "${disabled_return_line}" && -n "${helper_line}" ]]
((disabled_line < disabled_return_line && disabled_return_line < helper_line))
backend_stop_line="$(grep -nE 'compose(_logged)? stop api worker scheduler' <<<"${run_dev_migration_gate_body}" | cut -d: -f1)"
data_services_line="$(grep -nE 'compose(_logged)? up -d --no-build --pull never postgres redis' <<<"${run_dev_migration_gate_body}" | cut -d: -f1)"
data_services_wait_line="$(grep -nF 'wait_for_data_services_healthy' <<<"${run_dev_migration_gate_body}" | cut -d: -f1)"
migration_line="$(grep -nF 'compose --profile tools run --rm --no-deps -e HFL_EXCLUSIVE_PLATFORM_MIGRATION=1 migration' <<<"${run_dev_migration_gate_body}" | cut -d: -f1)"
[[ -n "${backend_stop_line}" ]]
[[ -n "${data_services_line}" ]]
[[ -n "${data_services_wait_line}" ]]
[[ -n "${migration_line}" ]]
((backend_stop_line < data_services_line))
((data_services_line < data_services_wait_line))
((data_services_wait_line < migration_line))
grep -F 'HFL_DATA_SERVICES_HEALTH_TIMEOUT_SECONDS="${HFL_DATA_SERVICES_HEALTH_TIMEOUT_SECONDS:-300}"' \
	"${ROOT_REPO}/dev/stack.sh" >/dev/null
if grep -F 'up -d --wait --no-build --pull never postgres redis' <<<"${run_dev_migration_gate_body}" >/dev/null; then
	printf 'ERROR: migration gate must not rely on Docker Compose --wait for data services\n' >&2
	exit 1
fi

# The singleton migration must load the same backend extensions as runtime services.
grep -F 'for svc in ("migration", "api", "worker", "scheduler"):' \
	"${ROOT_REPO}/tools/extensions/materialize_extensions.py" >/dev/null

# Runtime artifacts are mounted below the backend source root in development.
# Publishing Python helpers into media must not restart API or Celery processes.
backend_entrypoint="${ROOT_REPO}/deploy/docker/backend-entrypoint.sh"
grep -F 'DEV_WATCH_IGNORE_PATHS="/opt/hyperfilelens/backend/media,/opt/hyperfilelens/backend/staticfiles,/opt/hyperfilelens/backend/lang-packs"' \
	"${backend_entrypoint}" >/dev/null
[[ "$(grep -Fc 'python /dev-process-supervisor.py' "${backend_entrypoint}")" -eq 3 ]]
grep -F 'deploy/docker/dev-process-supervisor.py deploy/bootstrap' \
	"${ROOT_REPO}/dev/stack.sh" >/dev/null
[[ "$(grep -Fc -- '--watch /opt/hyperfilelens/backend' "${backend_entrypoint}")" -eq 3 ]]
grep -F 'if [ -d /opt/hyperfilelens/extensions ]; then' \
	"${backend_entrypoint}" >/dev/null
grep -F 'set -- "$@" --watch /opt/hyperfilelens/extensions' \
	"${backend_entrypoint}" >/dev/null
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

# Explicit Agent test versions must not alter the default release resolver.
(
	config="$("${ROOT_REPO}/dev/stack.sh" up --agent-version v0.3.6 --print-config)"
	grep -Fx 'agent_version=0.3.6' <<<"${config}" >/dev/null
	config="$("${ROOT_REPO}/dev/stack.sh" up --print-config)"
	grep -Fx 'agent_version=<default>' <<<"${config}" >/dev/null
	# Joining extension inputs must not leak comma IFS into command dispatch.
	(
		print_config() {
			[[ "${IFS}" == $' \t\n' ]]
			[[ "${EXTENSION_SOURCES_CSV}" == "../ee-one,../ee-two" ]]
			[[ "${OPT_AGENT_VERSION}" == "0.3.6" ]]
		}
		main up --agent-version 0.3.6 \
			--extension-source ../ee-one --extension-source ../ee-two --print-config
	)
	for args in '--agent-version' '--agent-version invalid' '--agent-version 0.3.6-dev.1'; do
		if "${ROOT_REPO}/dev/stack.sh" up ${args} --print-config >"${tmp}/invalid-agent.log" 2>&1; then
			echo "Invalid Agent option accepted: ${args}" >&2
			exit 1
		fi
	done
	if "${ROOT_REPO}/dev/stack.sh" down --agent-version 0.3.6 --print-config \
		>"${tmp}/invalid-agent.log" 2>&1; then
		exit 1
	fi
	# Explicit publisher versions already take precedence over RELEASE_VERSION.
	config="$(RELEASE_VERSION=9.9.9 "${ROOT_REPO}/tools/agent/publish.sh" \
		--version 0.3.6 --print-config)"
	grep -Fx 'version=0.3.6' <<<"${config}" >/dev/null
)

# Use isolated fixtures: never build packages or recreate development services.
(
	ROOT="${tmp}/agent-repo"
	STATE_FILE="${ROOT}/build/state/dev-stack.json"
	mkdir -p "${ROOT}/tools/dev" "${ROOT}/tools/agent" "${ROOT}/src/agent"
	cp "${ROOT_REPO}/tools/dev/cache_state.py" "${ROOT}/tools/dev/"
	printf 'original source\n' >"${ROOT}/src/agent/source.go"
	git -C "${ROOT}" init -q
	git -C "${ROOT}" -c user.name=Test -c user.email=test@example.invalid \
		commit --allow-empty -qm fixture
	git -C "${ROOT}" tag v0.3.4
	cat >"${ROOT}/tools/agent/publish.sh" <<'PUBLISH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${FIXTURE_CALLS}"
[[ "${FIXTURE_FAIL:-0}" == 0 ]] || exit 7
version=0.3.4
while (($#)); do
	if [[ "$1" == --version ]]; then version="$2"; shift; fi
	shift
done
release="${FIXTURE_ROOT}/data/media/agent-releases/${version}"
mkdir -p "${release}"
printf '{}\n' >"${release}/RELEASE_MANIFEST.json"
for suffix in linux-amd64.tar.gz windows-amd64.zip \
	linux-amd64-ubuntu2004.tar.gz linux-amd64-ubuntu2204.tar.gz linux-amd64-ubuntu2404.tar.gz; do
	printf 'archive\n' >"${release}/hfl-agent-${version}-${suffix}"
done
PUBLISH
	chmod +x "${ROOT}/tools/agent/publish.sh"
	export FIXTURE_ROOT="${ROOT}" FIXTURE_CALLS="${tmp}/agent-publish-calls"
	export AGENT_MATRIX="linux:amd64 windows:amd64"
	KOPIA_GIT_REF=v0.23.1
	KOPIA_ARTIFACT_MODE=build
	KOPIA_GIT_URL=https://github.com/kopia/kopia.git
	UBUNTU2404_ARCH=amd64
	DEV_OFFLINE=0
	OPT_AGENT_VERSION=""
	AGENT_VERSION_READY=0
	AGENT_COMPOSE_OVERLAY=""
	TMPDIR="${tmp}/agent-overlays"
	mkdir -p "${TMPDIR}"
	export TMPDIR
	EXTENSION_SOURCES_CSV=""
	# Default invocation still forwards no version argument.
	publish_agent 0
	! grep -q -- '--version' "${FIXTURE_CALLS}"
	prepare_compose_files
	[[ "${#COMPOSE_FILES[@]}" -eq 2 && -z "${AGENT_COMPOSE_OVERLAY}" ]]

	OPT_AGENT_VERSION=0.3.6
	AGENT_VERSION_READY=0
	prepare_compose_files
	[[ "${#COMPOSE_FILES[@]}" -eq 2 ]]
	publish_agent 0
	grep -q -- '--version 0.3.6' "${FIXTURE_CALLS}"
	# Artifact matrix parsing must also be robust to an inherited comma IFS.
	(IFS=','; agent_test_artifacts_present)
	[[ "${AGENT_VERSION_READY}" -eq 1 ]]
	prepare_agent_compose_overlay
	prepare_compose_files
	[[ "${#COMPOSE_FILES[@]}" -eq 4 && -f "${AGENT_COMPOSE_OVERLAY}" ]]
	[[ "$(grep -c 'AGENT_VERSION: "0.3.6"' "${AGENT_COMPOSE_OVERLAY}")" -eq 4 ]]
	overlay="${AGENT_COMPOSE_OVERLAY}"
	prepare_compose_files
	[[ "${AGENT_COMPOSE_OVERLAY}" == "${overlay}" && "${#COMPOSE_FILES[@]}" -eq 4 ]]
	publish_agent 0
	[[ "$(wc -l <"${FIXTURE_CALLS}")" -eq 2 ]]
	# Compose command substitutions and pipelines must share the parent file.
	COMPOSE=(true)
	output="$(compose config)"
	compose config | cat
	[[ "$(find "${TMPDIR}" -name 'hfl-agent-compose.*' | wc -l)" -eq 1 ]]
	[[ "${AGENT_COMPOSE_OVERLAY}" == "${overlay}" ]]
	# Explicit publication must not replace the original default cache.
	OPT_AGENT_VERSION=""
	DEV_OFFLINE=1
	publish_agent 0
	[[ "$(wc -l <"${FIXTURE_CALLS}")" -eq 2 ]]
	OPT_AGENT_VERSION=0.3.6
	publish_agent 0
	[[ "$(wc -l <"${FIXTURE_CALLS}")" -eq 2 ]]
	DEV_OFFLINE=0

	# Source changes and missing required bundles invalidate the explicit cache.
	printf 'changed source\n' >"${ROOT}/src/agent/source.go"
	publish_agent 0
	[[ "$(wc -l <"${FIXTURE_CALLS}")" -eq 3 ]]
	rm -- "${ROOT}/data/media/agent-releases/0.3.6/hfl-agent-0.3.6-linux-amd64-ubuntu2204.tar.gz"
	publish_agent 0
	[[ "$(wc -l <"${FIXTURE_CALLS}")" -eq 4 ]]
	cleanup_agent_compose_overlay
	[[ ! -f "${overlay}" ]]
	[[ "$(find "${TMPDIR}" -name 'hfl-agent-compose.*' | wc -l)" -eq 0 ]]

	# Other published versions cannot satisfy a new test-version request.
	OPT_AGENT_VERSION=0.3.7
	AGENT_VERSION_READY=0
	export FIXTURE_FAIL=1
	result=0
	publish_agent 0 || result=$?
	[[ "${result}" -eq 7 && "${AGENT_VERSION_READY}" -eq 0 ]]
	prepare_compose_files
	[[ "${#COMPOSE_FILES[@]}" -eq 2 && -z "${AGENT_COMPOSE_OVERLAY}" ]]
	unset FIXTURE_FAIL
	publish_agent 0
	grep -q -- '--version 0.3.7' "${FIXTURE_CALLS}"
	prepare_agent_compose_overlay
	prepare_compose_files
	grep -q 'AGENT_VERSION: "0.3.7"' "${AGENT_COMPOSE_OVERLAY}"
	cleanup_agent_compose_overlay
	# Publishing another test version must preserve the previous version cache.
	OPT_AGENT_VERSION=0.3.6
	DEV_OFFLINE=1
	publish_agent 0
	[[ "$(wc -l <"${FIXTURE_CALLS}")" -eq 6 ]]
	DEV_OFFLINE=0

	# A plain invocation ignores any previous test-version override.
	OPT_AGENT_VERSION=""
	prepare_agent_compose_overlay
	prepare_compose_files
	[[ "${#COMPOSE_FILES[@]}" -eq 2 && -z "${AGENT_COMPOSE_OVERLAY}" ]]
	publish_agent 0
	! tail -1 "${FIXTURE_CALLS}" | grep -q -- '--version'
)

# Runtime cleanup must not destroy execution evidence while the database
# survives. Destructive data cleanup can remove both only with confirmation.
(
	ROOT="${tmp}/cleanup"
	mkdir -p "${ROOT}"
	calls="${tmp}/cleanup-calls"
	require_docker() { :; }
	ensure_env_file() { :; }
	compose() {
		[[ "$*" == "config --format json" ]]
		printf '%s\n' '{"volumes":{
			"frontend-node-modules":{"name":"custom_frontend-node-modules"},
			"orphan-executions":{"name":"custom_orphan-executions"}}}'
	}
	compose_logged() { printf 'compose:%s\n' "$*" >>"${calls}"; }
	docker() { printf 'docker:%s\n' "$*" >>"${calls}"; }
	rm() { printf 'rm:%s\n' "$*" >>"${calls}"; }
	CLEAN_SCOPE=runtime
	: >"${calls}"
	cmd_clean
	grep -Fx 'compose:down --remove-orphans' "${calls}" >/dev/null
	grep -Fx 'docker:volume rm custom_frontend-node-modules' "${calls}" >/dev/null
	! grep -Eq -- '--volumes|orphan-executions|^rm:' "${calls}"

	CLEAN_SCOPE=data
	CLEAN_YES=0
	: >"${calls}"
	result=0
	(cmd_clean) >/dev/null 2>&1 || result=$?
	[[ "${result}" -eq 2 && ! -s "${calls}" ]]

	CLEAN_YES=1
	cmd_clean
	grep -Fx "rm:-rf ${ROOT}/data" "${calls}" >/dev/null
	grep -Fx 'docker:volume rm custom_orphan-executions' "${calls}" >/dev/null
	data_line="$(grep -n '^rm:' "${calls}" | cut -d: -f1)"
	journal_line="$(grep -n 'volume rm custom_orphan-executions' "${calls}" | cut -d: -f1)"
	[[ "${data_line}" -lt "${journal_line}" ]]
)

# Both Compose templates place the journal on a shared local named volume,
# never the host log bind mount (which can be virtiofs on Docker Desktop).
python3 - "${ROOT_REPO}" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
for path in (root / "docker-compose.yml", root / "deploy/docker-compose.yml"):
    text = path.read_text()
    shared = text.split("x-backend-volumes: &backend-volumes\n", 1)[1].split("\nx-", 1)[0]
    assert "  - orphan-executions:/var/log/hyperfilelens/.orphan-executions\n" in shared, path
    assert "  orphan-executions:\n    driver: local\n" in text.split("\nvolumes:\n", 1)[1], path
PY

printf 'Development stack upgrade regression checks passed.\n'
