import asyncio
import json
import logging
import os
import random
from database import get_messages
from datetime import datetime, timedelta, timezone

import discord
from aiohttp import ClientSession, web

from config import DISCORD_TOKEN
from config_manager import config_manager
from discord_permissions import (
    can_send_messages,
    is_locked_or_private_channel,
    is_restricted_text_channel,
)
from message_listener import process_message
from promo_sender import generate_promo_variant
from state_manager import state
from thread_manager import thread_cleanup_loop

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
BULK_PROMO_ENABLED = os.getenv("BULK_PROMO_ENABLED", "true").lower() == "true"

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

    # ── Web Server ─────────────────────────────────────────────

    async def start_web_server(self):
        app = web.Application()
        app.add_routes([
            # Dashboard
            web.get("/",  self.handle_dashboard),
            # Status & data reads
            web.get("/api/status",                        self.handle_get_status),
            web.get("/api/messages",                      self.handle_get_messages),
            web.get("/api/memory",                        self.handle_get_memory),
            web.get("/api/channels",                      self.handle_get_channels),
            web.get("/api/guilds",                        self.handle_get_guilds),
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

    async def handle_get_status(self, request):
        """Return bot connection status and basic info."""
        uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds()
        latency = getattr(self, "latency", None)
        latency_ms = None
        if latency is not None and latency != float("inf"):
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
            })

        return web.json_response(
            data,
            headers=CORS_HEADERS
        )

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

    def _validate_bulk_message(self, base_message):
        if len(base_message) < 5:
            return "Promo message is required"
        if len(base_message) > PROMO_MAX_MESSAGE_LENGTH:
            return f"Promo message must be under {PROMO_MAX_MESSAGE_LENGTH} characters"
        return None

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

        previews = []
        for index, target in enumerate(ready_targets, start=1):
            preview = self._public_bulk_target(target)
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
            previews.append(preview)
        return previews

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
            validation_error = self._validate_bulk_message(base_message)
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
            previews = await self._build_bulk_promo_variants(base_message, targets)
            return web.json_response(
                {
                    "targets": [self._public_bulk_target(target) for target in targets],
                    "previews": previews,
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

            data = await request.json()
            base_message = str(data.get("base_message", "")).strip()
            validation_error = self._validate_bulk_message(base_message)
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

            preview_content = {
                str(item.get("channel_id")): str(item.get("content", "")).strip()
                for item in data.get("previews", [])
                if (
                    item.get("channel_id")
                    and str(item.get("content", "")).strip()
                    and len(str(item.get("content", "")).strip()) <= PROMO_MAX_MESSAGE_LENGTH
                )
            }
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
                "results": [],
                "current_channel_id": None,
                "next_send_at": None,
            }
            self.bulk_promo_task = asyncio.create_task(
                self._run_bulk_promo_job(
                    base_message,
                    ready_targets,
                    preview_content,
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
        min_delay,
        max_delay,
    ):
        try:
            for index, target in enumerate(targets, start=1):
                result = self._public_bulk_target(target)
                self.bulk_promo_job["current_channel_id"] = str(target["channel_id"])
                try:
                    content = preview_content.get(str(target["channel_id"]))
                    if not content:
                        content = await generate_promo_variant(
                            base_message,
                            target,
                            index,
                            len(targets),
                        )
                    if not content or len(content) > PROMO_MAX_MESSAGE_LENGTH:
                        raise RuntimeError("Promo message was empty or too long")

                    await target["_channel"].send(content)
                    result["status"] = "sent"
                    result["content"] = content
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
        client.discord_auth_probe = await _probe_discord_token(DISCORD_TOKEN)
        if not client.discord_auth_probe.get("ok"):
            status = client.discord_auth_probe.get("status")
            client.discord_last_error = f"Discord token check failed with HTTP {status}."
            logger.error("%s", client.discord_last_error)
            await _hold_web_server_for_diagnostics()

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
