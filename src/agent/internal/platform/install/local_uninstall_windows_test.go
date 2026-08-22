//go:build windows

package install

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"testing"
)

func TestWriteWindowsUninstallScriptUsesUninstallLogAndInstallPs1(t *testing.T) {
	dir := t.TempDir()
	dataDir := dir + `/data`
	logDir := dir + `/data/logs`
	path := dir + "/run-uninstall.ps1"
	err := writeWindowsUninstallScript(
		`C:\Program Files\HyperFileLens\Agent`,
		dataDir,
		logDir,
		false,
		false,
		UninstallCompletion{
			APIBaseURL: "https://control.example",
			Path:       "/api/v1/node/agent-uninstall/completion/",
			Token:      "signed-test-token",
		},
		path,
	)
	if err != nil {
		t.Fatalf("writeWindowsUninstallScript: %v", err)
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read script: %v", err)
	}
	body := string(raw)
	for _, want := range []string{
		`$logFile = ` + fmt.Sprintf("%q", UninstallLogPath(logDir)),
		`install.cmd uninstall`,
		`-PurgeAll`,
		`Stop-HflProcessesForUninstall`,
		`Start-Sleep -Seconds 3`,
		`Remove-InstallDirectoryResidue`,
		`removed residual install.cmd`,
		`Confirm-UninstallArtifacts`,
		`Confirm-UninstallArtifacts -InstallDir $install`,
		`Get-Service -Name HyperFileLensAgent`,
		`post-uninstall verify:`,
		`install.cmd uninstall succeeded`,
		`Push-Location $env:TEMP`,
		`Start-DeferredRemove`,
		`ping -n 3 127.0.0.1 >nul & rmdir /s /q "`,
		`Add-CleanupFailure`,
		`Stop-Or-ContinueAfterFailure`,
		`Force Cleanup will continue with the remaining physical cleanup steps`,
		`Report-UninstallCompletion`,
		`cleanup_failures = @($cleanupFailures)`,
		`retained_resources = @($retainedResources)`,
		`foreach ($attempt in 1..6)`,
		`$logEnabled = $true`,
		`if (-not $script:logEnabled)`,
		`Test-SafeAgentDataPath`,
		`Test-PathContainsReparsePoint -Path $full`,
		`[System.IO.FileAttributes]::ReparsePoint`,
		`$allowedRoot.TrimEnd('\') + '\'`,
		`agent_data_cleanup_refused`,
		`outside the approved Agent data directory`,
		`Remove-AgentDataDirectory`,
		`physical cleanup finished; removing data directory`,
		`$script:logEnabled = $false`,
		`Remove-Item -LiteralPath $DataDir -Recurse -Force -ErrorAction Stop`,
		`Add-CleanupFailure -code 'agent_data_cleanup_failed'`,
		`Remove-Item -LiteralPath $PSCommandPath`,
		`"signed-test-token"`,
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("script missing %q:\n%s", want, body)
		}
	}
	if strings.Contains(body, ".install.out") {
		t.Fatalf("script must not reference separate install output log:\n%s", body)
	}
	if strings.Contains(body, `$KeepFlag -eq '0'`) {
		t.Fatalf("general artifact verification must run before final data cleanup:\n%s", body)
	}
	if count := strings.Count(body, `Remove-InstallDirectoryResidue -InstallDir $install`); count != 2 {
		t.Fatalf("install.cmd and install.ps1 must share residue cleanup; got %d calls:\n%s", count, body)
	}
	dataCleanup := substringBetween(
		t,
		body,
		"function Remove-AgentDataDirectory",
		"Log \"detached uninstall script started",
	)
	assertOrdered(t, dataCleanup,
		`Test-SafeAgentDataPath -DataDir $DataDir`,
		`Log "physical cleanup finished; removing data directory $DataDir"`,
		`$script:logEnabled = $false`,
		`Remove-Item -LiteralPath $DataDir -Recurse -Force -ErrorAction Stop`,
	)
	assertOrdered(t, body,
		`Confirm-UninstallArtifacts -InstallDir $install`,
		`Remove-AgentDataDirectory -DataDir $data`,
		`Report-UninstallCompletion`,
	)
	assertPowerShellParses(t, path)
	assertPowerShellDataPathSafety(t, dir, body)
}

