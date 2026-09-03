#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
installer="${ROOT}/src/agent/packaging/install/install.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

# Load the installed Agent command functions without dispatching the real CLI.
# shellcheck disable=SC1090
source <(sed '/^case "$CMD" in/,$d' "${installer}")

AGENT_ROOT="${tmp}/opt/hyperfilelens-agent"
INSTALL_DIR="${AGENT_ROOT}/bin"
DEFAULT_DATA="${AGENT_ROOT}"
install_parent="$(dirname "${INSTALL_DIR}")"
data_parent="$(dirname "${DEFAULT_DATA}")"
INSTALLED_VERSION_FILE="${AGENT_ROOT}/INSTALLED_VERSION"
MANIFEST_FILE="${AGENT_ROOT}/MANIFEST.json"
GATEWAY_LIFECYCLE_SCRIPT="${INSTALL_DIR}/libexec/gateway-lifecycle.sh"
UNIT_DST="${tmp}/hyperfilelens-agent.service"
GATEWAY_RESOURCE_DROPIN="${tmp}/20-gateway-resources.conf"
marker="${tmp}/sidecar-removed"
stop_marker="${tmp}/agent-stopped"

mkdir -p "${INSTALL_DIR}/libexec" "${DEFAULT_DATA}/config"
printf '%s\n' \
	'HFL_NODE_ROLE=gateway' \
	"HFL_DATA_DIR=${DEFAULT_DATA}" \
	>"${DEFAULT_DATA}/config/agent.env"
printf agent >"${INSTALL_DIR}/hfl-agent"
printf '%s\n' \
	'#!/usr/bin/env bash' \
	'set -euo pipefail' \
	'[[ "$HFL_AGENT_ENV_FILE" == "$TEST_AGENT_ENV" ]]' \
	'[[ "$1" == "uninstall-sidecar" && "$2" == "--purge-all" ]]' \
	'printf removed >"$TEST_SIDECAR_MARKER"' \
	>"${GATEWAY_LIFECYCLE_SCRIPT}"
chmod 755 "${GATEWAY_LIFECYCLE_SCRIPT}"

require_root() { :; }
begin_uninstall_log() { :; }
finish_uninstall_log() { :; }
log_info() { :; }
log_step() { :; }
log_ok() { :; }
log_skip() { :; }
log_warn() { :; }
agent_uses_launchd() { return 1; }
stop_service() {
	[[ -f "${marker}" ]]
	printf stopped >"${stop_marker}"
}
remove_service_unit() { :; }
data_dir_allowed_for_removal() { [[ "$1" == "${DEFAULT_DATA}" || "$1" == "${INSTALL_DIR}" ]]; }
unmount_agent_mounts() { :; }

export TEST_AGENT_ENV="${DEFAULT_DATA}/config/agent.env"
export TEST_SIDECAR_MARKER="${marker}"

# Preserving data retires the installation identity. A missing Agent binary
# must fail during preflight, before the service or Gateway sidecar is touched.
set +e
(
	exec 3>&1 4>&2
	cmd_uninstall --keep-data
) >"${tmp}/uninstall-preflight.log" 2>&1
preflight_status=$?
set -e
[[ "${preflight_status}" -eq 1 ]]
grep -F 'Cannot retire the installation identity' "${tmp}/uninstall-preflight.log" >/dev/null
[[ ! -e "${marker}" ]]
[[ ! -e "${stop_marker}" ]]

original_gateway_mount_detector="$(declare -f gateway_workspace_mounts_in_agent_root)"
gateway_workspace_mounts_in_agent_root() {
	printf '%s\n' "${DEFAULT_DATA}/workspace"
}
set +e
(
	exec 3>&1 4>&2
	cmd_uninstall
) >"${tmp}/mounted-workspace-preflight.log" 2>&1
mounted_preflight_status=$?
set -e
[[ "${mounted_preflight_status}" -eq 2 ]]
grep -F 'Refusing complete removal while Gateway workspace storage is mounted' \
	"${tmp}/mounted-workspace-preflight.log" >/dev/null
[[ -f "${DEFAULT_DATA}/config/agent.env" ]]
[[ ! -e "${marker}" ]]
[[ ! -e "${stop_marker}" ]]

gateway_workspace_mounts_in_agent_root() { return 1; }
set +e
(
	exec 3>&1 4>&2
	cmd_uninstall --purge-all
) >"${tmp}/mount-scan-failure.log" 2>&1
mount_scan_status=$?
set -e
[[ "${mount_scan_status}" -eq 2 ]]
grep -F 'Could not verify Gateway workspace mounts; refusing complete removal' \
	"${tmp}/mount-scan-failure.log" >/dev/null
[[ -f "${DEFAULT_DATA}/config/agent.env" ]]
[[ ! -e "${marker}" ]]
[[ ! -e "${stop_marker}" ]]
eval "${original_gateway_mount_detector}"

cmd_uninstall

[[ -f "${marker}" ]]
[[ -f "${stop_marker}" ]]
[[ ! -e "${INSTALL_DIR}" ]]
[[ ! -e "${DEFAULT_DATA}" ]]
[[ ! -e "${install_parent}" ]]
[[ -d "${data_parent}" ]]

