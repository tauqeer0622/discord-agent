import os
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

DB_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DB_DIR, "automation.db")

def get_connection() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Processed tweets table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            tweet_id TEXT PRIMARY KEY,
            author TEXT,
            content TEXT,
            matched_keyword TEXT,
            url TEXT,
            status TEXT DEFAULT 'scanned',
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Comments audit log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tweet_id TEXT,
            author TEXT,
            reply_text TEXT,
            mode TEXT,
            status TEXT,
            error_message TEXT,
            posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tweet_id) REFERENCES posts(tweet_id)
        )
    """)

    # System activity logs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT,
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Restricted users table (authors who disable replies or protect accounts)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS restricted_users (
            author TEXT PRIMARY KEY,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

def is_tweet_processed(tweet_id: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM posts WHERE tweet_id = ?", (tweet_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def add_restricted_user(author: str, reason: str = "replies_restricted"):
    clean = (author or "").strip().lower().replace("@", "")
    if not clean or clean == "unknown":
        return
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO restricted_users (author, reason, created_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
    """, (clean, reason))
    conn.commit()
    conn.close()

def is_user_restricted(author: str) -> bool:
    clean = (author or "").strip().lower().replace("@", "")
    if not clean:
        return False
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM restricted_users WHERE author = ?", (clean,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def save_post(tweet_id: str, author: str, content: str, matched_keyword: str, url: str, status: str = "scanned"):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO posts (tweet_id, author, content, matched_keyword, url, status, discovered_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (tweet_id, author, content, matched_keyword, url, status))
    conn.commit()
    conn.close()

def update_post_status(tweet_id: str, status: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE posts SET status = ? WHERE tweet_id = ?", (status, tweet_id))
    conn.commit()
    conn.close()

def save_comment(tweet_id: str, author: str, reply_text: str, mode: str, status: str, error_message: Optional[str] = None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO comments (tweet_id, author, reply_text, mode, status, error_message, posted_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (tweet_id, author, reply_text, mode, status, error_message))
    conn.commit()
    conn.close()

def get_daily_comment_count() -> int:
    conn = get_connection()
    cursor = conn.cursor()
    # Count comments within the last 24 hours that were actually live or dry run successes
    since = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        SELECT COUNT(*) as count FROM comments 
        WHERE status = 'success' AND posted_at >= ?
    """, (since,))
    row = cursor.fetchone()
    conn.close()
    return row["count"] if row else 0

def get_stats() -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) as total FROM posts")
    total_scanned = cursor.fetchone()["total"]

    since_today = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("SELECT COUNT(*) as today FROM comments WHERE status = 'success' AND posted_at >= ?", (since_today,))
    comments_today = cursor.fetchone()["today"]

    cursor.execute("SELECT COUNT(*) as live FROM comments WHERE mode = 'live' AND status = 'success'")
    live_comments = cursor.fetchone()["live"]

    cursor.execute("SELECT COUNT(*) as dry FROM comments WHERE mode = 'dry_run' AND status = 'success'")
    dry_comments = cursor.fetchone()["dry"]

    cursor.execute("SELECT COUNT(*) as failed FROM comments WHERE status = 'failed'")
    failed_comments = cursor.fetchone()["failed"]

    conn.close()
    return {
        "total_scanned": total_scanned,
        "comments_today": comments_today,
        "live_comments": live_comments,
        "dry_comments": dry_comments,
        "failed_comments": failed_comments,
    }

def get_recent_posts(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM posts ORDER BY discovered_at DESC LIMIT ?", (limit,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def get_recent_comments(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.*, p.content as original_tweet, p.url as tweet_url 
        FROM comments c 
        LEFT JOIN posts p ON c.tweet_id = p.tweet_id 
        ORDER BY c.posted_at DESC LIMIT ?
    """, (limit,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def add_log(level: str, message: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO logs (level, message, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)", (level, message))
    conn.commit()
    conn.close()

def get_recent_logs(limit: int = 60) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM logs ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def get_recent_reply_texts(limit: int = 50) -> List[str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT reply_text FROM comments WHERE status = 'success' ORDER BY posted_at DESC LIMIT ?", (limit,))
    rows = [row["reply_text"] for row in cursor.fetchall() if row["reply_text"]]
    conn.close()
    return rows

init_db()
