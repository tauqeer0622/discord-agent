import logging
from database import get_connection

logger = logging.getLogger(__name__)


class ConfigManager:

    def get_all(self):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT label, channel_id, guild_id, guild_name, active
            FROM discord_channels
        """)

        rows = cursor.fetchall()
        conn.close()

        return [
            {
                "label": row[0],
                "channel_id": row[1],
                "guild_id": row[2],
                "guild_name": row[3],
                "active": bool(row[4]),
            }
            for row in rows
        ]

    def add(self, label, channel_id, guild_id=None, guild_name=None):

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT channel_id FROM discord_channels WHERE channel_id=?",
            (channel_id,)
        )

        if cursor.fetchone():
            conn.close()
            return False

        cursor.execute("""
            INSERT INTO discord_channels
            (label, channel_id, guild_id, guild_name, active)
            VALUES (?, ?, ?, ?, 1)
        """, (
            label,
            int(channel_id),
            guild_id,
            guild_name
        ))

        conn.commit()
        conn.close()

        logger.info(f"Added channel {channel_id}")

        return True

    def remove(self, channel_id):

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM discord_channels WHERE channel_id=?",
            (int(channel_id),)
        )

        deleted = cursor.rowcount

        conn.commit()
        conn.close()

        return deleted > 0

    def toggle(self, channel_id):

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT active
            FROM discord_channels
            WHERE channel_id=?
        """, (int(channel_id),))

        row = cursor.fetchone()

        if not row:
            conn.close()
            return None

        new_value = 0 if row[0] else 1

        cursor.execute("""
            UPDATE discord_channels
            SET active=?
            WHERE channel_id=?
        """, (
            new_value,
            int(channel_id)
        ))

        conn.commit()
        conn.close()

        return bool(new_value)

    def get_active_channel_ids(self):

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT channel_id
            FROM discord_channels
            WHERE active=1
        """)

        rows = cursor.fetchall()

        conn.close()

        return [int(row[0]) for row in rows]

    def active_count(self):
        return len(self.get_active_channel_ids())

    def inactive_count(self):

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*)
            FROM discord_channels
            WHERE active=0
        """)

        count = cursor.fetchone()[0]

        conn.close()

        return count


config_manager = ConfigManager()