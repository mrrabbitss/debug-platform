#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f .env ] || cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e './backend[dev]'
( cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 ) &
BACKEND_PID=$!
trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT
cd frontend
npm install
npm run dev
