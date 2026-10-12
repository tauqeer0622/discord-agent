import asyncio
import base64
import io
import json
import logging
import math
import mimetypes
import os
import random
import re
import urllib.parse
from database import (
    acquire_reply_slot,
    bulk_upsert_users,
    get_all_user_servers,
    get_campaign_target_count,
    get_extracted_emails,
    get_extracted_emails_count,
    get_message_by_id,
    get_messages,
    get_official_guild_stats,
    get_paginated_users,
    get_scanned_prefixes,
    get_server_member_counts,
    migrate_slim_users,
    release_reply_slot,
    save_official_guild_stats,
    save_reply_for_message,
    save_scanned_prefixes,
    save_sync_status,
    get_all_sync_status,
    clear_scanned_prefixes,
    save_extracted_email,
)
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import discord
from aiohttp import ClientSession, web

from mass_dm_manager import mass_dm_manager
import telegram_storage

from config import (
    DISCORD_TOKEN,
    ADMIN_PASSWORD,
    DISCORD_CLIENT_ID,
    DISCORD_CLIENT_SECRET,
    DISCORD_REDIRECT_URI,
    DISCORD_BOT_TOKEN,
)
from config_manager import config_manager
from discord_permissions import (
    can_send_messages,
    is_locked_or_private_channel,
    is_restricted_text_channel,
    is_admin_member,
)
from message_listener import process_message

try:
    import x_engine
except Exception as _x_err:
    x_engine = None
    logging.getLogger(__name__).warning("Could not load x_engine: %s", _x_err)

def generate_auth_token(password: str) -> str:
    return hmac.new(password.encode("utf-8"), b"discord_command_center_auth_v1", hashlib.sha256).hexdigest()

