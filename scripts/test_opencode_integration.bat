@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0test_opencode_integration.ps1" %*
exit /b %ERRORLEVEL%
