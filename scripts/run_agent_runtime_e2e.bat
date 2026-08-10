@echo off
setlocal
cd /d "%~dp0\.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\run_agent_runtime_e2e.py
) else (
  python scripts\run_agent_runtime_e2e.py
)
exit /b %ERRORLEVEL%
