@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_codeagent_vnext.ps1" %*
exit /b %ERRORLEVEL%
