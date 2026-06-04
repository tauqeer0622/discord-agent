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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discord_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author TEXT NOT NULL,
            content TEXT NOT NULL,
            channel_name TEXT,
            guild_name TEXT,
            timestamp TEXT
        )
    """)

    conn.commit()
    conn.close()


def save_message(
    author,
    content,
    channel_name,
    guild_name,
    timestamp
):
    conn = get_connection()

    conn.execute(
        """
        INSERT INTO discord_messages
        (
            author,
            content,
            channel_name,
            guild_name,
            timestamp
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            author,
            content,
            channel_name,
            guild_name,
            timestamp
        )
    )

    conn.commit()
    conn.close()

def get_messages():
    conn = get_connection()

    cursor = conn.execute(
        """
        SELECT
            id,
            author,
            content,
            channel_name,
            guild_name,
            timestamp
        FROM discord_messages
        ORDER BY id DESC
        """
    )

    rows = cursor.fetchall()

    conn.close()

    return rows