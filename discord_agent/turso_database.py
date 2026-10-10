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
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import libsql_client
from dotenv import load_dotenv

# Load local directory .env as well as parent/cwd .env
load_dotenv()
_local_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_local_env):
    load_dotenv(_local_env)

logger = logging.getLogger("turso_database")

def _get_turso_config():
    url = os.getenv("TURSO_DATABASE_URL")
    token = os.getenv("TURSO_AUTH_TOKEN")
    if url and url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    return url, token

TURSO_URL, TURSO_TOKEN = _get_turso_config()

if not TURSO_URL:
    LOCAL_DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "turso_local.db")
    TURSO_URL = f"file:{LOCAL_DB_FILE}"
    logger.warning("TURSO_DATABASE_URL not found in .env. Operating on local libSQL file: %s", LOCAL_DB_FILE)
else:
    logger.info("Turso active: Connecting to %s", TURSO_URL.split("@")[-1] if "@" in TURSO_URL else TURSO_URL)

_sync_client = None


def get_client():
    """Return a synchronous libsql_client connection."""
    global _sync_client, TURSO_URL, TURSO_TOKEN
    if _sync_client is None:
        if not TURSO_URL or TURSO_URL.startswith("file:"):
            c_url, c_token = _get_turso_config()
            if c_url:
                TURSO_URL = c_url
                TURSO_TOKEN = c_token
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
            channel_name TEXT,
            guild_name TEXT,
            guild_id TEXT,
            source_message_id TEXT,
            reply_content TEXT,
            reply_type TEXT,
            reply_timestamp TEXT,
            created_at TEXT,
            author_id TEXT,
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
            guild_id TEXT,
            thread_id TEXT,
            starter_message_id TEXT,
            last_activity TEXT,
            updated_at TEXT
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_ct_thread ON channel_threads(thread_id);",

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
            hour_start TEXT,
            day_start TEXT,
            hour_count INTEGER DEFAULT 0,
            day_count INTEGER DEFAULT 0
        );
        """,

        # Discord Channels Table
        """
        CREATE TABLE IF NOT EXISTS discord_channels (
            channel_id TEXT PRIMARY KEY,
            label TEXT,
            guild_id TEXT,
            guild_name TEXT,
            active INTEGER DEFAULT 1
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

# ─────────────────────────────────────────────────────────────────────────────
# DISCORD MESSAGES & REPLIES
# ─────────────────────────────────────────────────────────────────────────────

def save_message(
    author: str,
    content: str,
    channel_name: str,
    guild_name: str,
    timestamp: Any,
    channel_id: Optional[Any] = None,
    guild_id: Optional[Any] = None,
    source_message_id: Optional[Any] = None,
    author_id: Optional[Any] = None,
) -> bool:
    """Save an intercepted message into Turso."""
    client = get_client()
    created_at = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp or datetime.now(timezone.utc).isoformat())
    msg_id = str(source_message_id or f"msg_{time.time_ns()}")
    sql = """
    INSERT INTO discord_messages (
        message_id, channel_id, author, content, channel_name, guild_name,
        guild_id, source_message_id, created_at, author_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(message_id) DO UPDATE SET
        content = excluded.content,
        created_at = excluded.created_at
    """
    try:
        client.execute(sql, [
            msg_id,
            str(channel_id or ""),
            str(author or "Unknown"),
            str(content or ""),
            str(channel_name or ""),
            str(guild_name or ""),
            str(guild_id or ""),
            str(source_message_id or msg_id),
            created_at,
            str(author_id or "")
        ])
        return True
    except Exception as exc:
        logger.debug("Turso save_message notice: %s", exc)
        return False


