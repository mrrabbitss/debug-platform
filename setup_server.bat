@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_lan_server.ps1" %*
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%
