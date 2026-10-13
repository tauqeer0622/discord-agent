@echo off
setlocal
title Stop Discord Command Center
color 0C

echo ============================================================
echo   Stopping Discord Command Center and X Engine
echo ============================================================
echo.

:: 1. Kill any process listening on port 10000
echo [*] Checking for running process on port 10000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :10000') do (
    if not "%%a"=="0" (
        echo [*] Stopping PID %%a ...
        taskkill /F /PID %%a >nul 2>&1
    )
)

:: 2. Close command center console windows
taskkill /F /FI "WINDOWTITLE eq Discord Command Center*" /T >nul 2>&1

echo [OK] Stopped Command Center processes.
echo.
pause