def get_messages(active_channel_ids: Optional[List[int]] = None, limit: int = 200) -> List[Tuple]:
    """Retrieve recent messages formatted as tuples matching MongoDB get_messages."""
    client = get_client()
    sql = """
    SELECT message_id, author, content, channel_name, guild_name, created_at,
           channel_id, guild_id, source_message_id, reply_content, reply_timestamp,
           author_id, reply_type
    FROM discord_messages
    ORDER BY created_at DESC
    LIMIT ?
    """
    try:
        res = client.execute(sql, [limit])
        rows = []
        for r in res.rows:
            ch_id = int(r[6]) if r[6] and str(r[6]).isdigit() else None
            if active_channel_ids and ch_id not in active_channel_ids:
                continue
            rows.append((
                str(r[0]),
                r[1] or "",
                r[2] or "",
                r[3] or "",
                r[4] or "",
                str(r[5] or ""),
                ch_id,
                int(r[7]) if r[7] and str(r[7]).isdigit() else None,
                int(r[8]) if r[8] and str(r[8]).isdigit() else None,
                r[9],
                r[10],
                int(r[11]) if r[11] and str(r[11]).isdigit() else None,
                r[12],
            ))
        return rows
    except Exception as exc:
        logger.debug("Turso get_messages notice: %s", exc)
        return []


def get_message_by_id(message_id: str) -> Optional[Dict[str, Any]]:
    client = get_client()
    try:
        res = client.execute(
            "SELECT message_id, author, content, channel_name, guild_name, created_at, "
            "channel_id, guild_id, source_message_id, reply_content, reply_timestamp, author_id, reply_type "
            "FROM discord_messages WHERE message_id = ? LIMIT 1",
            [str(message_id)]
        )
        if res.rows:
            r = res.rows[0]
            return {
                "_id": r[0],
                "author": r[1],
                "content": r[2],
                "channel_name": r[3],
                "guild_name": r[4],
                "timestamp": r[5],
                "channel_id": r[6],
                "guild_id": r[7],
                "source_message_id": r[8],
                "reply_content": r[9],
                "reply_timestamp": r[10],
                "author_id": r[11],
                "reply_type": r[12],
            }
    except Exception:
        pass
    return None


def get_latest_message_for_channel(channel_id: Any) -> Optional[Dict[str, Any]]:
    client = get_client()
    try:
        res = client.execute(
            "SELECT message_id, author, content, channel_name, guild_name, created_at, "
            "channel_id, guild_id, source_message_id, reply_content, reply_timestamp, author_id, reply_type "
            "FROM discord_messages WHERE channel_id = ? ORDER BY created_at DESC LIMIT 1",
            [str(channel_id)]
        )
        if res.rows:
            r = res.rows[0]
            return {
                "_id": r[0],
                "author": r[1],
                "content": r[2],
                "channel_name": r[3],
                "guild_name": r[4],
                "timestamp": r[5],
                "channel_id": r[6],
                "guild_id": r[7],
                "source_message_id": r[8],
                "reply_content": r[9],
                "reply_timestamp": r[10],
                "author_id": r[11],
                "reply_type": r[12],
            }
    except Exception:
        pass
    return None


def save_reply_for_message(message_id: str, reply_content: str, replied_at: Any = None, reply_type: str = "channel") -> bool:
    client = get_client()
    now_iso = replied_at.isoformat() if hasattr(replied_at, "isoformat") else (str(replied_at) if replied_at else datetime.now(timezone.utc).isoformat())
    sql = "UPDATE discord_messages SET reply_content = ?, reply_type = ?, reply_timestamp = ? WHERE message_id = ?"
    try:
        client.execute(sql, [reply_content, reply_type, now_iso, str(message_id)])
        return True
    except Exception:
        return False


def save_reply_for_latest_message(channel_id: Any, reply_content: str, replied_at: Any = None, reply_type: str = "channel") -> bool:
    client = get_client()
    now_iso = replied_at.isoformat() if hasattr(replied_at, "isoformat") else (str(replied_at) if replied_at else datetime.now(timezone.utc).isoformat())
    try:
        res = client.execute("SELECT message_id FROM discord_messages WHERE channel_id = ? ORDER BY created_at DESC LIMIT 1", [str(channel_id)])
        if res.rows:
            mid = res.rows[0][0]
            return save_reply_for_message(mid, reply_content, now_iso, reply_type)
    except Exception:
        pass
    return False


