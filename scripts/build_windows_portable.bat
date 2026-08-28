@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows_portable.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo [ERROR] Windows portable package build failed with exit code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
