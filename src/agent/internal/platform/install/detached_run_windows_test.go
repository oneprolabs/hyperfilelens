//go:build windows

package install

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPsSingleQuote(t *testing.T) {
	got := psSingleQuote(`C:\ProgramData\HyperFileLens\Agent\lifecycle\upgrade\run-upgrade.ps1`)
	want := `'C:\ProgramData\HyperFileLens\Agent\lifecycle\upgrade\run-upgrade.ps1'`
	if got != want {
		t.Fatalf("psSingleQuote() = %q, want %q", got, want)
	}
	got = psSingleQuote(`C:\it's\path.ps1`)
	want = `'C:\it''s\path.ps1'`
	if got != want {
		t.Fatalf("psSingleQuote(escaped) = %q, want %q", got, want)
	}
}

func TestScheduledTaskPrincipalMatchesInstallationMode(t *testing.T) {
	userPrincipal := scheduledTaskPrincipal(true)
	if !strings.Contains(userPrincipal, "-LogonType Interactive") ||
		!strings.Contains(userPrincipal, "-RunLevel Limited") {
		t.Fatalf("current-user task principal is not interactive/limited: %s", userPrincipal)
	}
	systemPrincipal := scheduledTaskPrincipal(false)
	if !strings.Contains(systemPrincipal, "-UserId 'SYSTEM'") ||
		!strings.Contains(systemPrincipal, "-LogonType ServiceAccount") ||
		!strings.Contains(systemPrincipal, "-RunLevel Highest") {
		t.Fatalf("system task principal is not SYSTEM/service-account/highest: %s", systemPrincipal)
	}
}

func TestWindowsTaskRunnerBootstrapStartsTaskAndPreventsDuplicateInstances(t *testing.T) {
	body := windowsTaskRunnerBootstrap(`C:\Temp\run-uninstall.ps1`, true)
	for _, want := range []string{
		"Register-ScheduledTask",
		"Start-ScheduledTask -TaskName $taskName -TaskPath '\\'",
		"-MultipleInstances IgnoreNew",
		"-AllowStartIfOnBatteries",
		"-DontStopIfGoingOnBatteries",
		"HFL_TASK_STARTED",
		"HFL_TASK_START_DEFERRED",
		"HFL_TASK_REGISTRATION_CONFIRMED_AFTER_ERROR",
		"HFL_TASK_STATE_UNKNOWN",
		"HFL_TASK_REFERENCES_SCRIPT",
		"HFL_DETACHED_RUNNER_CONFLICT",
		"Test-HflDetachedRunnerTaskAction $registered",
		"Test-HflDetachedRunnerTaskAction $deferred",
		"$deferredState -notin @('Ready', 'Running', 'Queued')",
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("Windows task runner bootstrap missing %q:\n%s", want, body)
		}
	}
}

func TestWindowsTaskRunnerBootstrapParses(t *testing.T) {
	path := filepath.Join(t.TempDir(), "detached-runner-bootstrap.ps1")
	body := windowsTaskRunnerBootstrap(`C:\Temp\run-uninstall.ps1`, true)
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatalf("write detached runner bootstrap: %v", err)
	}
	assertPowerShellParses(t, path)
}

func TestWindowsDetachedTaskScriptCleanupDecision(t *testing.T) {
	conflict := classifyWindowsTaskRunnerError(errors.New("exit status 1"), "HFL_DETACHED_RUNNER_CONFLICT")
	if ShouldRetainDetachedLifecycleFiles(conflict) {
		t.Fatal("a conflicting task that references another script must not retain the requested script")
	}
	owned := classifyWindowsTaskRunnerError(errors.New("exit status 1"), "HFL_DETACHED_RUNNER_CONFLICT HFL_TASK_REFERENCES_SCRIPT")
	if !ShouldRetainDetachedLifecycleFiles(owned) {
		t.Fatal("a task that references the requested script must retain it")
	}
	unknown := classifyWindowsTaskRunnerError(errors.New("exit status 1"), "HFL_TASK_STATE_UNKNOWN")
	if !ShouldRetainDetachedLifecycleFiles(unknown) {
		t.Fatal("an unknown task state must conservatively retain staged files")
	}
}

func TestWindowsTaskRunnerBootstrapQueriesTaskStateStrictly(t *testing.T) {
	body := windowsTaskRunnerBootstrap(`C:\Temp\run-uninstall.ps1`, true)
	if strings.Contains(body, "Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue") {
		t.Fatalf("task lookup must not treat scheduler errors as an absent task:\n%s", body)
	}
	for _, want := range []string{
		"Get-ScheduledTask -ErrorAction Stop",
		"$_.TaskName -eq $taskName",
		"$_.TaskPath -eq '\\'",
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("strict task lookup missing %q:\n%s", want, body)
		}
	}
	if strings.Contains(body, "Register-ScheduledTask -TaskName $taskName -TaskPath '\\' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force") {
		t.Fatalf("task registration must not force-overwrite a task created after the conflict check:\n%s", body)
	}
}

func TestWindowsTaskRunnerBootstrapRetainsTriggerWhenImmediateStartFails(t *testing.T) {
	body := windowsTaskRunnerBootstrap(`C:\Temp\run-upgrade.ps1`, false)
	registerAt := strings.Index(body, "    Register-ScheduledTask")
	startAt := strings.Index(body, "Start-ScheduledTask -TaskName $taskName -TaskPath '\\'")
	deferredAt := strings.Index(body, "HFL_TASK_START_DEFERRED")
	if registerAt < 0 || startAt <= registerAt || deferredAt <= startAt {
		t.Fatalf("task must be registered before explicit start and preserve its trigger on an inconclusive start:\n%s", body)
	}
}

func TestWindowsTaskRunnerBootstrapRejectsActiveTask(t *testing.T) {
	body := windowsTaskRunnerBootstrap(`C:\Temp\run-upgrade.ps1`, true)
	for _, want := range []string{
		"if ($state -in @('Running', 'Queued'))",
		"if ($state -notin @('Ready', 'Disabled'))",
		"if ($state -eq 'Ready')",
		"Get-ScheduledTaskInfo -TaskName $taskName -TaskPath '\\' -ErrorAction Stop",
		"$taskInfo.LastRunTime.Year -lt 2000",
		"$taskInfo.LastRunTime -gt $staleBefore",
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("unsafe task replacement guard missing %q:\n%s", want, body)
		}
	}
}
