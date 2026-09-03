@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0\.."

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows_gguf_installer.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo [ERROR] Offline GGUF installer build failed with exit code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%

