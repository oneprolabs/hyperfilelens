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

// startWindowsDetachedScript launches scriptPath outside the agent service job object.
func startWindowsDetachedScript(scriptPath string, logFn func(string)) error {
	scriptPath = strings.TrimSpace(scriptPath)
	if scriptPath == "" {
		return fmt.Errorf("script path required")
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
		// A current-user Agent is itself attached to a kill-on-close Job Object.
		// Windows may reject CREATE_BREAKAWAY_FROM_JOB for that task. Register a
		// one-shot Task Scheduler task from a short-lived child instead; the
		// Task Scheduler service then launches the runner outside the Agent job.
		if errors.Is(err, windows.ERROR_ACCESS_DENIED) {
			if logFn != nil {
				logFn("breakaway launch was denied; falling back to a one-shot Task Scheduler runner")
			}
			if fallbackErr := scheduleWindowsTaskRunner(scriptPath, logFn); fallbackErr == nil {
				return nil
			} else {
				return fmt.Errorf("start detached script launcher: %w; Task Scheduler fallback failed: %v", err, fallbackErr)
			}
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

// scheduleWindowsTaskRunner registers a temporary interactive task. Unlike a
// process started by the Agent, the Task Scheduler service does not inherit
// the Agent's kill-on-close Job Object, so it survives the Agent restart.
func scheduleWindowsTaskRunner(scriptPath string, logFn func(string)) error {
	scriptPath = strings.TrimSpace(scriptPath)
	if scriptPath == "" {
		return fmt.Errorf("script path required")
	}
	taskName := "HyperFileLensAgent.DetachedRunner"
	bootstrap := fmt.Sprintf(`$taskName = %s
$script = %s
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " + [char]34 + $script + [char]34
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $actionArgs
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(5)
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
`, psSingleQuote(taskName), psSingleQuote(scriptPath))
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
		logFn(fmt.Sprintf("registered one-shot Task Scheduler upgrade runner task=%s", taskName))
	}
	return nil
}
