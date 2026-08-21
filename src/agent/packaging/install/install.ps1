<#
.SYNOPSIS
  HyperFileLens Agent bundle installer (Windows).

  After install, install.cmd / install.ps1 and MANIFEST.json are copied to the install directory
  for local uninstall/status. Upgrade still requires a fresh release archive (or remote agent.upgrade).
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
  [string]$Role = "agent",
  [string]$From = "",
  [switch]$NoService,
  [switch]$NoStart,
  [switch]$PurgeAll,
  [switch]$KeepInstallationIdentity,
  [switch]$AgentOnly,
  [switch]$KopiaOnly,
  [switch]$NoRestart,
  [switch]$Help,
  [switch]$QuietFooter
)

$ErrorActionPreference = "Stop"
$BundleRoot = $PSScriptRoot
$userInstallRoot = Join-Path $env:LOCALAPPDATA "Programs\HyperFileLens\Agent"
$InstallationMode = if ($env:HFL_INSTALLATION_MODE) {
  $env:HFL_INSTALLATION_MODE.Trim().ToLowerInvariant()
}
elseif ($BundleRoot.TrimEnd('\') -eq $userInstallRoot.TrimEnd('\')) {
  "user"
}
else {
  "system"
}
if ($InstallationMode -notin @("system", "user")) {
  throw "HFL_INSTALLATION_MODE must be system or user."
}
$ServiceName = "HyperFileLensAgent"
if ($InstallationMode -eq "user") {
  $InstallRoot = $userInstallRoot
  $DefaultDataRoot = Join-Path $env:LOCALAPPDATA "HyperFileLens\AgentData"
}
else {
  $InstallRoot = Join-Path $env:ProgramFiles "HyperFileLens\Agent"
  $DefaultDataRoot = Join-Path $env:ProgramData "HyperFileLens\Agent"
}
$InstalledVersionFile = Join-Path $InstallRoot "INSTALLED_VERSION"
$TaskName = "HyperFileLensAgent"
$LifecycleLabel = if ($InstallationMode -eq "user") { "current-user task" } else { "Windows service" }
$CurrentWindowsIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name

function Test-HflAdministrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-HflInstallationIdentity {
  $elevated = Test-HflAdministrator
  if ($InstallationMode -eq "user" -and $Role -ne "agent") {
    throw "User-level installation is only available for Source Agent."
  }
  if ($InstallationMode -eq "user" -and $elevated) {
    throw "User-level installation must run without UAC elevation."
  }
  if ($InstallationMode -eq "system" -and -not $elevated) {
    throw "Administrator privileges are required for system-level installation."
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
                          Extracts to DATA_DIR/runtime/workspace, merges missing agent.env keys,
                          migrates agent.db schema, overwrites binaries; removes workspace on success

  uninstall:
    -PurgeAll                   Remove data directory and agent.env
    -KeepInstallationIdentity   Keep agent.env installation identity (incomplete-install rollback)

Install paths:
  $InstallRoot         Binaries and installer scripts
  $DefaultDataRoot     Runtime data, backup, and configuration
  $DefaultDataRoot\backup\state\  Pre-upgrade agent.env/agent.db snapshot (retained)
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
  $script:HflInstallLogPath = Join-Path (Join-Path $DataRoot "logs") "install.log"
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
  $script:HflUninstallLogPath = Join-Path (Join-Path $DataRoot "logs") "uninstall.log"
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
      Write-HflDisplayLine "Upgrade completed successfully"
      Write-HflDisplayLine ("=" * 64)
      Write-HflDisplayLine "  HyperFileLens Agent upgraded successfully."
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
  if ($InstallationMode -eq "user") {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) { return "not installed" }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $info) { return $task.State.ToString().ToLowerInvariant() }
    return "$($task.State.ToString().ToLowerInvariant()) (current-user task)"
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
    $manifest = Join-Path $InstallRoot "MANIFEST.json"
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
    (Test-Path -LiteralPath (Join-Path $Root "bin\hfl-agent.exe"))
}

function Get-UpgradeWorkspace {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
  return Join-Path $DataRoot "runtime\workspace"
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
    [string]$PreviousVersion = "unknown"
  )
  $stateDir = Join-Path $DataRoot "backup\state"
  $archive = Join-Path $stateDir "latest.zip"
  $meta = Join-Path $DataRoot "backup\meta.json"
  $names = @("agent.env", "agent.db", "agent.db-wal", "agent.db-shm")
  $items = @()
  foreach ($name in $names) {
    $path = Join-Path $DataRoot $name
    if (Test-Path -LiteralPath $path) { $items += $path }
  }
  New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
  if ($items.Count -eq 0) {
    Write-HflSkip "backup agent.env/agent.db (nothing to back up)"
    return
  }
  $tempDir = Join-Path $env:TEMP "hfl-agent-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
  New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
  try {
    foreach ($path in $items) {
      Copy-Item -LiteralPath $path -Destination (Join-Path $tempDir (Split-Path -Leaf $path)) -Force
    }
    Compress-Archive -Path (Join-Path $tempDir '*') -DestinationPath $archive -Force
    Write-HflOk "backed up agent.env/agent.db -> $archive"
    $createdAt = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    @"
{
  "created_at": "$createdAt",
  "previous_version": "$PreviousVersion",
  "state_archive": "backup/state/latest.zip"
}
"@ | Set-Content -LiteralPath $meta -Encoding UTF8
    Write-HflOk "wrote $meta"
  }
  catch {
    Write-HflWarn "backup agent.env/agent.db failed (agent may still be running): $($_.Exception.Message)"
    Write-HflWarn "backup skipped; continuing upgrade"
  }
  finally {
    Remove-Item -Recurse -Force -LiteralPath $tempDir -ErrorAction SilentlyContinue
  }
}

