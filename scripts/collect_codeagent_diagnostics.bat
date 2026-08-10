@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0collect_codeagent_diagnostics.ps1" %*
exit /b %ERRORLEVEL%
