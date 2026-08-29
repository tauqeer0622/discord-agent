import asyncio
import logging

import discord

from config import CONTROL_SERVER_ID
from config_manager import config_manager
from database import (
    acquire_reply_slot,
    get_channel_thread,
    get_latest_message_for_channel,
    get_thread_mapping,
    release_reply_slot,
    save_reply_for_latest_message,
    save_message,
    upsert_user,
)
from discord_permissions import is_restricted_text_channel
from priority_engine import is_target_question
from state_manager import state
from thread_manager import append_to_control_thread, create_control_thread
from typing_simulator import calculate_typing_duration, simulate_typing_and_send

logger = logging.getLogger(__name__)


async def _handle_operator_reply(client, message):
    mapping = get_thread_mapping(message.channel.id)
    if not mapping:
        return False

    source_channel_id = mapping[0]
    if source_channel_id not in config_manager.get_active_channel_ids():
        await message.channel.send(
            "Source channel is no longer monitored. Reply not sent."
        )
        return True

    raw_content = (message.content or "").strip()
    is_dm = raw_content.startswith("!dm")
    if is_dm:
        reply_content = raw_content[3:].strip()
        if not reply_content:
            await message.channel.send("⚠️ Usage: `!dm <your message text>` to send a private DM.")
            return True
    else:
        reply_content = raw_content

    if not reply_content:
        return True

    # ── Handle Private DM Reply ────────────────────────────────
    if is_dm:
        latest_doc = get_latest_message_for_channel(source_channel_id)
        author_id = latest_doc.get("author_id") if latest_doc else None
        author_name = latest_doc.get("author", "user") if latest_doc else "the user"

        if not author_id:
            await message.channel.send(
                "❌ Could not find a sender user ID for this channel to send a DM."
            )
            return True

        target_user = client.get_user(int(author_id))
        if not target_user:
            try:
                target_user = await client.fetch_user(int(author_id))
            except (discord.NotFound, discord.Forbidden):
                target_user = None

        if not target_user:
            await message.channel.send(f"❌ User `{author_name}` (`{author_id}`) is unavailable.")
            return True

        reply_slot = acquire_reply_slot()
        if not reply_slot:
            await message.channel.send(
                "Global Discord reply limit reached. DM not sent."
            )
            return True

        try:
            async with target_user.typing():
                await asyncio.sleep(calculate_typing_duration(reply_content))
            await target_user.send(reply_content)
        except discord.Forbidden:
            release_reply_slot(reply_slot)
            await message.channel.send(
                f"❌ Failed to DM **{target_user.name}**: Direct messages are closed/blocked in their privacy settings."
            )
            return True
        except Exception as exc:
            release_reply_slot(reply_slot)
            logger.error("Operator DM failed: %s", exc)
            await message.channel.send(f"❌ Failed to DM **{target_user.name}**: {exc}")
            return True

        save_reply_for_latest_message(
            source_channel_id,
            reply_content,
            message.created_at.isoformat(),
            reply_type="dm",
        )
        await message.channel.send(f"🔒 Operator **private DM** sent to **{target_user.name}** successfully.")
        logger.info(
            "Operator DM sent from thread %s to user %s (%s)",
            message.channel.id,
            target_user.name,
            author_id,
        )
        return True

    # ── Handle Public Channel Reply ────────────────────────────
    source_channel = client.get_channel(source_channel_id)
    if not source_channel:
        try:
            source_channel = await client.fetch_channel(source_channel_id)
        except (discord.NotFound, discord.Forbidden):
            await message.channel.send("Source channel is unavailable. Reply not sent.")
            return True

    reply_slot = acquire_reply_slot()
    if not reply_slot:
        await message.channel.send(
            "Global Discord reply limit reached. Reply not sent."
        )
        return True

    sent = await simulate_typing_and_send(source_channel, reply_content)
    if not sent:
        release_reply_slot(reply_slot)
        await message.channel.send("Reply failed to send to the source channel.")
        return True

    save_reply_for_latest_message(
        source_channel_id,
        reply_content,
        message.created_at.isoformat(),
        reply_type="channel",
    )
    await message.channel.send("Operator reply sent to channel successfully.")
    logger.info(
        "Operator reply sent from thread %s to source channel %s",
        message.channel.id,
        source_channel_id,
    )
    return True


async def process_message(client: discord.Client, message: discord.Message):
    """Route monitored Discord messages into channel-scoped control threads."""
    if message.author.bot:
        return

    if message.guild and message.guild.id == CONTROL_SERVER_ID:
        if isinstance(message.channel, discord.Thread):
            if message.author.id == client.user.id:
                return
            await _handle_operator_reply(client, message)
        return

    if message.author.id == client.user.id or message.guild is None:
        return

    # Passively index message author into MongoDB users directory
    try:
        author = message.author
        roles = [r.name for r in getattr(author, "roles", []) if r.name != "@everyone"]
        upsert_user({
            "user_id": str(author.id),
            "username": author.name,
            "display_name": getattr(author, "global_name", None) or author.display_name or author.name,
            "server_nickname": getattr(author, "nick", None) or author.display_name or author.name,
            "server_name": message.guild.name if message.guild else None,
            "channel_name": f"#{message.channel.name}" if hasattr(message.channel, "name") else None,
            "assigned_roles": roles,
            "is_bot": bool(author.bot),
            "avatar_url": str(author.avatar.url) if author.avatar else None,
        })
    except Exception as exc:
        logger.debug("Failed to passively index user: %s", exc)

    active_channel_ids = set(config_manager.get_active_channel_ids())
    if message.channel.id not in active_channel_ids:
        return

    if is_restricted_text_channel(message.channel, client.user):
        logger.debug(
            "Ignored message in restricted/private channel %s",
            message.channel.id,
        )
        return

    if not is_target_question(message.content):
        logger.debug(
            "Ignored non-target message in channel %s",
            message.channel.id,
        )
        return

    save_message(
        author=message.author.name,
        content=message.content,
        channel_name=getattr(message.channel, "name", "Unknown"),
        guild_name=message.guild.name,
        timestamp=message.created_at.isoformat(),
        channel_id=message.channel.id,
        guild_id=message.guild.id,
        source_message_id=message.id,
        author_id=message.author.id,
    )

    mapping = get_channel_thread(message.channel.id)
    if mapping:
        await append_to_control_thread(client, message, mapping[2])
        return

    await create_control_thread(client, message)

