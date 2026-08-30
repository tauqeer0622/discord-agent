import asyncio
import base64
import io
import json
import logging
import math
import mimetypes
import os
import random
from database import (
    acquire_reply_slot,
    bulk_upsert_users,
    get_all_user_servers,
    get_campaign_target_count,
    get_message_by_id,
    get_messages,
    get_paginated_users,
    release_reply_slot,
    save_reply_for_message,
)
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import discord
from aiohttp import ClientSession, web

from mass_dm_manager import mass_dm_manager

from config import DISCORD_TOKEN, ADMIN_PASSWORD
from config_manager import config_manager
from discord_permissions import (
    can_send_messages,
    is_locked_or_private_channel,
    is_restricted_text_channel,
)
from message_listener import process_message

def generate_auth_token(password: str) -> str:
    return hmac.new(password.encode("utf-8"), b"discord_command_center_auth_v1", hashlib.sha256).hexdigest()

@web.middleware
async def auth_middleware(request, handler):
    # Allow public UI shells, health check (/api/status), auth endpoints, and CORS preflight
    public_paths = {"/", "/messages", "/api/status", "/api/auth/login", "/api/auth/check"}
    if request.path in public_paths or request.method == "OPTIONS":
        return await handler(request)

    # Allow Render infrastructure health-check probes
    if "Render" in request.headers.get("User-Agent", ""):
        return await handler(request)

    # Check Authorization header (Bearer <token>), cookie, or query param
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header.split("Bearer ", 1)[1].strip()
    elif "token" in request.query:
        token = request.query.get("token", "")
    elif "auth_token" in request.cookies:
        token = request.cookies.get("auth_token", "")

    # Also allow X-Admin-Password header
    admin_pw = request.headers.get("X-Admin-Password")
    if admin_pw and admin_pw == ADMIN_PASSWORD:
        return await handler(request)

    expected = generate_auth_token(ADMIN_PASSWORD)
    if not token or not hmac.compare_digest(token, expected):
        return web.json_response(
            {"error": "Unauthorized. Please enter Admin Password to access Discord Command Center."},
            status=401,
            headers=CORS_HEADERS,
        )

    return await handler(request)
from market_image_renderer import (
    generate_visual_style_for_market,
    parse_market_data,
    render_market_image,
    sample_market_data_json,
)
from promo_sender import generate_promo_variant
from state_manager import state
from thread_manager import thread_cleanup_loop
from typing_simulator import calculate_typing_duration

from database import initialize_database

initialize_database()

logger = logging.getLogger(__name__)

MEMORY_FILE = os.path.join(os.path.dirname(__file__), "ai_memory.json")
DASHBOARD_FILE = os.path.join(os.path.dirname(__file__), "dashboard.html")
MESSAGES_FILE = os.path.join(
    os.path.dirname(__file__),
    "messages.html"
)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, PATCH, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, ngrok-skip-browser-warning",
}

try:
    PROMO_MAX_CHANNELS = max(1, int(os.getenv("PROMO_MAX_CHANNELS", "100")))
except ValueError:
    PROMO_MAX_CHANNELS = 100
PROMO_DEFAULT_MIN_DELAY_SECONDS = 120
PROMO_DEFAULT_MAX_DELAY_SECONDS = 300
PROMO_MAX_MESSAGE_LENGTH = 1900
try:
    PROMO_PREVIEW_CONCURRENCY = max(1, int(os.getenv("PROMO_PREVIEW_CONCURRENCY", "8")))
except ValueError:
    PROMO_PREVIEW_CONCURRENCY = 8
BULK_PROMO_ENABLED = os.getenv("BULK_PROMO_ENABLED", "true").lower() == "true"
DISCORD_AUTH_PROBE_ENABLED = os.getenv("DISCORD_AUTH_PROBE_ENABLED", "false").lower() == "true"
try:
    PROMO_MAX_IMAGE_BYTES = max(1, int(os.getenv("PROMO_MAX_IMAGE_MB", "8"))) * 1024 * 1024
except ValueError:
    PROMO_MAX_IMAGE_BYTES = 8 * 1024 * 1024
PROMO_ALLOWED_IMAGE_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
DISCORD_API_BASE_URL = "https://discord.com/api/v9"
DISCORD_TEXT_CHANNEL_TYPE = 0
DISCORD_ANNOUNCEMENT_CHANNEL_TYPE = 5
DISCORD_CATEGORY_CHANNEL_TYPE = 4
DISCORD_ADDABLE_CHANNEL_TYPES = {
    DISCORD_TEXT_CHANNEL_TYPE,
    DISCORD_ANNOUNCEMENT_CHANNEL_TYPE,
}
VIEW_CHANNEL_PERMISSION = 1 << 10
SEND_MESSAGES_PERMISSION = 1 << 11
ADMINISTRATOR_PERMISSION = 1 << 3
GATE_CHANNEL_NAME_PARTS = (
    "access",
    "verify",
    "verification",
)


def _permission_value(overwrite, key):
    try:
        return int(overwrite.get(key, 0))
    except (TypeError, ValueError):
        return 0


def _default_role_view_state(channel_data, guild_id):
    for overwrite in channel_data.get("permission_overwrites", []) or []:
        if str(overwrite.get("id")) != str(guild_id):
            continue
        if _permission_value(overwrite, "deny") & VIEW_CHANNEL_PERMISSION:
            return False
        if _permission_value(overwrite, "allow") & VIEW_CHANNEL_PERMISSION:
            return True
    return None


def _role_permission_value(role):
    permissions = getattr(role, "permissions", None)
    return int(getattr(permissions, "value", 0) or 0)


def _current_guild_member(guild, user):
    user_id = getattr(user, "id", None)
    if user_id is not None and hasattr(guild, "get_member"):
        member = guild.get_member(user_id)
        if member is not None:
            return member
    return getattr(guild, "me", None)


def _base_member_permissions(guild, member):
    default_role = getattr(guild, "default_role", None)
    permissions = _role_permission_value(default_role)

    for role in getattr(member, "roles", []) or []:
        if getattr(role, "id", None) == getattr(guild, "id", None):
            continue
        permissions |= _role_permission_value(role)

    return permissions


def _apply_raw_overwrite(permissions, overwrite):
    allow = _permission_value(overwrite, "allow")
    deny = _permission_value(overwrite, "deny")
    return (permissions & ~deny) | allow


def _raw_effective_permissions(channel_data, category_by_id, guild, user):
    member = _current_guild_member(guild, user)
    user_id = getattr(user, "id", None)
    role_ids = {
        str(getattr(role, "id", ""))
        for role in getattr(member, "roles", []) or []
    }
    role_ids.add(str(guild.id))

    permissions = _base_member_permissions(guild, member)
    if permissions & ADMINISTRATOR_PERMISSION:
        return permissions | VIEW_CHANNEL_PERMISSION | SEND_MESSAGES_PERMISSION

    permission_source = channel_data
    if not channel_data.get("permission_overwrites"):
        parent_id = channel_data.get("parent_id")
        parent = category_by_id.get(str(parent_id)) if parent_id else None
        if parent:
            permission_source = parent

    overwrites = permission_source.get("permission_overwrites", []) or []

    for overwrite in overwrites:
        if str(overwrite.get("id")) == str(guild.id):
            permissions = _apply_raw_overwrite(permissions, overwrite)
            break

    deny = 0
    allow = 0
    for overwrite in overwrites:
        if str(overwrite.get("id")) == str(guild.id):
            continue
        if overwrite.get("type") == 0 and str(overwrite.get("id")) in role_ids:
            deny |= _permission_value(overwrite, "deny")
            allow |= _permission_value(overwrite, "allow")
    permissions = (permissions & ~deny) | allow

    if user_id is not None:
        for overwrite in overwrites:
            if overwrite.get("type") == 1 and str(overwrite.get("id")) == str(user_id):
                permissions = _apply_raw_overwrite(permissions, overwrite)
                break

    return permissions


def _raw_channel_can_send(channel_data, category_by_id, guild, user):
    permissions = _raw_effective_permissions(channel_data, category_by_id, guild, user)
    return (
        bool(permissions & VIEW_CHANNEL_PERMISSION)
        and bool(permissions & SEND_MESSAGES_PERMISSION)
    )


def _raw_channel_is_locked(channel_data, category_by_id, guild_id):
    channel_state = _default_role_view_state(channel_data, guild_id)
    if channel_state is not None:
        return channel_state is False
    if channel_data.get("permission_overwrites"):
        return False

    parent_id = channel_data.get("parent_id")
    parent = category_by_id.get(str(parent_id)) if parent_id else None
    if not parent:
        return False

    parent_state = _default_role_view_state(parent, guild_id)
    return parent_state is False


