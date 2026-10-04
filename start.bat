@echo off
REM Launch MyMacro from the virtual environment.
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo [!] .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

.venv\Scripts\pythonw.exe run.py
