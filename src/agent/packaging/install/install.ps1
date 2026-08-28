<#
.SYNOPSIS
  HyperFileLens Agent bundle installer (Windows).

  After install, installers are copied to bin/ while MANIFEST.json and
  INSTALLED_VERSION remain at the Agent Root for local status and rollback.
  Upgrade still requires a fresh release archive (or remote agent.upgrade).
  Run install.cmd (not install.ps1 directly).

.EXAMPLE
  install.cmd -WssUrl 'wss://...' -NodeToken '...'
  install.cmd upgrade -From C:\path\to\package.zip
  install.cmd uninstall -PurgeAll
  install.cmd status
#>
param(
  [Parameter(Position = 0)]
  [ValidateSet("install", "start", "stop", "restart", "status", "upgrade", "uninstall")]
  [string]$Command = "install",
  [string]$WssUrl = "",
  [string]$ApiBase = "",
  [string]$OrgKey = "",
  [string]$NodeToken = "",
  [string]$NodeId = "",
  [string]$DataDir = "",
  [string]$RunAsUser = "",
  [string]$RunAsHome = "",
  [string]$Role = "agent",
  [string]$From = "",
  [switch]$NoService,
  [switch]$NoStart,
  [switch]$PurgeAll,
  [switch]$KeepInstallationIdentity,
  [switch]$AgentOnly,
  [switch]$KopiaOnly,
  [switch]$NoRestart,
  [switch]$Yes,
  [switch]$Help,
  [switch]$QuietFooter
)

$ErrorActionPreference = "Stop"
$BundleRoot = $PSScriptRoot
$userAgentRoot = Join-Path $env:LOCALAPPDATA "HyperFileLens\Agent"
$machineAgentRoot = Join-Path $env:ProgramData "HyperFileLens\Agent"
$legacyInstallRoot = Join-Path $env:ProgramFiles "HyperFileLens\Agent"
$legacyDataRoot = Join-Path $env:ProgramData "HyperFileLens\Agent"
$InstallationMode = if ($env:HFL_INSTALLATION_MODE) {
  $env:HFL_INSTALLATION_MODE.Trim().ToLowerInvariant()
}
elseif ($BundleRoot.TrimEnd('\') -eq (Join-Path $userAgentRoot "bin").TrimEnd('\')) {
  "user"
}
else {
  "system"
}
if (-not $env:HFL_INSTALLATION_MODE -and $BundleRoot.TrimEnd('\') -ne (Join-Path $userAgentRoot "bin").TrimEnd('\')) {
  foreach ($existingEnv in @(
      (Join-Path $machineAgentRoot "config\agent.env"),
      (Join-Path $legacyDataRoot "agent.env")
    )) {
    if (Test-Path -LiteralPath $existingEnv) {
      foreach ($line in Get-Content -LiteralPath $existingEnv) {
        if ($line -match '^HFL_INSTALLATION_MODE=(.+)$') { $InstallationMode = $Matches[1].Trim().ToLowerInvariant() }
        if ($line -match '^HFL_RUN_AS_USER=(.+)$') { $RunAsUser = $Matches[1].Trim() }
        if ($line -match '^HFL_RUN_AS_HOME=(.+)$') { $RunAsHome = $Matches[1].Trim() }
      }
      break
    }
  }
}
if ($InstallationMode -notin @("system", "user", "account")) {
  throw "HFL_INSTALLATION_MODE must be system, user, or account. Linux user_continuous mode is not supported on Windows."
}
$ServiceName = "HyperFileLensAgent"
if ($InstallationMode -eq "user") {
  $AgentRoot = $userAgentRoot
}
else {
  $AgentRoot = $machineAgentRoot
}
$InstallRoot = Join-Path $AgentRoot "bin"
$DefaultDataRoot = $AgentRoot
$ConfigRoot = Join-Path $AgentRoot "config"
$DataStoreRoot = Join-Path $AgentRoot "data"
$LogsRoot = Join-Path $AgentRoot "logs"
$CacheRoot = Join-Path $AgentRoot "cache"
$MountsRoot = Join-Path $AgentRoot "mounts"
$RuntimeRoot = Join-Path $AgentRoot "runtime"
$LifecycleRoot = Join-Path $AgentRoot "lifecycle"
$BackupRoot = Join-Path $AgentRoot "backup"
$InstalledVersionFile = Join-Path $AgentRoot "INSTALLED_VERSION"
$ManifestFile = Join-Path $AgentRoot "MANIFEST.json"
$LifecycleLabel = if ($InstallationMode -eq "user") { "current-user task" } elseif ($InstallationMode -eq "account") { "specified-user task" } else { "Windows service" }
$CurrentWindowsIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$CurrentWindowsSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
# User installations have independent roots and must also have independent
# scheduler identities. The machine-slot account task keeps its compatibility
# name because account and system installations are mutually exclusive.
$TaskName = if ($InstallationMode -eq "user") { "HyperFileLensAgent.User.$CurrentWindowsSid" } else { "HyperFileLensAgent" }
$script:LegacyMigrationRoot = ""
$script:LegacyServiceWasRunning = $false
$script:UpgradeTransactionActive = $false
$script:UpgradeStateSnapshotReady = $false
$script:UpgradeDeploymentStarted = $false
$script:UpgradeStopAttempted = $false
$script:UpgradeLifecycleWasRunning = $false
$script:UpgradeOperationStateCreated = $false
$script:UpgradePreviousVersion = "unknown"
$script:UpgradeTargetVersion = "unknown"
$script:LifecycleLockPath = ""
$script:UpgradeStatePath = ""
if ([string]::IsNullOrWhiteSpace($RunAsUser) -and $env:HFL_RUN_AS_USER) {
  $RunAsUser = $env:HFL_RUN_AS_USER.Trim()
}
if ([string]::IsNullOrWhiteSpace($RunAsHome) -and $env:HFL_RUN_AS_HOME) {
  $RunAsHome = $env:HFL_RUN_AS_HOME.Trim()
}

function Test-HflAdministrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-HflConfigRoot { param([string]$Root = $AgentRoot); return (Join-Path $Root "config") }
function Get-HflDataStoreRoot { param([string]$Root = $AgentRoot); return (Join-Path $Root "data") }
function Get-HflLogsRoot { param([string]$Root = $AgentRoot); return (Join-Path $Root "logs") }
function Get-HflCacheRoot { param([string]$Root = $AgentRoot); return (Join-Path $Root "cache") }
function Get-HflMountsRoot { param([string]$Root = $AgentRoot); return (Join-Path $Root "mounts") }
function Get-HflRuntimeRoot { param([string]$Root = $AgentRoot); return (Join-Path $Root "runtime") }
function Get-HflLifecycleRoot { param([string]$Root = $AgentRoot); return (Join-Path $Root "lifecycle") }
function Get-HflBackupRoot { param([string]$Root = $AgentRoot); return (Join-Path $Root "backup") }
function Get-HflEnvFile { param([string]$Root = $AgentRoot); return (Join-Path (Get-HflConfigRoot $Root) "agent.env") }
function Get-HflConfigFile { param([string]$Root = $AgentRoot); return (Join-Path (Get-HflConfigRoot $Root) "config.json") }

function Ensure-HflAgentLayout {
  param([Parameter(Mandatory = $true)][string]$Root)
  $directories = @(
    (Join-Path $Root "bin"),
    (Join-Path $Root "config"),
    (Join-Path $Root "data"),
    (Join-Path $Root "logs"),
    (Join-Path $Root "cache\repositories"),
    (Join-Path $Root "mounts\repositories"),
    (Join-Path $Root "mounts\sources"),
    (Join-Path $Root "mounts\custom"),
    (Join-Path $Root "runtime\workspace"),
    (Join-Path $Root "runtime\download"),
    (Join-Path $Root "lifecycle\upgrade"),
    (Join-Path $Root "lifecycle\uninstall"),
    (Join-Path $Root "backup\rollback"),
    (Join-Path $Root "backup\legacy")
  )
  New-Item -ItemType Directory -Force -Path $directories | Out-Null
}

function Acquire-HflLifecycleLock {
  param([Parameter(Mandatory = $true)][string]$DataRoot, [Parameter(Mandatory = $true)][string]$Operation)
  $lock = Join-Path (Get-HflLifecycleRoot $DataRoot) "install.lock"
  New-Item -ItemType Directory -Force -Path (Get-HflLifecycleRoot $DataRoot) | Out-Null
  try {
    New-Item -ItemType Directory -Path $lock -ErrorAction Stop | Out-Null
  }
  catch {
    $pidPath = Join-Path $lock "pid"
    $owner = 0
	for ($attempt = 0; $attempt -lt 5 -and -not (Test-Path -LiteralPath $pidPath); $attempt++) {
	  Start-Sleep -Milliseconds 100
	}
    if (Test-Path -LiteralPath $pidPath) { [int]::TryParse((Get-Content -Raw -LiteralPath $pidPath), [ref]$owner) | Out-Null }
	$ownerProcess = if ($owner -gt 0) { Get-Process -Id $owner -ErrorAction SilentlyContinue } else { $null }
    if ($null -ne $ownerProcess) {
	  $recordedStart = ""
	  $startPath = Join-Path $lock "process_started_at_ticks"
	  if (Test-Path -LiteralPath $startPath) { $recordedStart = (Get-Content -Raw -LiteralPath $startPath).Trim() }
	  $actualStart = try { $ownerProcess.StartTime.ToUniversalTime().Ticks.ToString() } catch { "" }
	  if (-not $recordedStart -or -not $actualStart -or $recordedStart -eq $actualStart) {
	    throw "Another Agent lifecycle operation is already running (PID $owner)."
	  }
    }
    Remove-Item -Recurse -Force -LiteralPath $lock -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $lock -ErrorAction Stop | Out-Null
  }
  $script:LifecycleLockPath = $lock
  try {
	$processStart = (Get-Process -Id $PID -ErrorAction Stop).StartTime.ToUniversalTime().Ticks.ToString()
	Set-Content -LiteralPath (Join-Path $lock "process_started_at_ticks") -Value $processStart -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $lock "operation") -Value $Operation -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $lock "started_at") -Value ([DateTime]::UtcNow.ToString('o')) -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $lock "target_version") -Value "unknown" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $lock "target_commit") -Value "unknown" -Encoding ASCII
	# Publish the PID last: seeing pid means all owner identity metadata exists.
	Set-Content -LiteralPath (Join-Path $lock "pid") -Value $PID -Encoding ASCII
  }
  catch {
    Remove-Item -Recurse -Force -LiteralPath $lock -ErrorAction SilentlyContinue
    $script:LifecycleLockPath = ""
    throw
  }
}

function Update-HflLifecycleLockTarget {
  param([Parameter(Mandatory = $true)][string]$Version, [string]$Manifest = $ManifestFile)
  if (-not $script:LifecycleLockPath -or -not (Test-Path -LiteralPath $script:LifecycleLockPath)) { return }
  $commit = "unknown"
  if (Test-Path -LiteralPath $Manifest) {
    try {
      $value = [string](Get-Content -Raw -LiteralPath $Manifest | ConvertFrom-Json).agent_commit
      if (-not [string]::IsNullOrWhiteSpace($value)) { $commit = $value }
    }
    catch { }
  }
  Set-Content -LiteralPath (Join-Path $script:LifecycleLockPath "target_version") -Value $Version -Encoding ASCII
  Set-Content -LiteralPath (Join-Path $script:LifecycleLockPath "target_commit") -Value $commit -Encoding ASCII
}

function Release-HflLifecycleLock {
  if ($script:LifecycleLockPath) {
    Remove-Item -Recurse -Force -LiteralPath $script:LifecycleLockPath -ErrorAction SilentlyContinue
    $script:LifecycleLockPath = ""
  }
}

function Write-HflUpgradeState {
  param([Parameter(Mandatory = $true)][string]$DataRoot, [Parameter(Mandatory = $true)][string]$Phase)
  $statePath = Join-Path (Get-HflLifecycleRoot $DataRoot) "upgrade-state.json"
  $state = [ordered]@{
    phase = $Phase
    operation = "upgrade"
    pid = $PID
    previous_version = $script:UpgradePreviousVersion
    target_version = $script:UpgradeTargetVersion
    installation_mode = $InstallationMode
    lifecycle_was_running = $script:UpgradeLifecycleWasRunning
    state_snapshot_ready = $script:UpgradeStateSnapshotReady
    deployment_started = $script:UpgradeDeploymentStarted
    updated_at = [DateTime]::UtcNow.ToString('o')
  }
  $tempPath = "$statePath.tmp"
  $script:UpgradeStatePath = $statePath
  $state | ConvertTo-Json -Compress | Set-Content -LiteralPath $tempPath -Encoding UTF8
  Move-Item -Force -LiteralPath $tempPath -Destination $statePath
}

function Clear-HflUpgradeState {
  if ($script:UpgradeStatePath) {
    Remove-Item -Force -LiteralPath $script:UpgradeStatePath -ErrorAction SilentlyContinue
    $script:UpgradeStatePath = ""
  }
}

function Assert-HflInstallationIdentity {
  $elevated = Test-HflAdministrator
  if ($InstallationMode -ne "system" -and $Role -ne "agent") {
    throw "User-level installation is only available for Source Agent."
  }
  if ($InstallationMode -eq "user" -and $elevated) {
    throw "User-level installation must run without UAC elevation."
  }
  if ($InstallationMode -in @("system", "account") -and -not $elevated) {
    throw "Administrator privileges are required for system-level installation."
  }
  if ($InstallationMode -eq "account" -and $Command -eq "install") {
    $defaultRunAsUser = if ([string]::IsNullOrWhiteSpace($RunAsUser)) { $CurrentWindowsIdentity } else { $RunAsUser }
    $selectedRunAsUser = Read-Host "Enter the existing Windows account to protect (default: $defaultRunAsUser)"
    $RunAsUser = if ([string]::IsNullOrWhiteSpace($selectedRunAsUser)) { $defaultRunAsUser } else { $selectedRunAsUser.Trim() }
  }
}

