@echo off
setlocal
chcp 65001 >nul
if not defined GWAP_SERVER_DATA_ROOT set "GWAP_SERVER_DATA_ROOT=%LOCALAPPDATA%\GWAPDebugServer"
set "gwapConfig=%GWAP_SERVER_DATA_ROOT%\config\server.json"
if not exist "%gwapConfig%" (
  echo [ERROR] Configured per-user server data was not found: %gwapConfig%
  echo Start the installed server from this Windows account, or set GWAP_SERVER_DATA_ROOT to its existing data directory.
  pause
  exit /b 1
)
"%~dp0runtime\python\python.exe" -B -s "%~dp0server_admin.py" recover-admin --config "%gwapConfig%" %*
set "result=%errorlevel%"
if "%result%"=="0" (
  echo.
  echo [OK] Read the recovery credential only from: %GWAP_SERVER_DATA_ROOT%\config\admin-recovery-token.txt
  echo [NEXT] Use it at the web page's Administrator entry, create and verify a personal administrator token, then revoke the recovery token and delete this file.
  pause
  exit /b 0
)
pause
exit /b %result%
