# Complete Windows RDP Deployment Guide

This guide explains how to deploy and run the **Discord Command Center** and **Autonomous X (Twitter) Growth Engine** on your management-provided Windows RDP VPS for **24/7 continuous operation**.

---

## 🌟 Why Windows RDP is Superior to Render Free Tier

| Feature | Render Free Tier (Removed) | Windows RDP Server |
| :--- | :--- | :--- |
| **RAM** | Capped at **512 MB** (caused crashes) | **8 GB – 32 GB+** (Zero OOM risk) |
| **Uptime** | Spins down after 15 minutes of inactivity | **24/7 Uninterrupted** (Runs even when laptop is OFF) |
| **Cost** | Free tier had limits; upgrades cost $7–$25/mo | **$0 Extra Cost** (Company-provided server) |
| **Browser Execution** | Headless Linux container (heavy bot friction) | **Genuine Visual Google Chrome** (`HEADLESS=false`) |
| **Session Persistence** | Ephemeral container disk | **Permanent local disk** (`./browser_data`) |
| **Crash Recovery** | Slow container cold-boots | **Instant 5-second automatic watchdog restart** |

---

## 🚀 Quick Setup (5 Minutes)

### Step 1: Connect to your RDP Server
1. On your local laptop, press `Win + R`, type `mstsc.exe`, and press **Enter**.
2. Enter the **IP Address**, **Username**, and **Password** provided by your management.
3. Click **Connect**.

