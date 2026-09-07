@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0server_maintenance.ps1" %*
set "result=%errorlevel%"
pause
exit /b %result%
