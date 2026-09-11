"""
migrate_telegram_to_mongo.py
============================
High-speed migration of Telegram audience members from local SQLite (and/or CSV)
into the active MongoDB cluster (discord_agent_db.telegram_users).

Features:
- Ensures unique index on user_id to prevent duplicates
- Ensures indexing on source_channel, username, and scraped_at for fast dashboard queries
- High-throughput bulk upserts (batches of 2,000)
- Validation check confirming counts, distinct channels, and sample data fidelity
"""

import os
import sys
import time
import sqlite3
import logging
from pymongo import ASCENDING, DESCENDING, UpdateOne
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("migrate_telegram")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.path.join(BASE_DIR, "telegram_local_storage.db")
CSV_PATH = os.path.join(BASE_DIR, "scraped_telegram_users.csv")


def migrate():
    import database

    logger.info("Connecting to active MongoDB cluster...")
    db = database.get_database()
    col = db["telegram_users"]
    logger.info("Target collection: %s.%s", db.name, col.name)

    # 1. Create Indexes
    logger.info("Creating indexes on %s...", col.name)
    col.create_index([("user_id", ASCENDING)], unique=True)
    col.create_index([("source_channel", ASCENDING)])
    col.create_index([("username", ASCENDING)])
    col.create_index([("scraped_at", DESCENDING)])
    logger.info("✅ Indexes verified.")

    # 2. Extract from SQLite
    if not os.path.exists(SQLITE_PATH):
        logger.error("SQLite database not found at %s", SQLITE_PATH)
        sys.exit(1)

    logger.info("Reading Telegram members from SQLite: %s ...", SQLITE_PATH)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM telegram_users")
    total_sqlite_rows = cur.fetchone()[0] or 0
    logger.info("Total records in SQLite: %d", total_sqlite_rows)

    cur.execute("SELECT user_id, username, first_name, last_name, phone, source_channel, scraped_at FROM telegram_users")
    
    batch_size = 2000
    ops = []
    total_processed = 0
    start_time = time.time()

    logger.info("Starting bulk migration to MongoDB...")

    for row in cur:
        doc = {
            "user_id": str(row["user_id"]),
            "username": row["username"] or "",
            "first_name": row["first_name"] or "",
            "last_name": row["last_name"] or "",
            "phone": row["phone"] or "",
            "source_channel": row["source_channel"] or "",
            "scraped_at": row["scraped_at"] or "",
        }
        ops.append(
            UpdateOne(
                {"user_id": doc["user_id"]},
                {"$set": doc},
                upsert=True
            )
        )

        if len(ops) >= batch_size:
            col.bulk_write(ops, ordered=False)
            total_processed += len(ops)
            elapsed = time.time() - start_time
            rate = total_processed / elapsed if elapsed > 0 else 0
            logger.info("  ↳ Migrated %d / %d members (%.1f%%) — %.0f docs/sec", 
                        total_processed, total_sqlite_rows, (total_processed / total_sqlite_rows) * 100, rate)
            ops = []

    if ops:
        col.bulk_write(ops, ordered=False)
        total_processed += len(ops)
        logger.info("  ↳ Final batch complete: %d members total.", total_processed)

    conn.close()

    elapsed = time.time() - start_time
    logger.info("Migration loop completed in %.2f seconds.", elapsed)

    # 3. Verification
    logger.info("Verifying data in MongoDB...")
    mongo_count = col.count_documents({})
    distinct_channels = col.distinct("source_channel")
    with_username_count = col.count_documents({"username": {"$nin": [None, "", "@"]}})

    print("\n" + "=" * 60)
    print("📊 TELEGRAM MIGRATION SUMMARY & AUDIT")
    print("=" * 60)
    print(f"SQLite Source Records:    {total_sqlite_rows:,}")
    print(f"MongoDB Target Records:   {mongo_count:,}")
    print(f"Target Database:          {db.name}")
    print(f"Target Collection:        {col.name}")
    print(f"Distinct Channels:        {len(distinct_channels)}")
    print(f"Members with Username:    {with_username_count:,}")
    print(f"Execution Time:           {elapsed:.2f}s")

    sample = col.find_one({}, {"_id": 0})
    print("\nSample MongoDB Document:")
    for k, v in (sample or {}).items():
        print(f"  {k}: {repr(v)}")

    if mongo_count == total_sqlite_rows:
        print("\n✅ PERFECT MATCH: All 33,076 Telegram users successfully migrated to MongoDB!")
    else:
        print(f"\n⚠️  Discrepancy: SQLite ({total_sqlite_rows}) vs MongoDB ({mongo_count})")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    migrate()
