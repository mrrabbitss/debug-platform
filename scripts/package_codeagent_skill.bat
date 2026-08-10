@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0package_codeagent_skill.ps1" %*
exit /b %ERRORLEVEL%
