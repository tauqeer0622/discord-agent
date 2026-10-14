@echo off
setlocal
cd /d "%~dp0"
title Reddit Interactive Login Helper
color 0B

echo ============================================================
echo   Reddit Engine: Interactive Login Helper
echo ============================================================
echo.

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

python discord_agent/reddit_engine/login.py
echo.
pause
