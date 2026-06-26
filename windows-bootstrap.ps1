# Lodestar Windows 一鍵安裝 + 啟動（idempotent）。
# 雙擊 Lodestar-Install.bat 會呼叫本檔。需要 Windows 10+ 內建的 winget。
#
# 流程：偵測 Python 3.12 / Node 22+ / git / claude CLI（已安裝就略過，只裝缺的）
#       -> 確認 claude 已登入（沒登入才開瀏覽器登入）-> 建 venv + 裝依賴（已有就略過）-> 啟動。
#
# 重跑安全：任何一步已完成都會偵測並略過，所以「關掉重跑」永遠沒問題。

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
Set-Location $PSScriptRoot

function Write-Step($m) { Write-Host "`n>> $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "   [OK]   $m" -ForegroundColor Green }
function Write-Skip($m) { Write-Host "   [SKIP] $m（已存在，不重裝）" -ForegroundColor DarkGray }
function Write-Warn($m) { Write-Host "   [!]    $m" -ForegroundColor Yellow }

# 安裝後本視窗的 PATH 不會自動更新，需從登錄檔重新載入
function Update-SessionPath {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user    = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ";"
}

function Has-Cmd($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Get-NodeMajor {
    if (-not (Has-Cmd node)) { return 0 }
    try {
        $v = (node --version) -replace '[^\d.]', ''
        return [int]($v.Split('.')[0])
    } catch { return 0 }
}

function Has-Py312 {
    if (Has-Cmd py) {
        try { py -3.12 --version *> $null; if ($LASTEXITCODE -eq 0) { return $true } } catch {}
    }
    return $false
}

# 偵測剛裝完但本視窗還抓不到的情況 -> 請使用者關掉重跑（重跑會略過已裝的，很快）
function Require-Or-Rerun($cmdOk, $name) {
    if (-not $cmdOk) {
        Write-Warn "$name 已安裝，但這個視窗還抓不到它（PATH 尚未生效）。"
        Write-Warn "請關閉本視窗，再雙擊 Lodestar-Install.bat 跑一次即可（已裝好的會直接略過）。"
        Read-Host "按 Enter 結束"
        exit 1
    }
}

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Lodestar Windows 一鍵安裝與啟動" -ForegroundColor Cyan
Write-Host "  會偵測你機器上的環境，已安裝的不會重裝。" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# --- 0. winget ---
if (-not (Has-Cmd winget)) {
    Write-Host "`n[X] 找不到 winget。" -ForegroundColor Red
    Write-Host "    請先從 Microsoft Store 安裝『應用程式安裝程式 (App Installer)』後重試，" -ForegroundColor Red
    Write-Host "    或手動安裝 Python 3.12 / Node 22+ / git / claude 後改用 start.bat。" -ForegroundColor Red
    Read-Host "按 Enter 結束"; exit 1
}
$wg = @('--accept-source-agreements', '--accept-package-agreements', '--silent', '-e')

# --- 1. Python 3.12 ---
Write-Step "檢查 Python 3.12"
if (Has-Py312) {
    Write-Skip "Python 3.12"
} else {
    Write-Host "   安裝 Python.Python.3.12（可能跳出 UAC，請允許）..."
    winget install --id Python.Python.3.12 $wg
    Update-SessionPath
    Require-Or-Rerun (Has-Py312) "Python 3.12"
    Write-Ok "Python 3.12"
}

# --- 2. Node 22+ ---
Write-Step "檢查 Node 22+"
if ((Get-NodeMajor) -ge 22) {
    Write-Skip ("Node " + (node --version))
} else {
    Write-Host "   安裝 OpenJS.NodeJS.LTS（>=22；可能跳出 UAC）..."
    winget install --id OpenJS.NodeJS.LTS $wg
    Update-SessionPath
    Require-Or-Rerun ((Get-NodeMajor) -ge 22) "Node 22+"
    Write-Ok ("Node " + (node --version))
}

# --- 3. git ---
Write-Step "檢查 git"
if (Has-Cmd git) {
    Write-Skip "git"
} else {
    Write-Host "   安裝 Git.Git（可能跳出 UAC）..."
    winget install --id Git.Git $wg
    Update-SessionPath
    Require-Or-Rerun (Has-Cmd git) "git"
    Write-Ok "git"
}

# --- 4. claude CLI ---
Write-Step "檢查 claude CLI"
if (Has-Cmd claude) {
    Write-Skip "claude"
} else {
    Write-Host "   用 npm 安裝 @anthropic-ai/claude-code ..."
    cmd /c "npm install -g @anthropic-ai/claude-code"
    Update-SessionPath
    Require-Or-Rerun (Has-Cmd claude) "claude CLI"
    Write-Ok "claude"
}

# --- 5. claude 登入狀態 ---
Write-Step "檢查 claude 登入狀態"
$loggedIn = $false
try { claude auth status *> $null; if ($LASTEXITCODE -eq 0) { $loggedIn = $true } } catch {}
if ($loggedIn) {
    Write-Ok "claude 已登入"
} else {
    Write-Warn "尚未登入 —— 即將開啟瀏覽器，請用『你自己的 Claude 帳號』登入。"
    Write-Warn "（這是每台機器/每個人都要做一次、且無法被打包省略的步驟。）"
    claude auth login
}

# --- 6. 後端 venv + 依賴 ---
Write-Step "後端環境（backend\.venv）"
if (Test-Path "backend\.venv\Scripts\python.exe") {
    Write-Skip "backend\.venv"
} else {
    py -3.12 -m venv backend\.venv
    Write-Ok "建立 venv"
}
Write-Host "   安裝/確認後端依賴 ..."
backend\.venv\Scripts\python -m pip install -q -r backend\requirements.txt
Write-Ok "後端依賴"

# --- 7. 前端依賴 ---
Write-Step "前端依賴（frontend\node_modules）"
if (Test-Path "frontend\node_modules") {
    Write-Skip "node_modules"
} else {
    cmd /c "npm --prefix frontend install"
    Write-Ok "前端依賴"
}

# --- 8. 啟動 ---
Write-Step "啟動 Lodestar"
Write-Host "   設定完成，正在啟動服務（稍候會自動開啟瀏覽器；本視窗按 Ctrl-C 停止）..." -ForegroundColor Green
# 背景等服務起來後自動開瀏覽器（start.ps1 會在前景阻塞）
Start-Job { Start-Sleep -Seconds 6; Start-Process "http://localhost:8724" } | Out-Null
& "$PSScriptRoot\start.ps1"
