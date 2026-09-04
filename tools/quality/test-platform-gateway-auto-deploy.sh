#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
installer="${ROOT_REPO}/deploy/installer/install.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

# Load only the dotenv boolean parser and platform Gateway deployment functions.
# shellcheck disable=SC1090
source <(sed -n '/^read_env_value()/,/^resolve_console_host()/p' "${installer}" | sed '$d')
# shellcheck disable=SC1090
source <(sed -n '/^platform_gateway_auto_deploy_enabled()/,/^# --- Commands ---/p' "${installer}" | sed '$d')

[[ "$(grep -Fc 'ok "Platform Data Gateway is online and usable"' "${installer}")" -eq 1 ]]

ROOT="${tmp}/install"
LOCAL_PLATFORM_AGENT_INSTALL_DIR="${tmp}/agent-install"
LOCAL_PLATFORM_AGENT_DATA_DIR="${tmp}/agent-data"
LOCAL_PLATFORM_AGENT_LEGACY_INSTALL_DIR="${tmp}/legacy-agent-install"
LOCAL_PLATFORM_AGENT_LEGACY_DATA_DIR="${tmp}/legacy-agent-data"
LOCAL_PLATFORM_AGENT_SYSTEMD_UNIT_FILE="${tmp}/hyperfilelens-agent.service"
LOCAL_PLATFORM_LENSNODE_ENV_FILE="${tmp}/lensnode.env"
LOCAL_PLATFORM_LEGACY_LENSNODE_ENV_FILE="${tmp}/legacy-lensnode.env"
LOCAL_PLATFORM_LENSNODE_COMPOSE_DIR="${tmp}/lensnode-compose"
LOCAL_PLATFORM_LEGACY_LENSNODE_COMPOSE_DIR="${tmp}/legacy-lensnode-compose"
LOCAL_PLATFORM_LENSNODE_IMAGE="hyperfilelens-sourcelens-lensnode:latest"
mkdir -p \
	"${ROOT}/data/media/enroll-bootstrap" \
	"${ROOT}/data/media/agent-releases" \
	"${ROOT}/data/media/gateway-bootstrap" \
	"${LOCAL_PLATFORM_AGENT_INSTALL_DIR}" \
	"${LOCAL_PLATFORM_AGENT_DATA_DIR}"
helper="${ROOT}/data/media/enroll-bootstrap/hfl-enroll-linux-amd64"
marker="${tmp}/helper-ran"
cat >"${helper}" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "$HFL_ORG_KEY" == "__platform_lens__" ]]
[[ "$HFL_NODE_ROLE" == "gateway" ]]
[[ "$HFL_API_BASE" == "https://127.0.0.1:11443" ]]
[[ "$HFL_WSS_URL" == "wss://127.0.0.1:11443/ws/node/agent/" ]]
[[ "$HFL_FORCE_SIDECAR_INSTALL" == "1" ]]
[[ -z "${SENTRY_ENABLED+x}" ]]
[[ -z "${SENTRY_BACKEND_DSN+x}" ]]
[[ -z "${HFL_SENTRY_POLICY_MANAGED+x}" ]]
[[ "$1" == "gateway-install" && "$2" == "--yes" ]]
mkdir -p \
	"$TEST_AGENT_INSTALL_DIR" \
	"$TEST_AGENT_DATA_DIR/config" \
	"$TEST_AGENT_DATA_DIR/data"
if [[ ! -f "$TEST_AGENT_DATA_DIR/INSTALLED_VERSION" ]]; then
	printf '%s\n' "$TEST_DESIRED_VERSION" >"$TEST_AGENT_DATA_DIR/INSTALLED_VERSION"
fi
touch "$TEST_AGENT_DATA_DIR/data/agent.db"
cat >"$TEST_AGENT_DATA_DIR/config/agent.env" <<EOF
HFL_ORG_KEY=__platform_lens__
HFL_NODE_ROLE=gateway
HFL_NODE_ID=99
HFL_NODE_TOKEN=fixture-token
EOF
printf '%s|%s|%s' "$HFL_API_BASE" "$HFL_WSS_URL" "$HFL_INSECURE_TLS" >"$TEST_PLATFORM_GATEWAY_MARKER"
SH
chmod 755 "${helper}"

