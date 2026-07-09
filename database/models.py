"""
Database models for TikTok Auto Pipeline.

Defines schemas for:
- Tracked Videos: Processed and uploaded video records
- Pipeline Logs: System execution logs and diagnostics
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class VideoStatus(str, enum.Enum):
    """Processing status for tracked videos."""

    DISCOVERED = "discovered"  # Found by scraper
    DOWNLOADED = "downloaded"  # Raw video saved
    PROCESSING = "processing"  # AI mutations applied
    PROCESSED = "processed"  # Ready for upload
    UPLOADING = "uploading"  # Upload in progress
    PUBLISHED = "published"  # Successfully uploaded
    FAILED = "failed"  # Processing/upload failed
    SKIPPED = "skipped"  # Deduplicated or filtered out


class LogLevel(str, enum.Enum):
    """Log severity levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class TrackedVideo(Base):
    """
    Represents a video tracked through the pipeline.
    
    Tracks the video from discovery through publication,
    storing metadata, processing history, and upload status.
    """

    __tablename__ = "tracked_videos"

    # Primary identifiers
    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(String(50), unique=True, nullable=False, index=True)
    original_url = Column(String(500), nullable=False)

    # Source metadata
    source_author = Column(String(100), nullable=False)
    source_author_id = Column(String(100), nullable=False)
    original_description = Column(Text, default="")
    original_hashtags = Column(JSON, default=list)

    # Engagement metrics (at time of discovery)
    view_count = Column(Integer, default=0)
    like_count = Column(Integer, default=0)
    share_count = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    engagement_score = Column(Float, default=0.0)

    # Processing info
    status = Column(
        Enum(VideoStatus),
        default=VideoStatus.DISCOVERED,
        nullable=False,
        index=True,
    )

    # File paths
    raw_file_path = Column(String(500), nullable=True)
    processed_file_path = Column(String(500), nullable=True)

    # AI-generated content
    generated_caption = Column(Text, default="")
    generated_hashtags = Column(JSON, default=list)

    # Upload results
    upload_url = Column(String(500), nullable=True)
    upload_video_id = Column(String(50), nullable=True)
    upload_success = Column(Boolean, default=False)
    upload_error = Column(Text, nullable=True)
    upload_method = Column(String(20), nullable=True)

    # Processing metadata
    original_md5 = Column(String(32), nullable=True)
    processed_md5 = Column(String(32), nullable=True)
    mutations_applied = Column(JSON, default=list)

    # Timestamps
    discovered_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    downloaded_at = Column(DateTime, nullable=True)
    processed_at = Column(DateTime, nullable=True)
    uploaded_at = Column(DateTime, nullable=True)

    # Additional metadata
    music_title = Column(String(200), default="")
    music_author = Column(String(200), default="")
    duration = Column(Integer, default=0)  # seconds
    is_verified_source = Column(Boolean, default=False)
    extra_metadata = Column(JSON, default=dict)  # Flexible storage

    def __repr__(self) -> str:
        return (
            f"<TrackedVideo(id={self.id}, video_id={self.video_id}, "
            f"status={self.status.value}, author={self.source_author})>"
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "video_id": self.video_id,
            "original_url": self.original_url,
            "source_author": self.source_author,
            "source_author_id": self.source_author_id,
            "original_description": self.original_description,
            "original_hashtags": self.original_hashtags,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "share_count": self.share_count,
            "comment_count": self.comment_count,
            "engagement_score": self.engagement_score,
            "status": self.status.value,
            "raw_file_path": self.raw_file_path,
            "processed_file_path": self.processed_file_path,
            "generated_caption": self.generated_caption,
            "generated_hashtags": self.generated_hashtags,
            "upload_url": self.upload_url,
            "upload_video_id": self.upload_video_id,
            "upload_success": self.upload_success,
            "upload_error": self.upload_error,
            "upload_method": self.upload_method,
            "original_md5": self.original_md5,
            "processed_md5": self.processed_md5,
            "mutations_applied": self.mutations_applied,
            "discovered_at": self.discovered_at.isoformat() if self.discovered_at else None,
            "downloaded_at": self.downloaded_at.isoformat() if self.downloaded_at else None,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
            "music_title": self.music_title,
            "music_author": self.music_author,
            "duration": self.duration,
            "is_verified_source": self.is_verified_source,
        }

    @classmethod
    def from_scraper_metadata(cls, metadata) -> "TrackedVideo":
        """Create a TrackedVideo instance from scraper metadata."""
        return cls(
            video_id=metadata.video_id,
            original_url=metadata.url,
            source_author=metadata.author,
            source_author_id=metadata.author_id,
            original_description=metadata.description,
            original_hashtags=metadata.hashtags,
            view_count=metadata.view_count,
            like_count=metadata.like_count,
            share_count=metadata.share_count,
            comment_count=metadata.comment_count,
            engagement_score=metadata.engagement_score,
            music_title=metadata.music_title,
            music_author=metadata.music_author,
            duration=metadata.duration,
            is_verified_source=metadata.is_verified,
            status=VideoStatus.DISCOVERED,
        )


class PipelineLog(Base):
    """
    System execution log entry.
    
    Records pipeline operations for monitoring, debugging,
    and performance analysis.
    """

    __tablename__ = "pipeline_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Log metadata
    level = Column(
        Enum(LogLevel),
        default=LogLevel.INFO,
        nullable=False,
        index=True,
    )
    phase = Column(String(50), nullable=False, index=True)  # e.g., discovery, download, processing, upload
    message = Column(Text, nullable=False)

    # Related video (if applicable)
    video_id = Column(String(50), nullable=True, index=True)

    # Execution context
    execution_id = Column(String(32), nullable=True, index=True)  # Pipeline run ID
    module = Column(String(100), nullable=True)  # Source module name
    function = Column(String(100), nullable=True)  # Source function name

    # Timing
    duration_ms = Column(Integer, nullable=True)  # Operation duration in milliseconds
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Additional context
    extra_data = Column(JSON, default=dict)  # Structured log data
    error_traceback = Column(Text, nullable=True)  # Full traceback for errors

    def __repr__(self) -> str:
        return (
            f"<PipelineLog(id={self.id}, level={self.level.value}, "
            f"phase={self.phase}, video_id={self.video_id})>"
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "level": self.level.value,
            "phase": self.phase,
            "message": self.message,
            "video_id": self.video_id,
            "execution_id": self.execution_id,
            "module": self.module,
            "function": self.function,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "extra_data": self.extra_data,
            "error_traceback": self.error_traceback,
        }


class ProxyHealth(Base):
    """
    Proxy health tracking for the proxy rotation system.
    
    Monitors proxy performance and reliability.
    """

    __tablename__ = "proxy_health"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Proxy identification
    proxy_url = Column(String(500), nullable=False)
    proxy_type = Column(String(20), default="http")  # http, https, socks4, socks5

    # Health metrics
    is_active = Column(Boolean, default=True)
    success_count = Column(Integer, default=0)
    failure_count = Column(Integer, default=0)
    consecutive_failures = Column(Integer, default=0)
    average_response_ms = Column(Integer, nullable=True)

    # Last check
    last_used_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    last_failure_at = Column(DateTime, nullable=True)
    last_error_message = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.success_count / total

    def __repr__(self) -> str:
        return (
            f"<ProxyHealth(id={self.id}, proxy={self.proxy_url}, "
            f"active={self.is_active}, rate={self.success_rate:.2%})>"
        )
