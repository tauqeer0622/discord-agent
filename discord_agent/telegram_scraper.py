"""
Telegram Channel & Group Member Scraper
=========================================
High-speed, safe Telegram member discovery tool.
Supports:
  - Public & private supergroups / chat groups (full participant scrape)
  - Broadcast channels (scrapes linked discussion group & active commenters)
  - Automatic Admin & Moderator exclusion (skips creators, admins, and bot accounts)
  - Dual storage: exports clean CSV + upserts to MongoDB `telegram_users` collection
  - FloodWait protection & safe rate limiting

Requirements:
  pip install telethon pymongo python-dotenv

Credentials needed from https://my.telegram.org:
  TELEGRAM_API_ID
  TELEGRAM_API_HASH
  TELEGRAM_PHONE
"""

import argparse
import asyncio
import csv
import logging
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("telegram_scraper")

# Admin role title keywords to skip
ADMIN_TITLE_REGEX = re.compile(
    r"\b(admin|administrator|mod|moderator|owner|co-owner|founder|co-founder|staff|lead|core\s*team|community\s*manager|management|manager|officer|head|support)\b",
    re.IGNORECASE,
)

# MongoDB setup (optional)
_mongo_col = None
try:
    from database import get_database
    db = get_database()
    _mongo_col = db["telegram_users"]
    # Ensure unique index on user_id
    _mongo_col.create_index([("user_id", 1)], unique=True)
except Exception:
    logger.debug("MongoDB not connected; results will be saved to CSV only.")


def get_telegram_client(session_name="tg_scraper_session"):
    """Initialize and return Telethon TelegramClient."""
    from telethon import TelegramClient

    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")

    if not api_id or not api_hash:
        print("\n" + "=" * 60)
        print("⚠️  MISSING TELEGRAM API CREDENTIALS")
        print("=" * 60)
        print("To scrape Telegram, you need free credentials from Telegram:")
        print("1. Log in to https://my.telegram.org with your phone number.")
        print("2. Go to 'API development tools' and create an application.")
        print("3. Add the following to your .env file:")
        print("   TELEGRAM_API_ID=your_api_id")
        print("   TELEGRAM_API_HASH=your_api_hash")
        print("   TELEGRAM_PHONE=+your_phone_number")
        print("=" * 60 + "\n")
        
        # Prompt interactively if running in terminal
        api_id = input("Enter your Telegram API ID: ").strip()
        api_hash = input("Enter your Telegram API Hash: ").strip()
        if not api_id or not api_hash:
            raise ValueError("API ID and API Hash are required to connect to Telegram.")

    return TelegramClient(session_name, int(api_id), str(api_hash))


def is_admin_participant(participant, user=None) -> bool:
    """Return True if participant is an administrator, creator, or has admin title."""
    from telethon.tl.types import (
        ChannelParticipantAdmin,
        ChannelParticipantCreator,
    )

    if isinstance(participant, (ChannelParticipantCreator, ChannelParticipantAdmin)):
        return True

    if getattr(participant, "admin_rights", None) is not None:
        return True

    # Check custom admin rank / title (e.g. "Community Mod", "Admin")
    rank = getattr(participant, "rank", "") or ""
    if rank and ADMIN_TITLE_REGEX.search(rank):
        return True

    # Check user bio / username if available
    if user:
        username = getattr(user, "username", "") or ""
        first_name = getattr(user, "first_name", "") or ""
        if ADMIN_TITLE_REGEX.search(username) or ADMIN_TITLE_REGEX.search(first_name):
            return True

    return False


