#!/usr/bin/env bash
set -euo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -rf "${fixture}"' EXIT

# shellcheck source=../../deploy/installer/install.sh
source "${REPO_ROOT}/deploy/installer/install.sh"

LOCAL_PLATFORM_AGENT_INSTALL_DIR="${fixture}/opt/hyperfilelens-agent/bin"
LOCAL_PLATFORM_AGENT_DATA_DIR="${fixture}/opt/hyperfilelens-agent"
LOCAL_PLATFORM_AGENT_LEGACY_INSTALL_DIR="${fixture}/legacy/opt/hyperfilelens-agent"
LOCAL_PLATFORM_AGENT_LEGACY_DATA_DIR="${fixture}/legacy/var/lib/hyperfilelens-agent"
LOCAL_PLATFORM_AGENT_SYSTEMD_UNIT_FILE="${fixture}/etc/systemd/system/hyperfilelens-agent.service"
mkdir -p "${LOCAL_PLATFORM_AGENT_INSTALL_DIR}" "${LOCAL_PLATFORM_AGENT_DATA_DIR}/config"
printf '%s\n' \
	'HFL_ORG_KEY=__platform_lens__' \
	'HFL_NODE_ROLE=gateway' \
	>"${LOCAL_PLATFORM_AGENT_DATA_DIR}/config/agent.env"
local_platform_gateway_agent_is_managed

printf '%s\n' \
	'HFL_ORG_KEY=customer-org' \
	'HFL_NODE_ROLE=gateway' \
	>"${LOCAL_PLATFORM_AGENT_DATA_DIR}/config/agent.env"
if local_platform_gateway_agent_is_managed; then
	printf 'ordinary customer Gateway was classified as installer-managed\n' >&2
	exit 1
fi

printf '%s\n' \
	'HFL_ORG_KEY=__platform_lens__' \
	'HFL_NODE_ROLE=gateway' \
	>"${LOCAL_PLATFORM_AGENT_DATA_DIR}/config/agent.env"
invocation="${fixture}/agent-uninstall-invocation"
printf '%s\n' \
	'#!/usr/bin/env bash' \
	'set -euo pipefail' \
	'[[ "$*" == "uninstall --purge-all" ]]' \
	'printf "%s\n" "$*" >"${TEST_AGENT_INVOCATION}"' \
	'rm -rf "${TEST_AGENT_INSTALL_DIR}" "${TEST_AGENT_DATA_DIR}"' \
	>"${LOCAL_PLATFORM_AGENT_INSTALL_DIR}/install.sh"
chmod 755 "${LOCAL_PLATFORM_AGENT_INSTALL_DIR}/install.sh"
export TEST_AGENT_INVOCATION="${invocation}"
export TEST_AGENT_INSTALL_DIR="${LOCAL_PLATFORM_AGENT_INSTALL_DIR}"
export TEST_AGENT_DATA_DIR="${LOCAL_PLATFORM_AGENT_DATA_DIR}"
run_as_root() { "$@"; }
docker() {
	case "${1:-}" in
	info) return 0 ;;
	ps) return 0 ;;
	*) return 1 ;;
	esac
}
step() { :; }
ok() { :; }
uninstall_managed_local_platform_gateway
grep -Fx 'uninstall --purge-all' "${invocation}" >/dev/null
[[ ! -e "${LOCAL_PLATFORM_AGENT_INSTALL_DIR}" ]]
[[ ! -e "${LOCAL_PLATFORM_AGENT_DATA_DIR}" ]]

# A failed managed-Gateway uninstall is a hard stop: the HFL control plane and
# its data must remain available so the operator can retry safely.
mkdir -p "${LOCAL_PLATFORM_AGENT_INSTALL_DIR}" "${LOCAL_PLATFORM_AGENT_DATA_DIR}/config"
printf '%s\n' \
	'HFL_ORG_KEY=__platform_lens__' \
	'HFL_NODE_ROLE=gateway' \
	>"${LOCAL_PLATFORM_AGENT_DATA_DIR}/config/agent.env"
