import os
import logging
from dotenv import load_dotenv

# Load environment variables (resolves cwd, local package dir, and project root)
load_dotenv()
_local_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_local_env):
    load_dotenv(_local_env)
_parent_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_parent_env):
    load_dotenv(_parent_env)

# Setup logging
LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_STR, logging.INFO)

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Core Settings
def _clean_env_value(name):
    value = os.getenv(name)
    if value is None:
        return None
    cleaned = value.strip().strip('"').strip("'")
    prefix = f"{name}="
    if cleaned.startswith(prefix):
        cleaned = cleaned[len(prefix):].strip().strip('"').strip("'")
    return cleaned


DISCORD_TOKEN = _clean_env_value("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    logging.error("DISCORD_TOKEN is missing in environment variables.")

CONTROL_SERVER_ID = int(os.getenv("CONTROL_SERVER_ID", "0"))
CONTROL_CHANNEL_ID = int(os.getenv("CONTROL_CHANNEL_ID", "0"))
OPENAI_API_KEY = _clean_env_value("OPENAI_API_KEY")
ADMIN_PASSWORD = _clean_env_value("ADMIN_PASSWORD") or "admin123"

if not CONTROL_SERVER_ID or not CONTROL_CHANNEL_ID:
    logging.error("CONTROL_SERVER_ID or CONTROL_CHANNEL_ID is missing.")

# Target Channels (as per Admin Panel structure)
TARGET_CHANNELS = [
    {"label": "Market cipher - btc and eth", "channel_id": 1184732195525513297},
    {"label": "Market cipher - shitcoins", "channel_id": 1184727619363667978},
    {"label": "Jayson Casper - general discussion", "channel_id": 1226681998123339796}
]

TARGET_CHANNEL_IDS = [c["channel_id"] for c in TARGET_CHANNELS]

# Discord OAuth2 Credentials
DISCORD_CLIENT_ID = _clean_env_value("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = _clean_env_value("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = _clean_env_value("DISCORD_REDIRECT_URI") or "http://localhost:8080/api/oauth2/callback"
DISCORD_BOT_TOKEN = _clean_env_value("DISCORD_BOT_TOKEN")

# Database Backend (Turso libSQL primary, MongoDB Atlas optional backup)
USE_TURSO = os.getenv("USE_TURSO", "true").lower() in ("true", "1")
TURSO_DATABASE_URL = _clean_env_value("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = _clean_env_value("TURSO_AUTH_TOKEN")
MONGODB_URI = _clean_env_value("NEW_MONGODB_URI") or _clean_env_value("MONGODB_URI")
MONGODB_DB_NAME = _clean_env_value("MONGODB_DB_NAME") or "discord_agent_db"