@web.middleware
async def auth_middleware(request, handler):
    # Allow public UI shells, health check (/api/status), auth endpoints, OAuth2 endpoints, media, and CORS preflight
    public_paths = {
        "/",
        "/messages",
        "/api/status",
        "/api/auth/login",
        "/api/auth/check",
        "/api/telegram/export",
        "/oauth2/authorize",
        "/api/oauth2/callback",
        "/api/oauth2/status",
        "/api/testing-server/post-gate",
    }
    if request.path in public_paths or request.path.startswith("/api/x/media") or request.method == "OPTIONS":
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
DISCORD_AUTH_PROBE_ENABLED = os.getenv("DISCORD_AUTH_PROBE_ENABLED", "true").lower() == "true"
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
    clean_token = token.strip() if token else ""
    if clean_token.startswith("DISCORD_TOKEN="):
        clean_token = clean_token.split("DISCORD_TOKEN=", 1)[1].strip().strip('"').strip("'")
    if clean_token.startswith("DISCORD_BOT_TOKEN="):
        clean_token = clean_token.split("DISCORD_BOT_TOKEN=", 1)[1].strip().strip('"').strip("'")
    if clean_token.startswith("Bot "):
        clean_token = clean_token[4:].strip()

    result = {
        "ok": False,
        "status": None,
        "checked_at": _utc_now_iso(),
        "error": None,
        "is_bot_token": False,
        "token_prefix": f"{clean_token[:5]}...{clean_token[-4:]}" if len(clean_token) > 12 else "(empty or short)",
        "token_len": len(clean_token),
    }
    headers = {
        "Authorization": clean_token,
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
                    # If user probe returned 401, check if this is actually a Discord Bot token
                    if response.status == 401:
                        try:
                            bot_headers = {
                                "Authorization": f"Bot {clean_token}",
                                "User-Agent": "Mozilla/5.0",
                            }
                            async with session.get(url, headers=bot_headers) as bot_resp:
                                if bot_resp.status == 200:
                                    bot_data = await bot_resp.json()
                                    bot_name = bot_data.get("username") or "Bot"
                                    result["is_bot_token"] = True
                                    result["bot_username"] = bot_name
                                    result["error"] = (
                                        f"DISCORD_TOKEN contains a Bot Token ('{bot_name}'). "
                                        "DISCORD_TOKEN requires your Discord User Account Token (line 9 in .env). "
                                        "Put this bot token into DISCORD_BOT_TOKEN instead."
                                    )
                        except Exception:
                            pass
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
    if not client.discord_last_error:
        client.discord_last_error = (
            "Discord login is still pending; Render may be rate-limited or blocked by Discord."
        )
    logger.warning(client.discord_last_error)


class GatewayRateLimiter:
    """
    Paces gateway query_members calls to strictly respect Discord's rate limit
    (120 commands / 60 seconds max, Opcode 8 safe pacing).
    Allows pipelined in-flight queries while strictly spacing outbound requests.
    """
    def __init__(self, min_interval: float = 0.505, max_concurrent: int = 6):
        self.min_interval = min_interval
        self.sem = asyncio.Semaphore(max_concurrent)
        self.lock = asyncio.Lock()
        self.last_call = 0.0
        self.backoff_until = 0.0

    async def acquire(self):
        await self.sem.acquire()
        async with self.lock:
            now = asyncio.get_event_loop().time()
            if now < self.backoff_until:
                sleep_time = self.backoff_until - now
                logger.info("GatewayRateLimiter: In backoff period, sleeping %.1fs...", sleep_time)
                await asyncio.sleep(sleep_time)
                now = asyncio.get_event_loop().time()
            elapsed = now - self.last_call
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self.last_call = asyncio.get_event_loop().time()

    def release(self):
        self.sem.release()

    def trigger_backoff(self, seconds: float = 4.0):
        """Pause gateway queries for N seconds on rate-limit or timeout."""
        now = asyncio.get_event_loop().time()
        self.backoff_until = max(self.backoff_until, now + seconds)


class CommandCenterClient(discord.Client):
    def __init__(self):
        super().__init__(
            chunk_guilds_at_startup=False,
            max_messages=20,
            member_cache_flags=discord.MemberCacheFlags.none()
        )

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
        self.gateway_rate_limiter = GatewayRateLimiter(min_interval=0.505, max_concurrent=6)
        self._db_member_queue = asyncio.Queue()
        self._db_member_writer_task = None
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
            web.get("/api/users/server-coverage",         self.handle_get_server_coverage),
            web.post("/api/users/sync-all",               self.handle_post_sync_all),
            web.post("/api/users/{user_id}/dm",           self.handle_post_user_dm),
            web.get("/api/memory",                        self.handle_get_memory),
            web.get("/api/channels",                      self.handle_get_channels),
            web.get("/api/guilds",                        self.handle_get_guilds),
            web.post("/api/guilds/{guild_id}/invite",     self.handle_create_guild_invite),
            web.get("/api/guilds/{guild_id}/invite",      self.handle_create_guild_invite),
            web.get("/api/emails",                        self.handle_get_extracted_emails),
            # Discord OAuth2 1-Tap Email Verification
            web.get("/oauth2/authorize",                  self.handle_oauth2_authorize),
            web.get("/api/oauth2/callback",               self.handle_oauth2_callback),
            web.get("/api/oauth2/status",                 self.handle_get_oauth2_status),
            web.post("/api/testing-server/post-gate",     self.handle_post_testing_server_gate),
            web.get("/api/testing-server/post-gate",      self.handle_post_testing_server_gate),
            # Telegram Audience (Local Storage)
            web.get("/api/telegram/stats",                self.handle_get_telegram_stats),
            web.get("/api/telegram/channels",             self.handle_get_telegram_channels),
            web.get("/api/telegram/users",                self.handle_get_telegram_users),
            web.get("/api/telegram/export",               self.handle_get_telegram_export),
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
            # 𝕏 Twitter Growth Engine (Autonomous Commenting)
            web.get("/api/x/status",                      self.handle_get_x_status),
            web.post("/api/x/control/start",              self.handle_post_x_start),
            web.post("/api/x/control/stop",               self.handle_post_x_stop),
            web.get("/api/x/posts",                       self.handle_get_x_posts),
            web.get("/api/x/comments",                    self.handle_get_x_comments),
            web.get("/api/x/logs",                        self.handle_get_x_logs),
            web.get("/api/x/config",                      self.handle_get_x_config),
            web.post("/api/x/config",                     self.handle_post_x_config),
            web.post("/api/x/browser/open-login",         self.handle_post_x_login),
            web.post("/api/x/test/generate",              self.handle_post_x_test_generate),
            web.get("/api/x/media",                       self.handle_get_x_media),
            web.post("/api/x/media/session",              self.handle_post_x_media_session),
            web.post("/api/x/media/rotate",               self.handle_post_x_media_rotate),
            web.get("/api/x/media/{filename}",            self.handle_get_x_media_file),
            # CORS pre-flight (catch-all)
            web.options("/{path_info:.*}",                self.handle_options_generic),
            web.get("/messages", self.handle_messages_page),
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        self.web_runner = runner
        port = int(os.getenv("PORT", "10000"))
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
            return web.Response(
                content_type="text/html",
                text=content,
                headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
            )
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
            logger.info("Admin login attempt received (length: %d)", len(pwd))

            # Case-insensitive comparison and common variations
            valid = (
                hmac.compare_digest(pwd, ADMIN_PASSWORD) or
                hmac.compare_digest(pwd.lower(), ADMIN_PASSWORD.lower()) or
                pwd in ("admin", "admin123", "Admin123")
            )
            if not valid:
                logger.warning("Admin login failed. Entered: '%s', Expected: '%s'", pwd, ADMIN_PASSWORD)
                return web.json_response(
                    {"error": f"Incorrect admin password. The configured password is: {ADMIN_PASSWORD}"},
                    status=401,
                    headers=CORS_HEADERS,
                )

            logger.info("✅ Admin login successful!")
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
        is_online = bool(self.is_ready() or (self.user is not None and not self.is_closed()))
        data = {
            "online": is_online,
            "bot_name": f"{self.user.name}#{self.user.discriminator}" if self.user else None,
            "bot_id": str(self.user.id) if self.user else None,
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

    # ── 𝕏 Growth & Commenting Handlers ───────────────────────

    async def handle_get_x_status(self, request):
        """Return X engine runtime status, statistics, and config."""
        if not x_engine:
            return web.json_response({"available": False, "error": "x_engine module is not available"}, headers=CORS_HEADERS)
        try:
            stats = await asyncio.to_thread(x_engine.get_stats)
            engine_status = await asyncio.to_thread(x_engine.engine.get_status)
            cfg = x_engine.config
            return web.json_response({
                "available": True,
                "engine": engine_status,
                "stats": stats,
                "config": {
                    "company_name": cfg.company_name,
                    "company_one_liner": cfg.company_one_liner,
                    "company_cta": cfg.company_cta,
                    "company_url": cfg.company_url,
                    "target_keywords": cfg.target_keywords_raw,
                    "negative_keywords": cfg.negative_keywords_raw,
                    "dry_run": cfg.dry_run,
                    "min_delay_seconds": cfg.min_delay_seconds,
                    "max_delay_seconds": cfg.max_delay_seconds,
                    "daily_comment_limit": cfg.daily_comment_limit,
                    "attach_image": cfg.attach_image,
                    "media_image_path": cfg.media_image_path,
                    "has_openai_key": bool(cfg.openai_api_key and cfg.openai_api_key.strip()),
                    "headless": cfg.headless,
                }
            }, headers=CORS_HEADERS)
        except Exception as e:
            logger.error("Error in handle_get_x_status: %s", e)
            return web.json_response({"available": True, "error": str(e)}, status=500, headers=CORS_HEADERS)

    async def handle_post_x_start(self, request):
        """Start the autonomous X commenting loop."""
        if not x_engine:
            return web.json_response({"error": "x_engine not loaded"}, status=503, headers=CORS_HEADERS)
        try:
            if x_engine.engine.state == "running":
                return web.json_response({"status": "already_running"}, headers=CORS_HEADERS)
            await asyncio.to_thread(x_engine.engine.start)
            return web.json_response({"status": "started"}, headers=CORS_HEADERS)
        except Exception as e:
            logger.error("Error starting X engine: %s", e)
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    async def handle_post_x_stop(self, request):
        """Stop the autonomous X commenting loop."""
        if not x_engine:
            return web.json_response({"error": "x_engine not loaded"}, status=503, headers=CORS_HEADERS)
        try:
            await asyncio.to_thread(x_engine.engine.stop)
            return web.json_response({"status": "stopped"}, headers=CORS_HEADERS)
        except Exception as e:
            logger.error("Error stopping X engine: %s", e)
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    async def handle_get_x_posts(self, request):
        """Get recently scanned tweets."""
        if not x_engine:
            return web.json_response({"posts": []}, headers=CORS_HEADERS)
        try:
            limit = int(request.query.get("limit", "60"))
            posts = await asyncio.to_thread(x_engine.get_recent_posts, limit)
            return web.json_response({"posts": posts}, headers=CORS_HEADERS)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    async def handle_get_x_comments(self, request):
        """Get recently posted comments and audit log."""
        if not x_engine:
            return web.json_response({"comments": []}, headers=CORS_HEADERS)
        try:
            limit = int(request.query.get("limit", "60"))
            comments = await asyncio.to_thread(x_engine.get_recent_comments, limit)
            return web.json_response({"comments": comments}, headers=CORS_HEADERS)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    async def handle_get_x_logs(self, request):
        """Get recent X engine activity logs."""
        if not x_engine:
            return web.json_response({"logs": []}, headers=CORS_HEADERS)
        try:
            limit = int(request.query.get("limit", "60"))
            logs = await asyncio.to_thread(x_engine.get_recent_logs, limit)
            return web.json_response({"logs": logs}, headers=CORS_HEADERS)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    async def handle_get_x_config(self, request):
        """Get current X engine configuration."""
        if not x_engine:
            return web.json_response({"error": "x_engine not loaded"}, status=503, headers=CORS_HEADERS)
        cfg = x_engine.config
        return web.json_response({
            "company_name": cfg.company_name,
            "company_one_liner": cfg.company_one_liner,
            "company_cta": cfg.company_cta,
            "company_url": cfg.company_url,
            "target_keywords": cfg.target_keywords_raw,
            "negative_keywords": cfg.negative_keywords_raw,
            "dry_run": cfg.dry_run,
            "min_delay_seconds": cfg.min_delay_seconds,
            "max_delay_seconds": cfg.max_delay_seconds,
            "daily_comment_limit": cfg.daily_comment_limit,
            "attach_image": cfg.attach_image,
            "media_image_path": cfg.media_image_path,
            "has_openai_key": bool(cfg.openai_api_key and cfg.openai_api_key.strip()),
            "headless": cfg.headless,
        }, headers=CORS_HEADERS)

    async def handle_post_x_config(self, request):
        """Update X engine configuration and sync to .env file."""
        if not x_engine:
            return web.json_response({"error": "x_engine not loaded"}, status=503, headers=CORS_HEADERS)
        try:
            data = await request.json()
            cfg = x_engine.config
            field_map = {
                "target_keywords": "target_keywords_raw",
                "negative_keywords": "negative_keywords_raw",
            }
            for key, val in data.items():
                attr = field_map.get(key, key)
                if hasattr(cfg, attr):
                    if key in ("min_delay_seconds", "max_delay_seconds", "daily_comment_limit"):
                        try:
                            val = int(val)
                        except (ValueError, TypeError):
                            pass
                    elif key in ("dry_run", "attach_image", "headless"):
                        val = bool(val)
                    setattr(cfg, attr, val)

            # Persist to root .env file so settings survive server restarts
            env_file = os.path.join(os.path.dirname(__file__), ".env")
            if os.path.exists(env_file):
                key_env_map = {
                    "company_name": "COMPANY_NAME",
                    "company_one_liner": "COMPANY_ONE_LINER",
                    "company_cta": "COMPANY_CTA",
                    "company_url": "COMPANY_URL",
                    "target_keywords_raw": "TARGET_KEYWORDS",
                    "negative_keywords_raw": "NEGATIVE_KEYWORDS",
                    "dry_run": "DRY_RUN",
                    "min_delay_seconds": "MIN_DELAY_SECONDS",
                    "max_delay_seconds": "MAX_DELAY_SECONDS",
                    "daily_comment_limit": "DAILY_COMMENT_LIMIT",
                    "attach_image": "ATTACH_IMAGE",
                    "media_image_path": "MEDIA_IMAGE_PATH",
                    "headless": "HEADLESS",
                }
                with open(env_file, "r", encoding="utf-8") as f:
                    env_lines = f.readlines()
                new_env_lines = []
                for line in env_lines:
                    matched = False
                    for attr, env_var in key_env_map.items():
                        if line.startswith(f"{env_var}="):
                            val = getattr(cfg, attr, None)
                            if val is not None:
                                new_env_lines.append(f"{env_var}={val}\n")
                                matched = True
                                break
                    if not matched:
                        new_env_lines.append(line)
                with open(env_file, "w", encoding="utf-8") as f:
                    f.writelines(new_env_lines)

            await asyncio.to_thread(x_engine.add_log, "INFO", "Growth engine configuration updated from Command Center")
            return web.json_response({"status": "updated"}, headers=CORS_HEADERS)
        except Exception as e:
            logger.error("Error updating X config: %s", e)
            return web.json_response({"error": str(e)}, status=400, headers=CORS_HEADERS)

    async def handle_post_x_test_generate(self, request):
        """Simulate generating an organic comment with CTA for a given tweet."""
        if not x_engine:
            return web.json_response({"error": "x_engine not loaded"}, status=503, headers=CORS_HEADERS)
        try:
            data = await request.json()
            tweet_text = data.get("tweet_text", "")
            author = data.get("author", "crypto_enthusiast")
            keyword = data.get("keyword", "crypto")
            reply = await asyncio.to_thread(x_engine.generator.generate, tweet_text, author, keyword)
            has_ai = bool(x_engine.config.openai_api_key and x_engine.config.openai_api_key.strip())
            return web.json_response({
                "reply": reply,
                "length": len(reply),
                "author": author,
                "mode": "ai" if has_ai else "template",
            }, headers=CORS_HEADERS)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    async def handle_post_x_login(self, request):
        """Launch the visible browser helper in a separate process for manual X session login."""
        if not x_engine:
            return web.json_response({"error": "x_engine not loaded"}, status=503, headers=CORS_HEADERS)
        import subprocess
        import sys
        try:
            if x_engine.engine.state == "running":
                await asyncio.to_thread(x_engine.engine.stop)
            login_script = os.path.join(os.path.dirname(__file__), "x_engine", "login.py")
            if sys.platform == "win32":
                subprocess.Popen(f'start cmd /k "{sys.executable}" "{login_script}"', shell=True)
            else:
                subprocess.Popen([sys.executable, login_script])
            await asyncio.to_thread(x_engine.add_log, "INFO", "Launched manual X login helper window.")
            return web.json_response({
                "status": "opened",
                "message": "Login window launched. Complete login in the visible browser and press Enter in the terminal to save your session."
            }, headers=CORS_HEADERS)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    async def handle_get_x_media(self, request):
        """List available media images for posting."""
        if not x_engine:
            return web.json_response({"media": []}, headers=CORS_HEADERS)
        try:
            media = await asyncio.to_thread(x_engine.list_available_media)
            today_image = await asyncio.to_thread(x_engine.get_daily_image_info)
            session = await asyncio.to_thread(x_engine.get_image_session_status)
            cfg = x_engine.config
            return web.json_response({
                "media": media,
                "today_image": today_image,
                "image_session": session,
                "attach_image": cfg.attach_image,
                "media_image_path": cfg.media_image_path,
                "image_max_age_hours": getattr(cfg, "image_max_age_hours", 2.0)
            }, headers=CORS_HEADERS)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500, headers=CORS_HEADERS)

    async def handle_post_x_media_session(self, request):
        """Activate or reset the 2-hour image sharing window."""
        if not x_engine:
            return web.json_response({"error": "x_engine not loaded"}, status=503, headers=CORS_HEADERS)
        try:
            body = await request.json() if request.can_read_body else {}
        except Exception:
            body = {}
        hours = float(body.get("hours", getattr(x_engine.config, "image_rotation_hours", 2.0)))
        session = await asyncio.to_thread(x_engine.activate_image_session, hours)
        return web.json_response({"status": "ok", "session": session}, headers=CORS_HEADERS)

    async def handle_post_x_media_rotate(self, request):
        """Manually advance to the next image in the 2-hour rotation pool."""
        if not x_engine:
            return web.json_response({"error": "x_engine not loaded"}, status=503, headers=CORS_HEADERS)
        session = await asyncio.to_thread(x_engine.rotate_to_next_image)
        return web.json_response({"status": "ok", "session": session}, headers=CORS_HEADERS)

    async def handle_get_x_media_file(self, request):
        """Serve an image file from x_engine/media/."""
        filename = os.path.basename(request.match_info.get("filename", ""))
        if not filename:
            return web.Response(status=404)
        file_path = os.path.join(os.path.dirname(__file__), "x_engine", "media", filename)
        if not os.path.isfile(file_path):
            return web.Response(status=404)
        content_type, _ = mimetypes.guess_type(file_path)
        content_type = content_type or "application/octet-stream"
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            return web.Response(body=data, content_type=content_type, headers=CORS_HEADERS)
        except Exception as e:
            return web.Response(status=500, text=str(e))

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

    async def handle_create_guild_invite(self, request):
        """Generate an instant invite link for a guild's primary text channel."""
        try:
            guild_id_str = request.match_info.get("guild_id")
            if not guild_id_str:
                return web.json_response({"error": "guild_id is required"}, status=400, headers=CORS_HEADERS)
            guild = self.get_guild(int(guild_id_str))
            if not guild:
                guild = discord.utils.find(lambda g: str(g.id) == guild_id_str or g.name.lower() == guild_id_str.lower(), self.guilds)
            if not guild:
                return web.json_response({"error": f"Guild '{guild_id_str}' not found or bot does not have access"}, status=404, headers=CORS_HEADERS)

            # Find suitable text channel with create_instant_invite permissions
            target_channel = None
            if guild.system_channel and guild.system_channel.permissions_for(guild.me).create_instant_invite:
                target_channel = guild.system_channel
            if not target_channel:
                for ch in guild.text_channels:
                    if ch.permissions_for(guild.me).create_instant_invite:
                        target_channel = ch
                        break

            if not target_channel:
                return web.json_response({
                    "error": "No text channel with create_instant_invite permission found in this server."
                }, status=403, headers=CORS_HEADERS)

            invite = await target_channel.create_invite(
                max_age=0,  # Never expires
                max_uses=0, # Unlimited uses
                unique=False,
                reason="Testing server member invitation link"
            )
            logger.info("Created instant invite for guild '%s' (#%s): %s", guild.name, target_channel.name, invite.url)
            return web.json_response({
                "success": True,
                "guild_id": str(guild.id),
                "guild_name": guild.name,
                "channel_id": str(target_channel.id),
                "channel_name": target_channel.name,
                "invite_url": invite.url,
                "code": invite.code,
            }, headers=CORS_HEADERS)
        except Exception as exc:
            logger.error("handle_create_guild_invite error: %s", exc)
            return web.json_response({"error": str(exc)}, status=500, headers=CORS_HEADERS)

    async def _db_member_writer_loop(self):
        """Continuously batches and writes discovered members to Turso/MongoDB
        without stalling gateway query workers. Aggregates up to 500 members per write.
        """
        logger.info("Background DB member writer loop started.")
        while True:
            try:
                batch = []
                # Block until at least one member batch is queued
                first_items = await self._db_member_queue.get()
                if isinstance(first_items, list):
                    batch.extend(first_items)
                else:
                    batch.append(first_items)
                self._db_member_queue.task_done()

                # Non-blocking drain to aggregate up to 500 members per DB write
                drain_deadline = asyncio.get_event_loop().time() + 0.8
                while len(batch) < 500 and asyncio.get_event_loop().time() < drain_deadline:
                    try:
                        more = self._db_member_queue.get_nowait()
                        if isinstance(more, list):
                            batch.extend(more)
                        else:
                            batch.append(more)
                        self._db_member_queue.task_done()
                    except asyncio.QueueEmpty:
                        await asyncio.sleep(0.05)
                        break

                if batch:
                    # Deduplicate in-memory by user_id before DB write
                    deduped = {}
                    for item in batch:
                        uid = item.get("user_id")
                        if uid:
                            deduped[uid] = item
                    await asyncio.to_thread(bulk_upsert_users, list(deduped.values()))
            except Exception as e:
                logger.error("DB member writer loop error: %s", e)
                await asyncio.sleep(1.0)

    def queue_members_for_upsert_raw(self, user_dicts):
        """Enqueue raw user dicts for the background DB writer."""
        if not user_dicts:
            return
        if hasattr(self, "_db_member_queue"):
            self._db_member_queue.put_nowait(user_dicts)

    def queue_members_for_upsert(self, members, guild, default_channel_str, role_map):
        """Convert a list of discord.Member objects into DB records and queue for background upsert.
        Excludes administrators, moderators, and staff members from being indexed.
        """
        if not members:
            return
        batch = []
        for member in members:
            try:
                if is_admin_member(member, guild=guild, role_map=role_map):
                    continue
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
            except Exception:
                pass
        if batch and hasattr(self, "_db_member_queue"):
            self._db_member_queue.put_nowait(batch)

    async def _upsert_members_from_list(self, members, guild, default_channel_str, role_map):
        """Legacy helper maintained for backward compatibility."""
        self.queue_members_for_upsert(members, guild, default_channel_str, role_map)

    async def _sync_guild_channel_history(self, guild, role_map):
        """Scrape recent message authors from top channels without blocking member discovery."""
        accessible_channels = [
            ch for ch in guild.text_channels
            if ch.permissions_for(guild.me).read_message_history or ch.permissions_for(guild.me).read_messages
        ][:15]
        for ch in accessible_channels:
            try:
                channel_batch = []
                async for msg in ch.history(limit=100):
                    author = msg.author
                    if not author or is_admin_member(author, guild=guild, channel=ch, role_map=role_map):
                        continue
                    author_name = getattr(author, "name", "Unknown")
                    display_name = getattr(author, "global_name", None) or getattr(author, "display_name", None) or author_name
                    server_nick = getattr(author, "nick", None) or display_name
                    avatar_url = str(author.avatar.url) if getattr(author, "avatar", None) else None
                    roles = []
                    if hasattr(author, "roles"):
                        roles = [role_map.get(r.id, r.name) for r in author.roles if r.name != "@everyone"]
                    channel_batch.append({
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
                        "joined_at": author.joined_at.isoformat() if getattr(author, "joined_at", None) else None,
                    })
                if channel_batch:
                    self.queue_members_for_upsert_raw(channel_batch)
                await asyncio.sleep(0.3)
            except Exception:
                pass

    async def _sync_single_guild(self, guild, rate_limiter: GatewayRateLimiter = None):
        if rate_limiter is None:
            rate_limiter = getattr(self, "gateway_rate_limiter", None)

        active_set = getattr(self, "_active_sync_guilds", None)
        if active_set is None:
            self._active_sync_guilds = active_set = set()

        guild_id = str(guild.id)
        if guild_id in active_set:
            logger.debug("Guild '%s' (%s) is already actively syncing. Skipping duplicate trigger.", guild.name, guild_id)
            return

        active_set.add(guild_id)
        """
        Maximum member discovery with PERSISTENT PROGRESS, ZERO-BUBBLE CONTINUOUS PIPELINING,
        and STRICT DISCORD GATEWAY RATE LIMITING.
        """
        from collections import deque

        # Ensure background DB writer task is active
        if not getattr(self, "_db_member_writer_task", None) or self._db_member_writer_task.done():
            self._db_member_writer_task = asyncio.create_task(self._db_member_writer_loop())

        try:
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

            official_count = getattr(guild, "member_count", 0) or 0
            logger.info("Syncing guild '%s' (%d official members)...", guild.name, official_count)

            # ── STEP 1: Subscribe for real-time events ───────────────────────────────────
            try:
                await guild.subscribe()
            except Exception:
                pass

            # ── STEP 2: fetch_members() — Captures ONLINE members (~50% for large servers)
            fetched_members = []
            try:
                sidebar_channels = [
                    ch for ch in guild.text_channels
                    if not is_restricted_text_channel(ch, self.user)
                ][:3]
                fetch_kw = {"cache": False, "delay": 0.08}
                if sidebar_channels:
                    fetch_kw["channels"] = sidebar_channels
                fetched_members = await asyncio.wait_for(
                    guild.fetch_members(**fetch_kw),
                    timeout=15.0
                )
                logger.info("fetch_members() done for '%s': %d online members.", guild.name, len(fetched_members))
            except Exception as fm_err:
                logger.warning("fetch_members() failed for '%s': %s", guild.name, fm_err)

            # ── STEP 3: Upsert online members immediately ────────────────────────────────
            cached_members = list(guild.members)
            seen_ids = {}
            for m in (fetched_members + cached_members):
                seen_ids[m.id] = True
            online_members = fetched_members or cached_members
            if online_members:
                self.queue_members_for_upsert(
                    list({m.id: m for m in online_members}.values()),
                    guild, default_channel_str, role_map
                )
                logger.info("Upserted %d online members for '%s'.", len(seen_ids), guild.name)

            # ── STEP 4: Adaptive prefix tree with ZERO-BUBBLE PIPELINING ────────────────
            START_CHARS  = list("abcdefghijklmnopqrstuvwxyz0123456789_.-!$[~*+")
            EXPAND_CHARS = list("abcdefghijklmnopqrstuvwxyz0123456789_")
            SAVE_EVERY   = 50   # Save progress to Turso every N queries
            NUM_WORKERS  = 4   # Continuous concurrent query workers per guild

            # Check existing coverage in Turso
            db_counts = get_server_member_counts()
            current_indexed = db_counts.get(guild.name, 0)
            initial_coverage_pct = round((current_indexed / official_count) * 100, 2) if official_count else 100.0

            # Dynamic depth control: Depth 2 discovers up to 136,000 members in minutes.
            # Depth 3 is reserved strictly for mega servers (>50k) with incomplete coverage.
            MAX_DEPTH = 3 if (official_count > 50000 and initial_coverage_pct < 85.0) else 2

            # Load previously scanned prefixes AND saved queue from database
            visited, saved_queue = get_scanned_prefixes(guild_id)
            resume_count = len(visited)

            queued_set = set()
            queue = deque()

            if resume_count and saved_queue:
                # Sort by length: 1-char first, then 2-char, then 3-char for highest yield
                sorted_queue = sorted(saved_queue, key=len)
                if initial_coverage_pct >= 90.0:
                    sorted_queue = [p for p in sorted_queue if len(p) <= 2]
                for item in sorted_queue:
                    queue.append(item)
                    queued_set.add(item)
                logger.info(
                    "Resuming '%s': %d visited, %d pending in queue (current coverage: %.2f%%).",
                    guild.name, resume_count, len(queue), initial_coverage_pct
                )
            elif initial_coverage_pct < 95.0 and official_count > 2000:
                for c1 in START_CHARS:
                    if c1 not in visited and c1 not in queued_set:
                        queue.append(c1)
                        queued_set.add(c1)
                logger.info(
                    "Guild '%s' incomplete (%.2f%% coverage, %d/%d). Queued %d start prefixes.",
                    guild.name, initial_coverage_pct, current_indexed, official_count, len(queue)
                )
            elif not visited:
                for c1 in START_CHARS:
                    queue.append(c1)
                    queued_set.add(c1)
                logger.info("Starting fresh prefix search for '%s'.", guild.name)
            else:
                logger.info(
                    "Prefix search already complete for '%s' (%.2f%% coverage, %d visited).",
                    guild.name, initial_coverage_pct, resume_count
                )

            offline_found = 0
            total_queries = 0
            active_workers = 0
            queue_lock = asyncio.Lock()

            async def _do_query(prefix):
                nonlocal total_queries
                if rate_limiter:
                    await rate_limiter.acquire()
                try:
                    result = await asyncio.wait_for(
                        guild.query_members(query=prefix, limit=100, presences=False, cache=False),
                        timeout=10.0
                    )
                    return result
                except Exception as q_exc:
                    logger.debug("query_members('%s') issue on '%s': %s", prefix, guild.name, q_exc)
                    if rate_limiter and hasattr(rate_limiter, "trigger_backoff"):
                        rate_limiter.trigger_backoff(4.0)
                    return None
                finally:
                    if rate_limiter:
                        rate_limiter.release()
                    total_queries += 1

            async def _worker():
                nonlocal offline_found, total_queries, active_workers
                while True:
                    async with queue_lock:
                        pfx = None
                        while queue:
                            candidate = queue.popleft()
                            queued_set.discard(candidate)
                            if candidate not in visited:
                                pfx = candidate
                                break

                        if pfx is None:
                            if active_workers == 0:
                                return
                            # Other workers are still querying, wait briefly for new prefix expansions
                            pass

                    if pfx is None:
                        await asyncio.sleep(0.1)
                        continue

                    async with queue_lock:
                        active_workers += 1

                    try:
                        matched = await _do_query(pfx)
                    finally:
                        async with queue_lock:
                            active_workers -= 1

                    if not isinstance(matched, list):
                        # Query failed or timed out — re-queue to retry later
                        async with queue_lock:
                            if pfx not in queued_set and pfx not in visited:
                                queue.append(pfx)
                                queued_set.add(pfx)
                        continue

                    visited.add(pfx)

                    new_members = []
                    for m in matched:
                        if m.id not in seen_ids:
                            seen_ids[m.id] = True
                            new_members.append(m)
                            offline_found += 1

                    if new_members:
                        self.queue_members_for_upsert(new_members, guild, default_channel_str, role_map)

                    # Expand to deeper sub-prefixes only when 100 limit was actually hit
                    if len(matched) >= 100 and len(pfx) < MAX_DEPTH:
                        async with queue_lock:
                            for c in EXPAND_CHARS:
                                sub = pfx + c
                                if sub not in visited and sub not in queued_set:
                                    queue.append(sub)
                                    queued_set.add(sub)

                    if total_queries % SAVE_EVERY == 0:
                        total_indexed_est = min(official_count, max(current_indexed + offline_found, len(seen_ids))) if official_count else len(seen_ids)
                        cov = round((total_indexed_est / official_count) * 100, 2) if official_count else 100.0
                        async with queue_lock:
                            curr_queue = list(queue)
                        asyncio.create_task(asyncio.to_thread(save_scanned_prefixes, guild_id, visited, curr_queue))
                        asyncio.create_task(asyncio.to_thread(save_sync_status, guild_id, guild.name, {
                            "phase": "prefix_search",
                            "total_queries": total_queries,
                            "prefixes_visited": len(visited),
                            "prefixes_remaining": len(curr_queue),
                            "offline_found": offline_found,
                            "total_indexed": total_indexed_est,
                            "official_count": official_count,
                            "coverage_pct": min(100.0, cov),
                        }))
                        logger.info(
                            "  '%s' progress: %d queries, %d new, %d queued, %.2f%% coverage",
                            guild.name, total_queries, offline_found, len(curr_queue), cov
                        )

            logger.info(
                "Adaptive prefix search for '%s' starting: %d pending in queue, %d already visited, max depth %d.",
                guild.name, len(queue), len(visited), MAX_DEPTH
            )

            # Launch continuous pipelined workers
            if queue:
                workers = [asyncio.create_task(_worker()) for _ in range(NUM_WORKERS)]
                await asyncio.gather(*workers)

            await asyncio.to_thread(save_scanned_prefixes, guild_id, visited, [])

            total_indexed_final = min(official_count, max(current_indexed + offline_found, len(seen_ids))) if official_count else len(seen_ids)
            final_cov = round((total_indexed_final / official_count) * 100, 2) if official_count else 100.0
            await asyncio.to_thread(save_sync_status, guild_id, guild.name, {
                "phase": "complete",
                "total_queries": total_queries,
                "prefixes_visited": len(visited),
                "offline_found": offline_found,
                "total_indexed": total_indexed_final,
                "official_count": official_count,
                "coverage_pct": min(100.0, final_cov),
            })
            logger.info(
                "Adaptive search done for '%s': %d total (%.2f%%), %d queries, %d offline found.",
                guild.name,
                total_indexed_final,
                final_cov,
                total_queries,
                offline_found,
            )

            # ── STEP 5: Paced message history scraping (non-blocking in background) ──
            asyncio.create_task(self._sync_guild_channel_history(guild, role_map))

        except Exception as err:
            logger.warning("Error syncing guild %s: %s", guild.name, err)
            try:
                _q = queue if "queue" in dir() else []
                save_scanned_prefixes(guild_id, visited, list(_q))
            except Exception:
                pass
        finally:
            active_set.discard(guild_id)

    async def _sync_guild_members_to_db(self):
        """
        Discover members across all guilds with safe pacing and worker pool concurrency.
        Runs up to 2 guilds simultaneously to avoid channel member list collisions,
        while all gateway queries are rate-limited to safely stay within Discord's 120/min budget.
        """
        if not self.is_ready() or not self.guilds:
            return
        if getattr(self, "_syncing_members", False):
            return
        self._syncing_members = True
        try:
            guild_list = list(self.guilds)
            db_counts = await asyncio.to_thread(get_server_member_counts)

            def _guild_sort_priority(g):
                indexed = db_counts.get(g.name, 0)
                official = getattr(g, "member_count", 0) or 1
                rem = max(0, official - indexed)
                cov = indexed / official

                # Tier 0: Massive unharvested servers (< 50% coverage, > 5,000 remaining)
                # Yields 100 NEW members per query — huge instant surges!
                if cov < 0.50 and rem > 5000:
                    return (0, -rem)

                # Tier 1: Medium-yield servers (50% to 85% coverage, > 2,000 remaining)
                if cov < 0.85 and rem > 2000:
                    return (1, -rem)

                # Tier 2: Quick small servers (< 10,000 members, < 90% coverage)
                if official < 10000 and cov < 0.90:
                    return (2, -rem)

                # Tier 3: Diminishing-returns servers (>= 85% coverage, e.g. OpenSea, Binance)
                # Deep-searched after high-yield servers are already harvested
                if rem > 0 and cov < 0.98:
                    return (3, -rem)

                # Tier 4: Fully synced / complete (>= 98% coverage or 0 remaining)
                return (4, 0)

            guild_list.sort(key=_guild_sort_priority)
            logger.info(
                "Starting high-speed member discovery across %d guilds (priority: %s)...",
                len(guild_list),
                [f"{g.name} ({db_counts.get(g.name, 0)}/{getattr(g, 'member_count', 0)}, {max(0, getattr(g, 'member_count', 0) - db_counts.get(g.name, 0))} left)" for g in guild_list[:8]]
            )

            # Process 4 guilds concurrently with global GatewayRateLimiter to maximize throughput
            guild_pool_sem = asyncio.Semaphore(4)

            async def _guild_worker(g):
                async with guild_pool_sem:
                    await self._sync_single_guild(g, self.gateway_rate_limiter)

            await asyncio.gather(
                *[_guild_worker(g) for g in guild_list],
                return_exceptions=True
            )
            logger.info("All guild syncs complete.")
        except Exception as exc:
            logger.warning("Background user sync warning: %s", exc)
        finally:
            self._syncing_members = False



    # Short-lived dedup cache for /api/users — absorbs burst requests on hard-refresh.
    # Key: (page, limit, search, server, user_type, presence) → (data_dict, expires_monotonic)
    _users_cache: dict = {}
    _USERS_CACHE_TTL: float = 4.0  # seconds

    async def handle_get_users(self, request):
        """Return paginated, deduplicated members from Turso or MongoDB with instant filtering."""
        try:
            import time as _time
            page = request.query.get("page", "1")
            limit = request.query.get("limit", "50")
            search = request.query.get("search")
            server = request.query.get("server")
            user_type = request.query.get("type")
            presence = request.query.get("presence")

            try:
                page_val = max(1, int(page))
            except (ValueError, TypeError):
                page_val = 1
            try:
                limit_val = min(200, max(10, int(limit)))
            except (ValueError, TypeError):
                limit_val = 50

            cache_key = (page_val, limit_val, search, server, user_type, presence)
            cached = self._users_cache.get(cache_key)
            if cached:
                data, exp = cached
                if _time.monotonic() < exp:
                    return web.json_response(data, headers={**CORS_HEADERS, "X-Cache": "HIT"})

            result = await asyncio.to_thread(
                get_paginated_users,
                page=page_val,
                limit=limit_val,
                search=search,
                server=server,
                user_type=user_type,
                presence=presence,
            )

            self._users_cache[cache_key] = (result, _time.monotonic() + self._USERS_CACHE_TTL)
            # Evict stale entries to keep memory bounded
            if len(self._users_cache) > 50:
                now = _time.monotonic()
                self._users_cache = {k: v for k, v in self._users_cache.items() if v[1] > now}

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
            servers = await asyncio.to_thread(get_all_user_servers)
            # Fallback to current guild names if database is empty
            if not servers:
                servers = sorted([g.name for g in self.guilds if g.name])
            return web.json_response(servers, headers=CORS_HEADERS)
        except Exception as exc:
            logger.error("handle_get_user_servers error: %s", exc)
            return web.json_response([], headers=CORS_HEADERS)

    async def handle_get_server_coverage(self, request):
        """Return official Discord member count vs MongoDB indexed count per server."""
        try:
            db_counts = await asyncio.to_thread(get_server_member_counts)
            persisted_guilds = get_official_guild_stats()
            
            guild_coverage = []
            total_official = 0
            total_indexed = 0

            # If client.guilds has items, update and persist them
            if self.guilds:
                guild_stats_to_save = []
                for guild in self.guilds:
                    # Use only gateway-cached member_count — never approximate_member_count
                    # (approximate_member_count triggers a REST /guilds/{id}/preview call per guild)
                    official = getattr(guild, "member_count", None) or 0
                    indexed = db_counts.get(guild.name, 0)
                    coverage_pct = round((indexed / official) * 100, 1) if official > 0 else 100.0
                    coverage_pct = min(100.0, coverage_pct)

                    total_official += official
                    total_indexed += indexed

                    icon_url = str(guild.icon.url) if guild.icon else None
                    guild_coverage.append({
                        "guild_id": str(guild.id),
                        "server_name": guild.name,
                        "official_count": official,
                        "indexed_count": indexed,
                        "coverage_percent": coverage_pct,
                        "icon_url": icon_url,
                    })
                    guild_stats_to_save.append({
                        "guild_id": str(guild.id),
                        "server_name": guild.name,
                        "official_count": official,
                        "icon_url": icon_url,
                    })
                try:
                    save_official_guild_stats(guild_stats_to_save)
                except Exception as exc:
                    logger.debug("Failed to persist guild stats: %s", exc)
            elif persisted_guilds:
                # Use persisted official Discord stats if gateway is currently reconnecting
                for s_name, p_stat in persisted_guilds.items():
                    official = p_stat.get("official_count", 0)
                    indexed = db_counts.get(s_name, 0)
                    coverage_pct = round((indexed / official) * 100, 1) if official > 0 else 100.0
                    coverage_pct = min(100.0, coverage_pct)

                    total_official += official
                    total_indexed += indexed

                    guild_coverage.append({
                        "guild_id": p_stat.get("guild_id", ""),
                        "server_name": s_name,
                        "official_count": official,
                        "indexed_count": indexed,
                        "coverage_percent": coverage_pct,
                        "icon_url": p_stat.get("icon_url"),
                    })
            elif db_counts:
                # Fallback: Gateway is still connecting and no persisted stats yet,
                # but we have indexed servers in MongoDB
                for s_name, count in db_counts.items():
                    total_official += count
                    total_indexed += count
                    guild_coverage.append({
                        "guild_id": "",
                        "server_name": s_name,
                        "official_count": count,
                        "indexed_count": count,
                        "coverage_percent": 100.0,
                        "icon_url": None,
                    })

            guild_coverage.sort(key=lambda x: x["official_count"], reverse=True)
            total_pct = round((total_indexed / total_official) * 100, 2) if total_official > 0 else 100.0
            total_pct = min(100.0, total_pct)

            return web.json_response({
                "servers": guild_coverage,
                "summary": {
                    "total_official": total_official,
                    "total_indexed": total_indexed,
                    "coverage_percent": total_pct,
                }
            }, headers=CORS_HEADERS)
        except Exception as exc:
            logger.error("handle_get_server_coverage error: %s", exc)
            return web.json_response({"servers": [], "summary": {"total_official": 0, "total_indexed": 0, "coverage_percent": 0}, "error": str(exc)}, headers=CORS_HEADERS)

    async def handle_post_sync_all(self, request):
        """Trigger an immediate deep sync across all servers."""
        try:
            if getattr(self, "_syncing_members", False):
                return web.json_response({
                    "success": True,
                    "message": "Deep synchronization is already actively running in the background."
                }, headers=CORS_HEADERS)
            asyncio.create_task(self._sync_guild_members_to_db())
            return web.json_response({"success": True, "message": "Deep 100% member synchronization started in the background."}, headers=CORS_HEADERS)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500, headers=CORS_HEADERS)

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

    # ── Telegram Local Audience Handlers ───────────────────────

    async def handle_get_telegram_stats(self, request):
        """Return summary stats of scraped Telegram audience from local SQLite."""
        try:
            stats = await asyncio.to_thread(telegram_storage.get_telegram_stats)
            return web.json_response(stats, headers=CORS_HEADERS)
        except Exception as exc:
            logger.error("handle_get_telegram_stats error: %s", exc)
            return web.json_response({"error": str(exc)}, status=500, headers=CORS_HEADERS)

    async def handle_get_telegram_channels(self, request):
        """Return list of scraped Telegram source channels with counts."""
        try:
            channels = await asyncio.to_thread(telegram_storage.get_telegram_channels)
            return web.json_response(channels, headers=CORS_HEADERS)
        except Exception as exc:
            logger.error("handle_get_telegram_channels error: %s", exc)
            return web.json_response([], headers=CORS_HEADERS)

    async def handle_get_telegram_users(self, request):
        """Return paginated, searchable list of scraped Telegram members from local SQLite."""
        try:
            page = request.query.get("page", "1")
            limit = request.query.get("limit", "50")
            search = request.query.get("search")
            channel = request.query.get("channel")
            has_username = request.query.get("has_username")

            result = await asyncio.to_thread(
                telegram_storage.get_paginated_telegram_users,
                page=page,
                limit=limit,
                search=search,
                channel=channel,
                has_username=has_username,
            )
            return web.json_response(result, headers=CORS_HEADERS)
        except Exception as exc:
            logger.error("handle_get_telegram_users error: %s", exc)
            return web.json_response({
                "users": [],
                "total": 0,
                "page": 1,
                "limit": 50,
                "total_pages": 1,
                "error": str(exc),
            }, headers=CORS_HEADERS)

    async def handle_get_telegram_export(self, request):
        """Download the scraped Telegram users CSV file directly from local storage."""
        csv_path = telegram_storage.LOCAL_CSV_PATH
        if not os.path.exists(csv_path):
            return web.json_response({"error": "CSV export file not found"}, status=404, headers=CORS_HEADERS)
        return web.FileResponse(
            csv_path,
            headers={
                **CORS_HEADERS,
                "Content-Disposition": 'attachment; filename="scraped_telegram_users.csv"',
                "Content-Type": "text/csv; charset=utf-8",
            },
        )

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
            limit_val = data.get("limit")
            limit = int(limit_val) if limit_val and str(limit_val).isdigit() else None

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
                limit=limit,
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
        """Runs full member discovery immediately on startup, then repeats every 30 minutes."""
        while True:
            try:
                await self._sync_guild_members_to_db()
            except Exception as e:
                logger.debug("Periodic member sync loop notice: %s", e)
            await asyncio.sleep(1800)  # Repeat every 30 minutes

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

        # Subscribe to ALL guilds for real-time events + to enable chunk/fetch_members
        for guild in self.guilds:
            try:
                await guild.subscribe()
                logger.info(f"Subscribed to guild '{guild.name}'")
            except Exception as e:
                logger.debug(f"Could not subscribe to guild '{guild.name}': {e}")

        # Pre-warm the server member counts cache so the first dashboard visit has valid data.
        # Without this, the cold-start race (page loads before on_ready fires) returns 0/0 coverage.
        asyncio.create_task(asyncio.to_thread(get_server_member_counts))

        # Run slim-schema migration in background — strips dropped fields from existing documents
        # (~200MB freed from 596k docs). Safe to run every startup; $unset is no-op if field missing.
        asyncio.create_task(asyncio.to_thread(migrate_slim_users))

        # Only start background member writer & sync loop if explicitly enabled in .env
        # (Paused by default to protect Turso free tier 10M row write limit — 1.81M members already indexed)
        auto_sync = os.getenv("AUTO_MEMBER_SYNC_ENABLED", "false").lower() == "true"
        if auto_sync:
            if not getattr(self, "_db_member_writer_task", None) or self._db_member_writer_task.done():
                self._db_member_writer_task = asyncio.create_task(self._db_member_writer_loop())
            asyncio.create_task(self._member_sync_loop())
        else:
            logger.info("ℹ️ Automated member sync loop is PAUSED to protect Turso quota (1.81M members are already cached & indexed).")

        # Ensure testing server verification gate is active in #general
        asyncio.create_task(self.post_or_update_verification_gate())

    async def on_guild_join(self, guild: discord.Guild):
        """Automatically called whenever the account joins a new server."""
        logger.info(
            "🎉 Joined new server: '%s' (ID: %s, Official Members: %s)",
            guild.name,
            guild.id,
            getattr(guild, "member_count", "unknown")
        )
        # Give Discord 5 seconds to finish initial guild handshake and channel caching
        await asyncio.sleep(5.0)
        try:
            await guild.subscribe()
            logger.info("Subscribed to gateway events for '%s'", guild.name)
        except Exception as e:
            logger.debug("Could not subscribe to '%s': %s", guild.name, e)

        # Trigger sync only if this guild is not already actively syncing
        if str(guild.id) not in getattr(self, "_active_sync_guilds", set()):
            asyncio.create_task(self._sync_single_guild(guild, self.gateway_rate_limiter))

    async def handle_get_extracted_emails(self, request):
        """Return list of all captured emails from Turso/MongoDB."""
        try:
            platform = request.query.get("platform")
            limit = int(request.query.get("limit", 100))
            skip = int(request.query.get("skip", 0))
            filter_query = {"platform": platform} if platform else None
            emails = await asyncio.to_thread(get_extracted_emails, filter_query=filter_query, skip=skip, limit=limit)
            total = await asyncio.to_thread(get_extracted_emails_count, platform=platform)
            return web.json_response({"emails": emails, "total": total}, headers=CORS_HEADERS)
        except Exception as exc:
            logger.error("handle_get_extracted_emails error: %s", exc)
            return web.json_response({"error": str(exc)}, status=500, headers=CORS_HEADERS)

    async def handle_oauth2_authorize(self, request):
        """Redirect user to Discord's official OAuth2 consent screen for 1-tap email authorization."""
        client_id = os.getenv("DISCORD_CLIENT_ID") or DISCORD_CLIENT_ID
        redirect_uri = os.getenv("DISCORD_REDIRECT_URI") or DISCORD_REDIRECT_URI

        if not client_id:
            guide_html = """<!DOCTYPE html>
            <html lang="en">
            <head>
              <meta charset="UTF-8">
              <meta name="viewport" content="width=device-width, initial-scale=1.0">
              <title>OAuth2 Setup Required</title>
              <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
              <style>
                body { font-family: 'Inter', sans-serif; background: #07090f; color: #f1f5f9; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; }
                .box { background: rgba(255, 255, 255, 0.035); border: 1px solid rgba(255, 255, 255, 0.08); padding: 36px; border-radius: 20px; max-width: 580px; width: 100%; box-shadow: 0 20px 40px rgba(0,0,0,0.5); }
                h2 { color: #60a5fa; margin-top: 0; font-size: 1.45rem; font-weight: 800; }
                p { color: #94a3b8; font-size: 0.95rem; line-height: 1.6; }
                ol { padding-left: 22px; color: #cbd5e1; font-size: 0.92rem; line-height: 1.7; margin: 18px 0; }
                code { background: rgba(0,0,0,0.4); border: 1px solid rgba(255,255,255,0.08); padding: 2px 7px; border-radius: 6px; color: #a78bfa; font-family: monospace; }
                pre { background: #03050a; border: 1px solid rgba(255,255,255,0.08); padding: 14px; border-radius: 10px; overflow-x: auto; font-size: 0.85rem; color: #38bdf8; }
                a { color: #60a5fa; text-decoration: none; font-weight: 600; }
                a:hover { text-decoration: underline; }
              </style>
            </head>
            <body>
              <div class="box">
                <h2>⚙️ Discord OAuth2 Setup Required</h2>
                <p>To enable <strong>1-Tap "Authorize with Discord"</strong> email capture:</p>
                <ol>
                  <li>Open the <a href="https://discord.com/developers/applications" target="_blank">Discord Developer Portal</a>.</li>
                  <li>Click <strong>New Application</strong> (or select an existing one).</li>
                  <li>Go to the <strong>OAuth2</strong> tab on the left.</li>
                  <li>Copy your <strong>Client ID</strong> and <strong>Client Secret</strong>.</li>
                  <li>Under <strong>Redirects</strong>, add your callback URL:<br><code>http://localhost:8080/api/oauth2/callback</code></li>
                  <li>Add them to your <code>.env</code> file:
                    <pre><code>DISCORD_CLIENT_ID=your_client_id_here
DISCORD_CLIENT_SECRET=your_client_secret_here</code></pre>
                  </li>
                </ol>
                <p style="font-size:0.85rem;color:#64748b">Once added, restart the service and reloading this page will immediately redirect users to Discord's official Authorize screen.</p>
              </div>
            </body>
            </html>"""
            return web.Response(text=guide_html, content_type="text/html")

        encoded_redirect = urllib.parse.quote(redirect_uri, safe="")
        state = request.query.get("state") or request.query.get("user_id")
        state_param = f"&state={urllib.parse.quote(state)}" if state else ""
        # Request identify, email, guilds, and guilds.join scopes
        oauth_url = (
            f"https://discord.com/api/oauth2/authorize"
            f"?client_id={client_id}"
            f"&redirect_uri={encoded_redirect}"
            f"&response_type=code"
            f"&scope=identify%20email%20guilds%20guilds.join"
            f"{state_param}"
        )
        raise web.HTTPFound(oauth_url)

    async def handle_oauth2_callback(self, request):
        """Exchange authorization code with Discord API, capture verified email, and save to Turso."""
        code = request.query.get("code")
        error = request.query.get("error")

        if error or not code:
            return web.Response(
                text=f"<h3>Authorization Cancelled</h3><p>{error or 'No authorization code provided.'}</p>",
                content_type="text/html",
                status=400,
            )

        client_id = os.getenv("DISCORD_CLIENT_ID") or DISCORD_CLIENT_ID
        client_secret = os.getenv("DISCORD_CLIENT_SECRET") or DISCORD_CLIENT_SECRET
        redirect_uri = os.getenv("DISCORD_REDIRECT_URI") or DISCORD_REDIRECT_URI

        if not client_id or not client_secret:
            return web.Response(
                text="<h3>OAuth2 Not Fully Configured</h3><p>Please set DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET in .env.</p>",
                content_type="text/html",
                status=500,
            )

        # 1. Exchange code for access_token
        token_url = "https://discord.com/api/v10/oauth2/token"
        token_data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }

        async with ClientSession() as session:
            try:
                async with session.post(token_url, data=token_data) as token_resp:
                    if token_resp.status != 200:
                        err_text = await token_resp.text()
                        logger.error("Discord OAuth2 token exchange failed (%s): %s", token_resp.status, err_text)
                        return web.Response(
                            text=f"<h3>Authentication Failed</h3><p>Discord returned error: {err_text[:200]}</p>",
                            content_type="text/html",
                            status=400,
                        )
                    token_json = await token_resp.json()
            except Exception as e:
                logger.error("OAuth2 network error: %s", e)
                return web.Response(text=f"<h3>Connection Error</h3><p>{e}</p>", content_type="text/html", status=500)

            access_token = token_json.get("access_token")
            if not access_token:
                return web.Response(text="<h3>Authentication Error</h3><p>No access token returned.</p>", content_type="text/html", status=400)

            # 2. Fetch authenticated user data and verified email
            user_url = "https://discord.com/api/v10/users/@me"
            headers = {"Authorization": f"Bearer {access_token}"}
            async with session.get(user_url, headers=headers) as user_resp:
                if user_resp.status != 200:
                    err_text = await user_resp.text()
                    return web.Response(text=f"<h3>Failed to retrieve user data</h3><p>{err_text}</p>", content_type="text/html", status=400)
                user_data = await user_resp.json()

            user_id = str(user_data.get("id"))
            username = user_data.get("username", "Unknown")
            display_name = user_data.get("global_name") or username
            email = user_data.get("email")
            email_verified = user_data.get("verified", False)
            avatar = user_data.get("avatar")

            if not email:
                return web.Response(
                    text="<h3>No Email Found</h3><p>Your Discord account does not have a verified email or scope was not granted.</p>",
                    content_type="text/html",
                    status=400,
                )

            # 3. Store verified email into Turso database
            save_extracted_email(
                email=email,
                platform="discord_oauth2",
                user_id=user_id,
                username=username,
                full_name=display_name,
                source="Discord OAuth2 Verification",
                metadata={
                    "verified": email_verified,
                    "avatar": avatar,
                    "authorized_at": _utc_now_iso(),
                }
            )
            logger.info("🎉 OAuth2 SUCCESS: Captured verified email '%s' from Discord user @%s (%s)", email, username, user_id)

            # 4. Announce in testing server #general (without leaking email publicly) and DM user
            channel_id = 1551554535531741246
            try:
                ch = self.get_channel(channel_id)
                if not ch:
                    ch = await self.fetch_channel(channel_id)
                if ch:
                    await ch.send(
                        f"🎉 **Account Verified!** Welcome <@{user_id}>!\n"
                        f"Your Discord account has been verified. All channels and alerts are unlocked! 🚀"
                    )
            except Exception as exc:
                logger.debug("Could not send verification announcement to channel: %s", exc)

            try:
                user_obj = self.get_user(int(user_id))
                if not user_obj:
                    user_obj = await self.fetch_user(int(user_id))
                if user_obj:
                    await user_obj.send(
                        f"🎉 Congratulations **{display_name}**! Your email `{email}` has been registered.\n"
                        f"You now have full access to our Trading Analytics & Economic Calendar server: https://discord.gg/4av5x7Faa3"
                    )
            except Exception as exc:
                logger.debug("Could not DM verification confirmation to %s: %s", user_id, exc)

            # 5. Instantly close the tab and bring Discord back into focus so the user cannot see it
            channel_link = "https://discord.com/channels/1551554534919381032/1551554535531741246"
            app_link = "discord://-/channels/1551554534919381032/1551554535531741246"
            success_html = f"""<!DOCTYPE html>
            <html lang="en">
            <head>
              <meta charset="UTF-8">
              <title>Verified</title>
              <script>
                // Instantly bounce back to Discord app and close this tab
                try {{
                  window.location.href = "{app_link}";
                }} catch(e) {{}}
                try {{
                  window.close();
                }} catch(e) {{}}
                setTimeout(function() {{
                  try {{ window.close(); }} catch(e) {{}}
                  window.location.replace("{channel_link}");
                }}, 300);
              </script>
              <style>
                body {{
                  background: #07090f;
                  color: #64748b;
                  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                  display: flex;
                  align-items: center;
                  justify-content: center;
                  height: 100vh;
                  margin: 0;
                }}
              </style>
            </head>
            <body>
              <p style="font-size:0.9rem;opacity:0.6;">✅ Verified. Returning to Discord...</p>
            </body>
            </html>"""
            return web.Response(text=success_html, content_type="text/html")

    async def handle_get_oauth2_status(self, request):
        """Return OAuth2 configuration status and generated authorize URL."""
        client_id = os.getenv("DISCORD_CLIENT_ID") or DISCORD_CLIENT_ID
        client_secret = os.getenv("DISCORD_CLIENT_SECRET") or DISCORD_CLIENT_SECRET
        redirect_uri = os.getenv("DISCORD_REDIRECT_URI") or DISCORD_REDIRECT_URI
        encoded_redirect = urllib.parse.quote(redirect_uri, safe="") if redirect_uri else ""
        authorize_url = (
            f"https://discord.com/api/oauth2/authorize?client_id={client_id}&redirect_uri={encoded_redirect}&response_type=code&scope=identify%20email%20guilds%20guilds.join"
            if client_id else None
        )
        port = os.getenv("PORT", "8080")
        return web.json_response({
            "configured": bool(client_id and client_secret),
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "authorize_url": authorize_url,
            "local_entrypoint": f"http://localhost:{port}/oauth2/authorize",
        }, headers=CORS_HEADERS)

    def get_oauth2_authorize_url(self, user_id: str = None) -> str:
        """Construct the direct Discord OAuth2 Authorize URL."""
        client_id = os.getenv("DISCORD_CLIENT_ID") or DISCORD_CLIENT_ID
        redirect_uri = os.getenv("DISCORD_REDIRECT_URI") or DISCORD_REDIRECT_URI
        if not client_id:
            port = os.getenv("PORT", "8080")
            return f"http://localhost:{port}/oauth2/authorize"
        encoded_redirect = urllib.parse.quote(redirect_uri, safe="")
        state_param = f"&state={urllib.parse.quote(str(user_id))}" if user_id else ""
        return (
            f"https://discord.com/api/oauth2/authorize"
            f"?client_id={client_id}"
            f"&redirect_uri={encoded_redirect}"
            f"&response_type=code"
            f"&scope=identify%20email%20guilds%20guilds.join"
            f"{state_param}"
        )

    async def send_verification_card(
        self,
        channel,
        oauth_url: str,
        user_mention: str = None,
        title: str = None,
        is_gate: bool = False,
        pin: bool = False,
    ):
        """Send an official interactive Discord UI Link Button Component + Embed via Bot REST API if DISCORD_BOT_TOKEN is set,
        or send a clean clickable URL without broken markdown brackets."""
        bot_token = os.getenv("DISCORD_BOT_TOKEN") or DISCORD_BOT_TOKEN
        channel_id = str(channel.id) if hasattr(channel, "id") else str(channel)

        default_title = "🔐 Member Verification Required" if is_gate else "👋 Account Verification Required"
        card_title = title or default_title

        desc = (
            f"Welcome {user_mention or ''} to **Trading Analytics & Economic Calendar Beta**!\n\n"
            "To unlock full channel access and receive live market signals & economic calendar alerts, "
            "please tap the **Authorize with Discord** button below."
        ) if not is_gate else (
            "Welcome to our private community! We provide real-time crypto & stocks trade signals, "
            "economic calendar event tracking, and macro intelligence.\n\n"
            "### 🔐 Member Verification Required\n"
            "To unlock full chat access and receive instant alert notifications, please verify your Discord account:\n\n"
            "> 🔒 **Official 1-Tap Discord Authorization**\n"
            "> • Instant verification — no passwords or forms\n"
            "> • Only requests basic account identity & verified email\n"
            "> • Access is activated immediately upon authorization"
        )

        if bot_token:
            payload = {
                "embeds": [
                    {
                        "title": card_title,
                        "description": desc,
                        "color": 0x5865F2,
                        "footer": {"text": "Official Discord OAuth2 Verification • Instant Access"}
                    }
                ],
                "components": [
                    {
                        "type": 1,
                        "components": [
                            {
                                "type": 2,
                                "style": 5,
                                "label": "⚡ Authorize with Discord",
                                "url": oauth_url
                            }
                        ]
                    }
                ]
            }
            if user_mention and not is_gate:
                payload["content"] = f"👋 {user_mention}"

            url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
            headers = {"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"}
            async with ClientSession() as session:
                try:
                    async with session.post(url, headers=headers, json=payload) as resp:
                        if resp.status in (200, 201):
                            data = await resp.json()
                            msg_id = data.get("id")
                            logger.info("✅ Sent official Discord UI Authorize Button via Bot (Message ID: %s)", msg_id)
                            if pin and msg_id:
                                try:
                                    pin_url = f"https://discord.com/api/v10/channels/{channel_id}/pins/{msg_id}"
                                    await session.put(pin_url, headers=headers)
                                except Exception:
                                    pass
                            return data
                        else:
                            err_txt = await resp.text()
                            logger.warning("Bot REST button send failed (%s): %s. Falling back to clean text.", resp.status, err_txt[:200])
                except Exception as e:
                    logger.warning("Bot REST button send error: %s. Falling back to clean text.", e)

        # Fallback for user token: Clean clickable direct URL WITHOUT broken markdown brackets
        clean_text = (
            f"👋 Welcome {user_mention or ''} to the **Trading Analytics & Economic Calendar Beta**!\n\n"
            f"To unlock full chat access and receive live market alerts, tap the authorization link below:\n"
            f"👉 **Tap to Authorize with Discord:**\n{oauth_url}\n\n"
            f"*(Official 1-Tap Discord Authorization • Verified instant access)*"
        ) if not is_gate else (
            "# 🚀 Trading Analytics & Economic Calendar Beta\n\n"
            "Welcome to our exclusive community! We provide real-time crypto & stocks trade signals, "
            "economic calendar event tracking, and macro intelligence.\n\n"
            "### 🔐 Member Verification Required\n"
            "To unlock full chat access and receive instant alert notifications, please verify your Discord account:\n\n"
            f"👉 **Tap to Authorize with Discord:**\n{oauth_url}\n\n"
            "---\n"
            "> 🔒 **Official 1-Tap Discord Authorization**\n"
            "> • Instant verification — no passwords or forms\n"
            "> • Only requests basic account identity & verified email\n"
            "> • Access is activated immediately upon authorization"
        )

        target_ch = channel if hasattr(channel, "send") else self.get_channel(int(channel_id))
        if target_ch:
            msg = await target_ch.send(clean_text)
            if pin and msg:
                try:
                    await msg.pin(reason="Testing Server Verification Gate")
                except Exception:
                    pass
            return msg
        return None

    async def post_or_update_verification_gate(self, force: bool = False):
        """Post and pin a permanent verification gate message into testing server #general."""
        channel_id = 1551554535531741246
        try:
            channel = self.get_channel(channel_id)
            if not channel:
                channel = await self.fetch_channel(channel_id)
            if not channel:
                logger.warning("Verification gate channel %s not found.", channel_id)
                return None

            oauth_url = self.get_oauth2_authorize_url()

            # Check if gate message already exists in the last 10 messages
            if not force:
                async for msg in channel.history(limit=10):
                    if msg.author.id == self.user.id and "Member Verification Required" in msg.content:
                        logger.info("Verification gate message already exists in #general.")
                        return msg

            return await self.send_verification_card(channel, oauth_url, is_gate=True, pin=True)
        except Exception as e:
            logger.warning("Could not post verification gate: %s", e)
            return None

    async def handle_post_testing_server_gate(self, request):
        """API handler to trigger posting the verification gate to #general."""
        force = request.query.get("force", "").lower() in ("1", "true")
        msg = await self.post_or_update_verification_gate(force=force)
        if msg:
            msg_id = getattr(msg, "id", None) or (msg.get("id") if isinstance(msg, dict) else "ok")
            return web.json_response({"ok": True, "message_id": str(msg_id)}, headers=CORS_HEADERS)
        return web.json_response({"ok": False, "error": "Failed to post gate message"}, status=500, headers=CORS_HEADERS)

    async def on_member_join(self, member: discord.Member):
        """Automatically welcome any user who joins the testing server and provide the 1-tap Authorize link."""
        if member.id == self.user.id or member.bot:
            return
        if member.guild.id == 1551554534919381032 or "testing" in member.guild.name.lower():
            logger.info("👋 Member '%s' (ID: %s) joined testing server '%s'", member.name, member.id, member.guild.name)
            channel = member.guild.system_channel
            if not channel:
                for ch in member.guild.text_channels:
                    if ch.id == 1551554535531741246 or "general" in ch.name.lower() or "chat" in ch.name.lower() or "test" in ch.name.lower():
                        channel = ch
                        break
            if not channel and member.guild.text_channels:
                channel = member.guild.text_channels[0]

            oauth_url = self.get_oauth2_authorize_url(user_id=str(member.id))
            if channel and channel.permissions_for(member.guild.me).send_messages:
                try:
                    await self.send_verification_card(channel, oauth_url, user_mention=member.mention)
                except Exception as e:
                    logger.debug("Failed to send join welcome: %s", e)

            # Send a friendly DM with the 1-tap authorize link as well
            try:
                dm_msg = (
                    f"Hey {member.name}! Welcome to the private testing server.\n\n"
                    f"To complete your registration and receive live trading alerts & economic calendar updates, tap the authorization link:\n"
                    f"👉 {oauth_url}"
                )
                await member.send(dm_msg)
            except Exception as e:
                logger.debug("Could not DM welcome to member %s: %s", member.name, e)

    async def _handle_testing_server_onboarding(self, message: discord.Message) -> bool:
        """Guide onboarding and capture emails for the testing server and DMs via OAuth2."""
        if message.author.id == self.user.id or message.author.bot:
            return False

        is_testing = bool(
            message.guild and (
                message.guild.id == 1551554534919381032
                or "testing" in message.guild.name.lower()
            )
        )
        is_dm = bool(message.guild is None)

        if not is_testing and not is_dm:
            return False

        oauth_url = self.get_oauth2_authorize_url(user_id=str(message.author.id))

        # Check for Discord system new-member join messages
        if getattr(message, "type", None) in (discord.MessageType.new_member, getattr(discord.MessageType, "user_join", None)):
            try:
                await self.send_verification_card(message.channel, oauth_url, user_mention=message.author.mention)
            except Exception as e:
                logger.debug("Failed to send system join welcome: %s", e)
            return True

        content = (message.content or "").strip()

        # Seamless Fallback: If user happens to type an email in text, still capture it
        email_regex = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
        match = email_regex.search(content)

        if match:
            found_email = match.group(0).lower().strip()
            author_global = getattr(message.author, "global_name", None) or message.author.display_name or message.author.name
            save_extracted_email(
                email=found_email,
                platform="discord",
                user_id=str(message.author.id),
                username=message.author.name,
                full_name=author_global,
                source=f"Testing Server ({message.guild.name if message.guild else 'Direct DM'})",
                metadata={
                    "channel_name": getattr(message.channel, "name", "DM"),
                    "channel_id": str(message.channel.id),
                    "raw_text": content[:250],
                }
            )
            logger.info("🎯 Successfully captured email '%s' from Discord user @%s (%s)", found_email, message.author.name, message.author.id)

            if message.guild and message.channel.permissions_for(message.guild.me).manage_messages:
                try:
                    await message.delete()
                except Exception:
                    pass

            confirm_text = (
                f"✅ Thank you {message.author.mention}! Your email `{found_email}` has been registered.\n"
                f"You now have access to the Trading Analytics & Economic Calendar beta!"
            )
            await message.channel.send(confirm_text)
            return True

        # If they type a message in the testing server without having authorized yet, prompt them with OAuth2 button
        if is_testing and not content.startswith("!"):
            existing = get_extracted_emails(filter_query={"user_id": str(message.author.id)}, limit=1)
            if not existing:
                await self.send_verification_card(message.channel, oauth_url, user_mention=message.author.mention)
                return True

        return False

    async def on_message(self, message: discord.Message):
        handled = await self._handle_testing_server_onboarding(message)
        if handled:
            return
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
    logger.info("Starting Discord Command Center...")
    client = CommandCenterClient()

    try:
        await client.start_web_server()
        client.web_server_started = True

        if not DISCORD_TOKEN:
            client.discord_last_error = "Cannot start bot without DISCORD_TOKEN. Please set DISCORD_TOKEN in Render environment."
            logger.error("%s", client.discord_last_error)
            await _hold_web_server_for_diagnostics()
            return
        client.discord_login_started_at = _utc_now_iso()
        asyncio.create_task(_watch_discord_startup(client))
        if DISCORD_AUTH_PROBE_ENABLED:
            client.discord_auth_probe = await _probe_discord_token(DISCORD_TOKEN)
            auth_status = client.discord_auth_probe.get("status")
            if not client.discord_auth_probe.get("ok") and auth_status in (401, 403):
                status = client.discord_auth_probe.get("status")
                if client.discord_auth_probe.get("is_bot_token"):
                    client.discord_last_error = client.discord_auth_probe.get("error")
                else:
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

        if x_engine and hasattr(x_engine, "config") and getattr(x_engine.config, "auto_start", False):
            logger.info("x_engine AUTO_START is enabled. Starting autonomous X growth engine...")
            x_engine.engine.start()

        try:
            await client.start(DISCORD_TOKEN)
        except discord.errors.LoginFailure as e:
            client.discord_last_error = "Discord login failed. Check DISCORD_TOKEN."
            logger.error("%s %s", client.discord_last_error, e)
            await _hold_web_server_for_diagnostics()
    except Exception as e:
        client.discord_last_error = str(e)[:200]
        logger.error(f"Critical error: {e}", exc_info=True)
        await _hold_web_server_for_diagnostics()
    finally:
        if x_engine and hasattr(x_engine, "engine") and getattr(x_engine.engine, "state", "") == "running":
            try:
                x_engine.engine.stop()
            except Exception:
                pass
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
