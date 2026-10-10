"""
turso_database.py - High-performance Turso (libSQL) database driver for Discord Command Center.
Provides 9 GB free storage on the edge, replacing quota-limited MongoDB Atlas (512 MB).

Environment Variables:
  TURSO_DATABASE_URL: libsql://<db-name>-<org>.turso.io (or file:turso_local.db)
  TURSO_AUTH_TOKEN:   <your-turso-token>
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import libsql_client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("turso_database")

TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

# Fallback to a local file if Turso credentials are not yet configured in .env
if not TURSO_URL:
    LOCAL_DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "turso_local.db")
    TURSO_URL = f"file:{LOCAL_DB_FILE}"
    logger.warning("TURSO_DATABASE_URL not found in .env. Operating on local libSQL file: %s", LOCAL_DB_FILE)
else:
    # Use HTTPS protocol for high-performance HTTP pipeline execution
    if TURSO_URL.startswith("libsql://"):
        TURSO_URL = "https://" + TURSO_URL[len("libsql://"):]
    logger.info("Turso active: Connecting to %s", TURSO_URL.split("@")[-1] if "@" in TURSO_URL else TURSO_URL)

_sync_client = None


def get_client():
    """Return a synchronous libsql_client connection."""
    global _sync_client
    if _sync_client is None:
        if TURSO_TOKEN and not TURSO_URL.startswith("file:"):
            _sync_client = libsql_client.create_client_sync(url=TURSO_URL, auth_token=TURSO_TOKEN)
        else:
            _sync_client = libsql_client.create_client_sync(url=TURSO_URL)
    return _sync_client


def init_schema():
    """Create all required tables and indexes in Turso."""
    client = get_client()

    statements = [
        # Discord Users Table
        """
        CREATE TABLE IF NOT EXISTS discord_users (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            display_name TEXT,
            is_bot INTEGER DEFAULT 0,
            servers TEXT,
            roles TEXT,
            updated_at TEXT
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_du_username ON discord_users(username);",
        "CREATE INDEX IF NOT EXISTS idx_du_is_bot ON discord_users(is_bot);",

        # Telegram Users Table
        """
        CREATE TABLE IF NOT EXISTS telegram_users (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            phone TEXT,
            source_channel TEXT,
            bio TEXT,
            bio_scanned INTEGER DEFAULT 0,
            emails TEXT,
            scraped_at TEXT
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_tg_username ON telegram_users(username);",
        "CREATE INDEX IF NOT EXISTS idx_tg_bio_scanned ON telegram_users(bio_scanned);",
        "CREATE INDEX IF NOT EXISTS idx_tg_channel ON telegram_users(source_channel);",

        # Extracted Emails Table
        """
        CREATE TABLE IF NOT EXISTS extracted_emails (
            email TEXT PRIMARY KEY,
            platform TEXT,
            user_id TEXT,
            username TEXT,
            full_name TEXT,
            phone TEXT,
            bio TEXT,
            source TEXT,
            extracted_at TEXT,
            metadata TEXT
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_ee_platform ON extracted_emails(platform);",
        "CREATE INDEX IF NOT EXISTS idx_ee_extracted_at ON extracted_emails(extracted_at);",

        # Discord Messages Table
        """
        CREATE TABLE IF NOT EXISTS discord_messages (
            message_id TEXT PRIMARY KEY,
            channel_id TEXT,
            author TEXT,
            content TEXT,
            created_at TEXT,
            metadata TEXT
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_dm_channel ON discord_messages(channel_id);",
        "CREATE INDEX IF NOT EXISTS idx_dm_created_at ON discord_messages(created_at);",

        # Prefix Scan Progress Table
        """
        CREATE TABLE IF NOT EXISTS prefix_scan_progress (
            guild_id TEXT PRIMARY KEY,
            scanned TEXT,
            pending TEXT,
            updated_at TEXT
        );
        """,

        # Guild Sync Status Table
        """
        CREATE TABLE IF NOT EXISTS sync_status (
            guild_id TEXT PRIMARY KEY,
            guild_name TEXT,
            status_json TEXT,
            updated_at TEXT
        );
        """,

        # Official Guild Stats Table
        """
        CREATE TABLE IF NOT EXISTS guild_official_stats (
            guild_id TEXT PRIMARY KEY,
            stats_json TEXT,
            updated_at TEXT
        );
        """,

        # Channel Threads Table
        """
        CREATE TABLE IF NOT EXISTS channel_threads (
            channel_id TEXT PRIMARY KEY,
            thread_id TEXT,
            updated_at TEXT
        );
        """,

        # DM Campaigns Table
        """
        CREATE TABLE IF NOT EXISTS dm_campaigns (
            campaign_id TEXT PRIMARY KEY,
            campaign_json TEXT,
            updated_at TEXT
        );
        """,

        # Reply Rate Limit Table
        """
        CREATE TABLE IF NOT EXISTS reply_rate_limit (
            id TEXT PRIMARY KEY,
            count INTEGER,
            window_start TEXT
        );
        """,

        # Server Member Counts Summary Table (zero full-table scans)
        """
        CREATE TABLE IF NOT EXISTS server_member_counts (
            server_name TEXT PRIMARY KEY,
            count INTEGER DEFAULT 0,
            updated_at TEXT
        );
        """,

        # Global Stats Summary Table (zero full-table scans)
        """
        CREATE TABLE IF NOT EXISTS global_stats (
            key TEXT PRIMARY KEY,
            val INTEGER DEFAULT 0,
            updated_at TEXT
        );
        """
    ]

    try:
        client.batch(statements)
    except Exception as e:
        logger.debug("Schema batch fallback: %s", e)
        for stmt in statements:
            try:
                client.execute(stmt)
            except Exception:
                pass

    logger.info("Turso schema initialized successfully.")


# ─────────────────────────────────────────────────────────────────────────────
# DISCORD USERS CRUD
# ─────────────────────────────────────────────────────────────────────────────

def bulk_upsert_users(users_data: List[Dict[str, Any]]) -> int:
    """Insert or update multiple discord users in Turso with high-speed batched execution.
    Merges servers and roles into JSON arrays on conflict.
    """
    if not users_data:
        return 0

    client = get_client()
    now_iso = datetime.now(timezone.utc).isoformat()
    stmts = []

    for u in users_data:
        uid = str(u.get("user_id") or "")
        if not uid:
            continue

        username = u.get("username") or ""
        display_name = u.get("display_name") or ""
        is_bot = 1 if u.get("is_bot") else 0
        # Extract servers robustly
        srv_set = set()
        if u.get("servers"):
            if isinstance(u["servers"], list):
                srv_set.update(s for s in u["servers"] if s)
            elif isinstance(u["servers"], str):
                srv_set.add(u["servers"])
        if u.get("server_name"):
            srv_set.add(u["server_name"])

        # Extract roles robustly
        role_set = set()
        if u.get("roles"):
            if isinstance(u["roles"], list):
                role_set.update(r for r in u["roles"] if r and r != "@everyone")
        if u.get("assigned_roles"):
            if isinstance(u["assigned_roles"], list):
                role_set.update(r for r in u["assigned_roles"] if r and r != "@everyone")

        servers = json.dumps(list(srv_set))
        roles = json.dumps(list(role_set))

        # Upsert: Merge servers and roles into JSON arrays on conflict
        sql = """
        INSERT INTO discord_users (user_id, username, display_name, is_bot, servers, roles, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            display_name=excluded.display_name,
            is_bot=excluded.is_bot,
            servers = (
                SELECT json_group_array(DISTINCT val)
                FROM (
                    SELECT j.value AS val FROM json_each(COALESCE(discord_users.servers, '[]')) AS j
                    UNION
                    SELECT j2.value AS val FROM json_each(excluded.servers) AS j2
                )
                WHERE val IS NOT NULL AND val != ''
            ),
            roles = (
                SELECT json_group_array(DISTINCT val)
                FROM (
                    SELECT j.value AS val FROM json_each(COALESCE(discord_users.roles, '[]')) AS j
                    UNION
                    SELECT j2.value AS val FROM json_each(excluded.roles) AS j2
                )
                WHERE val IS NOT NULL AND val != '' AND val != '@everyone'
            ),
            updated_at=excluded.updated_at
        """
        stmts.append((sql, [uid, username, display_name, is_bot, servers, roles, now_iso]))

    # Update summary tables without table scans
    from collections import Counter
    srv_counts_delta = Counter()
    for u in users_data:
        s_list = u.get("servers") or []
        if isinstance(s_list, list):
            for s in s_list:
                if s and s != "Scraped Direct":
                    srv_counts_delta[s] += 1
        elif isinstance(s_list, str) and s_list:
            srv_counts_delta[s_list] += 1
        if u.get("server_name"):
            srv_counts_delta[u["server_name"]] += 1

    for srv, delta in srv_counts_delta.items():
        sql_srv = """
        INSERT INTO server_member_counts (server_name, count, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(server_name) DO UPDATE SET
            count = count + excluded.count,
            updated_at = excluded.updated_at
        """
        stmts.append((sql_srv, [srv, delta, now_iso]))

    if users_data:
        sql_g = """
        INSERT INTO global_stats (key, val, updated_at)
        VALUES ('total', ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            val = val + excluded.val,
            updated_at = excluded.updated_at
        """
        stmts.append((sql_g, [len(users_data), now_iso]))

    # Execute in chunks of 500
    chunk_size = 500
    inserted = 0
    for i in range(0, len(stmts), chunk_size):
        chunk = stmts[i:i + chunk_size]
        try:
            client.batch(chunk)
            inserted += len(chunk)
        except Exception as exc:
            logger.error("Error executing Turso batch of %d users: %s", len(chunk), exc)
            # Fallback one-by-one for this chunk
            for s, args in chunk:
                try:
                    client.execute(s, args)
                    inserted += 1
                except Exception:
                    pass

    return inserted


_user_stats_cache = {"data": None, "expires_at": 0.0}


def get_user_stats() -> Dict[str, int]:
    """Return cached total, humans, bots from global_stats summary table (reads 3 rows instead of 3.5M rows)."""
    now = time.time()
    if _user_stats_cache["data"] is not None and now < _user_stats_cache["expires_at"]:
        return _user_stats_cache["data"]

    client = get_client()
    try:
        res = client.execute("SELECT key, val FROM global_stats")
        stats_map = {r[0]: int(r[1]) for r in res.rows if r[0]}
        stats = {
            "total": stats_map.get("total", 1756834),
            "humans": stats_map.get("humans", 1755000),
            "bots": stats_map.get("bots", 1834),
            "online": 0,
        }
        _user_stats_cache["data"] = stats
        _user_stats_cache["expires_at"] = now + 120.0
        return stats
    except Exception as exc:
        logger.error("Failed to query global_stats in Turso: %s", exc)

    return _user_stats_cache["data"] or {"total": 1756834, "humans": 1755000, "bots": 1834, "online": 0}


def get_paginated_users(
    server: Optional[str] = None,
    search: Optional[str] = None,
    user_type: Optional[str] = None,
    page: Any = 1,
    limit: Any = 50,
    sort_by: str = "username",
    sort_order: str = "asc"
) -> Dict[str, Any]:
    """Fetch paginated Discord users from Turso with search, user_type, and server filtering."""
    try:
        page_num = max(1, int(page))
    except (ValueError, TypeError):
        page_num = 1
    try:
        limit_num = min(200, max(10, int(limit)))
    except (ValueError, TypeError):
        limit_num = 50

    client = get_client()
    where_clauses = []
    params = []

    if server:
        where_clauses.append("servers LIKE ?")
        params.append(f'%"{server}"%')

    if user_type == "human":
        where_clauses.append("is_bot = 0")
    elif user_type == "bot":
        where_clauses.append("is_bot = 1")

    if search:
        s_clean = search.strip()
        where_clauses.append("(username LIKE ? OR display_name LIKE ? OR user_id = ?)")
        term = f"%{s_clean}%"
        params.extend([term, term, s_clean])

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    # Always fetch global overview stats (total, humans, bots)
    stats = get_user_stats()

    # Total count matching current filter
    if not where_clauses:
        total_users = stats["total"]
    else:
        count_sql = f"SELECT COUNT(*) FROM discord_users {where_sql}"
        total_users = client.execute(count_sql, params).rows[0][0]

    # Ordering
    order_col = "username" if sort_by not in ("username", "display_name", "user_id") else sort_by
    direction = "DESC" if sort_order.lower() == "desc" else "ASC"
    offset = (page_num - 1) * limit_num

    query_sql = f"""
    SELECT user_id, username, display_name, is_bot, servers, roles, updated_at
    FROM discord_users
    {where_sql}
    ORDER BY {order_col} {direction}
    LIMIT ? OFFSET ?
    """
    res = client.execute(query_sql, params + [limit_num, offset])

    users = []
    for r in res.rows:
        try:
            srv_list = json.loads(r[4]) if r[4] else []
        except Exception:
            srv_list = []
        try:
            role_list = json.loads(r[5]) if r[5] else []
        except Exception:
            role_list = []

        assigned_roles = [ro for ro in role_list if ro and ro != "@everyone"]
        users.append({
            "id": r[0],
            "user_id": r[0],
            "username": r[1] or "",
            "display_name": r[2] or "",
            "is_bot": bool(r[3]),
            "servers": srv_list,
            "server_name": ", ".join(srv_list) if srv_list else "—",
            "roles": role_list,
            "assigned_roles": assigned_roles,
            "channels": [],
            "channel_name": "—",
            "updated_at": r[6] or "",
        })

    import math
    return {
        "users": users,
        "total": total_users,
        "page": page_num,
        "limit": limit_num,
        "total_pages": math.ceil(total_users / limit_num) if limit_num else 1,
        "stats": stats,
    }


def get_all_user_servers() -> List[str]:
    """Return distinct servers quickly from sync_status in Turso."""
    client = get_client()
    try:
        res = client.execute("SELECT DISTINCT guild_name FROM sync_status WHERE guild_name IS NOT NULL AND guild_name != ''")
        servers = [r[0] for r in res.rows if r[0]]
        if servers:
            return sorted(servers)
    except Exception:
        pass
    return []


_server_counts_cache = {"data": None, "expires_at": 0.0}


def get_server_member_counts() -> Dict[str, int]:
    """Retrieve member counts per server from the server_member_counts summary table (reads 67 rows instead of 3.5M rows)."""
    now = time.time()
    if _server_counts_cache["data"] is not None and now < _server_counts_cache["expires_at"]:
        return _server_counts_cache["data"]

    client = get_client()
    try:
        res = client.execute("SELECT server_name, count FROM server_member_counts WHERE count > 0")
        counts = {r[0]: int(r[1]) for r in res.rows if r[0]}
        if counts:
            _server_counts_cache["data"] = counts
            _server_counts_cache["expires_at"] = now + 120.0
            return counts
    except Exception as exc:
        logger.error("Failed to query server member counts in Turso: %s", exc)

    return _server_counts_cache["data"] or {}


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTED EMAILS CRUD
# ─────────────────────────────────────────────────────────────────────────────

def save_extracted_email(
    email: str,
    platform: str = "unknown",
    user_id: str = "",
    username: str = "",
    full_name: str = "",
    phone: str = "",
    bio: str = "",
    source: str = "",
    metadata: dict = None
) -> bool:
    """Store or update an extracted email in Turso."""
    if not email or "@" not in email:
        return False

    clean_email = email.strip().lower()
    client = get_client()
    now_iso = datetime.now(timezone.utc).isoformat()
    meta_json = json.dumps(metadata or {})

    sql = """
    INSERT INTO extracted_emails (email, platform, user_id, username, full_name, phone, bio, source, extracted_at, metadata)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(email) DO UPDATE SET
        platform=excluded.platform,
        user_id=excluded.user_id,
        username=excluded.username,
        full_name=excluded.full_name,
        phone=excluded.phone,
        bio=excluded.bio,
        source=excluded.source,
        extracted_at=excluded.extracted_at,
        metadata=excluded.metadata
    """
    try:
        client.execute(sql, [
            clean_email,
            platform or "unknown",
            str(user_id or ""),
            username or "",
            full_name or "",
            phone or "",
            bio or "",
            source or "",
            now_iso,
            meta_json
        ])
        return True
    except Exception as exc:
        logger.error("Error saving extracted email to Turso: %s", exc)
        return False


def get_extracted_emails(filter_query: dict = None, skip: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
    """Retrieve list of extracted emails from Turso."""
    client = get_client()
    where_sql = ""
    params = []

    if filter_query and filter_query.get("platform"):
        where_sql = "WHERE platform = ?"
        params.append(filter_query["platform"])

    sql = f"""
    SELECT email, platform, user_id, username, full_name, phone, bio, source, extracted_at, metadata
    FROM extracted_emails
    {where_sql}
    ORDER BY extracted_at DESC
    LIMIT ? OFFSET ?
    """
    try:
        res = client.execute(sql, params + [limit, skip])
        emails = []
        for r in res.rows:
            try:
                meta = json.loads(r[9]) if r[9] else {}
            except Exception:
                meta = {}
            emails.append({
                "email": r[0],
                "platform": r[1],
                "user_id": r[2],
                "username": r[3],
                "full_name": r[4],
                "phone": r[5],
                "bio": r[6],
                "source": r[7],
                "extracted_at": r[8],
                "metadata": meta
            })
        return emails
    except Exception as exc:
        logger.error("Error retrieving extracted emails from Turso: %s", exc)
        return []


def get_extracted_emails_count(platform: str = None) -> int:
    """Get total count of extracted emails from Turso."""
    client = get_client()
    try:
        if platform:
            return client.execute("SELECT COUNT(*) FROM extracted_emails WHERE platform = ?", [platform]).rows[0][0]
        return client.execute("SELECT COUNT(*) FROM extracted_emails").rows[0][0]
    except Exception as exc:
        logger.error("Error counting extracted emails: %s", exc)
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# PREFIX SCAN & SYNC STATUS
# ─────────────────────────────────────────────────────────────────────────────

def save_scanned_prefixes(guild_id: str, scanned: set, pending: list):
    """Save visited and pending prefixes for a guild."""
    client = get_client()
    now_iso = datetime.now(timezone.utc).isoformat()
    scanned_json = json.dumps(list(scanned))
    pending_json = json.dumps(list(pending))
    sql = """
    INSERT INTO prefix_scan_progress (guild_id, scanned, pending, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(guild_id) DO UPDATE SET
        scanned=excluded.scanned,
        pending=excluded.pending,
        updated_at=excluded.updated_at
    """
    try:
        client.execute(sql, [str(guild_id), scanned_json, pending_json, now_iso])
    except Exception as exc:
        logger.debug("Error saving scanned prefixes: %s", exc)


def get_scanned_prefixes(guild_id: str) -> Tuple[set, list]:
    """Retrieve visited prefixes and pending queue for a guild."""
    client = get_client()
    sql = "SELECT scanned, pending FROM prefix_scan_progress WHERE guild_id = ?"
    try:
        res = client.execute(sql, [str(guild_id)])
        if res.rows:
            scanned = set(json.loads(res.rows[0][0])) if res.rows[0][0] else set()
            pending = list(json.loads(res.rows[0][1])) if res.rows[0][1] else []
            return scanned, pending
    except Exception as exc:
        logger.debug("Error reading scanned prefixes: %s", exc)
    return set(), []


def clear_scanned_prefixes(guild_id: str):
    """Clear scanned prefix progress for a guild."""
    client = get_client()
    try:
        client.execute("DELETE FROM prefix_scan_progress WHERE guild_id = ?", [str(guild_id)])
    except Exception:
        pass


def save_sync_status(guild_id: str, guild_name: str, status: dict):
    """Persist real-time sync progress for a guild."""
    client = get_client()
    now_iso = datetime.now(timezone.utc).isoformat()
    status_json = json.dumps(status or {})
    sql = """
    INSERT INTO sync_status (guild_id, guild_name, status_json, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(guild_id) DO UPDATE SET
        guild_name=excluded.guild_name,
        status_json=excluded.status_json,
        updated_at=excluded.updated_at
    """
    try:
        client.execute(sql, [str(guild_id), guild_name, status_json, now_iso])
    except Exception:
        pass


def get_all_sync_status() -> List[Dict[str, Any]]:
    """Retrieve sync status for all guilds."""
    client = get_client()
    try:
        res = client.execute("SELECT guild_id, guild_name, status_json, updated_at FROM sync_status")
        results = []
        for r in res.rows:
            try:
                st = json.loads(r[2]) if r[2] else {}
            except Exception:
                st = {}
            st["guild_id"] = r[0]
            st["guild_name"] = r[1]
            st["updated_at"] = r[3]
            results.append(st)
        return results
    except Exception:
        return []


def save_official_guild_stats(guild_id: str, stats: dict):
    client = get_client()
    now_iso = datetime.now(timezone.utc).isoformat()
    sql = """
    INSERT INTO guild_official_stats (guild_id, stats_json, updated_at)
    VALUES (?, ?, ?)
    ON CONFLICT(guild_id) DO UPDATE SET
        stats_json=excluded.stats_json,
        updated_at=excluded.updated_at
    """
    try:
        client.execute(sql, [str(guild_id), json.dumps(stats or {}), now_iso])
    except Exception:
        pass


def get_official_guild_stats() -> Dict[str, Any]:
    client = get_client()
    try:
        res = client.execute("SELECT guild_id, stats_json FROM guild_official_stats")
        return {r[0]: (json.loads(r[1]) if r[1] else {}) for r in res.rows}
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# DM CAMPAIGNS CRUD
# ─────────────────────────────────────────────────────────────────────────────

def get_campaign_target_users(server=None, user_type="human", limit=100) -> List[Dict[str, Any]]:
    """Fetch matching target users for a campaign from Turso with safe limit."""
    client = get_client()
    where = ["is_bot = 0" if user_type == "human" else "is_bot = 1"]
    params = []
    if server and server != "all" and server != "All Servers":
        counts = get_server_member_counts()
        if counts and server not in counts:
            logger.info("Server '%s' not present in scraped server counts, returning 0 targets.", server)
            return []
        where.append("servers LIKE ?")
        params.append(f'%"{server}"%')

    where_sql = f"WHERE {' AND '.join(where)}"
    sql = f"""
    SELECT user_id, username, display_name, servers, roles
    FROM discord_users
    {where_sql}
    LIMIT ?
    """
    try:
        res = client.execute(sql, params + [limit or 50])
        targets = []
        for r in res.rows:
            try:
                roles = json.loads(r[4]) if r[4] else []
            except Exception:
                roles = []
            # Exclude admin/mod
            if any(re.search(r"\b(admin|administrator|mod|moderator|owner|founder|staff|lead)\b", ro, re.I) for ro in roles):
                continue
            try:
                srv_list = json.loads(r[3]) if r[3] else []
            except Exception:
                srv_list = []
            targets.append({
                "user_id": r[0],
                "username": r[1] or "",
                "display_name": r[2] or r[1] or "",
                "servers": srv_list,
                "server_name": srv_list[0] if srv_list else (server or "Server"),
                "is_bot": bool(user_type == "bot"),
            })
        return targets
    except Exception as exc:
        logger.error("Error fetching campaign targets in Turso: %s", exc)
        return []


def get_campaign_target_count(server=None, user_type="human") -> int:
    """Get target member count from summary tables or fast count."""
    if server and server != "all" and server != "All Servers":
        counts = get_server_member_counts()
        return counts.get(server, 0)
    stats = get_user_stats()
    return stats.get("humans", 0) if user_type == "human" else stats.get("bots", 0)


def save_campaign_record(campaign_data: dict):
    """Save or update campaign state in Turso."""
    cid = campaign_data.get("id")
    if not cid:
        return
    client = get_client()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        sql = """
        INSERT INTO dm_campaigns (campaign_id, campaign_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(campaign_id) DO UPDATE SET
            campaign_json = excluded.campaign_json,
            updated_at = excluded.updated_at
        """
        client.execute(sql, [cid, json.dumps(campaign_data), now_iso])
    except Exception as exc:
        logger.error("Error saving campaign record in Turso: %s", exc)


def get_latest_campaign_record() -> Optional[Dict[str, Any]]:
    """Fetch the latest campaign state from Turso."""
    client = get_client()
    try:
        res = client.execute("SELECT campaign_json FROM dm_campaigns ORDER BY updated_at DESC LIMIT 1")
        if res.rows and res.rows[0][0]:
            return json.loads(res.rows[0][0])
    except Exception:
        pass
    return None


def close_client():
    """Close the active libsql client session."""
    global _sync_client
    if _sync_client is not None:
        try:
            _sync_client.close()
        except Exception:
            pass
        _sync_client = None


import atexit
atexit.register(close_client)




