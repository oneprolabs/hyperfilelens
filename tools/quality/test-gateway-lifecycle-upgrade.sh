#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIFECYCLE="${ROOT}/deploy/bootstrap/gateway-lifecycle.sh"
SIDECAR_INSTALLER="${ROOT}/deploy/bootstrap/gateway-install-lensnode-sidecar.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
export HFL_GATEWAY_SIDECAR_LOCK_FILE="${tmp}/sidecar.lock"

test_resume_after_interruption() (
	# shellcheck disable=SC1090
	source "${LIFECYCLE}"
	local destination="${tmp}/resumed.bin" calls="${tmp}/resume-calls"
	local expected='Aurora Glass|37 days|BLUE-ORBIT-731'
	GATEWAY_BOOTSTRAP_BASE=https://console.example/media/gateway-bootstrap
	DOWNLOAD_MAX_ATTEMPTS=3
	DOWNLOAD_RETRY_DELAY_SECONDS=0
	curl() {
		local output="" resume="" arg
		while [[ $# -gt 0 ]]; do
			arg=$1
			shift
			case "${arg}" in
			-o) output=$1; shift ;;
			--continue-at) resume=$1; shift ;;
			esac
		done
		[[ "${resume}" == "-" && -n "${output}" ]]
		if [[ ! -f "${calls}" ]]; then
			printf 1 >"${calls}"
			printf '%s' "${expected:0:17}" >"${output}"
			return 18
		fi
		[[ "$(wc -c <"${output}")" -eq 17 ]]
		printf '%s' "${expected:17}" >>"${output}"
		printf 2 >"${calls}"
	}

	download_bootstrap_file payload.bin "${destination}"
	[[ "$(<"${destination}")" == "${expected}" ]]
	[[ "$(<"${calls}")" == 2 ]]
	[[ ! -e "${destination}.part" ]]
)