$script:UpgradeBinBackup = ""

function Backup-RollbackBinaries {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
  $script:UpgradeBinBackup = Join-Path $DataRoot "backup\rollback\bin"
  $rollbackRoot = Join-Path $DataRoot "backup\rollback"
  if (Test-Path -LiteralPath $rollbackRoot) {
    Remove-Item -Recurse -Force -LiteralPath $rollbackRoot
  }
  New-Item -ItemType Directory -Force -Path $script:UpgradeBinBackup | Out-Null
  foreach ($name in @("hfl-agent.exe", "kopia.exe", "MANIFEST.json", "INSTALLED_VERSION")) {
    $src = Join-Path $InstallRoot $name
    if (Test-Path -LiteralPath $src) {
      Copy-Item -LiteralPath $src -Destination (Join-Path $script:UpgradeBinBackup $name) -Force
    }
  }
  Write-HflOk "backed up binaries -> $($script:UpgradeBinBackup)"
}

function Restore-RollbackBinaries {
  if ([string]::IsNullOrWhiteSpace($script:UpgradeBinBackup) -or -not (Test-Path -LiteralPath $script:UpgradeBinBackup)) {
    return
  }
  foreach ($name in @("hfl-agent.exe", "kopia.exe", "MANIFEST.json", "INSTALLED_VERSION")) {
    $src = Join-Path $script:UpgradeBinBackup $name
    if (Test-Path -LiteralPath $src) {
      Copy-Item -LiteralPath $src -Destination (Join-Path $InstallRoot $name) -Force
    }
  }
  Write-HflWarn "restored binaries from $($script:UpgradeBinBackup)"
}

function Remove-UpgradeRollback {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
  $rollback = Join-Path $DataRoot "backup\rollback"
  if (Test-Path -LiteralPath $rollback) {
    Remove-Item -Recurse -Force -LiteralPath $rollback
    Write-HflOk "removed $rollback (upgrade succeeded; state snapshot retained)"
  }
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
    HFL_INSTALLATION_MODE = $InstallationMode
    HFL_KOPIA_PATH        = $kopiaPath
    HFL_INSECURE_TLS      = "1"
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
  if ($added.Count -gt 0) {
    Set-Content -Path $EnvFile -Value ($lines -join "`n") -Encoding UTF8
    Write-HflOk "merged agent.env keys: $($added -join ', ')"
  }
  else {
    Write-HflOk "agent.env unchanged (no missing keys)"
  }
}