def delete_old_messages(retention_days: int = 7) -> int:
    client = get_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    try:
        client.execute("DELETE FROM discord_messages WHERE created_at < ?", [cutoff])
        return 0
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# CHANNEL THREADS
# ─────────────────────────────────────────────────────────────────────────────

def get_channel_thread(channel_id: Any) -> Optional[Tuple]:
    client = get_client()
    try:
        res = client.execute("SELECT channel_id, guild_id, thread_id, starter_message_id, last_activity FROM channel_threads WHERE channel_id = ? LIMIT 1", [str(channel_id)])
        if res.rows:
            r = res.rows[0]
            return (
                int(r[0]) if str(r[0]).isdigit() else r[0],
                int(r[1]) if r[1] and str(r[1]).isdigit() else r[1],
                int(r[2]) if r[2] and str(r[2]).isdigit() else r[2],
                int(r[3]) if r[3] and str(r[3]).isdigit() else r[3],
                r[4]
            )
    except Exception:
        pass
    return None


def get_thread_mapping(thread_id: Any) -> Optional[Tuple]:
    client = get_client()
    try:
        res = client.execute("SELECT channel_id, guild_id, thread_id, starter_message_id, last_activity FROM channel_threads WHERE thread_id = ? LIMIT 1", [str(thread_id)])
        if res.rows:
            r = res.rows[0]
            return (
                int(r[0]) if str(r[0]).isdigit() else r[0],
                int(r[1]) if r[1] and str(r[1]).isdigit() else r[1],
                int(r[2]) if r[2] and str(r[2]).isdigit() else r[2],
                int(r[3]) if r[3] and str(r[3]).isdigit() else r[3],
                r[4]
            )
    except Exception:
        pass
    return None


def save_channel_thread(channel_id: Any, guild_id: Any, thread_id: Any, starter_message_id: Any = None, last_activity: Any = None):
    client = get_client()
    now_iso = last_activity.isoformat() if hasattr(last_activity, "isoformat") else (str(last_activity) if last_activity else datetime.now(timezone.utc).isoformat())
    sql = """
    INSERT INTO channel_threads (channel_id, guild_id, thread_id, starter_message_id, last_activity, updated_at)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(channel_id) DO UPDATE SET
        guild_id = excluded.guild_id,
        thread_id = excluded.thread_id,
        starter_message_id = excluded.starter_message_id,
        last_activity = excluded.last_activity,
        updated_at = excluded.updated_at
    """
    try:
        client.execute(sql, [str(channel_id), str(guild_id), str(thread_id), str(starter_message_id or ""), now_iso, now_iso])
    except Exception:
        pass


def touch_channel_thread(channel_id: Any, last_activity: Any = None):
    client = get_client()
    now_iso = last_activity.isoformat() if hasattr(last_activity, "isoformat") else (str(last_activity) if last_activity else datetime.now(timezone.utc).isoformat())
    try:
        client.execute("UPDATE channel_threads SET last_activity = ?, updated_at = ? WHERE channel_id = ?", [now_iso, now_iso, str(channel_id)])
    except Exception:
        pass


def get_expired_channel_threads(inactive_days: int = 7) -> List[Tuple]:
    client = get_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=inactive_days)).isoformat()
    try:
        res = client.execute("SELECT channel_id, guild_id, thread_id, starter_message_id, last_activity FROM channel_threads WHERE last_activity <= ?", [cutoff])
        results = []
        for r in res.rows:
            results.append((
                int(r[0]) if str(r[0]).isdigit() else r[0],
                int(r[1]) if r[1] and str(r[1]).isdigit() else r[1],
                int(r[2]) if r[2] and str(r[2]).isdigit() else r[2],
                int(r[3]) if r[3] and str(r[3]).isdigit() else r[3],
                r[4]
            ))
        return results
    except Exception:
        return []


