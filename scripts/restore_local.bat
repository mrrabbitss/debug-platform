@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Missing .venv. Run scripts\bootstrap_local.bat first.
  pause
  exit /b 1
)

set "BACKUP_PATH=%~1"
if not defined BACKUP_PATH (
  echo Drag a debug-platform backup ZIP onto this BAT file, or paste its full path below.
  set /p "BACKUP_PATH=Backup archive: "
)
set "BACKUP_PATH=%BACKUP_PATH:"=%"
if not exist "%BACKUP_PATH%" (
  echo [ERROR] Backup archive not found: %BACKUP_PATH%
  pause
  exit /b 2
)

echo.
echo [WARNING] Stop scripts\start_local.bat and all backend processes before continuing.
echo Existing database and storage will be replaced; a rollback copy will be retained.
set /p "CONFIRM=Type RESTORE to continue: "
if not "%CONFIRM%"=="RESTORE" (
  echo [CANCELLED] Confirmation did not match RESTORE.
  pause
  exit /b 3
)

".venv\Scripts\python.exe" "scripts\restore_local.py" --archive "%BACKUP_PATH%" --confirm "%CONFIRM%"
set "RESTORE_EXIT=%ERRORLEVEL%"
echo.
if "%RESTORE_EXIT%"=="0" (
  echo [OK] Restore completed. You can now run scripts\start_local.bat.
) else (
  echo [ERROR] Restore failed. Review the error and rollback path above.
)
pause
exit /b %RESTORE_EXIT%
