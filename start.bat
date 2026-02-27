@echo off
setlocal
set SCRIPT_DIR=%~dp0
set PS_SCRIPT=%SCRIPT_DIR%start.ps1

if not exist "%PS_SCRIPT%" (
  echo [ERROR] start.ps1 not found: "%PS_SCRIPT%"
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" %*
if errorlevel 1 (
  echo [ERROR] Startup script failed.
  echo [HINT] Try: powershell -ExecutionPolicy Bypass -File ".\start.ps1" -Acceptance -Port 9000
  exit /b 1
)
endlocal