function Ensure-HflLogsDir {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
  $logDir = Join-Path $DataRoot "logs"
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  # HyperFileLensAgent runs as LocalSystem; upgrade scripts may create logs as admin.
  icacls $logDir /grant "SYSTEM:(OI)(CI)M" /Q 2>$null | Out-Null
  icacls $logDir /grant "*S-1-5-32-544:(OI)(CI)M" /Q 2>$null | Out-Null
  $agentLog = Join-Path $logDir "agent.log"
  if (-not (Test-Path -LiteralPath $agentLog)) {
    New-Item -ItemType File -Force -Path $agentLog | Out-Null
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
    Write-HflWarn "agent.db migration check failed (service start may retry)"
  }
}

function Get-BundleVersion {
  return Get-BundleVersionFrom -Root $BundleRoot
}

function Test-Installed {
  return Test-Path -LiteralPath (Join-Path $InstallRoot "hfl-agent.exe")
}

function Get-ResolvedDataRoot {
  param([string]$Override)
  if ($Override) { return $Override }
  $candidates = @(
    (Join-Path $DefaultDataRoot "agent.env")
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
  if ($InstallationMode -eq "user") {
    $expected = [System.IO.Path]::GetFullPath($DefaultDataRoot)
    return $full.TrimEnd('\').Equals(
      $expected.TrimEnd('\'),
      [System.StringComparison]::OrdinalIgnoreCase
    )
  }
  $base = $env:ProgramData
  $allowedRoot = Join-Path ([System.IO.Path]::GetFullPath($base)) "HyperFileLens"
  return $full.TrimEnd('\').StartsWith(
    $allowedRoot.TrimEnd('\') + '\',
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
  if ($InstallationMode -eq "user") {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
      Write-HflSkip "stop current-user task $TaskName (not installed)"
      return
    }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-HflOk "stopped current-user task $TaskName"
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
  foreach ($name in @("hfl-agent", "kopia")) {
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
      $procs = @(Get-Process -Name $name -ErrorAction SilentlyContinue)
      if ($procs.Count -eq 0) { break }
      foreach ($proc in $procs) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
      }
      Start-Sleep -Milliseconds 500
    }
    if (Get-Process -Name $name -ErrorAction SilentlyContinue) {
      Write-HflWarn "process $name still running after stop attempts ($Reason)"
    }
    else {
      Write-HflOk "stopped $name process(es) ($Reason)"
    }
  }
}

function Stop-AgentForUpgrade {
  Stop-HflService
  $proc = Get-Process -Name "hfl-agent" -ErrorAction SilentlyContinue
  if ($null -ne $proc) {
    Stop-Process -Name "hfl-agent" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Write-HflOk "stopped hfl-agent process (pre-upgrade)"
  }
}

function Remove-HflService {
  if ($InstallationMode -eq "user") {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
      Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
      Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
      Write-HflOk "removed current-user task $TaskName"
    }
    else {
      Write-HflSkip "remove current-user task $TaskName (not installed)"
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
    $action = New-ScheduledTaskAction `
      -Execute $ExePath `
      -Argument "run -data-dir `"$DataRoot`""
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
  if ($InstallationMode -eq "user") {
    if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
      Install-HflService `
        -ExePath (Join-Path $InstallRoot "hfl-agent.exe") `
        -DataRoot (Get-ResolvedDataRoot -Override "")
      return
    }
    Start-ScheduledTask -TaskName $TaskName
    Write-HflOk "started current-user task $TaskName ($(Get-HflServiceStatusLine))"
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
  Write-HflBanner "start"
  Write-HflSection "Actions"
  Start-HflServiceOnly
  Write-HflSection "Summary"
  Write-HflSummaryLine "Service" "$ServiceName ($(Get-HflServiceStatusLine))"
  Write-Host ""
  Write-Host "Done."
  Write-Host ""
}

function Invoke-Stop {
  Assert-HflInstalled
  Write-HflBanner "stop"
  Write-HflSection "Actions"
  Stop-HflService
  Write-HflSection "Summary"
  Write-HflSummaryLine "Service" "$ServiceName ($(Get-HflServiceStatusLine))"
  Write-Host ""
  Write-Host "Done."
  Write-Host ""
}

function Invoke-Restart {
  Assert-HflInstalled
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
    Copy-Item -Force -Path $srcUninstall -Destination $destUninstall
  }
  else {
    throw "Missing bundle uninstaller: $srcUninstall"
  }
  Write-HflOk "deployed $destUninstall"
  if (Test-Path -LiteralPath $srcManifest) {
    Copy-Item -Force -Path $srcManifest -Destination (Join-Path $InstallRoot "MANIFEST.json")
    Write-HflOk "deployed $(Join-Path $InstallRoot 'MANIFEST.json')"
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
  $cmd = @"
Start-Sleep -Seconds 2
Move-Item -LiteralPath '$Source' -Destination '$Destination' -Force
"@
  Start-Process -FilePath 'powershell.exe' `
    -ArgumentList @('-NoProfile', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-Command', $cmd) `
    | Out-Null
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
  Copy-Item -Force -LiteralPath $Source -Destination $Destination
  Write-HflOk "deployed $Destination"
}

function Deploy-Binaries {
  param([string]$SrcRoot = $BundleRoot)
  $srcAgent = Join-Path $SrcRoot "bin\hfl-agent.exe"
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
  New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
  $deployAgent = -not $KopiaOnly.IsPresent
  $deployKopia = -not $AgentOnly.IsPresent
  $ver = Get-BundleVersionFrom -Root $SrcRoot
  if ($deployAgent) {
    Copy-Item -Force -Path $srcAgent -Destination (Join-Path $InstallRoot "hfl-agent.exe")
    Write-HflOk "deployed $(Join-Path $InstallRoot 'hfl-agent.exe') ($ver)"
  }
  if ($deployKopia) {
    Copy-Item -Force -Path $srcKopia -Destination (Join-Path $InstallRoot "kopia.exe")
    Write-HflOk "deployed $(Join-Path $InstallRoot 'kopia.exe')"
  }
  Set-Content -Path $InstalledVersionFile -Value $ver -Encoding UTF8
  Write-HflOk "wrote $InstalledVersionFile ($ver)"
  Deploy-AdminScripts -SrcRoot $SrcRoot
}

function Write-AgentEnv {
  param([string]$EnvFile, [string]$DataRoot)
  Ensure-HflLogsDir -DataRoot $DataRoot
  $kopiaPath = Join-Path $InstallRoot "kopia.exe"
  $lines = @(
    "HFL_DATA_DIR=$DataRoot",
    "HFL_KOPIA_PATH=$kopiaPath",
    "HFL_NODE_ROLE=$Role",
    "HFL_INSTALLATION_MODE=$InstallationMode",
    "HFL_INSECURE_TLS=1"
  )
  if ($WssUrl) { $lines = @("HFL_WSS_URL=$WssUrl") + $lines }
  if ($ApiBase) { $lines += "HFL_API_BASE=$ApiBase" }
  if ($OrgKey) { $lines += "HFL_ORG_KEY=$OrgKey" }
  if ($NodeToken) { $lines += "HFL_NODE_TOKEN=$NodeToken" }
  if ($NodeId) { $lines += "HFL_NODE_ID=$NodeId" }
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

    Deploy-Binaries
    $envFile = Join-Path $dataRoot "agent.env"
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
      $nextCommand = if ($InstallationMode -eq "user") {
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
    Write-HflLog -Level 'FAIL ' -Message "Installation failed: $($_.Exception.Message)"
    Stop-HflInstallLog -ExitCode 1
    throw
  }
}

function Invoke-Upgrade {
  if (-not (Test-Installed)) {
    throw "Agent not installed. Use: install.cmd"
  }
  if (-not $From) {
    throw "upgrade requires -From <directory-or.zip>"
  }

  $null = Get-HflSupportedArchitecture
  $dataRoot = Get-ResolvedDataRoot -Override $DataDir
  $envFile = Join-Path $dataRoot "agent.env"
  $workspace = Get-UpgradeWorkspace -DataRoot $dataRoot
  $prevVer = "unknown"
  if (Test-Path -LiteralPath $InstalledVersionFile) {
    $prevVer = (Get-Content -LiteralPath $InstalledVersionFile -Raw).Trim()
  }

  $upgradeSucceeded = $false
  Start-HflInstallLog -DataRoot $dataRoot
  try {
    $srcRoot = Resolve-UpgradeSource -Path $From -DataRoot $dataRoot
    $newVer = Get-BundleVersionFrom -Root $srcRoot

    if (-not $QuietFooter) {
      $installedRole = Read-HflEnvValue -EnvFile $envFile -Key "HFL_NODE_ROLE"
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

    Backup-RollbackBinaries -DataRoot $dataRoot
    try {
      Stop-AgentForUpgrade
      Backup-AgentConfigAndDb -DataRoot $dataRoot -PreviousVersion $prevVer
      Deploy-Binaries -SrcRoot $srcRoot
      Merge-AgentEnv -EnvFile $envFile -DataRoot $dataRoot
      Update-AgentDb -DataRoot $dataRoot

      if (-not $NoService) {
        Install-HflService -ExePath (Join-Path $InstallRoot "hfl-agent.exe") -DataRoot $dataRoot -NoStart:$NoRestart
      }
    }
    catch {
      Write-HflWarn "upgrade failed: $($_.Exception.Message); attempting rollback"
      Restore-RollbackBinaries
      if (-not $NoService) {
        try {
          Start-HflServiceOnly
        }
        catch { }
      }
      throw
    }

    Remove-UpgradeRollback -DataRoot $dataRoot
    $upgradeSucceeded = $true
  }
  catch {
    Write-HflLog -Level 'FAIL ' -Message "Upgrade failed: $($_.Exception.Message)"
    throw
  }
  finally {
    Remove-UpgradeWorkspace -Workspace $workspace
    if (-not $upgradeSucceeded) {
      Stop-HflInstallLog -ExitCode 1
    }
  }

  if ($QuietFooter) {
    Stop-HflInstallLog -ExitCode 0
    return
  }

  Write-HflSection "Verifying"
  if (-not $NoService) {
    Write-HflOk "Agent managed startup is $(Get-HflServiceStatusLine)"
  }
  Write-HflSection "Upgrade summary"
  Write-HflSummaryLine "Status" "upgraded"
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
  $envFile = Join-Path $DefaultDataRoot "agent.env"
  $nodeId = Read-HflEnvValue -EnvFile $envFile -Key "HFL_NODE_ID"
  $installedRole = Read-HflEnvValue -EnvFile $envFile -Key "HFL_NODE_ROLE"
  $displayRole = Get-HflRoleDisplayName -Value $installedRole
  $installedVersion = "unknown"
  if (Test-Path -LiteralPath $InstalledVersionFile) {
    $installedVersion = (Get-Content -LiteralPath $InstalledVersionFile -Raw).Trim()
  }
  Start-HflUninstallLog -DataRoot $dataRoot
  try {

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
    Write-HflOk "Local installation identity retired; remove the old console record before reinstalling or changing run mode."
  }
  elseif ($KeepInstallationIdentity) {
    Write-HflSkip "installation identity preserved for install retry"
  }

  Remove-HflInstallFile (Join-Path $InstallRoot "hfl-agent.exe")
  Remove-HflInstallFile (Join-Path $InstallRoot "kopia.exe")
  Remove-HflInstallFile (Join-Path $InstallRoot "install.ps1")
  Remove-HflInstallFile (Join-Path $InstallRoot "uninstall.cmd")
  Remove-HflInstallFile (Join-Path $InstallRoot "MANIFEST.json")
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
  Stop-HflUninstallLog -ExitCode 0
  }
  catch {
    Write-HflLog -Level 'FAIL ' -Message "Uninstallation failed: $($_.Exception.Message)"
    Stop-HflUninstallLog -ExitCode 1
    throw
  }
}

function Invoke-Status {
  $envFile = Join-Path $DefaultDataRoot "agent.env"
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
