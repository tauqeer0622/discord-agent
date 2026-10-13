@echo off
setlocal
cd /d "%~dp0"
title Update Discord Command Center & X Engine
color 0E

echo ============================================================
echo   Updating Discord Command Center & X Engine from GitHub
echo ============================================================
echo.

:: 1. Pull latest code from GitHub
echo [*] Pulling latest updates from GitHub...
git pull
if %errorlevel% neq 0 (
    color 0C
    echo [ERROR] git pull failed. Please check your internet connection or git configuration.
    pause
    exit /b 1
)

:: 2. Update dependencies if any new packages were added
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    echo [*] Checking and updating Python dependencies...
    pip install -r requirements.txt >nul 2>&1
)

echo.
echo ============================================================
echo   [SUCCESS] Code updated to latest version!
echo ============================================================
echo.
echo If the bot is currently running, close the bot window (or run stop_bot.bat)
echo and double-click start_rdp.bat to apply the new changes.
echo.
pause
