@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configure_agent_integrations.ps1" %*
exit /b %ERRORLEVEL%
