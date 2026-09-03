#!/usr/bin/env bash
# HyperFileLens Agent bundle installer (Linux / macOS).
# Usage: install.sh [command] [options]
# When no command is given, equivalent to: install.sh install
# After install, lifecycle scripts are copied into the selected installation
# directory for local upgrade, status, and uninstall operations.

set -Eeuo pipefail

BUNDLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLATION_MODE="${HFL_INSTALLATION_MODE:-}"
RUN_AS_USER="${HFL_RUN_AS_USER:-}"
RUN_AS_HOME="${HFL_RUN_AS_HOME:-}"
USER_DATA_HOME="${XDG_DATA_HOME:-}"
[[ "${USER_DATA_HOME}" == /* ]] || USER_DATA_HOME="${HOME:-}/.local/share"
if [[ -z "${INSTALLATION_MODE}" ]]; then
	if [[ "${BUNDLE_ROOT}" == "/opt/hyperfilelens-agent" || "${BUNDLE_ROOT}" == "/opt/hyperfilelens-agent/bin" ]]; then
		INSTALLATION_MODE="system"
	else
		case "${BUNDLE_ROOT}" in
		"${USER_DATA_HOME}/hyperfilelens-agent/bin" | "${HOME:-/nonexistent}"/.local/share/hyperfilelens-agent/bin | "${HOME:-/nonexistent}"/Library/Application\ Support/HyperFileLens/Agent/bin)
			INSTALLATION_MODE="user"
		;;
		*) INSTALLATION_MODE="system" ;;
		esac
	fi
fi
# Commands launched from an installed machine-wide script must retain the
# persisted account mode. Bootstrap installs pass HFL_INSTALLATION_MODE
# explicitly, so this only applies to local start/status/upgrade/uninstall.
unquote_env_value() {
	local value="$1"
	value="${value%$'\r'}"
	if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
		value="${value:1:${#value}-2}"
		value="${value//\\\"/\"}"
		value="${value//\\\\/\\}"
	fi
	printf '%s' "${value}"
}
if [[ -z "${HFL_INSTALLATION_MODE:-}" ]]; then
	PERSISTED_ENV=""
	case "${BUNDLE_ROOT}" in
	*/lifecycle/upgrade/installer)
		# Older Agents cannot pass their installation mode to the detached
		# runner. The staged installer still has a trustworthy location below
		# the persisted Agent Root, so bootstrap the mode from that root.
		PERSISTED_ENV="${BUNDLE_ROOT%/lifecycle/upgrade/installer}/config/agent.env"
		;;
	/opt/hyperfilelens-agent | /opt/hyperfilelens-agent/bin)
		PERSISTED_ENV="/opt/hyperfilelens-agent/config/agent.env"
		;;
	"/Library/Application Support/HyperFileLens/Agent/bin")
		PERSISTED_ENV="/Library/Application Support/HyperFileLens/Agent/config/agent.env"
		;;
	esac
	if [[ -n "${PERSISTED_ENV}" && ! -f "${PERSISTED_ENV}" && "${BUNDLE_ROOT}" != */lifecycle/upgrade/installer ]]; then
		PERSISTED_ENV="/var/lib/hyperfilelens-agent/agent.env"
	fi
	if [[ -f "${PERSISTED_ENV}" ]]; then
		while IFS='=' read -r key value; do
			value="$(unquote_env_value "${value}")"
			case "${key}" in
			HFL_INSTALLATION_MODE) INSTALLATION_MODE="${value}" ;;
			HFL_RUN_AS_USER) RUN_AS_USER="${value}" ;;
			HFL_RUN_AS_HOME) RUN_AS_HOME="${value}" ;;
			esac
		done <"${PERSISTED_ENV}"
	fi
fi
if [[ -z "${HFL_INSTALLATION_MODE:-}" && "${INSTALLATION_MODE}" == "user" ]]; then
	user_mode_env="${USER_DATA_HOME}/hyperfilelens-agent/config/agent.env"
	if [[ -f "${user_mode_env}" ]]; then
		while IFS='=' read -r key value; do
			value="$(unquote_env_value "${value}")"
			[[ "${key}" == "HFL_INSTALLATION_MODE" ]] && INSTALLATION_MODE="${value}"
		done <"${user_mode_env}"
	fi
fi
[[ "${INSTALLATION_MODE}" == "system" || "${INSTALLATION_MODE}" == "user" || "${INSTALLATION_MODE}" == "user_continuous" || "${INSTALLATION_MODE}" == "account" ]] \
	|| { echo "ERROR: HFL_INSTALLATION_MODE must be system, user, user_continuous, or account" >&2; exit 2; }

is_user_mode() {
	[[ "${INSTALLATION_MODE}" == "user" || "${INSTALLATION_MODE}" == "user_continuous" ]]
}

# Keep compatibility with both old systemd (key/value output) and newer
# loginctl output. CentOS 7 ships systemd 219, where `--value` is unavailable.
user_linger_state() {
	local raw
	raw="$(loginctl show-user "$(id -u)" --property=Linger 2>/dev/null)" || return 1
	printf '%s\n' "${raw}" | sed -n 's/^Linger=//p' | tr -d '[:space:]'
}
if [[ "${INSTALLATION_MODE}" == "user_continuous" && "$(uname -s)" != "Linux" ]]; then
	echo "ERROR: HFL_INSTALLATION_MODE=user_continuous is supported on Linux only" >&2
	exit 2
fi

# New installations use one Agent Root. Product directories are direct
# siblings below it; there is no state/ wrapper directory.
if [[ "${INSTALLATION_MODE}" == "user" && "$(uname -s)" == "Darwin" ]]; then
	AGENT_ROOT="${HOME}/Library/Application Support/HyperFileLens/Agent"
	INSTALL_DIR="${AGENT_ROOT}/bin"
	DEFAULT_DATA="${AGENT_ROOT}"
	UNIT_DST=""
	LAUNCHD_PLIST="${HOME}/Library/LaunchAgents/com.hyperfilelens.agent.plist"
	LAUNCHD_DOMAIN="gui/$(id -u)"
