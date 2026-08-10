@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0doctor_agent_runtime.ps1"
exit /b %ERRORLEVEL%