function Show-HflUsage {
  @"
Usage: install.cmd [command] [options]

When no command is given, equivalent to: install.cmd install

Commands:
  install       Install agent binaries and configuration
  start         Start HyperFileLensAgent managed startup
  stop          Stop HyperFileLensAgent managed startup
  restart       Stop then start HyperFileLensAgent managed startup
  status        Show installed version, paths, and lifecycle state
  upgrade       In-place upgrade from another release package directory or .zip
  uninstall     Stop managed startup and remove install dir (keeps data dir by default)

Options:
  install:
    -WssUrl URL         WebSocket control plane URL
    -ApiBase URL        HyperFileLens API base URL
    -OrgKey KEY          Organization key
    -NodeToken TOKEN     Node enrollment token
    -NodeId ID           Node ID (usually set after enrollment heartbeat)
    -DataDir PATH        Data directory (default: $DefaultDataRoot)
    -Role ROLE           Node role (default: agent)
    -NoStart             Do not start managed startup after install

  upgrade:
    -From PATH           Path to new package directory or hfl-agent-*.zip (required)
    -Yes                  Non-interactive: continue when target version equals installed version
                          Extracts to DATA_DIR/runtime/workspace, merges missing config/agent.env keys,
                          migrates agent.db schema, overwrites binaries; removes workspace on success

  uninstall:
    -PurgeAll                   Remove Agent Root and config/agent.env
    -KeepInstallationIdentity   Keep agent.env installation identity (incomplete-install rollback)

Install paths:
  $InstallRoot         Binaries and installer scripts
  $DefaultDataRoot     Unified Agent Root (config/, data/, logs/, cache/, mounts/, runtime/, lifecycle/, backup/)
  ${LifecycleLabel}: $ServiceName   Managed startup registration

Examples (cmd.exe):
  install.cmd
  install.cmd install -WssUrl 'wss://console.example/ws/node/agent/' -ApiBase 'https://console.example' -OrgKey 'org_xxx' -NodeToken 'tok_xxx'
  install.cmd start
  install.cmd stop
  install.cmd restart
  install.cmd status
  install.cmd upgrade -From C:\path\to\hfl-agent-0.1.0-windows-amd64.zip
  install.cmd uninstall
  install.cmd uninstall -PurgeAll

Examples (PowerShell, same entry point):
  .\install.cmd status
  .\install.cmd uninstall -PurgeAll

Note: install.ps1 is invoked internally by install.cmd. Do not run install.ps1 directly
(PowerShell execution policy and file association may block or open it in an editor).
"@
}

if ($Help) {
  Show-HflUsage
  exit 0
}

$HflDivider = "----------------------------------------"
$script:HflInstallLogPath = $null
$script:HflUninstallLogPath = $null

function Write-HflInstallLogLine {
  param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Line)
  $entry = $Line
  if ($entry -notmatch '^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\] ') {
    $entry = "[$([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ'))] [INFO ] $entry"
  }
  foreach ($path in @($script:HflInstallLogPath, $script:HflUninstallLogPath)) {
    if (-not $path) { continue }
    try {
      Add-Content -LiteralPath $path -Value $entry -Encoding UTF8 -ErrorAction Stop
    }
    catch {
      # Installer logging is diagnostic and must never replace the real failure.
    }
  }
}

function Write-HflDisplayLine {
  param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Line)
  Write-Host $Line
  Write-HflInstallLogLine $Line
}

function Write-HflDetailLine {
  param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Line)
  $ts = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
  Write-HflInstallLogLine "[$ts] [DETAIL] $Line"
}

function Start-HflInstallLog {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
  Ensure-HflLogsDir -DataRoot $DataRoot
  $script:HflInstallLogPath = Join-Path (Get-HflLogsRoot $DataRoot) "install.log"
  $ts = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
  Write-HflInstallLogLine "[$ts] [INFO ] Install session started."
}

function Stop-HflInstallLog {
  param([int]$ExitCode = 0)
  if (-not $script:HflInstallLogPath) { return }
  $ts = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
  if ($ExitCode -eq 0) {
    Write-HflInstallLogLine "[$ts] [INFO ] Install session finished successfully."
  }
  else {
    Write-HflInstallLogLine "[$ts] [WARN ] Install session finished with errors (exit=$ExitCode)."
  }
  $script:HflInstallLogPath = $null
}

function Start-HflUninstallLog {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
  Ensure-HflLogsDir -DataRoot $DataRoot
  $script:HflUninstallLogPath = Join-Path (Get-HflLogsRoot $DataRoot) "uninstall.log"
  $ts = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
  Write-HflInstallLogLine "[$ts] [INFO ] Uninstall session started."
}

function Stop-HflUninstallLog {
  param([int]$ExitCode = 0)
  if (-not $script:HflUninstallLogPath) { return }
  $ts = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
  if ($ExitCode -eq 0) {
    Write-HflInstallLogLine "[$ts] [INFO ] Uninstall session finished successfully."
  }
  else {
    Write-HflInstallLogLine "[$ts] [WARN ] Uninstall session finished with errors (exit=$ExitCode)."
  }
  $script:HflUninstallLogPath = $null
}

function Get-HflRoleDisplayName {
  param([string]$Value)
  switch ($Value.Trim().ToLowerInvariant()) {
    'proxy' { return 'Proxy Host' }
    'gateway' { return 'Data Gateway' }
    default { return 'Source Host' }
  }
}

function Write-HflBanner {
  param(
    [Parameter(Mandatory = $true)][string]$Title,
    [string]$RoleName = ""
  )
  if ($QuietFooter) { return }
  if ($Title -notmatch '^(install|upgrade|uninstall)') {
    Write-Host "HyperFileLens Agent - $Title"
    return
  }
  if (-not $RoleName) { $RoleName = Get-HflRoleDisplayName -Value $Role }
  $operation = switch -Regex ($Title) {
    '^install' { 'Installer'; break }
    '^upgrade' { 'Upgrade'; break }
    '^uninstall' { 'Uninstaller'; break }
    default { $Title }
  }
  $banner = @'
 _   _                       _____ _ _      _
| | | |_   _ _ __   ___ _ _|  ___(_) | ___| |    ___ _ __  ___
| |_| | | | | '_ \ / _ \ '__| |_  | | |/ _ \ |   / _ \ '_ \/ __|
|  _  | |_| | |_) |  __/ |  |  _| | | |  __/ |__|  __/ | | \__ \
|_| |_|\__, | .__/ \___|_|  |_|   |_|_|\___|_____\___|_| |_|___/
       |___/|_|                     INSTALLER
'@
  foreach ($line in ($banner -split "`r?`n")) {
    Write-HflDisplayLine $line
  }
  Write-Host ""
  Write-HflDisplayLine "HyperFileLens $RoleName $operation"
  Write-HflDisplayLine ("-" * 64)
}

function Write-HflSection {
  param([Parameter(Mandatory = $true)][string]$Title)
  if ($QuietFooter) { return }
  Write-Host ""
  Write-HflDisplayLine $Title
}

function Get-HflLogTimestamp {
  return [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
}

function Format-HflSentence {
  param([Parameter(Mandatory = $true)][string]$Message)
  $Message = $Message.Trim()
  if ($Message -match '[.!?]$') { return $Message }
  return "$Message."
}

function Write-HflLog {
  param(
    [Parameter(Mandatory = $true)][ValidateSet('INFO ', ' OK  ', 'WARN ', 'FAIL ', 'STEP ', 'SKIP ')][string]$Level,
    [Parameter(Mandatory = $true)][string]$Message
  )
  $messageText = Format-HflSentence $Message
  $line = "[$(Get-HflLogTimestamp)] [$Level] $messageText"
  $status = switch ($Level.Trim()) {
    'OK' { ' OK '; break }
    'WARN' { 'WARN'; break }
    'FAIL' { 'FAIL'; break }
    'STEP' { '....'; break }
    'SKIP' { 'SKIP'; break }
    default { 'INFO' }
  }
  $displayLine = "  [$status] $messageText"
  # QuietFooter suppresses duplicate inner lifecycle output, but the outer
  # enrollment command still needs the concrete failure reason.
  if ((-not $QuietFooter) -or ($Level -eq 'FAIL ')) {
    if ($Level -eq 'WARN ') {
      Write-Host $displayLine -ForegroundColor Yellow
    }
    elseif ($Level -eq 'FAIL ') {
      Write-Host $displayLine -ForegroundColor Red
    }
    else {
      Write-Host $displayLine
    }
  }
  Write-HflInstallLogLine $line
}

function Write-HflOk {
  param([Parameter(Mandatory = $true)][string]$Message)
  Write-HflLog -Level ' OK  ' -Message $Message
}
function Write-HflSkip {
  param([Parameter(Mandatory = $true)][string]$Message)
  Write-HflLog -Level 'SKIP ' -Message $Message
}
function Write-HflWarn {
  param([Parameter(Mandatory = $true)][string]$Message)
  Write-HflLog -Level 'WARN ' -Message $Message
}

function Write-HflSummaryLine {
  param(
    [Parameter(Mandatory = $true)][string]$Key,
    [Parameter(Mandatory = $true)][string]$Value
  )
  $message = "${Key}: ${Value}"
  if (-not $QuietFooter) {
    Write-Host ("  {0,-13} {1}" -f $Key, $Value)
  }
  Write-HflInstallLogLine "[$(Get-HflLogTimestamp)] [INFO ] $message"
}

function Write-HflFooter {
  param(
    [Parameter(Mandatory = $true)][ValidateSet('install', 'upgrade', 'uninstall', 'status')][string]$Outcome
  )
  if ($QuietFooter) { return }
  Write-Host ""
  if ($Outcome -ne 'status') {
    Write-HflDisplayLine ("=" * 64)
  }
  switch ($Outcome) {
    'install' {
      Write-HflDisplayLine "Installation completed successfully"
      Write-HflDisplayLine ("=" * 64)
      if ($NoStart) {
        Write-HflDisplayLine "  Installation files deployed on this host."
        Write-HflDisplayLine "  Complete enrollment (register node and start service) to finish setup."
      }
      elseif ($NoService) {
        Write-HflDisplayLine "  HyperFileLens Agent installed successfully."
        Write-Host ""
        Write-HflDisplayLine "  Return to the HyperFileLens console to add backup sources,"
        Write-HflDisplayLine "  configure policies, and run backup jobs."
        if ($ApiBase) {
          Write-Host ""
          Write-HflDisplayLine "  Console: $($ApiBase.TrimEnd('/'))"
        }
      }
      else {
        Write-HflDisplayLine "  HyperFileLens Agent installed successfully."
        Write-Host ""
        Write-HflDisplayLine "  Return to the HyperFileLens console to add backup sources,"
        Write-HflDisplayLine "  configure policies, and run backup jobs."
        if ($ApiBase) {
          Write-Host ""
          Write-HflDisplayLine "  Console: $($ApiBase.TrimEnd('/'))"
        }
      }
    }
    'upgrade' {
      Write-HflDisplayLine ($(if ($NoRestart) { "Upgrade staged; restart and verification are pending" } else { "Upgrade completed successfully" }))
      Write-HflDisplayLine ("=" * 64)
      Write-HflDisplayLine ($(if ($NoRestart) { "  Rollback data is retained until local health verification completes." } else { "  HyperFileLens Agent upgraded successfully." }))
      if ($ApiBase) {
        Write-Host ""
        Write-HflDisplayLine "  Console: $($ApiBase.TrimEnd('/'))"
      }
    }
    'uninstall' {
      Write-HflDisplayLine "Uninstallation completed successfully"
      Write-HflDisplayLine ("=" * 64)
      Write-HflDisplayLine "  HyperFileLens Agent removed from this host."
      Write-HflDisplayLine "  The local uninstall does not change the console record."
    }
    'status' {
      Write-HflDisplayLine "Done."
    }
  }
  Write-Host ""
}

function Write-HflBundlePreflight {
  if (-not (Test-InstalledScriptLocation)) {
    Write-HflSummaryLine "bundle dir" $BundleRoot
  }
}

function Get-HflServiceStatusLine {
  if ($InstallationMode -ne "system") {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) { return "not installed" }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $info) { return $task.State.ToString().ToLowerInvariant() }
    $label = if ($InstallationMode -eq "account") { "specified-user task ($RunAsUser)" } else { "current-user task" }
    return "$($task.State.ToString().ToLowerInvariant()) ($label)"
  }
  $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
  if ($null -eq $svc) { return "not installed" }
  $startType = $svc.StartType.ToString().ToLower()
  return "$($svc.Status.ToString().ToLower()) ($startType)"
}

function Get-HflSupportedArchitecture {
  $nativeArch = if ($env:PROCESSOR_ARCHITEW6432) {
    $env:PROCESSOR_ARCHITEW6432
  }
  else {
    $env:PROCESSOR_ARCHITECTURE
  }
  switch ($nativeArch) {
    "AMD64" { return "amd64" }
    "ARM64" { throw "Windows ARM64 is not supported by this release." }
    "x86" { throw "32-bit Windows is not supported by this release." }
    default { throw "Unsupported Windows architecture: $nativeArch" }
  }
}

function Read-HflEnvValue {
  param(
    [Parameter(Mandatory = $true)][string]$EnvFile,
    [Parameter(Mandatory = $true)][string]$Key
  )
  if (-not (Test-Path -LiteralPath $EnvFile)) { return "" }
  foreach ($line in Get-Content -LiteralPath $EnvFile) {
    if ($line -match "^\s*$([regex]::Escape($Key))=(.+)$") {
      return $Matches[1].Trim()
    }
  }
  return ""
}

function Test-BundleLayout {
  return (Test-Path -LiteralPath (Join-Path $BundleRoot "bin\hfl-agent.exe")) -and
    (Test-Path -LiteralPath (Join-Path $BundleRoot "bin\hfl-agent-user-launcher.exe")) -and
    (Test-Path -LiteralPath (Join-Path $BundleRoot "bin\kopia.exe"))
}

function Test-InstalledScriptLocation {
  $bundle = try { [System.IO.Path]::GetFullPath($BundleRoot) } catch { $BundleRoot }
  $install = try { [System.IO.Path]::GetFullPath($InstallRoot) } catch { $InstallRoot }
  return ($bundle -eq $install)
}

function Get-BundleVersionFrom {
  param([Parameter(Mandatory = $true)][string]$Root)
  $manifest = Join-Path $Root "MANIFEST.json"
  if (-not (Test-Path -LiteralPath $manifest)) {
    $manifest = $ManifestFile
  }
  if (Test-Path -LiteralPath $manifest) {
    return (Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json).agent_version
  }
  return "unknown"
}

function Test-AgentPackageRoot {
  param([Parameter(Mandatory = $true)][string]$Root)
  return (Test-Path -LiteralPath (Join-Path $Root "MANIFEST.json")) -and
    (Test-Path -LiteralPath (Join-Path $Root "install.ps1")) -and
    (Test-Path -LiteralPath (Join-Path $Root "bin\hfl-agent.exe")) -and
    (Test-Path -LiteralPath (Join-Path $Root "bin\hfl-agent-user-launcher.exe"))
}

function Compare-HflVersion {
  param([string]$Left, [string]$Right)
  $leftText = if ($null -eq $Left) { "" } else { $Left.Trim() }
  $rightText = if ($null -eq $Right) { "" } else { $Right.Trim() }
  $leftText = $leftText.TrimStart('v')
  $rightText = $rightText.TrimStart('v')
  if ($leftText -match '^main-[0-9a-f]{7}$' -or $rightText -match '^main-[0-9a-f]{7}$') {
    if ($leftText -eq $rightText) { return 0 }
    return $null
  }
  $leftParts = $leftText -split '\.'
  $rightParts = $rightText -split '\.'
  if ($leftParts.Count -ne 3 -or $rightParts.Count -ne 3 -or
      ($leftParts | Where-Object { $_ -notmatch '^\d+$' }) -or
      ($rightParts | Where-Object { $_ -notmatch '^\d+$' })) { return $null }
  for ($i = 0; $i -lt 3; $i++) {
    $leftNumber = [int]$leftParts[$i]
    $rightNumber = [int]$rightParts[$i]
    if ($leftNumber -lt $rightNumber) { return -1 }
    if ($leftNumber -gt $rightNumber) { return 1 }
  }
  return 0
}

