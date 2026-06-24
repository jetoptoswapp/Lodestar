#!/usr/bin/env bash
# Lodestar 一次性環境設定（macOS / Linux）：建立 Python venv + 安裝後端依賴 + 安裝前端依賴。
# 用法：./setup.sh          （需要 python3.12 與 Node 22+ / npm 已安裝）
#       PYTHON=python3.11 ./setup.sh   （指定別的 python）
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3.12}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "✗ 找不到 $PYTHON。請先安裝 Python 3.11+（建議 3.12），或設 PYTHON=python3.x 後重試。" >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "✗ 找不到 npm（需要 Node 22+）。請先安裝 Node 22+ 後重試。" >&2
  exit 1
fi

echo "▸ 建立 backend/.venv（$PYTHON）"
"$PYTHON" -m venv backend/.venv
echo "▸ 安裝後端依賴"
backend/.venv/bin/pip install -r backend/requirements.txt
echo "▸ 安裝前端依賴（npm install）"
( cd frontend && npm install )

echo ""
echo "✓ 環境設定完成。"
echo "  下一步：確認 AI CLI 已安裝並登入 →  claude auth login   （claude auth status 檢查狀態）"
echo "  然後啟動：./start.sh"
