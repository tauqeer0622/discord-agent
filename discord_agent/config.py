import os
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_STR, logging.INFO)

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Core Settings
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    logging.error("DISCORD_TOKEN is missing in environment variables.")

CONTROL_SERVER_ID = int(os.getenv("CONTROL_SERVER_ID", "0"))
CONTROL_CHANNEL_ID = int(os.getenv("CONTROL_CHANNEL_ID", "0"))

if not CONTROL_SERVER_ID or not CONTROL_CHANNEL_ID:
    logging.error("CONTROL_SERVER_ID or CONTROL_CHANNEL_ID is missing.")

# Target Channels (as per Admin Panel structure)
TARGET_CHANNELS = [
    {"label": "Market cipher - btc and eth", "channel_id": 1184732195525513297},
    {"label": "Market cipher - shitcoins", "channel_id": 1184727619363667978},
    {"label": "Jayson Casper - general discussion", "channel_id": 1226681998123339796}
]

TARGET_CHANNEL_IDS = [c["channel_id"] for c in TARGET_CHANNELS]
