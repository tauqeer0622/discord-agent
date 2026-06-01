from database import get_connection

conn = get_connection()
cursor = conn.cursor()

cursor.execute("""
SELECT label, channel_id, guild_name, active
FROM discord_channels
""")

for row in cursor.fetchall():
    print(row)

conn.close()