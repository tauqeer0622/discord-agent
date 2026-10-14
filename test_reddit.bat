@echo off
setlocal
cd /d "%~dp0"
title Reddit Engine Test Post
color 0E

echo ============================================================
echo   Reddit Engine: Dry-Run Post Simulation
echo ============================================================
echo.

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

python discord_agent/reddit_engine/test_post.py --dry-run
echo.
pause
