@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "HOST=127.0.0.1"
set "PORT=8000"
set "DATA_DIR=%~dp0data"
set "OPEN_BROWSER=1"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment missing. Follow README installation steps.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" launcher.py --mode personal
if errorlevel 1 pause