test_retry_exhaustion_keeps_partial() {
	local destination="${tmp}/exhausted.bin"
	if (
		# shellcheck disable=SC1090
		source "${LIFECYCLE}"
		GATEWAY_BOOTSTRAP_BASE=https://console.example/media/gateway-bootstrap
		DOWNLOAD_MAX_ATTEMPTS=3
		DOWNLOAD_RETRY_DELAY_SECONDS=0
		curl() {
			local output="" arg
			while [[ $# -gt 0 ]]; do
				arg=$1
				shift
				if [[ "${arg}" == "-o" ]]; then
					output=$1
					shift
				fi
			done
			printf x >>"${output}"
			return 18
		}
		download_bootstrap_file payload.bin "${destination}"
	) 2>"${tmp}/exhausted.log"; then
		printf 'download unexpectedly succeeded after retry exhaustion\n' >&2
		return 1
	fi
	[[ "$(wc -c <"${destination}.part")" -eq 3 ]]
	grep -F 'failed to download payload.bin after 3 attempts' "${tmp}/exhausted.log" >/dev/null
}

test_failed_staging_reports_and_preserves_sidecar() {
	local env_file="${tmp}/agent.env"
	local status_file="${tmp}/lifecycle-status" down_marker="${tmp}/sidecar-down"
	printf '%s\n' \
		'HFL_API_BASE=https://console.example' \
		'HFL_ORG_KEY=org-test' \
		'HFL_NODE_TOKEN=node-test' \
		'HFL_NODE_ID=42' \
		>"${env_file}"
	if (
		HFL_AGENT_ENV_FILE="${env_file}"
		# shellcheck disable=SC1090
		source "${LIFECYCLE}"
		report_lifecycle_status() {
			printf '%s:%s:%s\n' "$1" "$2" "${3:-}" >>"${status_file}"
		}
		ensure_docker_ready() { :; }
		download_bootstrap_file() { hfl_fail 'simulated staging download failure' 23; }
		compose_down_sidecar() { printf down >"${down_marker}"; }
		cmd_upgrade_sidecar
	); then
		printf 'Gateway sidecar staging failure unexpectedly succeeded\n' >&2
		return 1
	fi
	[[ ! -e "${down_marker}" ]]
	grep -Fx 'sidecar_upgrade:running:' "${status_file}" >/dev/null
	grep -Fx 'sidecar_upgrade:failed:simulated staging download failure' "${status_file}" >/dev/null
}

test_legacy_layout_adoption_is_retryable() (
	# shellcheck disable=SC1090
	source "${LIFECYCLE}"
	local legacy_root="${tmp}/legacy" agent_root="${tmp}/agent"
	LEGACY_LENS_ENV_FILE="${legacy_root}/lensnode.env"
	LEGACY_COMPOSE_DIR="${legacy_root}/lensnode"
	AGENT_ROOT="${agent_root}"
	LENS_ENV_FILE="${agent_root}/config/lensnode.env"
	COMPOSE_DIR="${agent_root}/runtime/lensnode"
	LEGACY_ADOPTION_MARKER="${COMPOSE_DIR}/.hfl-legacy-layout-adopted"
	LEGACY_MIGRATION_ENABLED=1
	mkdir -p "${LEGACY_COMPOSE_DIR}"
	printf '%s\n' 'LENSNODE_TOKEN=legacy' >"${LEGACY_LENS_ENV_FILE}"
	printf '%s\n' 'services: {}' >"${LEGACY_COMPOSE_DIR}/docker-compose.yml"

	migrate_legacy_layout
	cmp -s "${LEGACY_LENS_ENV_FILE}" "${LENS_ENV_FILE}"
	cmp -s "${LEGACY_COMPOSE_DIR}/docker-compose.yml" "${COMPOSE_DIR}/docker-compose.yml"
	[[ -f "${LEGACY_ADOPTION_MARKER}" ]]

	# A failed first sidecar start may leave the new control-plane config next
	# to the old file. The durable adoption marker makes the retry deterministic.
	printf '%s\n' 'LENSNODE_TOKEN=refreshed' >"${LENS_ENV_FILE}"
	migrate_legacy_layout
	grep -Fx 'LENSNODE_TOKEN=refreshed' "${LENS_ENV_FILE}" >/dev/null
)

test_sidecar_start_failure_restores_previous_compose() {
	local root="${tmp}/sidecar-rollback" compose_dir="${tmp}/sidecar-rollback/runtime/lensnode"
	local fake_compose="${tmp}/fake-compose" fake_docker="${tmp}/fake-docker"
	local calls="${tmp}/compose-calls" docker_calls="${tmp}/docker-calls" old_compose="${tmp}/old-compose"
	mkdir -p "${compose_dir}" "${root}/workspace/org-42/data"
	printf '%s\n' 'services:' '  lensnode:' '    image: previous:test' >"${old_compose}"
	cp "${old_compose}" "${compose_dir}/docker-compose.yml"
	cat >"${fake_compose}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${TEST_COMPOSE_CALLS}"
case "$*" in
  *" config --quiet") exit 0 ;;
  "-p hyperfilelens-gateway ps -q lensnode") printf '%s\n' current-lensnode ;;
  "-p hyperfilelens-gateway up -d --pull never --force-recreate") exit 17 ;;
  "-p hyperfilelens-gateway up -d --pull never") exit 0 ;;
  *) printf 'unexpected compose invocation: %s\n' "$*" >&2; exit 90 ;;
esac
EOF
	cat >"${fake_docker}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${TEST_DOCKER_CALLS}"
case "$*" in
  "image inspect desired:test") exit 0 ;;
  "image inspect --format {{.Id}} desired:test") printf '%s\n' sha256:desired ;;
  "inspect --format {{.Image}} current-lensnode") printf '%s\n' sha256:previous ;;
  "image tag sha256:previous desired:test") exit 0 ;;
  *) printf 'unexpected docker invocation: %s\n' "$*" >&2; exit 91 ;;
