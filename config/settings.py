"""
Centralized settings management for TikTok Auto Pipeline.

All sensitive values are loaded from environment variables.
Provides sensible defaults for non-sensitive configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env file if present
load_dotenv()

# ── Base Paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
STORAGE_RAW: Path = PROJECT_ROOT / "storage" / "raw"
STORAGE_PROCESSED: Path = PROJECT_ROOT / "storage" / "processed"
COOKIES_PATH: Path = PROJECT_ROOT / "config" / "cookies.json"
LOGS_PATH: Path = PROJECT_ROOT / "logs"

# Ensure directories exist
STORAGE_RAW.mkdir(parents=True, exist_ok=True)
STORAGE_PROCESSED.mkdir(parents=True, exist_ok=True)
LOGS_PATH.mkdir(parents=True, exist_ok=True)


# ── Database Configuration ─────────────────────────────────────────────────
@dataclass(frozen=True)
class DatabaseConfig:
    """Database connection parameters."""

    provider: str = os.getenv("DB_PROVIDER", "sqlite")  # sqlite | postgresql
    sqlite_path: Path = PROJECT_ROOT / os.getenv("SQLITE_DB_NAME", "tiktok_pipeline.db")
    postgres_host: str = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    postgres_user: str = os.getenv("POSTGRES_USER", "")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "")
    postgres_db: str = os.getenv("POSTGRES_DB", "tiktok_pipeline")

    @property
    def connection_string(self) -> str:
        if self.provider.lower() == "postgresql":
            return (
                f"postgresql://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return f"sqlite:///{self.sqlite_path}"


# ── Scraping Configuration ─────────────────────────────────────────────────
@dataclass(frozen=True)
class ScrapingConfig:
    """Trend discovery and content filtering parameters."""

    # Virality thresholds
    min_views: int = int(os.getenv("MIN_VIEWS", "100000"))
    min_views_timeframe_hours: int = int(os.getenv("MIN_VIEWS_TIMEFRAME_HOURS", "24"))
    min_like_to_view_ratio: float = float(os.getenv("MIN_LIKE_TO_VIEW_RATIO", "0.05"))
    min_shares: int = int(os.getenv("MIN_SHARES", "500"))

    # Search scope
    trending_hashtags_count: int = int(os.getenv("TRENDING_HASHTAGS_COUNT", "10"))
    target_creators: list[str] = field(default_factory=list)
    max_videos_per_run: int = int(os.getenv("MAX_VIDEOS_PER_RUN", "5"))

    # Rate limiting
    requests_per_minute: int = int(os.getenv("REQUESTS_PER_MINUTE", "30"))
    request_timeout: int = int(os.getenv("REQUEST_TIMEOUT", "30"))

    def __post_init__(self):
        creators_env = os.getenv("TARGET_CREATORS", "")
        if creators_env:
            object.__setattr__(
                self, "target_creators", [c.strip() for c in creators_env.split(",")]
            )


# ── Video Processing Configuration ─────────────────────────────────────────
@dataclass(frozen=True)
class ProcessingConfig:
    """AI video mutation and alteration parameters."""

    # Speed alteration (subtle, to avoid detection)
    speed_factor: float = float(os.getenv("SPEED_FACTOR", "1.01"))
    speed_variation: float = float(os.getenv("SPEED_VARIATION", "0.005"))

    # Visual mutations
    mirror_probability: float = float(os.getenv("MIRROR_PROBABILITY", "0.5"))
    color_adjustment_range: float = float(os.getenv("COLOR_ADJUSTMENT_RANGE", "0.02"))
    crop_pixels: int = int(os.getenv("CROP_PIXELS", "2"))
    brightness_adjustment: float = float(os.getenv("BRIGHTNESS_ADJUSTMENT", "0.01"))
    contrast_adjustment: float = float(os.getenv("CONTRAST_ADJUSTMENT", "0.01"))
    saturation_adjustment: float = float(os.getenv("SATURATION_ADJUSTMENT", "0.01"))

    # Frame rate
    target_fps: int = int(os.getenv("TARGET_FPS", "30"))
    fps_variation: int = int(os.getenv("FPS_VARIATION", "1"))

    # Output
    output_format: str = os.getenv("OUTPUT_FORMAT", "mp4")
    output_quality: str = os.getenv("OUTPUT_QUALITY", "veryfast")
    output_crf: int = int(os.getenv("OUTPUT_CRF", "23"))

    # Audio
    audio_bitrate: str = os.getenv("AUDIO_BITRATE", "128k")
    audio_sample_rate: int = int(os.getenv("AUDIO_SAMPLE_RATE", "44100"))


# ── AI / LLM Configuration ─────────────────────────────────────────────────
@dataclass(frozen=True)
class AIConfig:
    """Generative AI integration parameters."""

    provider: str = os.getenv("AI_PROVIDER", "openai")  # openai | anthropic
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")

    # Generation parameters
    max_tokens: int = int(os.getenv("AI_MAX_TOKENS", "300"))
    temperature: float = float(os.getenv("AI_TEMPERATURE", "0.8"))
    top_p: float = float(os.getenv("AI_TOP_P", "0.95"))

    # Prompt templates
    caption_system_prompt: str = (
        "You are a TikTok viral content expert. Generate engaging, high-retention captions "
        "that drive engagement. Use hooks, emotional triggers, and call-to-actions. "
        "Keep captions concise (under 150 characters)."
    )
    hashtag_system_prompt: str = (
        "You are a TikTok SEO specialist. Generate 5-8 highly relevant, trending hashtags "
        "that maximize discoverability. Mix broad and niche tags. "
        "Format as space-separated hashtags with # prefix."
    )


# ── Upload Configuration ───────────────────────────────────────────────────
@dataclass(frozen=True)
class UploadConfig:
    """Publication and browser automation parameters."""

    # Upload method: playwright | api | auto
    method: str = os.getenv("UPLOAD_METHOD", "auto")

    # Scheduling
    upload_interval_minutes: int = int(os.getenv("UPLOAD_INTERVAL_MINUTES", "60"))
    randomize_interval: bool = os.getenv("RANDOMIZE_INTERVAL", "true").lower() == "true"
    interval_jitter_percent: int = int(os.getenv("INTERVAL_JITTER_PERCENT", "20"))

    # Browser settings (for Playwright)
    headless: bool = os.getenv("HEADLESS", "true").lower() == "true"
    browser_type: str = os.getenv("BROWSER_TYPE", "chromium")
    user_data_dir: Optional[str] = os.getenv("USER_DATA_DIR")

    # Anti-detection
    viewport_width: int = int(os.getenv("VIEWPORT_WIDTH", "1280"))
    viewport_height: int = int(os.getenv("VIEWPORT_HEIGHT", "720"))
    locale: str = os.getenv("LOCALE", "en-US")
    timezone: str = os.getenv("TIMEZONE", "America/New_York")

    # Video settings
    video_description_max_length: int = int(os.getenv("VIDEO_DESCRIPTION_MAX_LENGTH", "2200"))
    allow_duet: bool = os.getenv("ALLOW_DUET", "true").lower() == "true"
    allow_stitch: bool = os.getenv("ALLOW_STITCH", "true").lower() == "true"
    allow_comments: str = os.getenv("ALLOW_COMMENTS", "everyone")  # everyone | friends | none
    visibility: str = os.getenv("VISIBILITY", "public")  # public | friends | private


# ── Proxy Configuration ────────────────────────────────────────────────────
@dataclass(frozen=True)
class ProxyConfig:
    """IP rotation and proxy management parameters."""

    enabled: bool = os.getenv("PROXY_ENABLED", "true").lower() == "true"
    provider: str = os.getenv("PROXY_PROVIDER", "rotating")  # rotating | static | custom
    proxy_list_url: Optional[str] = os.getenv("PROXY_LIST_URL")
    proxy_list_file: Optional[str] = os.getenv("PROXY_LIST_FILE")
    rotation_interval: int = int(os.getenv("PROXY_ROTATION_INTERVAL", "300"))
    max_failures: int = int(os.getenv("PROXY_MAX_FAILURES", "3"))
    verify_ssl: bool = os.getenv("PROXY_VERIFY_SSL", "true").lower() == "true"
    timeout: int = int(os.getenv("PROXY_TIMEOUT", "10"))

    # Proxy authentication (if using static/custom)
    proxy_username: Optional[str] = os.getenv("PROXY_USERNAME")
    proxy_password: Optional[str] = os.getenv("PROXY_PASSWORD")
    proxy_host: Optional[str] = os.getenv("PROXY_HOST")
    proxy_port: Optional[int] = int(os.getenv("PROXY_PORT", "0")) or None


# ── Application Settings ───────────────────────────────────────────────────
@dataclass(frozen=True)
class Settings:
    """Consolidated application settings."""

    app_name: str = "TikTok Auto Pipeline"
    app_version: str = "1.0.0"
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # Sub-configs
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    scraping: ScrapingConfig = field(default_factory=ScrapingConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    upload: UploadConfig = field(default_factory=UploadConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)

    # Paths
    project_root: Path = PROJECT_ROOT
    storage_raw: Path = STORAGE_RAW
    storage_processed: Path = STORAGE_PROCESSED
    cookies_path: Path = COOKIES_PATH
    logs_path: Path = LOGS_PATH


# Global settings instance
settings = Settings()
