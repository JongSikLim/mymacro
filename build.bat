@echo off
REM Build a single-file MyMacro.exe into dist\.
setlocal
cd /d "%~dp0"

if not exist .venv (
    echo [!] .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
pip install -r requirements-dev.txt

pyinstaller --noconfirm --clean --onefile --windowed ^
    --name MyMacro ^
    --hidden-import pynput.keyboard._win32 ^
    --hidden-import pynput.mouse._win32 ^
    run.py
if errorlevel 1 (
    echo [!] Build failed.
    pause
    exit /b 1
)

if not exist dist\macros\images mkdir dist\macros\images

echo.
echo Built dist\MyMacro.exe
echo Macros and captured images are stored next to the exe in dist\macros\.
pause
