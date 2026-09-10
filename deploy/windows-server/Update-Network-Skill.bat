@echo off
setlocal
chcp 65001 >nul
if not defined GWAP_SERVER_PACKAGE_ROOT set "GWAP_SERVER_PACKAGE_ROOT=%LOCALAPPDATA%\Programs\GWAPDebugServer\app"
if not defined GWAP_SERVER_DATA_ROOT set "GWAP_SERVER_DATA_ROOT=%LOCALAPPDATA%\GWAPDebugServer"
if not exist "%GWAP_SERVER_PACKAGE_ROOT%\runtime\python\python.exe" (
  echo 未找到已安装服务器。请在安装服务器的 Windows 账号下运行。
  echo 自定义安装路径可设置 GWAP_SERVER_PACKAGE_ROOT 后重试。
  pause
  exit /b 1
)
echo 仅更新指定组网 Skill。请先正常停止服务器。
"%GWAP_SERVER_PACKAGE_ROOT%\runtime\python\python.exe" -B -s "%~dp0update_network_skill.py" --package "%GWAP_SERVER_PACKAGE_ROOT%" --data-root "%GWAP_SERVER_DATA_ROOT%" %*
set "GWAP_UPDATE_RESULT=%ERRORLEVEL%"
pause
exit /b %GWAP_UPDATE_RESULT%
