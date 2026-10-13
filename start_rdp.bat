@echo off
setlocal
cd /d "%~dp0"
title Discord Command Center & Autonomous X Engine (RDP)
color 0B

echo ============================================================
echo   Discord Command Center + Autonomous X Growth Engine
echo   Windows RDP 24/7 Production Runner
echo ============================================================
echo.

:: 1. Activate Virtual Environment if present
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    echo [*] Python virtual environment activated.
) else (
    echo [INFO] Running using system Python.
)

:: 2. Verify dependencies
python -c "import discord, playwright, aiohttp" >nul 2>&1
if %errorlevel% neq 0 (
    color 0E
    echo [WARNING] Some dependencies may be missing.
    echo Running setup_rdp.bat is recommended if this is your first launch.
    echo.
)

:: 3. Startup Banner & Dashboard URL
echo ============================================================
echo   [DASHBOARD] http://localhost:10000
echo   [LOGIN PW]  admin123  (or configured ADMIN_PASSWORD in .env)
echo   [STATUS]    Starting 24/7 Watchdog Loop...
echo ============================================================
echo.

:: 4. Auto-restart watchdog loop
:run_loop
echo [%date% %time%] Launching Discord Command Center...
python discord_agent/main.py

echo.
echo ============================================================
echo  [WATCHDOG] Process exited at %date% %time%.
echo  Auto-restarting in 5 seconds to keep 24/7 uptime active...
echo  (Press Ctrl+C to terminate this watchdog loop)
echo ============================================================
timeout /t 5 /nobreak >nul
goto run_loop
