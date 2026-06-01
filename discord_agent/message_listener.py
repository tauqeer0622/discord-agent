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

logger = logging.getLogger(__name__)

async def post_to_django(message: discord.Message):
    """POST incoming messages to Django backend webhook."""
    url = os.getenv("DJANGO_WEBHOOK_URL", "http://127.0.0.1:8000/webhook/messages/")
    
    channel_name = message.channel.name if hasattr(message.channel, 'name') else "DM"
    source_label = f"Direct Message - {message.author.name}" if not message.guild else f"{message.guild.name} - {channel_name}"
    
    payload = {
        source_label: [
            {
                "author": message.author.name,
                "content": message.content,
                "timestamp": message.created_at.isoformat()
            }
        ]
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                if response.status in (200, 201):
                    logger.info(f"✅ Successfully posted message to Django API: {message.content}")
                else:
                    logger.error(f"❌ Failed to post to Django API. Status: {response.status}")
    except Exception as e:
        logger.error(f"❌ Could not connect to Django API: {e}")

async def process_message(client: discord.Client, message: discord.Message):
    """
    Main routing logic for all incoming messages.
    """
    logger.info(f"Incoming message from {message.author}: {message.content} (Channel: {message.channel.id})")
    # 1. Ignore bot messages
    if message.author.bot:
        return

    # 2. Handle messages in Control Server
    if message.guild and message.guild.id == CONTROL_SERVER_ID:
        # Check if it's a message in a tracked thread
        if isinstance(message.channel, discord.Thread) and state.get_state(message.channel.id):
            # Only process if it's the account owner replying
            if message.author.id == client.user.id:
                # Prevent infinite loops from the bot's own status messages
                bot_prefixes = ("✅", "🚀", "⚠️", "⏰", "🤖", "Incoming message", "━━━━━━━━━━━━━━━━━━━━━━", "⏳", "**Follow-up")
                if message.content.startswith(bot_prefixes):
                    return
                
                logger.info("Operator reply detected in Control Thread, but replies are temporarily disabled.")
                # await handle_manual_reply(message)
        return

    # 3. Ignore own messages in external servers to prevent loops
    if message.author.id == client.user.id:
       return

    # POST external messages to Django backend before applying hardcoded channel filters
    if not message.guild or message.guild.id != CONTROL_SERVER_ID:
        client.loop.create_task(post_to_django(message))

    # 4. Check if there's an active thread for this user
    active_thread_id = state.get_thread_for_user(message.author.id)

    if active_thread_id:
        logger.info(f"Routing follow-up message from {message.author.name} to existing thread {active_thread_id}")
        await append_to_control_thread(client, message, active_thread_id)
        return

    # 5. Filter messages to only target questions
    # Bypass filter for Direct Messages so the AI can freely converse
    is_dm = message.guild is None
    print("MESSAGE CHANNEL:", message.channel.id)
    print("ACTIVE CHANNELS:", config_manager.get_active_channel_ids())

    print(
        f"CHANNEL NAME: {message.channel.name}, "
        f"CHANNEL ID: {message.channel.id}"
    )

    print("MESSAGE CHANNEL:", message.channel.id, getattr(message.channel, "name", "unknown"))
    print("ACTIVE CHANNELS:", config_manager.get_active_channel_ids())
    # Enforce Target Channel filtering for external servers
    if not is_dm and message.channel.id not in config_manager.get_active_channel_ids():
        return

    if not is_dm and not is_target_question(message.content):
        logger.debug(f"Ignored non-target message from {message.author.name}")
        return

    # 6. Handle incoming target questions from other servers/DMs
    logger.info(f"Intercepted target question from {message.author.name} in {message.guild.name if message.guild else 'DM'}")
    await create_control_thread(client, message)

