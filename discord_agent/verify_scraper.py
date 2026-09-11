import asyncio
import os
import sys
import sqlite3
from dotenv import load_dotenv

# Set UTF-8 encoding for Windows terminals
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest

api_id = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")

async def verify_all():
    if not api_id or not api_hash:
        print("Missing TELEGRAM_API_ID or TELEGRAM_API_HASH in .env")
        return

    client = TelegramClient("tg_scraper_session", int(api_id), api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        print("Telegram session is not authorized.")
        return

    conn = sqlite3.connect("telegram_local_storage.db")
    cur = conn.cursor()

    targets_file = "targets.txt"
    if not os.path.exists(targets_file):
        print("targets.txt not found.")
        return

    with open(targets_file, "r", encoding="utf-8") as f:
        targets = [line.strip().replace("https://t.me/", "").replace("@", "").rstrip("/") for line in f if line.strip()]

    print("\n" + "=" * 95)
    print(f"{'Target Target':<22} | {'Community Title':<26} | {'Type':<12} | {'Official':<10} | {'Scraped in DB'}")
    print("=" * 95)

    supergroup_total_official = 0
    supergroup_total_scraped = 0

    for t in targets:
        try:
            entity = await client.get_entity(t)
            title = entity.title
            
            # Official count
            official_count = "N/A"
            official_num = 0
            try:
                full = await client(GetFullChannelRequest(entity))
                official_num = getattr(full.full_chat, "participants_count", 0) or 0
                official_count = f"{official_num:,}" if official_num else "N/A"
            except Exception:
                official_num = getattr(entity, "participants_count", 0) or 0
                official_count = f"{official_num:,}" if official_num else "N/A"

            # Chat Type
            if getattr(entity, "megagroup", False):
                ctype = "Supergroup"
            elif getattr(entity, "broadcast", False):
                ctype = "Broadcast"
            else:
                ctype = "Group Chat"

            # Check DB count
            cur.execute("SELECT COUNT(*) FROM telegram_users WHERE source_channel = ?", (title,))
            scraped_count = cur.fetchone()[0]

            if scraped_count == 0:
                cur.execute("SELECT COUNT(*) FROM telegram_users WHERE source_channel LIKE ?", (f"%{title[:8]}%",))
                scraped_count = cur.fetchone()[0]

            # Telegram API note
            limit_note = ""
            if ctype == "Supergroup" and official_num > 10000:
                limit_note = " (10k TG API Cap)"
            elif ctype == "Broadcast":
                limit_note = " (Subscribers Hidden)"

            display_title = title[:24] + "…" if len(title) > 25 else title
            print(f"{t[:20]:<22} | {display_title:<26} | {ctype:<12} | {official_count:<10} | {scraped_count:,}{limit_note}")

        except Exception as e:
            err_msg = str(e).split("(")[0].strip()
            print(f"{t[:20]:<22} | {'[Inaccessible / Dead Link]':<26} | {'N/A':<12} | {'0':<10} | 0 ({err_msg[:25]})")

    conn.close()
    await client.disconnect()
    print("=" * 95 + "\n")

if __name__ == "__main__":
    asyncio.run(verify_all())