cat >"${LOCAL_PLATFORM_AGENT_INSTALL_DIR}/install.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == "upgrade" && "$2" == "--from" ]]
[[ "${TEST_AGENT_UPGRADE_FAIL:-0}" != "1" ]] || exit 42
printf '%s\n' "$3" >"$TEST_AGENT_UPGRADE_MARKER"
printf '%s\n' "$TEST_DESIRED_VERSION" >"$TEST_AGENT_DATA_DIR/INSTALLED_VERSION"
SH
chmod 755 "${LOCAL_PLATFORM_AGENT_INSTALL_DIR}/install.sh"

export TEST_PLATFORM_GATEWAY_MARKER="${marker}"
export TEST_AGENT_UPGRADE_MARKER="${tmp}/agent-upgrade-ran"
export TEST_AGENT_INSTALL_DIR="${LOCAL_PLATFORM_AGENT_INSTALL_DIR}"
export TEST_AGENT_DATA_DIR="${LOCAL_PLATFORM_AGENT_DATA_DIR}"
export SENTRY_ENABLED=true
export SENTRY_BACKEND_DSN=https://untrusted@sentry.example.com/25
export HFL_SENTRY_POLICY_MANAGED=true

AUTO_DEPLOY=false
TLS_MODE=1
AGENT_ACTIVE=1
READINESS_OK=1
LOCAL_GATEWAY_IS_DEFAULT=0
READINESS_QUERY=""
READINESS_WAIT_CALLS=0
read_env_value() {
	case "$1" in
	HFL_PLATFORM_GATEWAY_AUTO_DEPLOY) printf '%s' "${AUTO_DEPLOY}" ;;
	HFL_INSECURE_TLS) printf '%s' "${TLS_MODE}" ;;
	HFL_TENANT_PORT) printf '11443' ;;
	esac
}
read_version() { tr -d ' \t\r\n' <"${ROOT}/VERSION"; }
skip() { :; }
step() { :; }
ok() { :; }
die() { printf 'FAIL: %s\n' "$1" >&2; exit "${2:-1}"; }
require_root_or_sudo() { :; }
require_docker() { :; }
run_as_root() { "$@"; }
systemctl() {
	[[ "$*" == "is-active --quiet hyperfilelens-agent.service" ]] \
		&& [[ "${AGENT_ACTIVE}" == "1" ]]
}
converge_local_platform_gateway_lensnode() { :; }
wait_for_local_platform_gateway_readiness() {
	[[ "$1" == "180" ]]
	READINESS_WAIT_CALLS=$((READINESS_WAIT_CALLS + 1))
	LOCAL_PLATFORM_GATEWAY_READINESS_REASON=""
	if [[ "${READINESS_OK}" == "1" ]]; then
		return 0
	fi
	LOCAL_PLATFORM_GATEWAY_READINESS_REASON="fixture Gateway is not fully ready"
	return 1
}
active_api_service() { printf 'api-blue'; }
compose_in_root() {
	if [[ "$*" == *"gateway_runtime_state"* ]]; then
		READINESS_QUERY="$*"
		if [[ "${LOCAL_GATEWAY_IS_DEFAULT}" != "1" \
			&& "$*" == *"is_platform_default=True"* ]]; then
			return 1
		fi
		[[ "${READINESS_OK}" == "1" ]]
		return
	fi
	printf 'HFL_LOCAL_PLATFORM_GATEWAY_ENROLLMENT={"org_key":"%s","token":"fixture-token","api_base":"https://console.example:11443","wss_url":"wss://console.example:11443/ws/node/agent/","managed_node_ids":[99]}\n' "${ENROLLMENT_ORG}"
}

ENROLLMENT_ORG=__platform_lens__
export TEST_DESIRED_VERSION=main-1111111
printf '%s\n' "${TEST_DESIRED_VERSION}" >"${ROOT}/VERSION"

# Fresh installs must reject conflicting Agents before any Docker preparation,
# while preserving installer-managed platform Gateways for the final ownership
# check performed by ensure_local_platform_gateway.
AUTO_DEPLOY=true
agent_installer_fixture="${LOCAL_PLATFORM_AGENT_INSTALL_DIR}/install.sh"
mv "${agent_installer_fixture}" "${tmp}/agent-install.sh"
preflight_local_platform_gateway_agent_conflict

printf '0.1.0\n' >"${LOCAL_PLATFORM_AGENT_DATA_DIR}/INSTALLED_VERSION"
if (preflight_local_platform_gateway_agent_conflict) 2>/dev/null; then
	printf 'ERROR: preflight accepted Agent artifacts without trusted ownership metadata\n' >&2
	exit 1