printf '%s\n' \
	'#!/usr/bin/env bash' \
	'exit 23' \
	>"${LOCAL_PLATFORM_AGENT_INSTALL_DIR}/install.sh"
chmod 755 "${LOCAL_PLATFORM_AGENT_INSTALL_DIR}/install.sh"
if (uninstall_managed_local_platform_gateway) >/dev/null 2>&1; then
	printf 'failed managed-Gateway uninstall was accepted\n' >&2
	exit 1
fi
[[ -e "${LOCAL_PLATFORM_AGENT_INSTALL_DIR}" ]]
[[ -e "${LOCAL_PLATFORM_AGENT_DATA_DIR}" ]]

# Runtime ownership remains discoverable from Compose labels when the
# SourceLens .env link has already been removed by an older partial purge.
(
	set -euo pipefail
	source "${REPO_ROOT}/deploy/installer/install.sh"
	ROOT="${fixture}/ownership-root"
	mkdir -p "${ROOT}/sourcelens"
	docker() {
		if [[ "${1:-}" == ps && "$*" == *'project=hyperfilelens-sourcelens'* ]]; then
			printf '%s\n' owned-current foreign-current
			return 0
		fi
		if [[ "${1:-}" == ps && "$*" == *'project=sourcelens'* ]]; then
			printf '%s\n' owned-legacy
			return 0
		fi
		if [[ "${1:-}" == inspect && "${2:-}" != --format ]]; then
			return 0
		fi
		if [[ "${1:-}" == inspect && "${2:-}" == --format ]]; then
			local format=${3:-} container_id=${4:-}
			case "${format}" in
			*project.working_dir*)
				[[ "${container_id}" == owned-current ]] && printf '%s\n' "${ROOT}/sourcelens"
				[[ "${container_id}" == foreign-current ]] && printf '%s\n' /srv/foreign-sourcelens
				;;
			*project.config_files*)
				[[ "${container_id}" == owned-legacy ]] && printf '%s\n' "${ROOT}/sourcelens/docker-compose.yml"
				;;
			*com.docker.compose.project*)
				[[ "${container_id}" == owned-legacy ]] && printf '%s\n' sourcelens \
					|| printf '%s\n' hyperfilelens-sourcelens
				;;
			esac
			return 0
		fi
		return 90
	}
	collect_owned_installation_containers hyperfilelens-sourcelens sourcelens
	[[ "${OWNED_INSTALLATION_CONTAINER_IDS[*]}" == 'owned-current owned-legacy' ]]
	sourcelens_runtime_present
)

# The real SourceLens fallback must remove verified orphan containers before
# images and data. Image cleanup failure must preserve data for retry.
run_sourcelens_component_contract() (
	set -euo pipefail
	source "${REPO_ROOT}/deploy/installer/install.sh"
	ROOT="${fixture}/component-$1"
	mkdir -p "${ROOT}/data/sourcelens" "${ROOT}/sourcelens"
	local scenario=$1 events="${fixture}/component-events-$1"
	: >"${events}"
	sourcelens_installed() { return 1; }
	sourcelens_runtime_present() { return 0; }
	remove_owned_installation_containers() { printf '%s\n' containers >>"${events}"; }
	remove_empty_owned_compose_networks() { printf 'networks:%s\n' "$*" >>"${events}"; }
	remove_sourcelens_images() {
		printf '%s\n' images >>"${events}"
		[[ "${scenario}" != image_failure ]]
	}
	purge_sourcelens_data_dir() { printf '%s\n' data >>"${events}"; }
	step() { :; }
	warn() { :; }
	log() { :; }
	uninstall_bundled_sourcelens 1
)

run_sourcelens_component_contract orphan_success
mapfile -t component_events <"${fixture}/component-events-orphan_success"
[[ "${component_events[*]}" == 'containers networks:hyperfilelens-sourcelens sourcelens images data' ]]
if run_sourcelens_component_contract image_failure >/dev/null 2>&1; then
	printf 'SourceLens image cleanup failure was accepted\n' >&2
	exit 1
fi
mapfile -t image_failure_events <"${fixture}/component-events-image_failure"
[[ "${image_failure_events[*]}" == 'containers networks:hyperfilelens-sourcelens sourcelens images' ]]