function Confirm-HflSameVersionUpgrade {
  param([Parameter(Mandatory = $true)][string]$Version)
  if ($Yes) {
    Write-HflWarn "new package version matches current ($Version); continuing upgrade (-Yes)"
    return
  }
  if ([Environment]::UserInteractive -and -not $QuietFooter) {
    $answer = Read-Host "Package version is already $Version. Continue upgrade? [y/N]"
    if ($answer -match '^(?i:y|yes)$') { return }
  }
  throw "Same-version upgrade requires interactive confirmation or -Yes."
}

function Verify-HflUpgradePackage {
  param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$RoleName,
    [Parameter(Mandatory = $true)][string]$Version
  )
  $targetVerifier = Join-Path $Root "bin\hfl-agent.exe"
  if (-not (Test-Path -LiteralPath $targetVerifier)) { throw "Upgrade package verifier is missing: $targetVerifier" }
  $verifier = $targetVerifier
  $installedVerifier = Join-Path $InstallRoot "hfl-agent.exe"
  if (Test-Path -LiteralPath $installedVerifier) {
    $installedHelp = (& $installedVerifier help 2>&1 | Out-String)
    if ($LASTEXITCODE -eq 0 -and $installedHelp -match 'package verify') {
      # Once this verifier is installed, use the trusted current Agent for all
      # later upgrades. The target binary is only the compatibility bridge from
      # releases that predate the package-verify command.
      $verifier = $installedVerifier
    }
  }
  $verifyOutput = (& $verifier package verify --root $Root --role $RoleName --version $Version 2>&1 | Out-String).Trim()
  if ($LASTEXITCODE -ne 0) {
    $detail = if ($verifyOutput) { ": $verifyOutput" } else { "" }
    throw "Upgrade package manifest and checksum validation failed$detail"
  }
  Write-HflOk "upgrade package manifest and checksums verified"
}

function Get-UpgradeWorkspace {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
  return Join-Path (Get-HflRuntimeRoot $DataRoot) "workspace"
}

function Remove-UpgradeWorkspace {
  param([Parameter(Mandatory = $true)][string]$Workspace)
  if (Test-Path -LiteralPath $Workspace) {
    Remove-Item -Recurse -Force -LiteralPath $Workspace
    Write-HflOk "removed $Workspace"
  }
}

function Resolve-UpgradeSource {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$DataRoot
  )
  $workspace = Get-UpgradeWorkspace -DataRoot $DataRoot
  if (Test-Path -LiteralPath $Path -PathType Container) {
    $resolved = try { [System.IO.Path]::GetFullPath($Path) } catch { $Path }
    if (-not (Test-AgentPackageRoot $resolved)) {
      throw "invalid agent package layout: $resolved"
    }
    return $resolved
  }
  if (Test-Path -LiteralPath $Path -PathType Leaf) {
    Remove-UpgradeWorkspace -Workspace $workspace
    New-Item -ItemType Directory -Force -Path $workspace | Out-Null
    Write-HflOk "extracting $Path -> $workspace"
    Expand-Archive -LiteralPath $Path -DestinationPath $workspace -Force
    $inner = Get-ChildItem -LiteralPath $workspace -Directory | Select-Object -First 1
    if (-not $inner -or -not (Test-AgentPackageRoot $inner.FullName)) {
      throw "invalid agent package layout under $workspace"
    }
    return $inner.FullName
  }
  throw "upgrade -From must be a directory or hfl-agent-*.zip: $Path"
}

function Backup-AgentConfigAndDb {
  param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [string]$PreviousVersion = "unknown",
    [Parameter(Mandatory = $true)][string]$SrcRoot
  )
  $stateDir = Join-Path (Get-HflBackupRoot $DataRoot) "rollback"
	$archive = Join-Path $stateDir "latest.zip"
	$meta = Join-Path $stateDir "meta.json"
	$names = @(
	  @{ Source = (Join-Path (Get-HflConfigRoot $DataRoot) "agent.env"); Archive = "config\agent.env" },
	  @{ Source = (Join-Path (Get-HflConfigRoot $DataRoot) "config.json"); Archive = "config\config.json" }
	)
	$items = @()
	foreach ($entry in $names) {
		if (Test-Path -LiteralPath $entry.Source) { $items += $entry }
  }
  New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
	$sourceDatabase = Join-Path (Get-HflDataStoreRoot $DataRoot) "agent.db"
	  if ($items.Count -eq 0 -and -not (Test-Path -LiteralPath $sourceDatabase)) { throw "Upgrade state snapshot could not be created: no configuration or database state was found." }
  $tempDir = Join-Path $env:TEMP ("hfl-agent-backup-{0}" -f ([Guid]::NewGuid().ToString('N')))
  New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
	  try {
	    foreach ($entry in $items) {
	      $destination = Join-Path $tempDir $entry.Archive
	      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
			Copy-Item -LiteralPath $entry.Source -Destination $destination -Force
	    }
	if (Test-Path -LiteralPath $sourceDatabase) {
	  $snapshotDatabase = Join-Path $tempDir "data\agent.db"
	  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $snapshotDatabase) | Out-Null
	  $backupAgent = Join-Path $SrcRoot "bin\hfl-agent.exe"
	  & $backupAgent database backup --source $sourceDatabase --destination $snapshotDatabase
	  if ($LASTEXITCODE -ne 0) { throw "consistent SQLite backup failed (exit $LASTEXITCODE)" }
	}
    Compress-Archive -Path (Join-Path $tempDir '*') -DestinationPath $archive -Force
    Write-HflOk "backed up Agent configuration and consistent SQLite state -> $archive"
    $createdAt = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    [ordered]@{
      created_at = $createdAt
      previous_version = $PreviousVersion
      installation_mode = $InstallationMode
      service_name = $ServiceName
      state_archive = "backup/rollback/latest.zip"
    } | ConvertTo-Json | Set-Content -LiteralPath $meta -Encoding UTF8
    Write-HflOk "wrote $meta"
  }
	  catch { throw "Upgrade state snapshot failed: $($_.Exception.Message)" }
  finally {
    Remove-Item -Recurse -Force -LiteralPath $tempDir -ErrorAction SilentlyContinue
  }
}

$script:UpgradeBinBackup = ""

function Backup-RollbackServiceDefinition {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
  $serviceDir = Join-Path (Join-Path (Get-HflBackupRoot $DataRoot) "rollback") "service"
  New-Item -ItemType Directory -Force -Path $serviceDir | Out-Null
  if ($InstallationMode -eq "system") {
    $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
    if ($null -eq $service) { New-Item -ItemType File -Force -Path (Join-Path $serviceDir "service.absent") | Out-Null }
    else { $service | Select-Object Name,PathName,StartMode,StartName,DisplayName,Description | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $serviceDir "service.json") -Encoding UTF8 }
  }
  elseif (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Export-ScheduledTask -TaskName $TaskName | Set-Content -LiteralPath (Join-Path $serviceDir "task.xml") -Encoding UTF8
  }
  else { New-Item -ItemType File -Force -Path (Join-Path $serviceDir "task.absent") | Out-Null }
}

function Backup-RollbackBinaries {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
	$script:UpgradeBinBackup = Join-Path (Get-HflBackupRoot $DataRoot) "rollback\bin"
	$rollbackRoot = Join-Path (Get-HflBackupRoot $DataRoot) "rollback"
  if (Test-Path -LiteralPath $rollbackRoot) {
    Remove-Item -Recurse -Force -LiteralPath $rollbackRoot
  }
  New-Item -ItemType Directory -Force -Path $script:UpgradeBinBackup | Out-Null
	foreach ($name in @("hfl-agent.exe", "hfl-agent-user-launcher.exe", "kopia.exe", "install.ps1", "install.cmd", "uninstall.cmd", "run-agent.ps1")) {
		$src = Join-Path $InstallRoot $name
    if (Test-Path -LiteralPath $src) {
      Copy-Item -LiteralPath $src -Destination (Join-Path $script:UpgradeBinBackup $name) -Force
	}
	  }
	foreach ($path in @($ManifestFile, $InstalledVersionFile)) {
		if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination (Join-Path $script:UpgradeBinBackup (Split-Path -Leaf $path)) -Force }
	}
	Backup-RollbackServiceDefinition -DataRoot $DataRoot
  Write-HflOk "backed up binaries -> $($script:UpgradeBinBackup)"
}

function Restore-RollbackBinaries {
  if ([string]::IsNullOrWhiteSpace($script:UpgradeBinBackup) -or -not (Test-Path -LiteralPath $script:UpgradeBinBackup)) {
    return
  }
	foreach ($name in @("hfl-agent.exe", "hfl-agent-user-launcher.exe", "kopia.exe", "install.ps1", "install.cmd", "uninstall.cmd", "run-agent.ps1")) {
	    $src = Join-Path $script:UpgradeBinBackup $name
	    $destination = Join-Path $InstallRoot $name
	    if (Test-Path -LiteralPath $src) {
	      if ($name -eq "install.ps1" -and $MyInvocation.PSCommandPath -and ((Get-FullPathOrSelf $destination) -eq (Get-FullPathOrSelf $MyInvocation.PSCommandPath))) {
	        Copy-Item -LiteralPath $src -Destination "$destination.pending" -Force
	        Register-DeferredFileMove -Source "$destination.pending" -Destination $destination
	      }
	      elseif ($name -eq "install.cmd") {
	        Copy-Item -LiteralPath $src -Destination "$destination.pending" -Force
	        Register-DeferredFileMove -Source "$destination.pending" -Destination $destination
	      }
	      else { Copy-HflFileAtomically -Source $src -Destination $destination }
	    }
	    elseif ($name -notin @("install.ps1", "install.cmd")) {
	      Remove-Item -Force -LiteralPath $destination -ErrorAction SilentlyContinue
	    }
	}
	foreach ($entry in @(@{ Name = "MANIFEST.json"; Target = $ManifestFile }, @{ Name = "INSTALLED_VERSION"; Target = $InstalledVersionFile })) {
		$src = Join-Path $script:UpgradeBinBackup $entry.Name
		if (Test-Path -LiteralPath $src) { Copy-HflFileAtomically -Source $src -Destination $entry.Target }
	}
  Write-HflWarn "restored binaries from $($script:UpgradeBinBackup)"
}

function Restore-AgentState {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
  $archive = Join-Path (Join-Path (Get-HflBackupRoot $DataRoot) "rollback") "latest.zip"
  if (-not (Test-Path -LiteralPath $archive)) { throw "rollback state archive is missing: $archive" }
  $temp = Join-Path $env:TEMP ("hfl-agent-restore-{0}" -f ([Guid]::NewGuid().ToString('N')))
  New-Item -ItemType Directory -Force -Path $temp | Out-Null
  try {
    Expand-Archive -LiteralPath $archive -DestinationPath $temp -Force
    if (-not (Test-Path -LiteralPath (Join-Path $temp "config\agent.env")) -and
        -not (Test-Path -LiteralPath (Join-Path $temp "data\agent.db"))) {
      throw "rollback state archive contains neither Agent configuration nor database state"
    }
    foreach ($name in @("config\agent.env", "config\config.json", "data\agent.db")) {
      $source = Join-Path $temp $name
      $destination = Join-Path $DataRoot $name
      if (Test-Path -LiteralPath $source) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
        Copy-HflFileAtomically -Source $source -Destination $destination
      }
      else { Remove-Item -Force -LiteralPath $destination -ErrorAction SilentlyContinue }
    }
    # The online SQLite backup is standalone; stale WAL/SHM files from the
    # failed target must not be paired with it.
    Remove-Item -Force -LiteralPath (Join-Path (Get-HflDataStoreRoot $DataRoot) "agent.db-wal") -ErrorAction SilentlyContinue
    Remove-Item -Force -LiteralPath (Join-Path (Get-HflDataStoreRoot $DataRoot) "agent.db-shm") -ErrorAction SilentlyContinue
  } finally { Remove-Item -Recurse -Force -LiteralPath $temp -ErrorAction SilentlyContinue }
  Write-HflWarn "restored Agent configuration and database state from $archive"
}

function Restore-RollbackServiceDefinition {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
  $serviceDir = Join-Path (Join-Path (Get-HflBackupRoot $DataRoot) "rollback") "service"
  if ($InstallationMode -eq "system") {
    Remove-HflService
    $serviceSnapshot = Join-Path $serviceDir "service.json"
    if (Test-Path -LiteralPath $serviceSnapshot) {
      Install-HflService -ExePath (Join-Path $InstallRoot "hfl-agent.exe") -DataRoot $DataRoot -NoStart
    }
  } else {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
      Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
      Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    $taskXml = Join-Path $serviceDir "task.xml"
    if (Test-Path -LiteralPath $taskXml) {
      Register-ScheduledTask -TaskName $TaskName -Xml (Get-Content -Raw -LiteralPath $taskXml) -Force | Out-Null
    }
  }
}

function Enable-HflLifecycleForRollbackStart {
  if ($InstallationMode -eq "system") {
    # A service that was disabled after being manually started cannot be
    # started again until its startup type is temporarily relaxed.
    Set-Service -Name $ServiceName -StartupType Manual
    return
  }
  Enable-ScheduledTask -TaskName $TaskName | Out-Null
}

function Restore-HflLifecycleStartupPolicy {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
  $serviceDir = Join-Path (Join-Path (Get-HflBackupRoot $DataRoot) "rollback") "service"
  if ($InstallationMode -eq "system") {
    $serviceSnapshot = Join-Path $serviceDir "service.json"
    if (-not (Test-Path -LiteralPath $serviceSnapshot)) { return }
    $previousStartMode = [string]((Get-Content -Raw -LiteralPath $serviceSnapshot | ConvertFrom-Json).StartMode)
    switch ($previousStartMode.ToLowerInvariant()) {
      "auto" { Set-Service -Name $ServiceName -StartupType Automatic }
      "manual" { Set-Service -Name $ServiceName -StartupType Manual }
      "disabled" { Set-Service -Name $ServiceName -StartupType Disabled }
    }
    return
  }

  $taskSnapshot = Join-Path $serviceDir "task.xml"
  if (-not (Test-Path -LiteralPath $taskSnapshot)) { return }
  [xml]$taskXml = Get-Content -Raw -LiteralPath $taskSnapshot
  $enabledNode = $taskXml.SelectSingleNode("//*[local-name()='Settings']/*[local-name()='Enabled']")
  if ($null -ne $enabledNode -and $enabledNode.InnerText.Trim().ToLowerInvariant() -eq "false") {
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
  }
}

