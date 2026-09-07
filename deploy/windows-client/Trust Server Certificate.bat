@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0trust_server_certificate.ps1" %*
set "result=%errorlevel%"
pause
exit /b %result%
