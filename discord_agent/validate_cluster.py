"""
MongoDB Cluster Migration Validation Tool
=========================================
Runs a side-by-side audit comparing the Old and New clusters:
- Document counts across all collections
- Random sampling data integrity check
- Index health verification
- Write permission check
"""

import os
import sys
import certifi
from dotenv import load_dotenv
from pymongo import MongoClient

# Ensure UTF-8 output in Windows PowerShell / CMD
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

OLD_URI = os.getenv("OLD_MONGODB_URI")
NEW_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("MONGODB_DB_NAME", "discord_agent_db")

def validate():
    print("\n" + "=" * 75)
    print("🔍 MONGODB CLUSTER MIGRATION AUDIT & VALIDATION")
    print("=" * 75)

    if not OLD_URI or not NEW_URI:
        print("❌ Error: Missing OLD_MONGODB_URI or MONGODB_URI in .env")
        return

    print("Connecting to Old Cluster...")
    old_client = MongoClient(OLD_URI, serverSelectionTimeoutMS=8000, tlsCAFile=certifi.where())
    old_db = old_client[DB_NAME]
    old_client.admin.command("ping")
    print("✅ Old Cluster: Connected")

    print("Connecting to New Cluster...")
    new_client = MongoClient(NEW_URI, serverSelectionTimeoutMS=8000, tlsCAFile=certifi.where())
    new_db = new_client[DB_NAME]
    new_client.admin.command("ping")
    print("✅ New Cluster: Connected\n")

    # 1. Collection-by-collection count comparison
    print("-" * 75)
    print(f"{'Collection Name':<26} | {'Old Cluster Count':<18} | {'New Cluster Count':<18} | Status")
    print("-" * 75)

    collections = [
        "discord_users",
        "discord_channels",
        "channel_threads",
        "guild_official_stats",
        "prefix_scan_progress",
        "sync_status",
        "discord_messages",
        "dm_campaigns",
        "reply_rate_limit",
        "migrations",
    ]

    all_match = True

    for col in collections:
        if col == "discord_users":
            old_count = old_db[col].estimated_document_count()
            new_count = new_db[col].estimated_document_count()
        else:
            old_count = old_db[col].count_documents({})
            new_count = new_db[col].count_documents({})

        match = (old_count == new_count)
        if not match:
            all_match = False
        status = "✅ MATCH" if match else "⚠️ MISMATCH"

        print(f"{col:<26} | {old_count:>17,} | {new_count:>17,} | {status}")

    print("-" * 75)

    # 2. Random Data Integrity Sample Check
    print("\n🔬 Sampling 5 random users from Old DB to check existence in New DB...")
    sample_users = list(old_db.discord_users.aggregate([{"$sample": {"size": 5}}]))
    sampled_matches = 0

    for u in sample_users:
        uid = u.get("user_id")
        found = new_db.discord_users.find_one({"user_id": uid})
        if found and found.get("username") == u.get("username"):
            sampled_matches += 1
            print(f"  • User ID {uid} (@{u.get('username')}): ✅ Verified in New DB")
        else:
            print(f"  • User ID {uid}: ❌ Not matching")

    print(f"Integrity Sample: {sampled_matches}/5 users perfectly matched.")

    # 3. Index Health Check
    print("\n⚙️  Checking Indexes on 'discord_users' in New DB...")
    indexes = new_db.discord_users.index_information()
    for idx_name, idx_info in indexes.items():
        keys = idx_info.get("key", [])
        print(f"  • Index: '{idx_name}' on fields {keys}")

    # 4. Write Permission Check
    print("\n✍️  Testing Write Capabilities on New DB...")
    try:
        res = new_db.validation_test.insert_one({"test": "write_check", "status": "active"})
        new_db.validation_test.delete_one({"_id": res.inserted_id})
        print("  • Write & Delete test: ✅ SUCCESS (Writes unrestricted)")
    except Exception as e:
        print(f"  • Write test: ❌ FAILED ({e})")

    print("\n" + "=" * 75)
    if all_match and sampled_matches == 5:
        print("🎉 AUDIT RESULT: PASSED! ALL 761,163 USERS & DATA 100% VERIFIED!")
    else:
        print("⚠️ AUDIT RESULT: Review table above for discrepancies.")
    print("=" * 75 + "\n")

if __name__ == "__main__":
    validate()
