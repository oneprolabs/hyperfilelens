package install

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestInstallPs1PurgeDoesNotRecreateDataLogDirectory(t *testing.T) {
	source := readPackagingInstallScript(t)
	for _, want := range []string{
		"if (-not `$dir -or -not (Test-Path -LiteralPath `$dir)) { return }",
		`$uninstallLog = if (-not $PurgeAll -and $uninstallLogPath) { $uninstallLogPath } else { "" }`,
	} {
		if !strings.Contains(source, want) {
			t.Fatalf("install.ps1 missing %q", want)
		}
	}
	if strings.Contains(source, "if (`$dir) { New-Item -ItemType Directory -Force -Path `$dir") {
		t.Fatal("deferred install-root cleanup must not recreate the uninstall log directory")
	}
}

func TestInstallPs1DoesNotRemoveInstallParent(t *testing.T) {
	source := readPackagingInstallScript(t)
	for _, forbidden := range []string{
		"`$parent = Split-Path -Parent `$target",
		"removed empty parent directory `$parent",
	} {
		if strings.Contains(source, forbidden) {
			t.Fatalf("install.ps1 must not remove the shared install parent: found %q", forbidden)
		}
	}
}

func TestInstallPs1SafeDataPathRequiresHyperFileLensDescendant(t *testing.T) {
	source := readPackagingInstallScript(t)
	for _, want := range []string{
		`(Join-Path $env:ProgramData "HyperFileLens\Agent")`,
		`$allowedRoot.TrimEnd('\') + '\'`,
		`Test-HflPathContainsReparsePoint -Path $full`,
		`[System.IO.FileAttributes]::ReparsePoint`,
	} {
		if !strings.Contains(source, want) {
			t.Fatalf("install.ps1 safe data path check missing %q", want)
		}
	}
	if strings.Contains(source, `StartsWith($pd.TrimEnd('\') + '\HyperFileLens'`) {
		t.Fatal("safe data path check must enforce a path-component boundary")
	}
}

func TestInstallPs1UsesFixedUserDataDirectory(t *testing.T) {
	source := readPackagingInstallScript(t)
	for _, want := range []string{
		`$expected = [System.IO.Path]::GetFullPath($DefaultDataRoot)`,
		`User-level installation uses the fixed data directory $DefaultDataRoot; -DataDir is not supported.`,
		`Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_DATA_DIR" -Value $DataRoot`,
		`Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_AGENT_ROOT" -Value $AgentRoot`,
	} {
		if !strings.Contains(source, want) {
			t.Fatalf("install.ps1 missing fixed user data rule %q", want)
		}
	}
}

func TestInstallPs1RestrictsUserModeToSourceAgent(t *testing.T) {
	source := readPackagingInstallScript(t)
	if !strings.Contains(source, `$InstallationMode -ne "system" -and $Role -ne "agent"`) {
		t.Fatal("install.ps1 must reject user-scoped Proxy and Data Gateway installs")
	}
}

func TestInstallPs1UsesFullWindowsIdentityForCurrentUserTask(t *testing.T) {
	source := readPackagingInstallScript(t)
	for _, want := range []string{
		`$CurrentWindowsIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name`,
		`New-ScheduledTaskTrigger -AtLogOn -User $CurrentWindowsIdentity`,
		`New-ScheduledTaskPrincipal`,
		`-LogonType Interactive`,
		`-RunLevel Limited`,
		`-AllowStartIfOnBatteries`,
		`-DontStopIfGoingOnBatteries`,
		`-Principal $principal`,
	} {
		if !strings.Contains(source, want) {
			t.Fatalf("install.ps1 current-user task is missing %q", want)
		}
	}
	if strings.Contains(source, `New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME`) {
		t.Fatal("current-user task must not use an ambiguous short account name")
	}
}

