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

# --- 啟動前清掉佔用連接埠的舊程序 ---
free_port() {
  local port="$1"
  local pids
  pids="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "▸ port $port 被佔用 (pid: $pids)，先關掉舊程序"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 1
    pids="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [ -n "$pids" ]; then
      echo "  仍未釋放，強制 kill -9 ($pids)"
      # shellcheck disable=SC2086
      kill -9 $pids 2>/dev/null || true
      sleep 1
    fi
  fi
}
free_port 8723
free_port 8724

# --- Backend (FastAPI, port 8723) ---
if [ ! -d backend/.venv ]; then
  echo "✗ backend/.venv 不存在；請先跑 python3.12 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt" >&2
  exit 1
fi
echo "▸ starting backend  http://localhost:8723"
( cd backend && .venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8723 --log-level info ) &
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
echo "  Lodestar · M0"
echo "  backend  · http://localhost:8723   (API)"
echo "  swagger  · http://localhost:8723/docs"
echo "  frontend · http://localhost:8724   (UI)"
echo "  ctrl-c to stop"
echo "──────────────────────────────────────────────"

wait