def _channel_name_key(name):
    return "".join(
        char.lower() if char.isalnum() else "-"
        for char in str(name or "")
    ).strip("-")


def _is_gate_channel_name(name):
    key = _channel_name_key(name)
    return any(part in key for part in GATE_CHANNEL_NAME_PARTS)


def _raw_channel_is_addable(channel_data, category_by_id, guild, user):
    cached_channel = guild.get_channel(int(channel_data["id"]))
    if cached_channel is not None:
        can_send = can_send_messages(cached_channel, user)
        is_locked = is_locked_or_private_channel(cached_channel)
    else:
        can_send = _raw_channel_can_send(channel_data, category_by_id, guild, user)
        is_locked = _raw_channel_is_locked(channel_data, category_by_id, guild.id)

    return (
        channel_data.get("type") in DISCORD_ADDABLE_CHANNEL_TYPES
        and not _is_gate_channel_name(channel_data.get("name"))
        and not is_locked
        and can_send
    )


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


async def _probe_discord_token(token):
    result = {
        "ok": False,
        "status": None,
        "checked_at": _utc_now_iso(),
        "error": None,
    }
    headers = {
        "Authorization": token,
        "User-Agent": "Mozilla/5.0",
    }
    url = f"{DISCORD_API_BASE_URL}/users/@me"

    try:
        async with ClientSession(headers=headers) as session:
            async with session.get(url) as response:
                result["status"] = response.status
                if response.status >= 400:
                    text = await response.text()
                    result["error"] = text[:200]
                    return result

                data = await response.json()
                result.update({
                    "ok": True,
                    "user_id": str(data.get("id") or ""),
                    "username": data.get("username"),
                })
                return result
    except Exception as exc:
        result["error"] = str(exc)[:200]
        return result


async def _hold_web_server_for_diagnostics():
    while True:
        await asyncio.sleep(3600)


async def _watch_discord_startup(client, timeout_seconds=120):
    await asyncio.sleep(timeout_seconds)
    if client.is_ready() or client.discord_connect_seen_at:
        return
    client.discord_last_error = (
        "Discord login is still pending; Render may be rate-limited or blocked by Discord."
    )
    logger.warning(client.discord_last_error)


