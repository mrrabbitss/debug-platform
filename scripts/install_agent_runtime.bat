@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_agent_runtime.ps1" %*
exit /b %ERRORLEVEL%
