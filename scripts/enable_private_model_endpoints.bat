@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0enable_private_model_endpoints.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
  echo [OK] Private-network model endpoints are persistently enabled in the local .env file.
  echo [NEXT] Close every running GW-AP Backend window, then run scripts\start_local.bat.
) else (
  echo [ERROR] The local .env file was not updated. Exit code: %EXIT_CODE%.
)

if "%NO_PAUSE%"=="0" pause
exit /b %EXIT_CODE%
