@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_huawei_codeagent_vnext.ps1" %*
exit /b %ERRORLEVEL%
