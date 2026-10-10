"""
Telegram Bio Email Scraper
==========================
Inspects the public "About" / Bio of scraped Telegram users from MongoDB,
extracts valid email addresses, saves results to `extracted_emails` collection,
and exports live to `extracted_telegram_emails.csv`.

Smart Priority Queue:
  1. High-intent profiles (crypto, trader, ceo, founder, collab, media, team, dev)
  2. Users with public usernames
  3. All remaining Telegram users

Features:
  - Resumable: tracks `bio_scanned: True` in MongoDB so it never re-scans.
  - Safe FloodWait handling: sleeps dynamically if Telegram requests backoff.
  - Live CSV export: appends newly found emails in real-time.
"""

import asyncio
import csv
import logging
import os
import re
import sys
from datetime import datetime, timezone

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError, UserIdInvalidError
from telethon.tl.functions.users import GetFullUserRequest

import database

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("telegram_bio_scraper")

CSV_FILE = "extracted_telegram_emails.csv"
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

IGNORE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".js", ".css")
IGNORE_DOMAINS = ("example.com", "telegram.org", "sentry.io", "domain.com", "email.com")

HIGH_INTENT_KEYWORDS = [
    "crypto", "trade", "ceo", "founder", "official", "support",
    "collab", "market", "team", "manager", "media", "press",
    "contact", "vip", "business", "invest", "agency", "bot",
    "sol", "eth", "btc", "lead", "dev", "admin"
]
HIGH_INTENT_REGEX = re.compile(r"(" + "|".join(HIGH_INTENT_KEYWORDS) + r")", re.IGNORECASE)


def is_valid_email(email_str: str) -> bool:
    e = email_str.strip().lower()
    if any(e.endswith(ext) for ext in IGNORE_EXTS):
        return False
    if any(d in e for d in IGNORE_DOMAINS):
        return False
    parts = e.split("@")
    if len(parts) != 2:
        return False
    domain = parts[1]
    if "." not in domain or len(domain.split(".")[-1]) < 2:
        return False
    return True


def init_csv():
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Platform",
                "User ID",
                "Username",
                "Full Name",
                "Phone",
                "Email",
                "Bio",
                "Source Channel",
                "Extracted At"
            ])


def append_email_to_csv(user_id, username, full_name, phone, email, bio, channel):
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Telegram",
            str(user_id),
            username or "",
            full_name or "",
            phone or "",
            email,
            (bio or "").replace("\n", " "),
            channel or "",
            datetime.now(timezone.utc).isoformat()
        ])