class CommandCenterClient(discord.Client):
    def __init__(self):
        super().__init__()

        self.web_server_started = False
        self.web_runner = None
        self.thread_cleanup_started = False
        self.start_time = datetime.now(timezone.utc)
        self.bulk_promo_job = None
        self.bulk_promo_task = None
        self.discord_login_started_at = None
        self.discord_ready_at = None
        self.discord_connect_seen_at = None
        self.discord_disconnect_seen_at = None
        self.discord_last_error = None
        self.discord_auth_probe = None
        mass_dm_manager.set_client(self)

    # ── Web Server ─────────────────────────────────────────────

    async def start_web_server(self):
        app = web.Application(middlewares=[auth_middleware])
        app.add_routes([
            # Dashboard Shell
            web.get("/",                                  self.handle_dashboard),
            # Auth
            web.post("/api/auth/login",                   self.handle_post_auth_login),
            web.get("/api/auth/check",                    self.handle_get_auth_check),
            # Status & data reads
            web.get("/api/status",                        self.handle_get_status),
            web.get("/api/messages",                      self.handle_get_messages),
            web.post("/api/messages/{message_id}/reply",  self.handle_post_message_reply),
            web.get("/api/users",                         self.handle_get_users),
            web.get("/api/users/servers",                 self.handle_get_user_servers),
            web.post("/api/users/{user_id}/dm",           self.handle_post_user_dm),
            web.get("/api/memory",                        self.handle_get_memory),
            web.get("/api/channels",                      self.handle_get_channels),
            web.get("/api/guilds",                        self.handle_get_guilds),
            # Mass DM Broadcast Campaign
            web.get("/api/campaign/target-count",         self.handle_get_campaign_target_count),
            web.post("/api/campaign/start",               self.handle_post_campaign_start),
            web.get("/api/campaign/status",              self.handle_get_campaign_status),
            web.post("/api/campaign/control",             self.handle_post_campaign_control),
            # Config CRUD
            web.get("/api/configs",                       self.handle_get_configs),
            web.post("/api/configs",                      self.handle_post_config),
            web.delete("/api/configs/{channel_id}",       self.handle_delete_config),
            web.patch("/api/configs/{channel_id}/toggle", self.handle_toggle_config),
            # Bulk promo
            web.post("/api/bulk-promo/preview",           self.handle_bulk_promo_preview),
            web.post("/api/bulk-promo/send",              self.handle_bulk_promo_send),
            web.post("/api/bulk-promo/cancel",            self.handle_bulk_promo_cancel),
            web.get("/api/bulk-promo/status",             self.handle_bulk_promo_status),
            web.post("/api/market-image/generate",        self.handle_market_image_generate),
            # CORS pre-flight (catch-all)
            web.options("/{path_info:.*}",                self.handle_options_generic),
            web.get("/messages", self.handle_messages_page),
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        self.web_runner = runner
        port = int(os.getenv("PORT", "8080"))
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info("Web API + Dashboard started on http://0.0.0.0:%s", port)

    # ── Route Handlers ─────────────────────────────────────────

    async def handle_options_generic(self, request):
        return web.Response(headers=CORS_HEADERS)

    async def handle_dashboard(self, request):
        """Serve the single-page dashboard HTML."""
        try:
            with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
                content = f.read()
            return web.Response(content_type="text/html", text=content)
        except FileNotFoundError:
            return web.Response(
                content_type="text/html",
                text="<h1>Dashboard not found.</h1><p>Make sure dashboard.html exists next to main.py.</p>",
                status=404,
            )

    async def handle_post_auth_login(self, request):
        """Verify admin password and return secure session token."""
        try:
            data = await request.json()
            pwd = str(data.get("password", "")).strip()
            if not pwd or not hmac.compare_digest(pwd, ADMIN_PASSWORD):
                return web.json_response({"error": "Incorrect admin password. Please try again."}, status=401, headers=CORS_HEADERS)

            token = generate_auth_token(ADMIN_PASSWORD)
            resp = web.json_response({"success": True, "token": token}, headers=CORS_HEADERS)
            resp.set_cookie("auth_token", token, max_age=86400 * 30, httponly=True, samesite="Lax")
            return resp
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400, headers=CORS_HEADERS)

    async def handle_get_auth_check(self, request):
        """Check if request contains valid auth token."""
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header.split("Bearer ", 1)[1].strip()
        elif "token" in request.query:
            token = request.query.get("token", "")
        elif "auth_token" in request.cookies:
            token = request.cookies.get("auth_token", "")

        valid = bool(token and hmac.compare_digest(token, generate_auth_token(ADMIN_PASSWORD)))
        return web.json_response({"authenticated": valid}, headers=CORS_HEADERS)

    async def handle_get_status(self, request):
        """Return bot connection status and basic info."""
        uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds()
        latency = getattr(self, "latency", None)
        latency_ms = None
        if latency is not None and math.isfinite(latency):
            latency_ms = int(latency * 1000)
        data = {
            "online": self.is_ready(),
            "bot_name": f"{self.user.name}#{self.user.discriminator}" if self.is_ready() else None,
            "bot_id": str(self.user.id) if self.is_ready() else None,
            "guild_count": len(self.guilds),
            "uptime_seconds": int(uptime),
            "start_time": self.start_time.isoformat(),
            "discord": {
                "ready": self.is_ready(),
                "closed": self.is_closed(),
                "latency_ms": latency_ms,
                "login_started_at": self.discord_login_started_at,
                "ready_at": self.discord_ready_at,
                "connect_seen_at": self.discord_connect_seen_at,
                "disconnect_seen_at": self.discord_disconnect_seen_at,
                "last_error": self.discord_last_error,
                "auth_probe": self.discord_auth_probe,
            },
        }
        return web.json_response(data, headers=CORS_HEADERS)

    async def handle_get_messages(self, request):

        rows = get_messages()

        data = []

        for row in rows:
            data.append({
                "id": row[0],
                "author": row[1],
                "content": row[2],
                "channel": row[3],
                "guild": row[4],
                "timestamp": row[5],
                "channel_id": str(row[6]) if row[6] is not None else None,
                "guild_id": str(row[7]) if row[7] is not None else None,
                "source_message_id": str(row[8]) if row[8] is not None else None,
                "source_url": (
                    f"https://discord.com/channels/{row[7]}/{row[6]}/{row[8]}"
                    if row[6] is not None and row[7] is not None and row[8] is not None
                    else (
                        f"https://discord.com/channels/{row[7]}/{row[6]}"
                        if row[6] is not None and row[7] is not None
                        else None
                    )
                ),
                "reply": row[9] or None,
                "reply_timestamp": row[10] if len(row) > 10 else None,
                "author_id": str(row[11]) if len(row) > 11 and row[11] is not None else None,
                "reply_type": row[12] if len(row) > 12 else None,
            })

        return web.json_response(
            data,
            headers=CORS_HEADERS
        )

    async def handle_post_message_reply(self, request):
        try:
            message_id = request.match_info["message_id"]
            data = await request.json()
            reply_content = str(data.get("reply", "")).strip()
            mode = str(data.get("mode", "channel")).strip().lower()
            send_as_dm = bool(data.get("send_as_dm", False)) or (mode == "dm")

            if not reply_content:
                return web.json_response(
                    {"error": "Reply message is required"},
                    status=400, headers=CORS_HEADERS,
                )
            if len(reply_content) > PROMO_MAX_MESSAGE_LENGTH:
                return web.json_response(
                    {"error": f"Reply must be under {PROMO_MAX_MESSAGE_LENGTH} characters"},
                    status=400, headers=CORS_HEADERS,
                )

            stored_message = get_message_by_id(message_id)
            if not stored_message:
                return web.json_response(
                    {"error": "Message not found"},
                    status=404, headers=CORS_HEADERS,
                )
            if stored_message.get("reply_content"):
                return web.json_response(
                    {"error": "This message is already replied"},
                    status=409, headers=CORS_HEADERS,
                )

            # ── Handle Private DM Reply Mode ───────────────────────
            if send_as_dm:
                author_id = stored_message.get("author_id")
                channel_id = stored_message.get("channel_id")
                source_message_id = stored_message.get("source_message_id")

                # If author_id was not yet in stored_message (older legacy entry), attempt to fetch from channel
                if not author_id and channel_id and source_message_id:
                    source_channel = self.get_channel(int(channel_id))
                    if source_channel is None:
                        try:
                            source_channel = await self.fetch_channel(int(channel_id))
                        except (discord.NotFound, discord.Forbidden):
                            source_channel = None
                    if source_channel:
                        try:
                            orig = await source_channel.fetch_message(int(source_message_id))
                            if orig and orig.author:
                                author_id = orig.author.id
                        except Exception:
                            pass

                if not author_id:
                    return web.json_response(
                        {"error": "Sender User ID could not be identified for DM"},
                        status=400, headers=CORS_HEADERS,
                    )

                target_user = self.get_user(int(author_id))
                if target_user is None:
                    try:
                        target_user = await self.fetch_user(int(author_id))
                    except (discord.NotFound, discord.Forbidden):
                        target_user = None

                if target_user is None:
                    return web.json_response(
                        {"error": f"Discord user ({author_id}) is unavailable or not found"},
                        status=404, headers=CORS_HEADERS,
                    )

                reply_slot = acquire_reply_slot()
                if not reply_slot:
                    return web.json_response(
                        {"error": "Global Discord reply limit reached"},
                        status=429, headers=CORS_HEADERS,
                    )

                try:
                    async with target_user.typing():
                        await asyncio.sleep(calculate_typing_duration(reply_content))
                    await target_user.send(reply_content)
                except discord.Forbidden:
                    release_reply_slot(reply_slot)
                    return web.json_response(
                        {"error": f"Cannot send DM to @{target_user.name}: User has direct messages closed or blocked."},
                        status=403, headers=CORS_HEADERS,
                    )
                except Exception as exc:
                    release_reply_slot(reply_slot)
                    logger.error("Dashboard DM reply failed: %s", exc)
                    return web.json_response(
                        {"error": f"Failed to send DM to @{target_user.name}: {str(exc)}"},
                        status=500, headers=CORS_HEADERS,
                    )

                replied_at = datetime.now(timezone.utc)
                save_reply_for_message(message_id, reply_content, replied_at, reply_type="dm")
                logger.info(
                    "Dashboard DM reply sent for message %s to user %s (%s)",
                    message_id,
                    target_user.name,
                    author_id,
                )
                return web.json_response(
                    {
                        "success": True,
                        "reply": reply_content,
                        "reply_type": "dm",
                        "reply_timestamp": replied_at.isoformat(),
                    },
                    headers=CORS_HEADERS,
                )

            # ── Handle Public Channel Reply Mode ───────────────────
            channel_id = stored_message.get("channel_id")
            if not channel_id:
                return web.json_response(
                    {"error": "Source channel is missing for this message"},
                    status=400, headers=CORS_HEADERS,
                )
            if int(channel_id) not in config_manager.get_active_channel_ids():
                return web.json_response(
                    {"error": "Source channel is no longer monitored"},
                    status=400, headers=CORS_HEADERS,
                )

            source_channel = self.get_channel(int(channel_id))
            if source_channel is None:
                try:
                    source_channel = await self.fetch_channel(int(channel_id))
                except (discord.NotFound, discord.Forbidden):
                    source_channel = None
            if source_channel is None:
                return web.json_response(
                    {"error": "Source channel is unavailable"},
                    status=400, headers=CORS_HEADERS,
                )
            if is_restricted_text_channel(source_channel, self.user):
                return web.json_response(
                    {"error": "Source channel is locked/private or cannot be posted in"},
                    status=403, headers=CORS_HEADERS,
                )
            source_message_id = stored_message.get("source_message_id")
            if not source_message_id:
                return web.json_response(
                    {"error": "Original Discord message reference is missing"},
                    status=400, headers=CORS_HEADERS,
                )
            try:
                original_message = await source_channel.fetch_message(int(source_message_id))
            except discord.NotFound:
                return web.json_response(
                    {"error": "Original Discord message was not found"},
                    status=404, headers=CORS_HEADERS,
                )
            except discord.Forbidden:
                return web.json_response(
                    {"error": "Cannot access the original Discord message"},
                    status=403, headers=CORS_HEADERS,
                )
            try:
                latest_message = None
                async for message in source_channel.history(limit=1):
                    latest_message = message
                should_reference_original = (
                    latest_message is not None
                    and latest_message.id != int(source_message_id)
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                logger.warning(
                    "Could not check latest message in channel %s before reply: %s",
                    channel_id,
                    exc,
                )
                should_reference_original = True

            reply_slot = acquire_reply_slot()
            if not reply_slot:
                return web.json_response(
                    {"error": "Global Discord reply limit reached"},
                    status=429, headers=CORS_HEADERS,
                )

            try:
                async with source_channel.typing():
                    await asyncio.sleep(calculate_typing_duration(reply_content))
                if should_reference_original:
                    await original_message.reply(
                        reply_content,
                        mention_author=False,
                    )
                else:
                    await source_channel.send(reply_content)
            except Exception as exc:
                release_reply_slot(reply_slot)
                logger.error("Dashboard Discord reply failed: %s", exc)
                return web.json_response(
                    {"error": "Reply failed to send to the source channel"},
                    status=500, headers=CORS_HEADERS,
                )

            replied_at = datetime.now(timezone.utc)
            save_reply_for_message(message_id, reply_content, replied_at, reply_type="channel")
            logger.info(
                "Dashboard reply sent for message %s to channel %s",
                message_id,
                channel_id,
            )
            return web.json_response(
                {
                    "success": True,
                    "reply": reply_content,
                    "reply_type": "channel",
                    "reply_timestamp": replied_at.isoformat(),
                },
                headers=CORS_HEADERS,
            )
        except Exception as e:
            logger.error("dashboard reply error: %s", e)
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    async def handle_get_configs(self, request):
        """Return all configured target channels from config_manager."""
        configs = config_manager.get_all()

        for c in configs:
            c["channel_id"] = str(c["channel_id"])
            if c.get("guild_id"):
                c["guild_id"] = str(c["guild_id"])

        return web.json_response(configs, headers=CORS_HEADERS)

    async def handle_post_config(self, request):
        """Add a new target channel configuration."""
        try:
            data = await request.json()
            logger.info(f"POST CONFIG DATA: {data}")
            label      = str(data.get("label", "")).strip()
            channel_id = int(data.get("channel_id", 0))
            guild_id   = int(data.get("guild_id", 0)) if data.get("guild_id") else None
            guild_name = str(data.get("guild_name", "")).strip() or None

            if not label or not channel_id:
                return web.json_response(
                    {"error": "'label' and 'channel_id' are required"},
                    status=400, headers=CORS_HEADERS,
                )

            if guild_id:
                guild = self.get_guild(guild_id)
                if guild is None:
                    return web.json_response(
                        {"error": "Guild/Server not found or bot does not have access"},
                        status=404, headers=CORS_HEADERS,
                    )
                addable_channels = await self._get_addable_raw_channels(guild)
                if not any(int(ch["id"]) == channel_id for ch in addable_channels):
                    return web.json_response(
                        {"error": "Locked/private channels cannot be monitored"},
                        status=403, headers=CORS_HEADERS,
                    )

            channel = self.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden):
                    channel = None

            if channel is None or not isinstance(channel, discord.TextChannel):
                return web.json_response(
                    {"error": "Text channel not found or unavailable"},
                    status=404, headers=CORS_HEADERS,
                )

            if is_restricted_text_channel(channel, self.user):
                return web.json_response(
                    {"error": "Restricted/private channels cannot be monitored"},
                    status=403, headers=CORS_HEADERS,
                )

            success = config_manager.add(label, channel_id, guild_id, guild_name)
            if not success:
                return web.json_response(
                    {"error": "Channel is already being monitored"},
                    status=409, headers=CORS_HEADERS,
                )
            return web.json_response({"success": True}, status=201, headers=CORS_HEADERS)
        except Exception as e:
            logger.error(f"handle_post_config error: {e}")
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    async def handle_delete_config(self, request):
        """Remove a target channel configuration by channel_id."""
        try:
            channel_id = int(request.match_info["channel_id"])
            success = config_manager.remove(channel_id)
            if not success:
                return web.json_response({"error": "Config not found"}, status=404, headers=CORS_HEADERS)
            return web.json_response({"success": True}, headers=CORS_HEADERS)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    async def handle_toggle_config(self, request):
        """Toggle the active/inactive state of a channel config."""
        try:
            channel_id = int(request.match_info["channel_id"])
            new_state = config_manager.toggle(channel_id)
            if new_state is None:
                return web.json_response({"error": "Config not found"}, status=404, headers=CORS_HEADERS)
            return web.json_response({"success": True, "active": new_state}, headers=CORS_HEADERS)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    async def _get_bulk_promo_targets(self, channel_ids=None, max_channels=None):
        selected_ids = {str(item) for item in channel_ids or [] if str(item).strip()}
        configs = [
            config
            for config in config_manager.get_all()
            if config.get("active", True)
        ]
        if selected_ids:
            configs = [
                config
                for config in configs
                if str(config.get("channel_id")) in selected_ids
            ]

        targets = []
        target_limit = min(max_channels, PROMO_MAX_CHANNELS) if max_channels else PROMO_MAX_CHANNELS
        for config in configs:
            if len(targets) >= target_limit:
                break

            channel_id = int(config["channel_id"])
            channel = self.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden):
                    channel = None

            target = {
                "channel_id": str(channel_id),
                "label": config.get("label") or str(channel_id),
                "guild_id": str(config.get("guild_id") or ""),
                "guild_name": config.get("guild_name") or "",
                "channel_name": getattr(channel, "name", config.get("label") or str(channel_id)),
            }

            if channel is None or not isinstance(channel, discord.TextChannel):
                target["status"] = "unavailable"
                target["error"] = "Text channel unavailable"
                targets.append(target)
                continue

            if is_restricted_text_channel(channel, self.user):
                target["status"] = "blocked"
                target["error"] = "Channel is locked/private or cannot be posted in"
                targets.append(target)
                continue

            target["status"] = "ready"
            target["_channel"] = channel
            targets.append(target)

        return targets

    def _parse_optional_positive_int(self, value, default=None, maximum=None):
        if value in (None, ""):
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        if parsed < 1:
            return default
        if maximum is not None:
            return min(parsed, maximum)
        return parsed

    def _validate_bulk_message(self, base_message, allow_empty=False):
        if not base_message:
            return None if allow_empty else "Promo message or image is required"
        if len(base_message) < 5:
            return "Promo message must be at least 5 characters"
        if len(base_message) > PROMO_MAX_MESSAGE_LENGTH:
            return f"Promo message must be under {PROMO_MAX_MESSAGE_LENGTH} characters"
        return None

    def _validate_bulk_image(self, image_payload):
        if not image_payload:
            return None
        content_type = image_payload.get("content_type") or ""
        if content_type not in PROMO_ALLOWED_IMAGE_TYPES:
            return "Image must be PNG, JPEG, GIF, or WEBP"
        size = len(image_payload.get("data") or b"")
        if size <= 0:
            return "Selected image is empty"
        if size > PROMO_MAX_IMAGE_BYTES:
            max_mb = PROMO_MAX_IMAGE_BYTES // (1024 * 1024)
            return f"Image must be {max_mb} MB or smaller"
        return None

    def _decode_generated_bulk_image(self, image_json):
        if not image_json:
            return None
        if isinstance(image_json, str):
            try:
                image_json = json.loads(image_json)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Generated image data must be valid JSON") from exc
        if not isinstance(image_json, dict):
            raise RuntimeError("Generated image data must be a JSON object")
        content_type = image_json.get("content_type") or "image/png"
        if content_type not in PROMO_ALLOWED_IMAGE_TYPES:
            raise RuntimeError("Generated image must be PNG, JPEG, GIF, or WEBP")
        try:
            image_data = base64.b64decode(image_json.get("data_base64") or "", validate=True)
        except Exception as exc:
            raise RuntimeError("Generated image data is invalid") from exc
        return {
            "filename": image_json.get("filename") or "market-summary.png",
            "content_type": content_type,
            "data": image_data,
        }

    async def _read_bulk_promo_send_data(self, request):
        if not request.content_type.startswith("multipart/"):
            return await request.json(), None

        data = {}
        image_payload = None
        reader = await request.multipart()
        async for part in reader:
            if part.name == "image" and part.filename:
                filename = os.path.basename(part.filename) or "promo-image"
                content_type = (
                    part.headers.get("Content-Type", "")
                    or mimetypes.guess_type(filename)[0]
                    or ""
                )
                image_payload = {
                    "filename": filename,
                    "content_type": content_type,
                    "data": await part.read(decode=False),
                }
                continue

            value = await part.text()
            if part.name == "previews":
                try:
                    data["previews"] = json.loads(value)
                except json.JSONDecodeError:
                    data["previews"] = []
            else:
                data[part.name] = value

        return data, image_payload

    async def _build_market_image(self, raw_data):
        from ai_engine import client as openai_client
        market_data = parse_market_data(raw_data)

        # If the caller pinned a style explicitly, use it.
        # Otherwise the LLM generates a fully custom style from scratch.
        explicit_style = (
            isinstance(raw_data, dict) and raw_data.get("visual_style")
        ) or (
            isinstance(raw_data, str) and "visual_style" in raw_data
        )
        if not explicit_style:
            generated = await generate_visual_style_for_market(market_data, openai_client)
            market_data["_generated_style"] = generated   # picked up by _visual_style()
            market_data["_style_rationale"] = generated.get("_rationale", "")
        else:
            market_data["_style_rationale"] = "explicit"

        image_bytes = await render_market_image(market_data)
        encoded = base64.b64encode(image_bytes).decode("ascii")
        style_meta = market_data.get("_generated_style") or {}
        return {
            "filename": "market-summary.png",
            "content_type": "image/png",
            "data_base64": encoded,
            "data_url": f"data:image/png;base64,{encoded}",
            "sample_data": sample_market_data_json(),
            "style": {
                "generated": not explicit_style,
                "layout": style_meta.get("layout"),
                "accent": style_meta.get("accent"),
                "font": style_meta.get("font"),
                "rationale": market_data.get("_style_rationale"),
            },
        }

    async def handle_market_image_generate(self, request):
        try:
            data = await request.json()
            market_image = await self._build_market_image(data.get("market_image_data"))
            return web.json_response(
                {"market_image": market_image},
                headers=CORS_HEADERS,
            )
        except Exception as e:
            logger.error("market image generate error: %s", e)
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    def _public_bulk_target(self, target):
        return {
            key: value
            for key, value in target.items()
            if not key.startswith("_")
        }

    async def _build_bulk_promo_variants(self, base_message, targets):
        ready_targets = [target for target in targets if target.get("status") == "ready"]
        total = len(ready_targets)
        if not total:
            return []

        semaphore = asyncio.Semaphore(min(PROMO_PREVIEW_CONCURRENCY, total))

        async def build_preview(index, target):
            preview = self._public_bulk_target(target)
            async with semaphore:
                try:
                    content = await generate_promo_variant(base_message, target, index, total)
                    if not content:
                        raise RuntimeError("Generated message was empty")
                    if len(content) > PROMO_MAX_MESSAGE_LENGTH:
                        raise RuntimeError("Generated message was too long")
                    preview["content"] = content
                except Exception as e:
                    preview["status"] = "preview_failed"
                    preview["error"] = str(e)
            return preview

        return await asyncio.gather(
            *(
                build_preview(index, target)
                for index, target in enumerate(ready_targets, start=1)
            )
        )

    def _bulk_promo_status_response(self):
        if not self.bulk_promo_job:
            return {"running": False, "job": None}
        data = dict(self.bulk_promo_job)
        data["running"] = bool(self.bulk_promo_task and not self.bulk_promo_task.done())
        if data.get("next_send_at") and data["running"]:
            try:
                next_send_at = datetime.fromisoformat(data["next_send_at"])
                remaining = int((next_send_at - datetime.now(timezone.utc)).total_seconds())
                data["next_delay_seconds"] = max(0, remaining)
            except (TypeError, ValueError):
                pass
        return {"running": data["running"], "job": data}

    async def handle_bulk_promo_preview(self, request):
        try:
            if not BULK_PROMO_ENABLED:
                return web.json_response(
                    {"error": "Bulk promo sender is disabled"},
                    status=403, headers=CORS_HEADERS,
                )

            data = await request.json()
            base_message = str(data.get("base_message", "")).strip()
            image_selected = bool(data.get("image_selected"))
            market_image_enabled = bool(data.get("market_image_enabled"))
            validation_error = self._validate_bulk_message(
                base_message,
                allow_empty=image_selected or market_image_enabled,
            )
            if validation_error:
                return web.json_response(
                    {"error": validation_error},
                    status=400, headers=CORS_HEADERS,
                )

            max_channels = self._parse_optional_positive_int(
                data.get("max_channels"),
                maximum=PROMO_MAX_CHANNELS,
            )
            targets = await self._get_bulk_promo_targets(
                data.get("channel_ids"),
                max_channels=max_channels,
            )
            if base_message:
                previews = await self._build_bulk_promo_variants(base_message, targets)
            else:
                previews = [
                    {
                        **self._public_bulk_target(target),
                        "content": "",
                        "image_only": True,
                    }
                    for target in targets
                    if target.get("status") == "ready"
                ]
            market_image = None
            if market_image_enabled:
                market_image = await self._build_market_image(
                    data.get("market_image_data"),
                )
            return web.json_response(
                {
                    "targets": [self._public_bulk_target(target) for target in targets],
                    "previews": previews,
                    "market_image": market_image,
                },
                headers=CORS_HEADERS,
            )
        except Exception as e:
            logger.error("bulk promo preview error: %s", e)
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    async def handle_bulk_promo_send(self, request):
        try:
            if not BULK_PROMO_ENABLED:
                return web.json_response(
                    {"error": "Bulk promo sender is disabled"},
                    status=403, headers=CORS_HEADERS,
                )

            if self.bulk_promo_task and not self.bulk_promo_task.done():
                return web.json_response(
                    {"error": "A bulk promo send is already running"},
                    status=409, headers=CORS_HEADERS,
                )

            data, image_payload = await self._read_bulk_promo_send_data(request)
            if not image_payload and data.get("generated_image"):
                image_payload = self._decode_generated_bulk_image(data.get("generated_image"))
            image_validation_error = self._validate_bulk_image(image_payload)
            if image_validation_error:
                return web.json_response(
                    {"error": image_validation_error},
                    status=400, headers=CORS_HEADERS,
                )

            base_message = str(data.get("base_message", "")).strip()
            validation_error = self._validate_bulk_message(
                base_message,
                allow_empty=bool(image_payload),
            )
            if validation_error:
                return web.json_response(
                    {"error": validation_error},
                    status=400, headers=CORS_HEADERS,
                )

            requested_min_delay = self._parse_optional_positive_int(
                data.get("min_delay_seconds"),
                default=PROMO_DEFAULT_MIN_DELAY_SECONDS,
            )
            requested_max_delay = self._parse_optional_positive_int(
                data.get("max_delay_seconds"),
                default=PROMO_DEFAULT_MAX_DELAY_SECONDS,
            )
            min_delay = max(
                PROMO_DEFAULT_MIN_DELAY_SECONDS,
                requested_min_delay,
            )
            max_delay = min(
                PROMO_DEFAULT_MAX_DELAY_SECONDS,
                requested_max_delay,
            )
            if max_delay < min_delay:
                max_delay = min_delay

            preview_content = {}
            for item in data.get("previews", []):
                channel_id = item.get("channel_id")
                content = str(item.get("content", "")).strip()
                if not channel_id:
                    continue
                if len(content) > PROMO_MAX_MESSAGE_LENGTH:
                    continue
                if content or image_payload:
                    preview_content[str(channel_id)] = content
            if not preview_content:
                return web.json_response(
                    {"error": "Preview is required before sending"},
                    status=400, headers=CORS_HEADERS,
                )

            max_channels = self._parse_optional_positive_int(
                data.get("max_channels"),
                maximum=PROMO_MAX_CHANNELS,
            )
            targets = await self._get_bulk_promo_targets(
                list(preview_content.keys()),
                max_channels=max_channels,
            )
            ready_targets = [target for target in targets if target.get("status") == "ready"]
            if not ready_targets:
                return web.json_response(
                    {"error": "No monitored channels are ready for promo sending"},
                    status=400, headers=CORS_HEADERS,
                )

            self.bulk_promo_job = {
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "base_message": base_message,
                "min_delay_seconds": min_delay,
                "max_delay_seconds": max_delay,
                "total": len(ready_targets),
                "sent": 0,
                "failed": 0,
                "has_image": bool(image_payload),
                "image_filename": image_payload.get("filename") if image_payload else None,
                "results": [],
                "current_channel_id": None,
                "next_send_at": None,
            }
            self.bulk_promo_task = asyncio.create_task(
                self._run_bulk_promo_job(
                    base_message,
                    ready_targets,
                    preview_content,
                    image_payload,
                    min_delay,
                    max_delay,
                )
            )
            return web.json_response(
                self._bulk_promo_status_response(),
                status=202, headers=CORS_HEADERS,
            )
        except Exception as e:
            logger.error("bulk promo send error: %s", e)
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    async def handle_bulk_promo_cancel(self, request):
        if not self.bulk_promo_task or self.bulk_promo_task.done():
            return web.json_response(
                {"error": "No bulk promo send is running"},
                status=404, headers=CORS_HEADERS,
            )
        self.bulk_promo_task.cancel()
        return web.json_response(
            self._bulk_promo_status_response(),
            headers=CORS_HEADERS,
        )

    async def handle_bulk_promo_status(self, request):
        return web.json_response(
            self._bulk_promo_status_response(),
            headers=CORS_HEADERS,
        )

    async def _run_bulk_promo_job(
        self,
        base_message,
        targets,
        preview_content,
        image_payload,
        min_delay,
        max_delay,
    ):
        try:
            for index, target in enumerate(targets, start=1):
                result = self._public_bulk_target(target)
                self.bulk_promo_job["current_channel_id"] = str(target["channel_id"])
                try:
                    content = preview_content.get(str(target["channel_id"]))
                    if not content and base_message and not image_payload:
                        content = await generate_promo_variant(
                            base_message,
                            target,
                            index,
                            len(targets),
                        )
                    if content and len(content) > PROMO_MAX_MESSAGE_LENGTH:
                        raise RuntimeError("Promo message was too long")
                    if not content and not image_payload:
                        raise RuntimeError("Promo message was empty")

                    file = None
                    if image_payload:
                        file = discord.File(
                            io.BytesIO(image_payload["data"]),
                            filename=image_payload["filename"],
                        )
                    send_kwargs = {"file": file} if file else {}
                    await target["_channel"].send(content or None, **send_kwargs)
                    result["status"] = "sent"
                    result["content"] = content
                    result["has_image"] = bool(image_payload)
                    if image_payload:
                        result["image_filename"] = image_payload["filename"]
                    result["sent_at"] = datetime.now(timezone.utc).isoformat()
                    self.bulk_promo_job["sent"] += 1
                except Exception as e:
                    result["status"] = "failed"
                    result["error"] = str(e)
                    self.bulk_promo_job["failed"] += 1

                self.bulk_promo_job["results"].append(result)

                if index < len(targets):
                    delay = random.randint(min_delay, max_delay)
                    self.bulk_promo_job["next_delay_seconds"] = delay
                    self.bulk_promo_job["next_send_at"] = (
                        datetime.now(timezone.utc) + timedelta(seconds=delay)
                    ).isoformat()
                    await asyncio.sleep(delay)

            self.bulk_promo_job["status"] = "completed"
        except asyncio.CancelledError:
            self.bulk_promo_job["status"] = "cancelled"
            raise
        except Exception as e:
            logger.error("bulk promo job failed: %s", e)
            self.bulk_promo_job["status"] = "failed"
            self.bulk_promo_job["error"] = str(e)
        finally:
            self.bulk_promo_job["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.bulk_promo_job["current_channel_id"] = None
            self.bulk_promo_job.pop("next_delay_seconds", None)
            self.bulk_promo_job.pop("next_send_at", None)

    async def handle_get_guilds(self, request):
        """Return all guilds by calling Discord API directly."""
        try:

            fetched_guilds = await self.fetch_guilds(with_counts=True)

            guilds = []

            for g in fetched_guilds:
                guilds.append({
                    "id": str(g.id),
                    "name": g.name,
                    "member_count": getattr(g, "member_count", None),
                    "icon_url": str(g.icon.url) if g.icon else None,
                })

            return web.json_response(
                guilds,
                headers=CORS_HEADERS
            )


        except Exception as e:

            logger.error(f"fetch_guilds error: {e}")

            # Fallback to cached guilds
            guilds = [
                {
                    "id": str(g.id),
                    "name": g.name,
                    "member_count": getattr(g, "member_count", None),
                    "icon_url": str(g.icon.url) if g.icon else None,
                }
                for g in self.guilds
            ]

            return web.json_response(
                guilds,
                headers=CORS_HEADERS
            )

    async def _sync_guild_members_to_db(self):
        """High-speed member discovery across all joined guilds via channel history + search scraping."""
        if getattr(self, "_syncing_members", False):
            return
        self._syncing_members = True
        try:
            for guild in list(self.guilds):
                role_map = {role.id: role.name for role in guild.roles if role.name != "@everyone"}
                active_configs = [
                    c for c in config_manager.get_all()
                    if str(c.get("guild_id")) == str(guild.id) and c.get("active", True)
                ]
                default_channel_str = f"#{guild.name}"
                if active_configs:
                    default_channel_str = ", ".join(f"#{c.get('label') or c.get('channel_id')}" for c in active_configs[:3])
                elif guild.text_channels:
                    default_channel_str = ", ".join(f"#{ch.name}" for ch in guild.text_channels[:3])

                # 1. Subscribe to the server and scrape all members over Gateway (sidebar scraper)
                try:
                    await guild.subscribe()
                except Exception:
                    pass

                try:
                    # fetch_members with force_scraping=True downloads all members from the whole server
                    scraped_members = await asyncio.wait_for(
                        guild.fetch_members(cache=True, force_scraping=True, delay=0.05),
                        timeout=25.0
                    )
                    if scraped_members:
                        batch = []
                        for member in scraped_members:
                            display_name = getattr(member, "global_name", None) or member.display_name or member.name
                            roles = [role_map.get(r.id, r.name) for r in member.roles if r.name != "@everyone"]
                            avatar_url = str(member.avatar.url) if member.avatar else None
                            batch.append({
                                "user_id": str(member.id),
                                "username": member.name,
                                "display_name": display_name,
                                "server_nickname": member.nick or display_name,
                                "server_name": guild.name,
                                "channel_name": default_channel_str,
                                "assigned_roles": roles,
                                "is_bot": bool(member.bot),
                                "presence_status": str(getattr(member, "status", "offline")),
                                "avatar_url": avatar_url,
                                "joined_at": member.joined_at.isoformat() if getattr(member, "joined_at", None) else None,
                            })
                        if batch:
                            bulk_upsert_users(batch)
                except Exception as exc:
                    logger.debug("Gateway fetch_members for %s: %s", guild.name, exc)

                # 2. Deep 2-Character Gateway query_members across character permutations
                try:
                    chars = "abcdefghijklmnopqrstuvwxyz0123456789_"
                    prefixes = list(chars) + [c1 + c2 for c1 in chars for c2 in chars]
                    batch = []
                    for prefix in prefixes:
                        try:
                            matched = await asyncio.wait_for(
                                guild.query_members(query=prefix, limit=100, cache=True),
                                timeout=3.0
                            )
                            if matched:
                                for member in matched:
                                    display_name = getattr(member, "global_name", None) or member.display_name or member.name
                                    roles = [role_map.get(r.id, r.name) for r in member.roles if r.name != "@everyone"]
                                    avatar_url = str(member.avatar.url) if member.avatar else None
                                    batch.append({
                                        "user_id": str(member.id),
                                        "username": member.name,
                                        "display_name": display_name,
                                        "server_nickname": member.nick or display_name,
                                        "server_name": guild.name,
                                        "channel_name": default_channel_str,
                                        "assigned_roles": roles,
                                        "is_bot": bool(member.bot),
                                        "presence_status": str(getattr(member, "status", "offline")),
                                        "avatar_url": avatar_url,
                                        "joined_at": member.joined_at.isoformat() if getattr(member, "joined_at", None) else None,
                                    })
                                if len(batch) >= 200:
                                    bulk_upsert_users(batch)
                                    batch = []
                        except Exception:
                            pass
                        await asyncio.sleep(0.02)
                    if batch:
                        bulk_upsert_users(batch)
                except Exception as q_err:
                    logger.debug("Gateway deep query_members for %s: %s", guild.name, q_err)

                # Upsert all members currently in memory/gateway
                cached_members = list(guild.members)
                if cached_members:
                    batch = []
                    for member in cached_members:
                        display_name = getattr(member, "global_name", None) or member.display_name or member.name
                        roles = [role_map.get(r.id, r.name) for r in member.roles if r.name != "@everyone"]
                        avatar_url = str(member.avatar.url) if member.avatar else None
                        batch.append({
                            "user_id": str(member.id),
                            "username": member.name,
                            "display_name": display_name,
                            "server_nickname": member.nick or display_name,
                            "server_name": guild.name,
                            "channel_name": default_channel_str,
                            "assigned_roles": roles,
                            "is_bot": bool(member.bot),
                            "presence_status": str(getattr(member, "status", "offline")),
                            "avatar_url": avatar_url,
                            "joined_at": member.joined_at.isoformat() if getattr(member, "joined_at", None) else None,
                        })
                    if batch:
                        bulk_upsert_users(batch)

                # 2. Fast Channel History Scraping (Reads messages across accessible channels in the guild)
                # User accounts have full permission to read message history across channels
                accessible_channels = [
                    ch for ch in guild.text_channels 
                    if ch.permissions_for(guild.me).read_message_history and not is_restricted_text_channel(ch, self.user)
                ]
                for ch in accessible_channels[:25]:
                    try:
                        channel_users = {}
                        async for msg in ch.history(limit=250):
                            author = msg.author
                            if not author or author.id in channel_users:
                                continue
                            author_name = getattr(author, "name", "Unknown")
                            display_name = getattr(author, "global_name", None) or getattr(author, "display_name", None) or author_name
                            server_nick = getattr(author, "nick", None) or display_name
                            avatar_url = str(author.avatar.url) if getattr(author, "avatar", None) else None
                            roles = []
                            if hasattr(author, "roles"):
                                roles = [role_map.get(r.id, r.name) for r in author.roles if r.name != "@everyone"]

                            channel_users[author.id] = {
                                "user_id": str(author.id),
                                "username": author_name,
                                "display_name": display_name,
                                "server_nickname": server_nick,
                                "server_name": guild.name,
                                "channel_name": f"#{ch.name}",
                                "assigned_roles": roles,
                                "is_bot": bool(getattr(author, "bot", False)),
                                "presence_status": str(getattr(author, "status", "offline")),
                                "avatar_url": avatar_url,
                                "last_seen_at": msg.created_at.isoformat() if msg.created_at else None,
                            }
                            # Also capture any mentioned members
                            for m_user in getattr(msg, "mentions", []):
                                if m_user and m_user.id not in channel_users:
                                    m_name = getattr(m_user, "name", "Unknown")
                                    m_disp = getattr(m_user, "global_name", None) or getattr(m_user, "display_name", None) or m_name
                                    channel_users[m_user.id] = {
                                        "user_id": str(m_user.id),
                                        "username": m_name,
                                        "display_name": m_disp,
                                        "server_nickname": getattr(m_user, "nick", None) or m_disp,
                                        "server_name": guild.name,
                                        "channel_name": f"#{ch.name}",
                                        "assigned_roles": [role_map.get(r.id, r.name) for r in getattr(m_user, "roles", []) if r.name != "@everyone"],
                                        "is_bot": bool(getattr(m_user, "bot", False)),
                                        "presence_status": "offline",
                                        "avatar_url": str(m_user.avatar.url) if getattr(m_user, "avatar", None) else None,
                                    }

                        if channel_users:
                            bulk_upsert_users(list(channel_users.values()))
                        await asyncio.sleep(0.08)
                    except Exception as ch_err:
                        logger.debug("Channel history sync error for %s: %s", ch.name, ch_err)

                # 3. Discord Search API Indexing with expanded 2-letter search prefixes
                headers = {
                    "Authorization": DISCORD_TOKEN,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                }
                async with ClientSession(headers=headers) as session:
                    # Common name prefixes to discover members rapidly
                    prefixes = [
                        "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
                        "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
                        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "_", ".",
                        "al", "an", "ar", "ch", "cr", "da", "de", "el", "em", "en", "ha",
                        "ja", "jo", "ka", "ke", "ma", "mi", "mo", "pa", "ra", "ro", "sa",
                        "sh", "si", "th", "to", "vi", "za"
                    ]
                    for q in prefixes:
                        search_url = f"https://discord.com/api/v9/guilds/{guild.id}/members/search?query={q}&limit=100"
                        try:
                            async with session.get(search_url) as resp:
                                if resp.status == 200:
                                    raw_list = await resp.json()
                                    if raw_list and isinstance(raw_list, list):
                                        batch = []
                                        for rm in raw_list:
                                            u = rm.get("user", {})
                                            uid = str(u.get("id") or "")
                                            if not uid:
                                                continue
                                            username = u.get("username", "Unknown")
                                            display_name = u.get("global_name") or username
                                            server_nick = rm.get("nick") or display_name
                                            is_bot = bool(u.get("bot", False))
                                            avatar = u.get("avatar")
                                            avatar_url = f"https://cdn.discordapp.com/avatars/{uid}/{avatar}.png" if avatar else None
                                            member_roles = [
                                                role_map.get(int(rid), str(rid))
                                                for rid in rm.get("roles", [])
                                                if int(rid) in role_map
                                            ]
                                            batch.append({
                                                "user_id": uid,
                                                "username": username,
                                                "display_name": display_name,
                                                "server_nickname": server_nick,
                                                "server_name": guild.name,
                                                "channel_name": default_channel_str,
                                                "assigned_roles": member_roles,
                                                "is_bot": is_bot,
                                                "presence_status": "offline",
                                                "avatar_url": avatar_url,
                                                "joined_at": rm.get("joined_at"),
                                            })
                                        if batch:
                                            bulk_upsert_users(batch)
                            await asyncio.sleep(0.12)
                        except Exception:
                            pass

                await asyncio.sleep(0.2)
        except Exception as exc:
            logger.warning("Background user sync warning: %s", exc)
        finally:
            self._syncing_members = False

    async def handle_get_users(self, request):
        """Return paginated, deduplicated members from MongoDB with instant filtering."""
        try:
            # Kick off background sync
            asyncio.create_task(self._sync_guild_members_to_db())

            page = request.query.get("page", "1")
            limit = request.query.get("limit", "50")
            search = request.query.get("search")
            server = request.query.get("server")
            user_type = request.query.get("type")
            presence = request.query.get("presence")

            result = get_paginated_users(
                page=page,
                limit=limit,
                search=search,
                server=server,
                user_type=user_type,
                presence=presence,
            )

            return web.json_response(result, headers=CORS_HEADERS)
        except Exception as exc:
            logger.error("handle_get_users error: %s", exc)
            return web.json_response(
                {
                    "users": [],
                    "total": 0,
                    "page": 1,
                    "limit": 50,
                    "total_pages": 1,
                    "stats": {"total": 0, "humans": 0, "bots": 0, "online": 0},
                    "error": str(exc),
                },
                headers=CORS_HEADERS,
            )

    async def handle_get_user_servers(self, request):
        """Return distinct server names where users exist."""
        try:
            servers = get_all_user_servers()
            # Fallback to current guild names if database is empty
            if not servers:
                servers = sorted([g.name for g in self.guilds if g.name])
            return web.json_response(servers, headers=CORS_HEADERS)
        except Exception as exc:
            logger.error("handle_get_user_servers error: %s", exc)
            return web.json_response([], headers=CORS_HEADERS)

    async def handle_post_user_dm(self, request):
        """Send a direct DM to a user by user_id."""
        try:
            user_id = request.match_info["user_id"]
            data = await request.json()
            message_content = str(data.get("message", "")).strip()

            if not message_content:
                return web.json_response(
                    {"error": "Message content is required"},
                    status=400, headers=CORS_HEADERS,
                )
            if len(message_content) > PROMO_MAX_MESSAGE_LENGTH:
                return web.json_response(
                    {"error": f"Message must be under {PROMO_MAX_MESSAGE_LENGTH} characters"},
                    status=400, headers=CORS_HEADERS,
                )

            target_user = self.get_user(int(user_id))
            if target_user is None:
                try:
                    target_user = await self.fetch_user(int(user_id))
                except (discord.NotFound, discord.Forbidden):
                    target_user = None

            if target_user is None:
                return web.json_response(
                    {"error": f"Discord user ({user_id}) not found or unavailable"},
                    status=404, headers=CORS_HEADERS,
                )

            reply_slot = acquire_reply_slot()
            if not reply_slot:
                return web.json_response(
                    {"error": "Global Discord reply limit reached"},
                    status=429, headers=CORS_HEADERS,
                )

            try:
                async with target_user.typing():
                    await asyncio.sleep(calculate_typing_duration(message_content))
                await target_user.send(message_content)
            except discord.Forbidden:
                release_reply_slot(reply_slot)
                return web.json_response(
                    {"error": f"Cannot send DM to @{target_user.name}: User has direct messages closed or blocked."},
                    status=403, headers=CORS_HEADERS,
                )
            except Exception as exc:
                release_reply_slot(reply_slot)
                logger.error("Direct User DM failed: %s", exc)
                return web.json_response(
                    {"error": f"Failed to send DM to @{target_user.name}: {str(exc)}"},
                    status=500, headers=CORS_HEADERS,
                )

            logger.info("Direct DM sent to user %s (%s)", target_user.name, user_id)
            return web.json_response(
                {
                    "success": True,
                    "user_id": str(user_id),
                    "username": target_user.name,
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                },
                headers=CORS_HEADERS,
            )
        except Exception as e:
            logger.error("handle_post_user_dm error: %s", e)
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    # ── Mass DM Campaign Handlers ──────────────────────────────

    async def handle_get_campaign_target_count(self, request):
        """Get target member count for selected server and type."""
        server = request.query.get("server")
        user_type = request.query.get("type", "human")
        count = get_campaign_target_count(server=server, user_type=user_type)
        return web.json_response({"server": server or "All Servers", "user_type": user_type, "count": count}, headers=CORS_HEADERS)

    async def handle_post_campaign_start(self, request):
        """Launch a new mass DM broadcast campaign."""
        try:
            data = await request.json()
            server_name = data.get("server")
            user_type = data.get("user_type", "human")
            message_template = str(data.get("message", "")).strip()
            delay_mode = data.get("delay_mode", "safe")

            image_data_b64 = data.get("image_data")
            image_filename = data.get("image_filename", "promo.png")
            image_bytes = None
            if image_data_b64:
                if "," in image_data_b64:
                    image_data_b64 = image_data_b64.split(",", 1)[1]
                image_bytes = base64.b64decode(image_data_b64)

            status = mass_dm_manager.start_campaign(
                server_name=server_name,
                user_type=user_type,
                message_template=message_template,
                image_bytes=image_bytes,
                image_filename=image_filename,
                delay_mode=delay_mode,
            )
            return web.json_response(status, headers=CORS_HEADERS)
        except Exception as exc:
            logger.error("handle_post_campaign_start error: %s", exc)
            return web.json_response({"error": str(exc)}, status=400, headers=CORS_HEADERS)

    async def handle_get_campaign_status(self, request):
        """Get current mass DM campaign status and delivery metrics."""
        status = mass_dm_manager.get_status()
        return web.json_response(status, headers=CORS_HEADERS)

    async def handle_post_campaign_control(self, request):
        """Pause, resume, or cancel the active mass DM campaign."""
        try:
            data = await request.json()
            action = data.get("action")
            if action == "pause":
                status = mass_dm_manager.pause_campaign()
            elif action == "resume":
                status = mass_dm_manager.resume_campaign()
            elif action == "stop":
                status = mass_dm_manager.stop_campaign()
            else:
                return web.json_response({"error": "Invalid action. Use 'pause', 'resume', or 'stop'."}, status=400, headers=CORS_HEADERS)
            return web.json_response(status, headers=CORS_HEADERS)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400, headers=CORS_HEADERS)

    async def handle_get_memory(self, request):
        """Return the AI conversation memory file."""
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                memory = json.load(f)
        except Exception:
            memory = {}
        return web.json_response(memory, headers=CORS_HEADERS)

    async def _fetch_raw_guild_channels(self, guild_id):
        headers = {
            "Authorization": DISCORD_TOKEN,
            "User-Agent": "Mozilla/5.0",
        }
        url = f"{DISCORD_API_BASE_URL}/guilds/{int(guild_id)}/channels"
        async with ClientSession(headers=headers) as session:
            async with session.get(url) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise RuntimeError(
                        f"Discord channel fetch failed with HTTP {response.status}: {text[:200]}"
                    )
                data = await response.json()
                if not isinstance(data, list):
                    raise RuntimeError("Discord channel fetch returned unexpected data")
                return data

    async def _get_addable_raw_channels(self, guild):
        raw_channels = await self._fetch_raw_guild_channels(guild.id)
        category_by_id = {
            str(ch["id"]): ch
            for ch in raw_channels
            if ch.get("type") == DISCORD_CATEGORY_CHANNEL_TYPE
        }
        return [
            ch
            for ch in sorted(raw_channels, key=lambda item: item.get("position", 0))
            if _raw_channel_is_addable(ch, category_by_id, guild, self.user)
        ]

    async def handle_get_channels(self, request):
        """Return text channels for a given guild (by id or name)."""
        guild_id_str = request.query.get("guild_id")
        server_name = request.query.get("server_name")

        if not guild_id_str and not server_name:
            return web.json_response(
                {"error": "Must provide guild_id or server_name parameter"},
                status=400, headers=CORS_HEADERS,
            )

        guild = None
        if guild_id_str:
            try:
                guild = self.get_guild(int(guild_id_str))
            except ValueError:
                return web.json_response({"error": "Invalid guild_id format"}, status=400, headers=CORS_HEADERS)
        elif server_name:
            guild = discord.utils.find(lambda g: g.name.lower() == server_name.lower(), self.guilds)

        if not guild:
            return web.json_response(
                {"error": "Guild/Server not found or bot does not have access"},
                status=404, headers=CORS_HEADERS,
            )

        # Return channel_id as STRING to prevent JavaScript 64-bit integer precision loss
        # Discord snowflake IDs exceed JS Number.MAX_SAFE_INTEGER (2^53)
        try:
            raw_channels = await self._get_addable_raw_channels(guild)
            channels = [
                {"channel_name": ch.get("name", "unknown"), "channel_id": str(ch["id"])}
                for ch in raw_channels
            ]
        except Exception as e:
            logger.warning(
                "Raw channel fetch failed for guild '%s'; using cache: %s",
                guild.name,
                e,
            )
            channels = [
                {"channel_name": ch.name, "channel_id": str(ch.id)}
                for ch in guild.text_channels
                if (
                    not _is_gate_channel_name(ch.name)
                    and not is_restricted_text_channel(ch, self.user)
                )
            ]
        return web.json_response(channels, headers=CORS_HEADERS)

    # ── Discord Events ─────────────────────────────────────────

    async def on_connect(self):
        self.discord_connect_seen_at = _utc_now_iso()
        self.discord_last_error = None
        logger.info("Discord gateway connected.")

    async def _member_sync_loop(self):
        """Runs periodic member sync every 10 minutes to continuously discover new members."""
        await asyncio.sleep(10)  # Initial wait
        while True:
            try:
                await self._sync_guild_members_to_db()
            except Exception as e:
                logger.debug("Periodic member sync loop notice: %s", e)
            await asyncio.sleep(600)  # Repeat every 10 minutes

    async def on_disconnect(self):
        self.discord_disconnect_seen_at = _utc_now_iso()
        if not self.is_ready():
            self.discord_last_error = "Discord gateway disconnected before ready."
        logger.warning("Discord gateway disconnected.")

    async def on_ready(self):
        self.discord_ready_at = _utc_now_iso()
        self.discord_last_error = None
        logger.info(f"Logged in as {self.user.name}#{self.user.discriminator} (ID: {self.user.id})")
        logger.info("Command Center Prototype is active and monitoring...")


        if not self.thread_cleanup_started:
            asyncio.create_task(thread_cleanup_loop(self))
            self.thread_cleanup_started = True

        # Subscribe to all guilds that contain configured channels.
        # discord.py-self uses lazy loading for large guilds — without subscribing,
        # on_message will NOT fire for channels in large servers (e.g. LeetCode).
        active_ids = set(config_manager.get_active_channel_ids())
        subscribed = set()
        for guild in self.guilds:
            for ch in guild.text_channels:
                if (
                    ch.id in active_ids
                    and guild.id not in subscribed
                    and not is_restricted_text_channel(ch, self.user)
                ):
                    try:
                        await guild.subscribe()
                        subscribed.add(guild.id)
                        logger.info(f"Subscribed to guild '{guild.name}' for real-time events.")
                    except Exception as e:
                        logger.warning(f"Could not subscribe to guild '{guild.name}': {e}")
                    break

        # Passively scan and sync all server members into MongoDB & start background loop
        asyncio.create_task(self._member_sync_loop())

    async def on_message(self, message: discord.Message):
        await process_message(self, message)

    async def handle_messages_page(self, request):
        try:
            with open(
                    MESSAGES_FILE,
                    "r",
                    encoding="utf-8"
            ) as f:
                content = f.read()

            return web.Response(
                text=content,
                content_type="text/html"
            )

        except Exception as e:
            return web.Response(
                text=str(e),
                status=500
            )


