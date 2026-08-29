//go:build windows

package install

import (
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"syscall"

	"golang.org/x/sys/windows"
)

const (
	windowsCreateNewProcessGroup  = 0x00000200
	windowsCreateBreakawayFromJob = 0x01000000
	windowsTaskStaleGraceSeconds  = 30
)

var (
	errWindowsTaskRunnerConflict     = errors.New("detached lifecycle runner conflict")
	errWindowsTaskRunnerStateUnknown = errors.New("detached lifecycle runner state could not be verified")
)

func psSingleQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "''") + "'"
}

// startWindowsDetachedScript launches scriptPath outside the Agent process job.
//
// Task Scheduler is the primary launcher on Windows. A process created by the
// Agent can inherit a kill-on-close Job Object, and CREATE_BREAKAWAY_FROM_JOB
// is not guaranteed to be permitted for every service/user installation. The
// scheduler service is the stable boundary that survives the Agent stopping
// itself for upgrade or uninstall.
func startWindowsDetachedScript(scriptPath string, userInstall bool, logFn func(string)) error {
	scriptPath = strings.TrimSpace(scriptPath)
	if scriptPath == "" {
		return fmt.Errorf("script path required")
	}
	schedulerErr := scheduleWindowsTaskRunner(scriptPath, userInstall, logFn)
	if schedulerErr == nil {
		return nil
	}
	if errors.Is(schedulerErr, errWindowsTaskRunnerConflict) ||
		errors.Is(schedulerErr, errWindowsTaskRunnerStateUnknown) {
		// Never fall back to a second process while a scheduled task may still
		// exist. The fallback could race an existing lifecycle runner.
		return schedulerErr
	} else if logFn != nil {
		logFn(fmt.Sprintf("Task Scheduler launch unavailable; trying detached process: %v", schedulerErr))
	}

	// Start-Process fully detaches from the service process tree (launchd/systemd analogue).
	bootstrap := fmt.Sprintf(
		"Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',%s) -WindowStyle Hidden",
		psSingleQuote(scriptPath),
	)
	cmd := exec.Command(
		"powershell.exe",
		"-NoProfile",
		"-ExecutionPolicy", "Bypass",
		"-WindowStyle", "Hidden",
		"-Command", bootstrap,
	)
	cmd.SysProcAttr = &syscall.SysProcAttr{
		CreationFlags: windowsCreateNewProcessGroup | windowsCreateBreakawayFromJob,
	}
	if err := cmd.Start(); err != nil {
		// A Job Object may reject CREATE_BREAKAWAY_FROM_JOB. The scheduler was
		// already attempted above, so return both errors for actionable logs.
		if errors.Is(err, windows.ERROR_ACCESS_DENIED) || strings.Contains(strings.ToLower(err.Error()), "access is denied") {
			return fmt.Errorf("start detached script launcher: %w; Task Scheduler launch failed: %v", err, schedulerErr)
		}
		return fmt.Errorf("start detached script launcher: %w", err)
	}
	go func() {
		_ = cmd.Wait()
	}()
	if logFn != nil {
		logFn(fmt.Sprintf("started detached runner via Start-Process script=%s", scriptPath))
	}
	return nil
}

// scheduleWindowsTaskRunner registers a temporary task. Unlike a
// process started by the Agent, the Task Scheduler service does not inherit
// the Agent's kill-on-close Job Object, so it survives the Agent restart.
func scheduleWindowsTaskRunner(scriptPath string, userInstall bool, logFn func(string)) error {
	scriptPath = strings.TrimSpace(scriptPath)
	if scriptPath == "" {
		return fmt.Errorf("script path required")
	}
	bootstrap := windowsTaskRunnerBootstrap(scriptPath, userInstall)
	cmd := exec.Command(
		"powershell.exe",
		"-NoProfile",
		"-ExecutionPolicy", "Bypass",
		"-WindowStyle", "Hidden",
		"-Command", bootstrap,
	)
	output, err := cmd.CombinedOutput()
	if err != nil {
		if logFn != nil {
			logFn(fmt.Sprintf("Task Scheduler launch failed: %v (%s)", err, strings.TrimSpace(string(output))))
		}
		return classifyWindowsTaskRunnerError(err, string(output))
	}
	if logFn != nil {
		if strings.Contains(string(output), "HFL_TASK_START_DEFERRED") {
			logFn("registered instance-scoped Task Scheduler lifecycle runner; immediate start was unavailable, retaining the one-shot trigger")
		} else {
			logFn("registered and started instance-scoped one-shot Task Scheduler lifecycle runner")
		}
	}
	return nil
}