func TestWriteWindowsForceCleanupScriptContinuesAfterInstallerFailure(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/run-uninstall.ps1"
	err := writeWindowsUninstallScript(
		`C:\Program Files\HyperFileLens\Agent`,
		dir+`/data`,
		dir+`/data/logs`,
		false,
		false,
		UninstallCompletion{
			APIBaseURL:   "https://control.example",
			Path:         "/api/v1/node/agent-uninstall/completion/",
			Token:        "signed-test-token",
			ForceCleanup: true,
		},
		path,
	)
	if err != nil {
		t.Fatalf("writeWindowsUninstallScript: %v", err)
	}
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read script: %v", err)
	}
	text := string(body)
	for _, want := range []string{
		`$forceCleanup = $true`,
		`install_cmd_uninstall_failed`,
		`Stop-Or-ContinueAfterFailure`,
		`Remove-InstallDirectoryResidue -InstallDir $install`,
		`Confirm-UninstallArtifacts`,
		`Remove-AgentDataDirectory -DataDir $data`,
		`Force Cleanup accepted the recorded uninstall residue`,
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("Force Cleanup script missing %q:\n%s", want, text)
		}
	}
}

func TestWriteWindowsUninstallScriptKeepDataSkipsPurgeAll(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/run-uninstall.ps1"
	err := writeWindowsUninstallScript(
		`C:\Program Files\HyperFileLens\Agent`,
		dir+`/data`,
		dir+`/data/logs`,
		true,
		false,
		UninstallCompletion{
			APIBaseURL: "https://control.example",
			Path:       "/api/v1/node/agent-uninstall/completion/",
			Token:      "signed-test-token",
		},
		path,
	)
	if err != nil {
		t.Fatalf("writeWindowsUninstallScript: %v", err)
	}
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read script: %v", err)
	}
	text := string(body)
	if !strings.Contains(text, `$keep = 1`) {
		t.Fatalf("keep_data script missing keep flag:\n%s", text)
	}
	if !strings.Contains(text, "keep_data=1; preserved data directory") {
		t.Fatalf("keep_data script missing preserve log line:\n%s", text)
	}
	if !strings.Contains(text, `config retire-installation --data-dir $data`) {
		t.Fatalf("keep_data script does not retire installation identity:\n%s", text)
	}
	if !strings.Contains(text, "the existing console record is preserved and the next installation will register a new record") {
		t.Fatalf("keep_data script must preserve the console record during local uninstall:\n%s", text)
	}
	if strings.Contains(text, "remove the old console record") {
		t.Fatalf("keep_data script must not require local uninstall to change the console record:\n%s", text)
	}
	assertOrdered(t, text,
		`config retire-installation --data-dir $data`,
		`if ($keep -eq '0') {`,
		`Remove-AgentDataDirectory -DataDir $data`,
		`keep_data=1; preserved data directory`,
	)
}

func TestWriteWindowsUserUninstallScriptUsesCurrentUserLifecycle(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/run-uninstall.ps1"
	err := writeWindowsUninstallScript(
		`C:\Users\agent\AppData\Local\HyperFileLens\Agent\bin`,
		dir+`/data`,
		dir+`/data/logs`,
		false,
		true,
		UninstallCompletion{
			APIBaseURL: "https://control.example",
			Path:       "/api/v1/node/agent-uninstall/completion/",
			Token:      "signed-test-token",
		},
		path,
	)
	if err != nil {
		t.Fatalf("writeWindowsUninstallScript: %v", err)
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read script: %v", err)
	}
	body := string(raw)
	for _, want := range []string{
		`$userInstall = $true`,
		`Stop-ScheduledTask -TaskName HyperFileLensAgent`,
		`Get-ScheduledTask -TaskName HyperFileLensAgent`,
		`Unregister-ScheduledTask -TaskName HyperFileLensAgent`,
		`Join-Path $env:LOCALAPPDATA 'HyperFileLens\Agent'`,
		`return $full.Equals($expected, [System.StringComparison]::OrdinalIgnoreCase)`,
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("user uninstall script missing %q:\n%s", want, body)
		}
	}
	assertPowerShellParses(t, path)
}