# ── Entry Point ────────────────────────────────────────────────

async def run_service():
    if not DISCORD_TOKEN:
        logger.error("Cannot start bot without DISCORD_TOKEN. Please check your .env file.")
        return

    logger.info("Starting Discord Command Center...")
    client = CommandCenterClient()

    try:
        await client.start_web_server()
        client.web_server_started = True
        client.discord_login_started_at = _utc_now_iso()
        asyncio.create_task(_watch_discord_startup(client))
        if DISCORD_AUTH_PROBE_ENABLED:
            client.discord_auth_probe = await _probe_discord_token(DISCORD_TOKEN)
            auth_status = client.discord_auth_probe.get("status")
            if not client.discord_auth_probe.get("ok") and auth_status in (401, 403):
                status = client.discord_auth_probe.get("status")
                client.discord_last_error = f"Discord token check failed with HTTP {status}."
                logger.error("%s", client.discord_last_error)
                await _hold_web_server_for_diagnostics()
            elif not client.discord_auth_probe.get("ok"):
                logger.warning(
                    "Discord token check was inconclusive with HTTP %s; continuing gateway login.",
                    auth_status,
                )
        else:
            client.discord_auth_probe = {
                "ok": None,
                "status": None,
                "checked_at": None,
                "skipped": True,
            }

        await client.start(DISCORD_TOKEN)
    except discord.errors.LoginFailure as e:
        client.discord_last_error = "Discord login failed. Check DISCORD_TOKEN."
        logger.error("%s %s", client.discord_last_error, e)
        await _hold_web_server_for_diagnostics()
    except Exception as e:
        client.discord_last_error = str(e)[:200]
        logger.error(f"Critical error: {e}")
    finally:
        if not client.is_closed():
            await client.close()
        if client.web_runner is not None:
            await client.web_runner.cleanup()


def main():
    try:
        asyncio.run(run_service())
    except KeyboardInterrupt:
        logger.info("Discord Command Center stopped.")


if __name__ == "__main__":
    main()