fi
rm -f "${LOCAL_PLATFORM_AGENT_DATA_DIR}/INSTALLED_VERSION"

touch "${LOCAL_PLATFORM_AGENT_SYSTEMD_UNIT_FILE}"
if (preflight_local_platform_gateway_agent_conflict) 2>/dev/null; then
	printf 'ERROR: preflight accepted an existing Agent systemd unit\n' >&2
	exit 1
fi
rm -f "${LOCAL_PLATFORM_AGENT_SYSTEMD_UNIT_FILE}"

canonical_env="${LOCAL_PLATFORM_AGENT_DATA_DIR}/config/agent.env"
mkdir -p "$(dirname "${canonical_env}")"
cat >"${canonical_env}" <<'EOF'
HFL_ORG_KEY=tenant-org
HFL_NODE_ROLE=agent
HFL_NODE_ID=17
EOF
conflict_before="$(sha256sum "${canonical_env}")"
conflict_output_file="${tmp}/preflight-conflict-output"
if (preflight_local_platform_gateway_agent_conflict) >"${conflict_output_file}" 2>&1; then
	printf 'ERROR: preflight accepted a conflicting Agent installation\n' >&2
	exit 1
fi
conflict_output="$(<"${conflict_output_file}")"
[[ "${conflict_output}" == "FAIL: Cannot install the Platform Data Gateway because a HyperFileLens Agent is already installed on this host. Uninstall the existing Agent and run the installer again, or use another host without a HyperFileLens Agent. No changes were made to the existing Agent, Docker services, or configuration" ]]
[[ "$(sha256sum "${canonical_env}")" == "${conflict_before}" ]]

AUTO_DEPLOY=false
preflight_local_platform_gateway_agent_conflict
AUTO_DEPLOY=true

cat >"${canonical_env}" <<'EOF'
HFL_ORG_KEY=__platform_lens__
HFL_NODE_ROLE=gateway
HFL_NODE_ID=99
HFL_NODE_TOKEN=fixture-token
EOF
preflight_local_platform_gateway_agent_conflict
rm -f "${canonical_env}"

mkdir -p "${LOCAL_PLATFORM_AGENT_LEGACY_DATA_DIR}"
cat >"${LOCAL_PLATFORM_AGENT_LEGACY_DATA_DIR}/agent.env" <<'EOF'
HFL_ORG_KEY=legacy-tenant
HFL_NODE_ROLE=gateway
EOF
if (preflight_local_platform_gateway_agent_conflict) 2>/dev/null; then
	printf 'ERROR: preflight accepted a conflicting legacy Agent installation\n' >&2
	exit 1
fi
rm -f "${LOCAL_PLATFORM_AGENT_LEGACY_DATA_DIR}/agent.env"
mv "${tmp}/agent-install.sh" "${agent_installer_fixture}"

python3 - "${installer}" <<'PY'
import pathlib
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
install = text.split("cmd_install() {", 1)[1].split("cmd_platform_gateway() {", 1)[0]
preflight = install.index("preflight_local_platform_gateway_agent_conflict")
for operation in (
    "init_install_root",
    "preflight_package_layout",
    "stack_containers_present",
    "ensure_host_docker",
    "ensure_bridge_network",
    "load_images_from_manifest",
    "install_bundled_sourcelens",
    "start_hfl_stack",
):
    if preflight >= install.index(operation):
        raise SystemExit(f"Agent conflict preflight runs after {operation}")
if preflight >= install.index("ensure_local_platform_gateway"):
    raise SystemExit("Agent conflict preflight replaced the final Gateway ownership check")
PY

# Enabling auto-deploy on an existing control plane must not spend the upgrade
# recovery window waiting for a Gateway that has never been installed.
AUTO_DEPLOY=true
READINESS_WAIT_CALLS=0
check_local_platform_gateway_continuity
[[ "${READINESS_WAIT_CALLS}" == "0" ]]

AUTO_DEPLOY=false
ensure_local_platform_gateway
[[ ! -e "${marker}" ]]

AUTO_DEPLOY=true
ensure_local_platform_gateway
[[ "$(<"${marker}")" == "https://127.0.0.1:11443|wss://127.0.0.1:11443/ws/node/agent/|1" ]]
[[ "$(local_platform_gateway_installed_agent_version)" == "main-1111111" ]]
[[ ! -e "${TEST_AGENT_UPGRADE_MARKER}" ]]

