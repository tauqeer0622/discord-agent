@echo off
echo [*] Stopping ApexPulse autonomous engine...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq ApexPulse*" 2>nul
curl -s -X POST http://127.0.0.1:8000/api/control/stop >nul 2>&1
echo [SUCCESS] Automation stopped.
pause
