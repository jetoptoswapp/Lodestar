# Lodestar 一次性環境設定（Windows 原生 PowerShell）。
# 用法：在專案根目錄  ->  .\setup.ps1
# 需要：Python 3.12（py -3.12）、Node 22+ / npm、git 皆已安裝。
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "▸ 建立 backend\.venv（py -3.12）"
py -3.12 -m venv backend\.venv
Write-Host "▸ 安裝後端依賴"
backend\.venv\Scripts\pip install -r backend\requirements.txt
Write-Host "▸ 安裝前端依賴（npm install）"
npm --prefix frontend install

Write-Host ""
Write-Host "✓ 環境設定完成。"
Write-Host "  下一步：確認 claude CLI 已安裝並登入 ->  claude auth login   （claude auth status 檢查）"
Write-Host "  然後啟動：.\start.ps1  （或雙擊 start.bat）"
