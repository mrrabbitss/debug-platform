@echo off
setlocal
cd /d "%~dp0\.."

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check_hf_model_access.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
  echo [OK] Hugging Face model access has a usable download path.
) else (
  echo [ERROR] Hugging Face model access check failed.
)
echo [INFO] Send the generated hf_model_access_report_*.txt file when requesting help.
pause
exit /b %EXIT_CODE%
