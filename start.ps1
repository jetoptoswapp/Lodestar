# Lodestar 原生啟動器（Windows）：釋放 port -> 起後端 + 前端 -> Ctrl-C 一併關閉。
# 這是 start.sh（Unix 專屬）的 PowerShell 對應版。前提：已跑過 setup.ps1，且 claude CLI 已登入。
Set-Location $PSScriptRoot

if (-not (Test-Path "backend\.venv\Scripts\python.exe")) {
  Write-Error "找不到 backend\.venv，請先執行 .\setup.ps1"; exit 1
}
if (-not (Test-Path "frontend\node_modules")) {
  Write-Error "找不到 frontend\node_modules，請先執行 .\setup.ps1"; exit 1
}

# 釋放 8723 / 8724（等同 start.sh 的 free_port）
Get-NetTCPConnection -LocalPort 8723,8724 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

# 後端：venv 內的 python -m uvicorn（--app-dir 指到 backend）
$backend = Start-Process -PassThru -NoNewWindow `
  -FilePath "backend\.venv\Scripts\python.exe" `
  -ArgumentList "-m","uvicorn","app:app","--host","0.0.0.0","--port","8723","--app-dir","backend"

# 前端：透過 cmd 呼叫 npm（npm 是 .cmd shim，用 cmd /c 最穩）
$frontend = Start-Process -PassThru -NoNewWindow `
  -FilePath "cmd.exe" -ArgumentList "/c","npm --prefix frontend run dev"

Write-Host "--------------------------------------------------"
Write-Host "  Lodestar 已啟動"
Write-Host "  frontend : http://localhost:8724"
Write-Host "  backend  : http://localhost:8723   (swagger: /docs)"
Write-Host "  Ctrl-C 停止"
Write-Host "--------------------------------------------------"

try {
  Wait-Process -Id $backend.Id, $frontend.Id
} finally {
  Stop-Process -Id $backend.Id  -Force -ErrorAction SilentlyContinue
  Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue
  # 提醒：Windows 下 next dev 會另起 node 子程序，關閉後若仍有殘留 node，請用工作管理員結束。
}
