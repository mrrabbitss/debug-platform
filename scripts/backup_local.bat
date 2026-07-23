@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Missing .venv. Run scripts\bootstrap_local.bat first.
  pause
  exit /b 1
)

if "%~1"=="" (
  ".venv\Scripts\python.exe" "scripts\backup_local.py"
) else (
  ".venv\Scripts\python.exe" "scripts\backup_local.py" --output "%~f1"
)
set "BACKUP_EXIT=%ERRORLEVEL%"
echo.
if "%BACKUP_EXIT%"=="0" (
  echo [OK] Verified backup created. Keep it in an access-controlled location.
) else (
  echo [ERROR] Backup failed. No completed archive was published.
)
pause
exit /b %BACKUP_EXIT%
