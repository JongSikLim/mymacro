@echo off
REM One-time setup: create a virtual environment and install dependencies.
setlocal
cd /d "%~dp0"

if not exist .venv (
    python -m venv .venv
    if errorlevel 1 (
        echo [!] Python 3.10 or newer must be installed and on PATH.
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo [!] Dependency install failed.
    pause
    exit /b 1
)

echo.
echo Setup done. Run start.bat to launch MyMacro.
pause
