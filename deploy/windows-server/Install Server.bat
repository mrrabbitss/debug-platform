@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_server.ps1" %*
set "result=%errorlevel%"
pause
exit /b %result%
