import logging
import discord
import aiohttp
import os
from config import CONTROL_SERVER_ID
from config_manager import config_manager
from thread_manager import create_control_thread, append_to_control_thread
from reply_dispatcher import handle_manual_reply
from state_manager import state
from priority_engine import is_target_question
from database import save_message
from ai_engine import generate_reply

logger = logging.getLogger(__name__)

# async def post_to_django(message: discord.Message):
#     """POST incoming messages to Django backend webhook."""
#     url = os.getenv("DJANGO_WEBHOOK_URL", "http://127.0.0.1:8000/webhook/messages/")
#
#     channel_name = message.channel.name if hasattr(message.channel, 'name') else "DM"
#     source_label = f"Direct Message - {message.author.name}" if not message.guild else f"{message.guild.name} - {channel_name}"
#
#     payload = {
#         source_label: [
#             {
#                 "author": message.author.name,
#                 "content": message.content,
#                 "timestamp": message.created_at.isoformat()
#             }
#         ]
#     }
#     try:
#         async with aiohttp.ClientSession() as session:
#             async with session.post(url, json=payload) as response:
#                 if response.status in (200, 201):
#                     logger.info(f"✅ Successfully posted message to Django API: {message.content}")
#                 else:
#                     logger.error(f"❌ Failed to post to Django API. Status: {response.status}")
#     except Exception as e:
#         logger.error(f"❌ Could not connect to Django API: {e}")
async def process_message(
    client: discord.Client,
    message: discord.Message
):
    """
    Main routing logic for all incoming messages.
    """

    logger.info(
        f"Incoming message from {message.author}: "
        f"{message.content} "
        f"(Channel: {message.channel.id})"
    )

    # Ignore bot messages
    if message.author.bot:
        return

    # Handle Control Server messages
    if (
        message.guild
        and message.guild.id == CONTROL_SERVER_ID
    ):
        if (
            isinstance(message.channel, discord.Thread)
            and state.get_state(message.channel.id)
        ):
            if message.author.id == client.user.id:

                bot_prefixes = (
                    "✅",
                    "🚀",
                    "⚠️",
                    "⏰",
                    "🤖",
                    "Incoming message",
                    "━━━━━━━━━━━━━━━━━━━━━━",
                    "⏳",
                    "**Follow-up"
                )

                if message.content.startswith(bot_prefixes):
                    return

                logger.info(
                    "Operator reply detected in Control Thread "
                    "(manual replies currently disabled)"
                )

                # await handle_manual_reply(message)

        return

    # Ignore own messages
    if message.author.id == client.user.id:
        return

    # Save EVERY incoming user message to DB
    save_message(
        author=message.author.name,
        content=message.content,
        channel_name=getattr(
            message.channel,
            "name",
            "DM"
        ),
        guild_name=(
            message.guild.name
            if message.guild
            else "DM"
        ),
        timestamp=message.created_at.isoformat()
    )

    # Check for existing thread
    active_thread_id = state.get_thread_for_user(
        message.author.id
    )

    if active_thread_id:
        logger.info(
            f"Routing follow-up message from "
            f"{message.author.name} "
            f"to existing thread {active_thread_id}"
        )

        await append_to_control_thread(
            client,
            message,
            active_thread_id
        )

        return

    # DM bypass
    is_dm = message.guild is None

    print("MESSAGE CHANNEL:", message.channel.id)
    print("ACTIVE CHANNELS:", config_manager.get_active_channel_ids())

    # Channel filter
    if (
        not is_dm
        and message.channel.id
        not in config_manager.get_active_channel_ids()
    ):
        return

    # Question filter
    if (
        not is_dm
        and not is_target_question(message.content)
    ):
        logger.debug(
            f"Ignored non-target message from "
            f"{message.author.name}"
        )
        return

    logger.info(
        f"Intercepted target question from "
        f"{message.author.name} "
        f"in "
        f"{message.guild.name if message.guild else 'DM'}"
    )

    await create_control_thread(
        client,
        message
    )