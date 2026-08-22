#!/usr/bin/env bash
# HyperFileLens Agent bundle installer (Linux / macOS).
# Usage: install.sh [command] [options]
# When no command is given, equivalent to: install.sh install
# After install, lifecycle scripts are copied into the selected installation
# directory for local upgrade, status, and uninstall operations.

set -euo pipefail

BUNDLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLATION_MODE="${HFL_INSTALLATION_MODE:-}"
RUN_AS_USER="${HFL_RUN_AS_USER:-}"
RUN_AS_HOME="${HFL_RUN_AS_HOME:-}"
if [[ -z "${INSTALLATION_MODE}" ]]; then
	if [[ "${BUNDLE_ROOT}" == "/opt/hyperfilelens-agent" ]]; then
		INSTALLATION_MODE="system"
	else
		case "${BUNDLE_ROOT}" in
		"${HOME:-/nonexistent}"/.local/lib/hyperfilelens-agent | "${HOME:-/nonexistent}"/Library/Application\ Support/HyperFileLens/Agent/bin)
			INSTALLATION_MODE="user"
			;;
		*) INSTALLATION_MODE="system" ;;
		esac
	fi
fi
# Commands launched from an installed machine-wide script must retain the
# persisted account mode. Bootstrap installs pass HFL_INSTALLATION_MODE
# explicitly, so this only applies to local start/status/upgrade/uninstall.
if [[ -z "${HFL_INSTALLATION_MODE:-}" && "${BUNDLE_ROOT}" == "/opt/hyperfilelens-agent" ]]; then
	PERSISTED_ENV="/var/lib/hyperfilelens-agent/agent.env"
	if [[ -f "${PERSISTED_ENV}" ]]; then
		while IFS='=' read -r key value; do
			case "${key}" in
			HFL_INSTALLATION_MODE) INSTALLATION_MODE="${value}" ;;
			HFL_RUN_AS_USER) RUN_AS_USER="${value}" ;;
			HFL_RUN_AS_HOME) RUN_AS_HOME="${value}" ;;
			esac
		done <"${PERSISTED_ENV}"
	fi
fi
[[ "${INSTALLATION_MODE}" == "system" || "${INSTALLATION_MODE}" == "user" || "${INSTALLATION_MODE}" == "account" ]] \
	|| { echo "ERROR: HFL_INSTALLATION_MODE must be system, user, or account" >&2; exit 2; }

# Unix paths use product slug "hyperfilelens-agent" (see internal/platform/vfs/paths.go).
if [[ "${INSTALLATION_MODE}" == "user" && "$(uname -s)" == "Darwin" ]]; then
	INSTALL_DIR="${HOME}/Library/Application Support/HyperFileLens/Agent/bin"
	DEFAULT_DATA="${HOME}/Library/Application Support/HyperFileLens/Agent"
	UNIT_DST=""
	LAUNCHD_PLIST="${HOME}/Library/LaunchAgents/com.hyperfilelens.agent.plist"
	LAUNCHD_DOMAIN="gui/$(id -u)"
