import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

print('=' * 60)
print('1. TURSO LIBSQL DATABASE STATUS (PRIMARY PROD DB)')
print('=' * 60)
try:
    import turso_database
    t0 = time.time()
    client = turso_database.get_client()
    res = client.execute("SELECT 1").rows
    latency = round((time.time() - t0) * 1000, 1)
    print(f"Status: ONLINE ({latency}ms latency)")
    print(f"Endpoint: {turso_database.TURSO_URL}")

    tables_res = client.execute("SELECT name FROM sqlite_master WHERE type='table'").rows
    table_names = [r[0] for r in tables_res]
    print(f"Tables found: {table_names}")
    for t in table_names:
        try:
            cnt = client.execute(f"SELECT COUNT(*) FROM {t}").rows[0][0]
            print(f"  - {t}: {cnt:,} rows")
        except Exception as e:
            print(f"  - {t}: error ({e})")

    if 'extracted_emails' in table_names:
        recent_emails = client.execute("SELECT email, username, source, extracted_at FROM extracted_emails ORDER BY extracted_at DESC LIMIT 5").rows
        print("\nRecent Extracted Emails in Turso:")
        for r in recent_emails:
            print(f"  - {r[0]} | @{r[1]} | {r[2]} | {r[3]}")
except Exception as e:
    print(f"Turso Error: {e}")

print('\n' + '=' * 60)
print('2. MONGODB ATLAS CLUSTER STATUS')
print('=' * 60)
try:
    import database
    t0 = time.time()
    db = database.get_database()
    db.command('ping')
    latency = round((time.time() - t0) * 1000, 1)
    print(f"Status: ONLINE ({latency}ms latency)")
    print(f"Database: {db.name}")
    collections = db.list_collection_names()
    print(f"Collections found: {collections}")
    for c in collections:
        try:
            cnt = db[c].estimated_document_count()
            print(f"  - {c}: {cnt:,} documents")
        except Exception as e:
            print(f"  - {c}: error ({e})")
except Exception as e:
    print(f"MongoDB Error: {e}")

print('\n' + '=' * 60)
print('3. LOCAL STORAGE STATUS')
print('=' * 60)
for db_file in ['telegram_local_storage.db', 'discord_agent.db']:
    path = os.path.join('d:/discord_agent/discord_agent', db_file)
    if os.path.exists(path):
        size_mb = round(os.path.getsize(path) / (1024 * 1024), 2)
        print(f"  - {db_file}: {size_mb} MB")
    else:
        print(f"  - {db_file}: Not found")
