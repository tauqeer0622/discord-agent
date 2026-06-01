import asyncio
import logging
import discord
from config import CONTROL_SERVER_ID, CONTROL_CHANNEL_ID
from state_manager import state
from utils import format_discord_timestamp, extract_attachments
from priority_engine import evaluate_priority
from ai_engine import generate_reply, add_to_context
from typing_simulator import simulate_typing_and_send

logger = logging.getLogger(__name__)

async def create_control_thread(client: discord.Client, message: discord.Message):
    """
    Creates a thread in the Control Server for the incoming message
    and posts the dashboard card.
    """
    # Fetch control guild and channel
    control_guild = client.get_guild(CONTROL_SERVER_ID)
    if not control_guild:
        logger.error(f"Could not find Control Server with ID {CONTROL_SERVER_ID}")
        return

    control_channel = control_guild.get_channel(CONTROL_CHANNEL_ID)
    if not control_channel or not isinstance(control_channel, discord.TextChannel):
        logger.error(f"Could not find Control Channel with ID {CONTROL_CHANNEL_ID} or it is not a text channel")
        return

    # Determine thread name
    source_name = f"{message.guild.name} - #{message.channel.name}" if message.guild else "Direct Message"
    author_name = message.author.name
    thread_name = f"{author_name} | {source_name}"[:100]  # Discord thread name limit is 100 chars

    # Evaluate priority
    priority_tag = evaluate_priority(message.content)

    # Format the dashboard card
    timestamp_str = format_discord_timestamp(message.created_at)
    attachments_str = extract_attachments(message)

    # Generate Message Link for source redirecting
    if message.guild:
        message_link = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id}"
    else:
        message_link = f"https://discord.com/channels/@me/{message.channel.id}/{message.id}"

    card_content = (
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"**NEW INCOMING MESSAGE**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"**Priority:** {priority_tag}\n"
        f"**Source:** {source_name}\n"
        f"**Link:** <{message_link}>\n"
        f"**From:** {author_name} (`{message.author.id}`)\n"
        f"**Time:** {timestamp_str}\n\n"
        f"**Message:**\n"
        f"> {message.content if message.content else '*[No text content]*'}\n\n"
        f"**Attachments:**\n"
        f"{attachments_str}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Status:** ✅ MESSAGE FETCHED\n"
        f"*Reply feature temporarily disabled.*"
    )

    try:
        # Send initial message to create thread from
        thread_msg = await control_channel.send(f"Incoming message from **{author_name}** ({priority_tag})")

        # Create thread
        thread = await thread_msg.create_thread(name=thread_name, auto_archive_duration=1440)

        # Post the card in the thread
        await thread.send(card_content)

        # Register in state manager
        state.register_thread(thread.id, message.channel, message, message.author)

        logger.info(f"Created control thread '{thread_name}' (ID: {thread.id})")

    except Exception as e:
        logger.error(f"Error creating control thread: {e}")

async def append_to_control_thread(client: discord.Client, message: discord.Message, thread_id: int):
    """Appends a follow-up message to an existing control thread and restarts the AI drafting process."""
    try:
        control_guild = client.get_guild(CONTROL_SERVER_ID)
        if not control_guild:
            logger.error("append_to_control_thread: control_guild not found")
            return

        thread = client.get_channel(thread_id)
        if not thread:
            logger.warning(f"append_to_control_thread: thread {thread_id} not found in cache, creating new thread.")
            # If thread is missing/deleted, fall back to creating a new one
            await create_control_thread(client, message)
            return

        # Cancel old auto-reply
        state.cancel_auto_reply(thread_id)

        # Mark as unresolved again so the new AI draft can send
        thread_state = state.get_state(thread_id)
        if thread_state:
            thread_state["resolved"] = False

        # Post the new message to the thread
        attachments_str = extract_attachments(message)

        # Generate Message Link
        if message.guild:
            message_link = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id}"
        else:
            message_link = f"https://discord.com/channels/@me/{message.channel.id}/{message.id}"

        await thread.send(f"**Follow-up from {message.author.name}:**\n> {message.content}\n**Link:** <{message_link}>\n{attachments_str}")

    except Exception as e:
        logger.error(f"Critical error in append_to_control_thread: {e}", exc_info=True)

async def auto_reply_countdown(thread: discord.Thread, thread_id: int, source_channel, ai_draft: str):
    """
    Waits 30 seconds. If the thread is not resolved by a human, sends the AI draft.
    """
    try:
        await asyncio.sleep(30)

        # Check if human intervened
        if not state.is_resolved(thread_id):
            logger.info(f"Auto-reply timeout reached for thread {thread_id}. Sending AI draft.")
            state.mark_resolved(thread_id)
            await thread.send("⏰ **Timeout reached. Auto-sending AI draft...**")
            await simulate_typing_and_send(source_channel, ai_draft)

            # Retrieve author id to update context
            thread_state = state.get_state(thread_id)
            if thread_state and thread_state.get("source_author"):
                add_to_context(str(thread_state["source_author"].id), "assistant", ai_draft)

            await thread.send("🚀 **Auto-reply sent successfully!**")
    except asyncio.CancelledError:
        logger.info(f"Auto-reply countdown cancelled for thread {thread_id} (Human override).")
    except Exception as e:
        logger.error(f"Critical error in auto_reply_countdown for thread {thread_id}: {e}")
        try:
            await thread.send(f"❌ **Error during auto-reply:** {e}")
        except:
            pass