func TestInstallPs1SupportsSpecifiedUserContinuousTask(t *testing.T) {
	source := readPackagingInstallScript(t)
	for _, want := range []string{
		`$InstallationMode -eq "account"`,
		`New-ScheduledTaskTrigger -AtStartup`,
		`-LogonType S4U`,
		`-UserId $RunAsUser`,
		`Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_RUN_AS_USER" -Value $RunAsUser`,
		`Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_RUN_AS_HOME" -Value $RunAsHome`,
		`(Join-Path $machineAgentRoot "config\agent.env")`,
		`Grant-HflDirectoryAccess -Path $mutable -Account $RunAsUser`,
		`Grant-HflDirectoryAccess -Path $logDir -Account $RunAsUser`,
		`$nextCommand = if ($InstallationMode -ne "system")`,
	} {
		if !strings.Contains(source, want) {
			t.Fatalf("install.ps1 missing specified-user continuous task contract %q", want)
		}
	}
}

func TestInstallPs1LegacyMigrationUsesUnifiedStateRoot(t *testing.T) {
	source := readPackagingInstallScript(t)
	if strings.Contains(source, `Join-Path $DefaultDataRoot "state\`) {
		t.Fatal("legacy migration must not create a state wrapper directory")
	}
	for _, want := range []string{
		`function Copy-LegacyBackupTree`,
		`$legacyDb = Join-Path $legacyDataRoot "agent.db"`,
		`$newDb = Join-Path $DataStoreRoot "agent.db"`,
		`$migrationMarker = Join-Path $LifecycleRoot ".legacy-migration"`,
		`if ($entry.Name -eq "legacy") { continue }`,
		`Join-Path $ConfigRoot "agent.env"`,
		`Join-Path $DataStoreRoot $name`,
		`Join-Path $BackupRoot "rollback"`,
		`Join-Path $legacyDataRoot "backup\state"`,
		`foreach ($name in @("agent.env", "agent.db", "agent.db-wal", "agent.db-shm", "config.json", "install.lock"))`,
		`Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_AGENT_ROOT" -Value $AgentRoot`,
		`Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_DATA_DIR" -Value $DataRoot`,
	} {
		if !strings.Contains(source, want) {
			t.Fatalf("install.ps1 legacy migration missing %q", want)
		}
	}
}

func TestInstallPs1RunsCurrentUserAgentThroughWindowlessLauncher(t *testing.T) {
	source := readPackagingInstallScript(t)
	for _, want := range []string{
		`$runner = Join-Path $InstallRoot "hfl-agent-user-launcher.exe"`,
		`-Execute $runner`,
		"-Argument \"-data-dir `\"$DataRoot`\"\"",
		`Copy-Item -Force -Path $srcLauncher -Destination (Join-Path $InstallRoot "hfl-agent-user-launcher.exe")`,
		`Remove-HflInstallFile (Join-Path $InstallRoot "hfl-agent-user-launcher.exe")`,
		`Remove-HflInstallFile (Join-Path $InstallRoot "run-agent.ps1")`,
	} {
		if !strings.Contains(source, want) {
			t.Fatalf("install.ps1 current-user launcher is missing %q", want)
		}
	}
	installServiceStart := strings.Index(source, "function Install-HflService")
	if installServiceStart < 0 {
		t.Fatal("install.ps1 service installer is missing")
	}
	userServiceStart := strings.Index(
		source[installServiceStart:],
		`if ($InstallationMode -eq "user") {`,
	)
	if userServiceStart < 0 {
		t.Fatal("install.ps1 current-user service branch is missing")
	}
	userService := source[installServiceStart+userServiceStart:]
	systemServiceStart := strings.Index(userService, `$binPath = "`)
	if systemServiceStart < 0 {
		t.Fatal("install.ps1 system service branch is missing")
	}
	userService = userService[:systemServiceStart]
	if strings.Contains(userService, "powershell.exe") || strings.Contains(userService, "$powershell") {
		t.Fatal("current-user task must not keep a console PowerShell runner alive")
	}
	if strings.Contains(source, "function Write-HflUserTaskRunner") {
		t.Fatal("legacy PowerShell current-user runner must not be generated")
	}
}