fake_bin="${tmp}/fake-bin"
docker_state="${tmp}/docker-state"
compose_dir="${tmp}/lensnode-compose"
mkdir -p "${fake_bin}" "${compose_dir}"
printf 'services: {}\n' >"${compose_dir}/docker-compose.yml"
cat >"${fake_bin}/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
  info) exit 0 ;;
  "compose version --short"|"compose version") printf '%s\n' '2.39.0' ;;
  "ps -aq --no-trunc") exit 0 ;;
  "compose -p hyperfilelens-gateway config --images")
    printf '%s\n' 'example/hfl-lensnode:test'
    ;;
  "compose -p hyperfilelens-gateway down --remove-orphans")
    printf down >"${TEST_DOCKER_STATE}.down"
    ;;
  "ps -aq --filter label=com.hyperfilelens.managed=true --filter label=com.hyperfilelens.component=gateway-lensnode")
    [[ -f "${TEST_DOCKER_STATE}.removed" ]] || printf '%s\n' owned-lensnode
    ;;
  "inspect --format {{.Config.Image}} owned-lensnode")
    printf '%s\n' 'example/hfl-lensnode:test'
    ;;
  "rm -f owned-lensnode")
    printf removed >"${TEST_DOCKER_STATE}.removed"
    ;;
  "ps -aq --filter ancestor=hyperfilelens-sourcelens-lensnode:latest"|\
  "ps -aq --filter ancestor=example/hfl-lensnode:test")
    exit 0
    ;;
  "image rm hyperfilelens-sourcelens-lensnode:latest"|\
  "image rm example/hfl-lensnode:test")
    printf '%s\n' "$*" >>"${TEST_DOCKER_STATE}.images"
    ;;
  *)
    printf 'unexpected docker invocation: %s\n' "$*" >&2
    exit 90
    ;;
esac
EOF
chmod 755 "${fake_bin}/docker"

PATH="${fake_bin}:${PATH}" \
	TEST_DOCKER_STATE="${docker_state}" \
	HFL_AGENT_ENV_FILE="${tmp}/missing-agent.env" \
	HFL_LENS_ENV_FILE="${tmp}/missing-lensnode.env" \
	HFL_GATEWAY_COMPOSE_DIR="${compose_dir}" \
	HFL_GATEWAY_SIDECAR_LOCK_FILE="${tmp}/gateway-sidecar.lock" \
	bash "${ROOT}/deploy/bootstrap/gateway-lifecycle.sh" uninstall-sidecar --purge-all

[[ -f "${docker_state}.down" ]]
[[ -f "${docker_state}.removed" ]]
[[ ! -e "${compose_dir}" ]]
grep -Fx 'image rm hyperfilelens-sourcelens-lensnode:latest' "${docker_state}.images" >/dev/null
grep -Fx 'image rm example/hfl-lensnode:test' "${docker_state}.images" >/dev/null

mounted_root="${tmp}/mounted-agent"
mounted_env="${mounted_root}/config/lensnode.env"
mounted_compose="${mounted_root}/runtime/lensnode"
mounted_workspace="${mounted_root}/workspace/org-42/data"
mkdir -p "$(dirname "${mounted_env}")" "${mounted_compose}" "${mounted_workspace}"
printf 'HFL_WORKSPACE_ROOT=%s\n' "${mounted_workspace}" >"${mounted_env}"
printf 'services: {}\n' >"${mounted_compose}/docker-compose.yml"
set +e
(
	# shellcheck disable=SC1090
	source "${ROOT}/deploy/bootstrap/gateway-lifecycle.sh"
	AGENT_ROOT="${mounted_root}"
	LENS_ENV_FILE="${mounted_env}"
	COMPOSE_DIR="${mounted_compose}"
	LEGACY_MIGRATION_ENABLED=0
	collect_mount_targets() { printf '%s\n' "${mounted_root}/workspace"; }
	compose_down_sidecar() { printf down >"${mounted_root}/compose-down"; }
	remove_lensnode_images() { printf images >"${mounted_root}/images-removed"; }
	purge_sidecar_artifacts
) >"${tmp}/mounted-sidecar-purge.log" 2>&1
mounted_sidecar_status=$?
set -e
[[ "${mounted_sidecar_status}" -eq 6 ]]
grep -F 'refusing to purge mounted Gateway workspace data' \
	"${tmp}/mounted-sidecar-purge.log" >/dev/null
[[ -f "${mounted_env}" ]]
[[ -f "${mounted_compose}/docker-compose.yml" ]]
[[ ! -e "${mounted_root}/compose-down" ]]
[[ ! -e "${mounted_root}/images-removed" ]]

validate_workspace() {
	bash -c 'source "$1"; validate_gateway_workspace_path "$2"' \
		_ "${ROOT}/deploy/bootstrap/gateway-lifecycle.sh" "$1"
}

[[ "$(validate_workspace /workspace/org-42/data)" == "/workspace/org-42/data" ]]
[[ "$(validate_workspace /workspace/org-42/data/)" == "/workspace/org-42/data" ]]
[[ "$(validate_workspace /opt/hyperfilelens-agent/workspace/org-42/data)" == "/opt/hyperfilelens-agent/workspace/org-42/data" ]]

legacy_state_mount="$(
	# shellcheck disable=SC1090
	source "${ROOT}/deploy/bootstrap/gateway-lifecycle.sh"
	AGENT_ROOT=/opt/hyperfilelens-agent
	collect_mount_targets() { printf '%s\n' /workspace/org-42/.hyperfilelens/sourcelens; }
	gateway_workspace_mounts /workspace/org-42/data
)"
[[ "${legacy_state_mount}" == "/workspace/org-42/.hyperfilelens/sourcelens" ]]

for unsafe_workspace in \
	/workspace/org-0/data \
	/workspace/org-alpha/data \
	/workspace/org-42 \
	/workspace/org-42/data/child \
	/workspace/org-42/data/../secrets \
	/workspace/../etc \
	/; do
	if validate_workspace "${unsafe_workspace}" >/dev/null 2>&1; then
		printf 'unsafe Gateway workspace path accepted: %s\n' "${unsafe_workspace}" >&2
		exit 1
	fi
done

printf 'Agent-managed Data Gateway uninstall contracts passed.\n'
