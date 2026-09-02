//go:build !windows

package install

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const upgradeDelaySecond = 5

// ScheduleDetachedUpgrade runs install.sh upgrade after a short delay so the agent
// can report task.result before stop_service terminates the process.
func ScheduleDetachedUpgrade(
	archivePath, installerPath, logDir, installationMode, runAsUser, runAsHome string,
	userInstall bool,
) error {
	if archivePath = strings.TrimSpace(archivePath); archivePath == "" {
		return fmt.Errorf("upgrade archive path required")
	}
	installerPath = strings.TrimSpace(installerPath)
	if installerPath == "" {
		return fmt.Errorf("upgrade installer path required")
	}
	logDir = resolveUpgradeLogDir("", logDir)
	if logDir != "" {
		_ = AppendUpgradeLog(logDir, fmt.Sprintf("Scheduled detached upgrade (archive=%s).", archivePath))
	}
	pendingDir := filepath.Dir(archivePath)
	scriptPath := filepath.Join(pendingDir, pendingUpgradeRunnerName)
	if err := writeUnixUpgradeScript(
		archivePath,
		installerPath,
		logDir,
		installationMode,
		runAsUser,
		runAsHome,
		userInstall,
		scriptPath,
	); err != nil {
		if logDir != "" {
			_ = AppendUpgradeLog(logDir, fmt.Sprintf("Failed to write upgrade script: %v.", err))
		}
		return err
	}
	logFn := func(msg string) {
		if logDir != "" {
			_ = AppendUpgradeLog(logDir, msg)
		}
	}
	if err := startDetachedShellScript(
		"hfl-agent-upgrade",
		scriptPath,
		userInstall,
		logFn,
	); err != nil {
		return fmt.Errorf("start detached upgrade: %w", err)
	}
	return nil
}

func writeUnixUpgradeScript(
	archivePath, installerPath, logDir, installationMode, runAsUser, runAsHome string,
	userInstall bool,
	scriptPath string,
) error {
	logFile := UpgradeLogPath(logDir)
	pendingDir := filepath.Dir(archivePath)
	userInstallFlag := "0"
	if userInstall {
		userInstallFlag = "1"
	}
	body := fmt.Sprintf(`#!/usr/bin/env bash
set -u
ARCHIVE=%q
INSTALL_SH=%q
LOG_FILE=%q
PENDING_DIR=%q
INSTALLATION_MODE=%q
RUN_AS_USER=%q
RUN_AS_HOME=%q
USER_INSTALL=%s
SLEEP_SECONDS=%d

mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
umask 022
exec >>"$LOG_FILE" 2>&1

log() {
  local level="$1"
  shift
  local msg="$*"
  case "${msg}" in
  *.|*.?|*!) ;;
  *) msg="${msg}." ;;
  esac
  printf '[%%s] [%%s] %%s\n' "$(date -u +%%Y-%%m-%%dT%%H:%%M:%%S.000Z 2>/dev/null || date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ)" "${level}" "${msg}"
}

hfl_systemctl() {
  if [[ "$USER_INSTALL" == "1" ]]; then
    systemctl --user "$@"
  else
    systemctl "$@"
  fi
}

log "INFO " "Detached upgrade script started (archive=${ARCHIVE})."
sleep "$SLEEP_SECONDS"
log "INFO " "Delay elapsed; running upgrade."
%s

if [[ ! -x "$INSTALL_SH" ]]; then
  log "FAIL " "install.sh is missing or not executable at ${INSTALL_SH}."
  exit 1
fi
if [[ ! -f "$ARCHIVE" ]]; then
  log "FAIL " "Upgrade archive is missing at ${ARCHIVE}."
  exit 1
fi

set +e
HFL_INSTALLATION_MODE="$INSTALLATION_MODE" HFL_RUN_AS_USER="$RUN_AS_USER" \
HFL_RUN_AS_HOME="$RUN_AS_HOME" bash "$INSTALL_SH" upgrade --from "$ARCHIVE" --yes --quiet-footer
rc=$?
set -e
if [[ "$rc" -eq 0 ]]; then
  log " OK  " "Upgrade completed successfully."
  if ! run_gateway_sidecar_upgrade_if_needed; then
    log "FAIL " "Gateway sidecar upgrade failed after agent upgrade."
    echo "failed" > "$PENDING_DIR/FAILED"
    exit 1
  fi
  rm -rf "$PENDING_DIR"
  exit 0
fi
log "FAIL " "Upgrade failed (exit=${rc}). Attempting service recovery."
if command -v systemctl >/dev/null 2>&1; then
  hfl_systemctl start hyperfilelens-agent.service 2>/dev/null || true
  if hfl_systemctl is-active hyperfilelens-agent.service >/dev/null 2>&1; then
    log " OK  " "Agent service recovered after the failed upgrade."
  else
    log "WARN " "Agent service is still inactive after the failed upgrade."
  fi
fi
echo "failed" > "$PENDING_DIR/FAILED"
exit "$rc"
`,
		archivePath,
		installerPath,
		logFile,
		pendingDir,
		installationMode,
		runAsUser,
		runAsHome,
		userInstallFlag,
		upgradeDelaySecond,
		unixGatewaySidecarUpgradeHook,
	)
	if err := os.MkdirAll(filepath.Dir(scriptPath), 0o750); err != nil {
		return err
	}
	return os.WriteFile(scriptPath, []byte(body), 0o700)
}