esac
EOF
	chmod 755 "${fake_compose}" "${fake_docker}"

	set +e
	(
		# Load only the function under test; the published installer normally
		# executes immediately after download.
		# shellcheck disable=SC1090
		source <(sed -n '/^install_docker_sidecar() {/,/^}/p' "${SIDECAR_INSTALLER}")
		COMPOSE_DIR="${compose_dir}"
		COMPOSE_PROJECT=hyperfilelens-gateway
		SENTRY_PRIVACY_FILE="${compose_dir}/hfl-sentry-sitecustomize.py"
		HFL_WORKSPACE_ROOT="${root}/workspace/org-42/data"
		HFL_SOURCELENS_MOUNTPOINT="${HFL_WORKSPACE_ROOT}/.sourcelens"
		HFL_SOURCELENS_STATE_ROOT="${root}/workspace/org-42/.hyperfilelens/sourcelens"
		HFL_GATEWAY_TRASH_ROOT="${HFL_WORKSPACE_ROOT}/.hyperfilelens-trash"
		LENSNODE_TOKEN=test-token
		LENSNODE_NAME=test-gateway
		LENS_CONTAINER_URL=https://host.docker.internal/sourcelens
		SENTRY_ENABLED=false
		HFL_INSECURE_TLS=1
		hfl_step() { :; }
		hfl_ok() { :; }
		hfl_warn() { printf '%s\n' "$*" >&2; }
		hfl_fail() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }
		lens_url_needs_extra_hosts() { return 0; }
		resolve_compose() { COMPOSE=("${fake_compose}"); }
		docker() { "${fake_docker}" "$@"; }
		remove_owned_legacy_gateway_containers() { printf removed >"${tmp}/legacy-removed-early"; }
		export TEST_COMPOSE_CALLS="${calls}"
		export TEST_DOCKER_CALLS="${docker_calls}"
		install_docker_sidecar desired:test
	) >"${tmp}/sidecar-rollback.log" 2>&1
	local status=$?
	set -e
	[[ "${status}" -eq 3 ]]
	cmp -s "${old_compose}" "${compose_dir}/docker-compose.yml"
	grep -Fx -- '-p hyperfilelens-gateway up -d --pull never --force-recreate' "${calls}" >/dev/null
	grep -Fx -- '-p hyperfilelens-gateway up -d --pull never' "${calls}" >/dev/null
	grep -Fx -- 'image tag sha256:previous desired:test' "${docker_calls}" >/dev/null
	[[ ! -e "${tmp}/legacy-removed-early" ]]
}

test_first_sidecar_start_failure_cleans_partial_project() {
	local root="${tmp}/sidecar-first-failure" compose_dir="${tmp}/sidecar-first-failure/runtime/lensnode"
	local fake_compose="${tmp}/fake-first-compose" fake_docker="${tmp}/fake-first-docker"
	local calls="${tmp}/first-compose-calls"
	mkdir -p "${compose_dir}" "${root}/workspace/org-42/data"
	cat >"${fake_compose}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${TEST_COMPOSE_CALLS}"
case "$*" in
  *" config --quiet") exit 0 ;;
  "-p hyperfilelens-gateway ps -q lensnode") printf '%s\n' partial-lensnode ;;
  "-p hyperfilelens-gateway up -d --pull never") exit 17 ;;
  "-p hyperfilelens-gateway down --remove-orphans") exit 0 ;;
  *) exit 90 ;;
esac
EOF
	cat >"${fake_docker}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
  "image inspect --format {{.Id}} desired:first") printf '%s\n' sha256:desired ;;
  *) exit 0 ;;
esac
EOF
	chmod 755 "${fake_compose}" "${fake_docker}"
	set +e
	(
		# shellcheck disable=SC1090
		source <(sed -n '/^install_docker_sidecar() {/,/^}/p' "${SIDECAR_INSTALLER}")
		COMPOSE_DIR="${compose_dir}"
		COMPOSE_PROJECT=hyperfilelens-gateway
		SENTRY_PRIVACY_FILE="${compose_dir}/hfl-sentry-sitecustomize.py"
		HFL_WORKSPACE_ROOT="${root}/workspace/org-42/data"
		HFL_SOURCELENS_MOUNTPOINT="${HFL_WORKSPACE_ROOT}/.sourcelens"
		HFL_SOURCELENS_STATE_ROOT="${root}/workspace/org-42/.hyperfilelens/sourcelens"
		HFL_GATEWAY_TRASH_ROOT="${HFL_WORKSPACE_ROOT}/.hyperfilelens-trash"
		LENSNODE_TOKEN=test-token LENSNODE_NAME=test-gateway
		LENS_CONTAINER_URL=https://host.docker.internal/sourcelens SENTRY_ENABLED=false HFL_INSECURE_TLS=1
		hfl_step() { :; }
		hfl_ok() { :; }
		hfl_warn() { :; }
		hfl_fail() { exit "${2:-1}"; }
		lens_url_needs_extra_hosts() { return 0; }
		resolve_compose() { COMPOSE=("${fake_compose}"); }
		docker() { "${fake_docker}" "$@"; }
		remove_owned_legacy_gateway_containers() { :; }
		export TEST_COMPOSE_CALLS="${calls}"
		install_docker_sidecar desired:first
	) >/dev/null 2>&1
	local status=$?
	set -e
	[[ "${status}" -eq 3 ]]
	[[ ! -e "${compose_dir}/docker-compose.yml" ]]
	grep -Fx -- '-p hyperfilelens-gateway down --remove-orphans' "${calls}" >/dev/null
}