# The shared bridge is removed only when HyperFileLens created it and no
# containers remain attached. Foreign or still-used networks are retained.
run_bridge_network_contract() (
	set -euo pipefail
	source "${REPO_ROOT}/deploy/installer/install.sh"
	local scenario=$1 events="${fixture}/bridge-events-$1"
	: >"${events}"
	docker() {
		case "${scenario}:$*" in
		missing:'network inspect hyperfilelens-bridge') return 1 ;;
		*:'info') return 0 ;;
		unmanaged:'network inspect --format {{index .Labels "com.hyperfilelens.managed"}} hyperfilelens-bridge')
			printf '%s\n' false ;;
		attached:'network inspect --format {{index .Labels "com.hyperfilelens.managed"}} hyperfilelens-bridge' | empty:'network inspect --format {{index .Labels "com.hyperfilelens.managed"}} hyperfilelens-bridge' | removal_failure:'network inspect --format {{index .Labels "com.hyperfilelens.managed"}} hyperfilelens-bridge')
			printf '%s\n' true ;;
		attached:'network inspect --format {{range $id, $_ := .Containers}}{{println $id}}{{end}} hyperfilelens-bridge')
			printf '%s\n' foreign-container ;;
		empty:'network inspect --format {{range $id, $_ := .Containers}}{{println $id}}{{end}} hyperfilelens-bridge')
			: ;;
		removal_failure:'network inspect --format {{range $id, $_ := .Containers}}{{println $id}}{{end}} hyperfilelens-bridge')
			: ;;
		empty:'network rm hyperfilelens-bridge') printf '%s\n' removed >>"${events}" ;;
		removal_failure:'network rm hyperfilelens-bridge') return 1 ;;
		empty:'network inspect hyperfilelens-bridge')
			grep -Fx 'removed' "${events}" >/dev/null 2>&1 && return 1
			return 0
			;;
		*:'network inspect hyperfilelens-bridge') return 0 ;;
		*) return 90 ;;
		esac
	}
	step() { :; }
	ok() { :; }
	warn() { printf 'warning\n' >>"${events}"; }
	remove_managed_bridge_network
	printf 'removed=%s\n' "${MANAGED_BRIDGE_NETWORK_REMOVED}" >>"${events}"
)

run_bridge_network_contract missing
grep -Fx 'removed=0' "${fixture}/bridge-events-missing" >/dev/null
run_bridge_network_contract unmanaged
grep -Fx 'warning' "${fixture}/bridge-events-unmanaged" >/dev/null
grep -Fx 'removed=0' "${fixture}/bridge-events-unmanaged" >/dev/null
run_bridge_network_contract attached
grep -Fx 'warning' "${fixture}/bridge-events-attached" >/dev/null
grep -Fx 'removed=0' "${fixture}/bridge-events-attached" >/dev/null
run_bridge_network_contract empty
mapfile -t bridge_events <"${fixture}/bridge-events-empty"
[[ "${bridge_events[*]}" == 'removed removed=1' ]]
if run_bridge_network_contract removal_failure >/dev/null 2>&1; then
	printf 'shared network removal failure was accepted\n' >&2
	exit 1
fi

# --keep-data removes replaceable application files but preserves exactly the
# control-plane state needed for a later reinstall. Complete removal keeps the
# active log descriptor valid while deleting that log and the installation root.
preserve_root="${fixture}/preserve/hyperfilelens"
mkdir -p "${preserve_root}"/{data,backup,logs,images,sourcelens}
: >"${preserve_root}/.env"
: >"${preserve_root}/.installer.lock"
: >"${preserve_root}/docker-compose.yml"
: >"${preserve_root}/MANIFEST.json"
: >"${preserve_root}/install.sh"
(
	set -euo pipefail
	source "${REPO_ROOT}/deploy/installer/install.sh"
	INSTALL_DIR="${preserve_root}"
	ROOT="${preserve_root}"
	remove_installation_files_preserving_data >/dev/null 2>&1
)
for retained in .env .installer.lock data backup logs; do
	[[ -e "${preserve_root}/${retained}" ]]
