#!/usr/bin/env bash
# HyperFileLens Agent bundle installer (Linux / macOS).
# Usage: install.sh [command] [options]
# When no command is given, equivalent to: install.sh install
# After install, install.sh and MANIFEST.json are copied to /opt/hyperfilelens-agent/
# for local uninstall/status. Upgrade extracts to DATA_DIR/runtime/workspace.

set -euo pipefail

BUNDLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Unix paths use product slug "hyperfilelens-agent" (see internal/platform/vfs/paths.go).
INSTALL_DIR="/opt/hyperfilelens-agent"
UNIT_DST="/etc/systemd/system/hyperfilelens-agent.service"
GATEWAY_RESOURCE_DROPIN="/etc/systemd/system/hyperfilelens-agent.service.d/20-gateway-resources.conf"
DEFAULT_DATA="/var/lib/hyperfilelens-agent"
INSTALLED_VERSION_FILE="${INSTALL_DIR}/INSTALLED_VERSION"
LAUNCHD_PLIST="/Library/LaunchDaemons/com.hyperfilelens.agent.plist"
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
UPGRADE_FROM=""
UPGRADE_YES=0

usage() {
	cat <<'USAGE'
Usage: install.sh [command] [options]

When no command is given, equivalent to: install.sh install

Commands:
  install       Install agent binaries and configuration (install dir /opt/hyperfilelens-agent)
  start         Start hyperfilelens-agent.service
  stop          Stop hyperfilelens-agent.service
  restart       Stop then start hyperfilelens-agent.service
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
    --data-dir PATH     Data directory (default: /var/lib/hyperfilelens-agent)
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
  /opt/hyperfilelens-agent                         Binaries and installer scripts
  /opt/hyperfilelens-agent/libexec/gateway-lifecycle.sh
                                                   Data Gateway LensNode lifecycle helper
  /var/lib/hyperfilelens-agent                     Runtime data, backup, and configuration
  /var/lib/hyperfilelens-agent/backup/state/       Pre-upgrade agent.env/agent.db snapshot (retained until uninstall --purge-all)
  /opt/hyperfilelens-agent/backup/                 Legacy upgrade archives (removed on uninstall)
  /etc/systemd/system/hyperfilelens-agent.service  systemd unit (Linux)
  /etc/systemd/system/hyperfilelens-agent.service.d/20-gateway-resources.conf
                                                   Soft resource policy for role=gateway
  /Library/LaunchDaemons/com.hyperfilelens.agent.plist  LaunchDaemon (macOS)

Examples:
  sudo ./install.sh
  sudo ./install.sh install --wss-url 'wss://console.example/ws/node/agent/' --api-base 'https://console.example' --org-key 'org_xxx' --node-token 'tok_xxx'
  sudo ./install.sh start
  sudo ./install.sh stop
  sudo ./install.sh restart
  sudo ./install.sh status
  sudo ./install.sh upgrade --from /path/to/hfl-agent-0.1.0-linux-amd64.tar.gz
  sudo ./install.sh uninstall
  sudo ./install.sh uninstall --purge-all
USAGE
}

hfl_systemctl() {
	PYTHONWARNINGS=ignore::SyntaxWarning systemctl "$@"
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
	if [[ "$(id -u)" -ne 0 ]]; then
		log_fail "Administrator privileges are required. Re-run with sudo." 1
	fi
}

require_agent_installed() {
	if ! is_installed; then
		log_fail "The agent is not installed. Run sudo ./install.sh install first." 2
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
	local quoted_env_file
	quoted_env_file="$(printf '%q' "${env_file}")"
	cat >"${RUN_AGENT_SCRIPT}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
ENV_FILE=${quoted_env_file}
if [[ -f "\$ENV_FILE" ]]; then
	set -a
	# shellcheck disable=SC1090
	source "\$ENV_FILE"
	set +a
fi
exec ${INSTALL_DIR}/hfl-agent run
EOF
	chmod 755 "${RUN_AGENT_SCRIPT}"
	log_ok "wrote ${RUN_AGENT_SCRIPT}"
}

install_launchd_plist() {
	local env_file="$1"
	local data_dir log_dir stdout stderr
	data_dir="$(dirname "${env_file}")"
	log_dir="${data_dir}/logs"
	stdout="${log_dir}/launchd.stdout.log"
	stderr="${log_dir}/launchd.stderr.log"
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
		<string>${RUN_AGENT_SCRIPT}</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>WorkingDirectory</key>
	<string>${INSTALL_DIR}</string>
	<key>StandardOutPath</key>
	<string>${stdout}</string>
	<key>StandardErrorPath</key>
	<string>${stderr}</string>
</dict>
</plist>
EOF
	chmod 644 "${LAUNCHD_PLIST}"
	log_ok "installed launchd plist ${LAUNCHD_PLIST}"
}

launchd_service_status_line() {
	if launchctl print "system/${LAUNCHD_LABEL}" >/dev/null 2>&1; then
		local state
		state="$(launchctl print "system/${LAUNCHD_LABEL}" 2>/dev/null | awk -F'= ' '/state =/{print $2; exit}' | tr -d ' ;')"
		echo "${state:-loaded}"
	else
		echo "not loaded"
	fi
}

