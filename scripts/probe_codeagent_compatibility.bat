@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0probe_codeagent_compatibility.ps1" %*
exit /b %ERRORLEVEL%
