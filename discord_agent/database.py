from datetime import datetime, timedelta, timezone
import logging
import os
import re
import sqlite3
import time
from threading import Lock

import certifi
from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient, UpdateOne
from bson import ObjectId

load_dotenv()

logger = logging.getLogger(__name__)

MONGODB_URI = os.getenv("NEW_MONGODB_URI") or os.getenv("MONGODB_URI")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "discord_agent_db")
LEGACY_DB_PATH = os.getenv("LEGACY_DB_PATH", "discord_agent.db")
HOURLY_REPLY_LIMIT = 10
DAILY_REPLY_LIMIT = 100
MESSAGE_RETENTION_DAYS = 7

_client = None
_database = None
_reply_limit_lock = Lock()

ADMIN_ROLE_REGEX = re.compile(
    r"\b(admin|administrator|mod|moderator|owner|co-owner|founder|co-founder|staff|lead|core\s*team|community\s*manager|management|manager|officer|head|support)\b",
    re.IGNORECASE,
)


def is_turso_active() -> bool:
    """Return True if Turso libSQL is configured or requested via USE_TURSO."""
    return bool(os.getenv("TURSO_DATABASE_URL") or os.getenv("USE_TURSO", "").lower() == "true")


def get_database():
    global _client, _database

    if not MONGODB_URI:
        raise RuntimeError("MONGODB_URI is missing from the environment.")

    if _database is None:
        host = MONGODB_URI.split("@")[-1].split("/")[0] if "@" in MONGODB_URI else "unknown"
        if "socialmediapost" in host:
            logger.warning("⚠️  ALERT: Connecting to OLD quota-limited cluster: %s", host)
        else:
            logger.info("✅ Connected to active MongoDB cluster: %s", host)

        _client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=10000,
            tlsCAFile=certifi.where(),
            tz_aware=True,
        )
        db_name = os.getenv("MONGODB_DB_NAME") or "discord_agent_db"
        if db_name != "discord_agent_db":
            try:
                if "discord_agent_db" in _client.list_database_names():
                    logger.info("Prioritizing 'discord_agent_db' containing 761k+ members.")
                    db_name = "discord_agent_db"
            except Exception:
                pass

        _database = _client[db_name]
        logger.info("Active MongoDB Database: '%s'", db_name)

    return _database


def get_collection(name):
    return get_database()[name]


def drop_redundant_indexes():
    """Drop obsolete or wasteful indexes from discord_users to free storage.
    Safe to call even when cluster quota is exceeded (index drops reclaim disk space).
    """
    try:
        col = get_collection("discord_users")
        existing = col.index_information()
        target_keys = {"last_seen_at", "display_name", "presence_status", "username", "is_bot"}
        for name, info in list(existing.items()):
            key_fields = {k[0] for k in info.get("key", [])}
            if key_fields & target_keys:
                try:
                    col.drop_index(name)
                    logger.info("Reclaimed storage: dropped obsolete index '%s'", name)
                except Exception as e:
                    logger.warning("Failed to drop index '%s': %s", name, e)

        # Also drop wasteful indexes on telegram_users
        tg_col = get_collection("telegram_users")
        tg_existing = tg_col.index_information()
        for tg_idx in ["username_1", "scraped_at_-1"]:
            if tg_idx in tg_existing:
                try:
                    tg_col.drop_index(tg_idx)
                    logger.info("Reclaimed storage: dropped telegram index '%s'", tg_idx)
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("drop_redundant_indexes check failed: %s", exc)


