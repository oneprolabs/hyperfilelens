@echo off
REM HyperFileLens Agent - double-click uninstall (UAC + confirmation dialog).
setlocal EnableExtensions
set "INSTALL_DIR=%~dp0"
REM The unified user Agent Root keeps binaries and all runtime siblings below
REM %LOCALAPPDATA%\HyperFileLens\Agent.  Detect the mode from the installed
REM bin directory so current-user uninstall does not request elevation.
set "USER_INSTALL_DIR=%LOCALAPPDATA%\HyperFileLens\Agent\bin\"
set "HFL_USER_MODE=0"
if /I "%INSTALL_DIR%"=="%USER_INSTALL_DIR%" (
  set "HFL_USER_MODE=1"
  set "HFL_INSTALLATION_MODE=user"
)

if "%HFL_USER_MODE%"=="0" if /I not "%~1"=="__elevated__" (
  net session >nul 2>&1
  if errorlevel 1 (
    powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '__elevated__' -Verb RunAs -Wait"
    exit /b %ERRORLEVEL%
  )
)

call :AppendUninstallLog "uninstall.cmd started (user_mode=%HFL_USER_MODE%)"
if errorlevel 1 (
  echo.
  echo WARNING: could not write uninstall.log under ProgramData.
)

powershell.exe -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; $msg = 'This will remove HyperFileLens Agent, its managed startup entry, and local agent data from this computer.' + [char]10 + [char]10 + 'Continue?'; $ans = [System.Windows.Forms.MessageBox]::Show($msg, 'Uninstall HyperFileLens Agent', 'YesNo', 'Warning', 'Button2'); if ($ans -ne 'Yes') { exit 2 }"
set "CONFIRM_EC=%ERRORLEVEL%"
if %CONFIRM_EC% equ 2 (
  call :AppendUninstallLog "uninstall cancelled by user (confirmation dialog)"
  exit /b 0
)
if not %CONFIRM_EC% equ 0 (
  call :AppendUninstallLog "confirmation dialog failed exit=%CONFIRM_EC%"
  echo.
  echo ERROR: confirmation dialog failed.
  pause
  exit /b 1
)

call :AppendUninstallLog "confirmation accepted; invoking install.cmd uninstall"
cd /d "%INSTALL_DIR%"
call "%INSTALL_DIR%install.cmd" uninstall
set "EC=%ERRORLEVEL%"
call :AppendUninstallLog "install.cmd uninstall finished exit=%EC%"
cd /d "%TEMP%"
echo.
if %EC% equ 0 (
  echo Uninstall finished. Return to the HyperFileLens console if the node still appears.
) else (
  echo Uninstall failed (exit code %EC%). Check the Agent data directory logs\uninstall.log
)
pause
exit /b %EC%

:AppendUninstallLog
set "LOG_MSG=%~1"
powershell.exe -NoProfile -Command ^
  "$data=if ($env:HFL_INSTALLATION_MODE -eq 'user') { $env:LOCALAPPDATA + '\HyperFileLens\Agent' } else { $env:ProgramData + '\HyperFileLens\Agent' };" ^
  "$envFile=Join-Path (Join-Path $data 'config') 'agent.env';" ^
  "if (Test-Path -LiteralPath $envFile) { Get-Content -LiteralPath $envFile | ForEach-Object { if ($_ -match '^\s*HFL_DATA_DIR=(.+)$') { $data=$Matches[1].Trim() } } };" ^
  "$logDir=Join-Path $data 'logs';" ^
  "New-Item -ItemType Directory -Force -Path $logDir | Out-Null;" ^
  "$log=Join-Path $logDir 'uninstall.log';" ^
  "$ts=[DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ');" ^
  "Add-Content -LiteralPath $log -Value ($ts + ' ' + $env:LOG_MSG) -Encoding UTF8" 2>nul
exit /b 0
