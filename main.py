#!/usr/bin/env python3
"""
System orchestrator / Pipeline execution entry point.

Implements the 5-phase deterministic pipeline:
1. Discovery & Scrape - Find viral content
2. Deduplication Check - Prevent duplicate processing
3. Clean Download - Fetch watermark-free videos
4. AI Mutation & Metadata Generation - Alter + generate copy
5. Managed Publication & Cleanup - Upload and clean

Usage:
    python main.py                    # Run single pipeline
    python main.py --loop             # Run continuous loop
    python main.py --loop --interval 3600  # Loop every hour
    python main.py --stats            # Show database statistics
    python main.py --health           # Run health checks
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import signal
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import settings
from core.copywriter import AICopywriter
from core.downloader import VideoDownloader
from core.processor import VideoProcessor
from core.scraper import TrendScraper, VideoMetadata
from core.uploader import VideoUploader
from database.connection import DatabaseManager
from database.models import PipelineLog, TrackedVideo, VideoStatus
from utils.logger import get_logger
from utils.proxy_manager import ProxyManager

logger = get_logger(__name__)


class PipelineOrchestrator:
    """
    Orchestrates the TikTok automation pipeline.
    
    Manages the 5-phase execution flow with proper error handling,
    logging, and resource cleanup.
    """

    def __init__(self) -> None:
        self.execution_id = hashlib.md5(
            f"{time.time()}_{uuid.uuid4()}".encode()
        ).hexdigest()[:12]

        # Initialize components
        self.db = DatabaseManager()
        self.scraper = TrendScraper()
        self.downloader = VideoDownloader()
        self.processor = VideoProcessor()
        self.copywriter = AICopywriter()
        self.uploader = VideoUploader()
        self.proxy_manager = ProxyManager()

        # Pipeline state
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Create tables if not exist
        self.db.create_tables()

        logger.info(
            f"Pipeline orchestrator initialized | Execution: {self.execution_id}"
        )

    def _log_phase(
        self,
        phase: str,
        message: str,
        video_id: Optional[str] = None,
        level: str = "INFO",
        extra: Optional[dict] = None,
    ) -> None:
        """Log a pipeline phase with structured data."""
        log_fn = getattr(logger, level.lower())
        context = f"[{self.execution_id}] [{phase}]"
        if video_id:
            context += f" [{video_id}]"

        log_fn(f"{context} {message}")

        # Also persist to database
        try:
            from database.models import LogLevel
            with self.db.session() as session:
                log_entry = PipelineLog(
                    level=LogLevel(level.upper()),
                    phase=phase,
                    message=message,
                    video_id=video_id,
                    execution_id=self.execution_id,
                    extra_data=extra or {},
                )
                session.add(log_entry)
        except Exception as e:
            logger.debug(f"Failed to persist log: {e}")

    async def _is_duplicate(self, video_id: str) -> bool:
        """Check if a video has already been processed."""
        try:
            with self.db.session() as session:
                existing = (
                    session.query(TrackedVideo)
                    .filter(TrackedVideo.video_id == video_id)
                    .first()
                )
                return existing is not None
        except Exception as e:
            self._log_phase("dedup", f"Error checking duplicate: {e}", level="ERROR")
            # Safer to assume not duplicate than to skip
            return False

    async def _save_video(self, video: TrackedVideo) -> None:
        """Save a tracked video to the database."""
        try:
            with self.db.session() as session:
                session.add(video)
                session.commit()
        except Exception as e:
            self._log_phase(
                "database", f"Error saving video {video.video_id}: {e}", level="ERROR"
            )

    async def _update_video_status(
        self, video_id: str, status: VideoStatus, **kwargs
    ) -> None:
        """Update a video's status and additional fields."""
        try:
            with self.db.session() as session:
                video = (
                    session.query(TrackedVideo)
                    .filter(TrackedVideo.video_id == video_id)
                    .first()
                )
                if video:
                    video.status = status
                    for key, value in kwargs.items():
                        if hasattr(video, key):
                            setattr(video, key, value)
                    session.commit()
        except Exception as e:
            self._log_phase(
                "database",
                f"Error updating video {video_id}: {e}",
                level="ERROR",
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 1: Discovery & Scrape
    # ═══════════════════════════════════════════════════════════════════════════
    async def phase1_discover(self) -> list[VideoMetadata]:
        """Phase 1: Discover viral content."""
        self._log_phase("discovery", "Starting discovery phase...")
        start_time = time.time()

        try:
            videos = await self.scraper.discover_videos()
            elapsed = time.time() - start_time

            self._log_phase(
                "discovery",
                f"Discovered {len(videos)} viral videos in {elapsed:.1f}s",
                extra={"count": len(videos), "elapsed_ms": int(elapsed * 1000)},
            )

            return videos

        except Exception as e:
            self._log_phase("discovery", f"Discovery failed: {e}", level="ERROR")
            return []

    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 2: Deduplication Check
    # ═══════════════════════════════════════════════════════════════════════════
    async def phase2_deduplicate(
        self, videos: list[VideoMetadata]
    ) -> list[VideoMetadata]:
        """Phase 2: Filter out already-processed videos."""
        self._log_phase("dedup", f"Checking {len(videos)} videos for duplicates...")
        start_time = time.time()

        unique_videos = []
        duplicates = 0

        for video in videos:
            if await self._is_duplicate(video.video_id):
                duplicates += 1
                self._log_phase(
                    "dedup",
                    f"Video {video.video_id} already processed, skipping",
                    video_id=video.video_id,
                )
                continue
            unique_videos.append(video)

        elapsed = time.time() - start_time
        self._log_phase(
            "dedup",
            f"Filtered {duplicates} duplicates, {len(unique_videos)} unique remaining",
            extra={
                "total": len(videos),
                "duplicates": duplicates,
                "unique": len(unique_videos),
                "elapsed_ms": int(elapsed * 1000),
            },
        )

        return unique_videos

    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 3: Clean Download
    # ═══════════════════════════════════════════════════════════════════════════
    async def phase3_download(self, video: VideoMetadata) -> Optional[Path]:
        """Phase 3: Download video without watermark."""
        self._log_phase("download", f"Starting download", video_id=video.video_id)
        start_time = time.time()

        # Save to database first
        tracked = TrackedVideo.from_scraper_metadata(video)
        await self._save_video(tracked)

        try:
            downloaded_path = await self.downloader.download(video)
            elapsed = time.time() - start_time

            if downloaded_path:
                # Calculate MD5 of raw file
                raw_md5 = hashlib.md5(downloaded_path.read_bytes()).hexdigest()

                await self._update_video_status(
                    video.video_id,
                    VideoStatus.DOWNLOADED,
                    raw_file_path=str(downloaded_path),
                    original_md5=raw_md5,
                    downloaded_at=datetime.utcnow(),
                )

                self._log_phase(
                    "download",
                    f"Download complete: {downloaded_path.name} ({downloaded_path.stat().st_size / 1024 / 1024:.1f} MB)",
                    video_id=video.video_id,
                    extra={
                        "file": downloaded_path.name,
                        "size_bytes": downloaded_path.stat().st_size,
                        "md5": raw_md5,
                        "elapsed_ms": int(elapsed * 1000),
                    },
                )
                return downloaded_path
            else:
                await self._update_video_status(
                    video.video_id, VideoStatus.FAILED, upload_error="Download failed"
                )
                self._log_phase(
                    "download",
                    "Download failed",
                    video_id=video.video_id,
                    level="ERROR",
                )

        except Exception as e:
            await self._update_video_status(
                video.video_id, VideoStatus.FAILED, upload_error=str(e)
            )
            self._log_phase(
                "download",
                f"Download error: {e}",
                video_id=video.video_id,
                level="ERROR",
            )

        return None

    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 4: AI Mutation & Metadata Generation
    # ═══════════════════════════════════════════════════════════════════════════
    async def phase4_process(
        self, video: VideoMetadata, raw_path: Path
    ) -> tuple[Optional[Path], str, list[str]]:
        """
        Phase 4: Apply AI mutations and generate viral copy.
        
        Returns:
            Tuple of (processed_path, caption, hashtags)
        """
        self._log_phase("processing", "Starting AI mutation", video_id=video.video_id)
        start_time = time.time()

        try:
            # Update status
            await self._update_video_status(
                video.video_id,
                VideoStatus.PROCESSING,
                processed_at=datetime.utcnow(),
            )

            # Run processing and copywriting concurrently
            process_task = self.processor.process(raw_path)
            copy_task = self.copywriter.generate_full_description(video)

            processed_path = await process_task
            caption, hashtags = await copy_task

            elapsed = time.time() - start_time

            if processed_path:
                # Calculate new MD5
                processed_md5 = hashlib.md5(processed_path.read_bytes()).hexdigest()

                await self._update_video_status(
                    video.video_id,
                    VideoStatus.PROCESSED,
                    processed_file_path=str(processed_path),
                    processed_md5=processed_md5,
                    generated_caption=caption,
                    generated_hashtags=hashtags,
                )

                self._log_phase(
                    "processing",
                    f"Processing complete: {processed_path.name}",
                    video_id=video.video_id,
                    extra={
                        "file": processed_path.name,
                        "caption_preview": caption[:80] if caption else "",
                        "hashtags": hashtags,
                        "md5_changed": video.video_id != processed_md5,
                        "elapsed_ms": int(elapsed * 1000),
                    },
                )
                return processed_path, caption, hashtags
            else:
                await self._update_video_status(
                    video.video_id, VideoStatus.FAILED, upload_error="Processing failed"
                )
                self._log_phase(
                    "processing",
                    "Processing failed",
                    video_id=video.video_id,
                    level="ERROR",
                )

        except Exception as e:
            await self._update_video_status(
                video.video_id, VideoStatus.FAILED, upload_error=str(e)
            )
            self._log_phase(
                "processing",
                f"Processing error: {e}",
                video_id=video.video_id,
                level="ERROR",
            )

        return None, "", []

    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 5: Managed Publication & Cleanup
    # ═══════════════════════════════════════════════════════════════════════════
    async def phase5_publish(
        self,
        video: VideoMetadata,
        processed_path: Path,
        caption: str,
        hashtags: list[str],
    ) -> bool:
        """Phase 5: Upload video and cleanup."""
        self._log_phase("upload", "Starting publication", video_id=video.video_id)
        start_time = time.time()

        try:
            await self._update_video_status(
                video.video_id,
                VideoStatus.UPLOADING,
                uploaded_at=datetime.utcnow(),
            )

            result = await self.uploader.upload(
                video_path=processed_path,
                caption=caption,
                hashtags=hashtags,
                metadata=video,
            )

            elapsed = time.time() - start_time

            if result.success:
                await self._update_video_status(
                    video.video_id,
                    VideoStatus.PUBLISHED,
                    upload_url=result.url,
                    upload_video_id=result.video_id,
                    upload_success=True,
                    upload_method=result.method_used,
                )

                self._log_phase(
                    "upload",
                    f"Upload successful! URL: {result.url}",
                    video_id=video.video_id,
                    extra={
                        "upload_url": result.url,
                        "upload_video_id": result.video_id,
                        "method": result.method_used,
                        "elapsed_ms": int(elapsed * 1000),
                    },
                )

                # Cleanup files
                await self._cleanup(video.video_id, processed_path)
                return True
            else:
                error_msg = result.error_message or "Unknown upload error"
                await self._update_video_status(
                    video.video_id,
                    VideoStatus.FAILED,
                    upload_error=error_msg,
                    upload_method=result.method_used,
                )

                self._log_phase(
                    "upload",
                    f"Upload failed: {error_msg}",
                    video_id=video.video_id,
                    level="ERROR",
                    extra={
                        "method": result.method_used,
                        "elapsed_ms": int(elapsed * 1000),
                    },
                )

        except Exception as e:
            await self._update_video_status(
                video.video_id, VideoStatus.FAILED, upload_error=str(e)
            )
            self._log_phase(
                "upload",
                f"Upload error: {e}",
                video_id=video.video_id,
                level="ERROR",
            )

        return False

    async def _cleanup(self, video_id: str, processed_path: Path) -> None:
        """Clean up local files after successful upload."""
        self._log_phase("cleanup", "Removing local files", video_id=video_id)

        try:
            # Find raw file
            raw_file = None
            with self.db.session() as session:
                video = (
                    session.query(TrackedVideo)
                    .filter(TrackedVideo.video_id == video_id)
                    .first()
                )
                if video and video.raw_file_path:
                    raw_file = Path(video.raw_file_path)

            # Delete processed file
            if processed_path.exists():
                processed_path.unlink()
                self._log_phase(
                    "cleanup", f"Deleted processed file: {processed_path.name}", video_id=video_id
                )

            # Delete raw file
            if raw_file and raw_file.exists():
                raw_file.unlink()
                self._log_phase(
                    "cleanup", f"Deleted raw file: {raw_file.name}", video_id=video_id
                )

            self._log_phase("cleanup", "Cleanup complete", video_id=video_id)

        except Exception as e:
            self._log_phase(
                "cleanup",
                f"Cleanup error: {e}",
                video_id=video_id,
                level="WARNING",
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Pipeline Execution
    # ═══════════════════════════════════════════════════════════════════════════
    async def run_pipeline(self) -> dict:
        """
        Execute the full 5-phase pipeline.
        
        Returns:
            Pipeline execution statistics.
        """
        pipeline_start = time.time()
        self._log_phase("pipeline", f"=== Pipeline started | Execution: {self.execution_id} ===")

        stats = {
            "execution_id": self.execution_id,
            "started_at": datetime.utcnow().isoformat(),
            "discovered": 0,
            "unique": 0,
            "downloaded": 0,
            "processed": 0,
            "published": 0,
            "failed": 0,
            "elapsed_seconds": 0,
        }

        # Phase 1: Discovery
        videos = await self.phase1_discover()
        stats["discovered"] = len(videos)

        if not videos:
            self._log_phase("pipeline", "No videos discovered, ending pipeline")
            stats["elapsed_seconds"] = round(time.time() - pipeline_start, 2)
            return stats

        # Phase 2: Deduplication
        unique_videos = await self.phase2_deduplicate(videos)
        stats["unique"] = len(unique_videos)

        # Process each unique video
        for video in unique_videos:
            if self._shutdown_event.is_set():
                self._log_phase("pipeline", "Shutdown signal received, stopping")
                break

            video_start = time.time()
            self._log_phase(
                "pipeline",
                f"Processing video {video.video_id} by @{video.author_id}",
                video_id=video.video_id,
            )

            # Phase 3: Download
            raw_path = await self.phase3_download(video)
            if not raw_path:
                stats["failed"] += 1
                continue
            stats["downloaded"] += 1

            # Phase 4: Processing & Copywriting
            processed_path, caption, hashtags = await self.phase4_process(video, raw_path)
            if not processed_path:
                stats["failed"] += 1
                continue
            stats["processed"] += 1

            # Phase 5: Upload
            success = await self.phase5_publish(video, processed_path, caption, hashtags)
            if success:
                stats["published"] += 1
            else:
                stats["failed"] += 1

            video_elapsed = time.time() - video_start
            self._log_phase(
                "pipeline",
                f"Video complete in {video_elapsed:.1f}s | Published: {success}",
                video_id=video.video_id,
                extra={"video_elapsed_ms": int(video_elapsed * 1000)},
            )

        # Cleanup old files
        await self.downloader.cleanup_old_files()
        await self.processor.cleanup_old_files()

        stats["elapsed_seconds"] = round(time.time() - pipeline_start, 2)
        stats["finished_at"] = datetime.utcnow().isoformat()

        self._log_phase(
            "pipeline",
            f"=== Pipeline complete | Published: {stats['published']}/{stats['unique']} | "
            f"Time: {stats['elapsed_seconds']:.1f}s ===",
            extra=stats,
        )

        return stats

    async def run_continuous(self, interval_seconds: int = 3600) -> None:
        """
        Run the pipeline in a continuous loop.
        
        Args:
            interval_seconds: Seconds between pipeline runs.
        """
        self._running = True
        self._log_phase(
            "pipeline",
            f"Starting continuous mode (interval: {interval_seconds}s)",
        )

        while self._running and not self._shutdown_event.is_set():
            try:
                stats = await self.run_pipeline()

                # Wait for next run or shutdown
                self._log_phase(
                    "pipeline",
                    f"Next run in {interval_seconds}s (press Ctrl+C to stop)",
                )

                # Wait with shutdown check
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass  # Normal interval timeout, continue

            except Exception as e:
                self._log_phase(
                    "pipeline",
                    f"Pipeline error: {e}",
                    level="ERROR",
                )
                await asyncio.sleep(60)  # Wait before retry

    def shutdown(self) -> None:
        """Signal the pipeline to shut down gracefully."""
        self._log_phase("pipeline", "Shutdown signal received")
        self._running = False
        self._shutdown_event.set()

    async def close(self) -> None:
        """Clean up all resources."""
        await self.scraper.close()
        await self.copywriter.close()
        await self.uploader.close()
        self.db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

def create_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="TikTok Auto Pipeline - Automated viral content discovery and publishing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                           Run single pipeline execution
  %(prog)s --loop                    Run continuously (default: 1h interval)
  %(prog)s --loop --interval 1800    Run every 30 minutes
  %(prog)s --stats                   Show database statistics
  %(prog)s --health                  Run health checks
  %(prog)s --version                 Show version info
        """,
    )

    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run pipeline in continuous loop",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=3600,
        help="Loop interval in seconds (default: 3600)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show database statistics and exit",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Run health checks and exit",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {settings.app_version}",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose/debug logging",
    )

    return parser


async def show_stats() -> None:
    """Display database statistics."""
    db = DatabaseManager()
    db.create_tables()

    stats = db.get_stats()
    print("\n" + "=" * 60)
    print("TikTok Auto Pipeline - Database Statistics")
    print("=" * 60)
    print(f"Provider:        {stats['provider']}")
    print(f"Connection:      {stats['connection_string']}")
    print(f"Tracked Videos:  {stats.get('tracked_videos_count', 'N/A')}")
    print(f"Total Logs:      {stats.get('logs_count', 'N/A')}")
    print(f"Success Rate:    {stats.get('success_rate', 'N/A')}")
    print("=" * 60 + "\n")

    db.close()


async def run_health_checks() -> None:
    """Run system health checks."""
    print("\n" + "=" * 60)
    print("TikTok Auto Pipeline - Health Check")
    print("=" * 60)

    checks = {
        "database": False,
        "ffmpeg": False,
        "storage_raw": False,
        "storage_processed": False,
        "cookies": False,
        "ai_config": False,
    }

    # Database
    try:
        db = DatabaseManager()
        checks["database"] = db.health_check()
        db.close()
    except Exception as e:
        print(f"Database: FAILED ({e})")

    # FFmpeg
    try:
        import subprocess
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5,
        )
        checks["ffmpeg"] = result.returncode == 0
    except Exception:
        pass

    # Storage
    checks["storage_raw"] = settings.storage_raw.exists()
    checks["storage_processed"] = settings.storage_processed.exists()

    # Cookies
    try:
        import json
        with open(settings.cookies_path) as f:
            data = json.load(f)
            cookies = data.get("cookies", [])
            checks["cookies"] = any(
                c.get("value") and "YOUR_" not in c.get("value", "")
                for c in cookies
            )
    except Exception:
        pass

    # AI config
    checks["ai_config"] = bool(
        settings.ai.openai_api_key or settings.ai.anthropic_api_key
    )

    # Print results
    for check, passed in checks.items():
        status = "PASS" if passed else "FAIL"
        symbol = "\u2713" if passed else "\u2717"
        print(f"  [{symbol}] {check:20s} {status}")

    all_passed = all(checks.values())
    print("=" * 60)
    print(f"Overall: {'ALL CHECKS PASSED' if all_passed else 'SOME CHECKS FAILED'}")
    print("=" * 60 + "\n")


async def main() -> None:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    # Set verbose logging
    if args.verbose:
        import logging
        logging.getLogger().setLevel(logging.DEBUG)

    # Stats mode
    if args.stats:
        await show_stats()
        return

    # Health check mode
    if args.health:
        await run_health_checks()
        return

    # Normal pipeline execution
    orchestrator = PipelineOrchestrator()

    # Handle graceful shutdown
    def signal_handler(sig, frame):
        print("\nShutdown signal received, stopping gracefully...")
        orchestrator.shutdown()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        if args.loop:
            await orchestrator.run_continuous(args.interval)
        else:
            stats = await orchestrator.run_pipeline()
            print(f"\nPipeline complete:")
            print(f"  Discovered:  {stats['discovered']}")
            print(f"  Unique:      {stats['unique']}")
            print(f"  Downloaded:  {stats['downloaded']}")
            print(f"  Processed:   {stats['processed']}")
            print(f"  Published:   {stats['published']}")
            print(f"  Failed:      {stats['failed']}")
            print(f"  Time:        {stats['elapsed_seconds']:.1f}s")
    finally:
        await orchestrator.close()


if __name__ == "__main__":
    asyncio.run(main())