elif is_user_mode; then
	AGENT_ROOT="${XDG_DATA_HOME:-}"
	[[ "${AGENT_ROOT}" == /* ]] || AGENT_ROOT="${HOME}/.local/share"
	AGENT_ROOT="${AGENT_ROOT}/hyperfilelens-agent"
	USER_CONFIG_HOME="${XDG_CONFIG_HOME:-}"
	[[ "${USER_CONFIG_HOME}" == /* ]] || USER_CONFIG_HOME="${HOME}/.config"
	INSTALL_DIR="${AGENT_ROOT}/bin"
	DEFAULT_DATA="${AGENT_ROOT}"
	UNIT_DST="${USER_CONFIG_HOME}/systemd/user/hyperfilelens-agent.service"
	LAUNCHD_PLIST=""
	LAUNCHD_DOMAIN=""
elif [[ "$(uname -s)" == "Darwin" ]]; then
	AGENT_ROOT="/Library/Application Support/HyperFileLens/Agent"
	INSTALL_DIR="${AGENT_ROOT}/bin"
	DEFAULT_DATA="${AGENT_ROOT}"
	UNIT_DST=""
	LAUNCHD_PLIST="/Library/LaunchDaemons/com.hyperfilelens.agent.plist"
	LAUNCHD_DOMAIN="system"
else
	AGENT_ROOT="/opt/hyperfilelens-agent"
	INSTALL_DIR="${AGENT_ROOT}/bin"
	DEFAULT_DATA="${AGENT_ROOT}"
	UNIT_DST="/etc/systemd/system/hyperfilelens-agent.service"
	LAUNCHD_PLIST="/Library/LaunchDaemons/com.hyperfilelens.agent.plist"
	LAUNCHD_DOMAIN="system"
fi
GATEWAY_RESOURCE_DROPIN="/etc/systemd/system/hyperfilelens-agent.service.d/20-gateway-resources.conf"
CONFIG_DIR="${AGENT_ROOT}/config"
DATA_STORE_DIR="${AGENT_ROOT}/data"
LOG_DIR="${AGENT_ROOT}/logs"
CACHE_DIR="${AGENT_ROOT}/cache"
MOUNTS_DIR="${AGENT_ROOT}/mounts"
RUNTIME_DIR="${AGENT_ROOT}/runtime"
LIFECYCLE_DIR="${AGENT_ROOT}/lifecycle"
BACKUP_DIR="${AGENT_ROOT}/backup"
INSTALLED_VERSION_FILE="${AGENT_ROOT}/INSTALLED_VERSION"
MANIFEST_FILE="${AGENT_ROOT}/MANIFEST.json"
LAUNCHD_LABEL="com.hyperfilelens.agent"
RUN_AGENT_SCRIPT="${INSTALL_DIR}/run-agent.sh"
GATEWAY_LIFECYCLE_SCRIPT="${INSTALL_DIR}/libexec/gateway-lifecycle.sh"
LEGACY_INSTALL_DIR="/opt/hyperfilelens-agent"
LEGACY_DATA_DIR="/var/lib/hyperfilelens-agent"
LEGACY_MIGRATION_DIR=""
LEGACY_ENV_SOURCE=""
LEGACY_SERVICE_WAS_ACTIVE=0

# The installer historically called the product root DATA_DIR. Keep that
# argument for CLI compatibility while mapping each concern to its sibling
# directory in the unified root.
agent_config_dir() { printf '%s/config' "${1%/}"; }
agent_data_store_dir() { printf '%s/data' "${1%/}"; }
agent_logs_dir() { printf '%s/logs' "${1%/}"; }
agent_cache_dir() { printf '%s/cache' "${1%/}"; }
agent_mounts_dir() { printf '%s/mounts' "${1%/}"; }
agent_runtime_dir() { printf '%s/runtime' "${1%/}"; }
agent_lifecycle_dir() { printf '%s/lifecycle' "${1%/}"; }
agent_backup_dir() { printf '%s/backup' "${1%/}"; }
agent_env_file() { printf '%s/agent.env' "$(agent_config_dir "$1")"; }
agent_config_json() { printf '%s/config.json' "$(agent_config_dir "$1")"; }

# Create the complete long-lived Agent Root layout up front. Keeping these
# directories as fixed siblings makes upgrades, diagnostics, and uninstall
# independent of which feature first touches a path.
ensure_agent_layout() {
	local root="${1%/}"
	mkdir -p \
		"${root}/bin" \
		"${root}/config" \
		"${root}/data" \
		"${root}/logs" \
		"${root}/cache/repositories" \
		"${root}/mounts/repositories" \
		"${root}/mounts/sources" \
		"${root}/mounts/custom" \
		"${root}/runtime/workspace" \
		"${root}/runtime/download" \
		"${root}/lifecycle/upgrade" \
		"${root}/lifecycle/uninstall" \
		"${root}/backup/rollback" \
		"${root}/backup/legacy"
}

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
KEEP_DATA=0
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
HFL_LIFECYCLE_LOCK_DIR=""
UPGRADE_STATE_FILE=""
UPGRADE_FROM=""
UPGRADE_YES=0
UPGRADE_TRANSACTION_ACTIVE=0
UPGRADE_PREVIOUS_VERSION="unknown"
UPGRADE_TARGET_VERSION="unknown"
UPGRADE_STATE_SNAPSHOT_READY=0
UPGRADE_DEPLOYMENT_STARTED=0
UPGRADE_STOP_ATTEMPTED=0
UPGRADE_SERVICE_WAS_ACTIVE=0
UPGRADE_SERVICE_WAS_ENABLED=0
UPGRADE_CURRENT_PHASE="preparing"

# Preserve the caller's terminal while command stdout/stderr is mirrored to a
# timestamped detail log during install, upgrade, and uninstall operations.
exec 3>&1 4>&2

usage() {
	local command_prefix="" lifecycle="hyperfilelens-agent.service"
	if ! is_user_mode; then
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
  reconcile-legacy  Complete a pending legacy-layout cleanup after an upgrade
  uninstall     Stop service and remove the complete Agent installation

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
						Extracts to DATA_DIR/runtime/workspace, merges missing config/agent.env keys,
                        migrates agent.db schema, overwrites binaries; removes workspace on success
    --yes               Non-interactive: continue when target version equals installed version

  uninstall:
    --keep-data                  Preserve local configuration, data, and logs

Install paths:
  ${INSTALL_DIR}  Binaries and installer scripts
  ${DEFAULT_DATA}  Unified Agent Root (config/, data/, logs/, cache/, mounts/, runtime/, lifecycle/, backup/)
  ${lifecycle}  Managed startup lifecycle

Examples:
  ${command_prefix}./install.sh
  ${command_prefix}./install.sh install --wss-url 'wss://console.example/ws/node/agent/' --api-base 'https://console.example' --org-key 'org_xxx' --node-token 'tok_xxx'
  ${command_prefix}./install.sh start
  ${command_prefix}./install.sh status
  ${command_prefix}./install.sh upgrade --from /path/to/hfl-agent-0.1.0.tar.gz
  ${command_prefix}./install.sh uninstall
  ${command_prefix}./install.sh uninstall --keep-data
USAGE
}

hfl_systemctl() {
	if is_user_mode; then
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

parse_reconcile_flags() {
	while [[ $# -gt 0 ]]; do
		case "$1" in
			--quiet-footer) QUIET_FOOTER=1; shift ;;
			-h|--help) usage; exit 0 ;;
			*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
		esac
	done
}

parse_uninstall_flags() {
	while [[ $# -gt 0 ]]; do
		case "$1" in
			--keep-data) KEEP_DATA=1; shift ;;
			--purge-all) PURGE_ALL=1; shift ;;
			--keep-installation-identity) KEEP_INSTALLATION_IDENTITY=1; KEEP_DATA=1; shift ;;
			--quiet-footer) QUIET_FOOTER=1; shift ;;
			-h|--help) usage; exit 0 ;;
			*)
				echo "Unknown option: $1" >&2
				usage >&2
				exit 2
				;;
		esac
	done
	if [[ "${PURGE_ALL}" -eq 1 && "${KEEP_DATA}" -eq 1 ]]; then
		echo "ERROR: --purge-all cannot be combined with --keep-data or --keep-installation-identity" >&2
		exit 2
	fi
}

require_root() {
	if is_user_mode; then
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
		if legacy_layout_present; then
			log_fail "A legacy Agent installation was found. Run ./install.sh upgrade --from <package.tar.gz> to migrate it to the unified Agent Root." 2
		else
			local command_prefix=""
			[[ "${INSTALLATION_MODE}" == "system" ]] && command_prefix="sudo "
			log_fail "The agent is not installed. Run ${command_prefix}./install.sh install first." 2
		fi
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
	if is_user_mode; then
		if ! command -v systemctl >/dev/null 2>&1 \
		|| ! hfl_systemctl show-environment >/dev/null 2>&1; then
			log_fail "A working systemd user service manager is required for user-level installation." 2
		fi
		command -v loginctl >/dev/null 2>&1 \
			|| log_fail "loginctl is required to verify that current-user mode stops after sign-out." 2
		local user_linger
		user_linger="$(user_linger_state)" \
			|| log_fail "Unable to verify the current user's systemd sign-out behavior." 2
		[[ "${user_linger}" == "yes" || "${user_linger}" == "no" ]] \
			|| log_fail "Unable to parse the current user's systemd linger state." 2
		if [[ "${INSTALLATION_MODE}" == "user_continuous" ]]; then
			if [[ "${user_linger}" != "yes" ]]; then
				if [[ "${CMD}" == "install" ]]; then
					# Linger is the one-time host authorization boundary. The Agent,
					# service unit, and all lifecycle commands remain user-scoped.
					command -v sudo >/dev/null 2>&1 \
						|| log_fail "Administrator authorization is required once to enable systemd user lingering (sudo is not available)." 2
					log_step "Enabling systemd user lingering for $(id -un)."
					sudo loginctl enable-linger "$(id -un)" \
						|| log_fail "Could not enable systemd user lingering. Ask an administrator to run: sudo loginctl enable-linger $(id -un)" 2
					user_linger="$(user_linger_state)" \
						|| log_fail "Unable to verify the current user's systemd linger state after authorization." 2
					[[ "${user_linger}" == "yes" ]] \
						|| log_fail "systemd user lingering is still disabled after administrator authorization." 2
				else
					log_warn "systemd user lingering is not enabled; the Agent may stop after sign-out until an administrator runs: sudo loginctl enable-linger $(id -un)"
				fi
			fi
			# Linger is a shared per-user systemd setting. Uninstalling this Agent
			# must not disable it because unrelated user services may depend on it.
		elif [[ "${user_linger}" == "yes" ]]; then
			log_fail "Current-user protection must pause after sign-out, but systemd user lingering is enabled. Choose User files continuous protection, or disable linger only if no other user services depend on it." 2
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
		if [[ "\${value:0:1}" == '"' && "\${value: -1}" == '"' ]]; then
			value="\${value#\"}"
			value="\${value%\"}"
		fi
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
	data_dir="${DEFAULT_DATA}"
	log_dir="$(agent_logs_dir "${data_dir}")"
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

wait_for_launchd_unload() {
	local timeout="${1:-10}"
	local attempts=0 max_attempts=$((timeout * 10))
	while launchctl print "${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL}" >/dev/null 2>&1; do
		attempts=$((attempts + 1))
		if [[ "${attempts}" -ge "${max_attempts}" ]]; then
			return 1
		fi
		sleep 0.1
	done
	return 0
}

stop_launchd_service() {
	local unload_timeout="${HFL_LAUNCHD_UNLOAD_TIMEOUT_SECONDS:-10}"
	[[ "${unload_timeout}" =~ ^[0-9]+$ \
		&& "${unload_timeout}" -gt 0 \
		&& "${unload_timeout}" -le 60 ]] || unload_timeout=10
	if launchctl print "${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL}" >/dev/null 2>&1; then
		if ! launchctl bootout "${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL}" 2>/dev/null \
			&& ! launchctl bootout "${LAUNCHD_DOMAIN}" "${LAUNCHD_PLIST}" 2>/dev/null; then
			if [[ "${UPGRADE_TRANSACTION_ACTIVE}" -eq 1 ]]; then
				log_fail "launchd service ${LAUNCHD_LABEL} could not be stopped; upgrade was aborted before deployment." 2
			fi
			log_warn "launchd service ${LAUNCHD_LABEL} could not be stopped"
		fi
		if ! wait_for_launchd_unload "${unload_timeout}"; then
			if [[ "${UPGRADE_TRANSACTION_ACTIVE}" -eq 1 ]]; then
				log_fail "launchd service ${LAUNCHD_LABEL} did not unload within ${unload_timeout} seconds; upgrade was aborted before deployment." 2
			fi
			log_warn "launchd service ${LAUNCHD_LABEL} did not unload within ${unload_timeout} seconds"
			return 1
		fi
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
	local env_file="${1:-$(agent_env_file "${DEFAULT_DATA}")}"
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
		start_launchd_service "$(agent_env_file "${DEFAULT_DATA}")"
		return 0
	fi
	if launchctl kickstart -k "${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL}" 2>/dev/null; then
		log_ok "started launchd service ${LAUNCHD_LABEL} ($(launchd_service_status_line))"
	else
		start_launchd_service "$(agent_env_file "${DEFAULT_DATA}")"
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

hfl_terminal_color_enabled() {
	local stream="${1:-stdout}" fd
	[[ -z "${NO_COLOR:-}" && "${HFL_COLOR:-auto}" != "0" ]] || return 1
	[[ "${TERM:-}" != "dumb" ]] || return 1
	case "${stream}" in
		stdout) if [[ -t 3 ]]; then fd=3; else fd=1; fi ;;
		stderr) if [[ -t 4 ]]; then fd=4; else fd=2; fi ;;
		[0-9]*) fd="${stream}" ;;
		*) return 1 ;;
	esac
	[[ -t "${fd}" ]]
}

hfl_color_level() {
	local level="$1" stream="${2:-stderr}"
	if ! hfl_terminal_color_enabled "${stream}"; then
		printf '%s' "${level}"
		return 0
	fi
	case "${level}" in
		'....') printf '\033[38;5;141m%s\033[0m' "${level}" ;;
		'INFO '|INFO) printf '\033[38;5;214m%s\033[0m' "${level}" ;;
		' OK ') printf '\033[38;5;114m%s\033[0m' "${level}" ;;
		WARN) printf '\033[38;5;214m%s\033[0m' "${level}" ;;
		FAIL) printf '\033[38;5;203m%s\033[0m' "${level}" ;;
		SKIP) printf '\033[38;5;183m%s\033[0m' "${level}" ;;
		*) printf '%s' "${level}" ;;
	esac
}

_hfl_emit_raw() {
	local level="$1"
	shift
	local message display_level display_level_colored log_dir output_stream
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
			output_stream=stderr
			display_level_colored="$(hfl_color_level "${display_level}" "${output_stream}")"
			printf '  [%s] %s\n' "${display_level_colored}" "${message}" >&4
		else
			output_stream=stdout
			display_level_colored="$(hfl_color_level "${display_level}" "${output_stream}")"
			printf '  [%s] %s\n' "${display_level_colored}" "${message}" >&3
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
	if [[ "${UPGRADE_TRANSACTION_ACTIVE}" -eq 1 ]]; then
		upgrade_rollback_on_error "${code}"
	fi
	exit "${code}"
}

process_start_marker() {
	local pid="$1" marker=""
	if [[ -r "/proc/${pid}/stat" ]]; then
		# Linux procfs exposes a monotonic start-time tick count. Strip the
		# command name first because it may contain spaces in parentheses.
		marker="$(sed 's/^[^)]*) //' "/proc/${pid}/stat" 2>/dev/null | awk '{print $20}' || true)"
	fi
	if [[ -z "${marker}" ]] && command -v ps >/dev/null 2>&1; then
		marker="$(LC_ALL=C ps -p "${pid}" -o lstart= 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' || true)"
	fi
	printf '%s' "${marker}"
}

acquire_lifecycle_lock() {
	local data_dir="$1" operation="$2" pid_file
	local lock_dir="$(agent_lifecycle_dir "${data_dir}")/install.lock"
	local owner_pid="" recorded_start="" actual_start="" attempt
	mkdir -p "$(agent_lifecycle_dir "${data_dir}")"
	if ! mkdir "${lock_dir}" 2>/dev/null; then
		pid_file="${lock_dir}/pid"
		# mkdir is the atomic ownership step, but the owner needs a moment to
		# persist its metadata. Do not erase a newly created lock in that window.
		for attempt in 1 2 3 4 5; do
			[[ -r "${pid_file}" ]] && break
			sleep 0.1
		done
		owner_pid="$(cat "${pid_file}" 2>/dev/null || true)"
		if [[ "${owner_pid}" =~ ^[0-9]+$ ]] && kill -0 "${owner_pid}" 2>/dev/null; then
			recorded_start="$(cat "${lock_dir}/process_started_at" 2>/dev/null || true)"
			actual_start="$(process_start_marker "${owner_pid}")"
			# Locks created without an owner-start marker are treated
			# conservatively. For current locks, a mismatched marker proves that
			# the PID was reused after the lifecycle owner exited.
			if [[ -z "${recorded_start}" || -z "${actual_start}" || "${recorded_start}" == "${actual_start}" ]]; then
				log_fail "Another Agent lifecycle operation is already running (operation lock: ${lock_dir})." 2
			fi
		fi
		rm -rf "${lock_dir}"
		mkdir "${lock_dir}" || log_fail "Unable to acquire Agent lifecycle operation lock ${lock_dir}." 2
	fi
	HFL_LIFECYCLE_LOCK_DIR="${lock_dir}"
	if ! {
		process_start_marker "$$" >"${lock_dir}/process_started_at" \
			&& printf '%s\n' "${operation}" >"${lock_dir}/operation" \
			&& printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"${lock_dir}/started_at" \
			&& printf '%s\n' "unknown" >"${lock_dir}/target_version" \
			&& printf '%s\n' "unknown" >"${lock_dir}/target_commit" \
			&& printf '%s\n' "$$" >"${lock_dir}/pid"
	}; then
		rm -rf "${lock_dir}" 2>/dev/null || true
		HFL_LIFECYCLE_LOCK_DIR=""
		log_fail "Unable to persist Agent lifecycle operation lock metadata (${lock_dir})." 2
	fi
}

update_lifecycle_lock_target() {
	local version="$1" manifest="${2:-${MANIFEST_FILE}}" commit="unknown"
	[[ -n "${HFL_LIFECYCLE_LOCK_DIR}" && -d "${HFL_LIFECYCLE_LOCK_DIR}" ]] || return 0
	if [[ -f "${manifest}" ]]; then
		commit="$(grep -E '"agent_commit"[[:space:]]*:' "${manifest}" | head -n1 | sed -n 's/.*"agent_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
		[[ -n "${commit}" ]] || commit="unknown"
	fi
	printf '%s\n' "${version}" >"${HFL_LIFECYCLE_LOCK_DIR}/target_version"
	printf '%s\n' "${commit}" >"${HFL_LIFECYCLE_LOCK_DIR}/target_commit"
}

release_lifecycle_lock() {
	if [[ -n "${HFL_LIFECYCLE_LOCK_DIR}" ]]; then
		rm -rf "${HFL_LIFECYCLE_LOCK_DIR}" 2>/dev/null || true
		HFL_LIFECYCLE_LOCK_DIR=""
	fi
}

write_upgrade_state() {
	local data_dir="$1" phase="$2" temporary
	local file="$(agent_lifecycle_dir "${data_dir}")/upgrade-state.json"
	mkdir -p "$(dirname "${file}")"
	UPGRADE_STATE_FILE="${file}"
	UPGRADE_CURRENT_PHASE="${phase}"
	temporary="${file}.tmp.$$"
	cat >"${temporary}" <<EOF
{"phase":"${phase}","operation":"upgrade","pid":$$,"previous_version":"${UPGRADE_PREVIOUS_VERSION}","target_version":"${UPGRADE_TARGET_VERSION}","installation_mode":"${INSTALLATION_MODE}","lifecycle_was_running":${UPGRADE_SERVICE_WAS_ACTIVE},"lifecycle_was_enabled":${UPGRADE_SERVICE_WAS_ENABLED},"state_snapshot_ready":${UPGRADE_STATE_SNAPSHOT_READY},"deployment_started":${UPGRADE_DEPLOYMENT_STARTED},"updated_at":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF
	mv -f "${temporary}" "${file}"
}

clear_upgrade_state() {
	[[ -n "${UPGRADE_STATE_FILE}" ]] && rm -f "${UPGRADE_STATE_FILE}" 2>/dev/null || true
}

upgrade_state_value() {
	local file="$1" key="$2"
	sed -n "s/.*\"${key}\":\"\([^\"]*\)\".*/\1/p" "${file}" 2>/dev/null | head -n1
}

upgrade_state_flag() {
	local file="$1" key="$2" value
	value="$(sed -n "s/.*\"${key}\":\([01]\).*/\1/p" "${file}" 2>/dev/null | head -n1)"
	[[ "${value}" == "1" ]] && printf '1' || printf '0'
}

