from datetime import datetime, timedelta, timezone
import logging
import os
import sqlite3
from threading import Lock

import certifi
from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient, UpdateOne
from bson import ObjectId

load_dotenv()

logger = logging.getLogger(__name__)

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "discord_agent_db")
LEGACY_DB_PATH = os.getenv("LEGACY_DB_PATH", "discord_agent.db")
HOURLY_REPLY_LIMIT = 10
DAILY_REPLY_LIMIT = 100
MESSAGE_RETENTION_DAYS = 7

_client = None
_database = None
_reply_limit_lock = Lock()


def get_database():
    global _client, _database

    if not MONGODB_URI:
        raise RuntimeError("MONGODB_URI is missing from the environment.")

    if _database is None:
        _client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=10000,
            tlsCAFile=certifi.where(),
            tz_aware=True,
        )
        _database = _client[MONGODB_DB_NAME]

    return _database


def get_collection(name):
    return get_database()[name]


def initialize_database():
    database = get_database()
    _client.admin.command("ping")

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
        [("username", ASCENDING)]
    )
    database.discord_users.create_index(
        [("display_name", ASCENDING)]
    )
    database.discord_users.create_index(
        [("servers", ASCENDING)]
    )
    database.discord_users.create_index(
        [("last_seen_at", DESCENDING)]
    )
    database.discord_users.create_index(
        [("is_bot", ASCENDING)]
    )
    database.discord_users.create_index(
        [("presence_status", ASCENDING)]
    )

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
    """Upsert a single Discord user into MongoDB ensuring zero duplicates."""
    if not user_data or not user_data.get("user_id"):
        return
    user_id = str(user_data["user_id"])
    now_iso = datetime.now(timezone.utc).isoformat()

    set_fields = {
        "username": user_data.get("username", "Unknown"),
        "display_name": user_data.get("display_name") or user_data.get("username", "Unknown"),
        "server_nickname": user_data.get("server_nickname") or user_data.get("display_name") or user_data.get("username", "—"),
        "is_bot": bool(user_data.get("is_bot", False)),
        "bot_status": "Bot" if user_data.get("is_bot") else "Human",
        "presence_status": user_data.get("presence_status", "offline"),
        "last_seen_at": user_data.get("last_seen_at") or now_iso,
    }
    if user_data.get("avatar_url"):
        set_fields["avatar_url"] = user_data["avatar_url"]
    if user_data.get("joined_at"):
        set_fields["joined_at"] = user_data["joined_at"]

    add_to_set = {}
    if user_data.get("server_name"):
        add_to_set["servers"] = user_data["server_name"]
    if user_data.get("channel_name"):
        add_to_set["channels"] = user_data["channel_name"]
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
    """Bulk upsert thousands/millions of users into MongoDB with high throughput."""
    if not users_list:
        return 0
    now_iso = datetime.now(timezone.utc).isoformat()
    operations = []

    for u in users_list:
        user_id = str(u.get("user_id", ""))
        if not user_id:
            continue
        set_fields = {
            "username": u.get("username", "Unknown"),
            "display_name": u.get("display_name") or u.get("username", "Unknown"),
            "server_nickname": u.get("server_nickname") or u.get("display_name") or u.get("username", "—"),
            "is_bot": bool(u.get("is_bot", False)),
            "bot_status": "Bot" if u.get("is_bot") else "Human",
            "presence_status": u.get("presence_status", "offline"),
            "last_seen_at": u.get("last_seen_at") or now_iso,
        }
        if u.get("avatar_url"):
            set_fields["avatar_url"] = u["avatar_url"]
        if u.get("joined_at"):
            set_fields["joined_at"] = u["joined_at"]

        add_to_set = {}
        if u.get("server_name"):
            add_to_set["servers"] = u["server_name"]
        if u.get("channel_name"):
            add_to_set["channels"] = u["channel_name"]
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


def get_paginated_users(page=1, limit=50, search=None, server=None, user_type=None, presence=None):
    """Retrieve paginated, deduplicated users directly from MongoDB with indexed filtering."""
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
        query["presence_status"] = presence.lower()

    if search:
        s_clean = search.strip()
        regex_pattern = {"$regex": s_clean, "$options": "i"}
        query["$or"] = [
            {"username": regex_pattern},
            {"display_name": regex_pattern},
            {"server_nickname": regex_pattern},
            {"user_id": regex_pattern},
            {"roles": regex_pattern},
            {"servers": regex_pattern},
        ]

    total_count = collection.count_documents(query)
    cursor = collection.find(query).sort("last_seen_at", DESCENDING).skip(skip).limit(limit)

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

        channels_list = doc.get("channels", [])
        if isinstance(channels_list, list):
            doc["channels"] = [c for c in channels_list if c]
            doc["channel_name"] = ", ".join(doc["channels"]) if doc["channels"] else "—"
        else:
            doc["channels"] = [str(channels_list)] if channels_list else []
            doc["channel_name"] = str(channels_list) if channels_list else "—"

        doc["assigned_roles"] = [r for r in doc.get("roles", []) if r and r != "@everyone"]
        users.append(doc)

    total_pages = max(1, (total_count + limit - 1) // limit)

    # Calculate global counters
    try:
        total_all = collection.estimated_document_count()
        humans_count = collection.count_documents({"is_bot": False})
        bots_count = collection.count_documents({"is_bot": True})
        online_count = collection.count_documents({"presence_status": {"$in": ["online", "idle", "dnd"]}})
    except Exception:
        total_all = total_count
        humans_count = 0
        bots_count = 0
        online_count = 0

    stats = {
        "total": total_all,
        "humans": humans_count,
        "bots": bots_count,
        "online": online_count,
    }

    return {
        "users": users,
        "total": total_count,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "stats": stats,
    }


def get_all_user_servers():
    """Return distinct server names where indexed users exist."""
    collection = get_collection("discord_users")
    return sorted([s for s in collection.distinct("servers") if s])
