"""
Centralized system logger.

Provides structured logging with multiple handlers:
- Console output (colored)
- Rotating file logs
- Optional database logging

Features:
- Log level configuration via environment
- Structured JSON logging for machine parsing
- Automatic log rotation
- Context tracking (execution IDs, video IDs)
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from config.settings import settings

# ── Constants ────────────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_FORMAT_DEBUG = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ANSI color codes
COLORS = {
    "DEBUG": "\033[36m",      # Cyan
    "INFO": "\033[32m",       # Green
    "WARNING": "\033[33m",    # Yellow
    "ERROR": "\033[31m",      # Red
    "CRITICAL": "\033[35m",   # Magenta
    "RESET": "\033[0m",
}


class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds colors to console output."""

    def __init__(self, fmt: str, datefmt: str, use_colors: bool = True):
        super().__init__(fmt, datefmt)
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        if self.use_colors:
            color = COLORS.get(record.levelname, COLORS["RESET"])
            reset = COLORS["RESET"]
            record.levelname = f"{color}{record.levelname}{reset}"
        return super().format(record)


class StructuredFormatter(logging.Formatter):
    """JSON formatter for machine-readable log output."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add extra fields if present
        if hasattr(record, "execution_id"):
            log_data["execution_id"] = record.execution_id
        if hasattr(record, "video_id"):
            log_data["video_id"] = record.video_id
        if hasattr(record, "phase"):
            log_data["phase"] = record.phase
        if hasattr(record, "extra_data"):
            log_data["extra"] = record.extra_data

        # Add exception info
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, default=str)


class ContextAdapter(logging.LoggerAdapter):
    """Logger adapter that adds contextual information to log records."""

    def __init__(
        self,
        logger: logging.Logger,
        execution_id: Optional[str] = None,
        video_id: Optional[str] = None,
        phase: Optional[str] = None,
    ):
        super().__init__(logger, {})
        self.execution_id = execution_id
        self.video_id = video_id
        self.phase = phase

    def process(self, msg: str, kwargs: Any) -> tuple[str, Any]:
        extra = kwargs.get("extra", {})
        if self.execution_id:
            extra["execution_id"] = self.execution_id
        if self.video_id:
            extra["video_id"] = self.video_id
        if self.phase:
            extra["phase"] = self.phase
        kwargs["extra"] = extra
        return msg, kwargs

    def with_video(self, video_id: str) -> "ContextAdapter":
        """Create a new adapter with video context."""
        return ContextAdapter(
            self.logger,
            execution_id=self.execution_id,
            video_id=video_id,
            phase=self.phase,
        )

    def with_phase(self, phase: str) -> "ContextAdapter":
        """Create a new adapter with phase context."""
        return ContextAdapter(
            self.logger,
            execution_id=self.execution_id,
            video_id=self.video_id,
            phase=phase,
        )


# ── Logger Setup ─────────────────────────────────────────────────────────────

def setup_logging() -> None:
    """Configure the root logger with handlers."""
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    is_debug = settings.debug or log_level == logging.DEBUG

    # Create logs directory
    logs_dir = settings.logs_path
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers
    root_logger.handlers = []

    # Console handler (colored)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_format = LOG_FORMAT_DEBUG if is_debug else LOG_FORMAT
    console_handler.setFormatter(
        ColoredFormatter(console_format, DATE_FORMAT, use_colors=True)
    )
    root_logger.addHandler(console_handler)

    # File handler (plain text, rotating)
    app_log_path = logs_dir / "pipeline.log"
    file_handler = logging.handlers.RotatingFileHandler(
        app_log_path,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT_DEBUG, DATE_FORMAT))
    root_logger.addHandler(file_handler)

    # JSON file handler (for machine parsing)
    json_log_path = logs_dir / "pipeline.json.log"
    json_handler = logging.handlers.RotatingFileHandler(
        json_log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    json_handler.setLevel(logging.DEBUG)
    json_handler.setFormatter(StructuredFormatter())
    root_logger.addHandler(json_handler)

    # Error file handler (errors only)
    error_log_path = logs_dir / "errors.log"
    error_handler = logging.handlers.RotatingFileHandler(
        error_log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(logging.Formatter(LOG_FORMAT_DEBUG, DATE_FORMAT))
    root_logger.addHandler(error_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    logging.getLogger("pipeline").info(
        f"Logging initialized: level={settings.log_level}, debug={is_debug}"
    )


def get_logger(
    name: str,
    execution_id: Optional[str] = None,
    video_id: Optional[str] = None,
    phase: Optional[str] = None,
) -> ContextAdapter:
    """
    Get a logger with optional context.
    
    Args:
        name: Logger name (typically __name__)
        execution_id: Pipeline execution ID for tracking
        video_id: Related video ID for context
        phase: Pipeline phase (discovery, download, processing, upload)
        
    Returns:
        ContextAdapter with contextual logging support
    """
    logger = logging.getLogger(name)
    return ContextAdapter(logger, execution_id, video_id, phase)


# Initialize logging on module import
setup_logging()
