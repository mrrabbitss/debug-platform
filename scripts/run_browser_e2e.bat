@echo off
setlocal
cd /d "%~dp0.."
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_browser_e2e.ps1" %*
exit /b %ERRORLEVEL%