def initialize_database():
    from pymongo.errors import OperationFailure
    database = get_database()
    
    # Retry ping up to 3 times with a short pause for cloud cold-starts
    for attempt in range(1, 4):
        try:
            _client.admin.command("ping")
            break
        except Exception as exc:
            if attempt < 3:
                logger.warning("MongoDB ping attempt %d failed (%s). Retrying in 3s...", attempt, exc)
                time.sleep(3)
            else:
                raise

    # Reclaim storage first by dropping obsolete indexes
    drop_redundant_indexes()

    # Indexes and seed writes — skip gracefully if Atlas storage quota is full (code 8000).
    # The bot can still START and serve existing data read-only even when writes are blocked.
    try:
        database.discord_channels.create_index(
            [("channel_id", ASCENDING)],
            unique=True,
        )
        database.discord_messages.create_index(
            [("channel_id", ASCENDING), ("timestamp", DESCENDING)]
        )
        database.discord_messages.create_index(
            [("timestamp", ASCENDING)]
        )
        database.channel_threads.create_index(
            [("channel_id", ASCENDING)],
            unique=True,
        )
        database.channel_threads.create_index(
            [("thread_id", ASCENDING)],
            unique=True,
        )
        database.channel_threads.create_index(
            [("last_activity", ASCENDING)]
        )
        database.discord_users.create_index(
            [("user_id", ASCENDING)],
            unique=True,
        )
        database.discord_users.create_index(
            [("servers", ASCENDING)]
        )
        database.telegram_users.create_index(
            [("user_id", ASCENDING)],
            unique=True,
        )
        database.telegram_users.create_index(
            [("source_channel", ASCENDING)]
        )
        # Dropped wasteful indexes: username, is_bot, display_name, last_seen_at, presence_status, scraped_at

        database.reply_rate_limit.update_one(
            {"_id": "global"},
            {
                "$setOnInsert": {
                    "hour_start": "",
                    "day_start": "",
                    "hour_count": 0,
                    "day_count": 0,
                }
            },
            upsert=True,
        )

        _migrate_legacy_sqlite(database)
        delete_old_messages(MESSAGE_RETENTION_DAYS)
        logger.info("MongoDB database '%s' is ready.", MONGODB_DB_NAME)

    except OperationFailure as e:
        if getattr(e, "code", None) == 8000:
            logger.error(
                "⚠️  MongoDB Atlas storage quota FULL (512 MB used). "
                "Writes are blocked — bot will run in READ-ONLY mode. "
                "Free up space or upgrade your Atlas cluster tier: "
                "https://cloud.mongodb.com"
            )
        else:
            raise


def _table_exists(connection, table_name):
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _migrate_legacy_sqlite(database):
    if database.migrations.find_one({"_id": "legacy_sqlite_v1"}):
        return

    if not os.path.exists(LEGACY_DB_PATH):
        database.migrations.insert_one(
            {"_id": "legacy_sqlite_v1", "status": "no_legacy_database"}
        )
        return

    connection = sqlite3.connect(LEGACY_DB_PATH)
    connection.row_factory = sqlite3.Row

    try:
        if (
            database.discord_channels.count_documents({}) == 0
            and _table_exists(connection, "discord_channels")
        ):
            rows = connection.execute(
                """
                SELECT label, channel_id, guild_id, guild_name, active
                FROM discord_channels
                """
            ).fetchall()
            if rows:
                database.discord_channels.insert_many(
                    [
                        {
                            "label": row["label"],
                            "channel_id": int(row["channel_id"]),
                            "guild_id": (
                                int(row["guild_id"])
                                if row["guild_id"] is not None
                                else None
                            ),
                            "guild_name": row["guild_name"],
                            "active": bool(row["active"]),
                        }
                        for row in rows
                    ]
                )

        if (
            database.discord_messages.count_documents({}) == 0
            and _table_exists(connection, "discord_messages")
        ):
            rows = connection.execute(
                """
                SELECT author, content, channel_name, guild_name,
                       channel_id, guild_id, timestamp
                FROM discord_messages
                """
            ).fetchall()
            if rows:
                database.discord_messages.insert_many(
                    [
                        {
                            "author": row["author"],
                            "content": row["content"],
                            "channel_name": row["channel_name"],
                            "guild_name": row["guild_name"],
                            "channel_id": (
                                int(row["channel_id"])
                                if row["channel_id"] is not None
                                else None
                            ),
                            "guild_id": (
                                int(row["guild_id"])
                                if row["guild_id"] is not None
                                else None
                            ),
                            "timestamp": _as_utc_datetime(row["timestamp"]),
                        }
                        for row in rows
                    ]
                )

        if (
            database.channel_threads.count_documents({}) == 0
            and _table_exists(connection, "channel_threads")
        ):
            rows = connection.execute(
                """
                SELECT channel_id, guild_id, thread_id,
                       starter_message_id, last_activity
                FROM channel_threads
                """
            ).fetchall()
            if rows:
                database.channel_threads.insert_many(
                    [
                        {
                            "channel_id": int(row["channel_id"]),
                            "guild_id": int(row["guild_id"]),
                            "thread_id": int(row["thread_id"]),
                            "starter_message_id": (
                                int(row["starter_message_id"])
                                if row["starter_message_id"] is not None
                                else None
                            ),
                            "last_activity": _as_utc_datetime(
                                row["last_activity"]
                            ),
                        }
                        for row in rows
                    ]
                )

        database.migrations.insert_one(
            {
                "_id": "legacy_sqlite_v1",
                "status": "completed",
                "completed_at": datetime.now(timezone.utc),
            }
        )
        logger.info("Imported available legacy SQLite data into MongoDB.")
    finally:
        connection.close()


