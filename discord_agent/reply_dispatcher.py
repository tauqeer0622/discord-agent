import logging
import discord
from state_manager import state
from ai_engine import humanize_override, add_to_context
from typing_simulator import simulate_typing_and_send

logger = logging.getLogger(__name__)

async def handle_manual_reply(message: discord.Message):
    """
    Handles a human operator's reply in a control thread.
    Cancels any auto-reply, expands/humanizes the response, simulates typing, and sends it.
    """
    thread_id = message.channel.id
    
    # Check if this thread is tracked
    thread_state = state.get_state(thread_id)
    if not thread_state:
        logger.debug(f"Message in un-tracked thread {thread_id}, ignoring.")
        return
        
    if state.is_resolved(thread_id):
        # Ignore if already resolved
        return

    # Cancel auto-reply explicitly, then mark as resolved
    state.cancel_auto_reply(thread_id)
    state.mark_resolved(thread_id)

    source_channel = thread_state["source_channel"]
    source_message = thread_state["source_message"]
    
    # Update UI to indicate action
    await message.channel.send("✅ *Human override detected. Canceling auto-reply and processing...*")
    
    # Humanize the operator's input (or use as-is if long)
    final_content = await humanize_override(message.content, source_message.content)
    
    # Simulate typing and send to source
    await simulate_typing_and_send(source_channel, final_content)
    
    # Add human reply to context memory
    add_to_context(str(source_message.author.id), "assistant", final_content)
    
    # Final confirmation in the thread
    await message.channel.send(f"🚀 **Human reply sent successfully!**")

