@echo off
if defined GWAP_SERVER_DATA_ROOT (start "" explorer.exe "%GWAP_SERVER_DATA_ROOT%") else (start "" explorer.exe "%LOCALAPPDATA%\GWAPDebugServer")
