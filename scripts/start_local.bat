@echo off
setlocal
cd /d "%~dp0\.."
set "ROOT=%CD%"
set "PYTHON_CMD="

where python >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"

if not defined PYTHON_CMD (
  where py >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
  set "FAIL_STEP=Python 3.11 or newer was not found in PATH."
  goto :fail
)

%PYTHON_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  set "FAIL_STEP=Python 3.11 or newer is required."
  goto :fail
)

where node >nul 2>&1
if errorlevel 1 (
  set "FAIL_STEP=Node.js was not found in PATH."
  goto :fail
)

where npm >nul 2>&1
if errorlevel 1 (
  set "FAIL_STEP=Node.js/npm was not found in PATH."
  goto :fail
)

node -e "const [major, minor] = process.versions.node.split('.').map(Number); process.exit((major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major > 22 ? 0 : 1)"
if errorlevel 1 (
  set "FAIL_STEP=Node.js 20.19+ or 22.12+ is required."
  goto :fail
)

if not exist .env (
  copy .env.example .env >nul
  if errorlevel 1 (
    set "FAIL_STEP=Could not create .env from .env.example."
    goto :fail
  )
)

if not exist .venv\Scripts\python.exe (
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 (
    set "FAIL_STEP=Could not create the Python virtual environment."
    goto :fail
  )
)

".venv\Scripts\python.exe" -m pip install -e "backend[dev]"
if errorlevel 1 (
  set "FAIL_STEP=Backend dependency installation failed."
  goto :fail
)

pushd frontend
call npm ci
if errorlevel 1 (
  popd
  set "FAIL_STEP=Frontend dependency installation failed."
  goto :fail
)
popd

netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul
if errorlevel 1 (
  start "GW-AP Backend" /D "%ROOT%\backend" cmd /k ""%ROOT%\.venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
) else (
  echo [WARN] Port 8000 is already in use; the existing backend will be reused.
  echo [WARN] Close the old backend process first if you need to restart it.
)

echo [INFO] Starting frontend at http://127.0.0.1:5173
pushd frontend
call npm run dev
if errorlevel 1 (
  popd
  set "FAIL_STEP=Frontend development server failed to start."
  goto :fail
)
popd
exit /b 0

:fail
echo.
echo [ERROR] %FAIL_STEP%
echo [ERROR] Startup stopped. Review the message above, then try again.
pause
exit /b 1
