@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
if not exist "runtime\python\python.exe" (
  echo [ERROR] Bundled Python is missing. Extract the complete package again.
  if "%~1"=="" pause
  exit /b 1
)
"runtime\python\python.exe" -B -s "portable_codeagent.py" %*
set "LAUNCH_EXIT=%ERRORLEVEL%"
if not "%LAUNCH_EXIT%"=="0" if "%~1"=="" pause
exit /b %LAUNCH_EXIT%
