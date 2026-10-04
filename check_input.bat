@echo off
REM Verify that MyMacro's input reaches another focused application.
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo [!] .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

echo Open an EMPTY Notepad window before continuing.
echo This test types text into whichever window has focus.
echo.
pause

.venv\Scripts\python.exe tools\check_input.py
echo.
pause