async def scrape_target(
    client,
    target_link: str,
    output_csv: str = "scraped_telegram_users.csv",
    save_to_db: bool = True,
    max_members: int = 10000,
):
    """Scrape users from a Telegram group or channel while strictly excluding admins."""
    from telethon.errors import FloodWaitError
    from telethon.tl.functions.channels import GetFullChannelRequest
    from telethon.tl.types import Channel, Chat

    clean_target = target_link.strip().replace("https://t.me/", "").replace("t.me/", "")
    logger.info("🔍 Resolving target: %s ...", clean_target)

    try:
        entity = await client.get_entity(clean_target)
    except Exception as exc:
        logger.error("❌ Could not find/access target '%s': %s", target_link, exc)
        return []

    title = getattr(entity, "title", clean_target)
    is_broadcast = getattr(entity, "broadcast", False)
    logger.info("✅ Connected to: '%s' (ID: %s, Type: %s)", title, entity.id, "Broadcast Channel" if is_broadcast else "Supergroup/Chat")

    users_scraped = []
    skipped_admins = 0
    skipped_bots = 0

    # ─────────────────────────────────────────────────────────────
    # CASE A: Supergroup / Chat Group (Full Participant Scraper)
    # ─────────────────────────────────────────────────────────────
    if not is_broadcast:
        logger.info("⚡ Scraping participant list (filtering out all admins & bots)...")
        try:
            async for participant in client.iter_participants(entity):
                if len(users_scraped) >= max_members:
                    break

                user = getattr(participant, "user", None) or participant
                if getattr(user, "bot", False):
                    skipped_bots += 1
                    continue

                if getattr(user, "is_self", False):
                    continue

                if is_admin_participant(participant, user):
                    skipped_admins += 1
                    continue

                user_id = str(user.id)
                username = f"@{user.username}" if user.username else ""
                first_name = user.first_name or ""
                last_name = user.last_name or ""
                phone = getattr(user, "phone", "") or ""

                users_scraped.append({
                    "user_id": user_id,
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                    "phone": phone,
                    "source_channel": title,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                })

                if len(users_scraped) % 250 == 0:
                    logger.info("  ↳ Scraped %d valid members so far (%d admins skipped)...", len(users_scraped), skipped_admins)

        except FloodWaitError as fwe:
            logger.warning("⏳ Telegram rate limit hit. Waiting %d seconds...", fwe.seconds)
            await asyncio.sleep(fwe.seconds + 2)
        except Exception as exc:
            logger.error("Error fetching participants: %s", exc)

    # ─────────────────────────────────────────────────────────────
    # CASE B: Broadcast Channel (Scrapes Linked Discussion & Comments)
    # ─────────────────────────────────────────────────────────────
    else:
        logger.info("📢 Target is a Broadcast Channel. Checking for linked Discussion Group...")
        linked_chat = None
        try:
            full_channel = await client(GetFullChannelRequest(entity))
            linked_chat_id = full_channel.full_chat.linked_chat_id
            if linked_chat_id:
                linked_chat = await client.get_entity(linked_chat_id)
                logger.info("🔗 Found linked Discussion Group: '%s' (ID: %s)", getattr(linked_chat, "title", linked_chat_id), linked_chat_id)
        except Exception as exc:
            logger.debug("No linked discussion group found or not accessible: %s", exc)

        # Scrape discussion group participants if available
        if linked_chat:
            logger.info("⚡ Scraping linked discussion group members...")
            try:
                async for participant in client.iter_participants(linked_chat):
                    if len(users_scraped) >= max_members:
                        break

                    user = getattr(participant, "user", None) or participant
                    if getattr(user, "bot", False):
                        skipped_bots += 1
                        continue

                    if getattr(user, "is_self", False):
                        continue

                    if is_admin_participant(participant, user):
                        skipped_admins += 1
                        continue

                    users_scraped.append({
                        "user_id": str(user.id),
                        "username": f"@{user.username}" if user.username else "",
                        "first_name": user.first_name or "",
                        "last_name": user.last_name or "",
                        "phone": getattr(user, "phone", "") or "",
                        "source_channel": title,
                        "scraped_at": datetime.now(timezone.utc).isoformat(),
                    })
            except Exception as exc:
                logger.warning("Could not scrape linked chat participants: %s", exc)

        # Also scrape recent message commenters / participants
        logger.info("💬 Scraping active commenters from recent channel posts...")
        seen_ids = {u["user_id"] for u in users_scraped}
        try:
            async for msg in client.iter_messages(entity, limit=200):
                sender = await msg.get_sender()
                if not sender or getattr(sender, "bot", False):
                    continue
                if str(sender.id) in seen_ids:
                    continue

                # Exclude channel admin/sender
                sender_username = getattr(sender, "username", "") or ""
                if ADMIN_TITLE_REGEX.search(sender_username) or ADMIN_TITLE_REGEX.search(getattr(sender, "first_name", "") or ""):
                    skipped_admins += 1
                    continue

                seen_ids.add(str(sender.id))
                users_scraped.append({
                    "user_id": str(sender.id),
                    "username": f"@{sender.username}" if sender.username else "",
                    "first_name": getattr(sender, "first_name", "") or "",
                    "last_name": getattr(sender, "last_name", "") or "",
                    "phone": getattr(sender, "phone", "") or "",
                    "source_channel": title,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                })
        except Exception as exc:
            logger.warning("Error scraping message commenters: %s", exc)

    logger.info("🎉 Scraping complete for '%s'!", title)
    logger.info("   • Valid Members Found: %d", len(users_scraped))
    logger.info("   • Admins Excluded: %d", skipped_admins)
    logger.info("   • Bots Excluded: %d", skipped_bots)

    if not users_scraped:
        return []

    # ─────────────────────────────────────────────────────────────
    # SAVE TO CSV
    # ─────────────────────────────────────────────────────────────
    file_exists = os.path.isfile(output_csv)
    with open(output_csv, mode="a" if file_exists else "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["user_id", "username", "first_name", "last_name", "phone", "source_channel", "scraped_at"])
        if not file_exists:
            writer.writeheader()
        for u in users_scraped:
            writer.writerow(u)
    logger.info("💾 Saved %d users to CSV: %s", len(users_scraped), output_csv)

    # ─────────────────────────────────────────────────────────────
    # SAVE TO MONGODB (telegram_users collection)
    # ─────────────────────────────────────────────────────────────
    if save_to_db and _mongo_col is not None:
        from pymongo import UpdateOne
        operations = []
        for u in users_scraped:
            doc = {
                "username": u["username"],
                "first_name": u["first_name"],
                "last_name": u["last_name"],
                "phone": u["phone"],
                "source_channel": u["source_channel"],
                "scraped_at": u["scraped_at"],
            }
            operations.append(UpdateOne({"user_id": u["user_id"]}, {"$set": doc}, upsert=True))

        try:
            result = _mongo_col.bulk_write(operations, ordered=False)
            logger.info("💾 Saved %d users to MongoDB 'telegram_users' collection (upserted: %d, modified: %d)",
                        len(users_scraped), result.upserted_count, result.modified_count)
        except Exception as exc:
            logger.warning("Could not persist to MongoDB (cluster may be full): %s", exc)

    return users_scraped


async def main():
    parser = argparse.ArgumentParser(description="Telegram Group & Channel Member Scraper")
    parser.add_argument("--targets", "-t", type=str, help="Comma-separated Telegram channel/group usernames or invite links")
    parser.add_argument("--output", "-o", type=str, default="scraped_telegram_users.csv", help="CSV output filename (default: scraped_telegram_users.csv)")
    parser.add_argument("--limit", "-l", type=int, default=10000, help="Maximum members to scrape per target (default: 10000)")
    parser.add_argument("--no-db", action="store_true", help="Do not save to MongoDB (CSV only)")
    args = parser.parse_args()

    client = get_telegram_client()

    phone = os.getenv("TELEGRAM_PHONE")
    logger.info("Connecting to Telegram MTProto Gateway...")
    await client.start(phone=phone)
    logger.info("✅ Successfully authenticated with Telegram!")

    targets = []
    if args.targets:
        targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    else:
        raw_input = input("\nEnter Telegram channel/group links (separated by comma): ").strip()
        targets = [t.strip() for t in raw_input.split(",") if t.strip()]

    if not targets:
        logger.error("No targets provided. Exiting.")
        return

    total_scraped = 0
    for target in targets:
        users = await scrape_target(
            client=client,
            target_link=target,
            output_csv=args.output,
            save_to_db=not args.no_db,
            max_members=args.limit,
        )
        total_scraped += len(users)

    logger.info("🏁 ALL TARGETS COMPLETE! Total users discovered & saved: %d", total_scraped)
    print(f"\n✅ All done! Users exported to: {args.output}\n")


if __name__ == "__main__":
    asyncio.run(main())
