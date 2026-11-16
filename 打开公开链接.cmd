@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
".venv\Scripts\python.exe" public_share.py
if errorlevel 1 pause
