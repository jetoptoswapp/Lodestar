#!/usr/bin/env bash
# 一鍵起 backend (8723) + frontend (8724)。Ctrl-C 停。
set -euo pipefail
cd "$(dirname "$0")"

PIDS=()
cleanup() {
  echo ""
  echo "--- stopping services ---"
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --- Backend (FastAPI, port 8723) ---
if [ ! -d backend/.venv ]; then
  echo "✗ backend/.venv 不存在；請先跑 python3.12 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt" >&2
  exit 1
fi
echo "▸ starting backend  http://localhost:8723"
( cd backend && .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8723 --log-level info ) &
PIDS+=("$!")

# --- Frontend (Next.js, port 8724) ---
if [ ! -d frontend/node_modules ]; then
  echo "✗ frontend/node_modules 不存在；請先在 frontend/ 跑 npm install" >&2
  exit 1
fi
echo "▸ starting frontend http://localhost:8724"
( cd frontend && npm run dev ) &
PIDS+=("$!")

echo ""
echo "──────────────────────────────────────────────"
echo "  Forge · M0"
echo "  backend  · http://localhost:8723   (API)"
echo "  swagger  · http://localhost:8723/docs"
echo "  frontend · http://localhost:8724   (UI)"
echo "  ctrl-c to stop"
echo "──────────────────────────────────────────────"

wait
