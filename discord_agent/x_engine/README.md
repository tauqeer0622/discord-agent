# ApexPulse • Autonomous X Commenting & Growth Engine

An intelligent, autonomous social media engagement bot designed to discover real-time posts discussing **Crypto, Stocks, and Bitcoin (BTC)** on X (formerly Twitter), synthesize authentic, value-first comments introducing your company, and post replies with strict anti-ban and anti-spam protections.

---

## Key Features

1. **Autonomous Post Discovery**:
   - Continuously scans X's Live stream for targeted market discussions: `crypto`, `BTC`, `bitcoin`, `stocks`, `$BTC`, `$ETH`, etc.
   - Built-in negative keyword filtering to skip spam, pump-and-dumps, giveaways, and scam airdrops.

2. **Context-Aware Comment Engine**:
   - **Smart Dynamic Template Mode**: Over 50+ rotating, multi-part contextual templates tailored specifically for BTC, Stocks, and Altcoins without needing external API keys.
   - **LLM Mode (Optional)**: Connect your OpenAI API key (`gpt-4o-mini`) for fully personalized, AI-crafted replies.
   - Strictly enforces Twitter length limits (< 260 characters) and natural, conversational tone.

3. **Anti-Ban & Anti-Spam Protection**:
   - **Persistent Browser Profile**: Logs in once safely via Playwright; no fragile cookie exporting needed.
   - **Human-like typing emulation**: Natural randomized keystroke timing and navigation jitter.
   - **Pacing delays**: Configurable cooldowns (90s–240s between comments).
   - **Daily quota cap**: Automatic daily hard limit (default 25 comments/day) to prevent platform flags.
   - **SQLite Deduplication**: Guarantees zero duplicate replies to the same tweet.
   - **Dry-Run Mode**: Test and verify discovered posts and generated comments in real-time without publishing.

4. **Web Management Dashboard**:
   - Modern glassmorphic dark-mode control center at `http://127.0.0.1:8000`.
   - Real-time metrics: Posts Scanned, Daily Comments, Live vs. Dry-Run counts.
   - Live Feed table with direct links to target tweets.
   - Live AI Comment Simulator.
   - Dynamic configuration drawer (Company Name, Pitch, Keywords, Rate Limits).

---

## Quick Start

### 1. Launch the Dashboard
Run the following command in your terminal:

```bash
python run.py
```

Open your browser and navigate to:
```
http://127.0.0.1:8000
```

### 2. Log in to X (One-Time Setup)
1. Click the **"X Account Login"** button on the top-right of the dashboard.
2. A visible browser window will open to `x.com/login`.
3. Log into your account as usual (including 2FA if enabled).
4. Close the browser window once logged in. Your session is now saved in `./browser_data`!

### 3. Test with Dry-Run Mode
1. Keep **Dry Run Mode** checked in the Settings tab.
2. Click **"Start Automation"** on the dashboard.
3. Watch the system scan for crypto/stock tweets and generate comments in the **Comments & Feed** tab.

### 4. Switch to Live Commenting
When you are satisfied with the replies:
1. Go to the **Settings** tab.
2. Uncheck **Dry Run Mode** and click **Save Changes** (or start with `python run.py --live`).
3. The engine will now publish live replies to target tweets with anti-spam cooldowns.

---

## CLI Headless Mode

To run in the background as a headless service without the web dashboard:

```bash
# Run in Dry-Run Mode
python run.py --cli

# Run in Live Mode
python run.py --cli --live
```

---

## Configuration (`.env`)

You can edit settings either via the Web Dashboard or directly in `.env`:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `COMPANY_NAME` | Name of your company | `ApexTrading` |
| `COMPANY_ONE_LINER` | Short pitch of your product/service | See `.env` |
| `COMPANY_CTA` | Call to action in comments | See `.env` |
| `TARGET_KEYWORDS` | Comma-separated search terms | `crypto, BTC, bitcoin, stocks, $BTC, $ETH` |
| `NEGATIVE_KEYWORDS` | Words that trigger skipping a tweet | `airdrop, giveaway, whatsapp, presale` |
| `DRY_RUN` | Enable safe simulation | `true` |
| `MIN_DELAY_SECONDS` | Minimum delay between replies | `90` |
| `MAX_DELAY_SECONDS` | Maximum delay between replies | `240` |
| `DAILY_COMMENT_LIMIT`| Maximum replies allowed in 24h | `25` |
| `OPENAI_API_KEY` | Optional OpenAI API Key | `""` |

---

## Project Structure

```
d:\x_automation\
├── browser_controller.py   # Playwright automation & session handling
├── comment_generator.py    # LLM & smart contextual template engine
├── config.py               # Pydantic settings & env management
├── database.py             # SQLite persistence, audit log & stats
├── engine.py               # Autonomous background loop orchestrator
├── run.py                  # Entrypoint for Web Dashboard & CLI
├── server.py               # FastAPI REST API & static file server
├── requirements.txt        # Python dependencies
├── .env                    # System configuration
├── data/
│   └── automation.db       # SQLite database (auto-created)
├── static/
│   ├── index.html          # Web Dashboard UI
│   ├── style.css           # Glassmorphic dark theme stylesheet
│   └── app.js              # Real-time dashboard client
└── tests/
    └── test_system.py      # Automated unit & integration tests
```
