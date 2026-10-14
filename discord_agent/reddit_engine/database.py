import os
import sqlite3
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reddit_data.db")


def _get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initializes tables for Reddit post history and operational logs."""
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reddit_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subreddit TEXT NOT NULL,
                title TEXT NOT NULL,
                image_path TEXT,
                post_url TEXT,
                mode TEXT DEFAULT 'live',
                status TEXT NOT NULL,
                first_comment_status TEXT DEFAULT 'none',
                error_message TEXT,
                posted_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reddit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL
            )
        """)
        conn.commit()


# Initialize on import
init_db()


def add_log(level: str, message: str):
    """Appends an operational log entry."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with _get_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO reddit_logs (timestamp, level, message) VALUES (?, ?, ?)",
            (now, level.upper(), message)
        )
        conn.commit()


def record_post(
    subreddit: str,
    title: str,
    image_path: Optional[str],
    post_url: Optional[str],
    mode: str,
    status: str,
    first_comment_status: str = "none",
    error_message: Optional[str] = None
) -> int:
    """Records a submitted Reddit post in the audit trail."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reddit_posts (
                subreddit, title, image_path, post_url, mode, status,
                first_comment_status, error_message, posted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            subreddit, title, image_path, post_url, mode, status,
            first_comment_status, error_message, now
        ))
        conn.commit()
        return cursor.lastrowid


def get_recent_posts(limit: int = 50) -> List[Dict[str, Any]]:
    """Returns recent submitted posts."""
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM reddit_posts ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]


def get_recent_logs(limit: int = 100) -> List[Dict[str, Any]]:
    """Returns recent log entries."""
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM reddit_logs ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]


def get_stats() -> Dict[str, Any]:
    """Returns total post and success metrics."""
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM reddit_posts")
        total_posts = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM reddit_posts WHERE status = 'success'")
        successful_posts = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM reddit_posts WHERE status = 'failed'")
        failed_posts = cursor.fetchone()[0]

        return {
            "total_posts": total_posts,
            "successful_posts": successful_posts,
            "failed_posts": failed_posts
        }