test_upgrade_keeps_existing_sidecar_until_replacement_starts() {
	local body
	body="$(sed -n '/^cmd_upgrade_sidecar() {/,/^}/p' "${LIFECYCLE}")"
	if grep -Eq '^[[:space:]]*compose_down_sidecar([[:space:]]|$)' <<<"${body}"; then
		printf 'upgrade tears down the existing sidecar before replacement startup\n' >&2
		return 1
	fi
	grep -Eq '^[[:space:]]*run_sidecar_install_script([[:space:]]|$)' <<<"${body}"
}

test_lifecycle_accepts_persisted_node_credential() (
	# shellcheck disable=SC1090
	source "${LIFECYCLE}"
	ENV_FILE="${tmp}/credential-agent.env"
	cat >"${ENV_FILE}" <<'EOF'
HFL_API_BASE=https://console.example
HFL_ORG_KEY=org-test
HFL_NODE_CREDENTIAL=persisted-credential
HFL_NODE_ID=42
HFL_AGENT_ROOT=/opt/hyperfilelens-agent
EOF
	load_agent_credentials_optional
	[[ "${HFL_NODE_TOKEN}" == "persisted-credential" ]]
	[[ "${LENS_ENV_FILE}" == "/opt/hyperfilelens-agent/config/lensnode.env" ]]
	[[ "${COMPOSE_DIR}" == "/opt/hyperfilelens-agent/runtime/lensnode" ]]
)

test_lifecycle_rejects_relative_persisted_agent_root() (
	# shellcheck disable=SC1090
	source "${LIFECYCLE}"
	ENV_FILE="${tmp}/relative-root-agent.env"
	cat >"${ENV_FILE}" <<'EOF'
HFL_API_BASE=https://console.example
HFL_ORG_KEY=org-test
HFL_NODE_CREDENTIAL=persisted-credential
HFL_NODE_ID=42
HFL_AGENT_ROOT=relative/agent-root
EOF
	set +e
	(load_agent_credentials_optional) >"${tmp}/relative-root.log" 2>&1
	local status=$?
	set -e
	[[ "${status}" -eq 2 ]]
	grep -F 'invalid relative HFL_AGENT_ROOT' "${tmp}/relative-root.log" >/dev/null
)

test_lifecycle_rejects_relative_agent_env_path() (
	# shellcheck disable=SC1090
	source "${LIFECYCLE}"
	ENV_FILE="relative-agent.env"
	set +e
	(validate_lifecycle_paths) >"${tmp}/relative-env.log" 2>&1
	local status=$?
	set -e
	[[ "${status}" -eq 2 ]]
	grep -F 'HFL_AGENT_ENV_FILE must be an absolute file path' "${tmp}/relative-env.log" >/dev/null
)

test_legacy_agent_env_enables_layout_migration() {
	local enabled
	enabled="$(
		HFL_AGENT_ENV_FILE=/var/lib/hyperfilelens-agent/agent.env \
			bash -c 'source "$1"; printf "%s" "${LEGACY_MIGRATION_ENABLED}"' \
			_ "${LIFECYCLE}"
	)"
	[[ "${enabled}" == "1" ]]
}

