import logging

logger = logging.getLogger(__name__)

class StateManager:
    def __init__(self):
        # Maps Control Server Thread ID -> dict of source context
        self.pending_replies = {}

    def register_thread(self, thread_id: int, source_channel, source_message, source_author):
        """Registers a new control thread mapping to its source."""
        self.pending_replies[thread_id] = {
            "source_channel": source_channel,
            "source_message": source_message,
            "source_author": source_author,
            "resolved": False,
            "auto_reply_task": None
        }
        logger.debug(f"Registered thread {thread_id} for channel {source_channel.id}")

    def register_auto_reply(self, thread_id: int, task):
        if thread_id in self.pending_replies:
            self.pending_replies[thread_id]["auto_reply_task"] = task

    def get_state(self, thread_id: int):
        """Returns the state dictionary for a given thread, or None."""
        return self.pending_replies.get(thread_id)

    def get_thread_for_user(self, author_id: int):
        """Returns the thread ID for this user, if any exists."""
        for t_id in reversed(list(self.pending_replies.keys())):
            if self.pending_replies[t_id]["source_author"].id == author_id:
                return t_id
        return None

    def is_resolved(self, thread_id: int) -> bool:
        """Checks if a thread has already been resolved to prevent double sends."""
        state = self.get_state(thread_id)
        if not state:
            return True # If not found, consider it resolved/invalid
        return state["resolved"]

    def mark_resolved(self, thread_id: int):
        """Marks a thread as resolved."""
        state = self.get_state(thread_id)
        if state:
            state["resolved"] = True
            logger.debug(f"Thread {thread_id} marked as resolved.")

    def cancel_auto_reply(self, thread_id: int):
        """Explicitly cancels the auto-reply task if it exists (for human overrides)."""
        state = self.get_state(thread_id)
        if state and state.get("auto_reply_task"):
            state["auto_reply_task"].cancel()
            state["auto_reply_task"] = None
            logger.debug(f"Cancelled auto-reply task for thread {thread_id}.")

    def set_auto_reply_task(self, thread_id: int, task):
        """Stores the asyncio Task for the auto-reply countdown."""
        state = self.get_state(thread_id)
        if state:
            state["auto_reply_task"] = task

    def cleanup_thread(self, thread_id: int):
        """Removes a thread from state tracking."""
        if thread_id in self.pending_replies:
            del self.pending_replies[thread_id]
            logger.debug(f"Thread {thread_id} cleaned up from state.")

    def get_messages_data(self) -> list:
        """Returns a JSON-serializable list of all tracked messages."""
        result = []
        for thread_id, data in self.pending_replies.items():
            try:
                author = data.get("source_author")
                message = data.get("source_message")
                channel = data.get("source_channel")
                guild_name = ""
                if message and message.guild:
                    guild_name = message.guild.name
                result.append({
                    "thread_id": str(thread_id),
                    "author": author.name if author else "Unknown",
                    "author_id": str(author.id) if author else "",
                    "content": message.content if message else "",
                    "channel": channel.name if channel and hasattr(channel, 'name') else "DM",
                    "guild": guild_name,
                    "resolved": data.get("resolved", False),
                    "timestamp": message.created_at.isoformat() if message else "",
                })
            except Exception as e:
                logger.warning(f"Could not serialize thread {thread_id}: {e}")
        return result

# Global instance for easy access across modules
state = StateManager()