func TestInstallPs1UpgradePersistsMissingInstallationMode(t *testing.T) {
	source := readPackagingInstallScript(t)
	mergeStart := strings.Index(source, "function Merge-AgentEnv")
	if mergeStart < 0 {
		t.Fatal("install.ps1 missing Merge-AgentEnv")
	}
	mergeEnd := strings.Index(source[mergeStart:], "function Ensure-HflLogsDir")
	if mergeEnd < 0 {
		t.Fatal("install.ps1 missing Merge-AgentEnv end marker")
	}
	merge := source[mergeStart : mergeStart+mergeEnd]
	if !strings.Contains(merge, "HFL_INSTALLATION_MODE = $InstallationMode") {
		t.Fatal("Windows upgrade must persist a missing installation mode")
	}
}

func TestInstallPs1ValidatesPurgePathBeforeUninstallLogging(t *testing.T) {
	source := readPackagingInstallScript(t)
	uninstallStart := strings.Index(source, "function Invoke-Uninstall")
	if uninstallStart < 0 {
		t.Fatal("install.ps1 missing Invoke-Uninstall")
	}
	uninstall := source[uninstallStart:]
	validateAt := strings.Index(uninstall, "$PurgeAll -and -not (Test-SafeDataPath $dataRoot)")
	logAt := strings.Index(uninstall, "Start-HflUninstallLog -DataRoot $dataRoot")
	if validateAt < 0 || logAt < 0 || validateAt > logAt {
		t.Fatal("PurgeAll path must be validated before uninstall logging or removal")
	}
}

func TestInstallPs1RetiresIdentityBeforeRemovingAgent(t *testing.T) {
	source := readPackagingInstallScript(t)
	retire := `& $agentBinary config retire-installation --data-dir $dataRoot`
	remove := `Remove-HflInstallFile (Join-Path $InstallRoot "hfl-agent.exe")`
	if !strings.Contains(source, retire) {
		t.Fatalf("install.ps1 missing %q", retire)
	}
	if strings.Index(source, retire) > strings.Index(source, remove) {
		t.Fatal("install.ps1 removes hfl-agent before retiring installation identity")
	}
	if !strings.Contains(source, "the existing console record is preserved and the next installation will register a new record") {
		t.Fatal("install.ps1 does not explain that local uninstall preserves the console record")
	}
	if strings.Contains(source, "remove the old console record") {
		t.Fatal("install.ps1 must not require local uninstall to change the console record")
	}
	if !strings.Contains(source, "-KeepInstallationIdentity") {
		t.Fatal("install.ps1 missing incomplete-install rollback flag")
	}
	if !strings.Contains(source, `(-not $PurgeAll) -and (-not $KeepInstallationIdentity)`) {
		t.Fatal("install.ps1 must skip identity retirement during incomplete-install rollback")
	}
}

func TestInstallPs1UpgradeRollbackUsesModeAwareLifecycle(t *testing.T) {
	source := readPackagingInstallScript(t)
	rollbackStart := strings.LastIndex(source, "Restore-RollbackBinaries")
	if rollbackStart < 0 {
		t.Fatal("install.ps1 missing upgrade rollback")
	}
	rollbackEnd := strings.Index(source[rollbackStart:], "Remove-UpgradeRollback")
	if rollbackEnd < 0 {
		t.Fatal("install.ps1 rollback block has no end marker")
	}
	rollback := source[rollbackStart : rollbackStart+rollbackEnd]
	if !strings.Contains(rollback, "Start-HflServiceOnly") {
		t.Fatal("upgrade rollback must restart the persisted system service or user task")
	}
	if strings.Contains(rollback, "Start-Service -Name $ServiceName") {
		t.Fatal("upgrade rollback must not hard-code the Windows service lifecycle")
	}
}

func readPackagingInstallScript(t *testing.T) string {
	t.Helper()
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve current test file")
	}
	path := filepath.Clean(filepath.Join(
		filepath.Dir(currentFile),
		"..", "..", "..", "packaging", "install", "install.ps1",
	))
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		t.Skipf("packaging source is not available beside the compiled test: %s", path)
	}
	if err != nil {
		t.Fatalf("read install.ps1: %v", err)
	}
	return string(raw)
}
