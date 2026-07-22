#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f .env ] || cp .env.example .env
if [ ! -x .venv/bin/python ]; then
  python -m venv .venv
fi
source .venv/bin/activate
python -m pip install -e './backend[dev]'
( cd frontend && npm ci )
( cd backend && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 ) &
BACKEND_PID=$!
trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT
cd frontend
npm run dev
