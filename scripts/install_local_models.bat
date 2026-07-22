@echo off
setlocal
cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python virtual environment was not found.
  echo [ERROR] Run scripts\start_local.bat once, close the service windows, and retry.
  pause
  exit /b 1
)

echo [INFO] Installing optional local Embedding and Reranker runtime...
".venv\Scripts\python.exe" -m pip install -e "backend[local-models]"
if errorlevel 1 (
  echo.
  echo [ERROR] Local model runtime installation failed.
  echo [ERROR] Check the pip output and your company network or package mirror settings.
  pause
  exit /b 1
)

echo.
echo [OK] Local model runtime is installed.
echo [INFO] Restart scripts\start_local.bat, then test and activate the local profile in System Settings.
echo [INFO] The first model test may download model files. You can also enter an existing local model directory.
pause