def _as_utc_datetime(value=None):
    if value is None:
        return datetime.now(timezone.utc)

    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _thread_tuple(document):
    if not document:
        return None

    activity = document["last_activity"]
    if isinstance(activity, datetime):
        activity = activity.isoformat()

    return (
        document["channel_id"],
        document["guild_id"],
        document["thread_id"],
        document.get("starter_message_id"),
        activity,
    )


def save_message(
    author,
    content,
    channel_name,
    guild_name,
    timestamp,
    channel_id=None,
    guild_id=None,
    source_message_id=None,
    author_id=None,
):
    get_collection("discord_messages").insert_one(
        {
            "author": author,
            "author_id": int(author_id) if author_id is not None else None,
            "content": content,
            "channel_name": channel_name,
            "guild_name": guild_name,
            "channel_id": int(channel_id) if channel_id is not None else None,
            "guild_id": int(guild_id) if guild_id is not None else None,
            "source_message_id": (
                int(source_message_id) if source_message_id is not None else None
            ),
            "reply_content": None,
            "reply_type": None,
            "reply_timestamp": None,
            "timestamp": _as_utc_datetime(timestamp),
        }
    )
    delete_old_messages(MESSAGE_RETENTION_DAYS)


def delete_old_messages(retention_days=MESSAGE_RETENTION_DAYS):
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = get_collection("discord_messages").delete_many(
        {"timestamp": {"$lt": cutoff}}
    )
    if result.deleted_count:
        logger.info(
            "Deleted %s intercepted messages older than %s days.",
            result.deleted_count,
            retention_days,
        )
    return result.deleted_count


def save_reply_for_latest_message(channel_id, reply_content, replied_at=None, reply_type="channel"):
    collection = get_collection("discord_messages")
    document = collection.find_one(
        {
            "channel_id": int(channel_id),
            "$or": [
                {"reply_content": None},
                {"reply_content": {"$exists": False}},
            ],
        },
        sort=[("timestamp", DESCENDING), ("_id", DESCENDING)],
    )
    if not document:
        document = collection.find_one(
            {"channel_id": int(channel_id)},
            sort=[("timestamp", DESCENDING), ("_id", DESCENDING)],
        )
    if not document:
        return False

    collection.update_one(
        {"_id": document["_id"]},
        {
            "$set": {
                "reply_content": reply_content,
                "reply_type": reply_type,
                "reply_timestamp": _as_utc_datetime(replied_at),
            }
        },
    )
    return True


def get_message_by_id(message_id):
    try:
        object_id = ObjectId(str(message_id))
    except Exception:
        return None
    return get_collection("discord_messages").find_one({"_id": object_id})


def get_latest_message_for_channel(channel_id):
    try:
        return get_collection("discord_messages").find_one(
            {"channel_id": int(channel_id)},
            sort=[("timestamp", DESCENDING), ("_id", DESCENDING)],
        )
    except Exception:
        return None


def save_reply_for_message(message_id, reply_content, replied_at=None, reply_type="channel"):
    try:
        object_id = ObjectId(str(message_id))
    except Exception:
        return False

    result = get_collection("discord_messages").update_one(
        {"_id": object_id},
        {
            "$set": {
                "reply_content": reply_content,
                "reply_type": reply_type,
                "reply_timestamp": _as_utc_datetime(replied_at),
            }
        },
    )
    return result.matched_count > 0


def get_messages():
    database = get_database()
    delete_old_messages(MESSAGE_RETENTION_DAYS)
    active_channel_ids = [
        row["channel_id"]
        for row in database.discord_channels.find(
            {"active": True},
            {"channel_id": 1},
        )
    ]

    documents = database.discord_messages.find(
        {"channel_id": {"$in": active_channel_ids}}
    ).sort([("timestamp", DESCENDING), ("_id", DESCENDING)])

    return [
        (
            str(document["_id"]),
            document["author"],
            document["content"],
            document.get("channel_name"),
            document.get("guild_name"),
            document["timestamp"].isoformat(),
            document.get("channel_id"),
            document.get("guild_id"),
            document.get("source_message_id"),
            document.get("reply_content"),
            (
                document["reply_timestamp"].isoformat()
                if isinstance(document.get("reply_timestamp"), datetime)
                else document.get("reply_timestamp")
            ),
            document.get("author_id"),
            document.get("reply_type"),
        )
        for document in documents
    ]


