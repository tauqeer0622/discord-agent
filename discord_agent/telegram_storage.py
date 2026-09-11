"""
telegram_storage.py - High-performance storage manager for scraped Telegram members.
Unified MongoDB Atlas backend with transparent local SQLite/CSV fallback.

Primary data store: MongoDB Atlas collection `discord_agent_db.telegram_users`
Fallback data store: Local SQLite `telegram_local_storage.db` & CSV
"""

import os
import re
import csv
import math
import sqlite3
import logging
from typing import Dict, Any, List, Optional
from pymongo import ASCENDING, DESCENDING, UpdateOne

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DB_PATH = os.path.join(BASE_DIR, "telegram_local_storage.db")
LOCAL_CSV_PATH = os.path.join(BASE_DIR, "scraped_telegram_users.csv")


def get_mongo_collection():
    """Return the active MongoDB telegram_users collection or None if unavailable."""
    try:
        import database
        return database.get_collection("telegram_users")
    except Exception as exc:
        logger.debug("MongoDB not available in telegram_storage: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SQLITE FALLBACK HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_sqlite_connection(db_path: str = LOCAL_DB_PATH) -> sqlite3.Connection:
    """Return an optimized SQLite connection with Row factory and WAL mode."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_telegram_sqlite_table(conn: sqlite3.Connection):
    """Ensure the local sqlite telegram_users table exists."""
    conn.execute("""
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


def ensure_sqlite_indexes(db_path: str = LOCAL_DB_PATH):
    """Ensure SQLite indexes exist."""
    if not os.path.exists(db_path):
        return
    try:
        conn = get_sqlite_connection(db_path)
        cur = conn.cursor()
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tg_source_channel ON telegram_users(source_channel);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tg_username ON telegram_users(username);")
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Could not create SQLite indexes: %s", e)


def import_csv_to_sqlite(csv_file_path: str, db_path: str = LOCAL_DB_PATH) -> int:
    """Import or merge users from a CSV file into local SQLite."""
    if not os.path.exists(csv_file_path):
        return 0
    conn = get_sqlite_connection(db_path)
    init_telegram_sqlite_table(conn)
    cur = conn.cursor()
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
    total = 0
    rows = []
    with open(csv_file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            u_id = str(row.get("user_id", "")).strip()
            if not u_id:
                continue
            rows.append((
                u_id,
                row.get("username", ""),
                row.get("first_name", ""),
                row.get("last_name", ""),
                row.get("phone", ""),
                row.get("source_channel", ""),
                row.get("scraped_at", ""),
            ))
            if len(rows) >= 5000:
                cur.executemany(upsert_sql, rows)
                conn.commit()
                total += len(rows)
                rows = []
        if rows:
            cur.executemany(upsert_sql, rows)
            conn.commit()
            total += len(rows)
    conn.close()
    ensure_sqlite_indexes(db_path)
    return total


# ─────────────────────────────────────────────────────────────────────────────
# CORE STATS & AGGREGATION (MONGODB PRIMARY, SQLITE FALLBACK)
# ─────────────────────────────────────────────────────────────────────────────

def get_telegram_stats(db_path: str = LOCAL_DB_PATH) -> Dict[str, Any]:
    """Return high-level summary statistics of scraped Telegram members from MongoDB."""
    csv_size_mb = 0.0
    if os.path.exists(LOCAL_CSV_PATH):
        csv_size_mb = round(os.path.getsize(LOCAL_CSV_PATH) / (1024 * 1024), 2)

    col = get_mongo_collection()
    if col is not None:
        try:
            total_users = col.count_documents({})
            if total_users > 0:
                distinct_channels = col.distinct("source_channel")
                total_channels = len([c for c in distinct_channels if c and str(c).strip()])
                with_username = col.count_documents({"username": {"$nin": [None, "", "@"]}})
                without_username = max(0, total_users - with_username)

                return {
                    "total_users": total_users,
                    "total_channels": total_channels,
                    "with_username": with_username,
                    "without_username": without_username,
                    "db_size_mb": 0.0,
                    "csv_size_mb": csv_size_mb,
                    "db_exists": True,
                    "storage_backend": "MongoDB Atlas (discord_agent_db)",
                }
        except Exception as exc:
            logger.warning("Failed to fetch Telegram stats from MongoDB, trying SQLite: %s", exc)

    # Fallback to local SQLite
    if os.path.exists(db_path):
        try:
            conn = get_sqlite_connection(db_path)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM telegram_users")
            total_users = cur.fetchone()[0] or 0

            cur.execute("SELECT COUNT(DISTINCT source_channel) FROM telegram_users WHERE source_channel IS NOT NULL AND source_channel != ''")
            total_channels = cur.fetchone()[0] or 0

            cur.execute("SELECT COUNT(*) FROM telegram_users WHERE username IS NOT NULL AND username != '' AND username != '@'")
            with_username = cur.fetchone()[0] or 0

            without_username = max(0, total_users - with_username)
            conn.close()

            db_size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 2)
            return {
                "total_users": total_users,
                "total_channels": total_channels,
                "with_username": with_username,
                "without_username": without_username,
                "db_size_mb": db_size_mb,
                "csv_size_mb": csv_size_mb,
                "db_exists": True,
                "storage_backend": "Local SQLite (Offline Mode)",
            }
        except Exception as exc:
            logger.error("Error querying SQLite Telegram stats: %s", exc)

    return {
        "total_users": 0,
        "total_channels": 0,
        "with_username": 0,
        "without_username": 0,
        "db_size_mb": 0.0,
        "csv_size_mb": csv_size_mb,
        "db_exists": False,
        "storage_backend": "None",
    }


def get_telegram_channels(db_path: str = LOCAL_DB_PATH) -> List[Dict[str, Any]]:
    """Return all distinct source channels sorted by member count descending."""
    col = get_mongo_collection()
    if col is not None:
        try:
            pipeline = [
                {"$match": {"source_channel": {"$nin": [None, ""]}}},
                {"$group": {"_id": "$source_channel", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$project": {"channel": "$_id", "count": 1, "_id": 0}},
            ]
            results = list(col.aggregate(pipeline))
            if results:
                return results
        except Exception as exc:
            logger.warning("Failed to fetch Telegram channels from MongoDB, falling back: %s", exc)

    # Fallback to local SQLite
    if os.path.exists(db_path):
        try:
            conn = get_sqlite_connection(db_path)
            cur = conn.cursor()
            cur.execute("""
                SELECT source_channel, COUNT(*) as member_count
                FROM telegram_users
                WHERE source_channel IS NOT NULL AND source_channel != ''
                GROUP BY source_channel
                ORDER BY member_count DESC
            """)
            rows = cur.fetchall()
            conn.close()
            return [{"channel": r["source_channel"], "count": r["member_count"]} for r in rows]
        except Exception as exc:
            logger.error("Error querying Telegram channels from SQLite: %s", exc)

    return []


def get_paginated_telegram_users(
    page: int = 1,
    limit: int = 50,
    search: Optional[str] = None,
    channel: Optional[str] = None,
    has_username: Optional[str] = None,
    db_path: str = LOCAL_DB_PATH,
) -> Dict[str, Any]:
    """
    Return paginated and filtered list of Telegram members from MongoDB Atlas.
    Falls back gracefully to local SQLite if MongoDB is unreachable.
    """
    try:
        page = max(1, int(page))
    except (ValueError, TypeError):
        page = 1

    try:
        limit = min(200, max(1, int(limit)))
    except (ValueError, TypeError):
        limit = 50

    offset = (page - 1) * limit

    col = get_mongo_collection()
    if col is not None:
        try:
            mongo_query: Dict[str, Any] = {}

            if channel and channel.strip():
                mongo_query["source_channel"] = channel.strip()

            if has_username == "yes":
                mongo_query["username"] = {"$nin": [None, "", "@"]}
            elif has_username == "no":
                mongo_query["username"] = {"$in": [None, "", "@"]}

            if search and search.strip():
                s = search.strip()
                clean_s = s.lstrip("@")
                escaped = re.escape(clean_s)
                raw_escaped = re.escape(s)
                mongo_query["$or"] = [
                    {"user_id": {"$regex": escaped, "$options": "i"}},
                    {"username": {"$regex": escaped, "$options": "i"}},
                    {"first_name": {"$regex": raw_escaped, "$options": "i"}},
                    {"last_name": {"$regex": raw_escaped, "$options": "i"}},
                    {"source_channel": {"$regex": raw_escaped, "$options": "i"}},
                ]

            total_count = col.count_documents(mongo_query)
            cursor = (
                col.find(mongo_query, {"_id": 0})
                .sort([("scraped_at", -1), ("_id", -1)])
                .skip(offset)
                .limit(limit)
            )

            users = []
            for r in cursor:
                u_name = r.get("username") or ""
                if u_name and not u_name.startswith("@"):
                    u_name = f"@{u_name}"

                first = r.get("first_name") or ""
                last = r.get("last_name") or ""
                full_name = f"{first} {last}".strip() or "Telegram User"

                users.append({
                    "user_id": str(r.get("user_id", "")),
                    "username": u_name,
                    "first_name": first,
                    "last_name": last,
                    "full_name": full_name,
                    "phone": r.get("phone") or "",
                    "source_channel": r.get("source_channel") or "Unknown Channel",
                    "scraped_at": r.get("scraped_at") or "",
                })

            total_pages = max(1, math.ceil(total_count / limit))
            return {
                "users": users,
                "total": total_count,
                "page": page,
                "limit": limit,
                "total_pages": total_pages,
            }
        except Exception as exc:
            logger.warning("MongoDB query error in get_paginated_telegram_users, trying SQLite fallback: %s", exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Fallback to local SQLite
    # ─────────────────────────────────────────────────────────────────────────
    if os.path.exists(db_path):
        where_clauses = []
        params = []

        if channel and channel.strip():
            where_clauses.append("source_channel = ?")
            params.append(channel.strip())

        if has_username == "yes":
            where_clauses.append("(username IS NOT NULL AND username != '' AND username != '@')")
        elif has_username == "no":
            where_clauses.append("(username IS NULL OR username = '' OR username = '@')")

        if search and search.strip():
            s = search.strip()
            clean_s = s.lstrip("@")
            where_clauses.append(
                "(user_id LIKE ? OR username LIKE ? OR first_name LIKE ? OR last_name LIKE ? OR source_channel LIKE ?)"
            )
            like_val = f"%{clean_s}%"
            params.extend([like_val, f"%{clean_s}%", f"%{s}%", f"%{s}%", f"%{s}%"])

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        try:
            conn = get_sqlite_connection(db_path)
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM telegram_users {where_sql}", params)
            total_count = cur.fetchone()[0] or 0

            query_sql = f"""
                SELECT user_id, username, first_name, last_name, phone, source_channel, scraped_at
                FROM telegram_users
                {where_sql}
                ORDER BY rowid DESC
                LIMIT ? OFFSET ?
            """
            cur.execute(query_sql, params + [limit, offset])
            rows = cur.fetchall()
            conn.close()

            users = []
            for r in rows:
                u_name = r["username"] or ""
                if u_name and not u_name.startswith("@"):
                    u_name = f"@{u_name}"
                first = r["first_name"] or ""
                last = r["last_name"] or ""
                full_name = f"{first} {last}".strip() or "Telegram User"

                users.append({
                    "user_id": str(r["user_id"]),
                    "username": u_name,
                    "first_name": first,
                    "last_name": last,
                    "full_name": full_name,
                    "phone": r["phone"] or "",
                    "source_channel": r["source_channel"] or "Unknown Channel",
                    "scraped_at": r["scraped_at"] or "",
                })

            total_pages = max(1, math.ceil(total_count / limit))
            return {
                "users": users,
                "total": total_count,
                "page": page,
                "limit": limit,
                "total_pages": total_pages,
            }
        except Exception as exc:
            logger.error("Error querying SQLite paginated users: %s", exc)

    return {
        "users": [],
        "total": 0,
        "page": page,
        "limit": limit,
        "total_pages": 1,
    }


def save_telegram_users(users_list: List[Dict[str, Any]]) -> int:
    """
    Save scraped Telegram users to MongoDB Atlas (and optionally local SQLite backup).
    Returns count of users upserted.
    """
    if not users_list:
        return 0

    total_saved = 0
    col = get_mongo_collection()
    if col is not None:
        try:
            ops = [
                UpdateOne(
                    {"user_id": str(u["user_id"])},
                    {
                        "$set": {
                            "user_id": str(u["user_id"]),
                            "username": u.get("username", "") or "",
                            "first_name": u.get("first_name", "") or "",
                            "last_name": u.get("last_name", "") or "",
                            "phone": u.get("phone", "") or "",
                            "source_channel": u.get("source_channel", "") or "",
                            "scraped_at": u.get("scraped_at", "") or "",
                        }
                    },
                    upsert=True,
                )
                for u in users_list
                if u.get("user_id")
            ]
            if ops:
                result = col.bulk_write(ops, ordered=False)
                total_saved = result.upserted_count + result.modified_count
                logger.info("Saved %d users to MongoDB Atlas (upserted: %d, modified: %d)",
                            len(ops), result.upserted_count, result.modified_count)
        except Exception as exc:
            logger.error("Error saving Telegram users to MongoDB: %s", exc)

    # Also keep local SQLite updated as local offline backup
    try:
        if os.path.exists(LOCAL_DB_PATH) or not col:
            conn = get_sqlite_connection(LOCAL_DB_PATH)
            init_telegram_sqlite_table(conn)
            cur = conn.cursor()
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
                    str(u["user_id"]),
                    u.get("username", "") or "",
                    u.get("first_name", "") or "",
                    u.get("last_name", "") or "",
                    u.get("phone", "") or "",
                    u.get("source_channel", "") or "",
                    u.get("scraped_at", "") or "",
                )
                for u in users_list
                if u.get("user_id")
            ]
            cur.executemany(upsert_sql, rows)
            conn.commit()
            conn.close()
    except Exception as exc:
        logger.debug("SQLite backup error: %s", exc)

    return total_saved
