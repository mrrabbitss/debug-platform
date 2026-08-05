@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0refresh_python_lock.ps1" %*
exit /b %errorlevel%