def get_channel_thread(channel_id):
    document = get_collection("channel_threads").find_one(
        {"channel_id": int(channel_id)}
    )
    return _thread_tuple(document)


def get_thread_mapping(thread_id):
    document = get_collection("channel_threads").find_one(
        {"thread_id": int(thread_id)}
    )
    return _thread_tuple(document)


def save_channel_thread(
    channel_id,
    guild_id,
    thread_id,
    starter_message_id=None,
    last_activity=None,
):
    get_collection("channel_threads").update_one(
        {"channel_id": int(channel_id)},
        {
            "$set": {
                "guild_id": int(guild_id),
                "thread_id": int(thread_id),
                "starter_message_id": (
                    int(starter_message_id) if starter_message_id else None
                ),
                "last_activity": _as_utc_datetime(last_activity),
            }
        },
        upsert=True,
    )


def touch_channel_thread(channel_id, last_activity=None):
    get_collection("channel_threads").update_one(
        {"channel_id": int(channel_id)},
        {"$set": {"last_activity": _as_utc_datetime(last_activity)}},
    )


def get_expired_channel_threads(inactive_days=7):
    cutoff = datetime.now(timezone.utc) - timedelta(days=inactive_days)
    documents = get_collection("channel_threads").find(
        {"last_activity": {"$lte": cutoff}}
    )
    return [_thread_tuple(document) for document in documents]


def delete_channel_thread(channel_id):
    get_collection("channel_threads").delete_one(
        {"channel_id": int(channel_id)}
    )


def acquire_reply_slot():
    with _reply_limit_lock:
        collection = get_collection("reply_rate_limit")
        document = collection.find_one({"_id": "global"}) or {}
        now = datetime.now(timezone.utc)
        current_hour = now.strftime("%Y-%m-%d %H")
        current_day = now.strftime("%Y-%m-%d")

        hour_count = document.get("hour_count", 0)
        day_count = document.get("day_count", 0)

        if document.get("hour_start") != current_hour:
            hour_count = 0
        if document.get("day_start") != current_day:
            day_count = 0

        if hour_count >= HOURLY_REPLY_LIMIT or day_count >= DAILY_REPLY_LIMIT:
            return None

        collection.update_one(
            {"_id": "global"},
            {
                "$set": {
                    "hour_start": current_hour,
                    "day_start": current_day,
                    "hour_count": hour_count + 1,
                    "day_count": day_count + 1,
                }
            },
            upsert=True,
        )

        return {
            "hour_start": current_hour,
            "day_start": current_day,
        }


def release_reply_slot(slot):
    if not slot:
        return

    with _reply_limit_lock:
        collection = get_collection("reply_rate_limit")
        document = collection.find_one({"_id": "global"})
        if not document:
            return

        hour_count = document.get("hour_count", 0)
        day_count = document.get("day_count", 0)

        if document.get("hour_start") == slot.get("hour_start"):
            hour_count = max(0, hour_count - 1)
        if document.get("day_start") == slot.get("day_start"):
            day_count = max(0, day_count - 1)

        collection.update_one(
            {"_id": "global"},
            {
                "$set": {
                    "hour_count": hour_count,
                    "day_count": day_count,
                }
            },
        )


# ── Discord Users High-Scale Directory ─────────────────────────

def upsert_user(user_data):
    """Upsert a single Discord user into MongoDB ensuring zero duplicates.
    Only stores fields essential for DM campaigns to keep document size minimal.
    """
    if not user_data or not user_data.get("user_id"):
        return
    if user_data.get("is_admin"):
        return
    assigned_roles = user_data.get("assigned_roles") or []
    if any(isinstance(r, str) and ADMIN_ROLE_REGEX.search(r) for r in assigned_roles):
        return
    user_id = str(user_data["user_id"])

    # Slim schema — only essential fields stored (saves ~330 bytes/doc vs old schema)
    set_fields = {
        "username": user_data.get("username", "Unknown"),
        "display_name": user_data.get("display_name") or user_data.get("username", "Unknown"),
        "is_bot": bool(user_data.get("is_bot", False)),
    }

    add_to_set = {}
    if user_data.get("server_name"):
        add_to_set["servers"] = user_data["server_name"]
    if user_data.get("assigned_roles"):
        roles = user_data["assigned_roles"]
        if isinstance(roles, list) and roles:
            add_to_set["roles"] = {"$each": [r for r in roles if r != "@everyone"]}

    update_doc = {"$set": set_fields}
    if add_to_set:
        update_doc["$addToSet"] = add_to_set

    get_collection("discord_users").update_one(
        {"user_id": user_id},
        update_doc,
        upsert=True,
    )


