@echo off
setlocal
cd /d "%~dp0\.."

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_local_models.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo [ERROR] Local model installation failed with exit code %EXIT_CODE%.
  echo [ERROR] Re-run this file to resume completed model files.
  pause
  exit /b %EXIT_CODE%
)

echo.
echo [OK] Local model installation completed.
pause
