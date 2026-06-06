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
        # f"*Reply feature temporarily disabled.*"
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
        # Generate AI draft
        ai_draft = await generate_reply(
            message.content,
            str(message.author.id)
        )

        await thread.send(
            f"🤖 **AI Draft Reply:**\n\n"
            f"{ai_draft}\n\n"
            f"⏳ This draft will be auto-sent in **60 seconds**.\n"
            f"Type **cancel** to stop auto-posting.\n"
            f"Or type your own reply to send it instead of the AI draft."
        )

        # Start auto reply timer
        task = asyncio.create_task(
            auto_reply_countdown(
                thread,
                thread.id,
                message.channel,
                ai_draft
            )
        )

        state.register_auto_reply(thread.id, task)

        logger.info(f"Created control thread '{thread_name}' (ID: {thread.id})")

    except Exception as e:
        logger.error(f"Error creating control thread: {e}")

async def append_to_control_thread(
    client: discord.Client,
    message: discord.Message,
    thread_id: int
):
    """
    Appends a follow-up message to an existing control thread,
    generates a fresh AI draft, and restarts the auto-reply timer.
    """
    try:
        control_guild = client.get_guild(CONTROL_SERVER_ID)

        if not control_guild:
            logger.error("append_to_control_thread: control_guild not found")
            return

        thread = client.get_channel(thread_id)

        if not thread:
            logger.warning(
                f"append_to_control_thread: thread {thread_id} not found. "
                f"Creating a new thread."
            )
            await create_control_thread(client, message)
            return

        thread_state = state.get_state(thread_id)

        if not thread_state:
            logger.error(
                f"append_to_control_thread: no state found for thread {thread_id}"
            )
            return

        # Cancel OLD timer first
        state.cancel_auto_reply(thread_id)

        # Mark unresolved again
        thread_state["resolved"] = False

        # Generate message link
        if message.guild:
            message_link = (
                f"https://discord.com/channels/"
                f"{message.guild.id}/"
                f"{message.channel.id}/"
                f"{message.id}"
            )
        else:
            message_link = (
                f"https://discord.com/channels/@me/"
                f"{message.channel.id}/"
                f"{message.id}"
            )

        attachments_str = extract_attachments(message)

        # Post follow-up first
        await thread.send(
            f"**Follow-up from {message.author.name}:**\n"
            f"> {message.content}\n"
            f"**Link:** <{message_link}>\n"
            f"{attachments_str}"
        )

        logger.info(
            f"Generating follow-up AI draft for: {message.content}"
        )

        # Generate fresh AI draft
        ai_draft = await generate_reply(

            message.content,str(message.author.id)
        )

        # Post AI draft
        await thread.send(
            f"🤖 **AI Draft Reply:**\n\n"
            f"{ai_draft}\n\n"
            f"⏳ This draft will be auto-sent in **60 seconds**.\n"
            f"Type **cancel** to stop auto-posting.\n"
            f"Or type your own reply to send it instead of the AI draft."
        )

        # Start NEW timer
        task = asyncio.create_task(
            auto_reply_countdown(
                thread,
                thread_id,
                thread_state["source_channel"],
                ai_draft
            )
        )

        state.set_auto_reply_task(thread_id, task)

        logger.info(
            f"Restarted auto-reply timer for thread {thread_id}"
        )

    except Exception as e:
        logger.error(
            f"Critical error in append_to_control_thread: {e}",
            exc_info=True
        )
async def auto_reply_countdown(
    thread: discord.Thread,
    thread_id: int,
    source_channel,
    ai_draft: str
):
    """
    Waits 60 seconds.
    If operator does nothing, auto-send AI draft.
    If operator intervenes, the task will be cancelled.
    """

    try:

        await asyncio.sleep(60)

        if state.is_resolved(thread_id):
            return

        logger.info(
            f"Auto-reply timeout reached for thread {thread_id}"
        )

        state.mark_resolved(thread_id)

        await thread.send(
            "⏰ **60 seconds elapsed. Auto-sending AI draft...**"
        )

        await simulate_typing_and_send(
            source_channel,
            ai_draft
        )

        thread_state = state.get_state(thread_id)

        if (
            thread_state
            and thread_state.get("source_author")
        ):
            add_to_context(
                str(thread_state["source_author"].id),
                "assistant",
                ai_draft
            )

        await thread.send(
            "🚀 **AI draft sent successfully!**"
        )

    except asyncio.CancelledError:

        logger.info(
            f"Auto-reply cancelled for thread {thread_id}"
        )

    except Exception as e:

        logger.error(
            f"auto_reply_countdown error: {e}"
        )
