@echo off
title SheetMeld (console)
echo ========================================
echo   SheetMeld - Console mode
echo ========================================
echo.

cd /d "%~dp0"

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found.
    pause
    exit /b 1
)

python -c "import flask, openpyxl, xlrd" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Installing dependencies...
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo Starting server (console mode, tray disabled)...
echo Access at: http://localhost:5566
echo.
python server.py --console
if %errorlevel% neq 0 (
    echo.
    echo Server exited with error.
    pause
)
