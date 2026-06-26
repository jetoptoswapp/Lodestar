# Lodestar 單檔 standalone 安裝器（Windows）。
#
# 推薦用法（PowerShell 貼一行即可）：
#   irm https://raw.githubusercontent.com/jetoptoswapp/Lodestar/main/Install-Lodestar.ps1 | iex
# 或：下載本檔後執行  powershell -NoProfile -ExecutionPolicy Bypass -File Install-Lodestar.ps1
#
# 流程：確認 git -> clone 公開 repo -> windows-bootstrap.ps1（裝 Python/Node/claude/依賴並啟動）。
# 安裝位置預設 ~\Lodestar，可用環境變數覆寫：$env:LODESTAR_DIR="D:\Lodestar"
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$RepoUrl = "https://github.com/jetoptoswapp/Lodestar.git"
$Dest = if ($env:LODESTAR_DIR) { $env:LODESTAR_DIR } else { Join-Path $HOME "Lodestar" }

function Has-Cmd($n) { [bool](Get-Command $n -ErrorAction SilentlyContinue) }
function Update-SessionPath {
    $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $u = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($m, $u) | Where-Object { $_ }) -join ";"
}

Write-Host "== Lodestar 單檔安裝器 ==" -ForegroundColor Cyan

# 1. git
if (-not (Has-Cmd git)) {
    if (-not (Has-Cmd winget)) {
        Write-Host "需要 git，但找不到 winget。請先安裝 git（或 Microsoft Store 的 App Installer）後重跑。" -ForegroundColor Red
        Read-Host "按 Enter 結束"; exit 1
    }
    Write-Host "安裝 git（可能跳 UAC）..."
    winget install --id Git.Git --accept-source-agreements --accept-package-agreements --silent -e
    Update-SessionPath
    if (-not (Has-Cmd git)) {
        Write-Host "git 已安裝，但本視窗 PATH 還沒生效。請關閉視窗、重跑這行指令即可。" -ForegroundColor Yellow
        Read-Host "按 Enter 結束"; exit 1
    }
}

# 2. clone 或更新
if (Test-Path (Join-Path $Dest ".git")) {
    Write-Host "已存在 $Dest，更新中（git pull）..."
    git -C $Dest pull --ff-only
} else {
    Write-Host "Clone 到 $Dest ..."
    git clone $RepoUrl $Dest
}

# 3. 交給 repo 內的 bootstrap（裝 Python/Node/claude/依賴 -> 啟動）
$boot = Join-Path $Dest "windows-bootstrap.ps1"
if (-not (Test-Path $boot)) {
    Write-Host "找不到 $boot；clone 可能失敗。" -ForegroundColor Red
    Read-Host "按 Enter 結束"; exit 1
}
Write-Host "交給 windows-bootstrap.ps1 繼續安裝並啟動 ..." -ForegroundColor Cyan
& $boot
