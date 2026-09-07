@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_client.ps1" %*
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%
