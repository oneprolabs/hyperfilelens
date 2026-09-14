//go:build !windows

package install

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/platform/vfs"
)

const (
	unixServiceUnit      = "hyperfilelens-agent.service"
	unixUnitPath         = "/etc/systemd/system/hyperfilelens-agent.service"
	unixResourceDropIn   = "/etc/systemd/system/hyperfilelens-agent.service.d/20-gateway-resources.conf"
	unixLaunchdPlist     = "/Library/LaunchDaemons/com.hyperfilelens.agent.plist"
	unixLaunchdLabel     = "com.hyperfilelens.agent"
	uninstallDelaySecond = 5
)

// ScheduleDetachedUninstall stops the agent service and removes install/data files
// after a short delay so the running process can report task.result upstream first.
func ScheduleDetachedUninstall(
	installDir, dataDir, logDir string,
	keepData bool,
	userInstall bool,
	completion UninstallCompletion,
) error {
	installDir = strings.TrimSpace(installDir)
	if installDir == "" {
		installDir = DefaultInstallDir()
	}
	dataDir = strings.TrimSpace(dataDir)
	installDir = filepath.Clean(installDir)
	mode := model.InstallationModeSystem
	if userInstall {
		mode = model.InstallationModeUser
	}
	if dataDir == "" {
		dataDir = vfs.AgentDataDirForMode(mode)
	}
	dataDir = filepath.Clean(dataDir)
	if err := validateUnixUninstallPaths(installDir, dataDir, keepData, userInstall); err != nil {
		return err
	}
	logDir = resolveUninstallLogDir(dataDir, logDir)
	if logDir != "" {
		_ = AppendUninstallLog(
			logDir,
			fmt.Sprintf(
				"scheduled detached uninstall install_dir=%s data_dir=%s keep_data=%t",
				installDir,
				dataDir,
				keepData,
			),
		)
	}
	return scheduleDetachedUninstallUnix(
		installDir,
		dataDir,
		logDir,
		keepData,
		userInstall,
		completion,
	)
}

func validateUnixUninstallPaths(
	installDir, dataDir string,
	keepData, userInstall bool,
) error {
	if !filepath.IsAbs(installDir) || !filepath.IsAbs(dataDir) {
		return fmt.Errorf("uninstall install and data directories must be absolute")
	}
	canonicalInstall, err := canonicalRemovalPath(installDir)
	if err != nil {
		return fmt.Errorf("resolve uninstall directory %s: %w", installDir, err)
	}
	canonicalData, err := canonicalRemovalPath(dataDir)
	if err != nil {
		return fmt.Errorf("resolve data directory %s: %w", dataDir, err)
	}
	mode := model.InstallationModeSystem
	if userInstall {
		mode = model.InstallationModeUser
	}
	expectedInstall, err := canonicalRemovalPath(vfs.InstallDirForMode(mode))
	if err != nil {
		return fmt.Errorf("resolve expected install directory: %w", err)
	}
	if canonicalInstall != expectedInstall {
		return fmt.Errorf("refuse uninstall from unexpected install directory %s", installDir)
	}
	if keepData {
		return nil
	}
	if !userInstall {
		if !PathAllowedForRemoval(canonicalData) {
			return fmt.Errorf("refuse purge of unexpected data directory %s", dataDir)
		}
		return nil
	}
	expectedData, err := canonicalRemovalPath(
		vfs.AgentDataDirForMode(model.InstallationModeUser),
	)
	if err != nil {
		return fmt.Errorf("resolve expected data directory: %w", err)
	}
	if canonicalData != expectedData {
		return fmt.Errorf("refuse purge of unexpected data directory %s", dataDir)
	}
	return nil
}

// canonicalRemovalPath resolves symlinks in parent components but deliberately
// leaves the final component unresolved: rm -rf removes a final symlink rather
// than following it, while an intermediate symlink changes the deletion root.
func canonicalRemovalPath(path string) (string, error) {
	path, err := filepath.Abs(filepath.Clean(strings.TrimSpace(path)))
	if err != nil {
		return "", err
	}
	parent := filepath.Dir(path)
	missing := make([]string, 0, 4)
	for {
		resolved, resolveErr := filepath.EvalSymlinks(parent)
		if resolveErr == nil {
			for index := len(missing) - 1; index >= 0; index-- {
				resolved = filepath.Join(resolved, missing[index])
			}
			return filepath.Join(resolved, filepath.Base(path)), nil
		}
		if !os.IsNotExist(resolveErr) {
			return "", resolveErr
		}
		next := filepath.Dir(parent)
		if next == parent {
			return "", resolveErr
		}
		missing = append(missing, filepath.Base(parent))
		parent = next
	}
}