elif [[ "${INSTALLATION_MODE}" == "user" ]]; then
	USER_STATE_HOME="${XDG_STATE_HOME:-}"
	[[ "${USER_STATE_HOME}" == /* ]] || USER_STATE_HOME="${HOME}/.local/state"
	USER_CONFIG_HOME="${XDG_CONFIG_HOME:-}"
	[[ "${USER_CONFIG_HOME}" == /* ]] || USER_CONFIG_HOME="${HOME}/.config"
	INSTALL_DIR="${HOME}/.local/lib/hyperfilelens-agent"
	DEFAULT_DATA="${USER_STATE_HOME}/hyperfilelens-agent"
	UNIT_DST="${USER_CONFIG_HOME}/systemd/user/hyperfilelens-agent.service"
	LAUNCHD_PLIST=""
	LAUNCHD_DOMAIN=""
else
	INSTALL_DIR="/opt/hyperfilelens-agent"
	DEFAULT_DATA="/var/lib/hyperfilelens-agent"
	UNIT_DST="/etc/systemd/system/hyperfilelens-agent.service"
	LAUNCHD_PLIST="/Library/LaunchDaemons/com.hyperfilelens.agent.plist"
	LAUNCHD_DOMAIN="system"
fi
GATEWAY_RESOURCE_DROPIN="/etc/systemd/system/hyperfilelens-agent.service.d/20-gateway-resources.conf"
INSTALLED_VERSION_FILE="${INSTALL_DIR}/INSTALLED_VERSION"
LAUNCHD_LABEL="com.hyperfilelens.agent"
RUN_AGENT_SCRIPT="${INSTALL_DIR}/run-agent.sh"
GATEWAY_LIFECYCLE_SCRIPT="${INSTALL_DIR}/libexec/gateway-lifecycle.sh"

if [[ $# -eq 0 ]]; then
	CMD="install"
elif [[ "$1" == "-h" || "$1" == "--help" ]]; then
	CMD="help"
elif [[ "$1" == --* ]]; then
	CMD="install"
else
	CMD="$1"
	shift || true
fi

WSS_URL=""
API_BASE=""
ORG_KEY=""
NODE_TOKEN=""
NODE_ID=""
DATA_DIR=""
NODE_ROLE="agent"
NO_START=0
PURGE_ALL=0
KEEP_INSTALLATION_IDENTITY=0
AGENT_ONLY=0
KOPIA_ONLY=0
NO_RESTART=0
QUIET_FOOTER=0
HFL_ACTIVE_LOG_FILE=""
HFL_ACTIVE_LOG_KIND=""
HFL_DETAIL_LOG_PID=""
HFL_FAILURE_REPORTED=0
HFL_FAILURE_MARKER=""
HFL_LOG_FINALIZING=0
UPGRADE_FROM=""
UPGRADE_YES=0

# Preserve the caller's terminal while command stdout/stderr is mirrored to a
# timestamped detail log during install, upgrade, and uninstall operations.
exec 3>&1 4>&2

usage() {
	local command_prefix="" lifecycle="hyperfilelens-agent.service"
	if [[ "${INSTALLATION_MODE}" != "user" ]]; then
		command_prefix="sudo "
	fi
	if [[ "$(uname -s)" == "Darwin" ]]; then
		lifecycle="${LAUNCHD_LABEL}"
	fi
	cat <<USAGE
Usage: install.sh [command] [options]

When no command is given, equivalent to: install.sh install

Commands:
  install       Install agent binaries and configuration (${INSTALL_DIR})
  start         Start ${lifecycle}
  stop          Stop ${lifecycle}
  restart       Stop then start ${lifecycle}
  status        Show installed version, paths, and service state
  upgrade       In-place upgrade from another release package directory or .tar.gz
  uninstall     Stop service and remove install dir (keeps data dir by default)

Options:
  install:
    --wss-url URL       WebSocket control plane URL
    --api-base URL      HyperFileLens API base URL
    --org-key KEY       Organization key
    --node-token TOKEN  Node enrollment token
    --node-id ID        Node ID (usually set after enrollment heartbeat)
    --data-dir PATH     Data directory (default: ${DEFAULT_DATA})
    --role ROLE         Node role (default: agent)
    --no-start          Do not start any service after install

  upgrade:
    --from PATH         Path to new package directory or hfl-agent-*.tar.gz (required)
                        Extracts to DATA_DIR/runtime/workspace, merges missing agent.env keys,
                        migrates agent.db schema, overwrites binaries; removes workspace on success
    --yes               Non-interactive: continue when target version equals installed version

  uninstall:
    --purge-all                   Remove data directory and agent.env (unmounts NAS shares first)
    --keep-installation-identity  Keep agent.env installation identity (incomplete-install rollback)

Install paths:
  ${INSTALL_DIR}  Binaries and installer scripts
  ${DEFAULT_DATA}  Runtime data, backup, and configuration
  ${lifecycle}  Managed startup lifecycle

Examples:
  ${command_prefix}./install.sh
  ${command_prefix}./install.sh install --wss-url 'wss://console.example/ws/node/agent/' --api-base 'https://console.example' --org-key 'org_xxx' --node-token 'tok_xxx'
  ${command_prefix}./install.sh start
  ${command_prefix}./install.sh status
  ${command_prefix}./install.sh upgrade --from /path/to/hfl-agent-0.1.0.tar.gz
  ${command_prefix}./install.sh uninstall --purge-all
USAGE
}

hfl_systemctl() {
	if [[ "${INSTALLATION_MODE}" == "user" ]]; then
		PYTHONWARNINGS=ignore::SyntaxWarning systemctl --user "$@"
	else
		PYTHONWARNINGS=ignore::SyntaxWarning systemctl "$@"
	fi
}

parse_install_flags() {
	while [[ $# -gt 0 ]]; do
		case "$1" in
			--wss-url) WSS_URL="$2"; shift 2 ;;
			--api-base) API_BASE="$2"; shift 2 ;;
			--org-key) ORG_KEY="$2"; shift 2 ;;
			--node-token) NODE_TOKEN="$2"; shift 2 ;;
			--node-id) NODE_ID="$2"; shift 2 ;;
			--data-dir) DATA_DIR="$2"; shift 2 ;;
			--run-as-user) RUN_AS_USER="$2"; shift 2 ;;
			--role) NODE_ROLE="$2"; shift 2 ;;
			--no-start) NO_START=1; shift ;;
			--quiet-footer) QUIET_FOOTER=1; shift ;;
			-h|--help) usage; exit 0 ;;
			*)
				echo "Unknown option: $1" >&2
				usage >&2
				exit 2
				;;
		esac
	done
}

parse_upgrade_flags() {
	while [[ $# -gt 0 ]]; do
		case "$1" in
			--from)
				shift
				UPGRADE_FROM="${1:-}"
				[[ -n "${UPGRADE_FROM}" ]] || log_fail "Upgrade requires --from <path>." 2
				shift
				;;
			--agent-only) AGENT_ONLY=1; shift ;;
			--kopia-only) KOPIA_ONLY=1; shift ;;
			--no-restart) NO_RESTART=1; shift ;;
			--quiet-footer) QUIET_FOOTER=1; shift ;;
			--yes) UPGRADE_YES=1; shift ;;
			-h|--help) usage; exit 0 ;;
			*)
				echo "Unknown option: $1" >&2
				usage >&2
				exit 2
				;;
		esac
	done
	if [[ $AGENT_ONLY -eq 1 && $KOPIA_ONLY -eq 1 ]]; then
		echo "ERROR: --agent-only and --kopia-only are mutually exclusive" >&2
		exit 2
	fi
}

parse_uninstall_flags() {
	while [[ $# -gt 0 ]]; do
		case "$1" in
			--purge-all) PURGE_ALL=1; shift ;;
			--keep-installation-identity) KEEP_INSTALLATION_IDENTITY=1; shift ;;
			--quiet-footer) QUIET_FOOTER=1; shift ;;
			-h|--help) usage; exit 0 ;;
			*)
				echo "Unknown option: $1" >&2
				usage >&2
				exit 2
				;;
		esac
	done
	if [[ "${PURGE_ALL}" -eq 1 && "${KEEP_INSTALLATION_IDENTITY}" -eq 1 ]]; then
		echo "ERROR: --purge-all and --keep-installation-identity are mutually exclusive" >&2
		exit 2
	fi
}

require_root() {
	if [[ "${INSTALLATION_MODE}" == "user" ]]; then
		if [[ "$(id -u)" -eq 0 ]]; then
			log_fail "User-level installation must run as the current user without sudo." 1
		fi
		return 0
	fi
	if [[ "$(id -u)" -ne 0 ]]; then
		log_fail "Administrator privileges are required. Re-run with sudo." 1
	fi
}

require_agent_installed() {
	if ! is_installed; then
		local command_prefix=""
		[[ "${INSTALLATION_MODE}" == "system" ]] && command_prefix="sudo "
		log_fail "The agent is not installed. Run ${command_prefix}./install.sh install first." 2
	fi
}

is_darwin() {
	[[ "$(uname -s)" == "Darwin" ]]
}

agent_uses_launchd() {
	is_darwin
}

agent_uses_systemd() {
	! is_darwin && command -v systemctl >/dev/null 2>&1
}

agent_manages_service() {
	agent_uses_systemd || agent_uses_launchd
}

require_service_manager() {
	if is_darwin; then
		command -v launchctl >/dev/null 2>&1 \
			|| log_fail "launchd is required to install the agent service on macOS." 2
		if [[ "${INSTALLATION_MODE}" == "user" ]] \
			&& ! launchctl print "${LAUNCHD_DOMAIN}" >/dev/null 2>&1; then
			log_fail "An active macOS user session is required for user-level installation." 2
		fi
		return 0
	fi
	if [[ "${INSTALLATION_MODE}" == "user" ]]; then
		if ! command -v systemctl >/dev/null 2>&1 \
		|| ! hfl_systemctl show-environment >/dev/null 2>&1; then
			log_fail "A working systemd user service manager is required for user-level installation." 2
		fi
		command -v loginctl >/dev/null 2>&1 \
			|| log_fail "loginctl is required to verify that current-user mode stops after sign-out." 2
		local user_linger
		user_linger="$(loginctl show-user "$(id -u)" --property=Linger --value 2>/dev/null)" \
			|| log_fail "Unable to verify the current user's systemd sign-out behavior." 2
		if [[ "${user_linger}" == "yes" ]]; then
			log_fail "Current-user protection must pause after sign-out, but systemd user lingering is enabled. Disable lingering or choose Host files continuous protection." 2
		fi
		return 0
	fi
	if ! command -v systemctl >/dev/null 2>&1 \
		|| [[ ! -d /run/systemd/system ]] \
		|| ! hfl_systemctl show-environment >/dev/null 2>&1; then
		log_fail "This release requires a systemd-based Linux distribution. OpenRC, non-systemd, and container deployments are not supported." 2
	fi
}

service_display_name() {
	if agent_uses_launchd; then
		echo "${LAUNCHD_LABEL}"
	else
		echo "hyperfilelens-agent.service"
	fi
}

write_run_agent_script() {
	local env_file="$1"
	local quoted_env_file quoted_agent
	quoted_env_file="$(printf '%q' "${env_file}")"
	quoted_agent="$(printf '%q' "${INSTALL_DIR}/hfl-agent")"
	cat >"${RUN_AGENT_SCRIPT}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
ENV_FILE=${quoted_env_file}
if [[ -f "\$ENV_FILE" ]]; then
	while IFS='=' read -r key value; do
		[[ "\$key" =~ ^[A-Za-z_][A-Za-z0-9_]*\$ ]] || continue
		export "\$key=\$value"
	done <"\$ENV_FILE"
fi
exec ${quoted_agent} run
EOF
	chmod 755 "${RUN_AGENT_SCRIPT}"
	log_ok "wrote ${RUN_AGENT_SCRIPT}"
}

xml_escape() {
	printf '%s' "$1" | sed \
		-e 's/&/\&amp;/g' \
		-e 's/</\&lt;/g' \
		-e 's/>/\&gt;/g' \
		-e 's/"/\&quot;/g'
}

install_launchd_plist() {
	local env_file="$1"
	local data_dir log_dir stdout stderr plist_script plist_install plist_stdout plist_stderr
	data_dir="$(dirname "${env_file}")"
	log_dir="${data_dir}/logs"
	stdout="${log_dir}/launchd.stdout.log"
	stderr="${log_dir}/launchd.stderr.log"
	plist_script="$(xml_escape "${RUN_AGENT_SCRIPT}")"
	plist_install="$(xml_escape "${INSTALL_DIR}")"
	plist_stdout="$(xml_escape "${stdout}")"
	plist_stderr="$(xml_escape "${stderr}")"
	mkdir -p "${log_dir}"
	mkdir -p "$(dirname "${LAUNCHD_PLIST}")"
	cat >"${LAUNCHD_PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>${LAUNCHD_LABEL}</string>
	<key>ProgramArguments</key>
	<array>
		<string>${plist_script}</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
		<key>WorkingDirectory</key>
		<string>${plist_install}</string>
		$(if [[ "${INSTALLATION_MODE}" == "account" ]]; then printf '<key>UserName</key>\n\t<string>%s</string>\n' "$(xml_escape "${RUN_AS_USER}")"; fi)
	<key>StandardOutPath</key>
	<string>${plist_stdout}</string>
	<key>StandardErrorPath</key>
	<string>${plist_stderr}</string>
</dict>
</plist>
EOF
	if [[ "${INSTALLATION_MODE}" == "user" ]]; then
		chmod 600 "${LAUNCHD_PLIST}"
	else
		chmod 644 "${LAUNCHD_PLIST}"
	fi
	log_ok "installed launchd plist ${LAUNCHD_PLIST}"
}

launchd_service_status_line() {
	if launchctl print "${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL}" >/dev/null 2>&1; then
		local state
		state="$(launchctl print "${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL}" 2>/dev/null | awk -F'= ' '/state =/{print $2; exit}' | tr -d ' ;')"
		echo "${state:-loaded}"
	else
		echo "not loaded"
	fi
}

stop_launchd_service() {
	if launchctl print "${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL}" >/dev/null 2>&1; then
		launchctl bootout "${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL}" 2>/dev/null \
			|| launchctl bootout "${LAUNCHD_DOMAIN}" "${LAUNCHD_PLIST}" 2>/dev/null \
			|| true
		log_ok "stopped launchd service ${LAUNCHD_LABEL}"
	else
		log_skip "stop launchd ${LAUNCHD_LABEL} (not loaded)"
	fi
}

remove_launchd_plist() {
	stop_launchd_service
	if [[ -f "${LAUNCHD_PLIST}" ]]; then
		rm -f "${LAUNCHD_PLIST}"
		log_ok "removed launchd plist ${LAUNCHD_PLIST}"
	else
		log_skip "remove launchd plist ${LAUNCHD_PLIST} (not present)"
	fi
}

start_launchd_service() {
	local env_file="${1:-${DEFAULT_DATA}/agent.env}"
	write_run_agent_script "${env_file}"
	install_launchd_plist "${env_file}"
	stop_launchd_service
	if launchctl bootstrap "${LAUNCHD_DOMAIN}" "${LAUNCHD_PLIST}" 2>/dev/null; then
		log_ok "bootstrapped launchd ${LAUNCHD_LABEL}"
	else
		log_skip "bootstrap launchd ${LAUNCHD_LABEL} (may already be loaded)"
	fi
	if launchctl kickstart -k "${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL}" 2>/dev/null; then
		log_ok "started launchd service ${LAUNCHD_LABEL} ($(launchd_service_status_line))"
	else
		log_warn "launchd ${LAUNCHD_LABEL} is not running after kickstart"
	fi
}

start_launchd_service_only() {
	if [[ ! -f "${LAUNCHD_PLIST}" ]]; then
		start_launchd_service "${DEFAULT_DATA}/agent.env"
		return 0
	fi
	if launchctl kickstart -k "${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL}" 2>/dev/null; then
		log_ok "started launchd service ${LAUNCHD_LABEL} ($(launchd_service_status_line))"
	else
		start_launchd_service "${DEFAULT_DATA}/agent.env"
	fi
}

hfl_now() {
	date -u +%Y-%m-%dT%H:%M:%S.000Z 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ
}

hfl_finish_sentence() {
	local msg="$*"
	msg="${msg%"${msg##*[![:space:]]}"}"
	case "${msg}" in
	*.|*.?|*!) printf '%s' "${msg}" ;;
	*) printf '%s.' "${msg}" ;;
	esac
}

_hfl_emit_raw() {
	local level="$1"
	shift
	local message display_level log_dir
	message="$(hfl_finish_sentence "$*")"
	display_level="$(printf '%s' "${level}" | sed 's/^ *//; s/ *$//')"
	case "${display_level}" in
	OK) display_level=" OK " ;;
	STEP) display_level="...." ;;
	INFO) display_level="INFO" ;;
	WARN) display_level="WARN" ;;
	FAIL) display_level="FAIL" ;;
	SKIP) display_level="SKIP" ;;
	esac
	if [[ -n "${HFL_ACTIVE_LOG_FILE}" ]]; then
		log_dir="$(dirname "${HFL_ACTIVE_LOG_FILE}")"
		if [[ -d "${log_dir}" ]]; then
			printf '[%s] [%s] %s\n' "$(hfl_now)" "${level}" "${message}" \
				>>"${HFL_ACTIVE_LOG_FILE}" 2>/dev/null || true
		fi
	fi
	if [[ "${QUIET_FOOTER}" -eq 0 || "${display_level}" == "FAIL" ]]; then
		if [[ "${display_level}" == "WARN" || "${display_level}" == "FAIL" ]]; then
			printf '  [%s] %s\n' "${display_level}" "${message}" >&4
		else
			printf '  [%s] %s\n' "${display_level}" "${message}" >&3
		fi
	fi
}

log_info() { _hfl_emit_raw "INFO " "$@"; }
log_ok() { _hfl_emit_raw " OK  " "$@"; }
log_step() { _hfl_emit_raw "STEP " "$@"; }
log_skip() { _hfl_emit_raw "SKIP " "$@"; }
log_warn() { _hfl_emit_raw "WARN " "$@"; }
log_fail() {
	local message="$1" code="${2:-1}"
	HFL_FAILURE_REPORTED=1
	if [[ -n "${HFL_FAILURE_MARKER}" ]]; then
		: >"${HFL_FAILURE_MARKER}" 2>/dev/null || true
	fi
	_hfl_emit_raw "FAIL " "${message}"
	exit "${code}"
}

hfl_detail_log_stream() {
	local log_file="$1" line
	while IFS= read -r line || [[ -n "${line}" ]]; do
		printf '[%s] [DETAIL] %s\n' "$(hfl_now)" "${line}" >>"${log_file}" 2>/dev/null || true
	done
}

hfl_role_display_name() {
	local role="${1:-agent}" scope="${2:-}"
	case "${role}" in
	proxy) printf '%s' "Proxy Host" ;;
	gateway)
		case "${scope}" in
		public | platform) printf '%s' "Public Data Gateway" ;;
		*) printf '%s' "Private Data Gateway" ;;
		esac
		;;
	*) printf '%s' "Source Host" ;;
	esac
}

hfl_write_display_log() {
	local line="$1" log_dir
	[[ -n "${HFL_ACTIVE_LOG_FILE}" ]] || return 0
	log_dir="$(dirname "${HFL_ACTIVE_LOG_FILE}")"
	[[ -d "${log_dir}" ]] || return 0
	printf '[%s] [INFO ] %s\n' "$(hfl_now)" "${line}" \
		>>"${HFL_ACTIVE_LOG_FILE}" 2>/dev/null || true
}

hfl_emit_display_line() {
	printf '%s\n' "$1" >&3
	hfl_write_display_log "$1"
}