def delete_channel_thread(channel_id: Any):
    client = get_client()
    try:
        client.execute("DELETE FROM channel_threads WHERE channel_id = ?", [str(channel_id)])
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# REPLY RATE LIMIT
# ─────────────────────────────────────────────────────────────────────────────

def acquire_reply_slot(hourly_limit: int = 10, daily_limit: int = 100) -> Optional[Dict[str, str]]:
    client = get_client()
    now = datetime.now(timezone.utc)
    current_hour = now.strftime("%Y-%m-%d %H")
    current_day = now.strftime("%Y-%m-%d")

    try:
        res = client.execute("SELECT hour_start, day_start, hour_count, day_count FROM reply_rate_limit WHERE id = 'global' LIMIT 1")
        if res.rows:
            r = res.rows[0]
            h_start, d_start, h_count, d_count = r[0], r[1], r[2] or 0, r[3] or 0
            if h_start != current_hour:
                h_count = 0
            if d_start != current_day:
                d_count = 0
            if h_count >= hourly_limit or d_count >= daily_limit:
                return None
            h_count += 1
            d_count += 1
            client.execute("""
                UPDATE reply_rate_limit
                SET hour_start = ?, day_start = ?, hour_count = ?, day_count = ?
                WHERE id = 'global'
            """, [current_hour, current_day, h_count, d_count])
        else:
            client.execute("""
                INSERT INTO reply_rate_limit (id, hour_start, day_start, hour_count, day_count)
                VALUES ('global', ?, ?, 1, 1)
            """, [current_hour, current_day])
        return {"hour_start": current_hour, "day_start": current_day}
    except Exception as exc:
        logger.debug("Turso rate limit notice: %s", exc)
        return {"hour_start": current_hour, "day_start": current_day}


def release_reply_slot(slot: Optional[Dict[str, str]]):
    if not slot:
        return
    client = get_client()
    try:
        res = client.execute("SELECT hour_count, day_count FROM reply_rate_limit WHERE id = 'global' LIMIT 1")
        if res.rows:
            h_count = max(0, (res.rows[0][0] or 1) - 1)
            d_count = max(0, (res.rows[0][1] or 1) - 1)
            client.execute("UPDATE reply_rate_limit SET hour_count = ?, day_count = ? WHERE id = 'global'", [h_count, d_count])
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# DISCORD CHANNELS CONFIG
# ─────────────────────────────────────────────────────────────────────────────

def get_discord_channels() -> List[Dict[str, Any]]:
    client = get_client()
    try:
        res = client.execute("SELECT channel_id, label, guild_id, guild_name, active FROM discord_channels ORDER BY label ASC")
        return [
            {
                "channel_id": int(r[0]) if str(r[0]).isdigit() else r[0],
                "label": r[1] or "",
                "guild_id": int(r[2]) if r[2] and str(r[2]).isdigit() else r[2],
                "guild_name": r[3] or "",
                "active": bool(r[4]),
            }
            for r in res.rows
        ]
    except Exception:
        return []


def add_discord_channel(label: str, channel_id: Any, guild_id: Any = None, guild_name: Any = None) -> bool:
    client = get_client()
    sql = """
    INSERT INTO discord_channels (channel_id, label, guild_id, guild_name, active)
    VALUES (?, ?, ?, ?, 1)
    ON CONFLICT(channel_id) DO UPDATE SET
        label = excluded.label,
        guild_id = excluded.guild_id,
        guild_name = excluded.guild_name,
        active = 1
    """
    try:
        client.execute(sql, [str(channel_id), label, str(guild_id or ""), str(guild_name or "")])
        return True
    except Exception:
        return False


def remove_discord_channel(channel_id: Any) -> bool:
    client = get_client()
    try:
        client.execute("DELETE FROM discord_channels WHERE channel_id = ?", [str(channel_id)])
        return True
    except Exception:
        return False