done
for removed in docker-compose.yml MANIFEST.json install.sh images sourcelens; do
	[[ ! -e "${preserve_root}/${removed}" ]]
done

complete_root="${fixture}/complete/hyperfilelens"
complete_output="${fixture}/complete-output"
mkdir -p "${complete_root}"
: >"${complete_root}/docker-compose.yml"
: >"${complete_root}/MANIFEST.json"
HFL_TEST_COMPLETE_ROOT="${complete_root}" bash -c '
set -euo pipefail
source "$1/deploy/installer/install.sh"
INSTALL_DIR="$HFL_TEST_COMPLETE_ROOT"
ROOT="$HFL_TEST_COMPLETE_ROOT"
configure_logging uninstall
printf "%s\n" "captured before root removal"
remove_complete_installation_root
printf "%s\n" "terminal remains usable after root removal"
' _ "${REPO_ROOT}" >"${complete_output}" 2>&1
[[ ! -e "${complete_root}" ]]
grep -F 'terminal remains usable after root removal' "${complete_output}" >/dev/null
if grep -F 'No such file or directory' "${complete_output}" >/dev/null; then
	printf 'internal uninstall log emitted an error while removing its root\n' >&2
	exit 1
fi

# Plain uninstall and --purge-all must remove the installer-owned Gateway first,
# then bundled SourceLens, and only then stop the HFL control plane. --keep-data
# removes the same runtimes while retaining persistent state. Explicit legacy
# selection flags keep their narrower behavior.
run_uninstall_contract() (
	set -euo pipefail
	source "${REPO_ROOT}/deploy/installer/install.sh"
	ROOT="${fixture}/release-$1"
	LOCAL_PLATFORM_AGENT_INSTALL_DIR="${fixture}/contract-agent"
	mkdir -p "${ROOT}/data/sourcelens" "${ROOT}/data/media" "${ROOT}/sourcelens"
	: >"${ROOT}/.env"
	local scenario=$1 events="${fixture}/events-$1"
	: >"${events}"
	init_install_root() { :; }
	docker() {
		[[ "${scenario}" != docker_down && "${1:-}" == info ]]
	}
	require_docker() { :; }
	sourcelens_installed() { [[ "${scenario}" != orphan ]]; }
	sourcelens_runtime_present() { [[ "${scenario}" == orphan ]]; }
	local_platform_gateway_agent_is_managed() {
		[[ "${scenario}" != unmanaged ]]
	}
	uninstall_managed_local_platform_gateway() { printf '%s\n' gateway >>"${events}"; }
	uninstall_bundled_sourcelens() {
		printf 'sourcelens:%s\n' "$1" >>"${events}"
		[[ "${scenario}" != sourcelens_failure ]]
	}
	uninstall_hfl_runtime() {
		printf '%s\n' hfl >>"${events}"
		[[ "${scenario}" != hfl_failure ]] || return 1
		printf '%s\n' images >>"${events}"
	}
	remove_managed_bridge_network() {
		printf '%s\n' bridge >>"${events}"
		[[ "${scenario}" != bridge_failure ]] || die "simulated shared network cleanup failure"
		MANAGED_BRIDGE_NETWORK_REMOVED=1
	}
	remove_complete_installation_root() { printf '%s\n' root >>"${events}"; }
	remove_installation_files_preserving_data() { printf '%s\n' application-files >>"${events}"; }
	safe_assert_removable_data_dir() { :; }
	safe_assert_env_file() { :; }
	safe_rm_dir() { printf '%s\n' data >>"${events}"; }
	safe_rm_file() { printf '%s\n' config >>"${events}"; }
	print_section() { :; }
	print_value() { :; }
	print_result() { :; }
	print_warning_summary() { :; }
	step() { :; }
	log() { :; }
	warn() { :; }
	shift
	cmd_uninstall "$@" >/dev/null
)

run_uninstall_contract managed
mapfile -t default_events <"${fixture}/events-managed"
[[ "${default_events[*]}" == 'gateway sourcelens:1 hfl images bridge data config root' ]]