READINESS_OK=0
if (ensure_local_platform_gateway) 2>/dev/null; then
	printf 'ERROR: auto-deploy accepted an incompletely ready platform Gateway\n' >&2
	exit 1
fi
READINESS_OK=1

# An equal desired version must not restart or upgrade the Agent.
rm -f "${marker}"
ensure_local_platform_gateway
[[ -e "${marker}" ]]
[[ ! -e "${TEST_AGENT_UPGRADE_MARKER}" ]]

# Main builds are identities, not ordered hashes: any unequal desired identity converges.
export TEST_DESIRED_VERSION=main-2222222
printf '%s\n' "${TEST_DESIRED_VERSION}" >"${ROOT}/VERSION"
release_dir="${ROOT}/data/media/agent-releases/${TEST_DESIRED_VERSION}"
mkdir -p "${release_dir}"
archive="${release_dir}/hfl-agent-${TEST_DESIRED_VERSION}-linux-amd64.tar.gz"
printf 'fixture\n' >"${archive}"
rm -f "${TEST_AGENT_UPGRADE_MARKER}"
ensure_local_platform_gateway
[[ "$(<"${TEST_AGENT_UPGRADE_MARKER}")" == "${archive}" ]]
[[ "$(local_platform_gateway_installed_agent_version)" == "main-2222222" ]]

TLS_MODE=0
rm -f "${marker}"
ensure_local_platform_gateway
[[ "$(<"${marker}")" == "https://127.0.0.1:11443|wss://127.0.0.1:11443/ws/node/agent/|1" ]]

ENROLLMENT_ORG=tenant-org
if (ensure_local_platform_gateway) 2>/dev/null; then
	printf 'ERROR: local platform Gateway accepted another organization\n' >&2
	exit 1
fi
ENROLLMENT_ORG=__platform_lens__

sed -i 's/HFL_NODE_ID=99/HFL_NODE_ID=100/' \
	"${LOCAL_PLATFORM_AGENT_DATA_DIR}/config/agent.env"
if (ensure_local_platform_gateway) 2>/dev/null; then
	printf 'ERROR: auto-deploy claimed a platform Gateway not managed by the installer\n' >&2
	exit 1
fi
sed -i 's/HFL_NODE_ID=100/HFL_NODE_ID=99/' \
	"${LOCAL_PLATFORM_AGENT_DATA_DIR}/config/agent.env"

# A failed exact upgrade preserves the previous Agent and leaves a retention marker.
export TEST_DESIRED_VERSION=main-3333333
printf '%s\n' "${TEST_DESIRED_VERSION}" >"${ROOT}/VERSION"
release_dir="${ROOT}/data/media/agent-releases/${TEST_DESIRED_VERSION}"
mkdir -p "${release_dir}"
printf 'fixture\n' >"${release_dir}/hfl-agent-${TEST_DESIRED_VERSION}-linux-amd64.tar.gz"
export TEST_AGENT_UPGRADE_FAIL=1
if (ensure_local_platform_gateway) 2>/dev/null; then
	printf 'ERROR: failed local Agent upgrade was accepted\n' >&2
	exit 1
fi
unset TEST_AGENT_UPGRADE_FAIL
[[ "$(local_platform_gateway_installed_agent_version)" == "main-2222222" ]]
[[ "$(<"${ROOT}/data/.platform-gateway-agent-upgrade")" == "main-3333333" ]]

AUTO_DEPLOY=invalid
if (platform_gateway_auto_deploy_enabled) 2>/dev/null; then
	printf 'ERROR: invalid platform Gateway auto-deploy value was accepted\n' >&2
	exit 1
fi