load_upgrade_state() {
	local data_dir="$1"
	local file="$(agent_lifecycle_dir "${data_dir}")/upgrade-state.json"
	UPGRADE_STATE_FILE="${file}"
	UPGRADE_CURRENT_PHASE="$(upgrade_state_value "${file}" phase || true)"
	UPGRADE_PREVIOUS_VERSION="$(upgrade_state_value "${file}" previous_version || true)"
	UPGRADE_TARGET_VERSION="$(upgrade_state_value "${file}" target_version || true)"
	UPGRADE_SERVICE_WAS_ACTIVE="$(upgrade_state_flag "${file}" lifecycle_was_running)"
	UPGRADE_SERVICE_WAS_ENABLED="$(upgrade_state_flag "${file}" lifecycle_was_enabled)"
	UPGRADE_STATE_SNAPSHOT_READY="$(upgrade_state_flag "${file}" state_snapshot_ready)"
	UPGRADE_DEPLOYMENT_STARTED="$(upgrade_state_flag "${file}" deployment_started)"
	UPGRADE_BIN_BACKUP="$(agent_backup_dir "${data_dir}")/rollback/bin"
}

recover_interrupted_upgrade() {
	local data_dir="$1" intent="${2:-upgrade}" phase state_file
	state_file="$(agent_lifecycle_dir "${data_dir}")/upgrade-state.json"
	load_upgrade_state "${data_dir}"
	phase="${UPGRADE_CURRENT_PHASE}"
	if [[ -z "${phase}" ]]; then
		[[ -f "${state_file}" ]] || return 0
		log_fail "The pending upgrade state is unreadable (${state_file}); rollback data was preserved for recovery." 70
	fi
	case "${phase}" in
	committed|rolled_back)
		cleanup_upgrade_rollback "${data_dir}"
		clear_upgrade_state
		return 0
		;;
	preparing|package_resolved)
		log_warn "discarding incomplete pre-deployment upgrade state (${phase})"
		rm -rf "$(agent_backup_dir "${data_dir}")/rollback" 2>/dev/null || true
		clear_upgrade_state
		return 0
		;;
	rollback_failed)
		log_fail "The previous upgrade and rollback both failed. Preserve $(agent_backup_dir "${data_dir}")/rollback and resolve the recorded failure before retrying." 2
		;;
	awaiting_restart)
		[[ "${intent}" == "start" ]] && return 2
		log_fail "A staged upgrade is awaiting restart and verification. Run ${INSTALL_DIR}/install.sh start before starting another upgrade." 2
		;;
	stopping|service_stopped|snapshotting|state_snapshotted)
		log_warn "recovering interrupted upgrade before binary deployment (${phase})"
		if [[ "${UPGRADE_SERVICE_WAS_ACTIVE}" -eq 1 ]] && agent_manages_service; then
			(start_service_only) || log_fail "Interrupted upgrade recovery could not restart the previous Agent service." 70
		fi
		upgrade_health_check "${data_dir}" "${UPGRADE_PREVIOUS_VERSION}" "${UPGRADE_SERVICE_WAS_ACTIVE}" \
			|| log_fail "Interrupted upgrade recovery could not verify the previous Agent." 70
		cleanup_upgrade_rollback "${data_dir}"
		clear_upgrade_state
		return 0
		;;
	starting_service|service_started|healthy)
		if upgrade_health_check "${data_dir}" "${UPGRADE_TARGET_VERSION}" "${UPGRADE_SERVICE_WAS_ACTIVE}"; then
			log_warn "finalizing an interrupted upgrade after target health verification (${phase})"
			write_upgrade_state "${data_dir}" "committed"
			cleanup_upgrade_rollback "${data_dir}"
			clear_upgrade_state
			return 0
		fi
		log_warn "rolling back interrupted upgrade transaction (${phase})"
		UPGRADE_STOP_ATTEMPTED=1
		UPGRADE_DEPLOYMENT_STARTED=1
		if upgrade_rollback_on_error 0; then :; fi
		clear_upgrade_state
		return 0
		;;
	deploying|deployed|migrating|migrated|configuring_service|rolling_back)
		log_warn "rolling back interrupted upgrade transaction (${phase})"
		UPGRADE_STOP_ATTEMPTED=1
		UPGRADE_DEPLOYMENT_STARTED=1
		if upgrade_rollback_on_error 0; then :; fi
		clear_upgrade_state
		return 0
		;;
	*)
		log_fail "Unknown interrupted upgrade phase ${phase}; preserve $(agent_backup_dir "${data_dir}")/rollback for manual recovery." 70
		;;
	esac
}

hfl_detail_log_stream() {
	local log_file="$1" line
	# Keep the durable transcript portable when nested tools emit terminal
	# styling. ANSI is only useful on the live terminal, never in install.log.
	sed $'s/\033\[[0-9;]*m//g' | while IFS= read -r line || [[ -n "${line}" ]]; do
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
	if hfl_terminal_color_enabled stdout; then
		printf '\033[1;35m' >&3
	fi
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
	if hfl_terminal_color_enabled stdout; then
		printf '\033[0m' >&3
	fi
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
	local log_file="$(agent_logs_dir "${data_dir}")/install.log"
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
	if [[ "${rc}" -ne 0 ]]; then
		restore_legacy_service_on_error || true
	fi
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
	local log_file="$(agent_logs_dir "${data_dir}")/uninstall.log"
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
	hfl_print_value "Log file" "$(agent_logs_dir "${data_dir}")/install.log"
}

hfl_print_upgrade_success() {
	local version="$1" service="$2" data_dir="$3"
	hfl_print_section "Verifying"
	if [[ "${service}" == not\ restarted* ]]; then
		log_skip "Agent service was not restarted by request."
		log_warn "Local health verification is pending; rollback data was retained."
	else
		log_ok "Agent service is ${service}."
	fi
	if [[ "${service}" == not\ restarted* ]]; then
		hfl_print_result "Upgrade staged; restart and verification are pending"
	else
		hfl_print_result "Upgrade completed successfully"
	fi
	hfl_print_section "Upgrade summary"
	hfl_print_value "Agent version" "${version}"
	hfl_print_value "Service state" "${service}"
	hfl_print_value "Install path" "${INSTALL_DIR}"
	hfl_print_value "Data path" "${data_dir}"
	hfl_print_value "Log file" "$(agent_logs_dir "${data_dir}")/install.log"
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
	elif [[ -f "${MANIFEST_FILE}" ]]; then
		manifest="${MANIFEST_FILE}"
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

verify_upgrade_package() {
	local root="$1" role="$2" version="$3" verifier output
	local target_verifier="${root}/bin/hfl-agent"
	[[ -x "${target_verifier}" ]] || log_fail "Upgrade package verifier is missing: ${target_verifier}." 2
	verifier="${target_verifier}"
	if [[ -x "${INSTALL_DIR}/hfl-agent" ]] \
		&& "${INSTALL_DIR}/hfl-agent" help 2>/dev/null | grep -q 'package verify'; then
		# Prefer the trusted currently installed Agent after the first release that
		# includes this command. The target binary bridges upgrades from older builds.
		verifier="${INSTALL_DIR}/hfl-agent"
	fi
	if ! output="$("${verifier}" package verify --root "${root}" --role "${role:-agent}" --version "${version}" 2>&1)"; then
		log_fail "Upgrade package validation failed; the current Agent was not stopped${output:+: ${output}}." 2
	fi
	log_ok "upgrade package manifest and checksums verified"
}

upgrade_workspace_dir() {
	local data_dir="$1"
	echo "$(agent_runtime_dir "${data_dir}")/workspace"
}

cleanup_upgrade_workspace() {
	local ws="$1"
	if [[ -d "${ws}" ]]; then
		if rm -rf "${ws}"; then
			log_ok "removed ${ws}"
		else
			log_warn "upgrade workspace cleanup was deferred (${ws})"
		fi
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
	local src_root="$3"
	local state_dir="$(agent_backup_dir "${data_dir}")/rollback"
	local archive="${state_dir}/latest.tar.gz"
	local meta="${state_dir}/meta.json"
	local snapshot temporary db_source verifier backup_ok=1
	mkdir -p "${state_dir}"
	snapshot="$(mktemp -d "${state_dir}/snapshot.XXXXXX")" \
		|| log_fail "Upgrade state snapshot workspace could not be created." 2
	mkdir -p "${snapshot}/config" "${snapshot}/data" || backup_ok=0
	[[ ! -f "$(agent_config_dir "${data_dir}")/agent.env" ]] \
		|| cp -p "$(agent_config_dir "${data_dir}")/agent.env" "${snapshot}/config/agent.env" \
		|| backup_ok=0
	[[ ! -f "$(agent_config_json "${data_dir}")" ]] \
		|| cp -p "$(agent_config_json "${data_dir}")" "${snapshot}/config/config.json" \
		|| backup_ok=0
	db_source="$(agent_data_store_dir "${data_dir}")/agent.db"
	verifier="${src_root}/bin/hfl-agent"
	if [[ -f "${db_source}" ]]; then
		"${verifier}" database backup --source "${db_source}" --destination "${snapshot}/data/agent.db" \
			|| backup_ok=0
		[[ ! -f "${snapshot}/data/agent.db" ]] || chmod 600 "${snapshot}/data/agent.db" || backup_ok=0
	fi
	if [[ ! -f "${snapshot}/config/agent.env" && ! -f "${snapshot}/data/agent.db" ]]; then
		backup_ok=0
	fi
	if [[ "${backup_ok}" -ne 1 ]]; then
		rm -rf "${snapshot}" 2>/dev/null || true
		log_fail "Upgrade state snapshot could not be created or verified; the current Agent was not stopped." 2
	fi
	temporary="${state_dir}/state.tar.gz.tmp"
	rm -f "${temporary}"
	if ! tar -czf "${temporary}" -C "${snapshot}" .; then
		rm -rf "${snapshot}" "${temporary}" 2>/dev/null || true
		log_fail "Upgrade state snapshot archive could not be created." 2
	fi
	rm -rf "${snapshot}"
	mv -f "${temporary}" "${archive}"
	chmod 600 "${archive}"
	log_ok "backed up Agent configuration and consistent SQLite state -> ${archive}"
	cat >"${meta}" <<EOF
{
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "previous_version": "${prev_ver}",
  "installation_mode": "${INSTALLATION_MODE}",
  "service_name": "$(service_display_name)",
  "state_archive": "backup/rollback/latest.tar.gz"
}
EOF
	log_ok "wrote ${meta}"
}

UPGRADE_MIN_FREE_MB="${HFL_UPGRADE_MIN_FREE_MB:-512}"
UPGRADE_BIN_BACKUP=""
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
	UPGRADE_BIN_BACKUP="$(agent_backup_dir "${data_dir}")/rollback/bin"
	rm -rf "$(agent_backup_dir "${data_dir}")/rollback"
	mkdir -p "${UPGRADE_BIN_BACKUP}"
	local entry
	for entry in hfl-agent kopia install.sh run-agent.sh; do
		[[ -e "${INSTALL_DIR}/${entry}" ]] && cp -a "${INSTALL_DIR}/${entry}" "${UPGRADE_BIN_BACKUP}/"
	done
	[[ -f "${MANIFEST_FILE}" ]] && cp -a "${MANIFEST_FILE}" "${UPGRADE_BIN_BACKUP}/"
	[[ -f "${INSTALLED_VERSION_FILE}" ]] && cp -a "${INSTALLED_VERSION_FILE}" "${UPGRADE_BIN_BACKUP}/"
	[[ -d "${INSTALL_DIR}/libexec" ]] && cp -a "${INSTALL_DIR}/libexec" "${UPGRADE_BIN_BACKUP}/"
	backup_upgrade_service_definition "${data_dir}"
	log_ok "backed up binaries -> ${UPGRADE_BIN_BACKUP}"
}

backup_upgrade_service_definition() {
	local data_dir="$1"
	local service_dir="$(agent_backup_dir "${data_dir}")/rollback/service"
	mkdir -p "${service_dir}"
	if agent_uses_launchd; then
		if [[ -f "${LAUNCHD_PLIST}" ]]; then
			cp -a "${LAUNCHD_PLIST}" "${service_dir}/launchd.plist"
		else
			: >"${service_dir}/launchd.plist.absent"
		fi
	else
		if [[ -f "${UNIT_DST}" ]]; then
			cp -a "${UNIT_DST}" "${service_dir}/hyperfilelens-agent.service"
		else
			: >"${service_dir}/hyperfilelens-agent.service.absent"
		fi
		if [[ -f "${GATEWAY_RESOURCE_DROPIN}" ]]; then
			cp -a "${GATEWAY_RESOURCE_DROPIN}" "${service_dir}/20-gateway-resources.conf"
		else
			: >"${service_dir}/20-gateway-resources.conf.absent"
		fi
	fi
}