stop_launchd_service() {
	if launchctl print "system/${LAUNCHD_LABEL}" >/dev/null 2>&1; then
		launchctl bootout "system/${LAUNCHD_LABEL}" 2>/dev/null \
			|| launchctl bootout system "${LAUNCHD_PLIST}" 2>/dev/null \
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
	if launchctl bootstrap system "${LAUNCHD_PLIST}" 2>/dev/null; then
		log_ok "bootstrapped launchd ${LAUNCHD_LABEL}"
	else
		log_skip "bootstrap launchd ${LAUNCHD_LABEL} (may already be loaded)"
	fi
	if launchctl kickstart -k "system/${LAUNCHD_LABEL}" 2>/dev/null; then
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
	if launchctl kickstart -k "system/${LAUNCHD_LABEL}" 2>/dev/null; then
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
	printf '[%s] [%s] %s\n' "$(hfl_now)" "${level}" "$(hfl_finish_sentence "$*")"
}

log_info() { _hfl_emit_raw "INFO " "$@" >&2; }
log_ok() { _hfl_emit_raw " OK  " "$@" >&2; }
log_step() { _hfl_emit_raw "STEP " "$@" >&2; }
log_skip() { _hfl_emit_raw "SKIP " "$@" >&2; }
log_warn() { _hfl_emit_raw "WARN " "$@" >&2; }
log_fail() {
	_hfl_emit_raw "FAIL " "$@" >&2
	exit "${2:-1}"
}

begin_install_log() {
	local data_dir="$1"
	local log_file="${data_dir}/logs/install.log"
	mkdir -p "$(dirname "${log_file}")"
	if [[ $QUIET_FOOTER -eq 1 ]]; then
		{
			log_info "Install session started."
		} >>"${log_file}" 2>&1
		exec >>"${log_file}" 2>&1
	else
		log_info "Install session started."
		exec > >(tee -a "${log_file}") 2>&1
	fi
}

finish_install_log() {
	local rc="$1"
	if [[ "${rc}" -eq 0 ]]; then
		log_info "Install session finished successfully."
	else
		log_warn "Install session finished with errors (exit=${rc})."
	fi
}

begin_uninstall_log() {
	local data_dir="$1"
	local log_file="${data_dir}/logs/uninstall.log"
	mkdir -p "$(dirname "${log_file}")"
	log_info "Uninstall session started."
	exec > >(tee -a "${log_file}") 2>&1
}

finish_uninstall_log() {
	local rc="$1"
	if [[ "${rc}" -eq 0 ]]; then
		log_info "Uninstall session finished successfully."
	else
		log_warn "Uninstall session finished with errors (exit=${rc})."
	fi
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
	local -a keys=(HFL_DATA_DIR HFL_KOPIA_PATH HFL_INSECURE_TLS)
	local -a vals=("${data_dir}" "${kopia_path}" "1")
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
	local p="$1"
	[[ -z "$p" ]] && return 1
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
		echo "HFL_KOPIA_PATH=${kopia_path}"
		echo "HFL_INSECURE_TLS=${HFL_INSECURE_TLS:-1}"
		for name in SENTRY_ENABLED SENTRY_BACKEND_DSN SENTRY_ENVIRONMENT SENTRY_RELEASE SENTRY_TRACES_SAMPLE_RATE HFL_SENTRY_LENSNODE_RELEASE; do
			[[ -z "${!name:-}" ]] || echo "${name}=${!name}"
		done
	} >"${env_file}"
	chmod 600 "${env_file}"
	log_ok "wrote ${env_file}"
}