function Test-HflLocalUpgradeHealth {
  param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion,
    [switch]$SkipLifecycle
  )
  $manifest = Get-Content -Raw -LiteralPath $ManifestFile | ConvertFrom-Json
  $expectedCommit = [string]$manifest.agent_commit
  if ([string]::IsNullOrWhiteSpace($expectedCommit)) { return $false }
  $identity = (& (Join-Path $InstallRoot "hfl-agent.exe") version 2>&1 | Out-String).Trim()
  if ($LASTEXITCODE -ne 0 -or $identity -notmatch '^hyperfilelens-agent\s+(\S+)\s+\(([^)]+)\)$') { return $false }
  $actualVersion = $Matches[1].TrimStart('v')
  $actualCommit = $Matches[2]
  if ($actualVersion -ne $ExpectedVersion.TrimStart('v') -or $actualCommit -ne $expectedCommit) { return $false }
  & (Join-Path $InstallRoot "hfl-agent.exe") tasks list --data-dir $DataRoot --limit 1 *> $null
  if ($LASTEXITCODE -ne 0) { return $false }
  if ($NoService -or $SkipLifecycle) { return $true }
  $stable = 0; $lastPid = 0
  while ($stable -lt 10) {
    if ($InstallationMode -eq "system") {
      $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
      if ($null -eq $service -or $service.Status -ne "Running") { return $false }
      $processId = [int](Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue).ProcessId
    } else {
      $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
      if ($null -eq $task -or $task.State -ne "Running") { return $false }
      $processId = [int]((Get-Process -Name hfl-agent -ErrorAction SilentlyContinue | Where-Object { $_.Path -and (Get-FullPathOrSelf $_.Path).StartsWith((Get-FullPathOrSelf $AgentRoot).TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1).Id)
    }
    if ($processId -le 0) { return $false }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -eq $process -or -not $process.Path -or (Get-FullPathOrSelf $process.Path) -ne (Get-FullPathOrSelf (Join-Path $InstallRoot "hfl-agent.exe"))) { return $false }
    if ($lastPid -ne 0 -and $lastPid -ne $processId) { return $false }
    $lastPid = $processId; $stable++; Start-Sleep -Seconds 1
  }
  Write-HflOk "local health check passed ($ExpectedVersion, $expectedCommit; stable ${stable}s)"
  return $true
}

function Invoke-HflUpgradeRollback {
  param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [Parameter(Mandatory = $true)][string]$PreviousVersion,
    [Parameter(Mandatory = $true)][string]$OriginalError,
    [switch]$LegacyUpgrade,
    [switch]$ReturnAfterRollback
  )
  $script:UpgradeTransactionActive = $false
  try { Write-HflUpgradeState -DataRoot $DataRoot -Phase "rolling_back" }
  catch { Write-HflWarn "could not persist rolling_back state: $($_.Exception.Message)" }
  Write-HflWarn "upgrade failed: $OriginalError; attempting rollback"
  $rollbackErrors = @()
  if ($script:UpgradeDeploymentStarted) {
    $canRestore = $true
    try { Stop-AgentForUpgrade }
    catch {
      $canRestore = $false
      $rollbackErrors += "stop: $($_.Exception.Message)"
    }
    if ($canRestore) {
      try { Restore-RollbackBinaries } catch { $rollbackErrors += "binaries: $($_.Exception.Message)" }
      if ($script:UpgradeStateSnapshotReady) {
        try { Restore-AgentState -DataRoot $DataRoot } catch { $rollbackErrors += "state: $($_.Exception.Message)" }
      }
      try { Restore-RollbackServiceDefinition -DataRoot $DataRoot } catch { $rollbackErrors += "service definition: $($_.Exception.Message)" }
    }
  }
	if (-not $NoService -and -not $LegacyUpgrade) {
	  if ($script:UpgradeLifecycleWasRunning) {
	    try {
	      Enable-HflLifecycleForRollbackStart
	      Start-HflServiceOnly
	    }
	    catch { $rollbackErrors += "service start: $($_.Exception.Message)" }
	  }
	  try { Restore-HflLifecycleStartupPolicy -DataRoot $DataRoot }
	  catch { $rollbackErrors += "startup policy: $($_.Exception.Message)" }
  }
  if (-not $LegacyUpgrade -and -not (Test-HflLocalUpgradeHealth -DataRoot $DataRoot -ExpectedVersion $PreviousVersion -SkipLifecycle:(-not $script:UpgradeLifecycleWasRunning))) {
    $rollbackErrors += "old Agent local health verification failed"
  }
  if ($rollbackErrors.Count -gt 0) {
    try { Write-HflUpgradeState -DataRoot $DataRoot -Phase "rollback_failed" }
    catch { $rollbackErrors += "persist rollback failure state: $($_.Exception.Message)" }
    throw "Upgrade failed: $OriginalError. Rollback also failed: $($rollbackErrors -join '; ')"
  }
  # The rollback copy is only needed while restoring or validating the old
  # Agent. Once rollback health checks pass, remove it so a later invocation
  # cannot mistake an old snapshot for an interrupted transaction.
  $rollbackStatePersisted = $true
  try { Write-HflUpgradeState -DataRoot $DataRoot -Phase "rolled_back" }
  catch {
    $rollbackStatePersisted = $false
    Write-HflWarn "rollback completed, but its state could not be persisted; rollback data was retained: $($_.Exception.Message)"
  }
  if ($rollbackStatePersisted) { Remove-UpgradeRollback -DataRoot $DataRoot }
  Write-HflWarn "upgrade failed; rollback completed successfully"
  if ($ReturnAfterRollback) { return }
  throw "Upgrade failed: $OriginalError. Rollback completed successfully."
}

function Restore-HflInterruptedUpgrade {
  param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [Parameter(Mandatory = $true)]$State,
    [switch]$ForStart
  )
  $phase = [string]$State.phase
  $script:UpgradeStatePath = Join-Path (Get-HflLifecycleRoot $DataRoot) "upgrade-state.json"
  $script:UpgradePreviousVersion = [string]$State.previous_version
  $script:UpgradeTargetVersion = [string]$State.target_version
  $script:UpgradeLifecycleWasRunning = [bool]$State.lifecycle_was_running
  $script:UpgradeStateSnapshotReady = [bool]$State.state_snapshot_ready
  $script:UpgradeDeploymentStarted = [bool]$State.deployment_started
  $script:UpgradeBinBackup = Join-Path (Get-HflBackupRoot $DataRoot) "rollback\bin"
  switch ($phase) {
    { $_ -in @("committed", "rolled_back") } {
      Remove-UpgradeRollback -DataRoot $DataRoot
      Clear-HflUpgradeState
      return $false
    }
    { $_ -in @("preparing", "package_resolved") } {
      Write-HflWarn "discarding incomplete pre-deployment upgrade state ($phase)"
      Remove-Item -Recurse -Force -LiteralPath (Join-Path (Get-HflBackupRoot $DataRoot) "rollback") -ErrorAction SilentlyContinue
      Clear-HflUpgradeState
      return $false
    }
    "rollback_failed" {
      throw "The previous upgrade and rollback both failed. Preserve backup\rollback and resolve the recorded failure before retrying."
    }
    "awaiting_restart" {
      if ($ForStart) { return $true }
      throw "A staged upgrade is awaiting restart and verification. Run install.cmd start before starting another upgrade."
    }
    { $_ -in @("stopping", "service_stopped", "snapshotting", "state_snapshotted") } {
      Write-HflWarn "recovering interrupted upgrade before binary deployment ($phase)"
	  if ($script:UpgradeLifecycleWasRunning -and -not $NoService) {
	    try {
	      Enable-HflLifecycleForRollbackStart
	      Start-HflServiceOnly
	    }
	    finally { Restore-HflLifecycleStartupPolicy -DataRoot $DataRoot }
	  }
      if (-not (Test-HflLocalUpgradeHealth -DataRoot $DataRoot -ExpectedVersion $script:UpgradePreviousVersion -SkipLifecycle:(-not $script:UpgradeLifecycleWasRunning))) {
        throw "Interrupted upgrade recovery could not verify the previous Agent."
      }
      Remove-UpgradeRollback -DataRoot $DataRoot
      Clear-HflUpgradeState
      return $false
    }
    { $_ -in @("starting_service", "service_started", "healthy") } {
      if (Test-HflLocalUpgradeHealth -DataRoot $DataRoot -ExpectedVersion $script:UpgradeTargetVersion -SkipLifecycle:(-not $script:UpgradeLifecycleWasRunning)) {
        Write-HflWarn "finalizing an interrupted upgrade after target health verification ($phase)"
        Write-HflUpgradeState -DataRoot $DataRoot -Phase "committed"
        Remove-UpgradeRollback -DataRoot $DataRoot
        Clear-HflUpgradeState
        return $false
      }
      Invoke-HflUpgradeRollback -DataRoot $DataRoot -PreviousVersion $script:UpgradePreviousVersion -OriginalError "upgrade process was interrupted during $phase" -ReturnAfterRollback
      Clear-HflUpgradeState
      return $false
    }
    { $_ -in @("deploying", "deployed", "migrating", "migrated", "configuring_service", "rolling_back") } {
      Write-HflWarn "rolling back interrupted upgrade transaction ($phase)"
      $script:UpgradeDeploymentStarted = $true
      Invoke-HflUpgradeRollback -DataRoot $DataRoot -PreviousVersion $script:UpgradePreviousVersion -OriginalError "upgrade process was interrupted during $phase" -ReturnAfterRollback
      Clear-HflUpgradeState
      return $false
    }
    default {
      throw "Unknown interrupted upgrade phase '$phase'; preserve backup\rollback for manual recovery."
    }
  }
}

function Remove-UpgradeRollback {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
  $rollback = Join-Path (Get-HflBackupRoot $DataRoot) "rollback"
  if (Test-Path -LiteralPath $rollback) {
    try {
      Remove-Item -Recurse -Force -LiteralPath $rollback
      Write-HflOk "removed $rollback after local health confirmation"
    }
    catch {
      Write-HflWarn "local health verification passed, but rollback cleanup was deferred: $($_.Exception.Message)"
    }
  }
}

function Test-HflLifecycleRunning {
  if ($NoService) { return $false }
  if ($InstallationMode -eq "system") {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    return ($null -ne $service -and $service.Status -eq "Running")
  }
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  return ($null -ne $task -and $task.State -eq "Running")
}

function Merge-AgentEnv {
  param(
    [Parameter(Mandatory = $true)][string]$EnvFile,
    [Parameter(Mandatory = $true)][string]$DataRoot
  )
  Ensure-HflLogsDir -DataRoot $DataRoot
  $kopiaPath = Join-Path $InstallRoot "kopia.exe"
  $template = [ordered]@{
    HFL_DATA_DIR          = $DataRoot
    HFL_AGENT_ROOT        = $AgentRoot
    HFL_INSTALLATION_MODE = $InstallationMode
    HFL_KOPIA_PATH        = $kopiaPath
    HFL_INSECURE_TLS      = "1"
  }
  if ($InstallationMode -eq "account") {
    $template.HFL_RUN_AS_USER = $RunAsUser
    $template.HFL_RUN_AS_HOME = $RunAsHome
  }
  $dir = Split-Path -Parent $EnvFile
  if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  if (-not (Test-Path -LiteralPath $EnvFile)) {
    ($template.GetEnumerator() | ForEach-Object { "{0}={1}" -f $_.Key, $_.Value }) | Set-Content -Path $EnvFile -Encoding UTF8
    Write-HflOk "created $EnvFile"
    return
  }
  $existing = @{}
  foreach ($line in Get-Content -LiteralPath $EnvFile) {
    if ($line -match '^\s*([A-Z0-9_]+)=(.*)$') {
      $existing[$Matches[1]] = $true
    }
  }
  $added = @()
  $lines = Get-Content -LiteralPath $EnvFile
  foreach ($key in $template.Keys) {
    if (-not $existing.ContainsKey($key)) {
      $lines += "{0}={1}" -f $key, $template[$key]
      $added += $key
    }
  }

  # Preserve identity, console, and credential fields from the old file, but
  # always rewrite installer-owned paths for the unified Agent Root.
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_DATA_DIR" -Value $DataRoot
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_AGENT_ROOT" -Value $AgentRoot
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_INSTALLATION_MODE" -Value $InstallationMode
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_KOPIA_PATH" -Value $kopiaPath
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_INSECURE_TLS" -Value "1"
  if ($InstallationMode -eq "account") {
    Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_RUN_AS_USER" -Value $RunAsUser
    Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_RUN_AS_HOME" -Value $RunAsHome
  }
  Set-Content -Path $EnvFile -Value ($lines -join "`n") -Encoding UTF8
  if ($added.Count -gt 0) {
    Write-HflOk "merged agent.env keys: $($added -join ', ')"
  }
  else {
    Write-HflOk "updated unified Agent Root paths in $EnvFile"
  }
}

function Ensure-HflLogsDir {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
	$logDir = Get-HflLogsRoot $DataRoot
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  # System mode runs as LocalSystem; account mode runs as the selected user.
  icacls $logDir /grant "SYSTEM:(OI)(CI)M" /Q 2>$null | Out-Null
  icacls $logDir /grant "*S-1-5-32-544:(OI)(CI)M" /Q 2>$null | Out-Null
  if ($InstallationMode -eq "account" -and $RunAsUser) {
    Grant-HflDirectoryAccess -Path $logDir -Account $RunAsUser
  }
  $agentLog = Join-Path $logDir "agent.log"
  if (-not (Test-Path -LiteralPath $agentLog)) {
    New-Item -ItemType File -Force -Path $agentLog | Out-Null
  }
}

function Grant-HflDirectoryAccess {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Account
  )
  & icacls.exe $Path /grant "${Account}:(OI)(CI)M" /Q 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Could not grant the specified account '$Account' access to '$Path' (icacls exit code $LASTEXITCODE)."
  }
}

function Grant-HflDirectoryReadAccess {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Account
  )
  & icacls.exe $Path /grant "${Account}:(OI)(CI)RX" /Q 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Could not grant the specified account '$Account' read access to '$Path' (icacls exit code $LASTEXITCODE)."
  }
}

function Set-HflAgentRootPermissions {
  Ensure-HflAgentLayout -Root $AgentRoot
  if ($InstallationMode -eq "user") {
    return
  }
  # Keep one physical root while preventing the runtime account from replacing
  # the Agent binaries. Mutable state receives a separate write ACL.
  & icacls.exe $AgentRoot /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /Q 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not secure Agent Root '$AgentRoot'." }
  & icacls.exe $InstallRoot /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX" /Q 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not secure Agent binaries '$InstallRoot'." }
  foreach ($mutable in @($ConfigRoot, $DataStoreRoot, $LogsRoot, $CacheRoot, $MountsRoot, $RuntimeRoot, $LifecycleRoot, $BackupRoot)) {
    & icacls.exe $mutable /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /Q 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not secure Agent directory '$mutable'." }
  }
  if ($InstallationMode -eq "account" -and $RunAsUser) {
    Grant-HflDirectoryReadAccess -Path $AgentRoot -Account $RunAsUser
    Grant-HflDirectoryReadAccess -Path $InstallRoot -Account $RunAsUser
    foreach ($mutable in @($ConfigRoot, $DataStoreRoot, $LogsRoot, $CacheRoot, $MountsRoot, $RuntimeRoot, $LifecycleRoot, $BackupRoot)) {
      Grant-HflDirectoryAccess -Path $mutable -Account $RunAsUser
    }
  }
}