run_uninstall_contract purge --purge-all
mapfile -t purge_events <"${fixture}/events-purge"
[[ "${purge_events[*]}" == 'gateway sourcelens:1 hfl images bridge data config root' ]]

run_uninstall_contract keep --keep-data
mapfile -t keep_events <"${fixture}/events-keep"
[[ "${keep_events[*]}" == 'gateway sourcelens:0 hfl images bridge application-files' ]]

run_uninstall_contract selective --with-sourcelens
mapfile -t selective_events <"${fixture}/events-selective"
[[ "${selective_events[*]}" == 'sourcelens:0 hfl images' ]]

run_uninstall_contract selective_config --purge-config
mapfile -t selective_config_events <"${fixture}/events-selective_config"
[[ "${selective_config_events[*]}" == 'hfl images config' ]]

run_uninstall_contract unmanaged
mapfile -t unmanaged_events <"${fixture}/events-unmanaged"
[[ "${unmanaged_events[*]}" == 'sourcelens:1 hfl images bridge data config root' ]]

run_uninstall_contract orphan
mapfile -t orphan_events <"${fixture}/events-orphan"
[[ "${orphan_events[*]}" == 'gateway sourcelens:1 hfl images bridge data config root' ]]

if run_uninstall_contract docker_down >/dev/null 2>&1; then
	printf 'plain uninstall continued without Docker\n' >&2
	exit 1
fi
[[ ! -s "${fixture}/events-docker_down" ]]

if run_uninstall_contract docker_down --purge-all >/dev/null 2>&1; then
	printf 'purge-all continued without Docker\n' >&2
	exit 1
fi
[[ ! -s "${fixture}/events-docker_down" ]]

if run_uninstall_contract docker_down --purge-config >/dev/null 2>&1; then
	printf 'configuration purge continued without Docker\n' >&2
	exit 1
fi
[[ ! -s "${fixture}/events-docker_down" ]]

for conflict in --purge-all --purge-config --purge-data --purge-media --purge-sourcelens-data; do
	if run_uninstall_contract "conflict-${conflict#--}" --keep-data "${conflict}" >/dev/null 2>&1; then
		printf '%s was accepted with --keep-data\n' "${conflict}" >&2
		exit 1
	fi
	[[ ! -s "${fixture}/events-conflict-${conflict#--}" ]]
done

if run_uninstall_contract preserve_sourcelens --purge-data >/dev/null 2>&1; then
	printf 'HFL data purge removed retained SourceLens data\n' >&2
	exit 1
fi
[[ ! -s "${fixture}/events-preserve_sourcelens" ]]

if run_uninstall_contract invalid_sourcelens_purge --purge-sourcelens-data >/dev/null 2>&1; then
	printf 'SourceLens data purge was accepted without --with-sourcelens\n' >&2
	exit 1
fi
[[ ! -s "${fixture}/events-invalid_sourcelens_purge" ]]

if run_uninstall_contract sourcelens_failure >/dev/null 2>&1; then
	printf 'complete uninstall continued after SourceLens cleanup failure\n' >&2
	exit 1
fi
mapfile -t sourcelens_failure_events <"${fixture}/events-sourcelens_failure"
[[ "${sourcelens_failure_events[*]}" == 'gateway sourcelens:1' ]]

if run_uninstall_contract hfl_failure >/dev/null 2>&1; then
	printf 'complete uninstall continued after HyperFileLens cleanup failure\n' >&2
	exit 1
fi
mapfile -t hfl_failure_events <"${fixture}/events-hfl_failure"
[[ "${hfl_failure_events[*]}" == 'gateway sourcelens:1 hfl' ]]

if run_uninstall_contract bridge_failure >/dev/null 2>&1; then
	printf 'complete uninstall continued after shared network cleanup failure\n' >&2
	exit 1
fi
mapfile -t bridge_failure_events <"${fixture}/events-bridge_failure"
[[ "${bridge_failure_events[*]}" == 'gateway sourcelens:1 hfl images bridge' ]]

printf 'Release purge-all ownership and lifecycle contracts passed.\n'
