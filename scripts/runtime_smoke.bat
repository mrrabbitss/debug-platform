@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime_smoke.ps1"
set "SMOKE_EXIT=%ERRORLEVEL%"
echo.
if "%SMOKE_EXIT%"=="0" (
  echo [OK] Isolated backend, migration, frontend proxy, and case API smoke test passed.
) else (
  echo [ERROR] Runtime smoke test failed. Review the error above.
)
pause
exit /b %SMOKE_EXIT%