function Update-AgentDb {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
  $agent = Join-Path $InstallRoot "hfl-agent.exe"
  if (-not (Test-Path -LiteralPath $agent)) {
    Write-HflSkip "migrate agent.db (hfl-agent missing)"
    return
  }
  & $agent tasks list -data-dir $DataRoot -limit 1 *> $null
  if ($LASTEXITCODE -eq 0) {
    Write-HflOk "agent.db schema upgraded (if needed)"
  }
  else {
    throw "agent.db migration check failed; the upgraded Agent was not started."
  }
}

function Get-BundleVersion {
  return Get-BundleVersionFrom -Root $BundleRoot
}

function Test-Installed {
  return Test-Path -LiteralPath (Join-Path $InstallRoot "hfl-agent.exe")
}

function Test-LegacyLayout {
  if ($InstallationMode -eq "user") { return $false }
  $legacyAgent = Join-Path $legacyInstallRoot "hfl-agent.exe"
  $legacyEnv = Join-Path $legacyDataRoot "agent.env"
  $legacyDb = Join-Path $legacyDataRoot "agent.db"
  $newAgent = Join-Path $InstallRoot "hfl-agent.exe"
  $newEnv = Get-HflEnvFile $DefaultDataRoot
  $newDb = Join-Path $DataStoreRoot "agent.db"
  $migrationMarker = Join-Path $LifecycleRoot ".legacy-migration"
  return ((Test-Path -LiteralPath $legacyAgent) -and -not (Test-Path -LiteralPath $newAgent)) -or
    ((Test-Path -LiteralPath $legacyEnv) -and -not (Test-Path -LiteralPath $newEnv)) -or
    # A partially initialized unified root can already have config/agent.env
    # while the legacy flat database still needs to be moved into data/.
    ((Test-Path -LiteralPath $legacyDb) -and -not (Test-Path -LiteralPath $newDb)) -or
    # Retry cleanup after an interrupted migration even when the canonical
    # database was already copied before the previous run failed.
    ((Test-Path -LiteralPath $migrationMarker) -and (Test-Path -LiteralPath $legacyDb))
}

function Copy-LegacyEntry {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
  )
  if (-not (Test-Path -LiteralPath $Source)) { return }
  try {
    $sourceFull = [System.IO.Path]::GetFullPath($Source).TrimEnd('\')
    $destinationFull = [System.IO.Path]::GetFullPath($Destination).TrimEnd('\')
    if ($sourceFull.Equals($destinationFull, [System.StringComparison]::OrdinalIgnoreCase)) {
      return
    }
  }
  catch {
    throw "Could not resolve legacy migration path: $($_.Exception.Message)"
  }
  $parent = Split-Path -Parent $Destination
  if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
  if (Test-Path -LiteralPath $Source -PathType Container) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Copy-Item -Path (Join-Path $Source '*') -Destination $Destination -Recurse -Force
  }
  else {
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
  }
}

function Copy-LegacyBackupTree {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
  )
  if (-not (Test-Path -LiteralPath $Source)) { return }
  New-Item -ItemType Directory -Force -Path $Destination | Out-Null
  # On Windows the legacy data root is the new Agent Root. The archive lives
  # below backup/legacy, so copying backup recursively must exclude that
  # archive subtree or the copy would contain itself.
  foreach ($entry in Get-ChildItem -Force -LiteralPath $Source) {
    if ($entry.Name -eq "legacy") { continue }
    Copy-Item -LiteralPath $entry.FullName -Destination $Destination -Recurse -Force
  }
}

function Invoke-LegacyMigration {
  if (-not (Test-LegacyLayout)) { return }
  $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
  $script:LegacyMigrationRoot = Join-Path (Get-HflBackupRoot $DefaultDataRoot) "legacy\$stamp"
  $legacyProgram = Join-Path $script:LegacyMigrationRoot "program"
  $legacyState = Join-Path $script:LegacyMigrationRoot "state"
  New-Item -ItemType Directory -Force -Path $legacyProgram, $legacyState, $DefaultDataRoot | Out-Null

  # Stop the old Windows service before copying SQLite and runtime state.
  $oldService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
  $script:LegacyServiceWasRunning = ($null -ne $oldService -and $oldService.Status -eq 'Running')
  Stop-HflService

  foreach ($name in @("hfl-agent.exe", "kopia.exe", "install.ps1", "install.cmd", "uninstall.cmd", "MANIFEST.json", "INSTALLED_VERSION", "run-agent.ps1", "hfl-agent-user-launcher.exe")) {
    Copy-LegacyEntry -Source (Join-Path $legacyInstallRoot $name) -Destination (Join-Path $legacyProgram $name)
  }
  foreach ($name in @("agent.env", "agent.db", "agent.db-wal", "agent.db-shm", "config.json", "logs", "cache", "mounts", "lifecycle", "install.lock")) {
    Copy-LegacyEntry -Source (Join-Path $legacyDataRoot $name) -Destination (Join-Path $legacyState $name)
  }
  Copy-LegacyBackupTree -Source (Join-Path $legacyDataRoot "backup") -Destination (Join-Path $legacyState "backup")
  Copy-LegacyEntry -Source (Join-Path $legacyDataRoot "agent.env") -Destination (Join-Path $ConfigRoot "agent.env")
  Copy-LegacyEntry -Source (Join-Path $legacyDataRoot "config.json") -Destination (Join-Path $ConfigRoot "config.json")
  foreach ($name in @("agent.db", "agent.db-wal", "agent.db-shm")) {
    Copy-LegacyEntry -Source (Join-Path $legacyDataRoot $name) -Destination (Join-Path $DataStoreRoot $name)
  }
  foreach ($name in @("logs", "cache", "mounts", "runtime", "lifecycle")) {
    Copy-LegacyEntry -Source (Join-Path $legacyDataRoot $name) -Destination (Join-Path $AgentRoot $name)
  }
  # Promote the pre-unified backup/state snapshots into backup/rollback. The
  # complete original tree remains available under backup/legacy for rollback.
  Copy-LegacyEntry -Source (Join-Path $legacyDataRoot "backup\rollback") -Destination (Join-Path $BackupRoot "rollback")
  Copy-LegacyEntry -Source (Join-Path $legacyDataRoot "backup\state") -Destination (Join-Path $BackupRoot "rollback")
  Copy-LegacyEntry -Source (Join-Path $legacyDataRoot "backup\meta.json") -Destination (Join-Path $BackupRoot "rollback\meta.json")
  Copy-LegacyEntry -Source (Join-Path $legacyDataRoot "install.lock") -Destination (Join-Path $LifecycleRoot "install.lock")
  Set-Content -LiteralPath (Join-Path $LifecycleRoot ".legacy-migration") -Value "HFL_INSTALLATION_MODE=system" -Encoding UTF8
  Write-HflWarn "migrated legacy Agent layout into $AgentRoot; old files are archived under $script:LegacyMigrationRoot"
}

function Restore-LegacyServiceOnFailure {
  if (-not $script:LegacyServiceWasRunning) { return }
  if (-not (Test-Path -LiteralPath $legacyInstallRoot)) { return }
  try {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($null -ne $service -and $service.Status -ne 'Running') {
      Remove-HflService
      $legacyBinary = Join-Path $legacyInstallRoot "hfl-agent.exe"
      $legacyData = $legacyDataRoot
      $legacyBinPath = "`"$legacyBinary`" run -data-dir `"$legacyData`""
      New-Service -Name $ServiceName `
        -BinaryPathName $legacyBinPath `
        -DisplayName "HyperFileLens Agent" `
        -Description "HyperFileLens backup agent (legacy installation)" `
        -StartupType Automatic | Out-Null
      Start-Service -Name $ServiceName
      Write-HflWarn "restored the legacy Agent service after migration failure"
    }
    elseif ($null -eq $service) {
      $legacyBinary = Join-Path $legacyInstallRoot "hfl-agent.exe"
      $legacyBinPath = "`"$legacyBinary`" run -data-dir `"$legacyDataRoot`""
      New-Service -Name $ServiceName `
        -BinaryPathName $legacyBinPath `
        -DisplayName "HyperFileLens Agent" `
        -Description "HyperFileLens backup agent (legacy installation)" `
        -StartupType Automatic | Out-Null
      Start-Service -Name $ServiceName
      Write-HflWarn "recreated the legacy Agent service after migration failure"
    }
  }
  catch {
    Write-HflWarn "could not restore the legacy Agent service after migration failure: $($_.Exception.Message)"
  }
}