def bulk_upsert_users(users_list):
    """Bulk upsert thousands/millions of users into MongoDB or Turso with high throughput.
    Slim schema — only essential DM fields stored to maximise cluster capacity.
    """
    if not users_list:
        return 0

    if is_turso_active():
        import turso_database
        return turso_database.bulk_upsert_users(users_list)

    operations = []

    for u in users_list:
        user_id = str(u.get("user_id", ""))
        if not user_id:
            continue
        if u.get("is_admin"):
            continue
        assigned_roles = u.get("assigned_roles") or []
        if any(isinstance(r, str) and ADMIN_ROLE_REGEX.search(r) for r in assigned_roles):
            continue
        # Slim schema — dropped: avatar_url, channels, server_nickname,
        # presence_status, last_seen_at, joined_at, bot_status (~330 bytes saved/doc)
        set_fields = {
            "username": u.get("username", "Unknown"),
            "display_name": u.get("display_name") or u.get("username", "Unknown"),
            "is_bot": bool(u.get("is_bot", False)),
        }

        add_to_set = {}
        if u.get("server_name"):
            add_to_set["servers"] = u["server_name"]
        if u.get("assigned_roles"):
            roles = u["assigned_roles"]
            if isinstance(roles, list) and roles:
                add_to_set["roles"] = {"$each": [r for r in roles if r != "@everyone"]}

        update_doc = {"$set": set_fields}
        if add_to_set:
            update_doc["$addToSet"] = add_to_set

        operations.append(UpdateOne({"user_id": user_id}, update_doc, upsert=True))

    if not operations:
        return 0

    collection = get_collection("discord_users")
    total_upserted = 0
    BATCH_SIZE = 500
    for i in range(0, len(operations), BATCH_SIZE):
        batch = operations[i:i + BATCH_SIZE]
        res = collection.bulk_write(batch, ordered=False)
        total_upserted += (res.upserted_count + res.modified_count)
    return total_upserted


_user_stats_cache = {
    "data": None,
    "expires_at": 0.0,
}


def _get_cached_user_stats(collection, fallback_total):
    now = time.time()
    if _user_stats_cache["data"] is not None and now < _user_stats_cache["expires_at"]:
        return _user_stats_cache["data"]

    try:
        total_all = collection.estimated_document_count()
        humans_count = collection.count_documents({"is_bot": False})
        bots_count = collection.count_documents({"is_bot": True})
    except Exception:
        total_all = fallback_total
        humans_count = 0
        bots_count = 0

    stats = {
        "total": total_all,
        "humans": humans_count,
        "bots": bots_count,
        "online": 0,  # presence_status field dropped to save storage
    }
    _user_stats_cache["data"] = stats
    _user_stats_cache["expires_at"] = now + 30.0
    return stats


