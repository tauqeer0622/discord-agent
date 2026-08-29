import asyncio
import io
import logging
import random
import uuid
from datetime import datetime, timezone
import discord

from database import (
    get_campaign_target_users,
    save_campaign_record,
    get_latest_campaign_record,
)

logger = logging.getLogger("mass_dm_manager")


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


class MassDmManager:
    """Manages server-targeted Mass DM campaigns with safe pacing and image attachments."""

    def __init__(self):
        self.client = None
        self.current_task = None
        self.state = {
            "id": None,
            "status": "idle",  # idle | running | paused | completed | stopped
            "server_name": None,
            "user_type": "human",
            "message_template": "",
            "has_image": False,
            "image_filename": None,
            "delay_mode": "safe",
            "total_targets": 0,
            "sent_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "current_index": 0,
            "current_target": None,
            "next_send_at": None,
            "started_at": None,
            "updated_at": None,
            "completed_at": None,
            "error": None,
            "logs": [],
        }
        self._image_bytes = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()

    def set_client(self, client):
        self.client = client

    def get_status(self):
        """Return the current campaign status."""
        progress_pct = 0
        total = self.state.get("total_targets", 0)
        processed = self.state.get("sent_count", 0) + self.state.get("skipped_count", 0) + self.state.get("failed_count", 0)
        if total > 0:
            progress_pct = min(100, round((processed / total) * 100, 1))

        return {
            **self.state,
            "processed_count": processed,
            "progress_percent": progress_pct,
        }

    def _log(self, text, level="info"):
        entry = {
            "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "text": text,
            "level": level,
        }
        logs = self.state.get("logs", [])
        logs.insert(0, entry)
        self.state["logs"] = logs[:60]  # Keep last 60 log entries
        self.state["updated_at"] = _utc_now_iso()
        save_campaign_record(self.state)

    def _get_delay_seconds(self, mode):
        if mode == "fast":
            return random.uniform(10.0, 20.0)
        elif mode == "stealth":
            return random.uniform(60.0, 120.0)
        # default safe mode
        return random.uniform(30.0, 60.0)

    def start_campaign(
        self,
        server_name=None,
        user_type="human",
        message_template="",
        image_bytes=None,
        image_filename=None,
        delay_mode="safe",
    ):
        """Initialize and launch a new server-targeted mass DM broadcast."""
        if self.state["status"] == "running":
            raise ValueError("A campaign is already running. Please pause or stop it first.")

        if not message_template and not image_bytes:
            raise ValueError("Campaign must have message text or an image attachment.")

        targets = get_campaign_target_users(server=server_name, user_type=user_type)
        if not targets:
            raise ValueError(f"No matching members found for server: {server_name or 'All'}")

        cid = f"cmp_{uuid.uuid4().hex[:8]}"
        self._image_bytes = image_bytes
        self._pause_event.set()

        self.state = {
            "id": cid,
            "status": "running",
            "server_name": server_name or "All Servers",
            "user_type": user_type,
            "message_template": message_template,
            "has_image": bool(image_bytes),
            "image_filename": image_filename or ("promo.png" if image_bytes else None),
            "delay_mode": delay_mode,
            "total_targets": len(targets),
            "sent_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "current_index": 0,
            "current_target": None,
            "next_send_at": None,
            "started_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "completed_at": None,
            "error": None,
            "logs": [],
        }

        self._log(f"🚀 Launched Mass DM Campaign for {len(targets)} members in '{self.state['server_name']}'.")
        self.current_task = asyncio.create_task(self._dispatch_loop(targets))
        return self.get_status()

    def pause_campaign(self):
        """Pause the running campaign."""
        if self.state["status"] != "running":
            return self.get_status()
        self._pause_event.clear()
        self.state["status"] = "paused"
        self._log("⏸️ Campaign paused by operator.")
        return self.get_status()

    def resume_campaign(self):
        """Resume a paused campaign."""
        if self.state["status"] != "paused":
            return self.get_status()
        self._pause_event.set()
        self.state["status"] = "running"
        self._log("▶️ Campaign resumed.")
        return self.get_status()

    def stop_campaign(self):
        """Cancel and stop the current campaign."""
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
        self._pause_event.set()
        self.state["status"] = "stopped"
        self.state["completed_at"] = _utc_now_iso()
        self._log("⏹️ Campaign stopped by operator.", level="warning")
        return self.get_status()

    async def _dispatch_loop(self, targets):
        """Background dispatcher loop."""
        try:
            for idx, target in enumerate(targets):
                # Check for pause event
                await self._pause_event.wait()

                user_id_str = target.get("user_id")
                username = target.get("username", "User")
                display_name = target.get("display_name") or target.get("server_nickname") or username
                server_name = target.get("server_name") or self.state.get("server_name") or "Server"

                self.state["current_index"] = idx + 1
                self.state["current_target"] = {
                    "user_id": user_id_str,
                    "username": username,
                    "display_name": display_name,
                }

                # Construct personalized message
                content = self.state["message_template"] or ""
                content = content.replace("{username}", username)
                content = content.replace("{display_name}", display_name)
                content = content.replace("{server_name}", server_name)

                # Send DM
                try:
                    await self._send_single_dm(int(user_id_str), content, self._image_bytes, self.state["image_filename"])
                    self.state["sent_count"] += 1
                    self._log(f"✅ Delivered DM to @{username} ({idx + 1}/{len(targets)})")
                except discord.Forbidden:
                    self.state["skipped_count"] += 1
                    self._log(f"🔒 Skipped @{username} (Closed DMs / Privacy settings)", level="warning")
                except discord.NotFound:
                    self.state["skipped_count"] += 1
                    self._log(f"⚠️ User @{username} (ID: {user_id_str}) not found", level="warning")
                except discord.HTTPException as http_err:
                    if http_err.status == 429:
                        retry_after = getattr(http_err, "retry_after", 30)
                        self._log(f"⏳ Rate limited by Discord. Pausing for {round(retry_after, 1)}s...", level="warning")
                        await asyncio.sleep(retry_after + 5)
                        # Retry once
                        try:
                            await self._send_single_dm(int(user_id_str), content, self._image_bytes, self.state["image_filename"])
                            self.state["sent_count"] += 1
                            self._log(f"✅ Delivered DM to @{username} after rate-limit recovery")
                        except Exception as retry_e:
                            self.state["failed_count"] += 1
                            self._log(f"❌ Failed to DM @{username}: {retry_e}", level="error")
                    else:
                        self.state["failed_count"] += 1
                        self._log(f"❌ Failed to DM @{username}: {http_err.text}", level="error")
                except Exception as exc:
                    self.state["failed_count"] += 1
                    self._log(f"❌ Error sending to @{username}: {exc}", level="error")

                # Pace next send if more targets remain
                if idx < len(targets) - 1:
                    delay = self._get_delay_seconds(self.state["delay_mode"])
                    self.state["next_send_at"] = (datetime.now(timezone.utc).timestamp() + delay)
                    await asyncio.sleep(delay)

            self.state["status"] = "completed"
            self.state["completed_at"] = _utc_now_iso()
            self._log(f"🎉 Campaign Completed! Sent: {self.state['sent_count']}, Skipped: {self.state['skipped_count']}, Failed: {self.state['failed_count']}.")
        except asyncio.CancelledError:
            self.state["status"] = "stopped"
            self.state["completed_at"] = _utc_now_iso()
            self._log("⏹️ Campaign execution cancelled.", level="warning")
        except Exception as err:
            logger.error("Campaign loop unexpected error: %s", err)
            self.state["status"] = "stopped"
            self.state["error"] = str(err)
            self._log(f"🚨 Campaign crashed: {err}", level="error")
        finally:
            self.state["current_target"] = None
            self.state["next_send_at"] = None
            save_campaign_record(self.state)

    async def _send_single_dm(self, user_id: int, content: str, image_bytes: bytes = None, image_filename: str = None):
        """Delivers a single 1-to-1 DM with optional image."""
        if not self.client:
            raise RuntimeError("Discord client is not ready.")

        user = self.client.get_user(user_id)
        if user is None:
            user = await self.client.fetch_user(user_id)

        dm_channel = user.dm_channel
        if dm_channel is None:
            dm_channel = await user.create_dm()

        file_obj = None
        if image_bytes:
            file_obj = discord.File(io.BytesIO(image_bytes), filename=image_filename or "promo.png")

        if file_obj:
            await dm_channel.send(content=content or None, file=file_obj)
        else:
            await dm_channel.send(content=content)


mass_dm_manager = MassDmManager()
