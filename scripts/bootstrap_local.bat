@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
set "NO_PAUSE=0"
set "PYTHON_CMD="
set "DEPENDENCY_STAMP="
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"

where python >nul 2>&1
if not errorlevel 1 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3"
  )
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

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] Creating Python virtual environment...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 (
    set "FAIL_STEP=Could not create the Python virtual environment."
    goto :fail
  )
)

echo [INFO] Installing backend dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
  set "FAIL_STEP=Python package installer upgrade failed."
  goto :fail
)
".venv\Scripts\python.exe" -m pip install -e "backend[dev]"
if errorlevel 1 (
  set "FAIL_STEP=Backend dependency installation failed."
  goto :fail
)

echo [INFO] Installing frontend dependencies...
pushd frontend
call npm ci
if errorlevel 1 (
  popd
  set "FAIL_STEP=Frontend dependency installation failed."
  goto :fail
)
popd

for /f "usebackq delims=" %%H in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$paths=@('backend\pyproject.toml','frontend\package-lock.json'); $hashes=$paths | ForEach-Object { (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash }; [Console]::Write([string]::Join('-', $hashes))"`) do set "DEPENDENCY_STAMP=%%H"
if not defined DEPENDENCY_STAMP (
  set "FAIL_STEP=Could not calculate the installed dependency fingerprint."
  goto :fail
)
> ".local_dependency_stamp" echo %DEPENDENCY_STAMP%

echo.
echo [OK] Local dependencies are ready. Future starts skip installation until dependency files change.
if "%NO_PAUSE%"=="0" pause
exit /b 0

:fail
echo.
echo [ERROR] %FAIL_STEP%
if "%NO_PAUSE%"=="0" pause
exit /b 1