func scheduleDetachedUninstallUnix(
	installDir, dataDir, logDir string,
	keepData bool,
	userInstall bool,
	completion UninstallCompletion,
) error {
	pendingDir := LifecycleUninstallDir(dataDir)
	if err := os.MkdirAll(pendingDir, 0o750); err != nil {
		return err
	}
	scriptPath := filepath.Join(pendingDir, pendingUninstallRunnerName)
	if err := writeUnixUninstallScript(
		installDir,
		dataDir,
		logDir,
		keepData,
		userInstall,
		completion,
		scriptPath,
	); err != nil {
		if logDir != "" {
			_ = AppendUninstallLog(logDir, fmt.Sprintf("failed to write uninstall script: %v", err))
		}
		return err
	}
	logFn := func(msg string) {
		if logDir != "" {
			_ = AppendUninstallLog(logDir, msg)
		}
	}
	if err := startDetachedShellScript(
		"hfl-agent-uninstall",
		scriptPath,
		userInstall,
		logFn,
	); err != nil {
		return fmt.Errorf("start detached uninstall: %w", err)
	}
	return nil
}

func writeUnixUninstallScript(
	installDir, dataDir, logDir string,
	keepData bool,
	userInstall bool,
	completion UninstallCompletion,
	scriptPath string,
) error {
	keepFlag := "0"
	if keepData {
		keepFlag = "1"
	}
	callbackURL, err := completion.CallbackURL()
	if err != nil {
		return err
	}
	insecureTLSFlag := "0"
	if completion.InsecureTLS {
		insecureTLSFlag = "1"
	}
	forceCleanupFlag := "0"
	if completion.ForceCleanup {
		forceCleanupFlag = "1"
	}
	userInstallFlag := "0"
	userInstallRoot := ""
	unitFile := unixUnitPath
	resourceDropIn := unixResourceDropIn
	launchdPlist := unixLaunchdPlist
	launchdDomain := "system"
	defaultDataRoot := dataDir
	userHome := ""
	if userInstall {
		userInstallFlag = "1"
		var homeErr error
		userHome, homeErr = os.UserHomeDir()
		if homeErr != nil || strings.TrimSpace(userHome) == "" {
			return fmt.Errorf("resolve current user home for uninstall")
		}
		trustedInstallRoot, installErr := vfs.UserInstallDir()
		if installErr != nil || strings.TrimSpace(trustedInstallRoot) == "" {
			return fmt.Errorf("resolve default current-user install directory")
		}
		userInstallRoot, installErr = filepath.Abs(trustedInstallRoot)
		if installErr != nil {
			return fmt.Errorf("resolve default current-user install directory: %w", installErr)
		}
		userInstallRoot = filepath.Clean(userInstallRoot)
		configHome := strings.TrimSpace(os.Getenv("XDG_CONFIG_HOME"))
		if configHome == "" || !filepath.IsAbs(configHome) {
			configHome = filepath.Join(userHome, ".config")
		}
		unitFile = filepath.Join(
			configHome,
			"systemd",
			"user",
			unixServiceUnit,
		)
		resourceDropIn = ""
		launchdPlist = filepath.Join(
			userHome,
			"Library",
			"LaunchAgents",
			unixLaunchdLabel+".plist",
		)
		launchdDomain = fmt.Sprintf("gui/%d", os.Geteuid())
		trustedDataRoot, dataErr := vfs.UserDataDir()
		if dataErr != nil || strings.TrimSpace(trustedDataRoot) == "" {
			return fmt.Errorf("resolve default current-user data directory")
		}
		defaultDataRoot, dataErr = filepath.Abs(trustedDataRoot)
		if dataErr != nil {
			return fmt.Errorf("resolve default current-user data directory: %w", dataErr)
		}
		defaultDataRoot = filepath.Clean(defaultDataRoot)
	}
	logFile := UninstallLogPath(logDir)
	body := fmt.Sprintf(`#!/usr/bin/env bash
set -u
INSTALL_DIR=%q
DATA_DIR=%q
LOG_FILE=%q
KEEP_DATA=%s
USER_INSTALL=%s
USER_INSTALL_ROOT=%q
UNIT_FILE=%q
RESOURCE_DROPIN=%q
SERVICE_NAME=%q
LAUNCHD_PLIST=%q
LAUNCHD_LABEL=%q
LAUNCHD_DOMAIN=%q
DEFAULT_DATA_ROOT=%q
SLEEP_SECONDS=%d
CALLBACK_URL=%q
CALLBACK_TOKEN=%q
CALLBACK_INSECURE_TLS=%s
FORCE_CLEANUP=%s
CLEANUP_FAILED=0
GATEWAY_SIDECAR_FAILED=0
MANAGED_MOUNTS_FAILED=0
AGENT_ARTIFACTS_FAILED=0

mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
umask 022
exec >>"$LOG_FILE" 2>&1

uninstall_ts_utc() { date -u +"%%Y-%%m-%%dT%%H:%%M:%%SZ" 2>/dev/null || date -u; }
log() { echo "$(uninstall_ts_utc) $*"; }
report_uninstall_completion() {
  local rc="$1" complete="true" payload_file
  local failures retained
  local -a failure_items=() retained_items=()
  if [[ "$GATEWAY_SIDECAR_FAILED" -eq 1 ]]; then
    failure_items+=('{"code":"gateway_sidecar_uninstall_failed","detail":"LensNode sidecar cleanup did not complete."}')
    retained_items+=('"lensnode_sidecar"')
  fi
  if [[ "$MANAGED_MOUNTS_FAILED" -eq 1 ]]; then
    failure_items+=('{"code":"managed_mount_cleanup_failed","detail":"One or more Agent-managed NAS mounts could not be unmounted."}')
    retained_items+=('"managed_nas_mounts"')
  fi
  if [[ "$AGENT_ARTIFACTS_FAILED" -eq 1 ]]; then
    failure_items+=('{"code":"agent_uninstall_failed","detail":"Agent service, files, or data remain after cleanup."}')
    retained_items+=('"agent_installation"')
  fi
  if [[ "${#failure_items[@]}" -eq 0 && ( "$rc" -ne 0 || "$CLEANUP_FAILED" -ne 0 ) ]]; then
    failure_items+=('{"code":"detached_uninstall_failed","detail":"Detached uninstall exited before all cleanup steps completed."}')
    retained_items+=('"agent_installation_or_managed_mounts"')
  fi
  # Bash 3.2 with set -u treats expansion of an empty local array as an
  # unbound variable. Preserve valid empty JSON arrays in that case.
  failures="[$(IFS=,; echo "${failure_items[*]-}")]"
  retained="[$(IFS=,; echo "${retained_items[*]-}")]"
  [[ "$rc" -eq 0 && "$CLEANUP_FAILED" -eq 0 ]] || {
    complete="false"
  }
  command -v curl >/dev/null 2>&1 || {
    log "curl not found; uninstall completion callback could not be sent"
    return 0
  }
  payload_file="$(mktemp "${TMPDIR:-/tmp}/hfl-uninstall-completion.XXXXXX")" || return 0
  chmod 600 "$payload_file" 2>/dev/null || true
  printf '{"token":"%%s","cleanup_complete":%%s,"cleanup_failures":%%s,"retained_resources":%%s}\n' \
    "$CALLBACK_TOKEN" "$complete" "$failures" "$retained" >"$payload_file"
  local -a curl_args=(-fsS -X POST -H 'Content-Type: application/json' --data-binary "@$payload_file")
  [[ "$CALLBACK_INSECURE_TLS" == "1" ]] && curl_args+=(-k)
  local attempt
  for attempt in 1 2 3 4 5 6; do
    if curl "${curl_args[@]}" "$CALLBACK_URL" >/dev/null; then
      log "uninstall completion callback accepted cleanup_complete=$complete attempt=$attempt"
      break
    fi
    log "uninstall completion callback failed attempt=$attempt"
    [[ "$attempt" -lt 6 ]] && sleep 10
  done
  rm -f "$payload_file"
}
finish_detached_uninstall() {
  local rc="$?"
  trap - EXIT
  report_uninstall_completion "$rc"
  rm -f -- "$0" 2>/dev/null || true
  exit "$rc"
}
trap finish_detached_uninstall EXIT
%s

hfl_systemctl() {
  if [[ "$USER_INSTALL" == "1" ]]; then
    systemctl --user "$@"
  else
    systemctl "$@"
  fi
}

is_managed_install_path() {
  local path="$1"
  [[ "$USER_INSTALL" == "1" && "$path" == "$USER_INSTALL_ROOT" ]]
}

is_managed_data_path() {
  local path="$1"
  [[ "$USER_INSTALL" == "1" && "$path" == "$DEFAULT_DATA_ROOT" ]]
}

verify_uninstall_artifacts() {
  local failed=0 target
  if [[ "$(uname -s)" == "Darwin" ]]; then
    if launchctl print "$LAUNCHD_DOMAIN/$LAUNCHD_LABEL" >/dev/null 2>&1; then
      log "launchd service remains loaded: $LAUNCHD_LABEL"
      failed=1
    fi
  elif command -v systemctl >/dev/null 2>&1; then
    if hfl_systemctl is-active --quiet "$SERVICE_NAME"; then
      log "systemd service remains active: $SERVICE_NAME"
      failed=1
    fi
    if hfl_systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
      log "systemd service remains enabled: $SERVICE_NAME"
      failed=1
    fi
  fi
  for target in \
    "$INSTALL_DIR/hfl-agent" \
    "$INSTALL_DIR/kopia" \
    "$INSTALL_DIR/run-agent.sh" \
    "$DATA_DIR/INSTALLED_VERSION" \
    "$INSTALL_DIR/install.sh" \
    "$DATA_DIR/MANIFEST.json" \
    "$UNIT_FILE" \
    "$RESOURCE_DROPIN" \
    "$LAUNCHD_PLIST"; do
    if [[ -e "$target" ]]; then
      log "uninstall artifact remains: $target"
      failed=1
    fi
  done
  if [[ "$KEEP_DATA" == "0" && -e "$DATA_DIR" ]]; then
    log "data directory remains after requested purge: $DATA_DIR"
    failed=1
  fi
  return "$failed"
}

log "detached uninstall script started install_dir=$INSTALL_DIR data_dir=$DATA_DIR keep_data=$KEEP_DATA log_file=$LOG_FILE"
sleep "$SLEEP_SECONDS"
if [[ "$KEEP_DATA" == "0" ]]; then
	if ! gateway_workspace_mounts="$(collect_gateway_workspace_mount_points "$DATA_DIR")"; then
		log "could not verify Gateway workspace mounts; refusing purge"
		CLEANUP_FAILED=1
		AGENT_ARTIFACTS_FAILED=1
		exit 1
	fi
	gateway_workspace_mounts="$(printf '%%s\n' "$gateway_workspace_mounts" | sort -u)"
  if [[ -n "$gateway_workspace_mounts" ]]; then
    log "refusing purge while Gateway workspace storage is mounted: ${gateway_workspace_mounts//$'\n'/, }"
    CLEANUP_FAILED=1
    AGENT_ARTIFACTS_FAILED=1
    exit 1
  fi
fi
log "delay elapsed; running gateway sidecar uninstall when applicable"
%s
if ! run_gateway_sidecar_uninstall_if_needed; then
  CLEANUP_FAILED=1
  GATEWAY_SIDECAR_FAILED=1
  if [[ "$FORCE_CLEANUP" == "1" ]]; then
    log "gateway sidecar uninstall failed; Force Cleanup will continue with Agent cleanup"
  else
    AGENT_ARTIFACTS_FAILED=1
    log "gateway sidecar uninstall failed; keeping the Agent installed for retry"
    exit 1
  fi
fi
log "cleaning Agent-managed NAS mounts before stopping the Agent service"
if ! unmount_agent_mounts "$DATA_DIR"; then
  CLEANUP_FAILED=1
  MANAGED_MOUNTS_FAILED=1
  if [[ "$FORCE_CLEANUP" == "1" ]]; then
    log "Agent-managed NAS mount cleanup failed; Force Cleanup will continue with Agent cleanup"
  else
    AGENT_ARTIFACTS_FAILED=1
    log "Agent-managed NAS mount cleanup failed; preserving Agent files and data for manual retry"
    exit 1
  fi
fi
log "delay elapsed; stopping service"

if [[ "$(uname -s)" == "Darwin" ]]; then
  if launchctl print "$LAUNCHD_DOMAIN/$LAUNCHD_LABEL" >/dev/null 2>&1; then
    if launchctl bootout "$LAUNCHD_DOMAIN/$LAUNCHD_LABEL" 2>/dev/null; then
      log "launchctl bootout $LAUNCHD_LABEL succeeded"
    else
      log "launchctl bootout $LAUNCHD_LABEL failed (exit=$?)"
    fi
  else
    log "launchd $LAUNCHD_LABEL not loaded"
  fi
  if [[ -f "$LAUNCHD_PLIST" ]]; then
    if rm -f "$LAUNCHD_PLIST"; then
      log "removed launchd plist $LAUNCHD_PLIST"
    else
      log "failed to remove launchd plist $LAUNCHD_PLIST (exit=$?)"
    fi
  else
    log "launchd plist $LAUNCHD_PLIST not present"
  fi
elif command -v systemctl >/dev/null 2>&1; then
  if hfl_systemctl stop "$SERVICE_NAME" 2>/dev/null; then
    log "systemctl stop $SERVICE_NAME succeeded"
  else
    log "systemctl stop $SERVICE_NAME failed (exit=$?)"
  fi
  if hfl_systemctl disable "$SERVICE_NAME" 2>/dev/null; then
    log "systemctl disable $SERVICE_NAME succeeded"
  else
    log "systemctl disable $SERVICE_NAME failed (exit=$?)"
  fi
else
  log "systemctl not found; skipped service stop/disable"
fi

if [[ "$(uname -s)" != "Darwin" && -f "$RESOURCE_DROPIN" ]]; then
  if rm -f "$RESOURCE_DROPIN"; then
    log "removed gateway resource policy $RESOURCE_DROPIN"
    rmdir "$(dirname "$RESOURCE_DROPIN")" 2>/dev/null || true
  else
    log "failed to remove gateway resource policy $RESOURCE_DROPIN (exit=$?)"
  fi
fi

if [[ "$(uname -s)" != "Darwin" && -f "$UNIT_FILE" ]]; then
  if rm -f "$UNIT_FILE"; then
    log "removed unit file $UNIT_FILE"
  else
    log "failed to remove unit file $UNIT_FILE (exit=$?)"
  fi
  if command -v systemctl >/dev/null 2>&1; then
    hfl_systemctl daemon-reload 2>/dev/null || log "systemctl daemon-reload failed (exit=$?)"
  fi
else
  if [[ "$(uname -s)" != "Darwin" ]]; then
    log "unit file $UNIT_FILE not present"
  fi
fi

if [[ "$KEEP_DATA" == "1" ]]; then
  if [[ ! -x "$INSTALL_DIR/hfl-agent" ]]; then
    log "cannot retire installation identity because $INSTALL_DIR/hfl-agent is unavailable"
    AGENT_ARTIFACTS_FAILED=1
    exit 1
  fi
  if ! HFL_DATA_DIR="$DATA_DIR" \
    "$INSTALL_DIR/hfl-agent" config retire-installation --data-dir "$DATA_DIR"; then
    log "failed to retire installation identity; Agent files and data were preserved for retry"
    AGENT_ARTIFACTS_FAILED=1
    exit 1
  fi
	log "retired installation identity; the existing console record is preserved and the next installation will register a new record"
fi

for target in "$INSTALL_DIR/hfl-agent" "$INSTALL_DIR/kopia" "$INSTALL_DIR/run-agent.sh" "$DATA_DIR/INSTALLED_VERSION" "$INSTALL_DIR/install.sh" "$DATA_DIR/MANIFEST.json"; do
  if [[ -e "$target" ]]; then
    if rm -f "$target"; then
      log "removed $target"
    else
      log "failed to remove $target (exit=$?)"
    fi
  else
    log "install artifact not present: $target"
  fi
done

if [[ "$INSTALL_DIR" == "/opt/hyperfilelens-agent" \
	|| "$INSTALL_DIR" == /opt/hyperfilelens-agent/* \
	|| "$INSTALL_DIR" == "/var/lib/hyperfilelens-agent" \
	|| "$INSTALL_DIR" == /var/lib/hyperfilelens-agent/* \
	|| "$INSTALL_DIR" == "/Library/Application Support/HyperFileLens/Agent" \
	|| "$INSTALL_DIR" == /Library/Application\ Support/HyperFileLens/Agent/* ]] \
  || is_managed_install_path "$INSTALL_DIR"; then
    if [[ -e "$INSTALL_DIR" ]]; then
      if rm -rf "$INSTALL_DIR"; then
        log "removed install directory tree $INSTALL_DIR (including backup artifacts)"
      else
        log "failed to remove install directory tree $INSTALL_DIR (exit=$?)"
      fi
    else
      log "install directory $INSTALL_DIR not present"
    fi
else
    if [[ -d "$INSTALL_DIR/backup" ]]; then
      if rm -rf "$INSTALL_DIR/backup"; then
        log "removed install backup directory $INSTALL_DIR/backup"
      else
        log "failed to remove install backup directory $INSTALL_DIR/backup (exit=$?)"
      fi
    fi
    if rmdir "$INSTALL_DIR" 2>/dev/null; then
      log "removed install directory $INSTALL_DIR"
    else
      log "install directory $INSTALL_DIR not removed (may be non-empty or missing)"
    fi
fi

if [[ "$KEEP_DATA" == "0" ]]; then
if [[ "$DATA_DIR" == "/var/lib/hyperfilelens-agent" \
	|| "$DATA_DIR" == /var/lib/hyperfilelens-agent/* \
	|| "$DATA_DIR" == "/opt/hyperfilelens-agent" \
	|| "$DATA_DIR" == /opt/hyperfilelens-agent/* \
	|| "$DATA_DIR" == "/Library/Application Support/HyperFileLens/Agent" \
	|| "$DATA_DIR" == /Library/Application\ Support/HyperFileLens/Agent/* ]] \
    || is_managed_data_path "$DATA_DIR"; then
      if [[ -e "$DATA_DIR" ]]; then
        if rm -rf "$DATA_DIR"; then
          if [[ -e "$DATA_DIR" ]]; then
            log "data directory $DATA_DIR remains after removal"
            AGENT_ARTIFACTS_FAILED=1
            exit 1
          fi
          log "removed data directory $DATA_DIR"
        else
          log "failed to remove data directory $DATA_DIR (exit=$?)"
          AGENT_ARTIFACTS_FAILED=1
          exit 1
        fi
      else
        log "data directory $DATA_DIR not present"
      fi
  else
      log "data directory $DATA_DIR outside allowed prefixes; skipped removal"
      AGENT_ARTIFACTS_FAILED=1
  fi
  if [[ -f "$DEFAULT_DATA_ROOT/config/agent.env" ]]; then
    if rm -f "$DEFAULT_DATA_ROOT/config/agent.env"; then
      log "removed $DEFAULT_DATA_ROOT/config/agent.env"
    else
      log "failed to remove $DEFAULT_DATA_ROOT/config/agent.env (exit=$?)"
    fi
  fi
else
  log "keep_data=1; preserved data directory $DATA_DIR (uninstall log retained under logs/)"
fi

if ! verify_uninstall_artifacts; then
  CLEANUP_FAILED=1
  AGENT_ARTIFACTS_FAILED=1
  if [[ "$FORCE_CLEANUP" == "1" ]]; then
    log "post-uninstall verification found residue; Force Cleanup will report it and finish"
  else
    log "post-uninstall verification failed; Strict Cleanup remains retryable"
    exit 1
  fi
fi

log "detached uninstall script finished"
`,
		installDir,
		dataDir,
		logFile,
		keepFlag,
		userInstallFlag,
		userInstallRoot,
		unitFile,
		resourceDropIn,
		unixServiceUnit,
		launchdPlist,
		unixLaunchdLabel,
		launchdDomain,
		defaultDataRoot,
		uninstallDelaySecond,
		callbackURL,
		completion.Token,
		insecureTLSFlag,
		forceCleanupFlag,
		unixManagedMountCleanupScript,
		unixGatewaySidecarUninstallHook,
	)
	if err := os.MkdirAll(filepath.Dir(scriptPath), 0o750); err != nil {
		return err
	}
	if err := os.WriteFile(scriptPath, []byte(body), 0o700); err != nil {
		return err
	}
	return nil
}