hfl_print_banner() {
	local role="$1" operation="$2"
	[[ "${QUIET_FOOTER}" -eq 0 ]] || return 0
	while IFS= read -r line; do
		hfl_emit_display_line "${line}"
	done <<'BANNER'
 _   _                       _____ _ _      _
| | | |_   _ _ __   ___ _ _|  ___(_) | ___| |    ___ _ __  ___
| |_| | | | | '_ \ / _ \ '__| |_  | | |/ _ \ |   / _ \ '_ \/ __|
|  _  | |_| | |_) |  __/ |  |  _| | | |  __/ |__|  __/ | | \__ \
|_| |_|\__, | .__/ \___|_|  |_|   |_|_|\___|_____\___|_| |_|___/
       |___/|_|                     INSTALLER
BANNER
	printf '\n' >&3
	hfl_emit_display_line "HyperFileLens ${role} ${operation}"
	hfl_emit_display_line '----------------------------------------------------------------'
}

hfl_print_section() {
	[[ "${QUIET_FOOTER}" -eq 0 ]] || return 0
	printf '\n%s\n' "$1" >&3
	hfl_write_display_log "$1"
}

hfl_print_value() {
	local label="$1" value="$2"
	[[ "${QUIET_FOOTER}" -eq 0 && -n "${value}" ]] || return 0
	printf '  %-13s %s\n' "${label}" "${value}" >&3
	hfl_write_display_log "${label}: ${value}"
}

hfl_print_result() {
	local title="$1"
	[[ "${QUIET_FOOTER}" -eq 0 ]] || return 0
	printf '\n' >&3
	hfl_emit_display_line '================================================================'
	hfl_emit_display_line "${title}"
	hfl_emit_display_line '================================================================'
}

begin_detail_log_capture() {
	local log_file="$1"
	exec > >(hfl_detail_log_stream "${log_file}") 2>&1
	HFL_DETAIL_LOG_PID=$!
}

finish_detail_log_capture() {
	exec 1>&3 2>&4
	if [[ -n "${HFL_DETAIL_LOG_PID}" ]]; then
		wait "${HFL_DETAIL_LOG_PID}" 2>/dev/null || true
		HFL_DETAIL_LOG_PID=""
	fi
}

begin_install_log() {
	local data_dir="$1" operation="${2:-install}"
	local log_file="${data_dir}/logs/install.log"
	mkdir -p "$(dirname "${log_file}")"
	HFL_ACTIVE_LOG_FILE="${log_file}"
	HFL_ACTIVE_LOG_KIND="${operation}"
	HFL_FAILURE_REPORTED=0
	HFL_FAILURE_MARKER="${log_file}.failure.$$"
	rm -f "${HFL_FAILURE_MARKER}"
	log_info "Install session started."
	begin_detail_log_capture "${log_file}"
	trap 'hfl_finalize_active_log $?' EXIT
}

finish_install_log() {
	local rc="$1"
	finish_detail_log_capture
	if [[ "${rc}" -eq 0 ]]; then
		log_info "Install session finished successfully."
	else
		log_warn "Install session finished with errors (exit=${rc})."
	fi
	HFL_ACTIVE_LOG_FILE=""
	HFL_ACTIVE_LOG_KIND=""
	HFL_FAILURE_REPORTED=0
	[[ -z "${HFL_FAILURE_MARKER}" ]] || rm -f "${HFL_FAILURE_MARKER}"
	HFL_FAILURE_MARKER=""
}

begin_uninstall_log() {
	local data_dir="$1"
	local log_file="${data_dir}/logs/uninstall.log"
	mkdir -p "$(dirname "${log_file}")"
	HFL_ACTIVE_LOG_FILE="${log_file}"
	HFL_ACTIVE_LOG_KIND="uninstall"
	HFL_FAILURE_REPORTED=0
	HFL_FAILURE_MARKER="${log_file}.failure.$$"
	rm -f "${HFL_FAILURE_MARKER}"
	log_info "Uninstall session started."
	begin_detail_log_capture "${log_file}"
	trap 'hfl_finalize_active_log $?' EXIT
}

finish_uninstall_log() {
	local rc="$1"
	finish_detail_log_capture
	if [[ "${rc}" -eq 0 ]]; then
		log_info "Uninstall session finished successfully."
	else
		log_warn "Uninstall session finished with errors (exit=${rc})."
	fi
	HFL_ACTIVE_LOG_FILE=""
	HFL_ACTIVE_LOG_KIND=""
	HFL_FAILURE_REPORTED=0
	[[ -z "${HFL_FAILURE_MARKER}" ]] || rm -f "${HFL_FAILURE_MARKER}"
	HFL_FAILURE_MARKER=""
}

hfl_finalize_active_log() {
	local rc="${1:-1}" operation failure_logged=0
	[[ -n "${HFL_ACTIVE_LOG_FILE}" && "${HFL_LOG_FINALIZING}" -eq 0 ]] || return 0
	HFL_LOG_FINALIZING=1
	case "${HFL_ACTIVE_LOG_KIND}" in
	install) operation="Installation" ;;
	upgrade) operation="Upgrade" ;;
	uninstall) operation="Uninstallation" ;;
	*) operation="Operation" ;;
	esac
	if [[ "${HFL_FAILURE_REPORTED}" -eq 1 ]] \
		|| [[ -n "${HFL_FAILURE_MARKER}" && -f "${HFL_FAILURE_MARKER}" ]]; then
		failure_logged=1
	fi
	if [[ "${rc}" -ne 0 && "${failure_logged}" -eq 0 ]]; then
		HFL_FAILURE_REPORTED=1
		_hfl_emit_raw "FAIL " "${operation} failed (exit code ${rc}); review ${HFL_ACTIVE_LOG_FILE} for details."
	fi
	case "${HFL_ACTIVE_LOG_KIND}" in
	uninstall) finish_uninstall_log "${rc}" ;;
	*) finish_install_log "${rc}" ;;
	esac
	HFL_LOG_FINALIZING=0
}

hfl_print_install_success() {
	local role="$1" version="$2" service="$3" data_dir="$4"
	hfl_print_section "Verifying"
	case "${service}" in
	"not started" | "not managed") log_skip "Agent service is ${service}." ;;
	*) log_ok "Agent service is ${service}." ;;
	esac
	hfl_print_result "Installation completed successfully"
	hfl_print_section "Installation summary"
	hfl_print_value "Role" "${role}"
	hfl_print_value "Agent version" "${version}"
	hfl_print_value "Service state" "${service}"
	hfl_print_value "Install path" "${INSTALL_DIR}"
	hfl_print_value "Data path" "${data_dir}"
	hfl_print_value "Log file" "${data_dir}/logs/install.log"
}

hfl_print_upgrade_success() {
	local version="$1" service="$2" data_dir="$3"
	hfl_print_section "Verifying"
	if [[ "${service}" == "not restarted" ]]; then
		log_skip "Agent service was not restarted by request."
	else
		log_ok "Agent service is ${service}."
	fi
	hfl_print_result "Upgrade completed successfully"
	hfl_print_section "Upgrade summary"
	hfl_print_value "Agent version" "${version}"
	hfl_print_value "Service state" "${service}"
	hfl_print_value "Install path" "${INSTALL_DIR}"
	hfl_print_value "Data path" "${data_dir}"
	hfl_print_value "Log file" "${data_dir}/logs/install.log"
}

bundle_agent() { echo "${BUNDLE_ROOT}/bin/hfl-agent"; }
bundle_kopia() { echo "${BUNDLE_ROOT}/bin/kopia"; }

is_installed_script_location() {
	[[ "$(cd "${BUNDLE_ROOT}" && pwd -P)" == "$(cd "${INSTALL_DIR}" && pwd -P)" ]]
}

bundle_version_from() {
	local root="${1:-${BUNDLE_ROOT}}"
	local manifest=""
	if [[ -f "${root}/MANIFEST.json" ]]; then
		manifest="${root}/MANIFEST.json"
	elif [[ -f "${INSTALL_DIR}/MANIFEST.json" ]]; then
		manifest="${INSTALL_DIR}/MANIFEST.json"
	fi
	if [[ -n "$manifest" ]]; then
		local ver
		ver="$(grep -E '"agent_version"[[:space:]]*:' "$manifest" | head -n1 | sed -n 's/.*"agent_version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
		if [[ -n "$ver" ]]; then
			echo "$ver"
			return 0
		fi
	fi
	echo "unknown"
}

bundle_version() {
	bundle_version_from "${BUNDLE_ROOT}"
}

version_lt() {
	local a=$1 b=$2
	[[ "$(printf '%s\n' "$a" "$b" | sort -V | head -n1)" == "$a" && "$a" != "$b" ]]
}

is_main_build() {
	[[ "${1:-}" =~ ^main-[0-9a-f]{7}$ ]]
}

confirm_same_version_upgrade() {
	local version=$1
	if [[ "${UPGRADE_YES}" -eq 1 ]]; then
		log_warn "new package version matches current (${version}); continuing upgrade (--yes)"
		return 0
	fi
	if [[ -t 0 ]]; then
		local ans
		printf 'Package version is already %s. Continue upgrade? [y/N] ' "${version}" >&2
		read -r ans
		case "${ans}" in
		y | Y | yes | YES) return 0 ;;
		esac
		log_fail "Upgrade was aborted because the target version matches the installed version." 2
	fi
	log_fail "Same-version upgrade requires an interactive terminal or --yes." 2
}

assert_agent_package_root() {
	local root="$1"
	if [[ ! -f "${root}/MANIFEST.json" || ! -x "${root}/install.sh" || ! -f "${root}/bin/hfl-agent" ]]; then
		log_fail "Invalid agent package layout at ${root}." 2
	fi
}

upgrade_workspace_dir() {
	local data_dir="$1"
	echo "${data_dir}/runtime/workspace"
}

cleanup_upgrade_workspace() {
	local ws="$1"
	if [[ -d "${ws}" ]]; then
		rm -rf "${ws}"
		log_ok "removed ${ws}"
	fi
}

prepare_upgrade_source() {
	local from="$1"
	local data_dir="$2"
	local ws
	ws="$(upgrade_workspace_dir "${data_dir}")"
	if [[ -d "${from}" ]]; then
		local resolved
		resolved="$(cd "${from}" && pwd)"
		assert_agent_package_root "${resolved}"
		printf '%s' "${resolved}"
		return 0
	fi
	if [[ -f "${from}" && "${from}" == *.tar.gz ]]; then
		cleanup_upgrade_workspace "${ws}"
		mkdir -p "${ws}"
		log_ok "extracting ${from} -> ${ws}"
		if tar --version 2>/dev/null | grep -qi 'GNU tar'; then
			tar --warning=no-unknown-keyword -xzf "${from}" -C "${ws}"
		else
			tar xzf "${from}" -C "${ws}"
		fi
		local inner
		inner="$(find "${ws}" -mindepth 1 -maxdepth 1 -type d | head -n1)"
		assert_agent_package_root "${inner}"
		printf '%s' "${inner}"
		return 0
	fi
	log_fail "Upgrade --from must be a directory or hfl-agent-*.tar.gz archive (${from})." 2
}

