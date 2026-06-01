import sqlite3

DB_NAME = "discord_agent.db"


def get_connection():
    return sqlite3.connect(DB_NAME)


def initialize_database():
    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS discord_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            channel_id INTEGER UNIQUE NOT NULL,
            guild_id INTEGER,
            guild_name TEXT,
            active INTEGER DEFAULT 1
        )
    """)

    conn.commit()
    conn.close()