### Step 2: Ensure Prerequisites are Installed on RDP
Open PowerShell on the RDP as Administrator and run:
```powershell
# 1. Install Python 3.11, Git, and Google Chrome via winget (if not already installed)
winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
winget install Git.Git --silent --accept-package-agreements --accept-source-agreements
winget install Google.Chrome --silent --accept-package-agreements --accept-source-agreements
```
*(Alternatively, download standard installers from [python.org](https://www.python.org/downloads/) and [google.com/chrome](https://www.google.com/chrome/)). Make sure "Add Python to PATH" is checked.*

### Step 3: Clone or Copy the Repository
On the RDP, open Command Prompt or PowerShell:
```cmd
cd C:\
git clone https://github.com/tauqeer0622/discord-agent.git
cd discord-agent
```
*(Or simply copy the folder from your laptop to the RDP over the Remote Desktop clipboard/drive share).*

### Step 4: Run the 1-Click Automated Setup
In the project folder, double-click:
```cmd
setup_rdp.bat
```
This script automatically:
* Creates an isolated Python virtual environment (`.\venv`).
* Installs all requirements (`discord.py-self`, `telethon`, `turso`, `openai`, `fastapi`, `aiohttp`, etc.).
* Pre-installs Playwright Chromium.
* Checks for native Google Chrome on the RDP.
* Sets up your `.env` configuration file.

### Step 5: Verify Your `.env` File
Ensure your `.env` file in the project root contains your active credentials:
```env
# Discord user token (Account authorization token)
DISCORD_TOKEN=your_discord_token_here
CONTROL_SERVER_ID=1504196546609152022
CONTROL_CHANNEL_ID=1504196547745943564

# Turso Cloud Database (Holds 2.24M members)
USE_TURSO=true
TURSO_DATABASE_URL=libsql://discordcommandcenter-brightaxis628.aws-ap-south-1.turso.io
TURSO_AUTH_TOKEN=your_turso_auth_token_here

# OpenAI
OPENAI_API_KEY=your_openai_key_here
OPENAI_MODEL=gpt-4o-mini

# X (Twitter) Engine
X_AUTH_TOKEN=your_x_auth_token_here
HEADLESS=false
DRY_RUN=false
AUTO_START=true
```

### Step 6: Start 24/7 Production Runner
Double-click:
```cmd
start_rdp.bat
```
*(Or `start_bot.bat`)*

A console window will launch displaying:
```
============================================================
  [DASHBOARD] http://localhost:10000
  [LOGIN PW]  admin123
  [STATUS]    Starting 24/7 Watchdog Loop...
============================================================
```

Open Chrome on the RDP and navigate to `http://localhost:10000` to access the Command Center!

---

## 🔒 24/7 Background Persistence & Disconnecting from RDP

### How to Safely Disconnect
When you are done checking on the bot:
* **DO NOT** click "Start" → "Shut Down" or "Sign Out".
* **DO** simply click the **X** (close button) on the top blue Remote Desktop bar.
* Windows RDP keeps all applications, background scripts, and browsers running 24/7 in your active session. You can turn off your laptop, go to sleep, and the bot will continue running without interruption.

### Prevent Windows from Sleeping (Power Settings)
To guarantee the RDP never goes to sleep:
1. Open PowerShell on RDP as Administrator and run:
```powershell
powercfg -change -standby-timeout-ac 0
powercfg -change -monitor-timeout-ac 0
powercfg -setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
```
*(This sets the Windows power plan to High Performance and disables standby).*

---

## 🌐 Accessing Dashboard Remotely (From Your Laptop)

If you want to view the Command Center dashboard at `http://<rdp-ip>:10000` directly from your laptop without opening Remote Desktop:

### Option A: Open Port 10000 in Windows Defender Firewall
Run this 1-line command in PowerShell as Administrator on the RDP:
```powershell
New-NetFirewallRule -DisplayName "Discord CommandCenter" -Direction Inbound -LocalPort 10000 -Protocol TCP -Action Allow
```
Now you can open `http://<RDP_PUBLIC_IP>:10000` in your laptop's browser.

### Option B: Cloudflare Tunnel or ngrok (Free & Secure)
On the RDP, install ngrok or Cloudflare Tunnel:
```cmd
winget install Inconshreveable.ngrok
ngrok http 10000
```
ngrok will provide a public HTTPS URL (e.g. `https://xyz.ngrok-free.app`) that you can bookmark on your phone or laptop.

---

## 🛠️ Management Commands Reference

| Script | Purpose |
| :--- | :--- |
| `setup_rdp.bat` | One-click automated setup (venv + dependencies + browser). Run once on new server. |
| `start_rdp.bat` | Runs the 24/7 bot & autonomous X engine with an auto-restart crash watchdog. |
| `update_bot.bat`| 1-click update: Pulls latest code from GitHub and updates pip packages. |
| `stop_bot.bat` | Cleanly stops all bot processes and frees port 10000. |

---

## 🔄 How to Make Changes in the Future

### Scenario 1: You Want Code Updates / New Features / Bug Fixes
1. **Make changes here on your laptop** (or tell the AI assistant to code the new feature/fix).
2. **Push to GitHub**:
   ```cmd
   git add .
   git commit -m "Update feature XYZ"
   git push origin main
   ```
3. **On your RDP server**:
   - Double-click `stop_bot.bat` (or close the running bot window).
   - Double-click `update_bot.bat` (pulls new code and updates packages automatically).
   - Double-click `start_rdp.bat` to run the updated version!

### Scenario 2: Changing Settings / Keywords / AI Prompts (Live UI)
* Open the Web Dashboard at `http://localhost:10000` (or your remote URL).
* Go to the **X Engine** or **Settings** tab.
* You can change **Target Keywords**, **Negative Keywords**, **CTA pitch**, **Daily limits**, or **OpenAI model** directly on the dashboard in real-time — **no code edits or restarts required**.

### Scenario 3: Changing Tokens or Credentials
* On the RDP, open `.env` in Notepad.
* Update `DISCORD_TOKEN`, `OPENAI_API_KEY`, or `X_AUTH_TOKEN`.
* Save the file, run `stop_bot.bat`, and restart with `start_rdp.bat`.

---

## ❓ Frequently Asked Questions

**Q: If I turn off my laptop, will the bot keep posting and replying?**  
**A:** **Yes!** The bot is running entirely on the remote RDP computer. It does not depend on your laptop hardware, battery, or local WiFi.

**Q: Can I watch the X (Twitter) browser run?**  
**A:** **Yes!** Because `HEADLESS=false` is set in `.env`, Google Chrome will open visually right on the RDP desktop. You can see it search keywords, open tweets, draft replies, and post infographic cards.

**Q: What if the internet flickers or Discord reconnects?**  
**A:** The `start_rdp.bat` script has a built-in watchdog loop. If any unexpected network exception occurs, it logs the time and automatically restarts within 5 seconds.
