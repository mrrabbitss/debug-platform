@echo off
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: LOCAL PYTHON ENVIRONMENT NOT FOUND
  echo Run scripts\start_local.bat once, then retry.
  exit /b 2
)

if not exist "artifacts\validation" mkdir "artifacts\validation"
set "REPORT=artifacts\validation\glm-chat-features.json"
".venv\Scripts\python.exe" "scripts\validate_glm_chat_features.py" --report "%REPORT%" %*
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" (
  echo GLM feature validation passed. Safe report: %REPORT%
) else (
  echo GLM feature validation failed. Safe report: %REPORT%
)
exit /b %EXIT_CODE%
