"""
Reddit Autonomous Growth Engine
Distributes daily market cards, macro catalysts, and Discord conversion funnels
across targeted financial and crypto subreddits with anti-spam pacing.
"""

from .config import config, RedditConfig
from .database import (
    add_log,
    record_post,
    get_recent_posts,
    get_recent_logs,
    get_stats,
)
from .browser_controller import controller, RedditBrowserController
from .engine import engine, RedditEngine

__all__ = [
    "config",
    "RedditConfig",
    "add_log",
    "record_post",
    "get_recent_posts",
    "get_recent_logs",
    "get_stats",
    "controller",
    "RedditBrowserController",
    "engine",
    "RedditEngine",
]