install_systemd_unit() {
	local env_file="$1"
	local src_root="${2:-${BUNDLE_ROOT}}"
	local unit_src="${src_root}/systemd/hyperfilelens-agent.service"
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
	if [[ -f "${GATEWAY_RESOURCE_DROPIN}" ]]; then
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
	require_service_manager
	verify_bundle

	if is_installed; then
		log_fail "The agent is already installed. Run sudo ./install.sh upgrade --from <package.tar.gz> instead." 2
	fi

	DATA_DIR="${DATA_DIR:-$DEFAULT_DATA}"
	begin_install_log "${DATA_DIR}"
	trap 'finish_install_log $?' RETURN

	if [[ $QUIET_FOOTER -eq 0 ]]; then
		log_info "Starting HyperFileLens agent installation (version $(bundle_version))."
		log_info "Platform: $(uname -s | tr '[:upper:]' '[:lower:]')/$(bundle_arch) · Role: ${NODE_ROLE} · Install dir: ${INSTALL_DIR} · Data dir: ${DATA_DIR}."
	fi

	gateway_resource_preflight "${NODE_ROLE}" "${DATA_DIR}"
	install_nas_deps "${NODE_ROLE}"
	deploy_binaries
	write_agent_env "${DATA_DIR}/agent.env"

	if agent_uses_launchd; then
		if [[ $NO_START -eq 1 ]]; then
			write_run_agent_script "${DATA_DIR}/agent.env"
			install_launchd_plist "${DATA_DIR}/agent.env"
			if [[ $QUIET_FOOTER -eq 0 ]]; then
				log_skip "Launchd service ${LAUNCHD_LABEL} was not started (--no-start)."
				log_info "Installation completed. Start the service with sudo ${INSTALL_DIR}/install.sh start."
			fi
			return 0
		fi
		start_launchd_service "${DATA_DIR}/agent.env"
		if [[ $QUIET_FOOTER -eq 0 ]]; then
			log_info "Installation completed successfully (service: ${LAUNCHD_LABEL}, $(launchd_service_status_line))."
			log_info "Return to the HyperFileLens console to add backup sources and policies."
		fi
		return 0
	fi

	if ! agent_uses_systemd; then
		if [[ $QUIET_FOOTER -eq 0 ]]; then
			log_info "Installation completed, but systemd was not found on this host."
			log_info "Start the agent manually from ${INSTALL_DIR}/hfl-agent."
		fi
		return 0
	fi

	install_systemd_unit_logged "${DATA_DIR}/agent.env"
	if [[ $NO_START -eq 1 ]]; then
		hfl_systemctl daemon-reload 2>/dev/null || true
		log_ok "Systemd was reloaded successfully."
		if [[ $QUIET_FOOTER -eq 0 ]]; then
			log_skip "Service hyperfilelens-agent.service was not started (--no-start)."
			log_info "Installation completed. Start the service with systemctl start hyperfilelens-agent."
		fi
		return 0
	fi

	start_service
	if [[ $QUIET_FOOTER -eq 0 ]]; then
		log_info "Installation completed successfully (service: hyperfilelens-agent.service, $(service_status_line))."
		log_info "Return to the HyperFileLens console to add backup sources and policies."
	fi
}

cmd_upgrade() {
	parse_upgrade_flags "$@"
	require_root
	require_service_manager

	[[ -n "${UPGRADE_FROM}" ]] || log_fail "Upgrade requires --from <directory-or.tar.gz>." 2

	if ! is_installed; then
		log_fail "The agent is not installed. Run sudo ./install.sh install first." 2
	fi

	local data_dir prev_ver src_root new_ver env_file upgrade_ws
	data_dir="$(resolve_data_dir)"
	env_file="${data_dir}/agent.env"
	upgrade_ws="$(upgrade_workspace_dir "${data_dir}")"
	prev_ver="unknown"
	[[ -f "$INSTALLED_VERSION_FILE" ]] && prev_ver="$(tr -d ' \t\r\n' <"$INSTALLED_VERSION_FILE")"

	trap 'cleanup_upgrade_workspace "$upgrade_ws"' EXIT
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
		log_info "Starting in-place upgrade (${prev_ver} → ${new_ver})."
		log_info "Install dir: ${INSTALL_DIR} · Data dir: ${data_dir} · Source: ${UPGRADE_FROM}."
	fi

	upgrade_preflight "${data_dir}"
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
			log_info "Upgrade completed successfully (version ${new_ver})."
		fi
		return 0
	fi

	if agent_manages_service; then
		start_service
	fi

	if [[ $QUIET_FOOTER -eq 0 ]]; then
		log_info "Upgrade completed successfully (version ${new_ver}, service: $(service_display_name), $(service_status_line))."
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
	log_ok "Local installation identity retired; the next install will create a new console record."
}

uninstall_gateway_sidecar_if_needed() {
	local env_file="$1" role="" purge_args=()
	[[ "${HFL_SKIP_GATEWAY_SIDECAR_UNINSTALL:-0}" != "1" ]] || return 0
	role="$(read_env_value "${env_file}" "HFL_NODE_ROLE" || true)"
	[[ "${role}" == "gateway" ]] || return 0
	[[ "$(uname -s)" == "Linux" ]] \
		|| log_fail "Data Gateway LensNode uninstall is supported on Linux only." 2
	[[ -x "${GATEWAY_LIFECYCLE_SCRIPT}" ]] \
		|| log_fail "Missing ${GATEWAY_LIFECYCLE_SCRIPT}; upgrade the Agent before uninstalling this Data Gateway." 2
	[[ "${PURGE_ALL}" -eq 0 ]] || purge_args+=(--purge-all)
	log_step "Removing the Data Gateway LensNode sidecar before the Agent."
	HFL_AGENT_ENV_FILE="${env_file}" \
		bash "${GATEWAY_LIFECYCLE_SCRIPT}" uninstall-sidecar "${purge_args[@]}"
	log_ok "Data Gateway LensNode sidecar removal completed."
}

cmd_uninstall() {
	parse_uninstall_flags "$@"
	require_root

	local resolved_data env_file
	resolved_data="$(resolve_data_dir)"
	env_file="${resolved_data}/agent.env"
	begin_uninstall_log "${resolved_data}"
	trap 'finish_uninstall_log $?' EXIT

	log_info "Starting HyperFileLens agent uninstallation."
	log_info "Install dir: ${INSTALL_DIR} · Data dir: ${resolved_data} · Purge data: $([[ $PURGE_ALL -eq 1 ]] && echo yes || echo no)."
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

	log_info "Uninstallation completed. If this node still appears in the console, remove it there."
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
