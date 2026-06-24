@echo off
REM Lodestar 一鍵啟動（Windows 雙擊）。內部呼叫 start.ps1。
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
pause