backup_agent_config_and_db() {
	local data_dir="$1"
	local prev_ver="${2:-unknown}"
	local state_dir="${data_dir}/backup/state"
	local archive="${state_dir}/latest.tar.gz"
	local meta="${data_dir}/backup/meta.json"
	local -a items=()
	mkdir -p "${state_dir}"
	[[ -f "${data_dir}/agent.env" ]] && items+=("agent.env")
	[[ -f "${data_dir}/agent.db" ]] && items+=("agent.db")
	if ((${#items[@]} == 0)); then
		log_skip "backup agent.env/agent.db (nothing to back up)"
		return 0
	fi
	tar -czf "${archive}" -C "${data_dir}" "${items[@]}"
	log_ok "backed up agent.env/agent.db -> ${archive}"
	cat >"${meta}" <<EOF
{
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "previous_version": "${prev_ver}",
  "state_archive": "backup/state/latest.tar.gz"
}
EOF
	log_ok "wrote ${meta}"
}

UPGRADE_MIN_FREE_MB="${HFL_UPGRADE_MIN_FREE_MB:-512}"
UPGRADE_BIN_BACKUP=""
UPGRADE_SERVICE_STOPPED=0

upgrade_preflight() {
	local data_dir="$1"
	local free_mb
	free_mb="$(df -Pm "${data_dir}" 2>/dev/null | awk 'NR==2 {print $4}')"
	if [[ -n "${free_mb}" && "${free_mb}" -lt "${UPGRADE_MIN_FREE_MB}" ]]; then
		log_fail "Upgrade preflight failed: ${data_dir} needs at least ${UPGRADE_MIN_FREE_MB}MB free space, but only ${free_mb}MB is available." 2
	fi
	free_mb="$(df -Pm "${INSTALL_DIR}" 2>/dev/null | awk 'NR==2 {print $4}')"
	if [[ -n "${free_mb}" && "${free_mb}" -lt "${UPGRADE_MIN_FREE_MB}" ]]; then
		log_fail "Upgrade preflight failed: ${INSTALL_DIR} needs at least ${UPGRADE_MIN_FREE_MB}MB free space, but only ${free_mb}MB is available." 2
	fi
	if agent_manages_service && agent_uses_systemd; then
		if ! hfl_systemctl cat hyperfilelens-agent.service >/dev/null 2>&1; then
			log_fail "Upgrade preflight failed: hyperfilelens-agent.service unit is missing." 2
		fi
	fi
	log_ok "upgrade preflight passed (min free ${UPGRADE_MIN_FREE_MB}MB)"
}

backup_upgrade_binaries() {
	local data_dir="$1"
	UPGRADE_BIN_BACKUP="${data_dir}/backup/rollback/bin"
	rm -rf "${data_dir}/backup/rollback"
	mkdir -p "${UPGRADE_BIN_BACKUP}"
	[[ -f "${INSTALL_DIR}/hfl-agent" ]] && cp -a "${INSTALL_DIR}/hfl-agent" "${UPGRADE_BIN_BACKUP}/"
	[[ -f "${INSTALL_DIR}/kopia" ]] && cp -a "${INSTALL_DIR}/kopia" "${UPGRADE_BIN_BACKUP}/"
	[[ -f "${INSTALL_DIR}/MANIFEST.json" ]] && cp -a "${INSTALL_DIR}/MANIFEST.json" "${UPGRADE_BIN_BACKUP}/"
	[[ -f "${INSTALLED_VERSION_FILE}" ]] && cp -a "${INSTALLED_VERSION_FILE}" "${UPGRADE_BIN_BACKUP}/"
	[[ -d "${INSTALL_DIR}/libexec" ]] && cp -a "${INSTALL_DIR}/libexec" "${UPGRADE_BIN_BACKUP}/"
	log_ok "backed up binaries -> ${UPGRADE_BIN_BACKUP}"
}

restore_upgrade_binaries() {
	[[ -n "${UPGRADE_BIN_BACKUP}" && -d "${UPGRADE_BIN_BACKUP}" ]] || return 0
	[[ -f "${UPGRADE_BIN_BACKUP}/hfl-agent" ]] && cp -a "${UPGRADE_BIN_BACKUP}/hfl-agent" "${INSTALL_DIR}/"
	[[ -f "${UPGRADE_BIN_BACKUP}/kopia" ]] && cp -a "${UPGRADE_BIN_BACKUP}/kopia" "${INSTALL_DIR}/"
	[[ -f "${UPGRADE_BIN_BACKUP}/MANIFEST.json" ]] && cp -a "${UPGRADE_BIN_BACKUP}/MANIFEST.json" "${INSTALL_DIR}/"
	[[ -f "${UPGRADE_BIN_BACKUP}/INSTALLED_VERSION" ]] && cp -a "${UPGRADE_BIN_BACKUP}/INSTALLED_VERSION" "${INSTALLED_VERSION_FILE}"
	rm -rf "${INSTALL_DIR}/libexec"
	if [[ -d "${UPGRADE_BIN_BACKUP}/libexec" ]]; then
		cp -a "${UPGRADE_BIN_BACKUP}/libexec" "${INSTALL_DIR}/"
	fi
	log_warn "restored binaries from ${UPGRADE_BIN_BACKUP}"
}

upgrade_rollback_on_error() {
	local rc=$?
	if [[ "${UPGRADE_SERVICE_STOPPED}" -eq 1 ]]; then
		log_warn "upgrade failed (exit=${rc}); attempting rollback"
		restore_upgrade_binaries || true
		if agent_manages_service; then
			start_service || true
		fi
	fi
	return "${rc}"
}

cleanup_upgrade_rollback() {
	local data_dir="$1"
	local rollback="${data_dir}/backup/rollback"
	if [[ -d "${rollback}" ]]; then
		rm -rf "${rollback}"
		log_ok "removed ${rollback} (upgrade succeeded; state snapshot retained)"
	fi
}

merge_agent_env() {
	local env_file="$1"
	local data_dir="$2"
	local kopia_path="${INSTALL_DIR}/kopia"
	local -a keys=(HFL_DATA_DIR HFL_INSTALLATION_MODE HFL_KOPIA_PATH HFL_INSECURE_TLS)
	local -a vals=("${data_dir}" "${INSTALLATION_MODE}" "${kopia_path}" "1")
	if [[ "${INSTALLATION_MODE}" == "account" ]]; then
		keys+=(HFL_RUN_AS_USER HFL_RUN_AS_HOME)
		vals+=("${RUN_AS_USER}" "${RUN_AS_HOME}")
	fi
	local optional
	for optional in SENTRY_ENABLED SENTRY_BACKEND_DSN SENTRY_ENVIRONMENT SENTRY_RELEASE SENTRY_TRACES_SAMPLE_RATE HFL_SENTRY_LENSNODE_RELEASE; do
		if [[ -n "${!optional:-}" && "${!optional}" != *$'\n'* && "${!optional}" != *$'\r'* ]]; then
			keys+=("${optional}")
			vals+=("${!optional}")
		fi
	done
	local i key val added=()

	if [[ ! -f "${env_file}" ]]; then
		mkdir -p "$(dirname "${env_file}")"
		: >"${env_file}"
		chmod 600 "${env_file}"
		for i in "${!keys[@]}"; do
			echo "${keys[$i]}=${vals[$i]}" >>"${env_file}"
		done
		log_ok "created ${env_file}"
		return 0
	fi

	for i in "${!keys[@]}"; do
		key="${keys[$i]}"
		val="${vals[$i]}"
		if ! grep -q "^${key}=" "${env_file}"; then
			echo "${key}=${val}" >>"${env_file}"
			added+=("${key}")
		fi
	done
	if ((${#added[@]} > 0)); then
		log_ok "merged agent.env keys: ${added[*]}"
	else
		log_ok "agent.env unchanged (no missing keys)"
	fi
}

migrate_agent_db() {
	local data_dir="$1"
	local agent="${INSTALL_DIR}/hfl-agent"
	[[ -x "${agent}" ]] || {
		log_skip "migrate agent.db (hfl-agent missing)"
		return 0
	}
	if "${agent}" tasks list --data-dir "${data_dir}" --limit 1 >/dev/null 2>&1; then
		log_ok "agent.db schema upgraded (if needed)"
	else
		log_warn "agent.db migration check failed (service start may retry)"
	fi
}

is_installed() {
	[[ -x "${INSTALL_DIR}/hfl-agent" ]]
}

read_env_value() {
	local f="$1" key="$2"
	[[ -f "$f" ]] || return 1
	local line val
	line="$(grep -E "^${key}=" "$f" | tail -n1 || true)"
	[[ -z "$line" ]] && return 1
	val="${line#${key}=}"
	val="${val%$'\r'}"
	printf '%s' "$val"
}

read_env_data_dir() {
	read_env_value "$1" "HFL_DATA_DIR"
}

service_status_line() {
	if agent_uses_launchd; then
		launchd_service_status_line
		return 0
	fi
	if ! command -v systemctl >/dev/null 2>&1; then
		echo "unavailable (no systemctl)"
		return 0
	fi
	local active enabled
	active="$(hfl_systemctl is-active hyperfilelens-agent.service 2>/dev/null || echo inactive)"
	enabled="$(hfl_systemctl is-enabled hyperfilelens-agent.service 2>/dev/null || echo disabled)"
	echo "${active} (${enabled})"
}

resolve_data_dir() {
	local env_file="${DEFAULT_DATA}/agent.env"
	local val=""
	val="$(read_env_data_dir "$env_file" || true)"
	if [[ -n "$val" ]]; then
		echo "$val"
	else
		echo "${DATA_DIR:-$DEFAULT_DATA}"
	fi
}

data_dir_allowed_for_removal() {
	local p="$1" parent leaf
	[[ -n "$p" && "$p" == /* ]] || return 1
	p="${p%/}"
	parent="$(dirname -- "$p")"
	leaf="$(basename -- "$p")"
	parent="$(cd -P -- "$parent" 2>/dev/null && pwd -P)" || return 1
	p="${parent%/}/${leaf}"
	if [[ "${INSTALLATION_MODE}" == "user" ]]; then
		local install_root data_root
		install_root="$(cd -P -- "$(dirname -- "${INSTALL_DIR}")" 2>/dev/null && pwd -P)/$(basename -- "${INSTALL_DIR}")" || return 1
		data_root="$(cd -P -- "$(dirname -- "${DEFAULT_DATA}")" 2>/dev/null && pwd -P)/$(basename -- "${DEFAULT_DATA}")" || return 1
		[[ "$p" == "$install_root" || "$p" == "$data_root" ]]
		return
	fi
	case "$p" in
		/var/lib/hyperfilelens-agent|/var/lib/hyperfilelens-agent/*) return 0 ;;
		/opt/hyperfilelens-agent|/opt/hyperfilelens-agent/*) return 0 ;;
		*) return 1 ;;
	esac
}

verify_bundle() {
	local agent kopia
	agent="$(bundle_agent)"
	kopia="$(bundle_kopia)"
	if [[ ! -f "$agent" ]]; then
		echo "ERROR: missing bundle binary: $agent" >&2
		if is_installed_script_location; then
			echo "Hint: run upgrade from an extracted release archive, or use remote agent.upgrade." >&2
		fi
		exit 2
	fi
	if [[ ! -f "$kopia" ]]; then
		echo "ERROR: missing bundle kopia: $kopia" >&2
		if is_installed_script_location; then
			echo "Hint: run upgrade from an extracted release archive, or use remote agent.upgrade." >&2
		fi
		exit 2
	fi
}

bundle_arch() {
	case "$(uname -m)" in
	x86_64 | amd64) echo amd64 ;;
	aarch64 | arm64) echo arm64 ;;
	*) echo "" ;;
	esac
}

nas_mount_helpers_ready() {
	local nfs_ok=0 cifs_ok=0
	if command -v mount.nfs >/dev/null 2>&1; then
		nfs_ok=1
	elif [[ -x /sbin/mount.nfs || -x /usr/sbin/mount.nfs ]]; then
		nfs_ok=1
	fi
	if command -v mount.cifs >/dev/null 2>&1; then
		cifs_ok=1
	elif [[ -x /sbin/mount.cifs || -x /usr/sbin/mount.cifs ]]; then
		cifs_ok=1
	fi
	if [[ $cifs_ok -eq 1 ]]; then
		local cifs_bin=""
		cifs_bin="$(command -v mount.cifs 2>/dev/null || true)"
		[[ -n "$cifs_bin" ]] || {
			[[ -x /sbin/mount.cifs ]] && cifs_bin="/sbin/mount.cifs"
			[[ -z "$cifs_bin" && -x /usr/sbin/mount.cifs ]] && cifs_bin="/usr/sbin/mount.cifs"
		}
		if [[ -z "$cifs_bin" ]] || ! "$cifs_bin" --version >/dev/null 2>&1; then
			cifs_ok=0
		fi
	fi
	[[ $nfs_ok -eq 1 && $cifs_ok -eq 1 ]]
}

cifs_utf8_module_ready() {
	if [[ -d /sys/module/nls_utf8 ]]; then
		return 0
	fi
	if [[ -r /proc/modules ]] && awk '$1 == "nls_utf8" { found = 1 } END { exit found ? 0 : 1 }' /proc/modules; then
		return 0
	fi
	if command -v modprobe >/dev/null 2>&1 && modprobe -n nls_utf8 >/dev/null 2>&1; then
		return 0
	fi
	return 1
}

warn_cifs_utf8_module_missing() {
	if ! cifs_utf8_module_ready; then
		log_warn 'SMB iocharset=utf8 support is not available (missing nls_utf8); install linux-modules-extra-$(uname -r), then run: modprobe nls_utf8'
	fi
}

install_nas_deps() {
	local role="${1:-}"
	local arch deps_dir ubuntu_release ubuntu_flavor

	[[ "$(uname -s)" == "Linux" ]] || return 0
	case "${role}" in
	proxy | gateway) ;;
	*) return 0 ;;
	esac
	if nas_mount_helpers_ready; then
		log_skip "install NAS packages (mount.nfs / mount.cifs already present)"
		warn_cifs_utf8_module_missing
		return 0
	fi

	arch="$(bundle_arch)"
	[[ -n "${arch}" ]] || {
		echo "ERROR: unsupported CPU arch for NAS dependency install" >&2
		exit 2
	}
	[[ -r /etc/os-release ]] || {
		echo "ERROR: /etc/os-release is required to select NAS dependencies" >&2
		exit 2
	}
	# shellcheck disable=SC1091
	. /etc/os-release
	[[ "${ID:-}" == "ubuntu" ]] || {
		echo "ERROR: offline NAS dependencies support Ubuntu only" >&2
		exit 2
	}
	ubuntu_release="${VERSION_ID:-}"
	case "${ubuntu_release}" in
	20.04) ubuntu_flavor=ubuntu2004 ;;
	22.04) ubuntu_flavor=ubuntu2204 ;;
	24.04) ubuntu_flavor=ubuntu2404 ;;
	*)
		echo "ERROR: offline NAS dependencies support Ubuntu 20.04, 22.04, or 24.04 (current: ${ubuntu_release:-unknown})" >&2
		exit 2
		;;
	esac
	deps_dir="${BUNDLE_ROOT}/deps/${ubuntu_flavor}/${arch}"
	if [[ ! -d "${deps_dir}" ]] || ! compgen -G "${deps_dir}/*.deb" >/dev/null; then
		echo "ERROR: NAS mount helpers missing and bundle has no deps/${ubuntu_flavor}/${arch}/*.deb" >&2
		echo "Use the hfl-agent archive matching Ubuntu ${ubuntu_release}, or install nfs-common and cifs-utils manually." >&2
		exit 2
	fi
	if ! command -v dpkg >/dev/null 2>&1; then
		echo "ERROR: dpkg is required to install bundled NAS dependencies" >&2
		exit 2
	fi

	log_ok "install NAS packages for role=${role} (offline ${ubuntu_flavor}/${arch})"
	local -a deb_files=()
	mapfile -t deb_files < <(find "${deps_dir}" -maxdepth 1 -type f -name '*.deb' -print | sort)
	local install_ok=0 attempt audit
	for attempt in 1 2 3; do
		if DEBIAN_FRONTEND=noninteractive dpkg -i "${deb_files[@]}"; then
			install_ok=1
			break
		fi
		log_warn "Offline NAS dependency install pass ${attempt}/3 reported unresolved package ordering; retrying..."
	done
	audit="$(dpkg --audit 2>&1 || true)"
	if [[ "${install_ok}" -ne 1 || -n "${audit}" ]]; then
		[[ -z "${audit}" ]] || printf '%s\n' "${audit}" >&2
		log_fail "Unable to install the complete Ubuntu ${ubuntu_release} NAS dependency closure offline (${arch})." 2
	fi
	if ! nas_mount_helpers_ready; then
		log_fail "NAS mount helpers are still missing after installing the Ubuntu ${ubuntu_release} bundled packages (${arch})." 2
	fi
	log_ok "NAS mount helpers ready (mount.nfs / mount.cifs)"
	warn_cifs_utf8_module_missing
}

deploy_admin_scripts() {
	local src_root="${1:-${BUNDLE_ROOT}}"
	local src_script="${src_root}/install.sh"
	local src_manifest="${src_root}/MANIFEST.json"
	local src_gateway_lifecycle="${src_root}/libexec/gateway-lifecycle.sh"
	[[ -f "$src_script" ]] || log_fail "Missing bundle installer: ${src_script}." 2
	install -m 755 "$src_script" "${INSTALL_DIR}/install.sh"
	log_ok "deployed ${INSTALL_DIR}/install.sh"
	if [[ -f "$src_manifest" ]]; then
		install -m 644 "$src_manifest" "${INSTALL_DIR}/MANIFEST.json"
		log_ok "deployed ${INSTALL_DIR}/MANIFEST.json"
	fi
	if [[ "$(uname -s)" == "Linux" && -f "${src_gateway_lifecycle}" ]]; then
		install -d -m 755 "${INSTALL_DIR}/libexec"
		install -m 755 "${src_gateway_lifecycle}" "${GATEWAY_LIFECYCLE_SCRIPT}"
		log_ok "deployed ${GATEWAY_LIFECYCLE_SCRIPT}"
	fi
}

deploy_binaries() {
	local src_root="${1:-${BUNDLE_ROOT}}"
	local deploy_agent=1 deploy_kopia=1 ver
	local agent_bin="${src_root}/bin/hfl-agent"
	local kopia_bin="${src_root}/bin/kopia"
	if [[ $AGENT_ONLY -eq 1 ]]; then deploy_kopia=0; fi
	if [[ $KOPIA_ONLY -eq 1 ]]; then deploy_agent=0; fi

	mkdir -p "${INSTALL_DIR}"
	if [[ $deploy_agent -eq 1 ]]; then
		[[ -f "$agent_bin" ]] || log_fail "Missing bundle binary: ${agent_bin}." 2
		install -m 755 "$agent_bin" "${INSTALL_DIR}/hfl-agent"
		log_ok "deployed ${INSTALL_DIR}/hfl-agent ($(bundle_version_from "${src_root}"))"
	fi
	if [[ $deploy_kopia -eq 1 ]]; then
		[[ -f "$kopia_bin" ]] || log_fail "Missing bundle binary: ${kopia_bin}." 2
		install -m 755 "$kopia_bin" "${INSTALL_DIR}/kopia"
		log_ok "deployed ${INSTALL_DIR}/kopia"
	fi
	ver="$(bundle_version_from "${src_root}")"
	echo "$ver" >"${INSTALLED_VERSION_FILE}"
	log_ok "wrote ${INSTALLED_VERSION_FILE} (${ver})"
	deploy_admin_scripts "${src_root}"
}

write_agent_env() {
	local env_file="$1"
	local kopia_path="${INSTALL_DIR}/kopia"
	local name
	mkdir -p "$(dirname "$env_file")"
	umask 077
	{
		[[ -n "${WSS_URL}" ]] && echo "HFL_WSS_URL=${WSS_URL}"
		[[ -n "${API_BASE}" ]] && echo "HFL_API_BASE=${API_BASE}"
		[[ -n "${ORG_KEY}" ]] && echo "HFL_ORG_KEY=${ORG_KEY}"
		[[ -n "${NODE_TOKEN}" ]] && echo "HFL_NODE_TOKEN=${NODE_TOKEN}"
		[[ -n "${NODE_ID}" ]] && echo "HFL_NODE_ID=${NODE_ID}"
		echo "HFL_DATA_DIR=${DATA_DIR}"
		echo "HFL_NODE_ROLE=${NODE_ROLE}"
		echo "HFL_INSTALLATION_MODE=${INSTALLATION_MODE}"
		[[ -z "${RUN_AS_USER}" ]] || echo "HFL_RUN_AS_USER=${RUN_AS_USER}"
		[[ -z "${RUN_AS_HOME}" ]] || echo "HFL_RUN_AS_HOME=${RUN_AS_HOME}"
		echo "HFL_KOPIA_PATH=${kopia_path}"
		echo "HFL_INSECURE_TLS=${HFL_INSECURE_TLS:-1}"
		for name in SENTRY_ENABLED SENTRY_BACKEND_DSN SENTRY_ENVIRONMENT SENTRY_RELEASE SENTRY_TRACES_SAMPLE_RATE HFL_SENTRY_LENSNODE_RELEASE; do
			[[ -z "${!name:-}" ]] || echo "${name}=${!name}"
		done
	} >"${env_file}"
	chmod 600 "${env_file}"
	log_ok "wrote ${env_file}"
}

systemd_escape_unit_value() {
	printf '%s' "$1" | sed \
		-e 's/\\/\\\\/g' \
		-e 's/"/\\"/g' \
		-e 's/%/%%/g'
}

install_systemd_unit() {
	local env_file="$1"
	local src_root="${2:-${BUNDLE_ROOT}}"
	local unit_src="${src_root}/systemd/hyperfilelens-agent.service"
	mkdir -p "$(dirname "${UNIT_DST}")"
	if [[ "${INSTALLATION_MODE}" == "user" ]]; then
		local unit_env_file unit_working_dir unit_agent
		unit_env_file="$(systemd_escape_unit_value "${env_file}")"
		unit_working_dir="$(systemd_escape_unit_value "${INSTALL_DIR}")"
		unit_agent="$(systemd_escape_unit_value "${INSTALL_DIR}/hfl-agent")"
		cat >"${UNIT_DST}" <<EOF
[Unit]
Description=HyperFileLens Agent (Current User)
StartLimitIntervalSec=0

[Service]
Type=simple
EnvironmentFile=${unit_env_file}
WorkingDirectory=${unit_working_dir}
ExecStart="${unit_agent}" run
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=default.target
EOF
		return 0
	fi
	if [[ -f "$unit_src" ]]; then
		cp -f "$unit_src" "$UNIT_DST"
	else
		cat >"$UNIT_DST" <<EOF
[Unit]
Description=HyperFileLens Agent
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
EnvironmentFile=${env_file}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/hfl-agent run
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
	fi
	if [[ "${INSTALLATION_MODE}" == "account" && "$(uname -s)" == "Linux" ]]; then
		sed -i '/^User=/d' "${UNIT_DST}"
		sed -i "/^\[Service\]/a User=${RUN_AS_USER}" "${UNIT_DST}"
		sed -i 's/^Description=.*/Description=HyperFileLens Agent (Specified User Continuous)/' "${UNIT_DST}"
	fi
	if [[ "$(uname -s)" == "Darwin" ]]; then
		sed -i '' "s#^EnvironmentFile=.*#EnvironmentFile=${env_file}#" "$UNIT_DST"
		sed -i '' "s#^WorkingDirectory=.*#WorkingDirectory=${INSTALL_DIR}#" "$UNIT_DST"
		sed -i '' "s#^ExecStart=.*#ExecStart=${INSTALL_DIR}/hfl-agent run#" "$UNIT_DST"
	else
		sed -i "s#^EnvironmentFile=.*#EnvironmentFile=${env_file}#" "$UNIT_DST"
		sed -i "s#^WorkingDirectory=.*#WorkingDirectory=${INSTALL_DIR}#" "$UNIT_DST"
		sed -i "s#^ExecStart=.*#ExecStart=${INSTALL_DIR}/hfl-agent run#" "$UNIT_DST"
	fi
}

configure_gateway_resource_policy() {
	local env_file="$1" role=""
	[[ "${INSTALLATION_MODE}" == "system" ]] || return 0
	[[ "$(uname -s)" == "Linux" ]] || return 0
	role="$(read_env_value "${env_file}" "HFL_NODE_ROLE" || true)"
	if [[ "${role}" != "gateway" ]]; then
		rm -f "${GATEWAY_RESOURCE_DROPIN}"
		return 0
	fi
	install -d -m 755 "$(dirname "${GATEWAY_RESOURCE_DROPIN}")"
	cat >"${GATEWAY_RESOURCE_DROPIN}" <<'EOF'
[Service]
CPUAccounting=true
CPUQuota=50%
CPUWeight=50
IOAccounting=true
IOWeight=50
MemoryAccounting=true
MemoryHigh=512M
TasksMax=256
EOF
	chmod 644 "${GATEWAY_RESOURCE_DROPIN}"
	log_ok "installed Data Gateway soft resource policy ${GATEWAY_RESOURCE_DROPIN}"
}

gateway_resource_preflight() {
	local role="$1" data_dir="$2" available_kb=0 free_kb=0 check_path=""
	[[ "${role}" == "gateway" && "$(uname -s)" == "Linux" ]] || return 0
	available_kb="$(awk '/^MemAvailable:/ {print $2; exit}' /proc/meminfo 2>/dev/null || true)"
	if [[ "${available_kb}" =~ ^[0-9]+$ ]]; then
		if [[ "${available_kb}" -lt 1048576 ]]; then
			log_warn "Data Gateway has less than 1GiB available memory ($((available_kb / 1024))MiB); installation will continue."
		elif [[ "${available_kb}" -lt 2097152 ]]; then
			log_warn "Data Gateway has less than 2GiB available memory ($((available_kb / 1024))MiB)."
		else
			log_ok "Data Gateway memory preflight passed ($((available_kb / 1024))MiB available)"
		fi
	fi
	check_path="${data_dir}"
	while [[ ! -e "${check_path}" && "${check_path}" != "/" ]]; do
		check_path="$(dirname "${check_path}")"
	done
	free_kb="$(df -Pk "${check_path}" 2>/dev/null | awk 'NR==2 {print $4}' || true)"
	if [[ "${free_kb}" =~ ^[0-9]+$ ]]; then
		if [[ "${free_kb}" -lt 10485760 ]]; then
			log_warn "Data Gateway has less than 10GiB free disk space ($((free_kb / 1024))MiB); installation will continue."
		elif [[ "${free_kb}" -lt 20971520 ]]; then
			log_warn "Data Gateway has less than 20GiB free disk space ($((free_kb / 1024))MiB)."
		else
			log_ok "Data Gateway disk preflight passed ($((free_kb / 1024))MiB free)"
		fi
	fi
	if [[ -r /proc/swaps ]] && [[ "$(wc -l </proc/swaps)" -le 1 ]]; then
		log_warn "No swap is configured; monitor host memory pressure for this Data Gateway."
	fi
}

stop_service() {
	if agent_uses_launchd; then
		stop_launchd_service
		return 0
	fi
	if ! command -v systemctl >/dev/null 2>&1; then
		log_skip "stop hyperfilelens-agent.service (systemctl not found)"
		return 0
	fi
	local state
	state="$(hfl_systemctl is-active hyperfilelens-agent.service 2>/dev/null || echo inactive)"
	case "$state" in
		active|activating|deactivating)
			log_step "stopping hyperfilelens-agent.service"
			hfl_systemctl stop --no-block hyperfilelens-agent.service 2>/dev/null || true
			local waited=0
			while [[ $waited -lt 45 ]]; do
				state="$(hfl_systemctl is-active hyperfilelens-agent.service 2>/dev/null || echo inactive)"
				case "$state" in
					inactive|failed) break ;;
				esac
				sleep 1
				waited=$((waited + 1))
			done
			if hfl_systemctl is-active hyperfilelens-agent.service >/dev/null 2>&1; then
				log_warn "service stop timed out; sending SIGKILL"
				hfl_systemctl kill --signal=SIGKILL hyperfilelens-agent.service 2>/dev/null || true
				sleep 1
			fi
			log_ok "stopped service hyperfilelens-agent.service"
			;;
		*)
			log_skip "stop hyperfilelens-agent.service (not active)"
			;;
	esac
}

disable_service() {
	if ! command -v systemctl >/dev/null 2>&1; then
		log_skip "disable hyperfilelens-agent.service (systemctl not found)"
		return 0
	fi
	if hfl_systemctl is-enabled hyperfilelens-agent.service >/dev/null 2>&1; then
		hfl_systemctl disable hyperfilelens-agent.service 2>/dev/null || true
		log_ok "disabled service hyperfilelens-agent.service"
	else
		log_skip "disable hyperfilelens-agent.service (not enabled)"
	fi
}

remove_systemd_unit() {
	if [[ "${INSTALLATION_MODE}" == "system" && -f "${GATEWAY_RESOURCE_DROPIN}" ]]; then
		rm -f "${GATEWAY_RESOURCE_DROPIN}"
		rmdir "$(dirname "${GATEWAY_RESOURCE_DROPIN}")" 2>/dev/null || true
		log_ok "removed Data Gateway resource policy ${GATEWAY_RESOURCE_DROPIN}"
	fi
	if [[ -f "$UNIT_DST" ]]; then
		rm -f "$UNIT_DST"
		log_ok "removed unit ${UNIT_DST}"
	else
		log_skip "remove unit ${UNIT_DST} (not present)"
	fi
	if command -v systemctl >/dev/null 2>&1; then
		hfl_systemctl daemon-reload 2>/dev/null || true
		log_ok "reloaded systemd"
	fi
}

install_systemd_unit_logged() {
	local env_file="$1"
	local src_root="${2:-${BUNDLE_ROOT}}"
	install_systemd_unit "$env_file" "$src_root"
	configure_gateway_resource_policy "$env_file"
	log_ok "installed unit ${UNIT_DST}"
}

remove_service_unit() {
	if agent_uses_launchd; then
		remove_launchd_plist
		return 0
	fi
	disable_service
	remove_systemd_unit
}

start_service() {
	if agent_uses_launchd; then
		local env_file="${DEFAULT_DATA}/agent.env"
		local resolved
		resolved="$(resolve_data_dir)"
		[[ -f "${resolved}/agent.env" ]] && env_file="${resolved}/agent.env"
		start_launchd_service "${env_file}"
		return 0
	fi
	if ! agent_uses_systemd; then
		log_skip "start hyperfilelens-agent.service (systemd not available)"
		return 0
	fi
	hfl_systemctl daemon-reload
	hfl_systemctl enable hyperfilelens-agent.service
	log_ok "enabled service hyperfilelens-agent.service"
	hfl_systemctl restart hyperfilelens-agent.service
	if hfl_systemctl is-active hyperfilelens-agent.service >/dev/null 2>&1; then
		log_ok "started service hyperfilelens-agent.service ($(service_status_line))"
	else
		log_warn "hyperfilelens-agent.service is not active after start"
	fi
}

start_service_only() {
	if agent_uses_launchd; then
		start_launchd_service_only
		return 0
	fi
	if ! agent_uses_systemd; then
		log_fail "Systemd is not available on this host." 2
	fi
	hfl_systemctl start hyperfilelens-agent.service
	log_ok "started service hyperfilelens-agent.service ($(service_status_line))"
}

cmd_install() {
	parse_install_flags "$@"
	require_root
	if [[ "${INSTALLATION_MODE}" != "system" && "${NODE_ROLE}" != "agent" ]]; then
		log_fail "User-scoped installation is only available for Source Agent." 2
	fi
	if [[ "${INSTALLATION_MODE}" == "account" ]]; then
		if [[ -z "${RUN_AS_USER}" ]]; then
			local default_run_as_user="${SUDO_USER:-}"
			read -r -p "Enter the existing ordinary account to protect${default_run_as_user:+ [${default_run_as_user}]}: " RUN_AS_USER
			RUN_AS_USER="${RUN_AS_USER:-${default_run_as_user}}"
		fi
		[[ -n "${RUN_AS_USER}" ]] || log_fail "An account is required for specified-user continuous protection." 2
		id "${RUN_AS_USER}" >/dev/null 2>&1 || log_fail "The specified account '${RUN_AS_USER}' does not exist." 2
		[[ "$(id -u "${RUN_AS_USER}")" -ne 0 ]] || log_fail "Specified-user continuous protection requires a non-root account." 2
		if [[ "$(uname -s)" == "Darwin" ]]; then
			RUN_AS_HOME="$(dscl . -read "/Users/${RUN_AS_USER}" NFSHomeDirectory 2>/dev/null | awk '{$1=""; sub(/^ /, ""); print}')"
		else
			RUN_AS_HOME="$(getent passwd "${RUN_AS_USER}" 2>/dev/null | cut -d: -f6)"
		fi
		[[ -n "${RUN_AS_HOME}" && -d "${RUN_AS_HOME}" ]] || log_fail "The home directory for '${RUN_AS_USER}' could not be resolved." 2
		export HFL_RUN_AS_USER="${RUN_AS_USER}"
		export HFL_RUN_AS_HOME="${RUN_AS_HOME}"
	fi
	require_service_manager
	verify_bundle

	if is_installed; then
		local command_prefix=""
		[[ "${INSTALLATION_MODE}" == "system" ]] && command_prefix="sudo "
		log_fail "The agent is already installed. Run ${command_prefix}./install.sh upgrade --from <package.tar.gz> instead." 2
	fi

	DATA_DIR="${DATA_DIR:-$DEFAULT_DATA}"
	if [[ "${INSTALLATION_MODE}" == "user" ]]; then
		[[ "${DATA_DIR}" == "${DEFAULT_DATA}" ]] \
			|| log_fail "User-level installation uses the fixed data directory ${DEFAULT_DATA}; --data-dir is not supported." 2
		mkdir -p "${DATA_DIR}"
		chmod 700 "${DATA_DIR}"
	fi
	if [[ "${INSTALLATION_MODE}" == "account" ]]; then
		mkdir -p "${DATA_DIR}"
		chown -R "${RUN_AS_USER}:" "${DATA_DIR}" 2>/dev/null || chown -R "${RUN_AS_USER}" "${DATA_DIR}"
		chmod 700 "${DATA_DIR}"
	fi
	begin_install_log "${DATA_DIR}"
	trap 'finish_install_log $?' RETURN

	if [[ $QUIET_FOOTER -eq 0 ]]; then
		hfl_print_banner "$(hfl_role_display_name "${NODE_ROLE}" "${HFL_GATEWAY_SCOPE:-}")" "Installer"
		hfl_print_section "Target"
		hfl_print_value "Console" "${API_BASE}"
		hfl_print_value "Organization" "${ORG_KEY}"
		hfl_print_value "Role" "$(hfl_role_display_name "${NODE_ROLE}" "${HFL_GATEWAY_SCOPE:-}")"
		hfl_print_value "Installation mode" "${INSTALLATION_MODE}"
		hfl_print_value "Agent version" "$(bundle_version)"
		hfl_print_value "Platform" "$(uname -s | tr '[:upper:]' '[:lower:]')/$(bundle_arch)"
		hfl_print_value "Install path" "${INSTALL_DIR}"
		hfl_print_value "Data path" "${DATA_DIR}"
		hfl_print_section "Preflight checks"
		if [[ "${INSTALLATION_MODE}" == "user" ]]; then
			log_ok "Current-user privileges and user service manager are available."
		else
			log_ok "Administrator privileges and service manager are available."
		fi
		log_ok "Agent package layout is valid."
		hfl_print_section "Installing Agent"
	fi

	gateway_resource_preflight "${NODE_ROLE}" "${DATA_DIR}"
	install_nas_deps "${NODE_ROLE}"
	deploy_binaries
	write_agent_env "${DATA_DIR}/agent.env"
	if [[ "${INSTALLATION_MODE}" == "account" ]]; then
		chown "${RUN_AS_USER}" "${DATA_DIR}/agent.env" "${DATA_DIR}" 2>/dev/null || true
		chown -R "${RUN_AS_USER}" "${DATA_DIR}/logs" 2>/dev/null || true
	fi

	if agent_uses_launchd; then
		if [[ $NO_START -eq 1 ]]; then
			write_run_agent_script "${DATA_DIR}/agent.env"
			install_launchd_plist "${DATA_DIR}/agent.env"
			if [[ $QUIET_FOOTER -eq 0 ]]; then
				log_skip "Launchd service ${LAUNCHD_LABEL} was not started (--no-start)."
				hfl_print_install_success "$(hfl_role_display_name "${NODE_ROLE}" "${HFL_GATEWAY_SCOPE:-}")" "$(bundle_version)" "not started" "${DATA_DIR}"
			fi
			return 0
		fi
		start_launchd_service "${DATA_DIR}/agent.env"
		if [[ $QUIET_FOOTER -eq 0 ]]; then
			hfl_print_install_success "$(hfl_role_display_name "${NODE_ROLE}" "${HFL_GATEWAY_SCOPE:-}")" "$(bundle_version)" "$(launchd_service_status_line)" "${DATA_DIR}"
		fi
		return 0
	fi

	if ! agent_uses_systemd; then
		if [[ $QUIET_FOOTER -eq 0 ]]; then
			log_warn "No supported service manager was found; start the Agent manually."
			hfl_print_install_success "$(hfl_role_display_name "${NODE_ROLE}" "${HFL_GATEWAY_SCOPE:-}")" "$(bundle_version)" "not managed" "${DATA_DIR}"
		fi
		return 0
	fi

	install_systemd_unit_logged "${DATA_DIR}/agent.env"
	if [[ $NO_START -eq 1 ]]; then
		hfl_systemctl daemon-reload 2>/dev/null || true
		log_ok "Systemd was reloaded successfully."
		if [[ $QUIET_FOOTER -eq 0 ]]; then
			log_skip "Service hyperfilelens-agent.service was not started (--no-start)."
			hfl_print_install_success "$(hfl_role_display_name "${NODE_ROLE}" "${HFL_GATEWAY_SCOPE:-}")" "$(bundle_version)" "not started" "${DATA_DIR}"
		fi
		return 0
	fi

	start_service
	if [[ $QUIET_FOOTER -eq 0 ]]; then
		hfl_print_install_success "$(hfl_role_display_name "${NODE_ROLE}" "${HFL_GATEWAY_SCOPE:-}")" "$(bundle_version)" "$(service_status_line)" "${DATA_DIR}"
	fi
}

cmd_upgrade() {
	parse_upgrade_flags "$@"
	require_root
	require_service_manager

	[[ -n "${UPGRADE_FROM}" ]] || log_fail "Upgrade requires --from <directory-or.tar.gz>." 2

	if ! is_installed; then
		local command_prefix=""
		[[ "${INSTALLATION_MODE}" == "system" ]] && command_prefix="sudo "
		log_fail "The agent is not installed. Run ${command_prefix}./install.sh install first." 2
	fi

	local data_dir prev_ver src_root new_ver env_file upgrade_ws
	data_dir="$(resolve_data_dir)"
	env_file="${data_dir}/agent.env"
	upgrade_ws="$(upgrade_workspace_dir "${data_dir}")"
	prev_ver="unknown"
	[[ -f "$INSTALLED_VERSION_FILE" ]] && prev_ver="$(tr -d ' \t\r\n' <"$INSTALLED_VERSION_FILE")"
	begin_install_log "${data_dir}" "upgrade"
	trap 'finish_install_log $?' RETURN

	trap 'rc=$?; cleanup_upgrade_workspace "$upgrade_ws"; hfl_finalize_active_log "$rc"' EXIT
	src_root="$(prepare_upgrade_source "${UPGRADE_FROM}" "${data_dir}")"
	new_ver="$(bundle_version_from "${src_root}")"

	if [[ "${new_ver}" == "${prev_ver}" ]]; then
		confirm_same_version_upgrade "${prev_ver}"
	elif [[ "${prev_ver}" != "unknown" && "${new_ver}" != "unknown" ]] \
		&& ! is_main_build "${new_ver}" \
		&& ! is_main_build "${prev_ver}" \
		&& version_lt "${new_ver}" "${prev_ver}"; then
		log_fail "Downgrade is not supported (${new_ver} < ${prev_ver})." 2
	fi

	if [[ $QUIET_FOOTER -eq 0 ]]; then
		local installed_role gateway_scope
		installed_role="$(read_env_value "${env_file}" "HFL_NODE_ROLE" || true)"
		gateway_scope="$(read_env_value "${env_file}" "HFL_GATEWAY_SCOPE" || true)"
		hfl_print_banner "$(hfl_role_display_name "${installed_role}" "${gateway_scope}")" "Upgrade"
		hfl_print_section "Target"
		hfl_print_value "Role" "$(hfl_role_display_name "${installed_role}" "${gateway_scope}")"
		hfl_print_value "Current version" "${prev_ver}"
		hfl_print_value "Target version" "${new_ver}"
		hfl_print_value "Install path" "${INSTALL_DIR}"
		hfl_print_value "Data path" "${data_dir}"
		hfl_print_section "Preflight checks"
	fi

	upgrade_preflight "${data_dir}"
	hfl_print_section "Upgrading Agent"
	backup_upgrade_binaries "${data_dir}"
	trap upgrade_rollback_on_error ERR

	stop_service
	UPGRADE_SERVICE_STOPPED=1
	backup_agent_config_and_db "${data_dir}" "${prev_ver}"
	deploy_binaries "${src_root}"
	merge_agent_env "${env_file}" "${data_dir}"
	migrate_agent_db "${data_dir}"

	if agent_uses_launchd; then
		write_run_agent_script "${env_file}"
		install_launchd_plist "${env_file}"
	elif agent_uses_systemd && [[ -f "${src_root}/systemd/hyperfilelens-agent.service" ]]; then
		install_systemd_unit_logged "${env_file}" "${src_root}"
	fi

	trap - ERR
	UPGRADE_SERVICE_STOPPED=0

	cleanup_upgrade_workspace "${upgrade_ws}"
	cleanup_upgrade_rollback "${data_dir}"
	trap - EXIT

	if [[ $NO_RESTART -eq 1 ]]; then
		if [[ $QUIET_FOOTER -eq 0 ]]; then
			log_skip "Service $(service_display_name) was not restarted (--no-restart)."
			hfl_print_upgrade_success "${new_ver}" "not restarted" "${data_dir}"
		fi
		return 0
	fi

	if agent_manages_service; then
		start_service
	fi

	if [[ $QUIET_FOOTER -eq 0 ]]; then
		hfl_print_upgrade_success "${new_ver}" "$(service_status_line)" "${data_dir}"
	fi
}

collect_agent_mount_points() {
	local mounts_root="$1" targets=""

	[[ -d "$mounts_root" ]] || return 0

	case "$(uname -s)" in
		Linux)
			if command -v findmnt >/dev/null 2>&1; then
				if targets="$(LC_ALL=C findmnt -rn -o TARGET 2>/dev/null)"; then
					printf '%s\n' "$targets" | awk -v root="$mounts_root" '
						BEGIN { len = length(root) }
						length($0) >= len && substr($0, 1, len) == root &&
							(length($0) == len || substr($0, len + 1, 1) == "/") {
							print $0
						}
					'
					return 0
				fi
			fi
			[[ -r /proc/mounts ]] || return 0
			awk -v root="$mounts_root" '
				BEGIN { len = length(root) }
				length($2) >= len && substr($2, 1, len) == root &&
					(length($2) == len || substr($2, len + 1, 1) == "/") {
					print $2
				}
			' /proc/mounts
			;;
		Darwin)
			mount | awk -v root="$mounts_root" '
				BEGIN { len = length(root) }
				{
					mp = $3
					if (length(mp) >= len && substr(mp, 1, len) == root &&
						(length(mp) == len || substr(mp, len + 1, 1) == "/")) {
						print mp
					}
				}
			'
			;;
	esac
}

sort_mount_points_deepest_first() {
	awk -F/ '{ print NF, $0 }' | sort -t' ' -k1,1rn | cut -d' ' -f2-
}

agent_mount_point_is_active() {
	local mounts_root="$1" point="$2"
	collect_agent_mount_points "$mounts_root" | grep -Fqx -- "$point"
}

run_managed_umount() {
	if command -v timeout >/dev/null 2>&1; then
		timeout 10 umount "$@"
	else
		umount "$@"
	fi
}

try_umount_point() {
	local mounts_root="$1" point="$2"
	local msg=""

	if run_managed_umount "$point" 2>/dev/null \
		&& ! agent_mount_point_is_active "$mounts_root" "$point"; then
		log_ok "unmounted ${point}"
		return 0
	fi
	if [[ "$(uname -s)" == "Linux" ]] \
		&& run_managed_umount -l "$point" 2>/dev/null \
		&& ! agent_mount_point_is_active "$mounts_root" "$point"; then
		log_ok "lazy-unmounted ${point}"
		return 0
	fi
	if run_managed_umount -f "$point" 2>/dev/null \
		&& ! agent_mount_point_is_active "$mounts_root" "$point"; then
		log_ok "force-unmounted ${point}"
		return 0
	fi
	msg="$(run_managed_umount "$point" 2>&1 || true)"
	if [[ -z "$msg" ]]; then
		msg="mount is still active after unmount attempts"
	fi
	log_warn "failed to unmount ${point}${msg:+: ${msg}}"
	return 1
}

unmount_agent_mounts() {
	local data_dir="$1"
	local mounts_root="${data_dir%/}/mounts"
	local -a points=() remaining=()
	local point failed=0

	mapfile -t points < <(
		collect_agent_mount_points "$mounts_root" | sort -u | sort_mount_points_deepest_first
	)

	if [[ ${#points[@]} -eq 0 ]]; then
		log_skip "no active mounts under ${mounts_root}"
		return 0
	fi

	log_step "unmounting NAS shares under ${mounts_root}"
	for point in "${points[@]}"; do
		[[ -n "$point" ]] || continue
		try_umount_point "$mounts_root" "$point" || failed=1
	done
	mapfile -t remaining < <(
		collect_agent_mount_points "$mounts_root" | sort -u | sort_mount_points_deepest_first
	)
	if [[ ${#remaining[@]} -gt 0 ]]; then
		for point in "${remaining[@]}"; do
			log_warn "Agent-managed mount remains active: ${point}"
		done
		failed=1
	fi
	return "$failed"
}

remove_install_file() {
	local path="$1"
	if [[ -e "$path" ]]; then
		rm -f "$path"
		log_ok "removed ${path}"
	else
		log_skip "remove ${path} (not present)"
	fi
}

retire_installation_identity() {
	local data_dir="$1" agent_bin="${INSTALL_DIR}/hfl-agent"
	# Incomplete-install rollback keeps the identity so retries reuse the console record.
	[[ "${KEEP_INSTALLATION_IDENTITY}" -eq 0 ]] || return 0
	# --purge-all deletes agent.env entirely, so retirement is unnecessary.
	[[ "${PURGE_ALL}" -eq 0 ]] || return 0
	[[ -x "${agent_bin}" ]] \
		|| log_fail "Cannot retire the installation identity because ${agent_bin} is unavailable." 1
	log_step "Retiring the local installation identity."
	if ! HFL_DATA_DIR="${data_dir}" \
		"${agent_bin}" config retire-installation --data-dir "${data_dir}"; then
		log_fail "Failed to retire the local installation identity; Agent files and data were preserved for retry." 1
	fi
	log_ok "Local installation identity retired; the existing console record is preserved and the next installation will register a new record."
}

uninstall_gateway_sidecar_if_needed() {
	local env_file="$1" role="" purge_args=()
	[[ "${HFL_SKIP_GATEWAY_SIDECAR_UNINSTALL:-0}" != "1" ]] || return 0
	role="$(read_env_value "${env_file}" "HFL_NODE_ROLE" || true)"
	[[ "${role}" == "gateway" ]] || return 0
	[[ "$(uname -s)" == "Linux" ]] \
		|| log_fail "Data Gateway AI engine uninstall is supported on Linux only." 2
	[[ -x "${GATEWAY_LIFECYCLE_SCRIPT}" ]] \
		|| log_fail "Missing ${GATEWAY_LIFECYCLE_SCRIPT}; upgrade the Agent before uninstalling this Data Gateway." 2
	[[ "${PURGE_ALL}" -eq 0 ]] || purge_args+=(--purge-all)
	log_step "Removing the Data Gateway AI engine before the Agent."
	HFL_AGENT_ENV_FILE="${env_file}" \
		bash "${GATEWAY_LIFECYCLE_SCRIPT}" uninstall-sidecar "${purge_args[@]}"
	log_ok "Data Gateway AI engine removal completed."
}

cmd_uninstall() {
	parse_uninstall_flags "$@"
	require_root

	local resolved_data env_file
	resolved_data="$(resolve_data_dir)"
	env_file="${resolved_data}/agent.env"
	if [[ "${PURGE_ALL}" -eq 1 ]] \
		&& ! data_dir_allowed_for_removal "${resolved_data}"; then
		log_fail "Refusing purge-all for unexpected data directory ${resolved_data}." 2
	fi
	begin_uninstall_log "${resolved_data}"
	trap 'hfl_finalize_active_log $?' EXIT

	local installed_role gateway_scope node_id installed_version data_policy
	installed_role="$(read_env_value "${env_file}" "HFL_NODE_ROLE" || true)"
	gateway_scope="$(read_env_value "${env_file}" "HFL_GATEWAY_SCOPE" || true)"
	node_id="$(read_env_value "${env_file}" "HFL_NODE_ID" || true)"
	installed_version="unknown"
	[[ -f "${INSTALLED_VERSION_FILE}" ]] && installed_version="$(tr -d ' \t\r\n' <"${INSTALLED_VERSION_FILE}")"
	data_policy="preserve"
	[[ "${PURGE_ALL}" -eq 0 ]] || data_policy="remove"
	hfl_print_banner "$(hfl_role_display_name "${installed_role}" "${gateway_scope}")" "Uninstaller"
	hfl_print_section "Target"
	hfl_print_value "Role" "$(hfl_role_display_name "${installed_role}" "${gateway_scope}")"
	hfl_print_value "Node ID" "${node_id}"
	hfl_print_value "Agent version" "${installed_version}"
	hfl_print_value "Service state" "$(service_status_line)"
	hfl_print_value "Install path" "${INSTALL_DIR}"
	hfl_print_value "Data path" "${resolved_data}"
	hfl_print_value "Data removal" "${data_policy}"
	hfl_print_section "Preflight checks"
	if [[ "${PURGE_ALL}" -eq 0 && "${KEEP_INSTALLATION_IDENTITY}" -eq 0 \
		&& ! -x "${INSTALL_DIR}/hfl-agent" ]]; then
		log_fail "Cannot retire the installation identity because ${INSTALL_DIR}/hfl-agent is unavailable." 1
	fi
	log_ok "Installed Agent paths and data policy were verified."
	hfl_print_section "Uninstalling"
	uninstall_gateway_sidecar_if_needed "${env_file}"

	stop_service
	if ! unmount_agent_mounts "$resolved_data"; then
		log_fail "Agent-managed NAS mount cleanup failed; Agent files and data were preserved for manual retry." 1
	fi
	remove_service_unit
	retire_installation_identity "${resolved_data}"

	remove_install_file "${INSTALL_DIR}/hfl-agent"
	remove_install_file "${INSTALL_DIR}/kopia"
	remove_install_file "${INSTALL_DIR}/run-agent.sh"
	remove_install_file "${INSTALL_DIR}/install.sh"
	remove_install_file "${INSTALL_DIR}/MANIFEST.json"
	remove_install_file "${INSTALLED_VERSION_FILE}"
	if data_dir_allowed_for_removal "${INSTALL_DIR}" && [[ -e "${INSTALL_DIR}" ]]; then
		rm -rf "${INSTALL_DIR}"
		log_ok "Install directory removed (${INSTALL_DIR}, including backup artifacts)."
	else
		if [[ -d "${INSTALL_DIR}/backup" ]]; then
			rm -rf "${INSTALL_DIR}/backup"
			log_ok "Removed ${INSTALL_DIR}/backup."
		fi
		if rmdir "${INSTALL_DIR}" 2>/dev/null; then
			log_ok "Install directory removed (${INSTALL_DIR})."
		else
			log_skip "Install directory ${INSTALL_DIR} was not removed (not empty or not present)."
		fi
	fi
	if [[ $PURGE_ALL -eq 1 && -f "$env_file" ]]; then
		rm -f "$env_file"
		log_ok "Removed ${env_file}."
	elif [[ -f "$env_file" ]]; then
		if [[ "${KEEP_INSTALLATION_IDENTITY}" -eq 1 ]]; then
			log_skip "${env_file} and installation identity were preserved for install retry."
		else
			log_skip "${env_file} was preserved without installation identity (use --purge-all to remove it)."
		fi
	else
		log_skip "${env_file} was not present."
	fi

	if [[ $PURGE_ALL -eq 0 ]]; then
		log_skip "Data directory ${resolved_data} was preserved (use --purge-all to remove it)."
	elif data_dir_allowed_for_removal "$resolved_data" && [[ -e "$resolved_data" ]]; then
		if rm -rf "$resolved_data"; then
			[[ ! -e "$resolved_data" ]] \
				|| log_fail "Data directory ${resolved_data} remains after removal." 1
			log_ok "Data directory removed (${resolved_data})."
		else
			log_fail "Failed to remove data directory ${resolved_data}." 1
		fi
	elif [[ -n "$resolved_data" ]]; then
		log_warn "Data directory ${resolved_data} is outside allowed prefixes and was not deleted."
	else
		log_skip "No data directory was resolved for removal."
	fi

	hfl_print_section "Verifying"
	log_ok "Agent service and installed files were removed."
	hfl_print_result "Uninstallation completed successfully"
	hfl_print_section "Uninstallation summary"
	hfl_print_value "Node ID" "${node_id}"
	hfl_print_value "Service" "removed"
	hfl_print_value "Install path" "removed"
	hfl_print_value "Data path" "$([[ "${PURGE_ALL}" -eq 1 ]] && echo removed || echo preserved)"
	hfl_print_value "Console record" "not changed by local uninstall"
	if [[ "${PURGE_ALL}" -eq 0 ]]; then
		hfl_print_value "Log file" "${resolved_data}/logs/uninstall.log"
	fi
	finish_uninstall_log 0
	trap - EXIT
}

cmd_status() {
	local installed="unknown" env_file="${DEFAULT_DATA}/agent.env"
	local node_id="" wss="" svc_line

	log_info "HyperFileLens agent status report."
	if is_installed; then
		[[ -f "$INSTALLED_VERSION_FILE" ]] && installed="$(tr -d ' \t\r\n' <"$INSTALLED_VERSION_FILE")"
		node_id="$(read_env_value "$env_file" "HFL_NODE_ID" || true)"
		wss="$(read_env_value "$env_file" "HFL_WSS_URL" || true)"
		svc_line="$(service_status_line)"
		log_info "Agent is installed (version ${installed}, bundle $(bundle_version))."
		log_info "Install dir: ${INSTALL_DIR} · Data dir: $(resolve_data_dir) · Service: $(service_display_name) (${svc_line})."
		if [[ -n "$node_id" ]]; then
			log_info "Node id: ${node_id}."
		else
			log_info "Node id: not registered."
		fi
		if [[ -n "$wss" ]]; then
			log_info "WebSocket URL is configured."
		else
			log_info "WebSocket URL is not configured."
		fi
	else
		log_info "Agent is not installed (bundle $(bundle_version))."
	fi
}

cmd_start() {
	require_root
	require_agent_installed
	log_step "Starting $(service_display_name)."
	start_service_only
	log_ok "Service $(service_display_name) is $(service_status_line)."
}

cmd_stop() {
	require_root
	require_agent_installed
	log_step "Stopping $(service_display_name)."
	stop_service
	log_ok "Service $(service_display_name) is $(service_status_line)."
}

cmd_restart() {
	require_root
	require_agent_installed
	log_step "Restarting $(service_display_name)."
	stop_service
	if agent_manages_service; then
		start_service_only
	fi
	log_ok "Service $(service_display_name) is $(service_status_line)."
}

case "$CMD" in
	install) cmd_install "$@" ;;
	start) cmd_start "$@" ;;
	stop) cmd_stop "$@" ;;
	restart) cmd_restart "$@" ;;
	status) cmd_status "$@" ;;
	upgrade) cmd_upgrade "$@" ;;
	uninstall) cmd_uninstall "$@" ;;
	help) usage; exit 0 ;;
	*)
		echo "Unknown command: $CMD" >&2
		usage >&2
		exit 2
		;;
esac
