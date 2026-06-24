#!/bin/bash
# macOS 可雙擊啟動 Lodestar（前端 8724 + 後端 8723）。視窗內按 Ctrl-C 停止。
# 前提：已跑過 setup.command（或 setup.sh）建好環境，且 claude CLI 已安裝並登入。
cd "$(dirname "$0")"
# 若用 nvm，補上最新 Node 22 路徑（系統預設 Node 16 不相容 Next 16）。沒用 nvm 可忽略這段。
if [ -d "$HOME/.nvm/versions/node" ]; then
  latest22="$(ls -d "$HOME"/.nvm/versions/node/v22.* 2>/dev/null | sort -V | tail -1)"
  [ -n "$latest22" ] && export PATH="$latest22/bin:$PATH"
fi
exec ./start.sh