restore_upgrade_binaries() {
	[[ -n "${UPGRADE_BIN_BACKUP}" && -d "${UPGRADE_BIN_BACKUP}" ]] || return 0
	local entry
	for entry in hfl-agent kopia install.sh run-agent.sh; do
		if [[ -e "${UPGRADE_BIN_BACKUP}/${entry}" ]]; then
			copy_file_atomically "${UPGRADE_BIN_BACKUP}/${entry}" "${INSTALL_DIR}/${entry}" || return 1
		else
			rm -f "${INSTALL_DIR}/${entry}" || return 1
		fi
	done
	if [[ -f "${UPGRADE_BIN_BACKUP}/MANIFEST.json" ]]; then
		copy_file_atomically "${UPGRADE_BIN_BACKUP}/MANIFEST.json" "${MANIFEST_FILE}" || return 1
	fi
	if [[ -f "${UPGRADE_BIN_BACKUP}/INSTALLED_VERSION" ]]; then
		copy_file_atomically "${UPGRADE_BIN_BACKUP}/INSTALLED_VERSION" "${INSTALLED_VERSION_FILE}" || return 1
	fi
	rm -rf "${INSTALL_DIR}/libexec" || return 1
	if [[ -d "${UPGRADE_BIN_BACKUP}/libexec" ]]; then
		cp -a "${UPGRADE_BIN_BACKUP}/libexec" "${INSTALL_DIR}/" || return 1
	fi
	log_warn "restored binaries from ${UPGRADE_BIN_BACKUP}"
}

copy_file_atomically() {
	local source="$1" destination="$2"
	local temporary="${destination}.rollback.$$"
	rm -f "${temporary}"
	if ! cp -a "${source}" "${temporary}"; then
		rm -f "${temporary}" 2>/dev/null || true
		return 1
	fi
	if ! mv -f "${temporary}" "${destination}"; then
		rm -f "${temporary}" 2>/dev/null || true
		return 1
	fi
}

restore_upgrade_state() {
	local data_dir="$1"
	local rollback="$(agent_backup_dir "${data_dir}")/rollback"
	local archive="${rollback}/latest.tar.gz" staging="${rollback}/restore.$$" relative destination
	[[ -f "${archive}" ]] || return 1
	rm -rf "${staging}" || return 1
	mkdir -p "${staging}" || return 1
	if ! tar -xzf "${archive}" -C "${staging}"; then
		rm -rf "${staging}" 2>/dev/null || true
		return 1
	fi
	if [[ ! -f "${staging}/config/agent.env" && ! -f "${staging}/data/agent.db" ]]; then
		rm -rf "${staging}" 2>/dev/null || true
		return 1
	fi
	for relative in config/agent.env config/config.json data/agent.db; do
		destination="${data_dir}/${relative}"
		if [[ -f "${staging}/${relative}" ]]; then
			copy_file_atomically "${staging}/${relative}" "${destination}" || {
				rm -rf "${staging}" 2>/dev/null || true
				return 1
			}
		else
			rm -f "${destination}" || return 1
		fi
	done
	# The online SQLite backup is a complete standalone database. Remove any
	# sidecars from the failed target before the restored Agent opens it.
	rm -f "$(agent_data_store_dir "${data_dir}")/agent.db-wal" \
		"$(agent_data_store_dir "${data_dir}")/agent.db-shm" || return 1
	rm -rf "${staging}"
	log_warn "restored Agent configuration and database state from ${archive}"
}

restore_upgrade_service_definition() {
	local data_dir="$1"
	local service_dir="$(agent_backup_dir "${data_dir}")/rollback/service"
	if agent_uses_launchd; then
		stop_launchd_service || true
		rm -f "${LAUNCHD_PLIST}" || return 1
		if [[ -f "${service_dir}/launchd.plist" ]]; then
			mkdir -p "$(dirname "${LAUNCHD_PLIST}")" || return 1
			cp -a "${service_dir}/launchd.plist" "${LAUNCHD_PLIST}" || return 1
		fi
	else
		hfl_systemctl stop hyperfilelens-agent.service 2>/dev/null || true
		rm -f "${UNIT_DST}" "${GATEWAY_RESOURCE_DROPIN}" || return 1
		if [[ -f "${service_dir}/hyperfilelens-agent.service" ]]; then
			mkdir -p "$(dirname "${UNIT_DST}")" || return 1
			cp -a "${service_dir}/hyperfilelens-agent.service" "${UNIT_DST}" || return 1
		fi
		if [[ -f "${service_dir}/20-gateway-resources.conf" ]]; then
			mkdir -p "$(dirname "${GATEWAY_RESOURCE_DROPIN}")" || return 1
			cp -a "${service_dir}/20-gateway-resources.conf" "${GATEWAY_RESOURCE_DROPIN}" || return 1
		fi
		hfl_systemctl daemon-reload 2>/dev/null || return 1
	fi
}

upgrade_health_check() {
	local data_dir="$1" expected_version="$2" require_service="${3:-1}" expected_commit="" actual="" pid="" command="" stable=0
	local identity_re='^hyperfilelens-agent[[:space:]]+([^[:space:]]+)[[:space:]]+\(([^)]*)\)$'
	expected_commit="$(grep -E '"agent_commit"[[:space:]]*:' "${MANIFEST_FILE}" 2>/dev/null | head -n1 | sed -n 's/.*"agent_commit"[[:space:]]*:[[:space:]]*"\([0-9A-Fa-f]*\)".*/\1/p')"
	[[ -n "${expected_commit}" ]] || { log_warn "local health check: target manifest has no agent_commit"; return 1; }
	actual="$("${INSTALL_DIR}/hfl-agent" version 2>/dev/null)" || { log_warn "local health check: Agent version command failed"; return 1; }
	[[ "${actual}" =~ ${identity_re} ]] || {
		log_warn "local health check: Agent returned an invalid build identity"; return 1;
	}
	local actual_version="${BASH_REMATCH[1]#v}" actual_commit="${BASH_REMATCH[2]}"
	[[ "${actual_version}" == "${expected_version#v}" && "${actual_commit}" == "${expected_commit}" ]] || {
		log_warn "local health check: running build identity does not match ${expected_version} (${expected_commit})"; return 1;
	}
	"${INSTALL_DIR}/hfl-agent" tasks list --data-dir "${data_dir}" --limit 1 >/dev/null 2>&1 || {
		log_warn "local health check: Agent database could not be opened"; return 1;
	}
	if [[ "${require_service}" -eq 0 ]] || ! agent_manages_service; then return 0; fi
	local seconds="${HFL_UPGRADE_HEALTH_SECONDS:-10}"
	[[ "${seconds}" =~ ^[0-9]+$ && "${seconds}" -ge 2 ]] || seconds=10
	while ((stable < seconds)); do
		if agent_uses_launchd; then
			launchctl print "${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL}" >/dev/null 2>&1 || return 1
			pid="$(launchctl print "${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL}" 2>/dev/null | awk -F'= ' '/pid =/{print $2; exit}' | tr -d ' ;')"
		else
			hfl_systemctl is-active hyperfilelens-agent.service >/dev/null 2>&1 || return 1
			pid="$(hfl_systemctl show hyperfilelens-agent.service -p MainPID 2>/dev/null | sed -n 's/^MainPID=//p')"
		fi
		[[ "${pid}" =~ ^[0-9]+$ && "${pid}" -gt 0 ]] || return 1
		if [[ "$(uname -s)" == "Linux" ]]; then
			[[ "$(readlink "/proc/${pid}/exe" 2>/dev/null || true)" == "${INSTALL_DIR}/hfl-agent" ]] || return 1
		else
			command="$(ps -p "${pid}" -o command= 2>/dev/null || true)"
			[[ "${command}" == *"${INSTALL_DIR}/hfl-agent"* ]] || return 1
		fi
		stable=$((stable + 1))
		sleep 1
	done
	log_ok "local health check passed (${expected_version}, ${expected_commit}; stable ${stable}s)"
}

upgrade_rollback_on_error() {
	local rc="${1:-$?}" failed_phase="${UPGRADE_CURRENT_PHASE}"
	if [[ "${UPGRADE_STOP_ATTEMPTED}" -eq 1 ]]; then
		UPGRADE_TRANSACTION_ACTIVE=0
		trap - ERR
		write_upgrade_state "${DATA_DIR}" "rolling_back" || log_warn "could not persist rolling_back state"
		log_warn "upgrade failed during ${failed_phase} (exit=${rc}); attempting rollback"
		local rollback_ok=1
		if [[ "${UPGRADE_DEPLOYMENT_STARTED}" -eq 1 ]]; then
			local can_restore=1
			(stop_service) >/dev/null 2>&1 || { rollback_ok=0; can_restore=0; }
			if [[ "${can_restore}" -eq 1 ]]; then
				(restore_upgrade_binaries) || rollback_ok=0
				if [[ "${UPGRADE_STATE_SNAPSHOT_READY}" -eq 1 ]]; then
					(restore_upgrade_state "${DATA_DIR}") || rollback_ok=0
				fi
				(restore_upgrade_service_definition "${DATA_DIR}") || rollback_ok=0
			fi
		fi
		if [[ "${UPGRADE_SERVICE_WAS_ACTIVE}" -eq 1 ]] && agent_manages_service; then
			(start_service) >/dev/null 2>&1 || rollback_ok=0
		fi
		if [[ "${UPGRADE_SERVICE_WAS_ENABLED}" -eq 0 ]] && agent_manages_service && agent_uses_systemd; then
			hfl_systemctl disable hyperfilelens-agent.service >/dev/null 2>&1 || rollback_ok=0
		fi
		if ! upgrade_health_check "${DATA_DIR}" "${UPGRADE_PREVIOUS_VERSION}" "${UPGRADE_SERVICE_WAS_ACTIVE}"; then rollback_ok=0; fi
		if [[ "${rollback_ok}" -eq 1 ]]; then
			# A stop/snapshot failure can happen before deployment starts. The
			# rollback directory is still only a transient safety copy in that
			# case; do not leave it to be mistaken for an active transaction.
			if write_upgrade_state "${DATA_DIR}" "rolled_back"; then
				cleanup_upgrade_rollback "${DATA_DIR}"
			else
				log_warn "rollback completed, but its state could not be persisted; rollback data was retained"
			fi
			log_warn "upgrade failed; rollback completed successfully"
		else
			write_upgrade_state "${DATA_DIR}" "rollback_failed" || true
			log_fail "Upgrade failed; rollback also failed. Rollback data was retained under $(agent_backup_dir "${DATA_DIR}")/rollback." 70
		fi
	fi
	return "${rc}"
}

cleanup_upgrade_rollback() {
	local data_dir="$1"
	local rollback="$(agent_backup_dir "${data_dir}")/rollback"
	if [[ -d "${rollback}" ]]; then
		if rm -rf "${rollback}"; then
			log_ok "removed ${rollback} after local health confirmation"
		else
			log_warn "local health verification passed, but rollback cleanup was deferred (${rollback})"
		fi
	fi
}

merge_agent_env() {
	local env_file="$1"
	local data_dir="$2"
	local kopia_path="${INSTALL_DIR}/kopia"
	local -a keys=(HFL_DATA_DIR HFL_AGENT_ROOT HFL_INSTALLATION_MODE HFL_KOPIA_PATH HFL_INSECURE_TLS)
	local -a vals=("${data_dir}" "${AGENT_ROOT}" "${INSTALLATION_MODE}" "${kopia_path}" "1")
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
	# Existing legacy files retain identity, console, and credential fields, but
	# their installer-owned paths must always move to the unified Agent Root.
	set_agent_env_key "${env_file}" HFL_DATA_DIR "${data_dir}"
	set_agent_env_key "${env_file}" HFL_AGENT_ROOT "${AGENT_ROOT}"
	set_agent_env_key "${env_file}" HFL_INSTALLATION_MODE "${INSTALLATION_MODE}"
	set_agent_env_key "${env_file}" HFL_KOPIA_PATH "${kopia_path}"
	set_agent_env_key "${env_file}" HFL_INSECURE_TLS "1"
	if [[ "${INSTALLATION_MODE}" == "account" ]]; then
		set_agent_env_key "${env_file}" HFL_RUN_AS_USER "${RUN_AS_USER}"
		set_agent_env_key "${env_file}" HFL_RUN_AS_HOME "${RUN_AS_HOME}"
	fi
	chmod 600 "${env_file}"
	log_ok "updated unified Agent Root paths in ${env_file}"
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
		log_fail "agent.db migration check failed; the upgraded Agent was not started." 2
	fi
}

is_installed() {
	[[ -x "${INSTALL_DIR}/hfl-agent" ]]
}

legacy_layout_present() {
	if is_user_mode; then
		return 1
	fi
	# A unified Agent Root may already contain the new bin/ tree while files
	# from the pre-unified layout are still present. Treat that partial state as
	# legacy too, so upgrades can archive and remove it after the new service is
	# verified healthy.
	if [[ "${LEGACY_INSTALL_DIR}" == "${AGENT_ROOT}" ]]; then
		local entry
		for entry in hfl-agent kopia install.sh install.cmd install.ps1 uninstall.cmd run-agent.sh libexec; do
			[[ -e "${AGENT_ROOT}/${entry}" ]] && return 0
		done
	fi
	if [[ "${LEGACY_DATA_DIR}" != "${DEFAULT_DATA}" && -d "${LEGACY_DATA_DIR}" ]]; then
		return 0
	fi
	if [[ -x "${LEGACY_INSTALL_DIR}/hfl-agent" && ! -x "${INSTALL_DIR}/hfl-agent" ]]; then
		return 0
	fi
	if [[ -f "${LEGACY_DATA_DIR}/agent.env" && ! -f "$(agent_env_file "${DEFAULT_DATA}")" ]]; then
		return 0
	fi
	# A partially initialized unified root may already contain config/agent.env
	# while the legacy flat SQLite database is still waiting to be migrated.
	# Treat that state as legacy as well; otherwise the new Agent would start
	# with an empty database and silently lose local task/repository state.
	if [[ -f "${LEGACY_DATA_DIR}/agent.db" && ! -f "$(agent_data_store_dir "${DEFAULT_DATA}")/agent.db" ]]; then
		return 0
	fi
	return 1
}

