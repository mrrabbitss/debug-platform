@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Local Python environment not found. Run scripts\bootstrap_local.bat first.
  exit /b 1
)
".venv\Scripts\python.exe" scripts\run_golden_evals.py %*
exit /b %ERRORLEVEL%
