#!/bin/bash
# macOS 可雙擊：執行一次性環境設定（轉呼 setup.sh）。
# Finder 雙擊會開 Terminal 並執行。完成後可雙擊 Lodestar.command 啟動。
cd "$(dirname "$0")"
# 若用 nvm，補上最新 Node 22 路徑，讓 npm 解析得到（沒用 nvm 可忽略）。
if [ -d "$HOME/.nvm/versions/node" ]; then
  latest22="$(ls -d "$HOME"/.nvm/versions/node/v22.* 2>/dev/null | sort -V | tail -1)"
  [ -n "$latest22" ] && export PATH="$latest22/bin:$PATH"
fi
exec ./setup.sh
