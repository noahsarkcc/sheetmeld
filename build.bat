@echo off
title SheetMeld - Build
echo ========================================
echo   SheetMeld - PyInstaller Build
echo ========================================
echo.

cd /d "%~dp0"

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found!
    pause
    exit /b 1
)

REM Install build dependencies (same as CI: requirements.txt + pyinstaller)
echo [1/3] Installing dependencies...
pip install -r requirements.txt pyinstaller >nul 2>&1

REM Clean previous build
echo [2/3] Cleaning previous build...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist SheetMeld.spec del SheetMeld.spec

REM Build (keep in sync with .github/workflows/release.yml)
REM All local modules (xml_parser, xml_differ, xml_merger, xlsx_parser,
REM svn_helper, updater, tray) are top-level imports of server.py, so
REM PyInstaller bundles them automatically. pystray's platform backend is
REM picked dynamically and must be declared as a hidden import.
echo [3/3] Building executable...
pyinstaller --noconfirm --onefile --noconsole ^
    --name SheetMeld ^
    --add-data "static;static" ^
    --hidden-import pystray._win32 ^
    --hidden-import PIL.Image ^
    --hidden-import PIL.ImageDraw ^
    server.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Build failed!
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Build complete!
echo   Output: dist\SheetMeld.exe
echo ========================================
echo.
echo To distribute, copy dist\SheetMeld.exe to the target machine.
echo The exe is fully standalone - no Python needed.
echo.
pause
