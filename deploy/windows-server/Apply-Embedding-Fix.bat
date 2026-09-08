@echo off
setlocal
chcp 65001 >nul
set "gwapApp=%LOCALAPPDATA%\Programs\GWAPDebugServer\app"
if not defined GWAP_SERVER_DATA_ROOT set "GWAP_SERVER_DATA_ROOT=%LOCALAPPDATA%\GWAPDebugServer"
if not exist "%gwapApp%\runtime\python\python.exe" (
  echo [ERROR] GWAP Server 0.3.3 was not found for this Windows user.
  pause
  exit /b 1
)
"%gwapApp%\runtime\python\python.exe" -B "%~dp0install_embedding_hotfix.py" --app "%gwapApp%" --data-root "%GWAP_SERVER_DATA_ROOT%"
set "result=%errorlevel%"
pause
exit /b %result%
