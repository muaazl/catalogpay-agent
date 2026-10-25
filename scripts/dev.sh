#!/usr/bin/env bash
# One command local dev: FastAPI on :8000 + Vite on :5173 (Ctrl+C stops both).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ ! -d backend/.venv ]; then
  echo "==> Creating backend/.venv"
  python3 -m venv backend/.venv
fi
PY="backend/.venv/bin/python"
[ -x "$PY" ] || PY="backend/.venv/Scripts/python.exe"   # Git Bash on Windows

if ! "$PY" -c "import fastapi" 2>/dev/null; then
  echo "==> Installing backend dependencies"
  "$PY" -m pip install -q -e "backend[dev]"
fi
if [ ! -d frontend/node_modules ]; then
  echo "==> Installing frontend dependencies"
  (cd frontend && npm ci --no-audit --no-fund)
fi

echo "==> API      http://127.0.0.1:8000  (docs: /docs)"
(cd backend && exec "$ROOT/$PY" -m uvicorn app.main:app --reload --port 8000) &
API_PID=$!
# Stop only the process we started, whatever way this script exits.
trap 'kill "$API_PID" 2>/dev/null || true' EXIT INT TERM

echo "==> UI       http://localhost:5173"
cd frontend && npm run dev
