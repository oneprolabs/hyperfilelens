//go:build windows

package install

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const (
	upgradeDelaySecond       = 5
	windowsUpgradeRunnerName = "run-upgrade.ps1"
)

// ScheduleDetachedUpgrade runs install.ps1 upgrade after a short delay so the agent
// can report task.result before the service stops.
func ScheduleDetachedUpgrade(
	installDir, archivePath, logDir string,
	userInstall bool,
) error {
	installDir = strings.TrimSpace(installDir)
	if installDir == "" {
		installDir = DefaultInstallDir()
	}
	archivePath = strings.TrimSpace(archivePath)
	if archivePath == "" {
		return fmt.Errorf("upgrade archive path required")
	}
	logDir = resolveUpgradeLogDir("", logDir)
	if logDir != "" {
		_ = AppendUpgradeLog(logDir, fmt.Sprintf("scheduled detached upgrade archive=%s", archivePath))
	}
	pendingDir := filepath.Dir(archivePath)
	scriptPath := filepath.Join(pendingDir, windowsUpgradeRunnerName)
	if err := writeWindowsUpgradeScript(
		installDir,
		archivePath,
		logDir,
		userInstall,
		scriptPath,
	); err != nil {
		_ = os.Remove(scriptPath)
		if logDir != "" {
			_ = AppendUpgradeLog(logDir, fmt.Sprintf("failed to write upgrade script: %v", err))
		}
		return err
	}
	logFn := func(msg string) {
		if logDir != "" {
			_ = AppendUpgradeLog(logDir, msg)
		}
	}
	if err := startWindowsDetachedScript(scriptPath, userInstall, logFn); err != nil {
		if !ShouldRetainDetachedLifecycleFiles(err) {
			_ = os.Remove(scriptPath)
		}
		logFn(fmt.Sprintf("failed to start detached upgrade script: %v", err))
		return fmt.Errorf("start detached upgrade: %w", err)
	}
	return nil
}

func writeWindowsUpgradeScript(
	installDir, archivePath, logDir string,
	userInstall bool,
	scriptPath string,
) error {
	installScript := filepath.Join(installDir, "install.ps1")
	logFile := UpgradeLogPath(logDir)
	pendingDir := filepath.Dir(archivePath)
	userInstallFlag := "$false"
	if userInstall {
		userInstallFlag = "$true"
	}
	body := fmt.Sprintf(`$logFile = %q
$install = %q
$archive = %q
$pending = %q
$userInstall = %s
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$runtimeTaskName = if ($userInstall) { "HyperFileLensAgent.User.$currentSid" } else { 'HyperFileLensAgent' }
$scheduledTaskName = if ($userInstall) { "$runtimeTaskName.DetachedRunner" } else { 'HyperFileLensAgent.DetachedRunner' }
$SLEEP_SECONDS = %d

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logFile) | Out-Null
function Log([string]$msg) {
  $ts = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
  Add-Content -LiteralPath $logFile -Value "$ts $msg" -Encoding UTF8
}

Log "detached upgrade script started archive=$archive"
$ErrorActionPreference = 'Continue'
$rc = 1
$upgradeSucceeded = $false
try {
  Start-Sleep -Seconds $SLEEP_SECONDS
  Log "delay elapsed; running upgrade"

  if (-not (Test-Path -LiteralPath $install)) {
    throw "install.ps1 missing at $install"
  }
  if (-not (Test-Path -LiteralPath $archive)) {
    throw "archive missing at $archive"
  }

  Log "running install.ps1 upgrade"
  $upgradeArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $install, 'upgrade', '-From', $archive, '-Yes', '-QuietFooter')
  & powershell.exe @upgradeArgs 2>&1 | ForEach-Object {
    $line = ($_.ToString()).TrimEnd()
    if ($line) { Log "install.ps1> $line" }
  }
  $rc = $LASTEXITCODE
  if ($rc -ne 0) {
    throw "install.ps1 upgrade failed exit=$rc"
  }
  $upgradeSucceeded = $true
  Log "install.ps1 upgrade succeeded"
} catch {
  Log "install.ps1 upgrade failed: $($_.Exception.Message)"
  if ($null -eq $rc -or $rc -eq 0) { $rc = 1 }
} finally {
  Unregister-ScheduledTask -TaskName $scheduledTaskName -TaskPath '\' -Confirm:$false -ErrorAction SilentlyContinue
}

if ($upgradeSucceeded) {
  Log "detached upgrade script finished"
  Remove-Item -Recurse -Force -LiteralPath $pending -ErrorAction SilentlyContinue
  exit 0
}
Set-Content -LiteralPath (Join-Path $pending 'FAILED') -Value 'failed' -Encoding ASCII -ErrorAction SilentlyContinue
exit $rc
`,
		logFile,
		installScript,
		archivePath,
		pendingDir,
		userInstallFlag,
		upgradeDelaySecond,
	)
	if err := os.MkdirAll(filepath.Dir(scriptPath), 0o750); err != nil {
		return err
	}
	// UTF-8 BOM helps Windows PowerShell 5.1 load -File scripts reliably.
	return os.WriteFile(scriptPath, append([]byte{0xEF, 0xBB, 0xBF}, []byte(body)...), 0o644)
}
