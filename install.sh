#!/usr/bin/env bash
# Lodestar 單檔 standalone 安裝器（macOS / Linux）。
#
# 推薦用法（保留終端機輸入，sudo / claude 登入才會正常）：
#   bash <(curl -fsSL https://raw.githubusercontent.com/jetoptoswapp/Lodestar/main/install.sh)
# 或：下載本檔後執行  bash install.sh
#
# 流程：確認/安裝 git・python3.12・node22・claude -> clone 公開 repo -> setup.sh -> (claude auth login) -> start.sh
# 安裝位置預設 ~/Lodestar，可用環境變數覆寫：LODESTAR_DIR=/path bash install.sh
set -euo pipefail

REPO_URL="https://github.com/jetoptoswapp/Lodestar.git"
DEST="${LODESTAR_DIR:-$HOME/Lodestar}"
OS="$(uname -s)"

say(){ printf "\n\033[36m== %s ==\033[0m\n" "$1"; }
have(){ command -v "$1" >/dev/null 2>&1; }

# 套件安裝器抽象（mac=brew / debian=apt / fedora=dnf）
pm_install(){
  if [ "$OS" = "Darwin" ]; then
    have brew || { echo "需要 Homebrew，請先安裝：https://brew.sh 後重跑。"; exit 1; }
    brew install "$@"
  elif have apt-get; then
    sudo apt-get update -y && sudo apt-get install -y "$@"
  elif have dnf; then
    sudo dnf install -y "$@"
  else
    echo "無法自動安裝（未知套件管理器）。請手動安裝：$*"; exit 1
  fi
}

say "Lodestar 安裝器（$OS）"

# 1. git
have git || { say "安裝 git"; pm_install git; }

# 2. Python 3.12
if ! have python3.12; then
  say "安裝 Python 3.12"
  if [ "$OS" = "Darwin" ]; then pm_install python@3.12
  elif have apt-get; then pm_install python3.12 python3.12-venv
  else pm_install python3.12; fi
fi

# 3. Node 22+
node_major=0
if have node; then node_major="$(node -v | sed -E 's/^v([0-9]+).*/\1/')"; fi
if [ "${node_major:-0}" -lt 22 ]; then
  say "安裝 Node 22+"
  if [ "$OS" = "Darwin" ]; then
    pm_install node
  elif have apt-get; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs
  else
    pm_install nodejs
  fi
fi

# 4. claude CLI（node 就緒後用 npm 全域安裝；EACCES 時退回 sudo）
have claude || { say "安裝 claude CLI（npm -g @anthropic-ai/claude-code）";
  npm install -g @anthropic-ai/claude-code || sudo npm install -g @anthropic-ai/claude-code; }

# 5. clone / 更新
if [ -d "$DEST/.git" ]; then
  say "更新既有 $DEST"; git -C "$DEST" pull --ff-only
else
  say "Clone 到 $DEST"; git clone "$REPO_URL" "$DEST"
fi
cd "$DEST"

# 6. 前後端依賴
say "安裝前後端依賴（setup.sh）"; bash ./setup.sh

# 7. 登入（沒登入才開瀏覽器；這步無法被打包省略）
if ! claude auth status >/dev/null 2>&1; then
  say "登入 Claude（用你自己的帳號）"; claude auth login
fi

# 8. 啟動
say "啟動 Lodestar（Ctrl-C 停止）"; exec bash ./start.sh
