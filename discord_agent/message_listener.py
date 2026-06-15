import logging

import discord

from config import CONTROL_SERVER_ID
from config_manager import config_manager
from database import (
    acquire_reply_slot,
    get_channel_thread,
    get_thread_mapping,
    save_reply_for_latest_message,
    save_message,
)
from discord_permissions import is_restricted_text_channel
from priority_engine import is_target_question
from state_manager import state
from thread_manager import append_to_control_thread, create_control_thread
from typing_simulator import simulate_typing_and_send

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

    sent = await simulate_typing_and_send(source_channel, message.content)
    if not sent:
        await message.channel.send("Reply failed to send to the source channel.")
        return True

    save_reply_for_latest_message(
        source_channel_id,
        message.content,
        message.created_at.isoformat(),
    )
    await message.channel.send("Operator reply sent successfully.")
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

    active_channel_ids = set(config_manager.get_active_channel_ids())
    if message.channel.id not in active_channel_ids:
        return

    if is_restricted_text_channel(message.channel):
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
    )

    mapping = get_channel_thread(message.channel.id)
    if mapping:
        await append_to_control_thread(client, message, mapping[2])
        return

    await create_control_thread(client, message)
