@echo off
setlocal
set "GWAP_PYTHON=%~dp0..\artifacts\lan\server-pilot-20260907\runtime\python\python.exe"
if not exist "%GWAP_PYTHON%" (
  echo Server runtime not found. Keep artifacts\lan\server-pilot-20260907 in this project.
  pause
  exit /b 1
)
"%GWAP_PYTHON%" -B -s "%~dp0run_lan_server.py" %*
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%
