@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

if not exist "runtime\python\python.exe" (
  echo [ERROR] Bundled Python runtime is missing.
  echo [ERROR] Download and extract the complete Windows portable ZIP again.
  pause
  exit /b 1
)

"runtime\python\python.exe" -B -s "portable_launcher.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo [ERROR] The portable platform stopped with exit code %EXIT_CODE%.
  echo [ERROR] Run start.bat --check for a dependency-free package diagnosis.
  pause
)
exit /b %EXIT_CODE%
