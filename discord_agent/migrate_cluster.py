"""
MongoDB Cluster-to-Cluster High-Speed Migration Script
======================================================
Migrates all collections, users, channel configs, and indexes
from the old quota-limited MongoDB cluster to the new cluster.
"""

import os
import sys
import time
import certifi
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import BulkWriteError

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv(r"d:\discord_agent\discord_agent\.env")

OLD_URI = os.getenv("MONGODB_URI")
NEW_URI = os.getenv("NEW_MONGODB_URI")
DB_NAME = os.getenv("MONGODB_DB_NAME", "discord_agent_db")

BATCH_SIZE = 5000

def migrate():
    print("=" * 70)
    print("🚀 MONGODB CLUSTER MIGRATION INITIALIZING")
    print("=" * 70)

    if not OLD_URI:
        print("❌ Error: MONGODB_URI missing from .env")
        return
    if not NEW_URI:
        print("❌ Error: NEW_MONGODB_URI missing from .env")
        return

    print("Connecting to Source Cluster (Old)...")
    source_client = MongoClient(OLD_URI, serverSelectionTimeoutMS=10000, tlsCAFile=certifi.where())
    source_client.admin.command("ping")
    source_db = source_client[DB_NAME]
    print("✅ Source Cluster connected!")

    print("Connecting to Destination Cluster (New)...")
    dest_client = MongoClient(NEW_URI, serverSelectionTimeoutMS=10000, tlsCAFile=certifi.where())
    dest_client.admin.command("ping")
    dest_db = dest_client[DB_NAME]
    print("✅ Destination Cluster connected!")

    # Priority collections
    collections_to_migrate = [
        "discord_channels",
        "channel_threads",
        "dm_campaigns",
        "guild_official_stats",
        "prefix_scan_progress",
        "sync_status",
        "discord_messages",
        "reply_rate_limit",
        "migrations",
        "discord_users",  # The largest collection (761k)
    ]

    total_start_time = time.time()

    for col_name in collections_to_migrate:
        src_col = source_db[col_name]
        dst_col = dest_db[col_name]

        total_docs = src_col.estimated_document_count()
        if total_docs == 0:
            total_docs = src_col.count_documents({})

        print(f"\n📦 Migrating Collection: '{col_name}' ({total_docs:,} documents)...")

        if total_docs == 0:
            print(f"  ⏭️  Skipping '{col_name}' (empty).")
            continue

        migrated_count = 0
        batch = []
        col_start_time = time.time()

        cursor = src_col.find({}).batch_size(BATCH_SIZE)

        try:
            for doc in cursor:
                batch.append(doc)
                if len(batch) >= BATCH_SIZE:
                    try:
                        dst_col.insert_many(batch, ordered=False)
                    except BulkWriteError as bwe:
                        pass # Ignore duplicate key errors if resuming
                    migrated_count += len(batch)
                    batch = []
                    
                    elapsed = time.time() - col_start_time
                    rate = migrated_count / elapsed if elapsed > 0 else 0
                    pct = (migrated_count / total_docs) * 100 if total_docs else 100
                    print(f"  ⏳ {migrated_count:,} / {total_docs:,} ({pct:.1f}%) — {rate:.0f} docs/sec", flush=True)

            if batch:
                try:
                    dst_col.insert_many(batch, ordered=False)
                except BulkWriteError:
                    pass
                migrated_count += len(batch)

            elapsed = time.time() - col_start_time
            print(f"  ✅ Completed '{col_name}': {migrated_count:,} docs transferred in {elapsed:.1f}s.", flush=True)

        finally:
            cursor.close()

    # Recreate optimized indexes
    print("\n" + "=" * 70)
    print("⚙️  REBUILDING OPTIMIZED INDEXES ON NEW CLUSTER...")
    print("=" * 70)

    try:
        dest_db.discord_channels.create_index([("channel_id", ASCENDING)], unique=True)
        dest_db.discord_messages.create_index([("channel_id", ASCENDING), ("timestamp", DESCENDING)])
        dest_db.discord_messages.create_index([("timestamp", ASCENDING)])
        dest_db.channel_threads.create_index([("channel_id", ASCENDING)], unique=True)
        dest_db.channel_threads.create_index([("thread_id", ASCENDING)], unique=True)
        dest_db.channel_threads.create_index([("last_activity", ASCENDING)])
        dest_db.discord_users.create_index([("user_id", ASCENDING)], unique=True)
        dest_db.discord_users.create_index([("username", ASCENDING)])
        dest_db.discord_users.create_index([("servers", ASCENDING)])
        dest_db.discord_users.create_index([("is_bot", ASCENDING)])
        print("✅ All indexes successfully rebuilt on destination database!")
    except Exception as e:
        print(f"⚠️ Index rebuild note: {e}")

    total_elapsed = time.time() - total_start_time
    print("\n" + "=" * 70)
    print(f"🎉 MIGRATION COMPLETE! Total Time: {total_elapsed:.1f} seconds")
    print("=" * 70)

if __name__ == "__main__":
    migrate()
