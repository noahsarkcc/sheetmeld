@echo off
REM SheetMeld launcher (tray mode, no console window).
REM For a console session with live logs run start_console.bat instead.

cd /d "%~dp0"

REM Check Python (silently)
where pythonw >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install Python 3.8+ and re-run.
    pause
    exit /b 1
)

REM Ensure dependencies are present. Run a one-time install when missing.
python -c "import flask, openpyxl, xlrd, pystray, PIL" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Installing dependencies, please wait...
    python -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)

REM Launch server with pythonw so no console window appears; the tray icon
REM gives access to the log file and quit command.
start "" pythonw server.py
