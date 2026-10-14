import os
import glob
import time
import random
import threading
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from .config import config
from .database import add_log, get_stats
from .browser_controller import controller


class RedditEngine:
    """
    Autonomous orchestration engine that distributes daily SpikeSignals market
    infographics across target financial subreddits with anti-spam pacing.
    """

    def __init__(self):
        self.state: str = "stopped"  # stopped | running | paused
        self.activity: str = "Idle"
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.posts_today: int = 0
        self.last_post_time: Optional[float] = None

    def get_status(self) -> Dict[str, Any]:
        """Returns real-time status of the Reddit automation engine."""
        return {
            "state": self.state,
            "activity": self.activity,
            "posts_today": self.posts_today,
            "daily_limit": config.daily_post_limit,
            "target_subreddits": config.target_subreddits,
            "dry_run": config.dry_run,
            "browser_active": controller.is_alive(),
        }

    def start(self):
        """Starts the background Reddit automation loop."""
        if self.state == "running":
            return
        self._stop_event.clear()
        self.state = "running"
        self.activity = "Starting"
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="RedditEngineWorker")
        self._thread.start()
        add_log("INFO", "Reddit growth engine worker thread launched.")

    def stop(self):
        """Stops the Reddit automation engine cleanly."""
        if self.state == "stopped":
            return
        self.state = "stopped"
        self.activity = "Stopping"
        self._stop_event.set()
        controller.stop()
        self.activity = "Idle"
        add_log("INFO", "Reddit growth engine stopped.")

    def _get_latest_market_card(self) -> Optional[str]:
        """Finds the most recently generated SpikeSignals infographic card."""
        media_dirs = [
            os.path.abspath(config.media_image_path),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "x_engine", "media"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "media"),
        ]

        for m_dir in media_dirs:
            if os.path.exists(m_dir):
                # Look for generated slot cards or templates
                cards = glob.glob(os.path.join(m_dir, "spikesignals_slot_*.jpg"))
                if not cards:
                    cards = glob.glob(os.path.join(m_dir, "*.jpg"))
                if cards:
                    # Return latest modified
                    cards.sort(key=os.path.getmtime, reverse=True)
                    return cards[0]
        return None

    def _format_post_title(self) -> str:
        """Constructs an engaging, educational market title with today's date."""
        today_str = datetime.now(timezone.utc).strftime("%b %d, %Y")
        title = config.title_template.format(
            prefix=config.title_prefix,
            date=today_str
        )
        return f"{title} — {today_str}"

    def post_single_subreddit(self, subreddit: str, dry_run: Optional[bool] = None) -> Dict[str, Any]:
        """Manually triggers a post to a single subreddit (used by dashboard or CLI)."""
        card_path = self._get_latest_market_card()
        if not card_path:
            return {"success": False, "message": "No market card found in media directories."}

        title = self._format_post_title()
        is_dry = config.dry_run if dry_run is None else dry_run

        add_log("INFO", f"Triggering manual post to r/{subreddit}: {title}")
        res = controller.post_image_to_subreddit(
            subreddit=subreddit,
            title=title,
            image_path=card_path,
            first_comment=config.first_comment_cta,
            dry_run=is_dry
        )
        if res.get("success"):
            self.posts_today += 1
            self.last_post_time = time.time()
        return res

    def _run_loop(self):
        """Main worker loop: publishes to target subreddits with safe spacing."""
        add_log("INFO", f"Reddit distribution cycle started. Target subreddits: {config.target_subreddits}")

        while not self._stop_event.is_set():
            try:
                # Check daily post limit
                if self.posts_today >= config.daily_post_limit:
                    self.activity = f"Daily limit reached ({self.posts_today}/{config.daily_post_limit}). Sleeping until next UTC day."
                    time.sleep(300)
                    continue

                card_path = self._get_latest_market_card()
                if not card_path:
                    self.activity = "Waiting for market card generation"
                    time.sleep(60)
                    continue

                title = self._format_post_title()

                # Iterate through target subreddits
                for sub in config.target_subreddits:
                    if self._stop_event.is_set():
                        break

                    self.activity = f"Preparing post for r/{sub}"
                    add_log("INFO", f"[Reddit Loop] Publishing daily card to r/{sub}...")

                    res = controller.post_image_to_subreddit(
                        subreddit=sub,
                        title=title,
                        image_path=card_path,
                        first_comment=config.first_comment_cta,
                        dry_run=config.dry_run
                    )

                    if res.get("success"):
                        self.posts_today += 1
                        self.last_post_time = time.time()
                        add_log("SUCCESS", f"r/{sub} post complete! ({self.posts_today}/{config.daily_post_limit} today)")
                    else:
                        add_log("WARN", f"r/{sub} post skipped or failed: {res.get('message')}")

                    # Delay between subreddits to comply with Reddit rate limits
                    delay = config.post_delay_between_subs
                    self.activity = f"Cooldown: waiting {delay}s before next subreddit post"
                    add_log("INFO", f"Pacing delay active: sleeping {delay}s before next subreddit.")

                    # Sleep in small slices to respond promptly to stop event
                    slept = 0
                    while slept < delay and not self._stop_event.is_set():
                        time.sleep(min(5, delay - slept))
                        slept += 5

                # Rest cycle after covering subreddits (check every 2 hours)
                self.activity = "Completed subreddit cycle. Resting until next scheduled window."
                for _ in range(120):
                    if self._stop_event.is_set():
                        break
                    time.sleep(60)

            except Exception as e:
                add_log("ERROR", f"Unhandled error in Reddit loop: {str(e)[:150]}")
                self.activity = f"Error: {str(e)[:50]}"
                time.sleep(60)

        self.activity = "Idle"
        self.state = "stopped"


engine = RedditEngine()
