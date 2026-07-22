@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

set "REPORT_PATH=%CD%\local_doctor_result.txt"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0doctor_local.ps1" -OutputPath "%REPORT_PATH%"
set "DOCTOR_EXIT=%ERRORLEVEL%"

echo.
if "%DOCTOR_EXIT%"=="0" (
  echo [OK] Local environment checks passed.
) else (
  echo [ERROR] One or more blocking checks failed.
)
echo [INFO] Report: %REPORT_PATH%
echo [INFO] The report contains paths and process details, but never reads .env values or API keys.
pause
exit /b %DOCTOR_EXIT%