function Complete-LegacyMigration {
  if ([string]::IsNullOrWhiteSpace($script:LegacyMigrationRoot)) { return }
  $status = Get-HflServiceStatusLine
  if ($status -notmatch 'running|active') {
    Write-HflWarn "legacy layout retained because the new service is not healthy ($status)"
    return
  }
  if (Test-Path -LiteralPath $legacyInstallRoot) {
    Remove-Item -Recurse -Force -LiteralPath $legacyInstallRoot -ErrorAction SilentlyContinue
  }
  # The Windows legacy data root is the new Agent Root. Remove only old
  # On Windows the legacy data root is the same physical AgentRoot, so the
  # copied entries are now the canonical siblings and must not be deleted.
  if ([System.IO.Path]::GetFullPath($legacyDataRoot).TrimEnd('\') -ne [System.IO.Path]::GetFullPath($AgentRoot).TrimEnd('\')) {
    foreach ($name in @("agent.env", "agent.db", "agent.db-wal", "agent.db-shm", "config.json", "logs", "cache", "mounts", "runtime", "lifecycle", "install.lock")) {
      Remove-Item -Recurse -Force -LiteralPath (Join-Path $AgentRoot $name) -ErrorAction SilentlyContinue
    }
  }
  else {
    # Legacy Windows state and metadata lived below backup/state or directly
    # under backup. Remove only those obsolete paths; backup/rollback is now
    # the single upgrade snapshot location.
    foreach ($name in @("agent.env", "agent.db", "agent.db-wal", "agent.db-shm", "config.json", "install.lock")) {
      Remove-Item -Force -LiteralPath (Join-Path $AgentRoot $name) -ErrorAction SilentlyContinue
    }
    Remove-Item -Recurse -Force -LiteralPath (Join-Path $BackupRoot "state") -ErrorAction SilentlyContinue
    Remove-Item -Force -LiteralPath (Join-Path $BackupRoot "meta.json") -ErrorAction SilentlyContinue
  }
  Remove-Item -Force -LiteralPath (Join-Path $LifecycleRoot ".legacy-migration") -ErrorAction SilentlyContinue
  Write-HflOk "removed legacy Agent directories after successful migration"
}

function Get-ResolvedDataRoot {
  param([string]$Override)
  if ($Override) { return $Override }
  $candidates = @(
    (Get-HflEnvFile $DefaultDataRoot)
  )
  foreach ($f in $candidates) {
    if (Test-Path -LiteralPath $f) {
      foreach ($line in Get-Content -LiteralPath $f) {
        if ($line -match '^\s*HFL_DATA_DIR=(.+)$') {
          return $Matches[1].Trim()
        }
      }
    }
  }
  return $DefaultDataRoot
}

function Test-SafeDataPath([string]$path) {
  if ([string]::IsNullOrWhiteSpace($path)) { return $false }
  $full = try { [System.IO.Path]::GetFullPath($path) } catch { return $false }
  if (Test-HflPathContainsReparsePoint -Path $full) { return $false }
  if ($InstallationMode -ne "system") {
    $expected = [System.IO.Path]::GetFullPath($DefaultDataRoot)
    return $full.TrimEnd('\').Equals(
      $expected.TrimEnd('\'),
      [System.StringComparison]::OrdinalIgnoreCase
    )
  }
  $allowedRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $env:ProgramData "HyperFileLens\Agent")
  )
  $normalizedFull = $full.TrimEnd('\')
  $normalizedAllowedRoot = $allowedRoot.TrimEnd('\')
  return $normalizedFull.Equals(
    $normalizedAllowedRoot,
    [System.StringComparison]::OrdinalIgnoreCase
  ) -or $normalizedFull.StartsWith(
    $normalizedAllowedRoot + '\',
    [System.StringComparison]::OrdinalIgnoreCase
  )
}

function Test-HflPathContainsReparsePoint([string]$Path) {
  try {
    $current = [System.IO.Path]::GetFullPath($Path)
    while (-not [string]::IsNullOrWhiteSpace($current)) {
      if (Test-Path -LiteralPath $current) {
        $item = Get-Item -Force -LiteralPath $current -ErrorAction Stop
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
          return $true
        }
      }
      $parent = [System.IO.Directory]::GetParent($current)
      if ($null -eq $parent) { break }
      if ($parent.FullName.Equals($current, [System.StringComparison]::OrdinalIgnoreCase)) { break }
      $current = $parent.FullName
    }
    return $false
  }
  catch {
    return $true
  }
}

function Wait-HflServiceStopped {
  param([int]$TimeoutSeconds = 30)
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($null -eq $svc -or $svc.Status -eq 'Stopped') { return $true }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function Stop-HflService {
  if ($InstallationMode -ne "system") {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
      Write-HflSkip "stop $LifecycleLabel $TaskName (not installed)"
      return
    }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-HflOk "stopped $LifecycleLabel $TaskName"
    return
  }
  $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
  if ($null -eq $svc) {
    Write-HflSkip "stop service $ServiceName (not installed)"
    return
  }
  if ($svc.Status -ne 'Stopped') {
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    if (Wait-HflServiceStopped) {
      Write-HflOk "stopped service $ServiceName"
    }
    else {
      Write-HflWarn "service $ServiceName did not reach Stopped within timeout"
    }
  }
  else {
    Write-HflSkip "stop service $ServiceName (not running)"
  }
}

function Stop-HflAgentProcesses {
  param([string]$Reason = "uninstall")
  foreach ($name in @("hfl-agent", "hfl-agent-user-launcher", "kopia")) {
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
      $procs = @(Get-Process -Name $name -ErrorAction SilentlyContinue | Where-Object {
        try {
          $path = $_.Path
          -not [string]::IsNullOrWhiteSpace($path) -and
            [System.IO.Path]::GetFullPath($path).StartsWith(
              [System.IO.Path]::GetFullPath($AgentRoot).TrimEnd('\') + '\',
              [System.StringComparison]::OrdinalIgnoreCase
            )
        }
        catch { $false }
      })
      if ($procs.Count -eq 0) { break }
      foreach ($proc in $procs) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
      }
      Start-Sleep -Milliseconds 500
    }
    $remaining = @(Get-Process -Name $name -ErrorAction SilentlyContinue | Where-Object {
      try {
        $_.Path -and [System.IO.Path]::GetFullPath($_.Path).StartsWith(
          [System.IO.Path]::GetFullPath($AgentRoot).TrimEnd('\') + '\',
          [System.StringComparison]::OrdinalIgnoreCase
        )
      }
      catch { $false }
    })
    if ($remaining.Count -gt 0) {
      Write-HflWarn "process $name still running after stop attempts ($Reason)"
    }
    else {
      Write-HflOk "stopped $name process(es) ($Reason)"
    }
  }
}

function Stop-AgentForUpgrade {
  Stop-HflService
  $procs = @(Get-Process -Name "hfl-agent" -ErrorAction SilentlyContinue | Where-Object {
    try {
      $_.Path -and [System.IO.Path]::GetFullPath($_.Path).StartsWith(
        [System.IO.Path]::GetFullPath($AgentRoot).TrimEnd('\') + '\',
        [System.StringComparison]::OrdinalIgnoreCase
      )
    }
    catch { $false }
  })
  if ($procs.Count -gt 0) {
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Write-HflOk "stopped hfl-agent process (pre-upgrade)"
  }
  $remaining = @(Get-Process -Name "hfl-agent" -ErrorAction SilentlyContinue | Where-Object {
    try {
      $_.Path -and (Get-FullPathOrSelf $_.Path).StartsWith((Get-FullPathOrSelf $AgentRoot).TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)
    }
    catch { $false }
  })
  if ($remaining.Count -gt 0) { throw "hfl-agent process did not stop before upgrade" }
  if ($InstallationMode -eq "system") {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($null -ne $service -and $service.Status -ne "Stopped") { throw "Windows service did not stop before upgrade" }
  }
}

function Remove-HflService {
  if ($InstallationMode -ne "system") {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
      Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
      Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
      Write-HflOk "removed $LifecycleLabel $TaskName"
    }
    else {
      Write-HflSkip "remove $LifecycleLabel $TaskName (not installed)"
    }
    return
  }
  $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
  if ($null -ne $svc) {
    Stop-HflService
    if (-not (Wait-HflServiceStopped)) {
      Write-HflWarn "service $ServiceName still not Stopped before sc delete"
    }
    $null = & sc.exe delete $ServiceName 2>&1
    Start-Sleep -Seconds 1
    Write-HflOk "removed service $ServiceName"
  }
  else {
    Write-HflSkip "remove service $ServiceName (not installed)"
  }
}

function Schedule-InstallRootRemoval {
  param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [string]$LogFile = ""
  )
  $target = try { [System.IO.Path]::GetFullPath($InstallRoot) } catch { $InstallRoot.TrimEnd('\') }
  if (-not (Test-Path -LiteralPath $target)) {
    Write-HflSkip "remove install directory $target (not present)"
    return
  }

  $runner = Join-Path $env:TEMP ("hfl-remove-install-{0}.ps1" -f ([Guid]::NewGuid().ToString('N')))
  $targetEsc = $target.Replace("'", "''")
  $logEsc = if ($LogFile) { $LogFile.Replace("'", "''") } else { "" }
  $body = @"
`$target = '$targetEsc'
`$logFile = '$logEsc'
function Write-Trace([string]`$msg) {
  if (-not `$logFile) { return }
  `$dir = Split-Path -Parent `$logFile
  if (-not `$dir -or -not (Test-Path -LiteralPath `$dir)) { return }
  `$ts = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
  Add-Content -LiteralPath `$logFile -Value "[`$ts] [DETAIL] `$msg" -Encoding UTF8 -ErrorAction SilentlyContinue
}
Write-Trace "deferred install dir removal started target=`$target"
Start-Sleep -Seconds 8
`$installCmd = Join-Path `$target 'install.cmd'
if (Test-Path -LiteralPath `$installCmd) {
  Remove-Item -Force -LiteralPath `$installCmd -ErrorAction SilentlyContinue
  if (Test-Path -LiteralPath `$installCmd) {
    Write-Trace "install.cmd still locked; retrying after delay"
    Start-Sleep -Seconds 3
    Remove-Item -Force -LiteralPath `$installCmd -ErrorAction SilentlyContinue
  }
  if (-not (Test-Path -LiteralPath `$installCmd)) {
    Write-Trace "removed residual install.cmd"
  }
}
if (-not (Test-Path -LiteralPath `$target)) {
  Write-Trace "install directory already removed"
  exit 0
}
for (`$attempt = 1; `$attempt -le 5; `$attempt++) {
  if (-not (Test-Path -LiteralPath `$target)) { break }
  try {
    Remove-Item -LiteralPath `$target -Recurse -Force -ErrorAction Stop
    Write-Trace "removed install directory `$target (attempt `$attempt)"
    break
  }
  catch {
    Write-Trace "failed to remove install directory `$target (attempt `$attempt): `$(`$_.Exception.Message)"
    Start-Sleep -Seconds 2
  }
}
if (Test-Path -LiteralPath `$target) {
  Write-Trace "failed to remove install directory `$target after retries"
  exit 1
}
exit 0
"@
  Set-Content -LiteralPath $runner -Value $body -Encoding UTF8
  Start-Process -FilePath 'powershell.exe' -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $runner
  ) | Out-Null
  Write-HflOk "scheduled removal of install directory $target (after install.cmd exits)"
}

function Install-HflService {
  param([string]$ExePath, [string]$DataRoot, [switch]$NoStart)
  Remove-HflService
  if ($InstallationMode -eq "user") {
    # A dedicated GUI-subsystem launcher keeps the task and Agent windowless.
    # PowerShell cannot reliably suppress its console under Windows Terminal.
    $runner = Join-Path $InstallRoot "hfl-agent-user-launcher.exe"
    if (-not (Test-Path -LiteralPath $runner)) {
      throw "Current-user Agent launcher is missing: $runner"
    }
    $action = New-ScheduledTaskAction `
      -Execute $runner `
      -Argument "-data-dir `"$DataRoot`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentWindowsIdentity
    $principal = New-ScheduledTaskPrincipal `
      -UserId $CurrentWindowsIdentity `
      -LogonType Interactive `
      -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
      -StartWhenAvailable `
      -AllowStartIfOnBatteries `
      -DontStopIfGoingOnBatteries `
      -RestartCount 3 `
      -RestartInterval (New-TimeSpan -Minutes 1) `
      -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask `
      -TaskName $TaskName `
      -Action $action `
      -Trigger $trigger `
      -Settings $settings `
      -Principal $principal `
      -Force | Out-Null
    Write-HflOk "installed current-user task $TaskName"
    if (-not $NoStart) {
      Start-ScheduledTask -TaskName $TaskName
      Write-HflOk "started current-user task $TaskName ($(Get-HflServiceStatusLine))"
    }
    else {
      Write-HflSkip "start current-user task $TaskName (-NoStart)"
    }
    return
  }
  if ($InstallationMode -eq "account") {
    $action = New-ScheduledTaskAction `
      -Execute $ExePath `
      -Argument "run -data-dir `"$DataRoot`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal `
      -UserId $RunAsUser `
      -LogonType S4U `
      -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
      -StartWhenAvailable `
      -AllowStartIfOnBatteries `
      -DontStopIfGoingOnBatteries `
      -RestartCount 3 `
      -RestartInterval (New-TimeSpan -Minutes 1) `
      -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask `
      -TaskName $TaskName `
      -Action $action `
      -Trigger $trigger `
      -Settings $settings `
      -Principal $principal `
      -Force | Out-Null
    Write-HflOk "installed specified-user task $TaskName ($RunAsUser)"
    if (-not $NoStart) {
      Start-ScheduledTask -TaskName $TaskName
      Write-HflOk "started specified-user task $TaskName ($(Get-HflServiceStatusLine))"
    }
    else {
      Write-HflSkip "start specified-user task $TaskName (-NoStart)"
    }
    return
  }
  $binPath = "`"$ExePath`" run -data-dir `"$DataRoot`""
  New-Service -Name $ServiceName `
    -BinaryPathName $binPath `
    -DisplayName "HyperFileLens Agent" `
    -Description "HyperFileLens backup agent (WebSocket control plane and local CLI)" `
    -StartupType Automatic | Out-Null
  sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/10000/restart/30000 | Out-Null
  Write-HflOk "installed service $ServiceName"
  if (-not $NoStart) {
    Start-Service -Name $ServiceName
    Write-HflOk "started service $ServiceName ($(Get-HflServiceStatusLine))"
  }
  else {
    Write-HflSkip "start service $ServiceName (-NoStart)"
  }
}

function Start-HflService {
  param([string]$ExePath, [string]$DataRoot)
  Install-HflService -ExePath $ExePath -DataRoot $DataRoot
}

function Assert-HflInstalled {
  if (-not (Test-Installed)) {
    throw "Agent not installed. Use: install.cmd"
  }
}

function Start-HflServiceOnly {
  if ($InstallationMode -ne "system") {
    if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
      Install-HflService `
        -ExePath (Join-Path $InstallRoot "hfl-agent.exe") `
        -DataRoot (Get-ResolvedDataRoot -Override "")
      return
    }
    Start-ScheduledTask -TaskName $TaskName
    Write-HflOk "started $LifecycleLabel $TaskName ($(Get-HflServiceStatusLine))"
    return
  }
  $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
  if ($null -eq $svc) {
    Install-HflService `
      -ExePath (Join-Path $InstallRoot "hfl-agent.exe") `
      -DataRoot (Get-ResolvedDataRoot -Override "")
    return
  }
  Start-Service -Name $ServiceName
  Write-HflOk "started service $ServiceName ($(Get-HflServiceStatusLine))"
}

function Invoke-Start {
  Assert-HflInstalled
  $dataRoot = Get-ResolvedDataRoot -Override ""
  $statePath = Join-Path (Get-HflLifecycleRoot $dataRoot) "upgrade-state.json"
  $pendingState = $null
  if (Test-Path -LiteralPath $statePath) {
    $pendingState = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
  }
  if ($null -ne $pendingState) {
    Acquire-HflLifecycleLock -DataRoot $dataRoot -Operation "upgrade-verification"
    try {
      $stagedStart = Restore-HflInterruptedUpgrade -DataRoot $dataRoot -State $pendingState -ForStart
      if ($stagedStart) {
        if ([string]::IsNullOrWhiteSpace($script:UpgradeTargetVersion)) {
          throw "Pending upgrade state is missing its target version ($statePath)."
        }
        $script:UpgradeDeploymentStarted = $true
        $script:UpgradeStopAttempted = $true
        $script:UpgradeTransactionActive = $true
        Write-HflBanner "start staged upgrade"
        Write-HflSection "Actions"
        try {
          Write-HflUpgradeState -DataRoot $dataRoot -Phase "starting_service"
          Start-HflServiceOnly
          Write-HflUpgradeState -DataRoot $dataRoot -Phase "service_started"
          if (-not (Test-HflLocalUpgradeHealth -DataRoot $dataRoot -ExpectedVersion $script:UpgradeTargetVersion)) {
            throw "the staged Agent failed local health verification"
          }
        }
        catch {
          Invoke-HflUpgradeRollback -DataRoot $dataRoot -PreviousVersion $script:UpgradePreviousVersion -OriginalError $_.Exception.Message
        }
        $script:UpgradeTransactionActive = $false
        Write-HflUpgradeState -DataRoot $dataRoot -Phase "committed"
        Remove-UpgradeRollback -DataRoot $dataRoot
        Clear-HflUpgradeState
        Write-HflSection "Summary"
        Write-HflSummaryLine "Status" "upgrade committed"
        Write-HflSummaryLine "Version" $script:UpgradeTargetVersion
        Write-Host ""
        return
      }
    }
    finally { Release-HflLifecycleLock }
  }
  Acquire-HflLifecycleLock -DataRoot $dataRoot -Operation "start"
  try {
  Write-HflBanner "start"
  Write-HflSection "Actions"
  Start-HflServiceOnly
  Write-HflSection "Summary"
  Write-HflSummaryLine "Service" "$ServiceName ($(Get-HflServiceStatusLine))"
  Write-Host ""
  Write-Host "Done."
  Write-Host ""
  }
  finally { Release-HflLifecycleLock }
}

function Invoke-Stop {
  Assert-HflInstalled
  $dataRoot = Get-ResolvedDataRoot -Override ""
  Acquire-HflLifecycleLock -DataRoot $dataRoot -Operation "stop"
  try {
  Write-HflBanner "stop"
  Write-HflSection "Actions"
  Stop-HflService
  Write-HflSection "Summary"
  Write-HflSummaryLine "Service" "$ServiceName ($(Get-HflServiceStatusLine))"
  Write-Host ""
  Write-Host "Done."
  Write-Host ""
  }
  finally { Release-HflLifecycleLock }
}

function Invoke-Restart {
  Assert-HflInstalled
  $dataRoot = Get-ResolvedDataRoot -Override ""
  $statePath = Join-Path (Get-HflLifecycleRoot $dataRoot) "upgrade-state.json"
  if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
    Invoke-Start
    return
  }
  Acquire-HflLifecycleLock -DataRoot $dataRoot -Operation "restart"
  try {
  Write-HflBanner "restart"
  Write-HflSection "Actions"
  Stop-HflService
  Start-HflServiceOnly
  Write-HflSection "Summary"
  Write-HflSummaryLine "Service" "$ServiceName ($(Get-HflServiceStatusLine))"
  Write-Host ""
  Write-Host "Done."
  Write-Host ""
  }
  finally { Release-HflLifecycleLock }
}

function Deploy-AdminScripts {
  param([string]$SrcRoot = $BundleRoot)
  $srcScript = Join-Path $SrcRoot "install.ps1"
  $srcManifest = Join-Path $SrcRoot "MANIFEST.json"
  if (-not (Test-Path -LiteralPath $srcScript)) {
    throw "Missing bundle installer: $srcScript"
  }
  $destPs1 = Join-Path $InstallRoot "install.ps1"
  Deploy-InstallerFile -Source $srcScript -Destination $destPs1
  $srcCmd = Join-Path $SrcRoot "install.cmd"
  $destCmd = Join-Path $InstallRoot "install.cmd"
  if (Test-Path -LiteralPath $srcCmd) {
    Deploy-InstallerFile -Source $srcCmd -Destination $destCmd
  }
  else {
    @"
@echo off
setlocal
set "PS1=%~dp0install.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "EC=%ERRORLEVEL%"
if /I "%~1"=="uninstall" cd /d "%TEMP%"
endlocal & exit /b %EC%
"@ | Set-Content -Path $destCmd -Encoding ASCII
    Write-HflOk "deployed $destCmd"
  }
  $srcUninstall = Join-Path $SrcRoot "uninstall.cmd"
  $destUninstall = Join-Path $InstallRoot "uninstall.cmd"
  if (Test-Path -LiteralPath $srcUninstall) {
    Copy-HflFileAtomically -Source $srcUninstall -Destination $destUninstall
  }
  else {
    throw "Missing bundle uninstaller: $srcUninstall"
  }
  Write-HflOk "deployed $destUninstall"
  if (Test-Path -LiteralPath $srcManifest) {
    Copy-HflFileAtomically -Source $srcManifest -Destination $ManifestFile
    Write-HflOk "deployed $ManifestFile"
  }
}

function Get-FullPathOrSelf {
  param([Parameter(Mandatory = $true)][string]$Path)
  try { return [System.IO.Path]::GetFullPath($Path) } catch { return $Path }
}

function Register-DeferredFileMove {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
  )
  $sourceEscaped = $Source.Replace("'", "''")
  $destinationEscaped = $Destination.Replace("'", "''")
  $cmd = @"
Start-Sleep -Seconds 2
Move-Item -LiteralPath '$sourceEscaped' -Destination '$destinationEscaped' -Force
"@
  Start-Process -FilePath 'powershell.exe' `
    -ArgumentList @('-NoProfile', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-Command', $cmd) `
    | Out-Null
}