test_custom_agent_root_does_not_enable_global_legacy_migration() {
	local enabled
	enabled="$(
		HFL_AGENT_ROOT=/srv/custom-hfl-agent \
			HFL_AGENT_ENV_FILE=/srv/custom-hfl-agent/config/agent.env \
			bash -c 'source "$1"; printf "%s" "${LEGACY_MIGRATION_ENABLED}"' \
			_ "${LIFECYCLE}"
	)"
	[[ "${enabled}" == "0" ]]
	enabled="$(
		HFL_AGENT_ROOT=/srv/custom-hfl-agent \
			HFL_AGENT_ENV_FILE=/opt/hyperfilelens-agent/config/agent.env \
			bash -c 'source "$1"; printf "%s" "${LEGACY_MIGRATION_ENABLED}"' \
			_ "${LIFECYCLE}"
	)"
	[[ "${enabled}" == "0" ]]
	enabled="$(
		HFL_AGENT_ENV_FILE=/opt/hyperfilelens-agent/config/agent.env \
		HFL_LENS_ENV_FILE=/srv/custom-hfl/lensnode.env \
		HFL_GATEWAY_COMPOSE_DIR=/srv/custom-hfl/lensnode \
		HFL_LEGACY_LAYOUT_ADOPTED=1 \
			bash -c 'source "$1"; printf "%s" "${LEGACY_MIGRATION_ENABLED}"' \
			_ "${LIFECYCLE}"
	)"
	[[ "${enabled}" == "0" ]]
}

test_custom_lensnode_paths_do_not_enable_global_legacy_migration() {
	local enabled
	enabled="$(
		HFL_AGENT_ENV_FILE=/opt/hyperfilelens-agent/config/agent.env \
		HFL_LENS_ENV_FILE=/srv/custom-hfl/lensnode.env \
		HFL_GATEWAY_COMPOSE_DIR=/srv/custom-hfl/lensnode \
			bash -c 'source "$1"; printf "%s" "${LEGACY_MIGRATION_ENABLED}"' \
			_ "${LIFECYCLE}"
	)"
	[[ "${enabled}" == "0" ]]
}

test_sidecar_custom_path_does_not_touch_global_legacy_layout() (
	local sidecar="${ROOT}/deploy/bootstrap/gateway-install-lensnode-sidecar.sh"
	local legacy_env="${tmp}/global-legacy/lensnode.env" legacy_compose="${tmp}/global-legacy/lensnode"
	local current_env="${tmp}/custom-agent/config/lensnode.env" current_compose="${tmp}/custom-agent/runtime/lensnode"
	mkdir -p "$(dirname "${legacy_env}")" "${legacy_compose}" "$(dirname "${current_env}")" "${current_compose}"
	printf '%s\n' legacy >"${legacy_env}"
	printf '%s\n' 'services: {}' >"${legacy_compose}/docker-compose.yml"
	# shellcheck disable=SC1090
	source <(sed -n '/^migrate_legacy_layout() {/,/^}/p' "${sidecar}")
	# shellcheck disable=SC1090
	source <(sed -n '/^cleanup_legacy_layout() {/,/^}/p' "${sidecar}")
	ENV_FILE="${current_env}"
	COMPOSE_DIR="${current_compose}"
	LEGACY_ENV_FILE="${legacy_env}"
	LEGACY_COMPOSE_DIR="${legacy_compose}"
	LEGACY_ADOPTION_MARKER="${current_compose}/.hfl-legacy-layout-adopted"
	LEGACY_MIGRATION_ENABLED=0
	migrate_legacy_layout
	cleanup_legacy_layout
	[[ -f "${legacy_env}" && -f "${legacy_compose}/docker-compose.yml" ]]
	[[ ! -e "${current_env}" && ! -e "${current_compose}/docker-compose.yml" ]]
)

test_sidecar_custom_marker_does_not_enable_global_legacy_migration() {
	local enabled
	enabled="$(
		HFL_LENS_ENV_FILE=/srv/custom-hfl/lensnode.env \
		HFL_GATEWAY_COMPOSE_DIR=/srv/custom-hfl/lensnode \
		HFL_LEGACY_LAYOUT_ADOPTED=1 \
			bash -c 'source <(sed -n "/^legacy_migration_allowed_for_paths() {/,/^}/p" "$1"); ENV_FILE="$HFL_LENS_ENV_FILE" COMPOSE_DIR="$HFL_GATEWAY_COMPOSE_DIR"; if legacy_migration_allowed_for_paths; then printf 1; else printf 0; fi' \
			_ "${SIDECAR_INSTALLER}"
	)"
	[[ "${enabled}" == "0" ]]
}

