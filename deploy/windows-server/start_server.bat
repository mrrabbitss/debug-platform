@echo off
setlocal
chcp 65001 >nul
if not defined GWAP_SERVER_DATA_ROOT set "GWAP_SERVER_DATA_ROOT=%LOCALAPPDATA%\GWAPDebugServer"
"%~dp0runtime\python\python.exe" -B -s "%~dp0scripts\run_lan_server.py" --package "%~dp0." --data-root "%GWAP_SERVER_DATA_ROOT%" %*
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%
