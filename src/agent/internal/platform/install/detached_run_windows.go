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
	principal := scheduledTaskPrincipal(userInstall)
	userInstallFlag := "$false"
	if userInstall {
		userInstallFlag = "$true"
	}
	bootstrap := fmt.Sprintf(`$userInstall = %s
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$taskName = if ($userInstall) { "HyperFileLensAgent.User.$currentSid.DetachedRunner" } else { 'HyperFileLensAgent.DetachedRunner' }
$script = %s
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " + [char]34 + $script + [char]34
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $actionArgs
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(5)
%s
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
`, userInstallFlag, psSingleQuote(scriptPath), principal)
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
			logFn(fmt.Sprintf("Task Scheduler fallback registration failed: %v (%s)", err, strings.TrimSpace(string(output))))
		}
		return fmt.Errorf("register one-shot upgrade task: %w", err)
	}
	if logFn != nil {
		logFn("registered instance-scoped one-shot Task Scheduler lifecycle runner")
	}
	return nil
}

func scheduledTaskPrincipal(userInstall bool) string {
	if userInstall {
		return `$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited`
	}
	return `$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest`
}
