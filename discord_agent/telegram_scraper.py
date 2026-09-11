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
import sys
from datetime import datetime, timezone

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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

# Local Storage Setup (SQLite on local hard drive - NO cloud quota limits)
LOCAL_DB_FILE = "telegram_local_storage.db"


def init_local_sqlite(db_file=LOCAL_DB_FILE):
    """Create local SQLite table with deduplication on user_id."""
    import sqlite3
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS telegram_users (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            phone TEXT,
            source_channel TEXT,
            scraped_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_users_to_local_sqlite(users_list, db_file=LOCAL_DB_FILE):
    """Save/update users in local SQLite database with zero duplicates."""
    if not users_list:
        return 0
    import sqlite3
    init_local_sqlite(db_file)
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    upsert_sql = """
        INSERT INTO telegram_users (user_id, username, first_name, last_name, phone, source_channel, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            last_name=excluded.last_name,
            phone=excluded.phone,
            source_channel=excluded.source_channel,
            scraped_at=excluded.scraped_at
    """
    rows = [
        (
            u["user_id"],
            u.get("username", ""),
            u.get("first_name", ""),
            u.get("last_name", ""),
            u.get("phone", ""),
            u.get("source_channel", ""),
            u.get("scraped_at", ""),
        )
        for u in users_list
    ]
    cursor.executemany(upsert_sql, rows)
    conn.commit()
    total_saved = cursor.rowcount
    conn.close()
    return total_saved


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
                    await asyncio.sleep(0.35)  # Safe cursor pacing to prevent Telegram FloodWait

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
    # 1. LOCAL STORAGE: SAVE TO CSV (Excel readable)
    # ─────────────────────────────────────────────────────────────
    file_exists = os.path.isfile(output_csv)
    with open(output_csv, mode="a" if file_exists else "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["user_id", "username", "first_name", "last_name", "phone", "source_channel", "scraped_at"])
        if not file_exists:
            writer.writeheader()
        for u in users_scraped:
            writer.writerow(u)
    logger.info("💾 Saved %d users to Local CSV: %s", len(users_scraped), output_csv)

    # ─────────────────────────────────────────────────────────────
    # 2. MONGODB ATLAS: SAVE TO DISCORD_AGENT_DB
    # ─────────────────────────────────────────────────────────────
    if save_to_db:
        try:
            import telegram_storage
            saved_mongo = telegram_storage.save_telegram_users(users_scraped)
            logger.info("☁️ Saved %d users to MongoDB Atlas (discord_agent_db.telegram_users)", len(users_scraped))
        except Exception as exc:
            logger.warning("MongoDB Atlas save error: %s", exc)

    # ─────────────────────────────────────────────────────────────
    # 3. LOCAL STORAGE: SAVE TO LOCAL SQLITE (Offline backup)
    # ─────────────────────────────────────────────────────────────
    try:
        saved_sqlite = save_users_to_local_sqlite(users_scraped)
        logger.info("💾 Saved %d users to Local SQLite database '%s'", len(users_scraped), LOCAL_DB_FILE)
    except Exception as exc:
        logger.warning("Local SQLite save error: %s", exc)

    return users_scraped


async def get_joined_groups_and_channels(client):
    """Scan all groups and channels the logged-in Telegram account is currently in (just like self.guilds in Discord)."""
    dialogs = []
    logger.info("Scanning all groups & channels in your Telegram account...")
    async for d in client.iter_dialogs():
        if d.is_group or d.is_channel:
            dialogs.append(d)
    return dialogs


async def main():
    parser = argparse.ArgumentParser(description="Telegram Group & Channel Member Scraper")
    parser.add_argument("--auto", "-a", action="store_true", help="Auto-scrape ALL groups and channels your account is in (No links needed, just like Discord)")
    parser.add_argument("--targets", "-t", type=str, help="Comma-separated Telegram channel/group usernames or invite links")
    parser.add_argument("--output", "-o", type=str, default="scraped_telegram_users.csv", help="CSV output filename (default: scraped_telegram_users.csv)")
    parser.add_argument("--limit", "-l", type=int, default=10000, help="Maximum members to scrape per target (default: 10000)")
    parser.add_argument("--no-db", action="store_true", help="Do not save to MongoDB (CSV only)")
    parser.add_argument("--force", "-f", action="store_true", help="Force re-scraping targets already present in MongoDB")
    parser.add_argument("--delay", "-d", type=float, default=2.5, help="Safe delay in seconds between targets (default: 2.5s)")
    args = parser.parse_args()

    client = get_telegram_client()

    phone = os.getenv("TELEGRAM_PHONE")
    logger.info("Connecting to Telegram MTProto Gateway...")
    await client.start(phone=phone)
    logger.info("✅ Successfully authenticated with Telegram!")

    targets = []

    # 1. If explicit targets given via CLI flag
    if args.targets:
        targets = [t.strip() for t in args.targets.split(",") if t.strip()]

    # 2. If targets.txt file exists in directory
    elif os.path.isfile("targets.txt") and os.path.getsize("targets.txt") > 0:
        with open("targets.txt", "r", encoding="utf-8") as f:
            targets = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
        if targets:
            logger.info("📄 Loaded %d targets from targets.txt", len(targets))

    # 3. If TELEGRAM_TARGETS set in .env
    elif os.getenv("TELEGRAM_TARGETS"):
        targets = [t.strip() for t in os.getenv("TELEGRAM_TARGETS").split(",") if t.strip()]

    # 4. If --auto flag passed, scrape all joined groups/channels automatically
    elif args.auto:
        dialogs = await get_joined_groups_and_channels(client)
        targets = [d.entity for d in dialogs]
        logger.info("Found %d joined groups/channels to scrape automatically!", len(targets))

    # 5. Interactive prompt: choose between auto-scrape or entering links
    else:
        print("\n" + "=" * 60)
        print("🎯 TELEGRAM MEMBER SCRAPER (CHOOSE MODE)")
        print("=" * 60)
        print("1. [Auto-Scrape ALL Joined Channels & Groups] (NO links needed)")
        print("2. [Enter Specific Channel/Group Links]")
        print("=" * 60)
        choice = input("Enter your choice (1 or 2, default: 1): ").strip()

        if choice == "2":
            raw_input = input("\nEnter Telegram channel/group links (separated by comma): ").strip()
            targets = [t.strip() for t in raw_input.split(",") if t.strip()]
        else:
            dialogs = await get_joined_groups_and_channels(client)
            if not dialogs:
                logger.warning("No joined groups or channels found in this account.")
                return
            print(f"\n✅ Found {len(dialogs)} groups/channels in your Telegram account:")
            for idx, d in enumerate(dialogs, 1):
                chat_type = "Broadcast Channel" if getattr(d.entity, "broadcast", False) else "Group"
                print(f"  {idx}. {d.title} ({chat_type})")
            
            sub_choice = input(f"\nScrape all {len(dialogs)} groups/channels? (Y/n): ").strip().lower()
            if sub_choice == "n":
                indexes = input("Enter comma-separated numbers to scrape (e.g. 1, 3, 5): ").strip()
                chosen_idx = [int(i.strip()) - 1 for i in indexes.split(",") if i.strip().isdigit()]
                targets = [dialogs[i].entity for i in chosen_idx if 0 <= i < len(dialogs)]
            else:
                targets = [d.entity for d in dialogs]

    if not targets:
        logger.error("No targets selected. Exiting.")
        return

    # Check already scraped channels in MongoDB to focus on remaining targets
    already_indexed = {}
    if not args.force:
        try:
            import telegram_storage
            ch_list = telegram_storage.get_telegram_channels()
            already_indexed = {c["channel"].lower().strip(): c["count"] for c in ch_list if c.get("channel")}
            logger.info("Found %d existing communities in MongoDB. Remaining targets will be prioritized.", len(already_indexed))
        except Exception as exc:
            logger.debug("Could not check existing channels: %s", exc)

    total_scraped = 0
    for idx, target in enumerate(targets, 1):
        target_ref = target if isinstance(target, str) else getattr(target, "title", str(target))
        clean_target = str(target_ref).strip().replace("https://t.me/", "").replace("t.me/", "").lower()

        # Check if already indexed
        if not args.force and already_indexed:
            matched_count = None
            for ch_name, count in already_indexed.items():
                if clean_target in ch_name or ch_name in clean_target:
                    matched_count = count
                    break
            if matched_count and matched_count >= 50:
                logger.info("[%d/%d] ⏭️ Skipping '%s' (already has %d members in MongoDB). Use --force to re-scrape.", 
                            idx, len(targets), target_ref, matched_count)
                continue

        logger.info("[%d/%d] 🎯 Scraping target: %s ...", idx, len(targets), target_ref)
        users = await scrape_target(
            client=client,
            target_link=target_ref if isinstance(target, str) else str(getattr(target, "id", target_ref)),
            output_csv=args.output,
            save_to_db=not args.no_db,
            max_members=args.limit,
        )
        total_scraped += len(users)

        if args.delay > 0 and idx < len(targets):
            logger.info("⏳ Pacing pause (%.1fs) before next target to prevent FloodWait...", args.delay)
            await asyncio.sleep(args.delay)

    logger.info("🏁 ALL TARGETS COMPLETE! Total users discovered & saved: %d", total_scraped)
    print(f"\n✅ All done! Users exported to: {args.output}\n")


if __name__ == "__main__":
    asyncio.run(main())

