@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0validate_all.ps1" %*
exit /b %ERRORLEVEL%
