@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

set "LOG_PATH=%~1"
if not defined LOG_PATH (
  echo GW/AP offline log inspection
  echo Drag an extensionless log onto this BAT file, or paste its full path below.
  echo No log content will be copied into the report.
  echo.
  set /p "LOG_PATH=Log file path: "
)

set "LOG_PATH=%LOG_PATH:"=%"
if not defined LOG_PATH (
  echo [ERROR] No log file was selected.
  pause
  exit /b 2
)

set "REPORT_PATH=%CD%\log_check_result.txt"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0inspect_log_file.ps1" -LogPath "%LOG_PATH%" -OutputPath "%REPORT_PATH%"
set "CHECK_EXIT=%ERRORLEVEL%"

echo.
if not "%CHECK_EXIT%"=="0" (
  echo [ERROR] Inspection failed with exit code %CHECK_EXIT%.
) else (
  echo [OK] Inspection completed.
  echo [OK] Short report: %REPORT_PATH%
)
pause
exit /b %CHECK_EXIT%