function Copy-HflFileAtomically {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
  )
  $temporary = "$Destination.new.$([Guid]::NewGuid().ToString('N'))"
  try {
    Copy-Item -Force -LiteralPath $Source -Destination $temporary
    Move-Item -Force -LiteralPath $temporary -Destination $Destination
  }
  finally { Remove-Item -Force -LiteralPath $temporary -ErrorAction SilentlyContinue }
}

function Deploy-InstallerFile {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
  )
  $destFull = Get-FullPathOrSelf -Path $Destination
  $running = if ($MyInvocation.PSCommandPath) { Get-FullPathOrSelf -Path $MyInvocation.PSCommandPath } else { "" }
  if ($running -and $destFull -eq $running) {
    $pending = "$Destination.pending"
    Copy-Item -Force -LiteralPath $Source -Destination $pending
    Register-DeferredFileMove -Source $pending -Destination $Destination
    Write-HflOk "staged $Destination replacement (applied after upgrade exits)"
    return
  }
  Copy-HflFileAtomically -Source $Source -Destination $Destination
  Write-HflOk "deployed $Destination"
}

function Deploy-Binaries {
  param([string]$SrcRoot = $BundleRoot)
  $srcAgent = Join-Path $SrcRoot "bin\hfl-agent.exe"
  $srcLauncher = Join-Path $SrcRoot "bin\hfl-agent-user-launcher.exe"
  $srcKopia = Join-Path $SrcRoot "bin\kopia.exe"
  if (-not (Test-Path -LiteralPath $srcAgent)) {
    if (Test-InstalledScriptLocation) {
      throw "Missing bundle binary: $srcAgent. Run upgrade -From <package.zip>, or use remote agent.upgrade."
    }
    throw "Missing bundle binary: $srcAgent"
  }
  if (-not (Test-Path -LiteralPath $srcKopia)) {
    if (Test-InstalledScriptLocation) {
      throw "Missing bundle kopia: $srcKopia. Run upgrade -From <package.zip>, or use remote agent.upgrade."
    }
    throw "Missing bundle kopia: $srcKopia"
  }
  if (-not (Test-Path -LiteralPath $srcLauncher)) {
    if (Test-InstalledScriptLocation) {
      throw "Missing current-user Agent launcher: $srcLauncher. Run upgrade -From <package.zip>, or use remote agent.upgrade."
    }
    throw "Missing current-user Agent launcher: $srcLauncher"
  }
  New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
  $deployAgent = -not $KopiaOnly.IsPresent
  $deployKopia = -not $AgentOnly.IsPresent
  $ver = Get-BundleVersionFrom -Root $SrcRoot
  # Stage the verified recovery-capable lifecycle script before replacing the
  # executables. A process interruption during deployment then leaves an entry
  # point that understands upgrade-state.json and can resume rollback.
  Deploy-AdminScripts -SrcRoot $SrcRoot
  if ($deployAgent) {
    Copy-HflFileAtomically -Source $srcAgent -Destination (Join-Path $InstallRoot "hfl-agent.exe")
    Write-HflOk "deployed $(Join-Path $InstallRoot 'hfl-agent.exe') ($ver)"
    Copy-HflFileAtomically -Source $srcLauncher -Destination (Join-Path $InstallRoot "hfl-agent-user-launcher.exe")
    Write-HflOk "deployed $(Join-Path $InstallRoot 'hfl-agent-user-launcher.exe')"
  }
  if ($deployKopia) {
    Copy-HflFileAtomically -Source $srcKopia -Destination (Join-Path $InstallRoot "kopia.exe")
    Write-HflOk "deployed $(Join-Path $InstallRoot 'kopia.exe')"
  }
  $versionTemp = "$InstalledVersionFile.new.$([Guid]::NewGuid().ToString('N'))"
  try {
    Set-Content -LiteralPath $versionTemp -Value $ver -Encoding UTF8
    Move-Item -Force -LiteralPath $versionTemp -Destination $InstalledVersionFile
  }
  finally { Remove-Item -Force -LiteralPath $versionTemp -ErrorAction SilentlyContinue }
  Write-HflOk "wrote $InstalledVersionFile ($ver)"
}

function Set-HflEnvLine {
  param(
    [Parameter(Mandatory = $true)][ref]$Lines,
    [Parameter(Mandatory = $true)][string]$Key,
    [string]$Value
  )
  if ([string]::IsNullOrWhiteSpace($Value)) { return }
  $escapedKey = [regex]::Escape($Key)
  $newLine = "{0}={1}" -f $Key, $Value.Replace('"', '\"')
  $found = $false
  # Force the pipeline result to remain an array even when it contains one
  # line. Without @(...), PowerShell unwraps a single string; the next `+=`
  # then concatenates the new key onto that string instead of appending a line.
  $updated = @(foreach ($line in @($Lines.Value)) {
    if ($line -match "^\s*$escapedKey=") {
      if (-not $found) { $found = $true; $newLine }
    }
    else { $line }
  })
  if (-not $found) { $updated += $newLine }
  $Lines.Value = @($updated)
}

function Write-AgentEnv {
  param([string]$EnvFile, [string]$DataRoot)
  Ensure-HflLogsDir -DataRoot $DataRoot
  $kopiaPath = Join-Path $InstallRoot "kopia.exe"
  $dir = Split-Path -Parent $EnvFile
  if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $lines = if (Test-Path -LiteralPath $EnvFile) { @(Get-Content -LiteralPath $EnvFile) } else { @() }
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_WSS_URL" -Value $WssUrl
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_API_BASE" -Value $ApiBase
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_ORG_KEY" -Value $OrgKey
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_NODE_TOKEN" -Value $NodeToken
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_NODE_ID" -Value $NodeId
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_DATA_DIR" -Value $DataRoot
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_AGENT_ROOT" -Value $AgentRoot
  $existingRole = Read-HflEnvValue -EnvFile $EnvFile -Key "HFL_NODE_ROLE"
  $effectiveRole = if ($Role -eq "agent" -and $existingRole) { $existingRole } else { $Role }
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_KOPIA_PATH" -Value $kopiaPath
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_NODE_ROLE" -Value $effectiveRole
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_INSTALLATION_MODE" -Value $InstallationMode
  if ($InstallationMode -eq "account") {
    Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_RUN_AS_USER" -Value $RunAsUser
    Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_RUN_AS_HOME" -Value $RunAsHome
  }
  Set-HflEnvLine -Lines ([ref]$lines) -Key "HFL_INSECURE_TLS" -Value "1"
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $EnvFile) | Out-Null
  Set-Content -Path $EnvFile -Value ($lines -join "`n") -Encoding UTF8
  Write-HflOk "wrote $EnvFile"
}