func TestWriteWindowsUserUpgradeScriptStopsScheduledTask(t *testing.T) {
	dir := t.TempDir()
	path := dir + `/run-upgrade.ps1`
	err := writeWindowsUpgradeScript(
		dir+`\install`,
		dir+`\package.zip`,
		dir+`\logs`,
		true,
		path,
	)
	if err != nil {
		t.Fatalf("writeWindowsUpgradeScript: %v", err)
	}
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	text := string(body)
	for _, expected := range []string{
		"$userInstall = $true",
		"Stop-ScheduledTask -TaskName HyperFileLensAgent",
	} {
		if !strings.Contains(text, expected) {
			t.Fatalf("user upgrade script missing %q", expected)
		}
	}
	assertPowerShellParses(t, path)
}

func assertOrdered(t *testing.T, body string, values ...string) {
	t.Helper()
	previous := -1
	for _, value := range values {
		index := strings.LastIndex(body, value)
		if index < 0 {
			t.Fatalf("script missing %q:\n%s", value, body)
		}
		if index <= previous {
			t.Fatalf("script value %q is out of order:\n%s", value, body)
		}
		previous = index
	}
}

func substringBetween(t *testing.T, body, startMarker, endMarker string) string {
	t.Helper()
	start := strings.Index(body, startMarker)
	if start < 0 {
		t.Fatalf("script missing start marker %q:\n%s", startMarker, body)
	}
	end := strings.Index(body[start:], endMarker)
	if end < 0 {
		t.Fatalf("script missing end marker %q:\n%s", endMarker, body)
	}
	return body[start : start+end]
}

func assertPowerShellParses(t *testing.T, path string) {
	t.Helper()
	command := fmt.Sprintf(`
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(%s, [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count -gt 0) {
  $errors | ForEach-Object { [Console]::Error.WriteLine($_.Message) }
  exit 1
}
`, psSingleQuote(path))
	output, err := exec.Command(
		"powershell.exe",
		"-NoProfile",
		"-NonInteractive",
		"-Command",
		command,
	).CombinedOutput()
	if err != nil {
		t.Fatalf("generated uninstall script does not parse: %v\n%s", err, output)
	}
}

func assertPowerShellDataPathSafety(t *testing.T, dir, body string) {
	t.Helper()
	safetyFunction := substringBetween(
		t,
		body,
		"function Test-SafeAgentDataPath",
		"function Remove-AgentDataDirectory",
	)
	testBody := safetyFunction + `
$allowed = Join-Path $env:ProgramData 'HyperFileLens\Agent'
$allowedChild = Join-Path $allowed 'custom'
$vendorRoot = Join-Path $env:ProgramData 'HyperFileLens'
$prefixCollision = Join-Path $env:ProgramData 'HyperFileLens-Other\Agent'
$outside = Join-Path $env:SystemDrive 'Users'
if (-not (Test-SafeAgentDataPath -DataDir $allowed)) { throw 'default Agent data path was rejected' }
if (-not (Test-SafeAgentDataPath -DataDir $allowedChild)) { throw 'Agent data descendant was rejected' }
if (Test-SafeAgentDataPath -DataDir $vendorRoot) { throw 'vendor root was accepted' }
if (Test-SafeAgentDataPath -DataDir $prefixCollision) { throw 'prefix collision was accepted' }
if (Test-SafeAgentDataPath -DataDir $outside) { throw 'outside path was accepted' }
if (Test-SafeAgentDataPath -DataDir '') { throw 'empty path was accepted' }
`
	path := dir + "/test-data-path-safety.ps1"
	if err := os.WriteFile(path, []byte(testBody), 0o644); err != nil {
		t.Fatalf("write data path safety test: %v", err)
	}
	output, err := exec.Command(
		"powershell.exe",
		"-NoProfile",
		"-NonInteractive",
		"-ExecutionPolicy",
		"Bypass",
		"-File",
		path,
	).CombinedOutput()
	if err != nil {
		t.Fatalf("PowerShell data path safety test failed: %v\n%s", err, output)
	}
}
