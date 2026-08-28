@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

if "%~1"=="" (
  echo Usage: scripts\verify_windows_portable.bat ^<extracted-package-directory^>
  exit /b 2
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0verify_windows_portable.ps1" -PackageRoot "%~1"
exit /b %ERRORLEVEL%
