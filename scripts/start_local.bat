@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
set "ROOT=%CD%"
set "EXPECTED_STAMP="
set "INSTALLED_STAMP="
set "NEED_BOOTSTRAP=0"

if not exist .env (
  copy .env.example .env >nul
  if errorlevel 1 (
    set "FAIL_STEP=Could not create .env from .env.example."
    goto :fail
  )
)

call :dependency_stamp
if errorlevel 1 (
  set "FAIL_STEP=Could not calculate the local dependency fingerprint."
  goto :fail
)

if not exist ".venv\Scripts\python.exe" set "NEED_BOOTSTRAP=1"
if not exist "frontend\node_modules\.package-lock.json" set "NEED_BOOTSTRAP=1"
if not exist ".local_dependency_stamp" (
  set "NEED_BOOTSTRAP=1"
) else (
  set /p "INSTALLED_STAMP="<".local_dependency_stamp"
)
if not "%INSTALLED_STAMP%"=="%EXPECTED_STAMP%" set "NEED_BOOTSTRAP=1"

if "%NEED_BOOTSTRAP%"=="1" (
  echo [INFO] Dependencies are missing or changed. Running one-time bootstrap...
  call "%ROOT%\scripts\bootstrap_local.bat" --no-pause
  if errorlevel 1 (
    set "FAIL_STEP=Local dependency bootstrap failed."
    goto :fail
  )
) else (
  echo [INFO] Dependencies are already installed; skipping pip and npm installation.
)

".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  set "FAIL_STEP=The local virtual environment must use Python 3.11 or newer."
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

call :backend_ready
if not errorlevel 1 (
  echo [INFO] A healthy GW/AP Debug backend is already running on http://127.0.0.1:8000.
  goto :start_frontend
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 (
  set "FAIL_STEP=Port 8000 is occupied by another service. Stop that service or change its port; it will not be reused as this backend."
  goto :fail
)

echo [INFO] Starting backend at http://127.0.0.1:8000
start "GW-AP Backend" /D "%ROOT%\backend" cmd /k ""%ROOT%\.venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(45); do { try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/' -TimeoutSec 2; if ($r.name -eq 'GW/AP Intelligent Debug Platform' -and $r.api -eq '/api/v1') { exit 0 } } catch {}; Start-Sleep -Milliseconds 500 } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 (
  set "FAIL_STEP=Backend did not become healthy within 45 seconds. Review the GW-AP Backend window."
  goto :fail
)

:start_frontend
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

:dependency_stamp
for /f "usebackq delims=" %%H in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$paths=@('backend\pyproject.toml','frontend\package-lock.json'); $hashes=$paths | ForEach-Object { (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash }; [Console]::Write([string]::Join('-', $hashes))"`) do set "EXPECTED_STAMP=%%H"
if not defined EXPECTED_STAMP exit /b 1
exit /b 0

:backend_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/' -TimeoutSec 2; if ($r.name -eq 'GW/AP Intelligent Debug Platform' -and $r.api -eq '/api/v1') { exit 0 } } catch {}; exit 1"
exit /b %errorlevel%

:fail
echo.
echo [ERROR] %FAIL_STEP%
echo [ERROR] Startup stopped. Review the message above, then try again.
pause
exit /b 1
