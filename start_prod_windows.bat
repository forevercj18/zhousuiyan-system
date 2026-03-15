@echo off
setlocal
set SCRIPT_DIR=%~dp0
set PS_SCRIPT=%SCRIPT_DIR%start_prod_windows.ps1

if not exist "%PS_SCRIPT%" (
  echo [ERROR] start_prod_windows.ps1 not found: "%PS_SCRIPT%"
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" %*
if errorlevel 1 (
  echo [ERROR] Production startup script failed.
  echo [HINT] Fill .env.prod first, then try start_prod_windows.bat
  exit /b 1
)
endlocal
