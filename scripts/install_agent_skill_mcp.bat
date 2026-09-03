@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_agent_skill_mcp.ps1" %*
exit /b %errorlevel%

