import logging
import os

from database import is_turso_active, get_collection

logger = logging.getLogger(__name__)


class ConfigManager:
    @property
    def collection(self):
        try:
            return get_collection("discord_channels")
        except Exception:
            return None

    def get_all(self):
        if is_turso_active():
            try:
                import turso_database
                channels = turso_database.get_discord_channels()
                if channels:
                    return channels
            except Exception as exc:
                logger.debug("Turso get_discord_channels error: %s", exc)

        col = self.collection
        if col is not None:
            try:
                documents = col.find({}).sort("label", 1)
                return [
                    {
                        "label": document["label"],
                        "channel_id": document["channel_id"],
                        "guild_id": document.get("guild_id"),
                        "guild_name": document.get("guild_name"),
                        "active": bool(document.get("active", True)),
                    }
                    for document in documents
                ]
            except Exception:
                pass

        # Default fallback target channels
        from config import TARGET_CHANNELS
        return [
            {
                "label": c["label"],
                "channel_id": c["channel_id"],
                "guild_id": None,
                "guild_name": None,
                "active": True,
            }
            for c in TARGET_CHANNELS
        ]

    def add(self, label, channel_id, guild_id=None, guild_name=None):
        if is_turso_active():
            try:
                import turso_database
                turso_database.add_discord_channel(label, channel_id, guild_id, guild_name)
            except Exception as exc:
                logger.debug("Turso add_discord_channel error: %s", exc)

        col = self.collection
        if col is not None:
            try:
                col.insert_one(
                    {
                        "label": label,
                        "channel_id": int(channel_id),
                        "guild_id": int(guild_id) if guild_id is not None else None,
                        "guild_name": guild_name,
                        "active": True,
                    }
                )
            except Exception:
                return False

        logger.info("Added channel %s (%s)", label, channel_id)
        return True

    def remove(self, channel_id):
        removed = False
        if is_turso_active():
            try:
                import turso_database
                removed = turso_database.remove_discord_channel(channel_id)
            except Exception:
                pass

        col = self.collection
        if col is not None:
            try:
                res = col.delete_one({"channel_id": int(channel_id)})
                removed = removed or (res.deleted_count > 0)
            except Exception:
                pass

        return removed

    def toggle(self, channel_id):
        if is_turso_active():
            try:
                import turso_database
                res = turso_database.toggle_discord_channel(channel_id)
                if res is not None:
                    return res
            except Exception:
                pass

        col = self.collection
        if col is not None:
            try:
                document = col.find_one({"channel_id": int(channel_id)})
                if document:
                    new_value = not bool(document.get("active", True))
                    col.update_one(
                        {"channel_id": int(channel_id)},
                        {"$set": {"active": new_value}},
                    )
                    return new_value
            except Exception:
                pass
        return None

    def get_active_channel_ids(self):
        channels = self.get_all()
        return [int(c["channel_id"]) for c in channels if c.get("active", True)]

    def active_count(self):
        channels = self.get_all()
        return sum(1 for c in channels if c.get("active", True))

    def inactive_count(self):
        channels = self.get_all()
        return sum(1 for c in channels if not c.get("active", True))


config_manager = ConfigManager()
