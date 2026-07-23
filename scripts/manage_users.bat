@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Missing .venv. Run scripts\bootstrap_local.bat first.
  pause
  exit /b 1
)

if "%~1"=="" (
  echo Usage examples:
  echo   scripts\manage_users.bat create --username admin --display-name "Administrator" --role ADMIN
  echo   scripts\manage_users.bat issue-token --username admin --token-name replacement
  echo   scripts\manage_users.bat list
  echo   scripts\manage_users.bat deactivate --username engineer1
  pause
  exit /b 2
)

set "PYTHONPATH=%CD%\backend"
".venv\Scripts\python.exe" "scripts\manage_users.py" %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" pause
exit /b %RESULT%
