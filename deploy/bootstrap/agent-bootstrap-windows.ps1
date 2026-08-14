# HyperFileLens Agent enrollment bootstrap (Windows). Rendered by GET /enrollment/bootstrap.
$ErrorActionPreference = "Stop"
# Keep a stable working directory if the caller launched from a removed install path.
Set-Location -LiteralPath $env:SystemDrive\

function Test-HflAdmin {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-HflBootstrapLog {
  param(
    [Parameter(Mandatory = $true)][string]$Level,
    [Parameter(Mandatory = $true)][string]$Message
  )
  if ($Message -notmatch '[.!?]$') { $Message = "$Message." }
  $status = switch ($Level.Trim()) {
    'OK' { ' OK '; break }
    'WARN' { 'WARN'; break }
    'FAIL' { 'FAIL'; break }
    'INFO' { 'INFO'; break }
    default { '....' }
  }
  Write-Host "  [$status] $Message"
}

function Format-HflBytes {
  param([Parameter(Mandatory = $true)][long]$Bytes)
  $units = @('B', 'KiB', 'MiB', 'GiB', 'TiB')
  $value = [double][Math]::Max(0, $Bytes)
  $unit = 0
  while ($value -ge 1024 -and $unit -lt ($units.Count - 1)) {
    $value /= 1024
    $unit++
  }
  if ($unit -eq 0) { return ('{0:N0} {1}' -f $value, $units[$unit]) }
  return ('{0:N1} {1}' -f $value, $units[$unit])
}

function Write-HflDownloadProgress {
  param(
    [Parameter(Mandatory = $true)][long]$Downloaded,
    [Parameter(Mandatory = $true)][long]$Total
  )
  if ($Total -gt 0) {
    $percent = [Math]::Min(100, [Math]::Round(($Downloaded * 100.0) / $Total))
    Write-Host -NoNewline ("`r  [....] Enrollment helper {0}% - {1} / {2}" -f `
        $percent, (Format-HflBytes $Downloaded), (Format-HflBytes $Total))
    return
  }
  Write-Host -NoNewline ("`r  [....] Enrollment helper - {0} downloaded" -f `
      (Format-HflBytes $Downloaded))
}

if (-not (Test-HflAdmin)) {
  Write-HflBootstrapLog "INFO " "Administrator privileges are required. Approve the UAC prompt to continue."
  $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath)
  $proc = Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -Verb RunAs -PassThru -Wait
  if ($null -eq $proc) {
    Write-HflBootstrapLog "FAIL " "Elevation was cancelled or failed."
    exit 1
  }
  exit $proc.ExitCode
}

$env:HFL_ORG_KEY = "__HFL_ORG_KEY__"
$env:HFL_NODE_ROLE = "__HFL_NODE_ROLE__"
$env:HFL_NODE_TOKEN = "__HFL_NODE_TOKEN__"
$env:HFL_API_BASE = "__HFL_API_BASE__"
$env:HFL_WSS_URL = "__HFL_WSS_URL__"
$env:HFL_INSECURE_TLS = "__HFL_INSECURE_TLS__"

function Get-HflEnrollmentBinary {
  param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$OutFile
  )
  $skipCert = ($env:HFL_INSECURE_TLS -ne '0')
  $partial = "$OutFile.part"
  Remove-Item -Force -LiteralPath $partial -ErrorAction SilentlyContinue
  Write-HflBootstrapLog ".... " "Downloading HyperFileLens enrollment helper."

  if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
    $curlArgs = @(
      '-fL', '--silent', '--show-error',
      '--retry', '3', '--retry-connrefused', '--retry-delay', '2',
      '-o', $partial, $Url
    )
    if ($skipCert) { $curlArgs = @('-k') + $curlArgs }
    & curl.exe @curlArgs
    if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $partial)) {
      Move-Item -Force -LiteralPath $partial -Destination $OutFile
      $size = (Get-Item -LiteralPath $OutFile).Length
      Write-HflBootstrapLog " OK  " "HyperFileLens enrollment helper downloaded ($(Format-HflBytes $size))."
      return
    }
    Remove-Item -Force -LiteralPath $partial -ErrorAction SilentlyContinue
    Write-HflBootstrapLog "WARN " "curl download failed (exit $LASTEXITCODE). Trying PowerShell instead."
  }

  try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    if ($skipCert) {
      [Net.ServicePointManager]::ServerCertificateValidationCallback = { [bool]1 }
    }
    $response = $null
    $inputStream = $null
    $outputStream = $null
    $total = [long]-1
    $downloaded = [long]0
    try {
      $request = [Net.HttpWebRequest]::Create($Url)
      $response = $request.GetResponse()
      $total = $response.ContentLength
      $inputStream = $response.GetResponseStream()
      $outputStream = [IO.File]::Open($partial, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
      $buffer = New-Object byte[] (1024 * 1024)
      $lastUpdate = [DateTime]::UtcNow
      while (($read = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
        $outputStream.Write($buffer, 0, $read)
        $downloaded += $read
        $now = [DateTime]::UtcNow
        if (($now - $lastUpdate).TotalSeconds -ge 1) {
          Write-HflDownloadProgress -Downloaded $downloaded -Total $total
          $lastUpdate = $now
        }
      }
    }
    finally {
      if ($null -ne $outputStream) { $outputStream.Dispose() }
      if ($null -ne $inputStream) { $inputStream.Dispose() }
      if ($null -ne $response) { $response.Dispose() }
    }
    if ($total -ge 0 -and $downloaded -ne $total) {
      throw "Download size mismatch: received $downloaded bytes, expected $total."
    }
    Write-HflDownloadProgress -Downloaded $downloaded -Total $total
    Write-Host
    Move-Item -Force -LiteralPath $partial -Destination $OutFile
    Write-HflBootstrapLog " OK  " "HyperFileLens enrollment helper downloaded ($(Format-HflBytes $downloaded))."
  }
  catch {
    Remove-Item -Force -LiteralPath $partial -ErrorAction SilentlyContinue
    Write-HflBootstrapLog "FAIL " "Failed to download the enrollment tool: $($_.Exception.Message)"
    throw
  }
}

$nativeArch = if ($env:PROCESSOR_ARCHITEW6432) {
  $env:PROCESSOR_ARCHITEW6432
}
else {
  $env:PROCESSOR_ARCHITECTURE
}
$archRel = switch ($nativeArch) {
  "AMD64" { "amd64"; break }
  "ARM64" {
    Write-HflBootstrapLog "FAIL " "Windows ARM64 is not supported by this release."
    exit 4
  }
  "x86" {
    Write-HflBootstrapLog "FAIL " "32-bit Windows is not supported by this release."
    exit 4
  }
  default {
    Write-HflBootstrapLog "FAIL " "Unsupported Windows architecture $nativeArch."
    exit 4
  }
}
$bin = Join-Path $env:TEMP ("hfl-enroll-" + [guid]::NewGuid().ToString("n") + ".exe")

$enrollUrl = "$($env:HFL_API_BASE)/media/enroll-bootstrap/hfl-enroll-windows-$archRel.exe"
$exitCode = 3
try {
  Get-HflEnrollmentBinary -Url $enrollUrl -OutFile $bin
  & $bin install @args
  $exitCode = $LASTEXITCODE
}
finally {
  if (Test-Path -LiteralPath $bin) {
    Remove-Item -Force -LiteralPath $bin -ErrorAction SilentlyContinue
  }
  if (Test-Path -LiteralPath "$bin.part") {
    Remove-Item -Force -LiteralPath "$bin.part" -ErrorAction SilentlyContinue
  }
}
exit $exitCode