# Exercise LensNode image convergence separately from Agent enrollment.
# shellcheck disable=SC1090
source <(sed -n '/^converge_local_platform_gateway_lensnode()/,/^ensure_local_platform_gateway()/p' "${installer}" | sed '$d')
CURRENT_LENSNODE_IMAGE_ID=sha256:desired
DESIRED_LENSNODE_IMAGE_ID=sha256:desired
LENSNODE_RUNNING=true
SIDECAR_RECREATED=0
script="${ROOT}/data/media/gateway-bootstrap/gateway-install-lensnode-sidecar.sh"
printf '#!/usr/bin/env bash\nexit 99\n' >"${script}"
chmod 755 "${script}"
docker() {
	case "$*" in
	"image inspect --format {{.Id}} hyperfilelens-sourcelens-lensnode:latest")
		printf '%s\n' "${DESIRED_LENSNODE_IMAGE_ID}"
		;;
	"ps -aq --no-trunc --filter label=com.hyperfilelens.managed=true --filter label=com.hyperfilelens.component=gateway-lensnode --filter label=com.docker.compose.project=hyperfilelens-gateway --filter label=com.docker.compose.service=lensnode")
		printf 'lensnode-container\n'
		;;
	"inspect --format {{.Image}} lensnode-container")
		printf '%s\n' "${CURRENT_LENSNODE_IMAGE_ID}"
		;;
	"inspect --format {{.State.Running}} lensnode-container")
		printf '%s\n' "${LENSNODE_RUNNING}"
		;;
	*) printf 'unexpected fake Docker invocation: %s\n' "$*" >&2; return 1 ;;
	esac
}
run_as_root() {
	if [[ "$1" == "env" ]]; then
		SIDECAR_RECREATED=$((SIDECAR_RECREATED + 1))
		CURRENT_LENSNODE_IMAGE_ID="${DESIRED_LENSNODE_IMAGE_ID}"
		return 0
	fi
	"$@"
}

converge_local_platform_gateway_lensnode
[[ "${SIDECAR_RECREATED}" == "0" ]]
CURRENT_LENSNODE_IMAGE_ID=sha256:old
converge_local_platform_gateway_lensnode
[[ "${SIDECAR_RECREATED}" == "1" ]]
[[ "${CURRENT_LENSNODE_IMAGE_ID}" == "${DESIRED_LENSNODE_IMAGE_ID}" ]]

# Restore the production readiness implementation after the deterministic
# ensure fixture above exercised its failure contract.
# shellcheck disable=SC1090
source <(sed -n '/^local_platform_gateway_readiness_once()/,/^local_platform_gateway_installed_agent_version()/p' "${installer}" | sed '$d')
wait_for_local_platform_gateway_readiness 0
[[ "${READINESS_QUERY}" == *"gateway_id=99, scope='platform'"* ]]
[[ "${READINESS_QUERY}" == *"sync_gateway_lensnode_status(link)"* ]]
if [[ "${READINESS_QUERY}" == *"is_platform_default=True"* ]]; then
	printf 'ERROR: local Gateway readiness was coupled to platform default selection\n' >&2
	exit 1
fi
READINESS_OK=0
if wait_for_local_platform_gateway_readiness 0; then
	printf 'ERROR: an unusable platform Gateway passed readiness\n' >&2
	exit 1
fi
[[ "${LOCAL_PLATFORM_GATEWAY_READINESS_REASON}" == \
	"managed Platform Data Gateway link, Agent WebSocket, or AI engine is not online and usable" ]]
READINESS_OK=1
LENSNODE_RUNNING=false
if wait_for_local_platform_gateway_readiness 0; then
	printf 'ERROR: a stopped LensNode passed platform Gateway readiness\n' >&2
	exit 1
fi
[[ "${LOCAL_PLATFORM_GATEWAY_READINESS_REASON}" == \
	"managed AI engine container is not running" ]]
LENSNODE_RUNNING=true
AGENT_ACTIVE=0
if wait_for_local_platform_gateway_readiness 0; then
	printf 'ERROR: a stopped Agent passed platform Gateway readiness\n' >&2
	exit 1
fi
[[ "${LOCAL_PLATFORM_GATEWAY_READINESS_REASON}" == "Agent service is not active" ]]
AGENT_ACTIVE=1

# Exercise the public read-only verification command after its dependencies
# have been replaced with deterministic fixtures.
# shellcheck disable=SC1090
source <(sed -n '/^cmd_platform_gateway()/,/^cmd_start()/p' "${installer}" | sed '$d')
init_install_root() { :; }
printf 'HFL_PLATFORM_GATEWAY_AUTO_DEPLOY=true\n' >"${ROOT}/.env"
AUTO_DEPLOY=true
cmd_platform_gateway verify --required --timeout 0
AUTO_DEPLOY=false
cmd_platform_gateway verify --timeout 0
if (cmd_platform_gateway verify --required --timeout 0) 2>/dev/null; then
	printf 'ERROR: required platform Gateway verification accepted disabled auto-deploy\n' >&2
	exit 1
fi
AUTO_DEPLOY=true
if (cmd_platform_gateway verify --timeout --required) 2>/dev/null; then
	printf 'ERROR: platform Gateway verification accepted a missing timeout\n' >&2
	exit 1
fi

printf 'Platform Gateway auto-deploy contracts passed.\n'