def get_paginated_users(page=1, limit=50, search=None, server=None, user_type=None, presence=None):
    """Retrieve paginated, deduplicated users directly from Turso or MongoDB."""
    if is_turso_active():
        import turso_database
        return turso_database.get_paginated_users(
            server=server,
            search=search,
            user_type=user_type,
            page=page,
            limit=limit,
        )

    collection = get_collection("discord_users")
    try:
        page = max(1, int(page))
    except (ValueError, TypeError):
        page = 1
    try:
        limit = min(200, max(10, int(limit)))
    except (ValueError, TypeError):
        limit = 50

    skip = (page - 1) * limit
    query = {}

    if server:
        query["servers"] = server
    if user_type == "human":
        query["is_bot"] = False
    elif user_type == "bot":
        query["is_bot"] = True

    if presence and presence != "all":
        pass  # presence_status field dropped — filter ignored

    if search:
        s_clean = search.strip()
        regex_pattern = {"$regex": s_clean, "$options": "i"}
        query["$or"] = [
            {"username": regex_pattern},
            {"display_name": regex_pattern},
            {"user_id": regex_pattern},
            {"roles": regex_pattern},
            {"servers": regex_pattern},
        ]

    total_count = collection.count_documents(query)
    # Sort by _id descending (insertion order) — last_seen_at field dropped
    cursor = collection.find(query).sort("_id", DESCENDING).skip(skip).limit(limit)

    users = []
    for doc in cursor:
        doc.pop("_id", None)
        servers_list = doc.get("servers", [])
        if isinstance(servers_list, list):
            doc["servers"] = [s for s in servers_list if s]
            doc["server_name"] = ", ".join(doc["servers"]) if doc["servers"] else "—"
        else:
            doc["servers"] = [str(servers_list)] if servers_list else []
            doc["server_name"] = str(servers_list) if servers_list else "—"

        # channels field dropped — always return empty
        doc["channels"] = []
        doc["channel_name"] = "—"

        doc["assigned_roles"] = [r for r in doc.get("roles", []) if r and r != "@everyone"]
        users.append(doc)

    total_pages = max(1, (total_count + limit - 1) // limit)

    stats = _get_cached_user_stats(collection, total_count)

    return {
        "users": users,
        "total": total_count,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "stats": stats,
    }


def migrate_slim_users():
    """One-time migration: strip dropped fields from all existing user documents.
    Frees ~220MB from the 596k existing documents in Atlas.
    Safe to call multiple times — $unset is a no-op on missing fields.
    """
    # Drop redundant indexes to free index space immediately
    drop_redundant_indexes()

    try:
        collection = get_collection("discord_users")
        # Check if legacy fields exist before attempting update
        if not collection.find_one({"$or": [{"channels": {"$exists": True}}, {"bot_status": {"$exists": True}}]}):
            logger.info("migrate_slim_users: documents already slimmed, skipping.")
            return 0

        result = collection.update_many(
            {},  # all documents
            {"$unset": {
                "avatar_url": "",
                "channels": "",
                "server_nickname": "",
                "presence_status": "",
                "last_seen_at": "",
                "joined_at": "",
                "bot_status": "",
            }}
        )
        logger.info(
            "migrate_slim_users: stripped dropped fields from %d documents.",
            result.modified_count,
        )
        return result.modified_count
    except Exception as exc:
        logger.warning("migrate_slim_users failed (writes may still be blocked): %s", exc)
        return 0


_user_servers_cache = {"data": None, "expires_at": 0.0}


def get_all_user_servers():
    """Return distinct server names where indexed users exist (cached 30s)."""
    if is_turso_active():
        import turso_database
        return turso_database.get_all_user_servers()

    now = time.time()
    if _user_servers_cache["data"] is not None and now < _user_servers_cache["expires_at"]:
        return _user_servers_cache["data"]
    collection = get_collection("discord_users")
    result = sorted([s for s in collection.distinct("servers") if s])
    _user_servers_cache["data"] = result
    _user_servers_cache["expires_at"] = now + 30.0
    return result


def get_campaign_target_users(server=None, user_type="human", limit=100):
    """Fetch all matching target users for a mass DM campaign."""
    if is_turso_active():
        import turso_database
        return turso_database.get_campaign_target_users(server=server, user_type=user_type, limit=limit)

    collection = get_collection("discord_users")
    query = {}
    if server and server != "all":
        query["servers"] = server
    if user_type == "human":
        query["is_bot"] = False
    elif user_type == "bot":
        query["is_bot"] = True

    # Exclude any user with admin/moderator roles from DM campaigns
    query["roles"] = {"$not": {"$regex": r"\b(admin|administrator|mod|moderator|owner|founder|staff|lead)\b", "$options": "i"}}

    cursor = collection.find(query, {
        "_id": 0,
        "user_id": 1,
        "username": 1,
        "display_name": 1,
        "servers": 1,
        "server_name": 1,
        "is_bot": 1,
    })
    if limit:
        cursor = cursor.limit(limit)

    return list(cursor)


def get_campaign_target_count(server=None, user_type="human"):
    """Get the total count of target members for a preview."""
    if is_turso_active():
        import turso_database
        return turso_database.get_campaign_target_count(server=server, user_type=user_type)

    collection = get_collection("discord_users")
    query = {}
    if server and server != "all":
        query["servers"] = server
    if user_type == "human":
        query["is_bot"] = False
    elif user_type == "bot":
        query["is_bot"] = True

    # Exclude any user with admin/moderator roles from DM campaigns
    query["roles"] = {"$not": {"$regex": r"\b(admin|administrator|mod|moderator|owner|founder|staff|lead)\b", "$options": "i"}}

    return collection.count_documents(query)


def save_campaign_record(campaign_data):
    """Save or update campaign state in Turso or MongoDB."""
    if is_turso_active():
        import turso_database
        return turso_database.save_campaign_record(campaign_data)

    collection = get_collection("dm_campaigns")
    cid = campaign_data.get("id")
    if not cid:
        return
    collection.update_one({"id": cid}, {"$set": campaign_data}, upsert=True)


def get_latest_campaign_record():
    """Fetch the latest campaign state."""
    if is_turso_active():
        import turso_database
        return turso_database.get_latest_campaign_record()

    collection = get_collection("dm_campaigns")
    doc = collection.find_one(sort=[("updated_at", -1)])
    if doc:
        doc.pop("_id", None)
    return doc


_server_counts_cache = {"data": None, "expires_at": 0.0}
_server_counts_lock = Lock()


def get_server_member_counts():
    """Aggregate member count per server in Turso or MongoDB."""
    if is_turso_active():
        import turso_database
        return turso_database.get_server_member_counts()

    now = time.time()
    if _server_counts_cache["data"] is not None and now < _server_counts_cache["expires_at"]:
        return _server_counts_cache["data"]

    # Non-blocking lock acquisition if stale data exists: return existing data immediately without waiting
    acquired = _server_counts_lock.acquire(blocking=(_server_counts_cache["data"] is None))
    if not acquired:
        return _server_counts_cache["data"] or {}

    try:
        now = time.time()
        if _server_counts_cache["data"] is not None and now < _server_counts_cache["expires_at"]:
            return _server_counts_cache["data"]

        collection = get_collection("discord_users")
        pipeline = [
            {"$unwind": "$servers"},
            {"$group": {"_id": "$servers", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        results = {}
        try:
            for doc in collection.aggregate(pipeline, maxTimeMS=20000):
                s_name = doc.get("_id")
                if s_name:
                    results[s_name] = doc.get("count", 0)
        except Exception as e:
            logger.warning("get_server_member_counts aggregation warning: %s", e)

        if results:
            _server_counts_cache["data"] = results
            _server_counts_cache["expires_at"] = time.time() + 180.0
        elif _server_counts_cache["data"] is not None:
            # Keep stale data for another 30 seconds on transient error
            _server_counts_cache["expires_at"] = time.time() + 30.0

        return _server_counts_cache["data"] or results
    finally:
        _server_counts_lock.release()


def save_official_guild_stats(guild_list):
    """Persist official Discord server metadata and official member counts in MongoDB."""
    if not guild_list:
        return
    try:
        collection = get_collection("guild_official_stats")
        for g in guild_list:
            gid = str(g.get("guild_id") or "")
            if not gid:
                continue
            collection.update_one(
                {"guild_id": gid},
                {"$set": {
                    "guild_id": gid,
                    "server_name": g.get("server_name"),
                    "official_count": g.get("official_count", 0),
                    "icon_url": g.get("icon_url"),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True
            )
    except Exception as exc:
        logger.debug("save_official_guild_stats skipped (cluster writes may be blocked): %s", exc)


def get_official_guild_stats():
    """Retrieve persisted official Discord server stats from MongoDB."""
    collection = get_collection("guild_official_stats")
    records = {}
    try:
        for doc in collection.find({}):
            doc.pop("_id", None)
            s_name = doc.get("server_name")
            if s_name:
                records[s_name] = doc
    except Exception:
        pass
    return records


def save_scanned_prefixes(guild_id: str, visited: set, pending_queue: list = None):
    """Persist visited prefixes AND pending queue for a guild so the search resumes exactly where it stopped."""
    if is_turso_active():
        import turso_database
        return turso_database.save_scanned_prefixes(guild_id, visited, pending_queue)
    try:
        col = get_collection("prefix_scan_progress")
        doc = {
            "guild_id": guild_id,
            "scanned": list(visited),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if pending_queue is not None:
            doc["pending"] = list(pending_queue)
        col.update_one(
            {"guild_id": guild_id},
            {"$set": doc},
            upsert=True,
        )
    except Exception:
        pass


def get_scanned_prefixes(guild_id: str) -> tuple:
    """Retrieve visited prefixes AND pending queue for a guild.
    Returns (visited_set, pending_list).
    """
    if is_turso_active():
        import turso_database
        return turso_database.get_scanned_prefixes(guild_id)
    try:
        col = get_collection("prefix_scan_progress")
        doc = col.find_one({"guild_id": guild_id})
        if doc:
            return set(doc.get("scanned", [])), list(doc.get("pending", []))
    except Exception:
        pass
    return set(), []


def clear_scanned_prefixes(guild_id: str):
    """Clear the scanned prefix progress for a guild (triggers fresh full scan)."""
    if is_turso_active():
        import turso_database
        return turso_database.clear_scanned_prefixes(guild_id)
    try:
        get_collection("prefix_scan_progress").delete_one({"guild_id": guild_id})
    except Exception:
        pass


def save_sync_status(guild_id: str, guild_name: str, status: dict):
    """Persist real-time sync progress for a guild."""
    if is_turso_active():
        import turso_database
        return turso_database.save_sync_status(guild_id, guild_name, status)
    try:
        col = get_collection("sync_status")
        col.update_one(
            {"guild_id": guild_id},
            {"$set": {
                "guild_id": guild_id,
                "guild_name": guild_name,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                **status,
            }},
            upsert=True,
        )
    except Exception:
        pass


def get_all_sync_status() -> list:
    """Retrieve sync status for all guilds."""
    if is_turso_active():
        import turso_database
        return turso_database.get_all_sync_status()
    try:
        col = get_collection("sync_status")
        return [
            {k: v for k, v in doc.items() if k != "_id"}
            for doc in col.find({})
        ]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTED EMAILS COLLECTION & STORAGE
# ─────────────────────────────────────────────────────────────────────────────

def ensure_email_indexes():
    """Ensure indexes for extracted_emails collection."""
    try:
        col = get_collection("extracted_emails")
        col.create_index([("email", ASCENDING)], unique=True)
        col.create_index([("platform", ASCENDING)])
        col.create_index([("extracted_at", DESCENDING)])
    except Exception as exc:
        logger.debug("Failed to create extracted_emails indexes: %s", exc)


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
    """Store or update an extracted email in Turso AND MongoDB (dual-write).
    If Turso reaches its monthly write quota, automatically falls back to MongoDB Atlas.
    """
    clean_email = (email or "").strip().lower()
    if not clean_email or "@" not in clean_email:
        return False

    turso_saved = False
    mongo_saved = False

    # 1. Try saving to Turso libSQL
    if is_turso_active():
        try:
            import turso_database
            turso_saved = turso_database.save_extracted_email(
                email=clean_email,
                platform=platform,
                user_id=user_id,
                username=username,
                full_name=full_name,
                phone=phone,
                bio=bio,
                source=source,
                metadata=metadata
            )
        except Exception as exc:
            logger.warning("Turso write quota limit or error: %s. Relying on MongoDB Atlas.", exc)

    # 2. Always dual-write to MongoDB Atlas (guaranteed backup)
    try:
        col = get_collection("extracted_emails")
        col.update_one(
            {"email": clean_email},
            {"$set": {
                "email": clean_email,
                "platform": platform or "unknown",
                "user_id": str(user_id or ""),
                "username": username or "",
                "full_name": full_name or "",
                "phone": phone or "",
                "bio": bio or "",
                "source": source or "",
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "metadata": metadata or {},
            }},
            upsert=True
        )
        mongo_saved = True
    except Exception as exc:
        logger.warning("MongoDB write notice: %s", exc)

    return turso_saved or mongo_saved


def get_extracted_emails(filter_query: dict = None, skip: int = 0, limit: int = 100) -> list:
    """Retrieve list of extracted emails from Turso, falling back to MongoDB Atlas if needed."""
    if is_turso_active():
        try:
            import turso_database
            res = turso_database.get_extracted_emails(filter_query=filter_query, skip=skip, limit=limit)
            if res:
                return res
        except Exception as exc:
            logger.warning("Turso read failed: %s. Fetching from MongoDB.", exc)

    try:
        col = get_collection("extracted_emails")
        q = filter_query or {}
        cursor = col.find(q).sort("extracted_at", DESCENDING).skip(skip).limit(limit)
        results = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            results.append(doc)
        return results
    except Exception as exc:
        logger.error("Error fetching extracted emails from MongoDB: %s", exc)
        return []


def get_extracted_emails_count(platform: str = None) -> int:
    """Get total count of extracted emails from Turso, with MongoDB fallback."""
    if is_turso_active():
        try:
            import turso_database
            return turso_database.get_extracted_emails_count(platform=platform)
        except Exception:
            pass

    try:
        col = get_collection("extracted_emails")
        q = {"platform": platform} if platform else {}
        return col.count_documents(q)
    except Exception as exc:
        logger.error("Error counting extracted emails: %s", exc)
        return 0


