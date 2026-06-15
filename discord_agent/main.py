import asyncio
import json
import logging
import os
from database import get_messages
from datetime import datetime, timezone

import discord
from aiohttp import web

from config import DISCORD_TOKEN
from config_manager import config_manager
from discord_permissions import is_restricted_text_channel
from message_listener import process_message
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


class CommandCenterClient(discord.Client):
    def __init__(self):
        super().__init__()

        self.web_server_started = False
        self.web_runner = None
        self.thread_cleanup_started = False
        self.start_time = datetime.now(timezone.utc)

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
        data = {
            "online": self.is_ready(),
            "bot_name": f"{self.user.name}#{self.user.discriminator}" if self.is_ready() else None,
            "bot_id": str(self.user.id) if self.is_ready() else None,
            "guild_count": len(self.guilds),
            "uptime_seconds": int(uptime),
            "start_time": self.start_time.isoformat(),
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

            if is_restricted_text_channel(channel):
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
        channels = [
            {"channel_name": ch.name, "channel_id": str(ch.id)}
            for ch in guild.text_channels
            if not is_restricted_text_channel(ch)
        ]
        return web.json_response(channels, headers=CORS_HEADERS)

    # ── Discord Events ─────────────────────────────────────────

    async def on_ready(self):
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
                    and not is_restricted_text_channel(ch)
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
        await client.start(DISCORD_TOKEN)
    except discord.errors.LoginFailure:
        logger.error("Improper token has been passed. Check your DISCORD_TOKEN.")
    except Exception as e:
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
