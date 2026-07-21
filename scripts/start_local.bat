@echo off
setlocal
cd /d %~dp0\..
if not exist .env copy .env.example .env
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
python -m pip install -e "backend[dev]"
start "GW-AP Backend" cmd /k "cd /d %CD%\backend && ..\.venv\Scripts\python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
cd frontend
call npm install
call npm run dev
