import os
from typing import List, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

class AppConfig(BaseModel):
    # Company Profile
    company_name: str = Field(default_factory=lambda: os.getenv("COMPANY_NAME", "ApexTrading"))
    company_one_liner: str = Field(default_factory=lambda: os.getenv("COMPANY_ONE_LINER", "Next-gen AI trading terminal for crypto & equities."))
    company_cta: str = Field(default_factory=lambda: os.getenv("COMPANY_CTA", "Check out our live market insights or DM us for early access!"))
    company_url: str = Field(default_factory=lambda: os.getenv("COMPANY_URL", "https://apextrading.ai"))

    # Keywords & Targeting
    target_keywords_raw: str = Field(default_factory=lambda: os.getenv("TARGET_KEYWORDS", "crypto, BTC, bitcoin, stocks, stock market, $BTC, $ETH, crypto trading"))
    negative_keywords_raw: str = Field(default_factory=lambda: os.getenv("NEGATIVE_KEYWORDS", "airdrop, giveaway, free coins, whatsapp, presale, claim free, 100x gem"))

    # Media & Image Attachment (2-hour cyclical image rotation)
    attach_image: bool = Field(default_factory=lambda: os.getenv("ATTACH_IMAGE", "true").lower() in ("true", "1", "yes"))
    media_image_path: str = Field(default_factory=lambda: os.getenv("MEDIA_IMAGE_PATH", "./media"))
    image_rotation_hours: float = Field(default_factory=lambda: float(os.getenv("IMAGE_ROTATION_HOURS", "2.0")))

    # AWS S3 Integration (Fetch JSON & dynamically create Market News card)
    s3_enabled: bool = Field(default_factory=lambda: os.getenv("S3_ENABLED", "true").lower() in ("true", "1", "yes"))
    aws_access_key_id: Optional[str] = Field(default_factory=lambda: os.getenv("AWS_ACCESS_KEY_ID", ""))
    aws_secret_access_key: Optional[str] = Field(default_factory=lambda: os.getenv("AWS_SECRET_ACCESS_KEY", ""))
    aws_region: str = Field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    s3_bucket_name: str = Field(default_factory=lambda: os.getenv("S3_BUCKET_NAME", ""))
    s3_json_key: str = Field(default_factory=lambda: os.getenv("S3_JSON_KEY", "latest_news.json"))
    s3_public_url: str = Field(default_factory=lambda: os.getenv("S3_PUBLIC_URL", ""))

    # AI Configuration
    openai_api_key: Optional[str] = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

    # Anti-Ban / Human Pacing & Rate Limits
    dry_run: bool = Field(default_factory=lambda: os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes"))
    auto_start: bool = Field(default_factory=lambda: os.getenv("AUTO_START", "true").lower() in ("true", "1", "yes"))
    min_delay_seconds: int = Field(default_factory=lambda: int(os.getenv("MIN_DELAY_SECONDS", "240")))
    max_delay_seconds: int = Field(default_factory=lambda: int(os.getenv("MAX_DELAY_SECONDS", "480")))
    daily_comment_limit: int = Field(default_factory=lambda: int(os.getenv("DAILY_COMMENT_LIMIT", "12")))
    session_comments_limit: int = Field(default_factory=lambda: int(os.getenv("SESSION_COMMENTS_LIMIT", "3")))
    session_cooldown_seconds: int = Field(default_factory=lambda: int(os.getenv("SESSION_COOLDOWN_SECONDS", "1200")))

    # Browser
    headless: bool = Field(default_factory=lambda: os.getenv("HEADLESS", "false").lower() in ("true", "1", "yes"))
    browser_data_dir: str = Field(default_factory=lambda: os.getenv("BROWSER_DATA_DIR", "./browser_data"))

    # Direct Auth Token (optional - bypass manual login)
    x_auth_token: Optional[str] = Field(default_factory=lambda: os.getenv("X_AUTH_TOKEN", ""))

    # Server
    web_host: str = Field(default_factory=lambda: os.getenv("WEB_HOST", "127.0.0.1"))
    web_port: int = Field(default_factory=lambda: int(os.getenv("WEB_PORT", "8000")))

    @property
    def target_keywords(self) -> List[str]:
        return [k.strip() for k in self.target_keywords_raw.split(",") if k.strip()]

    @property
    def negative_keywords(self) -> List[str]:
        return [k.strip().lower() for k in self.negative_keywords_raw.split(",") if k.strip()]


    def update_from_dict(self, data: dict):
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)

config = AppConfig()
