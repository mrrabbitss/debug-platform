@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "REPO_ROOT=%~dp0.."
set "PYTHON_EXE=%REPO_ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo ERROR: Local Python environment not found.
  echo Run scripts\start_local.bat once, then retry this demo command.
  exit /b 1
)
"%PYTHON_EXE%" "%~dp0seed_ap_offline_demo.py" %*
exit /b %ERRORLEVEL%
