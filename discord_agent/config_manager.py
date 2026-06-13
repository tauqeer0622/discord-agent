import logging

from pymongo.errors import DuplicateKeyError

from database import get_collection

logger = logging.getLogger(__name__)


class ConfigManager:
    @property
    def collection(self):
        return get_collection("discord_channels")

    def get_all(self):
        documents = self.collection.find({}).sort("label", 1)
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

    def add(self, label, channel_id, guild_id=None, guild_name=None):
        try:
            self.collection.insert_one(
                {
                    "label": label,
                    "channel_id": int(channel_id),
                    "guild_id": int(guild_id) if guild_id is not None else None,
                    "guild_name": guild_name,
                    "active": True,
                }
            )
        except DuplicateKeyError:
            return False

        logger.info("Added channel %s", channel_id)
        return True

    def remove(self, channel_id):
        result = self.collection.delete_one({"channel_id": int(channel_id)})
        return result.deleted_count > 0

    def toggle(self, channel_id):
        document = self.collection.find_one({"channel_id": int(channel_id)})
        if not document:
            return None

        new_value = not bool(document.get("active", True))
        self.collection.update_one(
            {"channel_id": int(channel_id)},
            {"$set": {"active": new_value}},
        )
        return new_value

    def get_active_channel_ids(self):
        documents = self.collection.find(
            {"active": True},
            {"channel_id": 1},
        )
        return [int(document["channel_id"]) for document in documents]

    def active_count(self):
        return self.collection.count_documents({"active": True})

    def inactive_count(self):
        return self.collection.count_documents({"active": False})


config_manager = ConfigManager()
