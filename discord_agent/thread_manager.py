import asyncio
import logging

import discord

from config import CONTROL_CHANNEL_ID, CONTROL_SERVER_ID
from config_manager import config_manager
from database import (
    delete_channel_thread,
    get_channel_thread,
    get_expired_channel_threads,
    save_channel_thread,
    touch_channel_thread,
)
from state_manager import state
from utils import extract_attachments, format_discord_timestamp

logger = logging.getLogger(__name__)

THREAD_INACTIVITY_DAYS = 7
THREAD_CLEANUP_INTERVAL_SECONDS = 60 * 60


async def _get_control_channel(client):
    control_guild = client.get_guild(CONTROL_SERVER_ID)
    if not control_guild:
        logger.error("Could not find Control Server with ID %s", CONTROL_SERVER_ID)
        return None

    control_channel = control_guild.get_channel(CONTROL_CHANNEL_ID)
    if not control_channel or not isinstance(control_channel, discord.TextChannel):
        logger.error(
            "Could not find text Control Channel with ID %s",
            CONTROL_CHANNEL_ID,
        )
        return None

    return control_channel


async def _get_thread(client, thread_id):
    thread = client.get_channel(int(thread_id))
    if thread:
        return thread

    try:
        return await client.fetch_channel(int(thread_id))
    except (discord.NotFound, discord.Forbidden):
        return None


async def _create_thread(starter, name):
    try:
        return await starter.create_thread(
            name=name,
            auto_archive_duration=10080,
        )
    except discord.HTTPException:
        logger.warning(
            "Seven-day auto-archive is unavailable; using one day. "
            "The inactivity cleanup still uses seven days."
        )
        return await starter.create_thread(
            name=name,
            auto_archive_duration=1440,
        )


def _message_link(message):
    return (
        f"https://discord.com/channels/{message.guild.id}/"
        f"{message.channel.id}/{message.id}"
    )


def _message_card(message, heading):
    content = message.content if message.content else "*[No text content]*"
    return (
        f"**{heading}**\n"
        f"**Source:** {message.guild.name} - #{message.channel.name}\n"
        f"**From:** {message.author.name} (`{message.author.id}`)\n"
        f"**Time:** {format_discord_timestamp(message.created_at)}\n"
        f"**Link:** <{_message_link(message)}>\n\n"
        f"**Message:**\n> {content}\n\n"
        f"**Attachments:**\n{extract_attachments(message)}"
    )


async def create_control_thread(client: discord.Client, message: discord.Message):
    """Create one persistent control thread for the source channel."""
    if message.guild is None:
        return None

    if message.channel.id not in config_manager.get_active_channel_ids():
        return None

    existing = get_channel_thread(message.channel.id)
    if existing:
        return await append_to_control_thread(client, message, existing[2])

    control_channel = await _get_control_channel(client)
    if not control_channel:
        return None

    source_name = f"{message.guild.name} - #{message.channel.name}"
    starter = None
    thread = None

    try:
        starter = await control_channel.send(
            f"Monitored channel: **{source_name}**"
        )
        thread = await _create_thread(starter, source_name[:100])
        await thread.send(_message_card(message, "NEW MATCHING MESSAGE"))

        save_channel_thread(
            channel_id=message.channel.id,
            guild_id=message.guild.id,
            thread_id=thread.id,
            starter_message_id=starter.id,
            last_activity=message.created_at.isoformat(),
        )
        state.register_thread(
            thread.id,
            message.channel,
            message,
            message.author,
        )
        logger.info(
            "Created control thread %s for source channel %s",
            thread.id,
            message.channel.id,
        )
        return thread.id
    except Exception:
        logger.exception("Error creating thread for channel %s", message.channel.id)
        if thread:
            try:
                await thread.delete()
            except Exception:
                logger.warning("Could not remove partially created thread %s", thread.id)
        if starter:
            try:
                await starter.delete()
            except Exception:
                logger.warning(
                    "Could not remove partially created starter message %s",
                    starter.id,
                )
        return None


async def append_to_control_thread(
    client: discord.Client,
    message: discord.Message,
    thread_id: int,
):
    """Append a source-channel message to its existing control thread."""
    thread = await _get_thread(client, thread_id)
    if not thread:
        delete_channel_thread(message.channel.id)
        return await create_control_thread(client, message)

    try:
        if getattr(thread, "archived", False):
            await thread.edit(archived=False)

        await thread.send(_message_card(message, "CHANNEL MESSAGE"))
        touch_channel_thread(
            message.channel.id,
            message.created_at.isoformat(),
        )
        state.register_thread(
            thread.id,
            message.channel,
            message,
            message.author,
        )
        logger.info(
            "Routed source channel %s to control thread %s",
            message.channel.id,
            thread.id,
        )
        return thread.id
    except (discord.NotFound, discord.Forbidden):
        delete_channel_thread(message.channel.id)
        return await create_control_thread(client, message)
    except Exception:
        logger.exception("Could not append to control thread %s", thread_id)
        return None


async def cleanup_expired_threads(client: discord.Client):
    """Delete threads whose source channels have been inactive for seven days."""
    control_channel = await _get_control_channel(client)

    for mapping in get_expired_channel_threads(THREAD_INACTIVITY_DAYS):
        channel_id, _, thread_id, starter_message_id, _ = mapping
        thread = await _get_thread(client, thread_id)

        if thread:
            try:
                await thread.delete()
            except discord.NotFound:
                pass
            except Exception:
                logger.exception("Could not delete expired thread %s", thread_id)
                continue

        if control_channel and starter_message_id:
            try:
                starter = await control_channel.fetch_message(starter_message_id)
                await starter.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
            except Exception:
                logger.warning(
                    "Could not delete starter message %s",
                    starter_message_id,
                )

        delete_channel_thread(channel_id)
        state.cleanup_thread(thread_id)
        logger.info(
            "Deleted inactive thread %s for source channel %s",
            thread_id,
            channel_id,
        )


async def thread_cleanup_loop(client: discord.Client):
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            await cleanup_expired_threads(client)
        except Exception:
            logger.exception("Thread cleanup cycle failed")
        await asyncio.sleep(THREAD_CLEANUP_INTERVAL_SECONDS)
