from database import initialize_database
from config_manager import config_manager

initialize_database()

for channel in config_manager.get_all():
    print(
        channel["label"],
        channel["channel_id"],
        channel.get("guild_name"),
        channel["active"],
    )