copy_legacy_entry() {
	local src="$1" dst="$2"
	[[ -e "${src}" ]] || return 0
	if [[ -d "${src}" ]]; then
		mkdir -p "${dst}"
		cp -a "${src}/." "${dst}/"
	else
		mkdir -p "$(dirname "${dst}")"
		cp -a "${src}" "${dst}"
	fi
}

migrate_legacy_layout() {
	local archive_only="${1:-0}" stop_service_for_archive="${2:-1}"
	legacy_layout_present || return 0
	local marker stamp legacy_root legacy_program legacy_data entry existing_archive
	marker="$(agent_lifecycle_dir "${DEFAULT_DATA}")/.legacy-migration"
	if [[ -f "${marker}" ]]; then
		existing_archive="$(read_env_value "${marker}" HFL_LEGACY_MIGRATION_DIR || true)"
		if [[ -n "${existing_archive}" && -d "${existing_archive}" ]]; then
			LEGACY_MIGRATION_DIR="${existing_archive}"
			if [[ "${stop_service_for_archive}" == "1" ]] && agent_manages_service; then
				case "$(service_status_line 2>/dev/null || true)" in
					active*|running*|loaded*) LEGACY_SERVICE_WAS_ACTIVE=1 ;;
				esac
				stop_service || true
			fi
			log_warn "reusing legacy Agent archive at ${LEGACY_MIGRATION_DIR}"
			return 0
		fi
	fi
	stamp="$(date -u +%Y%m%dT%H%M%SZ)"
	LEGACY_MIGRATION_DIR="$(agent_backup_dir "${DEFAULT_DATA}")/legacy/${stamp}"
	legacy_program="${LEGACY_MIGRATION_DIR}/program"
	legacy_data="${LEGACY_MIGRATION_DIR}/state"
	mkdir -p "${legacy_program}" "${legacy_data}" "${DATA_DIR}"

	# Stop the old service before copying SQLite and mounted runtime state.
	if [[ "${stop_service_for_archive}" == "1" ]] && agent_manages_service; then
		case "$(service_status_line 2>/dev/null || true)" in
			active*|running*|loaded*) LEGACY_SERVICE_WAS_ACTIVE=1 ;;
		esac
		stop_service || true
	fi

	if [[ -d "${LEGACY_INSTALL_DIR}" ]]; then
		for entry in hfl-agent kopia install.sh install.cmd install.ps1 uninstall.cmd MANIFEST.json INSTALLED_VERSION run-agent.sh libexec; do
			copy_legacy_entry "${LEGACY_INSTALL_DIR}/${entry}" "${legacy_program}/${entry}"
		done
		# The Linux legacy program root is also the new Agent Root. Its backup
		# directory was created by this installer, so archiving it here would
		# recursively copy backup/legacy into itself.
		if [[ "${LEGACY_INSTALL_DIR}" != "${AGENT_ROOT}" ]]; then
			copy_legacy_entry "${LEGACY_INSTALL_DIR}/backup" "${legacy_program}/backup"
		fi
	fi
	if [[ "$(uname -s)" == "Darwin" ]]; then
		copy_legacy_entry "/Library/LaunchDaemons/com.hyperfilelens.agent.plist" "${legacy_program}/com.hyperfilelens.agent.plist"
	else
		copy_legacy_entry "/etc/systemd/system/hyperfilelens-agent.service" "${legacy_program}/hyperfilelens-agent.service"
	fi
	if [[ -d "${LEGACY_DATA_DIR}" ]]; then
		for entry in agent.env agent.db agent.db-wal agent.db-shm config.json logs cache mounts backup runtime lifecycle install.lock; do
			copy_legacy_entry "${LEGACY_DATA_DIR}/${entry}" "${legacy_data}/${entry}"
		done
	fi
	LEGACY_ENV_SOURCE="${LEGACY_DATA_DIR}/agent.env"

	# Map the legacy flat state directory into the finalized sibling layout only
	# when the new root is not already authoritative. During a later upgrade,
	# archive-only mode prevents stale legacy state from overwriting valid data.
	if [[ "${archive_only}" != "1" && -d "${LEGACY_DATA_DIR}" ]]; then
		copy_legacy_entry "${LEGACY_DATA_DIR}/agent.env" "$(agent_config_dir "${DATA_DIR}")/agent.env"
		copy_legacy_entry "${LEGACY_DATA_DIR}/config.json" "$(agent_config_dir "${DATA_DIR}")/config.json"
		for entry in agent.db agent.db-wal agent.db-shm; do
			copy_legacy_entry "${LEGACY_DATA_DIR}/${entry}" "$(agent_data_store_dir "${DATA_DIR}")/${entry}"
		done
		for entry in logs cache mounts runtime lifecycle; do
			copy_legacy_entry "${LEGACY_DATA_DIR}/${entry}" "${DATA_DIR}/${entry}"
		done
		# The pre-unified installer kept upgrade snapshots in backup/state.
		# Promote those snapshots into the finalized backup/rollback location;
		# the complete original tree is already retained under backup/legacy.
		copy_legacy_entry "${LEGACY_DATA_DIR}/backup/rollback" "$(agent_backup_dir "${DATA_DIR}")/rollback"
		copy_legacy_entry "${LEGACY_DATA_DIR}/backup/state" "$(agent_backup_dir "${DATA_DIR}")/rollback"
		copy_legacy_entry "${LEGACY_DATA_DIR}/backup/meta.json" "$(agent_backup_dir "${DATA_DIR}")/rollback/meta.json"
		copy_legacy_entry "${LEGACY_DATA_DIR}/install.lock" "$(agent_lifecycle_dir "${DATA_DIR}")/install.lock"
	fi
	# The old program is installed directly under /opt on Unix. It is copied
	# into the new bin directory by deploy_binaries; no old binary is reused.
	mkdir -p "$(agent_lifecycle_dir "${DATA_DIR}")"
	printf 'HFL_INSTALLATION_MODE=system\nHFL_LEGACY_MIGRATION_DIR=%s\n' \
		"${LEGACY_MIGRATION_DIR}" >"$(agent_lifecycle_dir "${DATA_DIR}")/.legacy-migration"
	chmod 600 "$(agent_lifecycle_dir "${DATA_DIR}")/.legacy-migration"
	if [[ "${archive_only}" == "1" ]]; then
		log_warn "found legacy Agent residue; current unified state was kept and old files were archived under ${LEGACY_MIGRATION_DIR}"
	else
		log_warn "migrated legacy Agent layout into ${AGENT_ROOT}; old files are archived under ${LEGACY_MIGRATION_DIR}"
	fi
}

restore_legacy_service_on_error() {
	[[ "${LEGACY_SERVICE_WAS_ACTIVE}" -eq 1 ]] || return 0
	[[ -x "${LEGACY_INSTALL_DIR}/hfl-agent" ]] || return 0
	if agent_uses_launchd; then
		local old_plist="${LEGACY_MIGRATION_DIR}/program/com.hyperfilelens.agent.plist"
		if [[ -f "${old_plist}" ]]; then
			cp -f "${old_plist}" "${LAUNCHD_PLIST}"
		fi
		launchctl bootstrap "${LAUNCHD_DOMAIN}" "${LAUNCHD_PLIST}" 2>/dev/null || true
	elif command -v systemctl >/dev/null 2>&1; then
		local old_unit="${LEGACY_MIGRATION_DIR}/program/hyperfilelens-agent.service"
		if [[ -f "${old_unit}" ]]; then
			cp -f "${old_unit}" "/etc/systemd/system/hyperfilelens-agent.service"
			hfl_systemctl daemon-reload 2>/dev/null || true
		fi
		hfl_systemctl start hyperfilelens-agent.service 2>/dev/null || true
	fi
	LEGACY_SERVICE_WAS_ACTIVE=0
}

cleanup_legacy_layout() {
	[[ -n "${LEGACY_MIGRATION_DIR}" ]] || return 0
	local status data_dir="${DATA_DIR:-${DEFAULT_DATA}}" agent="${INSTALL_DIR}/hfl-agent"
	status="$(service_status_line 2>/dev/null || true)"
	case "${status}" in
		active*|running*) ;;
		loaded*)
			[[ "$(uname -s)" == "Darwin" ]] || {
				log_warn "legacy layout retained because the new service is not active (${status})"
				return 0
			}
			;;
	*) log_warn "legacy layout retained because the new service is not healthy (${status})"; return 0 ;;
	esac
	# A running service alone is not sufficient: verify that the new binary can
	# open the authoritative local task database before deleting old state.
	if [[ ! -x "${agent}" ]]; then
		log_warn "legacy layout retained because the new Agent binary is missing"
		return 0
	fi
	if ! HFL_DATA_DIR="${data_dir}" "${agent}" tasks list --data-dir "${data_dir}" --limit 1 >/dev/null 2>&1; then
		log_warn "legacy layout retained because the new Agent database could not be opened"
		return 0
	fi
	# Keep a rollback archive under the new Agent Root; remove only the old
	# active paths after the service has started successfully.
	if [[ -d "${LEGACY_INSTALL_DIR}" && "${LEGACY_INSTALL_DIR}" != "${AGENT_ROOT}" ]]; then
		rm -rf "${LEGACY_INSTALL_DIR}"
	fi
	if [[ -d "${LEGACY_DATA_DIR}" && "${LEGACY_DATA_DIR}" != "${DEFAULT_DATA}" ]]; then
		rm -rf "${LEGACY_DATA_DIR}"
	fi
	# When the old program root equals the new Agent Root, remove only legacy
	# top-level program files; the finalized sibling directories and root
	# metadata remain managed by the new layout.
	if [[ "${LEGACY_INSTALL_DIR}" == "${AGENT_ROOT}" ]]; then
		local entry
		for entry in hfl-agent kopia install.sh install.cmd install.ps1 uninstall.cmd run-agent.sh libexec; do
			rm -rf "${AGENT_ROOT}/${entry}"
		done
	fi
	rm -f "$(agent_lifecycle_dir "${DEFAULT_DATA}")/.legacy-migration"
	LEGACY_SERVICE_WAS_ACTIVE=0
	log_ok "removed legacy Agent directories after successful migration"
}

read_env_value() {
	local f="$1" key="$2"
	[[ -f "$f" ]] || return 1
	local line val
	line="$(grep -E "^${key}=" "$f" | tail -n1 || true)"
	[[ -z "$line" ]] && return 1
	val="${line#${key}=}"
	val="${val%$'\r'}"
	if [[ "${val:0:1}" == '"' && "${val: -1}" == '"' ]]; then
		val="${val:1:${#val}-2}"
		val="${val//\\\"/\"}"
		val="${val//\\\\/\\}"
	fi
	printf '%s' "$val"
}

# Keep agent.env readable by the Go parser, systemd, and the generated
# launchd wrapper. Values containing spaces are double-quoted; simple values
# retain the historical KEY=value representation.
env_value_for_file() {
	local value="$1"
	if [[ "${value}" == *[[:space:]#\"\']* ]]; then
		value="${value//\\/\\\\}"
		value="${value//\"/\\\"}"
		printf '"%s"' "${value}"
	else
		printf '%s' "${value}"
	fi
}

set_agent_env_key() {
	local env_file="$1" key="$2" value="$3" tmp line replaced=0
	tmp="${env_file}.tmp.$$"
	value="$(env_value_for_file "${value}")"
	if [[ -f "${env_file}" ]]; then
		while IFS= read -r line || [[ -n "${line}" ]]; do
			if [[ "${line}" == "${key}="* ]]; then
				if [[ "${replaced}" -eq 0 ]]; then
					printf '%s=%s\n' "${key}" "${value}" >>"${tmp}"
					replaced=1
				fi
			else
				printf '%s\n' "${line}" >>"${tmp}"
			fi
		done <"${env_file}"
	fi
	if [[ "${replaced}" -eq 0 ]]; then
		printf '%s=%s\n' "${key}" "${value}" >>"${tmp}"
	fi
	mv -f "${tmp}" "${env_file}"
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
	local env_file="$(agent_env_file "${DEFAULT_DATA}")"
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
	if is_user_mode; then
		local install_root data_root
		install_root="$(cd -P -- "$(dirname -- "${AGENT_ROOT}")" 2>/dev/null && pwd -P)/$(basename -- "${AGENT_ROOT}")" || return 1
		data_root="${install_root}"
		[[ "$p" == "$install_root" || "$p" == "$data_root" ]]
		return
	fi
	case "$p" in
		/opt/hyperfilelens-agent|/opt/hyperfilelens-agent/*) return 0 ;;
		/var/lib/hyperfilelens-agent|/var/lib/hyperfilelens-agent/*) return 0 ;;
		/Library/Application\ Support/HyperFileLens/Agent|/Library/Application\ Support/HyperFileLens/Agent/*) return 0 ;;
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

cifs_utf8_module_loaded() {
	if [[ -d /sys/module/nls_utf8 ]]; then
		return 0
	fi
	if [[ -r /proc/modules ]] && awk '$1 == "nls_utf8" { found = 1 } END { exit found ? 0 : 1 }' /proc/modules; then
		return 0
	fi
	return 1
}

prepare_cifs_utf8_module() {
	if cifs_utf8_module_loaded; then
		return 0
	fi
	if command -v modprobe >/dev/null 2>&1 \
		&& modprobe nls_utf8 >/dev/null 2>&1 \
		&& cifs_utf8_module_loaded; then
		log_ok "loaded host kernel module nls_utf8 for SMB UTF-8 filenames"
		return 0
	fi
	log_warn "SMB iocharset=utf8 is unavailable because the running host kernel does not provide nls_utf8; the offline HyperFileLens package does not download or replace host kernel modules"
	return 0
}

select_missing_nas_debs() {
	local deps_dir="$1"
	local deb package package_arch status
	local -a bundled_debs=()
	NAS_DEB_FILES=()

	mapfile -t bundled_debs < <(find "${deps_dir}" -maxdepth 1 -type f -name '*.deb' -print | sort)
	for deb in "${bundled_debs[@]}"; do
		package="$(dpkg-deb --field "${deb}" Package 2>/dev/null || true)"
		[[ "${package}" =~ ^[a-z0-9][a-z0-9+.-]+$ ]] \
			|| log_fail "The NAS dependency bundle contains an invalid package: ${deb##*/}." 2
		package_arch="$(dpkg-deb --field "${deb}" Architecture 2>/dev/null || true)"
		[[ "${package_arch}" =~ ^[a-z0-9][a-z0-9-]*$ ]] \
			|| log_fail "The NAS dependency bundle contains an invalid package architecture: ${deb##*/}." 2
		status="$(dpkg-query -W -f='${db:Status-Abbrev}' -- "${package}:${package_arch}" 2>/dev/null || true)"
		case "${status}" in
		?i\ ) ;;
		"" | ?n\  | ?c\ ) NAS_DEB_FILES+=("${deb}") ;;
		*) log_fail "The installed package ${package} is not in a healthy state (${status}). Repair the host package state before retrying." 2 ;;
		esac
	done
}