test_custom_layout_does_not_remove_global_legacy_container() (
	# shellcheck disable=SC1090
	source "${LIFECYCLE}"
	local removed="${tmp}/legacy-container-removed"
	COMPOSE_DIR="${tmp}/custom-agent/runtime/lensnode"
	LEGACY_COMPOSE_DIR="${tmp}/global-legacy/lensnode"
	LEGACY_MIGRATION_ENABLED=0
	remember_owned_lensnode_image() { :; }
	docker() {
		case "$*" in
		"ps -aq --no-trunc") printf '%s\n' legacy-lensnode ;;
		"inspect --format {{index .Config.Labels \"com.docker.compose.project\"}} legacy-lensnode") printf '%s\n' sourcelens ;;
		"inspect --format {{index .Config.Labels \"com.docker.compose.service\"}} legacy-lensnode") printf '%s\n' lensnode ;;
		"inspect --format {{index .Config.Labels \"com.docker.compose.project.working_dir\"}} legacy-lensnode") printf '%s\n' "${LEGACY_COMPOSE_DIR}" ;;
		"inspect --format {{index .Config.Labels \"com.docker.compose.project.config_files\"}} legacy-lensnode") printf '%s\n' "${LEGACY_COMPOSE_DIR}/docker-compose.yml" ;;
		"inspect --format {{.Config.Image}} legacy-lensnode") printf '%s\n' legacy:test ;;
		"rm -f legacy-lensnode") printf removed >"${removed}" ;;
		*) printf 'unexpected fake Docker invocation: %s\n' "$*" >&2; return 90 ;;
		esac
	}
	remove_owned_legacy_gateway_containers
	[[ ! -e "${removed}" ]]
	LEGACY_MIGRATION_ENABLED=1
	remove_owned_legacy_gateway_containers
	[[ -f "${removed}" ]]
)

test_uninstall_without_credentials_purges_legacy_layout() (
	# shellcheck disable=SC1090
	source "${LIFECYCLE}"
	local legacy_root="${tmp}/uninstall-legacy" agent_root="${tmp}/uninstall-agent"
	ENV_FILE="${agent_root}/config/agent.env"
	LEGACY_LENS_ENV_FILE="${legacy_root}/lensnode.env"
	LEGACY_COMPOSE_DIR="${legacy_root}/lensnode"
	AGENT_ROOT="${agent_root}"
	LENS_ENV_FILE="${agent_root}/config/lensnode.env"
	COMPOSE_DIR="${agent_root}/runtime/lensnode"
	LEGACY_ADOPTION_MARKER="${COMPOSE_DIR}/.hfl-legacy-layout-adopted"
	LEGACY_MIGRATION_ENABLED=1
	PURGE_ALL=1
	mkdir -p "${LEGACY_COMPOSE_DIR}"
	printf '%s\n' "HFL_WORKSPACE_ROOT=${agent_root}/workspace/org-42/data" \
		>"${LEGACY_LENS_ENV_FILE}"
	printf '%s\n' 'services: {}' >"${LEGACY_COMPOSE_DIR}/docker-compose.yml"
	ensure_docker_ready() { :; }
	acquire_sidecar_lock() { :; }
	collect_mount_targets() { :; }
	compose_down_sidecar() { :; }
	remove_lensnode_images() { :; }

	cmd_uninstall_sidecar

	[[ ! -e "${LENS_ENV_FILE}" ]]
	[[ ! -e "${COMPOSE_DIR}" ]]
	[[ ! -e "${LEGACY_LENS_ENV_FILE}" ]]
	[[ ! -e "${LEGACY_COMPOSE_DIR}" ]]
)

test_resume_after_interruption
test_retry_exhaustion_keeps_partial
test_failed_staging_reports_and_preserves_sidecar
test_legacy_layout_adoption_is_retryable
test_sidecar_start_failure_restores_previous_compose
test_first_sidecar_start_failure_cleans_partial_project
test_upgrade_keeps_existing_sidecar_until_replacement_starts
test_lifecycle_accepts_persisted_node_credential
test_lifecycle_rejects_relative_persisted_agent_root
test_lifecycle_rejects_relative_agent_env_path
test_legacy_agent_env_enables_layout_migration
test_custom_agent_root_does_not_enable_global_legacy_migration
test_custom_lensnode_paths_do_not_enable_global_legacy_migration
test_sidecar_custom_path_does_not_touch_global_legacy_layout
test_sidecar_custom_marker_does_not_enable_global_legacy_migration
test_custom_layout_does_not_remove_global_legacy_container
test_uninstall_without_credentials_purges_legacy_layout

printf 'Gateway sidecar resumable upgrade contracts passed.\n'