function Remove-HflInstallFile {
  param([Parameter(Mandatory = $true)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) {
    Write-HflSkip "remove $Path (not present)"
    return
  }
  Remove-Item -Force -LiteralPath $Path -ErrorAction SilentlyContinue
  if (Test-Path -LiteralPath $Path) {
    Write-HflWarn "failed to remove $Path (still present)"
  }
  else {
    Write-HflOk "removed $Path"
  }
}

function Invoke-Install {
  if (Test-Installed) {
    throw "Agent already installed. Use: install.cmd upgrade -From <package.zip>"
  }
  $archRel = Get-HflSupportedArchitecture
  $dataRoot = if ($DataDir) { $DataDir } else { $DefaultDataRoot }
  if ($InstallationMode -eq "user") {
    $resolvedDataRoot = [System.IO.Path]::GetFullPath($dataRoot).TrimEnd('\')
    $resolvedDefaultDataRoot = [System.IO.Path]::GetFullPath($DefaultDataRoot).TrimEnd('\')
    if (-not $resolvedDataRoot.Equals(
        $resolvedDefaultDataRoot,
        [System.StringComparison]::OrdinalIgnoreCase
      )) {
      throw "User-level installation uses the fixed data directory $DefaultDataRoot; -DataDir is not supported."
    }
  }
  if ($InstallationMode -eq "account") {
    if ([string]::IsNullOrWhiteSpace($RunAsUser)) {
      throw "A Windows account is required for specified-user continuous protection."
    }
    try {
      $sid = ([System.Security.Principal.NTAccount]::new($RunAsUser)).Translate([System.Security.Principal.SecurityIdentifier]).Value
    }
    catch {
      throw "The specified Windows account '$RunAsUser' could not be resolved."
    }
    # Administrators-group membership does not mean the runtime is elevated.
    # The specified-user task is always registered with RunLevel Limited, so
    # the Agent runs with the selected account's non-elevated token even when
    # that account is also a local administrator.
    if ([string]::IsNullOrWhiteSpace($RunAsHome)) {
      $profileKey = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$sid"
      $profile = (Get-ItemProperty -LiteralPath $profileKey -ErrorAction SilentlyContinue).ProfileImagePath
      if ($profile) { $RunAsHome = [Environment]::ExpandEnvironmentVariables($profile) }
    }
    if ([string]::IsNullOrWhiteSpace($RunAsHome) -or -not (Test-Path -LiteralPath $RunAsHome)) {
      throw "The profile directory for '$RunAsUser' could not be resolved."
    }
    $dataRoot = if ($DataDir) { $DataDir } else { $DefaultDataRoot }
    Ensure-HflAgentLayout -Root $AgentRoot
    $env:HFL_RUN_AS_USER = $RunAsUser
    $env:HFL_RUN_AS_HOME = $RunAsHome
  }
  Set-HflAgentRootPermissions
  Start-HflInstallLog -DataRoot $dataRoot
  try {
    if (-not $QuietFooter) {
      $displayRole = Get-HflRoleDisplayName -Value $Role
      Write-HflBanner "install v$(Get-BundleVersion)" -RoleName $displayRole
      Write-HflSection "Target"
      Write-HflSummaryLine "Platform" "windows/$archRel"
      Write-HflSummaryLine "Role" $displayRole
      Write-HflSummaryLine "Install path" $InstallRoot
      Write-HflSummaryLine "Data path" $dataRoot
      Write-HflSection "Preflight checks"
      Write-HflBundlePreflight
      Write-HflSection "Installing Agent"
    }

    Invoke-LegacyMigration
    Deploy-Binaries
    $envFile = Get-HflEnvFile $dataRoot
    Write-AgentEnv -EnvFile $envFile -DataRoot $dataRoot

    if ($NoService) {
      if (-not $QuietFooter) {
        Write-HflSkip "install managed startup (-NoService)"
        Write-HflSection "Verifying"
        Write-HflSkip "Managed startup was not installed by request"
        Write-HflSection "Installation summary"
        Write-HflSummaryLine "Status" "installed (no managed startup)"
        Write-HflSummaryLine "Binary" (Join-Path $InstallRoot "hfl-agent.exe")
        Write-HflSummaryLine "Config" $envFile
        Write-HflSummaryLine "Uninstall" (Join-Path $InstallRoot "uninstall.cmd")
        Write-HflFooter -Outcome install
      }
      Stop-HflInstallLog -ExitCode 0
      return
    }

    Install-HflService -ExePath (Join-Path $InstallRoot "hfl-agent.exe") -DataRoot $dataRoot -NoStart:$NoStart

    if (-not $NoStart) {
      Complete-LegacyMigration
    }
    if ($QuietFooter) {
      Stop-HflInstallLog -ExitCode 0
      return
    }

    Write-HflSection "Verifying"
    if ($NoStart) {
      Write-HflSkip "Agent managed startup was not started by request"
    }
    else {
      Write-HflOk "Agent managed startup is $(Get-HflServiceStatusLine)"
    }
    Write-HflSection "Installation summary"
    Write-HflSummaryLine "Status" "installed"
    Write-HflSummaryLine "Binary" (Join-Path $InstallRoot "hfl-agent.exe")
    Write-HflSummaryLine "Config" $envFile
    if ($NoStart) {
      Write-HflSummaryLine "Lifecycle" "$ServiceName (not started)"
      $nextCommand = if ($InstallationMode -ne "system") {
        "& `"$(Join-Path $InstallRoot 'install.cmd')`" start"
      }
      else {
        "Start-Service $ServiceName"
      }
      Write-HflSummaryLine "Next" $nextCommand
    }
    else {
      Write-HflSummaryLine "Lifecycle" "$ServiceName ($(Get-HflServiceStatusLine))"
    }
    Write-HflSummaryLine "Uninstall" (Join-Path $InstallRoot "uninstall.cmd")
    Write-HflFooter -Outcome install
    Stop-HflInstallLog -ExitCode 0
  }
  catch {
    Restore-LegacyServiceOnFailure
    Write-HflLog -Level 'FAIL ' -Message "Installation failed: $($_.Exception.Message)"
    Stop-HflInstallLog -ExitCode 1
    throw
  }
}

function Invoke-Upgrade {
  $legacyUpgrade = $false
  if (-not (Test-Installed) -and (Test-LegacyLayout)) {
    $legacyUpgrade = $true
  }
  elseif (-not (Test-Installed)) {
    throw "Agent not installed. Use: install.cmd"
  }
  if (-not $From) {
    throw "upgrade requires -From <directory-or.zip>"
  }
  if ($AgentOnly -or $KopiaOnly) {
    throw "Transactional upgrade requires the complete Agent package; -AgentOnly and -KopiaOnly are not supported."
  }
  if ($NoService -and $NoRestart) {
    throw "-NoRestart cannot be combined with -NoService because there is no managed lifecycle to complete deferred verification."
  }

  $null = Get-HflSupportedArchitecture
  $dataRoot = if ($legacyUpgrade) { $DefaultDataRoot } else { Get-ResolvedDataRoot -Override $DataDir }
  $envFile = Get-HflEnvFile $dataRoot
  $workspace = Get-UpgradeWorkspace -DataRoot $dataRoot
  $prevVer = "unknown"
  if (Test-Path -LiteralPath $InstalledVersionFile) {
    $prevVer = (Get-Content -LiteralPath $InstalledVersionFile -Raw).Trim()
  }
  elseif ($legacyUpgrade -and (Test-Path -LiteralPath (Join-Path $legacyInstallRoot "INSTALLED_VERSION"))) {
    $prevVer = (Get-Content -LiteralPath (Join-Path $legacyInstallRoot "INSTALLED_VERSION") -Raw).Trim()
  }

  $upgradeSucceeded = $false
  Start-HflInstallLog -DataRoot $dataRoot
  try {
    Acquire-HflLifecycleLock -DataRoot $dataRoot -Operation "upgrade"
    $existingStatePath = Join-Path (Get-HflLifecycleRoot $dataRoot) "upgrade-state.json"
    if (Test-Path -LiteralPath $existingStatePath) {
      $existingState = Get-Content -Raw -LiteralPath $existingStatePath | ConvertFrom-Json
      $null = Restore-HflInterruptedUpgrade -DataRoot $dataRoot -State $existingState
      $prevVer = "unknown"
      if (Test-Path -LiteralPath $InstalledVersionFile) {
        $prevVer = (Get-Content -LiteralPath $InstalledVersionFile -Raw).Trim()
      }
    }
    if ($legacyUpgrade) {
      Invoke-LegacyMigration
    }
    $srcRoot = Resolve-UpgradeSource -Path $From -DataRoot $dataRoot
    $newVer = Get-BundleVersionFrom -Root $srcRoot
	$script:UpgradeLifecycleWasRunning = $false
	$script:UpgradeStateSnapshotReady = $false
	$script:UpgradeDeploymentStarted = $false
	$script:UpgradeStopAttempted = $false
    $script:UpgradePreviousVersion = $prevVer
    $script:UpgradeTargetVersion = $newVer
    $script:UpgradeOperationStateCreated = $true
    Write-HflUpgradeState -DataRoot $dataRoot -Phase "package_resolved"

    $installedRole = Read-HflEnvValue -EnvFile $envFile -Key "HFL_NODE_ROLE"
    $versionComparison = Compare-HflVersion -Left $newVer -Right $prevVer
    if ($newVer -eq $prevVer -and $newVer -ne "unknown") {
      Confirm-HflSameVersionUpgrade -Version $prevVer
    }
    elseif ($null -ne $versionComparison -and $versionComparison -lt 0 -and $prevVer -ne "unknown" -and $newVer -ne "unknown") {
      throw "Downgrade is not supported ($newVer < $prevVer)."
    }
    $effectiveRole = if ($installedRole) { $installedRole } else { "agent" }
    Verify-HflUpgradePackage -Root $srcRoot -RoleName $effectiveRole -Version $newVer
    Update-HflLifecycleLockTarget -Version $newVer -Manifest (Join-Path $srcRoot "MANIFEST.json")

    if (-not $QuietFooter) {
      Write-HflBanner "upgrade $prevVer -> $newVer" -RoleName (Get-HflRoleDisplayName -Value $installedRole)
      Write-HflSection "Target"
      Write-HflSummaryLine "Current version" $prevVer
      Write-HflSummaryLine "Target version" $newVer
      Write-HflSummaryLine "Install path" $InstallRoot
      Write-HflSummaryLine "Data path" $dataRoot
      Write-HflSummaryLine "Package source" $From
      Write-HflSection "Preflight checks"
      Write-HflOk "Upgrade source and rollback paths are ready"
      Write-HflSection "Upgrading Agent"
    }

    $script:UpgradeStateSnapshotReady = $false
    $script:UpgradeDeploymentStarted = $false
    $script:UpgradeStopAttempted = $false
  $script:UpgradeLifecycleWasRunning = Test-HflLifecycleRunning
  Backup-RollbackBinaries -DataRoot $dataRoot
    $script:UpgradePreviousVersion = $prevVer
    $script:UpgradeTargetVersion = $newVer
    $script:UpgradeTransactionActive = $true
    try {
      Write-HflUpgradeState -DataRoot $dataRoot -Phase "snapshotting"
      Backup-AgentConfigAndDb -DataRoot $dataRoot -PreviousVersion $prevVer -SrcRoot $srcRoot
      $script:UpgradeStateSnapshotReady = $true
      Write-HflUpgradeState -DataRoot $dataRoot -Phase "state_snapshotted"
      # Capture configuration and SQLite sidecar files before stopping the
      # Agent. A stop failure must remain non-destructive, while a later
      # deployment failure still has a complete rollback snapshot.
      $script:UpgradeStopAttempted = $true
      Write-HflUpgradeState -DataRoot $dataRoot -Phase "stopping"
      Stop-AgentForUpgrade
      Write-HflUpgradeState -DataRoot $dataRoot -Phase "service_stopped"
      $script:UpgradeDeploymentStarted = $true
      Write-HflUpgradeState -DataRoot $dataRoot -Phase "deploying"
      Deploy-Binaries -SrcRoot $srcRoot
      Write-HflUpgradeState -DataRoot $dataRoot -Phase "deployed"
      Merge-AgentEnv -EnvFile $envFile -DataRoot $dataRoot
      Write-HflUpgradeState -DataRoot $dataRoot -Phase "migrating"
      Update-AgentDb -DataRoot $dataRoot
      Write-HflUpgradeState -DataRoot $dataRoot -Phase "migrated"

      if (-not $NoService) {
        Write-HflUpgradeState -DataRoot $dataRoot -Phase "configuring_service"
        if (-not $NoRestart -and $script:UpgradeLifecycleWasRunning) {
          Write-HflUpgradeState -DataRoot $dataRoot -Phase "starting_service"
        }
        Install-HflService `
          -ExePath (Join-Path $InstallRoot "hfl-agent.exe") `
          -DataRoot $dataRoot `
          -NoStart:($NoRestart -or -not $script:UpgradeLifecycleWasRunning)
        # Re-registering uses the product defaults. Preserve the pre-upgrade
        # policy so a manually started disabled Agent does not become automatic.
        Restore-HflLifecycleStartupPolicy -DataRoot $dataRoot
      }

      if (-not $NoRestart -and -not (Test-HflLocalUpgradeHealth -DataRoot $dataRoot -ExpectedVersion $newVer -SkipLifecycle:(-not $script:UpgradeLifecycleWasRunning))) {
        throw "new Agent failed local health verification after upgrade"
      }
      if (-not $NoRestart) { Write-HflUpgradeState -DataRoot $dataRoot -Phase "healthy" }
    }
    catch {
      $originalError = $_.Exception.Message
      Invoke-HflUpgradeRollback -DataRoot $dataRoot -PreviousVersion $prevVer -OriginalError $originalError -LegacyUpgrade:$legacyUpgrade
    }

    $script:UpgradeTransactionActive = $false
    if ($NoRestart) {
      Write-HflUpgradeState -DataRoot $dataRoot -Phase "awaiting_restart"
    } else {
      Write-HflUpgradeState -DataRoot $dataRoot -Phase "committed"
      Remove-UpgradeRollback -DataRoot $dataRoot
      Clear-HflUpgradeState
    }
    $upgradeSucceeded = $true
  }
  catch {
    Restore-LegacyServiceOnFailure
    Write-HflLog -Level 'FAIL ' -Message "Upgrade failed: $($_.Exception.Message)"
    if ($script:UpgradeOperationStateCreated -and -not $script:UpgradeStopAttempted) {
      Clear-HflUpgradeState
      Remove-UpgradeRollback -DataRoot $dataRoot
    }
    throw
  }
  finally {
    try { Remove-UpgradeWorkspace -Workspace $workspace }
    catch { Write-HflWarn "upgrade workspace cleanup was deferred: $($_.Exception.Message)" }
    finally { Release-HflLifecycleLock }
    if (-not $upgradeSucceeded) {
      Stop-HflInstallLog -ExitCode 1
    }
  }

  if ($QuietFooter) {
    if (-not $NoRestart) {
      Complete-LegacyMigration
    }
    Stop-HflInstallLog -ExitCode 0
    return
  }

  if (-not $NoService -and -not $NoRestart) {
    Complete-LegacyMigration
  }
  Write-HflSection "Verifying"
  if ($NoRestart) {
    Write-HflSkip "Agent restart and local health verification are pending by request"
    Write-HflWarn "Rollback data is retained until the staged upgrade is started and verified."
  }
  elseif (-not $NoService) {
    Write-HflOk "Agent managed startup is $(Get-HflServiceStatusLine)"
  }
  Write-HflSection "Upgrade summary"
  Write-HflSummaryLine "Status" ($(if ($NoRestart) { "staged (verification pending)" } else { "upgraded" }))
  Write-HflSummaryLine "Version" (Get-BundleVersionFrom -Root $InstallRoot)
  if (-not $NoService) {
    Write-HflSummaryLine "Lifecycle" "$ServiceName ($(Get-HflServiceStatusLine))"
  }
  Write-HflFooter -Outcome upgrade
  Stop-HflInstallLog -ExitCode 0
}

function Invoke-Uninstall {
  $dataRoot = Get-ResolvedDataRoot -Override $DataDir
  if ($PurgeAll -and -not (Test-SafeDataPath $dataRoot)) {
    throw "Refusing PurgeAll for unexpected data directory $dataRoot."
  }
  $envFile = Get-HflEnvFile $DefaultDataRoot
  $nodeId = Read-HflEnvValue -EnvFile $envFile -Key "HFL_NODE_ID"
  $installedRole = Read-HflEnvValue -EnvFile $envFile -Key "HFL_NODE_ROLE"
  $displayRole = Get-HflRoleDisplayName -Value $installedRole
  $installedVersion = "unknown"
  if (Test-Path -LiteralPath $InstalledVersionFile) {
    $installedVersion = (Get-Content -LiteralPath $InstalledVersionFile -Raw).Trim()
  }
  Start-HflUninstallLog -DataRoot $dataRoot
  try {
  Acquire-HflLifecycleLock -DataRoot $dataRoot -Operation "uninstall"

  Write-HflBanner "uninstall" -RoleName $displayRole
  Write-HflSection "Target"
  Write-HflSummaryLine "Role" $displayRole
  if ($nodeId) { Write-HflSummaryLine "Node ID" $nodeId }
  Write-HflSummaryLine "Agent version" $installedVersion
  Write-HflSummaryLine "Service state" (Get-HflServiceStatusLine)
  Write-HflSummaryLine "Install path" $InstallRoot
  Write-HflSummaryLine "Data path" $dataRoot
  Write-HflSummaryLine "Data removal" ($(if ($PurgeAll) { "Remove Agent data" } else { "Preserve Agent data" }))

  Write-HflSection "Preflight checks"
  if ($PurgeAll -and $KeepInstallationIdentity) {
    throw "-PurgeAll and -KeepInstallationIdentity are mutually exclusive."
  }

  $agentBinary = Join-Path $InstallRoot "hfl-agent.exe"
  if ((-not $PurgeAll) -and (-not $KeepInstallationIdentity) -and
      (-not (Test-Path -LiteralPath $agentBinary))) {
    throw "Cannot retire the installation identity because $agentBinary is unavailable."
  }
  Write-HflOk "Installed Agent paths and data policy were verified"

  Write-HflSection "Uninstalling"
  Remove-HflService
  Stop-HflAgentProcesses -Reason "uninstall"

  if ((-not $PurgeAll) -and (-not $KeepInstallationIdentity)) {
    Write-HflLog -Level 'STEP ' -Message "Retiring the local installation identity."
    $retireOutput = @(& $agentBinary config retire-installation --data-dir $dataRoot 2>&1)
    foreach ($line in $retireOutput) {
      $text = [string]$line
      Write-HflDetailLine $text
      if (-not $QuietFooter) { Write-Host $text }
    }
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to retire the local installation identity; Agent files and data were preserved for retry."
    }
    Write-HflOk "Local installation identity retired; the existing console record is preserved and the next installation will register a new record."
  }
  elseif ($KeepInstallationIdentity) {
    Write-HflSkip "installation identity preserved for install retry"
  }

  Remove-HflInstallFile (Join-Path $InstallRoot "hfl-agent.exe")
  Remove-HflInstallFile (Join-Path $InstallRoot "hfl-agent-user-launcher.exe")
  Remove-HflInstallFile (Join-Path $InstallRoot "kopia.exe")
  Remove-HflInstallFile (Join-Path $InstallRoot "run-agent.ps1")
  Remove-HflInstallFile (Join-Path $InstallRoot "install.ps1")
  Remove-HflInstallFile (Join-Path $InstallRoot "uninstall.cmd")
  Remove-HflInstallFile $ManifestFile
  Remove-HflInstallFile $InstalledVersionFile
  Write-HflSkip "remove $(Join-Path $InstallRoot 'install.cmd') (deferred; install.cmd is running this script)"

  if ($PurgeAll) {
    Remove-HflInstallFile $envFile
  }
  elseif ($KeepInstallationIdentity) {
    Write-HflSkip "remove $envFile (preserved with installation identity for install retry)"
  }
  else {
    Write-HflSkip "remove $envFile (preserved without installation identity; use -PurgeAll)"
  }

  $uninstallLogPath = $script:HflUninstallLogPath
  if (-not $PurgeAll) {
    Write-HflSkip "remove data directory $dataRoot (preserved; use -PurgeAll)"
  }
  elseif ((Test-SafeDataPath $dataRoot) -and (Test-Path -LiteralPath $dataRoot)) {
    Remove-Item -Recurse -Force -LiteralPath $dataRoot
    Write-HflOk "removed data directory $dataRoot"
  }
  elseif ($dataRoot) {
    Write-HflWarn "HFL_DATA_DIR ($dataRoot) is outside the approved Agent data directory; not deleted"
  }
  else {
    Write-HflSkip "remove data directory (none resolved)"
  }

  # PurgeAll removes the data directory that owns uninstall.log. The detached
  # install-root remover must never recreate that directory after cleanup.
  $uninstallLog = if (-not $PurgeAll -and $uninstallLogPath) { $uninstallLogPath } else { "" }
  Schedule-InstallRootRemoval -InstallRoot $InstallRoot -LogFile $uninstallLog

  Write-HflSection "Verifying"
  Write-HflOk "Agent service and installed files were removed"
  Write-HflSection "Uninstallation summary"
  Write-HflSummaryLine "Status" "uninstalled"
  Write-HflSummaryLine "Console record" "not changed by local uninstall"
  Write-HflFooter -Outcome uninstall
  Release-HflLifecycleLock
  Stop-HflUninstallLog -ExitCode 0
  }
  catch {
    Release-HflLifecycleLock
    Write-HflLog -Level 'FAIL ' -Message "Uninstallation failed: $($_.Exception.Message)"
    Stop-HflUninstallLog -ExitCode 1
    throw
  }
}

function Invoke-Status {
  $envFile = Get-HflEnvFile $DefaultDataRoot
  Write-HflBanner "status"
  Write-HflSection "Status"
  if (Test-Installed) {
    $installed = "unknown"
    if (Test-Path -LiteralPath $InstalledVersionFile) {
      $installed = (Get-Content -LiteralPath $InstalledVersionFile -Raw).Trim()
    }
    $nodeId = Read-HflEnvValue -EnvFile $envFile -Key "HFL_NODE_ID"
    $wss = Read-HflEnvValue -EnvFile $envFile -Key "HFL_WSS_URL"
    Write-HflSummaryLine "installed" "yes"
    Write-HflSummaryLine "version" $installed
    Write-HflSummaryLine "bundle" (Get-BundleVersion)
    Write-HflSummaryLine "install dir" $InstallRoot
    Write-HflSummaryLine "data dir" (Get-ResolvedDataRoot -Override "")
    Write-HflSummaryLine "service" "$ServiceName ($(Get-HflServiceStatusLine))"
    if ($nodeId) { Write-HflSummaryLine "node id" $nodeId }
    Write-HflSummaryLine "wss" ($(if ($wss) { "configured" } else { "not configured" }))
  }
  else {
    Write-HflSummaryLine "installed" "no"
    Write-HflSummaryLine "bundle" (Get-BundleVersion)
  }
  Write-HflFooter -Outcome status
}

Assert-HflInstallationIdentity

switch ($Command) {
  "install" { Invoke-Install }
  "start" { Invoke-Start }
  "stop" { Invoke-Stop }
  "restart" { Invoke-Restart }
  "status" { Invoke-Status }
  "upgrade" { Invoke-Upgrade }
  "uninstall" { Invoke-Uninstall }
}
