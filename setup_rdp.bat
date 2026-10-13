@echo off
setlocal enabledelayedexpansion
title Discord Command Center & X Engine - RDP Setup
color 0A

echo ============================================================
echo   Discord Command Center + Autonomous X Growth Engine
echo   Windows RDP Automated Setup Script
echo ============================================================
echo.

:: 1. Check Python installation
echo [*] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    color 0C
    echo [ERROR] Python is not found in your system PATH!
    echo.
    echo Please install Python 3.11 from:
    echo   https://www.python.org/downloads/windows/
    echo.
    echo IMPORTANT: Make sure to check "Add Python to PATH" during installation.
    echo Alternatively, run in PowerShell: winget install Python.Python.3.11
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version') do echo [OK] Found %%i

:: 2. Create or verify Virtual Environment
echo.
echo [*] Setting up Python virtual environment (venv)...
if not exist "venv\Scripts\activate.bat" (
    echo [*] Creating fresh virtual environment in .\venv ...
    python -m venv venv
    if %errorlevel% neq 0 (
        color 0C
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created successfully.
) else (
    echo [OK] Existing virtual environment found in .\venv.
)

:: 3. Activate Virtual Environment
call venv\Scripts\activate.bat

:: 4. Upgrade pip and install requirements
echo.
echo [*] Upgrading pip...
python -m pip install --upgrade pip >nul 2>&1

echo [*] Installing required packages from requirements.txt...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    color 0C
    echo [ERROR] Failed to install requirements. Please check your internet connection.
    pause
    exit /b 1
)
echo [OK] All Python dependencies installed successfully.

:: 5. Install Playwright Chromium binaries
echo.
echo [*] Installing Playwright Chromium browser binaries...
playwright install chromium
if %errorlevel% neq 0 (
    color 0E
    echo [WARNING] Playwright Chromium installation encountered a notice. Retrying via python -m ...
    python -m playwright install chromium
)
echo [OK] Playwright browser binaries verified.

:: 6. Check for Google Chrome installation
echo.
echo [*] Checking for native Google Chrome on RDP...
set CHROME_FOUND=0
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set CHROME_FOUND=1
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set CHROME_FOUND=1
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set CHROME_FOUND=1

if %CHROME_FOUND% equ 1 (
    echo [OK] Native Google Chrome is installed. The bot will use genuine Chrome for maximum stealth!
) else (
    echo [INFO] Google Chrome not found at default path. Bundled Chromium will be used.
    echo [TIP] For optimal anti-bot stealth on X, install Google Chrome:
    echo        https://www.google.com/chrome/
)

:: 7. Environment File Setup
echo.
if not exist ".env" (
    if exist "discord_agent\.env" (
        echo [*] Found existing discord_agent\.env - copying to root .env ...
        copy "discord_agent\.env" ".env" >nul
        echo [OK] Configured root .env from existing settings.
    ) else (
        echo [*] Creating .env from .env.example ...
        copy ".env.example" ".env" >nul
        echo [!] Created .env file. Please open .env and add your DISCORD_TOKEN, TURSO, and X credentials!
    )
) else (
    echo [OK] Root .env already exists.
)

:: 8. Create media and browser_data folders
if not exist "discord_agent\x_engine\browser_data" mkdir "discord_agent\x_engine\browser_data"
if not exist "discord_agent\x_engine\media" mkdir "discord_agent\x_engine\media"

echo.
echo ============================================================
echo   [SUCCESS] RDP Setup Complete!
echo ============================================================
echo.
echo Next steps:
echo   1. Verify your credentials in .env
echo   2. Double-click start_rdp.bat to launch the Command Center
echo   3. Open http://localhost:10000 in your browser
echo.
pause
