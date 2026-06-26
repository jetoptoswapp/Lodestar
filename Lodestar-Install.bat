@echo off
REM Lodestar Windows 一鍵安裝與啟動（首次用這個；雙擊即可）。
REM 會自動偵測並安裝缺少的 Python / Node / git / claude，已安裝的會略過。
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows-bootstrap.ps1"
pause
