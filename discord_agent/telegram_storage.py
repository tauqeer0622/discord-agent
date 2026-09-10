"""
telegram_storage.py - High-performance local storage manager for scraped Telegram members.
Reads and writes directly to local SQLite database (telegram_local_storage.db) and CSV.
NEVER uses or queries MongoDB Atlas to preserve free-tier cloud quotas.
"""

import os
import math
import sqlite3
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DB_PATH = os.path.join(BASE_DIR, "telegram_local_storage.db")
LOCAL_CSV_PATH = os.path.join(BASE_DIR, "scraped_telegram_users.csv")


def get_db_connection(db_path: str = LOCAL_DB_PATH) -> sqlite3.Connection:
    """Return an optimized SQLite connection with Row factory and WAL mode."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def ensure_indexes(db_path: str = LOCAL_DB_PATH):
    """Ensure indexes exist for blazing-fast filtering across 30k+ records."""
    if not os.path.exists(db_path):
        return
    try:
        conn = get_db_connection(db_path)
        cur = conn.cursor()
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tg_source_channel ON telegram_users(source_channel);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tg_username ON telegram_users(username);")
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Could not create SQLite indexes: %s", e)


def get_telegram_stats(db_path: str = LOCAL_DB_PATH) -> Dict[str, Any]:
    """Return high-level summary statistics of scraped Telegram members."""
    if not os.path.exists(db_path):
        return {
            "total_users": 0,
            "total_channels": 0,
            "with_username": 0,
            "without_username": 0,
            "db_size_mb": 0.0,
            "csv_size_mb": 0.0,
            "db_exists": False,
        }

    ensure_indexes(db_path)
    db_size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 2)
    csv_size_mb = 0.0
    if os.path.exists(LOCAL_CSV_PATH):
        csv_size_mb = round(os.path.getsize(LOCAL_CSV_PATH) / (1024 * 1024), 2)

    try:
        conn = get_db_connection(db_path)
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM telegram_users")
        total_users = cur.fetchone()[0] or 0

        cur.execute("SELECT COUNT(DISTINCT source_channel) FROM telegram_users WHERE source_channel IS NOT NULL AND source_channel != ''")
        total_channels = cur.fetchone()[0] or 0

        cur.execute("SELECT COUNT(*) FROM telegram_users WHERE username IS NOT NULL AND username != '' AND username != '@'")
        with_username = cur.fetchone()[0] or 0

        without_username = max(0, total_users - with_username)
        conn.close()

        return {
            "total_users": total_users,
            "total_channels": total_channels,
            "with_username": with_username,
            "without_username": without_username,
            "db_size_mb": db_size_mb,
            "csv_size_mb": csv_size_mb,
            "db_exists": True,
        }
    except Exception as exc:
        logger.error("Error querying Telegram stats: %s", exc)
        return {
            "total_users": 0,
            "total_channels": 0,
            "with_username": 0,
            "without_username": 0,
            "db_size_mb": db_size_mb,
            "csv_size_mb": csv_size_mb,
            "error": str(exc),
            "db_exists": True,
        }


def get_telegram_channels(db_path: str = LOCAL_DB_PATH) -> List[Dict[str, Any]]:
    """Return all distinct source channels sorted by member count descending."""
    if not os.path.exists(db_path):
        return []

    try:
        conn = get_db_connection(db_path)
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
        logger.error("Error querying Telegram channels: %s", exc)
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
    Return paginated and filtered list of Telegram members from local SQLite.
    Supports instant searching across user_id, username, first_name, and last_name.
    """
    if not os.path.exists(db_path):
        return {
            "users": [],
            "total": 0,
            "page": 1,
            "limit": limit,
            "total_pages": 1,
        }

    try:
        page = max(1, int(page))
    except (ValueError, TypeError):
        page = 1

    try:
        limit = min(200, max(1, int(limit)))
    except (ValueError, TypeError):
        limit = 50

    offset = (page - 1) * limit

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
        conn = get_db_connection(db_path)
        cur = conn.cursor()

        # Count total matching rows
        cur.execute(f"SELECT COUNT(*) FROM telegram_users {where_sql}", params)
        total_count = cur.fetchone()[0] or 0

        # Fetch page items
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
        logger.error("Error querying paginated Telegram users: %s", exc)
        return {
            "users": [],
            "total": 0,
            "page": page,
            "limit": limit,
            "total_pages": 1,
            "error": str(exc),
        }