validate_offline_nas_plan() {
	(($# > 0)) || return 0
	local plan_dir plan_log plan_sources plan_source_parts deb filename normalized
	local -a plan_debs=()
	plan_dir="$(mktemp -d /tmp/hfl-nas-plan-XXXXXX)"
	plan_log="${plan_dir}/apt-plan.log"
	plan_sources="${plan_dir}/sources.list"
	plan_source_parts="${plan_dir}/sources.list.d"
	: >"${plan_sources}"
	mkdir "${plan_source_parts}"
	for deb in "$@"; do
		filename="${deb##*/}"
		normalized="${plan_dir}/${filename//:/_}"
		[[ ! -e "${normalized}" ]] \
			|| { rm -rf "${plan_dir}"; log_fail "The NAS dependency bundle contains conflicting package filenames." 2; }
		ln -s -- "${deb}" "${normalized}"
		plan_debs+=("${normalized}")
	done

	if ! LC_ALL=C apt-get --simulate --no-download --no-install-recommends \
		-o Dir::Etc::sourcelist="${plan_sources}" \
		-o Dir::Etc::sourceparts="${plan_source_parts}" \
		install "${plan_debs[@]}" \
		>"${plan_log}" 2>&1; then
		cat "${plan_log}" >&2
		rm -rf "${plan_dir}"
		log_fail "NAS dependencies cannot be installed safely from the offline package set." 2
	fi
	if grep -Eq \
		'^The following packages will be (upgraded|REMOVED|DOWNGRADED):|^[1-9][0-9]* upgraded,| [1-9][0-9]* downgraded,| [1-9][0-9]* to remove' \
		"${plan_log}"; then
		cat "${plan_log}" >&2
		rm -rf "${plan_dir}"
		log_fail "NAS dependencies cannot be installed without changing existing system packages." 2
	fi
	rm -rf "${plan_dir}"
}

install_nas_deps() {
	local role="${1:-}"
	local package_root="${2:-${BUNDLE_ROOT}}"
	local arch deps_dir ubuntu_release ubuntu_flavor

	[[ "$(uname -s)" == "Linux" ]] || return 0
	case "${role}" in
	proxy | gateway) ;;
	*) return 0 ;;
	esac
	if nas_mount_helpers_ready; then
		log_skip "install NAS packages (mount.nfs / mount.cifs already present)"
		prepare_cifs_utf8_module
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
	deps_dir="${package_root}/deps/${ubuntu_flavor}/${arch}"
	if [[ ! -d "${deps_dir}" ]] || ! compgen -G "${deps_dir}/*.deb" >/dev/null; then
		echo "ERROR: NAS mount helpers missing and bundle has no deps/${ubuntu_flavor}/${arch}/*.deb" >&2
		echo "Use the hfl-agent archive matching Ubuntu ${ubuntu_release}, or install nfs-common and cifs-utils manually." >&2
		exit 2
	fi
	if ! command -v dpkg >/dev/null 2>&1; then
		echo "ERROR: dpkg is required to install bundled NAS dependencies" >&2
		exit 2
	fi
	if ! command -v dpkg-deb >/dev/null 2>&1 || ! command -v apt-get >/dev/null 2>&1; then
		echo "ERROR: dpkg-deb and apt-get are required to validate bundled NAS dependencies" >&2
		exit 2
	fi

	log_ok "install NAS packages for role=${role} (offline ${ubuntu_flavor}/${arch})"
	local -a NAS_DEB_FILES=()
	select_missing_nas_debs "${deps_dir}"
	validate_offline_nas_plan "${NAS_DEB_FILES[@]}"
	local install_ok=0 attempt audit
	if ((${#NAS_DEB_FILES[@]} == 0)); then
		install_ok=1
		log_skip "all bundled NAS dependencies are already installed"
	else
		for attempt in 1 2 3; do
			if DEBIAN_FRONTEND=noninteractive dpkg -i "${NAS_DEB_FILES[@]}"; then
				install_ok=1
				break
			fi
			log_warn "Offline NAS dependency install pass ${attempt}/3 reported unresolved package ordering; retrying..."
		done
	fi
	audit="$(dpkg --audit 2>&1 || true)"
	if [[ "${install_ok}" -ne 1 || -n "${audit}" ]]; then
		[[ -z "${audit}" ]] || printf '%s\n' "${audit}" >&2
		log_fail "Unable to install the complete Ubuntu ${ubuntu_release} NAS dependency closure offline (${arch})." 2
	fi
	if ! nas_mount_helpers_ready; then
		log_fail "NAS mount helpers are still missing after installing the Ubuntu ${ubuntu_release} bundled packages (${arch})." 2
	fi
	log_ok "NAS mount helpers ready (mount.nfs / mount.cifs)"
	prepare_cifs_utf8_module
}

deploy_admin_scripts() {
	local src_root="${1:-${BUNDLE_ROOT}}"
	local src_script="${src_root}/install.sh"
	local src_manifest="${src_root}/MANIFEST.json"
	local src_gateway_lifecycle="${src_root}/libexec/gateway-lifecycle.sh"
	[[ -f "$src_script" ]] || log_fail "Missing bundle installer: ${src_script}." 2
	install_file_atomically "$src_script" "${INSTALL_DIR}/install.sh" 755
	log_ok "deployed ${INSTALL_DIR}/install.sh"
	if [[ -f "$src_manifest" ]]; then
		install_file_atomically "$src_manifest" "${MANIFEST_FILE}" 644
		log_ok "deployed ${MANIFEST_FILE}"
	fi
	if [[ "$(uname -s)" == "Linux" && -f "${src_gateway_lifecycle}" ]]; then
		install -d -m 755 "${INSTALL_DIR}/libexec"
		install_file_atomically "${src_gateway_lifecycle}" "${GATEWAY_LIFECYCLE_SCRIPT}" 755
		log_ok "deployed ${GATEWAY_LIFECYCLE_SCRIPT}"
	fi
}

install_file_atomically() {
	local source="$1" destination="$2" mode="$3"
	local temporary="${destination}.new.$$"
	mkdir -p "$(dirname "${destination}")"
	rm -f "${temporary}"
	if ! install -m "${mode}" "${source}" "${temporary}"; then
		rm -f "${temporary}" 2>/dev/null || true
		return 1
	fi
	if ! mv -f "${temporary}" "${destination}"; then
		rm -f "${temporary}" 2>/dev/null || true
		return 1
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
	# Put the verified recovery-capable lifecycle script in place first. If the
	# process is interrupted while replacing binaries, the next local lifecycle
	# command can understand upgrade-state.json and resume rollback.
	deploy_admin_scripts "${src_root}"
	if [[ $deploy_agent -eq 1 ]]; then
		[[ -f "$agent_bin" ]] || log_fail "Missing bundle binary: ${agent_bin}." 2
		install_file_atomically "$agent_bin" "${INSTALL_DIR}/hfl-agent" 755
		log_ok "deployed ${INSTALL_DIR}/hfl-agent ($(bundle_version_from "${src_root}"))"
	fi
	if [[ $deploy_kopia -eq 1 ]]; then
		[[ -f "$kopia_bin" ]] || log_fail "Missing bundle binary: ${kopia_bin}." 2
		install_file_atomically "$kopia_bin" "${INSTALL_DIR}/kopia" 755
		log_ok "deployed ${INSTALL_DIR}/kopia"
	fi
	ver="$(bundle_version_from "${src_root}")"
	printf '%s\n' "$ver" >"${INSTALLED_VERSION_FILE}.new.$$"
	mv -f "${INSTALLED_VERSION_FILE}.new.$$" "${INSTALLED_VERSION_FILE}"
	log_ok "wrote ${INSTALLED_VERSION_FILE} (${ver})"
}

write_agent_env() {
	local env_file="$1"
	local kopia_path="${INSTALL_DIR}/kopia"
	local name existing_value
	mkdir -p "$(dirname "$env_file")"
	umask 077
	if [[ ! -f "${env_file}" && -n "${LEGACY_ENV_SOURCE}" && -f "${LEGACY_ENV_SOURCE}" ]]; then
		cp -p "${LEGACY_ENV_SOURCE}" "${env_file}"
	fi
	[[ -n "${WSS_URL}" ]] && set_agent_env_key "${env_file}" HFL_WSS_URL "${WSS_URL}"
	[[ -n "${API_BASE}" ]] && set_agent_env_key "${env_file}" HFL_API_BASE "${API_BASE}"
	[[ -n "${ORG_KEY}" ]] && set_agent_env_key "${env_file}" HFL_ORG_KEY "${ORG_KEY}"
	[[ -n "${NODE_TOKEN}" ]] && set_agent_env_key "${env_file}" HFL_NODE_TOKEN "${NODE_TOKEN}"
	[[ -n "${NODE_ID}" ]] && set_agent_env_key "${env_file}" HFL_NODE_ID "${NODE_ID}"
	set_agent_env_key "${env_file}" HFL_DATA_DIR "${DATA_DIR}"
	set_agent_env_key "${env_file}" HFL_AGENT_ROOT "${AGENT_ROOT}"
	existing_value="$(read_env_value "${env_file}" HFL_NODE_ROLE || true)"
	[[ -n "${existing_value}" && "${NODE_ROLE}" == "agent" ]] && NODE_ROLE="${existing_value}"
	set_agent_env_key "${env_file}" HFL_NODE_ROLE "${NODE_ROLE}"
	set_agent_env_key "${env_file}" HFL_INSTALLATION_MODE "${INSTALLATION_MODE}"
	[[ -z "${RUN_AS_USER}" ]] || set_agent_env_key "${env_file}" HFL_RUN_AS_USER "${RUN_AS_USER}"
	[[ -z "${RUN_AS_HOME}" ]] || set_agent_env_key "${env_file}" HFL_RUN_AS_HOME "${RUN_AS_HOME}"
	set_agent_env_key "${env_file}" HFL_KOPIA_PATH "${kopia_path}"
	set_agent_env_key "${env_file}" HFL_INSECURE_TLS "${HFL_INSECURE_TLS:-1}"
	for name in SENTRY_ENABLED SENTRY_BACKEND_DSN SENTRY_ENVIRONMENT SENTRY_RELEASE SENTRY_TRACES_SAMPLE_RATE HFL_SENTRY_LENSNODE_RELEASE; do
		[[ -z "${!name:-}" ]] || set_agent_env_key "${env_file}" "${name}" "${!name}"
	done
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
	if is_user_mode; then
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
			if hfl_systemctl is-active hyperfilelens-agent.service >/dev/null 2>&1; then
				log_fail "Agent service did not stop; upgrade was aborted before deployment." 2
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
		local env_file="$(agent_env_file "${DEFAULT_DATA}")"
		local resolved
		resolved="$(resolve_data_dir)"
		[[ -f "$(agent_env_file "${resolved}")" ]] && env_file="$(agent_env_file "${resolved}")"
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
	# The upgrade path rewrites the unit and any drop-ins before restoring the
	# previous running state. Reload systemd explicitly so start never uses a
	# stale unit definition from its manager cache.
	hfl_systemctl daemon-reload
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
			# Bootstrap is normally invoked as `sudo bash -s`, so stdin is the
			# bootstrap script rather than an interactive terminal. Prefer the
			# invoking sudo account and only prompt when a terminal is available.
			if [[ -t 0 ]]; then
				read -r -p "Enter the existing ordinary account to protect${default_run_as_user:+ [${default_run_as_user}]}: " RUN_AS_USER || true
			fi
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
	ensure_agent_layout "${DATA_DIR}"
	if is_user_mode; then
		[[ "${DATA_DIR}" == "${DEFAULT_DATA}" ]] \
			|| log_fail "User-level installation uses the fixed data directory ${DEFAULT_DATA}; --data-dir is not supported." 2
		chmod 700 "${AGENT_ROOT}" "${DATA_DIR}"
	fi
	if [[ "${INSTALLATION_MODE}" == "account" ]]; then
		chmod 755 "${AGENT_ROOT}" "${INSTALL_DIR}"
		chown -R "${RUN_AS_USER}:" "$(agent_config_dir "${DATA_DIR}")" "$(agent_data_store_dir "${DATA_DIR}")" \
			"$(agent_logs_dir "${DATA_DIR}")" "$(agent_cache_dir "${DATA_DIR}")" \
			"$(agent_mounts_dir "${DATA_DIR}")" "$(agent_runtime_dir "${DATA_DIR}")" \
			"$(agent_lifecycle_dir "${DATA_DIR}")" "$(agent_backup_dir "${DATA_DIR}")" 2>/dev/null \
			|| chown -R "${RUN_AS_USER}" "$(agent_config_dir "${DATA_DIR}")" "$(agent_data_store_dir "${DATA_DIR}")" \
			"$(agent_logs_dir "${DATA_DIR}")" "$(agent_cache_dir "${DATA_DIR}")" "$(agent_mounts_dir "${DATA_DIR}")" \
			"$(agent_runtime_dir "${DATA_DIR}")" "$(agent_lifecycle_dir "${DATA_DIR}")" "$(agent_backup_dir "${DATA_DIR}")"
		chmod 700 "$(agent_config_dir "${DATA_DIR}")" "$(agent_data_store_dir "${DATA_DIR}")" \
			"$(agent_logs_dir "${DATA_DIR}")" "$(agent_cache_dir "${DATA_DIR}")" "$(agent_mounts_dir "${DATA_DIR}")" \
			"$(agent_runtime_dir "${DATA_DIR}")" "$(agent_lifecycle_dir "${DATA_DIR}")" "$(agent_backup_dir "${DATA_DIR}")"
	elif [[ "${INSTALLATION_MODE}" == "system" ]]; then
		chmod 755 "${AGENT_ROOT}" "${INSTALL_DIR}"
		chmod 700 "$(agent_config_dir "${DATA_DIR}")" "$(agent_data_store_dir "${DATA_DIR}")" \
			"$(agent_logs_dir "${DATA_DIR}")" "$(agent_cache_dir "${DATA_DIR}")" "$(agent_mounts_dir "${DATA_DIR}")" \
			"$(agent_runtime_dir "${DATA_DIR}")" "$(agent_lifecycle_dir "${DATA_DIR}")" "$(agent_backup_dir "${DATA_DIR}")"
	fi
	migrate_legacy_layout
	if [[ "${INSTALLATION_MODE}" == "account" ]]; then
		chown -R "${RUN_AS_USER}:" "$(agent_config_dir "${DATA_DIR}")" "$(agent_data_store_dir "${DATA_DIR}")" \
			"$(agent_logs_dir "${DATA_DIR}")" "$(agent_cache_dir "${DATA_DIR}")" "$(agent_mounts_dir "${DATA_DIR}")" \
			"$(agent_runtime_dir "${DATA_DIR}")" "$(agent_lifecycle_dir "${DATA_DIR}")" "$(agent_backup_dir "${DATA_DIR}")" 2>/dev/null \
			|| chown -R "${RUN_AS_USER}" "$(agent_config_dir "${DATA_DIR}")" "$(agent_data_store_dir "${DATA_DIR}")" \
			"$(agent_logs_dir "${DATA_DIR}")" "$(agent_cache_dir "${DATA_DIR}")" "$(agent_mounts_dir "${DATA_DIR}")" \
			"$(agent_runtime_dir "${DATA_DIR}")" "$(agent_lifecycle_dir "${DATA_DIR}")" "$(agent_backup_dir "${DATA_DIR}")"
		chmod 700 "$(agent_config_dir "${DATA_DIR}")" "$(agent_data_store_dir "${DATA_DIR}")" \
			"$(agent_logs_dir "${DATA_DIR}")" "$(agent_cache_dir "${DATA_DIR}")" "$(agent_mounts_dir "${DATA_DIR}")" \
			"$(agent_runtime_dir "${DATA_DIR}")" "$(agent_lifecycle_dir "${DATA_DIR}")" "$(agent_backup_dir "${DATA_DIR}")"
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
		if is_user_mode; then
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
	write_agent_env "$(agent_env_file "${DATA_DIR}")"
	if [[ "${INSTALLATION_MODE}" == "account" ]]; then
		chown "${RUN_AS_USER}" "$(agent_env_file "${DATA_DIR}")" 2>/dev/null || true
		chown -R "${RUN_AS_USER}" "$(agent_logs_dir "${DATA_DIR}")" 2>/dev/null || true
	fi

	if agent_uses_launchd; then
		if [[ $NO_START -eq 1 ]]; then
			write_run_agent_script "$(agent_env_file "${DATA_DIR}")"
			install_launchd_plist "$(agent_env_file "${DATA_DIR}")"
			if [[ $QUIET_FOOTER -eq 0 ]]; then
				log_skip "Launchd service ${LAUNCHD_LABEL} was not started (--no-start)."
				hfl_print_install_success "$(hfl_role_display_name "${NODE_ROLE}" "${HFL_GATEWAY_SCOPE:-}")" "$(bundle_version)" "not started" "${DATA_DIR}"
			fi
			return 0
		fi
		start_launchd_service "$(agent_env_file "${DATA_DIR}")"
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

	install_systemd_unit_logged "$(agent_env_file "${DATA_DIR}")"
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
	cleanup_legacy_layout
	if [[ $QUIET_FOOTER -eq 0 ]]; then
		hfl_print_install_success "$(hfl_role_display_name "${NODE_ROLE}" "${HFL_GATEWAY_SCOPE:-}")" "$(bundle_version)" "$(service_status_line)" "${DATA_DIR}"
	fi
}

cmd_reconcile_legacy() {
	parse_reconcile_flags "$@"
	require_root
	require_service_manager
	DATA_DIR="$(resolve_data_dir)"
	ensure_agent_layout "${DATA_DIR}"
	begin_install_log "${DATA_DIR}" "migration"
	trap 'finish_install_log $?' RETURN

	if ! legacy_layout_present; then
		log_skip "no legacy Agent layout requires reconciliation"
		return 0
	fi
	# The caller has already started and health-checked the new service. Archive
	# old inactive paths without stopping that service a second time, then apply
	# the normal guarded cleanup.
	migrate_legacy_layout 1 0
	cleanup_legacy_layout
}

cmd_upgrade() {
	parse_upgrade_flags "$@"
	require_root
	require_service_manager

	[[ -n "${UPGRADE_FROM}" ]] || log_fail "Upgrade requires --from <directory-or.tar.gz>." 2
	if [[ "${AGENT_ONLY}" -eq 1 || "${KOPIA_ONLY}" -eq 1 ]]; then
		log_fail "Transactional upgrade requires the complete Agent package; --agent-only and --kopia-only are not supported." 2
	fi

	local legacy_upgrade=0 legacy_residue=0
	if ! is_installed && legacy_layout_present; then
		DATA_DIR="${DEFAULT_DATA}"
		ensure_agent_layout "${DATA_DIR}"
		legacy_upgrade=1
	elif is_installed && legacy_layout_present; then
		# A first upgrade can have the new binary in place while the old
		# /var/lib state is still authoritative. Only archive legacy paths when
		# the canonical env and database are both complete; otherwise migrate
		# them before merging the target package so installation identity and
		# local task state are preserved.
		if [[ ! -f "$(agent_env_file "${DEFAULT_DATA}")" ]] \
			|| [[ ! -f "$(agent_data_store_dir "${DEFAULT_DATA}")/agent.db" ]]; then
			DATA_DIR="${DEFAULT_DATA}"
			ensure_agent_layout "${DATA_DIR}"
			legacy_upgrade=1
		else
			legacy_residue=1
		fi
	fi
	if ! is_installed && [[ "${legacy_upgrade}" -eq 0 ]]; then
		local command_prefix=""
		[[ "${INSTALLATION_MODE}" == "system" ]] && command_prefix="sudo "
		log_fail "The agent is not installed. Run ${command_prefix}./install.sh install first." 2
	fi

	local data_dir prev_ver src_root new_ver env_file upgrade_ws
	data_dir="$(resolve_data_dir)"
	# Legacy reconciliation still uses the installer-wide DATA_DIR variable.
	# Keep it synchronized with the resolved authoritative Agent Root before
	# archiving any residue; otherwise a partially migrated root would issue
	# `mkdir -p ""` and abort under `set -u`.
	[[ -n "${data_dir}" && "${data_dir}" == /* ]] \
		|| log_fail "Upgrade could not resolve a valid Agent Root data directory." 2
	DATA_DIR="${data_dir}"
	env_file="$(agent_env_file "${data_dir}")"
	upgrade_ws="$(upgrade_workspace_dir "${data_dir}")"
	[[ -n "${upgrade_ws}" && "${upgrade_ws}" == /* ]] \
		|| log_fail "Upgrade could not resolve its workspace directory." 2
	prev_ver="unknown"
	if [[ -f "$INSTALLED_VERSION_FILE" ]]; then
		prev_ver="$(tr -d ' \t\r\n' <"$INSTALLED_VERSION_FILE")"
	elif [[ "${legacy_upgrade}" -eq 1 && -f "${LEGACY_INSTALL_DIR}/INSTALLED_VERSION" ]]; then
		prev_ver="$(tr -d ' \t\r\n' <"${LEGACY_INSTALL_DIR}/INSTALLED_VERSION")"
	fi
	UPGRADE_PREVIOUS_VERSION="${prev_ver}"
	UPGRADE_TARGET_VERSION="unknown"
	begin_install_log "${data_dir}" "upgrade"
	trap 'finish_install_log $?' RETURN

	# EXIT traps run after this function's local variables leave scope. Use a
	# default expansion so a failure before workspace assignment cannot trigger
	# a secondary `set -u` error and hide the original upgrade failure.
	acquire_lifecycle_lock "${data_dir}" "upgrade"
	# Keep log finalization installed while recovering an older transaction or
	# resolving the package; these steps can fail before the main transaction
	# cleanup trap is installed below.
	trap 'rc=$?; release_lifecycle_lock; hfl_finalize_active_log "$rc"' EXIT
	recover_interrupted_upgrade "${data_dir}" "upgrade"
	prev_ver="unknown"
	if [[ -f "${INSTALLED_VERSION_FILE}" ]]; then
		prev_ver="$(tr -d ' \t\r\n' <"${INSTALLED_VERSION_FILE}")"
	elif [[ "${legacy_upgrade}" -eq 1 && -f "${LEGACY_INSTALL_DIR}/INSTALLED_VERSION" ]]; then
		prev_ver="$(tr -d ' \t\r\n' <"${LEGACY_INSTALL_DIR}/INSTALLED_VERSION")"
	fi
	UPGRADE_PREVIOUS_VERSION="${prev_ver}"
	UPGRADE_TARGET_VERSION="unknown"
	UPGRADE_SERVICE_WAS_ACTIVE=0
	UPGRADE_SERVICE_WAS_ENABLED=0
	UPGRADE_STATE_SNAPSHOT_READY=0
	UPGRADE_DEPLOYMENT_STARTED=0
	UPGRADE_STOP_ATTEMPTED=0
	write_upgrade_state "${data_dir}" "preparing"
	trap 'rc=$?; if [[ "$rc" -ne 0 && "${UPGRADE_STOP_ATTEMPTED}" -eq 0 ]]; then clear_upgrade_state; cleanup_upgrade_rollback "${data_dir}"; fi; cleanup_upgrade_workspace "${upgrade_ws:-}"; release_lifecycle_lock; hfl_finalize_active_log "$rc"' EXIT
	src_root="$(prepare_upgrade_source "${UPGRADE_FROM}" "${data_dir}")"
	new_ver="$(bundle_version_from "${src_root}")"
	UPGRADE_TARGET_VERSION="${new_ver}"
	write_upgrade_state "${data_dir}" "package_resolved"

	if [[ "${new_ver}" == "${prev_ver}" ]]; then
		confirm_same_version_upgrade "${prev_ver}"
	elif [[ "${prev_ver}" != "unknown" && "${new_ver}" != "unknown" ]] \
		&& ! is_main_build "${new_ver}" \
		&& ! is_main_build "${prev_ver}" \
		&& version_lt "${new_ver}" "${prev_ver}"; then
		log_fail "Downgrade is not supported (${new_ver} < ${prev_ver})." 2
	fi

	local installed_role gateway_scope
	installed_role="$(read_env_value "${env_file}" "HFL_NODE_ROLE" || true)"
	verify_upgrade_package "${src_root}" "${installed_role:-agent}" "${new_ver}"
	update_lifecycle_lock_target "${new_ver}" "${src_root}/MANIFEST.json"
	if [[ $QUIET_FOOTER -eq 0 ]]; then
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
	install_nas_deps "${installed_role}" "${src_root}"
	# Do not mutate or stop a running legacy service until the requested target
	# version has passed confirmation and downgrade checks.
	if [[ "${legacy_upgrade}" -eq 1 ]]; then
		migrate_legacy_layout
	elif [[ "${legacy_residue}" -eq 1 ]]; then
		migrate_legacy_layout 1
	fi
	hfl_print_section "Upgrading Agent"
	backup_upgrade_binaries "${data_dir}"
	UPGRADE_STATE_SNAPSHOT_READY=0
	UPGRADE_DEPLOYMENT_STARTED=0
	UPGRADE_STOP_ATTEMPTED=0
	UPGRADE_SERVICE_WAS_ACTIVE=0
	if agent_manages_service; then
		if agent_uses_launchd; then
			launchctl print "${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL}" >/dev/null 2>&1 && UPGRADE_SERVICE_WAS_ACTIVE=1
		elif hfl_systemctl is-active hyperfilelens-agent.service >/dev/null 2>&1; then
			UPGRADE_SERVICE_WAS_ACTIVE=1
		fi
		if agent_uses_systemd && hfl_systemctl is-enabled hyperfilelens-agent.service >/dev/null 2>&1; then
			UPGRADE_SERVICE_WAS_ENABLED=1
		fi
	fi
	UPGRADE_TRANSACTION_ACTIVE=1
	trap upgrade_rollback_on_error ERR

	write_upgrade_state "${data_dir}" "snapshotting"
	backup_agent_config_and_db "${data_dir}" "${prev_ver}" "${src_root}"
	UPGRADE_STATE_SNAPSHOT_READY=1
	write_upgrade_state "${data_dir}" "state_snapshotted"
	# All rollback state is captured before touching the running Agent. This
	# keeps a stop failure non-destructive and preserves a standalone consistent
	# SQLite snapshot for any later deployment failure.
	UPGRADE_STOP_ATTEMPTED=1
	write_upgrade_state "${data_dir}" "stopping"
	stop_service
	write_upgrade_state "${data_dir}" "service_stopped"
	UPGRADE_DEPLOYMENT_STARTED=1
	write_upgrade_state "${data_dir}" "deploying"
	deploy_binaries "${src_root}"
	write_upgrade_state "${data_dir}" "deployed"
	merge_agent_env "${env_file}" "${data_dir}"
	write_upgrade_state "${data_dir}" "migrating"
	migrate_agent_db "${data_dir}"
	write_upgrade_state "${data_dir}" "migrated"

	write_upgrade_state "${data_dir}" "configuring_service"
	if agent_uses_launchd; then
		write_run_agent_script "${env_file}"
		install_launchd_plist "${env_file}"
	elif agent_uses_systemd && [[ -f "${src_root}/systemd/hyperfilelens-agent.service" ]]; then
		install_systemd_unit_logged "${env_file}" "${src_root}"
	fi

	if [[ $NO_RESTART -eq 1 ]]; then
		write_upgrade_state "${data_dir}" "awaiting_restart"
		UPGRADE_TRANSACTION_ACTIVE=0
		trap - ERR
		cleanup_upgrade_workspace "${upgrade_ws}"
		if [[ $QUIET_FOOTER -eq 0 ]]; then
			log_skip "Service $(service_display_name) was not restarted (--no-restart)."
			log_warn "Rollback snapshot retained until the upgraded Agent is started and verified."
			hfl_print_upgrade_success "${new_ver}" "not restarted; verification pending" "${data_dir}"
		fi
		return 0
	fi

	if [[ "${UPGRADE_SERVICE_WAS_ACTIVE}" -eq 1 ]] && agent_manages_service; then
		write_upgrade_state "${data_dir}" "starting_service"
		# Restore the previous running state without changing the systemd enable
		# policy. A manually started disabled unit must remain disabled.
		start_service_only
	fi
	write_upgrade_state "${data_dir}" "service_started"
	if ! upgrade_health_check "${data_dir}" "${new_ver}" "${UPGRADE_SERVICE_WAS_ACTIVE}"; then
		upgrade_rollback_on_error 1
		return 1
	fi
	UPGRADE_TRANSACTION_ACTIVE=0
	trap - ERR
	write_upgrade_state "${data_dir}" "healthy"
	cleanup_upgrade_workspace "${upgrade_ws}"
	cleanup_legacy_layout
	write_upgrade_state "${data_dir}" "committed"
	cleanup_upgrade_rollback "${data_dir}"
	clear_upgrade_state
	# The upgrade command may have been launched by the previous release's
	# installer. That old process has just deployed the new installer, so run
	# the new reconciliation command once to clean legacy paths on the first
	# upgrade instead of requiring a second user-triggered upgrade.
	if [[ "${NO_RESTART}" -eq 0 && -x "${INSTALL_DIR}/install.sh" ]]; then
		"${INSTALL_DIR}/install.sh" reconcile-legacy --quiet-footer || \
			log_warn "legacy Agent reconciliation was deferred; old paths were preserved"
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
	# Complete removal deletes agent.env entirely, so retirement is unnecessary.
	[[ "${KEEP_DATA}" -eq 1 ]] || return 0
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
	[[ "${KEEP_DATA}" -eq 1 ]] || purge_args+=(--purge-all)
	log_step "Removing the Data Gateway AI engine before the Agent."
	HFL_AGENT_ENV_FILE="${env_file}" \
		bash "${GATEWAY_LIFECYCLE_SCRIPT}" uninstall-sidecar "${purge_args[@]}"
	log_ok "Data Gateway AI engine removal completed."
}

gateway_workspace_mounts_in_agent_root() {
	local agent_root="${1%/}" workspace_root target canonical_target targets=""
	workspace_root="$(readlink -m -- "${agent_root}/workspace")" || return 1
	if command -v findmnt >/dev/null 2>&1; then
		targets="$(LC_ALL=C findmnt -rn -o TARGET 2>/dev/null)" || targets=""
	fi
	if [[ -z "${targets}" && -r /proc/mounts ]]; then
		targets="$(awk '{ print $2 }' /proc/mounts)" || return 1
	elif [[ -z "${targets}" ]]; then
		return 1
	fi
	while IFS= read -r target; do
		[[ -n "${target}" ]] || continue
		canonical_target="$(readlink -m -- "${target}")" || continue
		if [[ "${canonical_target}" == "${workspace_root}" || "${canonical_target}" == "${workspace_root}"/* ]]; then
			printf '%s\n' "${canonical_target}"
		fi
	done <<<"${targets}"
}

assert_gateway_workspace_purge_safe() {
	local agent_root="$1" mounts
	mounts="$(gateway_workspace_mounts_in_agent_root "${agent_root}")" \
		|| log_fail "Could not verify Gateway workspace mounts; refusing complete removal." 2
	mounts="$(printf '%s\n' "${mounts}" | sort -u)"
	[[ -z "${mounts}" ]] || log_fail \
		"Refusing complete removal while Gateway workspace storage is mounted (${mounts//$'\n'/, }); unmount it manually and retry." 2
}

cmd_uninstall() {
	parse_uninstall_flags "$@"
	require_root

	local resolved_data env_file
	resolved_data="$(resolve_data_dir)"
	env_file="$(agent_env_file "${resolved_data}")"
	if [[ "${KEEP_DATA}" -eq 0 ]] \
		&& ! data_dir_allowed_for_removal "${resolved_data}"; then
		log_fail "Refusing complete removal for unexpected data directory ${resolved_data}." 2
	fi
	acquire_lifecycle_lock "${resolved_data}" "uninstall"
	trap 'release_lifecycle_lock' EXIT
	begin_uninstall_log "${resolved_data}"
	trap 'rc=$?; release_lifecycle_lock; hfl_finalize_active_log "$rc"' EXIT

	local installed_role gateway_scope node_id installed_version data_policy
	installed_role="$(read_env_value "${env_file}" "HFL_NODE_ROLE" || true)"
	gateway_scope="$(read_env_value "${env_file}" "HFL_GATEWAY_SCOPE" || true)"
	node_id="$(read_env_value "${env_file}" "HFL_NODE_ID" || true)"
	installed_version="unknown"
	[[ -f "${INSTALLED_VERSION_FILE}" ]] && installed_version="$(tr -d ' \t\r\n' <"${INSTALLED_VERSION_FILE}")"
	data_policy="remove"
	[[ "${KEEP_DATA}" -eq 0 ]] || data_policy="preserve"
	if [[ "${KEEP_DATA}" -eq 0 && "$(uname -s)" == "Linux" ]]; then
		assert_gateway_workspace_purge_safe "${resolved_data}"
	fi
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
	if [[ "${KEEP_DATA}" -eq 1 && "${KEEP_INSTALLATION_IDENTITY}" -eq 0 \
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
	remove_install_file "${MANIFEST_FILE}"
	remove_install_file "${INSTALLED_VERSION_FILE}"
	if data_dir_allowed_for_removal "${INSTALL_DIR}" && [[ -e "${INSTALL_DIR}" ]]; then
		rm -rf "${INSTALL_DIR}"
		log_ok "Install directory removed (${INSTALL_DIR}, including backup artifacts)."
	else
		if [[ -d "$(agent_backup_dir "${resolved_data}")" ]]; then
			rm -rf "$(agent_backup_dir "${resolved_data}")"
			log_ok "Removed $(agent_backup_dir "${resolved_data}")."
		fi
		if rmdir "${INSTALL_DIR}" 2>/dev/null; then
			log_ok "Install directory removed (${INSTALL_DIR})."
		else
			log_skip "Install directory ${INSTALL_DIR} was not removed (not empty or not present)."
		fi
	fi
	if [[ $KEEP_DATA -eq 0 && -f "$env_file" ]]; then
		rm -f "$env_file"
		log_ok "Removed ${env_file}."
	elif [[ -f "$env_file" ]]; then
		if [[ "${KEEP_INSTALLATION_IDENTITY}" -eq 1 ]]; then
			log_skip "${env_file} and installation identity were preserved for install retry."
		else
			log_skip "${env_file} was preserved without installation identity."
		fi
	else
		log_skip "${env_file} was not present."
	fi

	if [[ $KEEP_DATA -eq 1 ]]; then
		log_skip "Data directory ${resolved_data} was preserved by --keep-data."
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
	hfl_print_value "Data path" "$([[ "${KEEP_DATA}" -eq 0 ]] && echo removed || echo preserved)"
	hfl_print_value "Console record" "not changed by local uninstall"
	if [[ "${KEEP_DATA}" -eq 1 ]]; then
		hfl_print_value "Log file" "$(agent_logs_dir "${resolved_data}")/uninstall.log"
	fi
	finish_uninstall_log 0
	release_lifecycle_lock
	trap - EXIT
}

cmd_status() {
	local installed="unknown" env_file="$(agent_env_file "${DEFAULT_DATA}")"
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
	local data_dir state_file phase
	data_dir="$(resolve_data_dir)"
	state_file="$(agent_lifecycle_dir "${data_dir}")/upgrade-state.json"
	phase="$(upgrade_state_value "${state_file}" phase || true)"
	if [[ -f "${state_file}" ]]; then
		DATA_DIR="${data_dir}"
		acquire_lifecycle_lock "${data_dir}" "upgrade-verification"
		trap 'release_lifecycle_lock' EXIT
		local recovery_rc=0
		if recover_interrupted_upgrade "${data_dir}" "start"; then
			:
		else
			recovery_rc=$?
			[[ "${recovery_rc}" -eq 2 ]] || return "${recovery_rc}"
		fi
		phase="${UPGRADE_CURRENT_PHASE}"
	fi
	if [[ "${phase}" == "awaiting_restart" ]]; then
		[[ -n "${UPGRADE_PREVIOUS_VERSION}" ]] || UPGRADE_PREVIOUS_VERSION="unknown"
		[[ -n "${UPGRADE_TARGET_VERSION}" ]] || log_fail "Pending upgrade state is missing its target version (${state_file})." 2
		UPGRADE_DEPLOYMENT_STARTED=1
		UPGRADE_STOP_ATTEMPTED=1
		UPGRADE_TRANSACTION_ACTIVE=1
		trap upgrade_rollback_on_error ERR
		log_step "Starting the staged Agent upgrade for local health verification."
		write_upgrade_state "${data_dir}" "starting_service"
		start_service_only
		write_upgrade_state "${data_dir}" "service_started"
		if ! upgrade_health_check "${data_dir}" "${UPGRADE_TARGET_VERSION}"; then
			if upgrade_rollback_on_error 1; then :; fi
			log_fail "The staged Agent failed local health verification; the previous version was restored." 1
		fi
		UPGRADE_TRANSACTION_ACTIVE=0
		trap - ERR
		write_upgrade_state "${data_dir}" "committed"
		cleanup_upgrade_rollback "${data_dir}"
		clear_upgrade_state
		release_lifecycle_lock
		trap - EXIT
		log_ok "Staged upgrade to ${UPGRADE_TARGET_VERSION} passed local health verification."
		return 0
	fi
	if [[ -n "${phase}" ]]; then
		release_lifecycle_lock
		trap - EXIT
	fi
	acquire_lifecycle_lock "${data_dir}" "start"
	trap 'release_lifecycle_lock' EXIT
	log_step "Starting $(service_display_name)."
	start_service_only
	log_ok "Service $(service_display_name) is $(service_status_line)."
	release_lifecycle_lock
	trap - EXIT
}

cmd_stop() {
	require_root
	require_agent_installed
	local data_dir
	data_dir="$(resolve_data_dir)"
	acquire_lifecycle_lock "${data_dir}" "stop"
	trap 'release_lifecycle_lock' EXIT
	log_step "Stopping $(service_display_name)."
	stop_service
	log_ok "Service $(service_display_name) is $(service_status_line)."
	release_lifecycle_lock
	trap - EXIT
}

cmd_restart() {
	require_root
	require_agent_installed
	local data_dir state_file phase
	data_dir="$(resolve_data_dir)"
	state_file="$(agent_lifecycle_dir "${data_dir}")/upgrade-state.json"
	phase="$(upgrade_state_value "${state_file}" phase || true)"
	if [[ -f "${state_file}" ]]; then
		cmd_start
		return 0
	fi
	acquire_lifecycle_lock "${data_dir}" "restart"
	trap 'release_lifecycle_lock' EXIT
	log_step "Restarting $(service_display_name)."
	stop_service
	if agent_manages_service; then
		start_service_only
	fi
	log_ok "Service $(service_display_name) is $(service_status_line)."
	release_lifecycle_lock
	trap - EXIT
}

case "$CMD" in
	install) cmd_install "$@" ;;
	reconcile-legacy) cmd_reconcile_legacy "$@" ;;
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
