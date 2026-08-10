@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_codeagent_vnext.ps1" %*
exit /b %ERRORLEVEL%