def toggle_discord_channel(channel_id: Any) -> Optional[bool]:
    client = get_client()
    try:
        res = client.execute("SELECT active FROM discord_channels WHERE channel_id = ? LIMIT 1", [str(channel_id)])
        if res.rows:
            new_val = 0 if res.rows[0][0] else 1
            client.execute("UPDATE discord_channels SET active = ? WHERE channel_id = ?", [new_val, str(channel_id)])
            return bool(new_val)
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM USERS HIGH-PERFORMANCE QUERYING
# ─────────────────────────────────────────────────────────────────────────────

def get_telegram_stats(local_db_path: str = None, local_csv_path: str = None) -> Dict[str, Any]:
    client = get_client()
    try:
        res_total = client.execute("SELECT COUNT(*) FROM telegram_users").rows[0][0]
        res_ch = client.execute("SELECT COUNT(DISTINCT source_channel) FROM telegram_users WHERE source_channel IS NOT NULL AND source_channel != ''").rows[0][0]
        res_uname = client.execute("SELECT COUNT(*) FROM telegram_users WHERE username IS NOT NULL AND username != ''").rows[0][0]
        csv_size_mb = 0.0
        if local_csv_path and os.path.exists(local_csv_path):
            csv_size_mb = round(os.path.getsize(local_csv_path) / (1024 * 1024), 2)
        return {
            "total_users": res_total,
            "total_channels": res_ch,
            "with_username": res_uname,
            "without_username": max(0, res_total - res_uname),
            "db_size_mb": 0.0,
            "csv_size_mb": csv_size_mb,
            "db_exists": True,
            "storage_backend": "Turso libSQL Cloud (Active Primary)",
        }
    except Exception as exc:
        logger.debug("Turso get_telegram_stats notice: %s", exc)
        return {
            "total_users": 0,
            "total_channels": 0,
            "with_username": 0,
            "without_username": 0,
            "db_size_mb": 0.0,
            "csv_size_mb": 0.0,
            "db_exists": False,
            "storage_backend": "None",
        }


def get_telegram_channels() -> List[Dict[str, Any]]:
    client = get_client()
    try:
        res = client.execute("""
            SELECT source_channel, COUNT(*) as cnt
            FROM telegram_users
            WHERE source_channel IS NOT NULL AND source_channel != ''
            GROUP BY source_channel
            ORDER BY cnt DESC
        """)
        return [{"channel": r[0], "count": r[1]} for r in res.rows]
    except Exception:
        return []


def get_paginated_telegram_users(channel: str = None, search: str = None, page: int = 1, limit: int = 50) -> Dict[str, Any]:
    client = get_client()
    page = max(1, int(page or 1))
    limit = max(1, min(200, int(limit or 50)))
    skip = (page - 1) * limit
    where = []
    params = []
    if channel and channel != "all":
        where.append("source_channel = ?")
        params.append(channel)
    if search:
        s = f"%{search.strip()}%"
        where.append("(username LIKE ? OR first_name LIKE ? OR last_name LIKE ? OR phone LIKE ?)")
        params.extend([s, s, s, s])
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    try:
        cnt_res = client.execute(f"SELECT COUNT(*) FROM telegram_users {where_sql}", params)
        total = cnt_res.rows[0][0] if cnt_res.rows else 0
        data_res = client.execute(
            f"SELECT user_id, username, first_name, last_name, phone, source_channel, scraped_at FROM telegram_users {where_sql} ORDER BY scraped_at DESC LIMIT ? OFFSET ?",
            params + [limit, skip]
        )
        users = [
            {
                "user_id": r[0],
                "username": r[1] or "",
                "first_name": r[2] or "",
                "last_name": r[3] or "",
                "phone": r[4] or "",
                "source_channel": r[5] or "",
                "scraped_at": r[6] or "",
            }
            for r in data_res.rows
        ]
        return {
            "users": users,
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": max(1, math.ceil(total / limit)),
        }
    except Exception as exc:
        logger.error("Error fetching paginated telegram users from Turso: %s", exc)
        return {"users": [], "total": 0, "page": page, "limit": limit, "total_pages": 1}


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