func classifyWindowsTaskRunnerError(err error, output string) error {
	detail := strings.TrimSpace(output)
	if strings.Contains(output, "HFL_DETACHED_RUNNER_CONFLICT") {
		if strings.Contains(output, "HFL_TASK_REFERENCES_SCRIPT") {
			return fmt.Errorf("%w: %w: %s", errWindowsTaskRunnerConflict, errDetachedRunnerMayOwnFiles, detail)
		}
		return fmt.Errorf("%w: %s", errWindowsTaskRunnerConflict, detail)
	}
	if strings.Contains(output, "HFL_TASK_STATE_UNKNOWN") {
		return fmt.Errorf("%w: %w: %s", errWindowsTaskRunnerStateUnknown, errDetachedRunnerMayOwnFiles, detail)
	}
	return fmt.Errorf("register one-shot lifecycle task: %w", err)
}

func windowsTaskRunnerBootstrap(scriptPath string, userInstall bool) string {
	userInstallFlag := "$false"
	if userInstall {
		userInstallFlag = "$true"
	}
	principal := scheduledTaskPrincipal(userInstall)
	return fmt.Sprintf(`$userInstall = %s
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$taskName = if ($userInstall) { "HyperFileLensAgent.User.$currentSid.DetachedRunner" } else { 'HyperFileLensAgent.DetachedRunner' }
$script = %s
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " + [char]34 + $script + [char]34
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $actionArgs
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(5)
%s
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$staleBefore = (Get-Date).AddSeconds(-%d)
$conflict = $false
$taskStateUnknown = $false
$taskReferencesScript = $false
function Get-HflDetachedRunnerTask {
  try {
    $matches = @(Get-ScheduledTask -ErrorAction Stop | Where-Object {
      $_.TaskName -eq $taskName -and $_.TaskPath -eq '\'
    })
  }
  catch {
    $script:taskStateUnknown = $true
    throw "HFL_TASK_STATE_UNKNOWN: could not query task ${taskName}: $($_.Exception.Message)"
  }
  if ($matches.Count -gt 1) {
    $script:taskStateUnknown = $true
    throw "HFL_TASK_STATE_UNKNOWN: multiple root tasks named $taskName were returned"
  }
  if ($matches.Count -eq 1) { return $matches[0] }
  return $null
}
function Get-HflDetachedRunnerTaskInfo {
  try {
    return Get-ScheduledTaskInfo -TaskName $taskName -TaskPath '\' -ErrorAction Stop
  }
  catch {
    $script:taskStateUnknown = $true
    throw "HFL_TASK_STATE_UNKNOWN: could not query task information for ${taskName}: $($_.Exception.Message)"
  }
}
function Test-HflDetachedRunnerTaskAction($task) {
  if ($null -eq $task) { return $false }
  $quotedScript = [char]34 + $script + [char]34
  foreach ($taskAction in @($task.Actions)) {
    $arguments = [string]$taskAction.Arguments
    if ($arguments.IndexOf($quotedScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
      return $true
    }
  }
  return $false
}
try {
  $existing = Get-HflDetachedRunnerTask
  if ($null -ne $existing) {
    $taskReferencesScript = Test-HflDetachedRunnerTaskAction $existing
    $state = [string]$existing.State
    if ($state -in @('Running', 'Queued')) {
      $conflict = $true
      throw "HFL_DETACHED_RUNNER_CONFLICT: task $taskName is $state"
    }
    if ($state -notin @('Ready', 'Disabled')) {
      $conflict = $true
      throw "HFL_DETACHED_RUNNER_CONFLICT: task $taskName is $state"
    }
    if ($state -eq 'Ready') {
      # Ready also describes a newly registered task waiting for its trigger.
      # Reclaim it only after Task Scheduler records a completed run outside
      # the startup race window. Disabled tasks cannot start and are safe to
      # replace without relying on prior-run metadata.
      $taskInfo = Get-HflDetachedRunnerTaskInfo
      if ($null -eq $taskInfo.LastRunTime -or $taskInfo.LastRunTime.Year -lt 2000 -or $taskInfo.LastRunTime -gt $staleBefore) {
        $conflict = $true
        throw "HFL_DETACHED_RUNNER_CONFLICT: task $taskName is ready but has not completed a stale run"
      }
    }
    try {
      Unregister-ScheduledTask -TaskName $taskName -TaskPath '\' -Confirm:$false -ErrorAction Stop
    }
    catch {
      $conflict = $true
      throw "HFL_DETACHED_RUNNER_CONFLICT: inactive task $taskName could not be removed: $($_.Exception.Message)"
    }
    if ($null -ne (Get-HflDetachedRunnerTask)) {
      $conflict = $true
      throw "HFL_DETACHED_RUNNER_CONFLICT: inactive task $taskName still exists after removal"
    }
  }
  try {
    Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -ErrorAction Stop | Out-Null
  }
  catch {
    $registrationError = $_
    # A cmdlet failure can be returned after the scheduler accepted the task.
    # Continue with that task instead of starting a second process.
    $registered = Get-HflDetachedRunnerTask
    if ($null -eq $registered) {
      throw $registrationError
    }
    if (-not (Test-HflDetachedRunnerTaskAction $registered)) {
      $conflict = $true
      throw "HFL_DETACHED_RUNNER_CONFLICT: task $taskName does not reference the requested lifecycle script"
    }
    $taskReferencesScript = $true
    Write-Output "HFL_TASK_REGISTRATION_CONFIRMED_AFTER_ERROR: $($registrationError.Exception.Message)"
  }
  # Start explicitly so the runner is not dependent on the Agent remaining
  # alive until the one-shot trigger is observed. If this request is
  # inconclusive, retain the registered trigger instead of launching a second
  # runner through Start-Process.
  try {
    Start-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction Stop
    Write-Output 'HFL_TASK_STARTED'
  }
  catch {
    $startError = $_
    $deferred = Get-HflDetachedRunnerTask
    if ($null -eq $deferred) {
      throw $startError
    }
    if (-not (Test-HflDetachedRunnerTaskAction $deferred)) {
      $conflict = $true
      throw "HFL_DETACHED_RUNNER_CONFLICT: deferred task $taskName does not reference the requested lifecycle script"
    }
    $taskReferencesScript = $true
    $deferredState = [string]$deferred.State
    if ($deferredState -notin @('Ready', 'Running', 'Queued')) {
      $conflict = $true
      throw "HFL_DETACHED_RUNNER_CONFLICT: deferred task $taskName cannot run from state $deferredState"
    }
    Write-Output "HFL_TASK_START_DEFERRED: $($startError.Exception.Message)"
  }
}
catch {
  if ($conflict) { Write-Output 'HFL_DETACHED_RUNNER_CONFLICT' }
  if ($taskStateUnknown) { Write-Output 'HFL_TASK_STATE_UNKNOWN' }
  if ($taskReferencesScript) { Write-Output 'HFL_TASK_REFERENCES_SCRIPT' }
  throw
}
`, userInstallFlag, psSingleQuote(scriptPath), principal, windowsTaskStaleGraceSeconds)
}

func scheduledTaskPrincipal(userInstall bool) string {
	if userInstall {
		return `$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited`
	}
	return `$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest`
}
