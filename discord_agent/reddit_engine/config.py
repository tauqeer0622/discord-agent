import os
from typing import List, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables across reddit_engine, discord_agent, and repository root
load_dotenv()
_local_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_local_env):
    load_dotenv(_local_env)
_parent_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_parent_env):
    load_dotenv(_parent_env)
_root_env = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
if os.path.exists(_root_env):
    load_dotenv(_root_env)


class RedditConfig(BaseModel):
    # Reddit Account & Authentication
    reddit_username: Optional[str] = Field(default_factory=lambda: os.getenv("REDDIT_USERNAME", ""))
    reddit_password: Optional[str] = Field(default_factory=lambda: os.getenv("REDDIT_PASSWORD", ""))

    # Target Subreddits for Market Infographics
    target_subreddits_raw: str = Field(
        default_factory=lambda: os.getenv(
            "REDDIT_SUBREDDITS",
            "CryptoCurrency, wallstreetbets, stocks, Daytrading, Bitcoin, options"
        )
    )

    # Post Title Templates
    title_prefix: str = Field(
        default_factory=lambda: os.getenv("REDDIT_TITLE_PREFIX", "[Market Movers & Key Events]")
    )
    title_template: str = Field(
        default_factory=lambda: os.getenv(
            "REDDIT_TITLE_TEMPLATE",
            "{prefix} Daily Macro Overview, Economic Catalysts & Whale Liquidity Levels"
        )
    )

    # First Comment Breakdown & Discord CTA
    first_comment_cta: str = Field(
        default_factory=lambda: os.getenv(
            "REDDIT_FIRST_COMMENT_CTA",
            "**Key Market Takeaways for Today:**\n\n"
            "- Major economic catalysts & volatility thresholds mapped on the attached card.\n"
            "- Watch whale liquidity absorption levels at critical support/resistance.\n\n"
            "📊 *We track these market-moving catalysts live with real-time on-chain alerts & charts:* "
            "[Join our VIP Discord Server](https://discord.gg/4av5x7Faa3)"
        )
    )
    discord_invite_url: str = Field(
        default_factory=lambda: os.getenv("REDDIT_DISCORD_INVITE", "https://discord.gg/4av5x7Faa3")
    )

    # Post Scheduling & Anti-Spam Safety
    post_delay_between_subs: int = Field(
        default_factory=lambda: int(os.getenv("REDDIT_DELAY_BETWEEN_SUBS", "1800")) # 30 mins between subs
    )
    daily_post_limit: int = Field(
        default_factory=lambda: int(os.getenv("REDDIT_DAILY_POST_LIMIT", "4"))
    )
    dry_run: bool = Field(
        default_factory=lambda: os.getenv("REDDIT_DRY_RUN", "false").lower() in ("true", "1", "yes")
    )
    auto_start: bool = Field(
        default_factory=lambda: os.getenv("REDDIT_AUTO_START", "false").lower() in ("true", "1", "yes")
    )

    # Browser Execution
    headless: bool = Field(
        default_factory=lambda: True if os.name != "nt" else (os.getenv("REDDIT_HEADLESS", "false").lower() in ("true", "1", "yes"))
    )
    browser_data_dir: str = Field(
        default_factory=lambda: os.getenv("REDDIT_BROWSER_DATA_DIR", "./reddit_engine/browser_data")
    )

    # Media Source Directory
    media_image_path: str = Field(
        default_factory=lambda: os.getenv("REDDIT_MEDIA_PATH", "../x_engine/media")
    )

    @property
    def target_subreddits(self) -> List[str]:
        return [s.strip().replace("r/", "") for s in self.target_subreddits_raw.split(",") if s.strip()]


config = RedditConfig()