async def run_bio_email_scraper(limit: int = None, delay: float = 0.85):
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")

    if not api_id or not api_hash:
        logger.error("Missing TELEGRAM_API_ID or TELEGRAM_API_HASH in .env!")
        return

    init_csv()
    database.ensure_email_indexes()
    db = database.get_database()

    # Load all Telegram users into memory for fast priority sorting
    logger.info("Loading Telegram users to build priority queue...")
    all_users = list(db.telegram_users.find({}, {
        "_id": 1,
        "user_id": 1,
        "username": 1,
        "first_name": 1,
        "last_name": 1,
        "phone": 1,
        "source_channel": 1,
        "bio_scanned": 1
    }))

    unscanned = [u for u in all_users if not u.get("bio_scanned")]
    logger.info("Found %d unscanned Telegram users (out of %d total).", len(unscanned), len(all_users))

    if not unscanned:
        logger.info("All Telegram users have already been scanned for bios!")
        return

    # Build 3-tier smart priority queue
    tier1_high_intent = []
    tier2_with_username = []
    tier3_remaining = []

    for u in unscanned:
        uname = u.get("username") or ""
        first = u.get("first_name") or ""
        last = u.get("last_name") or ""
        text = f"{uname} {first} {last}"

        if HIGH_INTENT_REGEX.search(text):
            tier1_high_intent.append(u)
        elif uname.strip():
            tier2_with_username.append(u)
        else:
            tier3_remaining.append(u)

    logger.info("Priority Tier 1 (High-Intent Founders/Traders/Devs): %d users", len(tier1_high_intent))
    logger.info("Priority Tier 2 (Users with Public Username): %d users", len(tier2_with_username))
    logger.info("Priority Tier 3 (Remaining Community Members): %d users", len(tier3_remaining))

    user_queue = tier1_high_intent + tier2_with_username + tier3_remaining
    if limit:
        user_queue = user_queue[:limit]

    logger.info("Total users queued for bio email extraction: %d", len(user_queue))

    client = TelegramClient("tg_scraper_session", int(api_id), str(api_hash))
    await client.connect()

    if not await client.is_user_authorized():
        logger.error("Telegram session is not authorized! Run telegram_scraper.py first to log in.")
        await client.disconnect()
        return

    me = await client.get_me()
    logger.info("Connected to Telegram as %s (ID: %s)", me.first_name, me.id)

    scanned_count = 0
    found_emails_count = 0

    try:
        for u in user_queue:
            uid_str = u.get("user_id")
            if not uid_str:
                continue

            try:
                uid = int(uid_str)
            except ValueError:
                continue

            uname = u.get("username", "") or ""
            first = u.get("first_name", "") or ""
            last = u.get("last_name", "") or ""
            full_name = f"{first} {last}".strip()
            phone = u.get("phone", "") or ""
            channel = u.get("source_channel", "") or ""

            bio = ""
            extracted_emails = []

            try:
                full_user = await client(GetFullUserRequest(uid))
                bio = getattr(full_user.full_user, "about", "") or ""

                # Check for emails in bio
                matches = EMAIL_REGEX.findall(bio)
                valid_emails = [m for m in matches if is_valid_email(m)]

                if valid_emails:
                    for em in set(valid_emails):
                        found_emails_count += 1
                        logger.info("🎯 FOUND EMAIL #%d: %s (%s) -> %s", found_emails_count, uname or full_name, uid, em)

                        # Save to CSV
                        append_email_to_csv(uid, uname, full_name, phone, em, bio, channel)

                        # Save to MongoDB extracted_emails collection
                        database.save_extracted_email(
                            email=em.lower(),
                            platform="telegram",
                            user_id=str(uid),
                            username=uname,
                            full_name=full_name,
                            phone=phone,
                            bio=bio,
                            source=channel,
                            metadata={"scraped_via": "telegram_bio_scraper"}
                        )
                    extracted_emails = valid_emails
                else:
                    if bio:
                        logger.debug("Bio for %s: %s", uname or uid, bio[:50])

                # Mark user as scanned in MongoDB
                db.telegram_users.update_one(
                    {"_id": u["_id"]},
                    {
                        "$set": {
                            "bio": bio,
                            "bio_scanned": True,
                            "emails": extracted_emails,
                            "bio_scanned_at": datetime.now(timezone.utc).isoformat(),
                        }
                    }
                )

                scanned_count += 1
                if scanned_count % 25 == 0:
                    logger.info("Progress: Scanned %d users | Found %d emails so far.", scanned_count, found_emails_count)

                await asyncio.sleep(delay)

            except FloodWaitError as fwe:
                logger.warning("Telegram FloodWait: Sleeping %d seconds as requested by Telegram...", fwe.seconds)
                await asyncio.sleep(fwe.seconds + 2)
            except (UserPrivacyRestrictedError, UserIdInvalidError) as e:
                db.telegram_users.update_one(
                    {"_id": u["_id"]},
                    {"$set": {"bio_scanned": True, "bio": "", "bio_error": str(e)}}
                )
                scanned_count += 1
                await asyncio.sleep(delay * 0.5)
            except Exception as exc:
                logger.debug("Error checking user %s: %s", uid, exc)
                db.telegram_users.update_one(
                    {"_id": u["_id"]},
                    {"$set": {"bio_scanned": True, "bio": "", "bio_error": str(exc)}}
                )
                scanned_count += 1
                await asyncio.sleep(delay * 0.5)

    except KeyboardInterrupt:
        logger.info("Scraper paused by user. Progress is saved.")
    finally:
        await client.disconnect()
        logger.info("Finished: Scanned %d users | Total emails found: %d. Saved to %s", scanned_count, found_emails_count, CSV_FILE)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scrape emails from Telegram user bios")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of users to scan")
    parser.add_argument("--delay", type=float, default=0.85, help="Delay between profile requests (default: 0.85s)")
    args = parser.parse_args()

    asyncio.run(run_bio_email_scraper(limit=args.limit, delay=args.delay))
