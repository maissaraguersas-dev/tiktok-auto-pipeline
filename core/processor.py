"""
AI video mutation and frame alteration engine.

Programmatically alters videos to create unique digital footprints and bypass
"Duplicate Content" flagging. Uses FFmpeg for high-performance mutations.
"""

from __future__ import annotations

import hashlib
import logging
import random
import subprocess
from pathlib import Path
from typing import Optional

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class VideoProcessor:
    """
    Processes and mutates TikTok videos to create unique digital fingerprints.
    
    Mutations applied (subtle, imperceptible):
    - Horizontal mirroring (random)
    - Color grading adjustments (brightness, contrast, saturation)
    - Frame micro-cropping
    - Speed scaling (±1%)
    - FPS variation
    - Audio pitch shift
    - Container metadata rewrite
    """

    def __init__(self) -> None:
        self.config = settings.processing
        self.processed_storage = settings.storage_processed

    def _generate_output_filename(self, input_path: Path) -> str:
        """Generate a unique filename for the processed video."""
        original_stem = input_path.stem
        unique_hash = hashlib.md5(
            f"{original_stem}_{random.randint(1000, 9999)}".encode()
        ).hexdigest()[:8]
        return f"processed_{original_stem}_{unique_hash}.{self.config.output_format}"

    def _get_video_info(self, video_path: Path) -> dict:
        """Get video metadata using ffprobe."""
        try:
            cmd = [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(video_path),
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                import json
                return json.loads(result.stdout)
        except Exception as e:
            logger.warning(f"Could not get video info: {e}")
        return {}

    def _build_ffmpeg_command(self, input_path: Path, output_path: Path) -> list[str]:
        """
        Build an FFmpeg command with random mutations.
        
        Each processing run generates slightly different mutations,
        ensuring unique output even for the same input.
        """
        cmd = ["ffmpeg", "-y", "-i", str(input_path)]

        # ── Video Filters ─────────────────────────────────────────────────
        video_filters = []

        # 1. Speed alteration (subtle, ±variation around base factor)
        speed_variation = random.uniform(
            -self.config.speed_variation, self.config.speed_variation
        )
        speed_factor = self.config.speed_factor + speed_variation
        speed_factor = round(max(0.98, min(1.02, speed_factor)), 4)

        # 2. Horizontal mirroring (50% chance)
        mirror = random.random() < self.config.mirror_probability

        # 3. Color adjustments (subtle random variations)
        brightness = round(
            self.config.brightness_adjustment + random.uniform(-0.005, 0.005), 4
        )
        contrast = round(
            self.config.contrast_adjustment + random.uniform(-0.005, 0.005) + 1.0, 4
        )
        saturation = round(
            self.config.saturation_adjustment + random.uniform(-0.005, 0.005) + 1.0, 4
        )

        # 4. Micro-crop (remove a few pixels from edges)
        crop_pixels = random.randint(0, self.config.crop_pixels)

        # 5. FPS variation
        fps_variation = random.randint(-self.config.fps_variation, self.config.fps_variation)
        target_fps = max(24, self.config.target_fps + fps_variation)

        # Build filter chain
        filters = []

        # Speed adjustment using setpts
        filters.append(f"setpts=PTS/{speed_factor}")

        # FPS
        filters.append(f"fps={target_fps}")

        # Micro-crop if applicable
        if crop_pixels > 0:
            # We'll apply crop after getting dimensions, so use cropdetect approach
            # For now, use a percentage-based crop
            crop_percent = crop_pixels / 100  # e.g., 2px on a 1080p = ~0.2%
            filters.append(f"crop=iw*(1-{crop_percent}):ih*(1-{crop_percent})")

        # Mirror
        if mirror:
            filters.append("hflip")

        # Color adjustments
        filters.append(f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}")

        # Slight noise to change MD5 (very subtle)
        noise_strength = random.uniform(0.0001, 0.0005)
        filters.append(f"noise=alls={noise_strength}:allf=t+u")

        # Scale slightly to ensure encoding changes
        scale_factor = random.uniform(0.998, 1.002)
        filters.append(f"scale=iw*{scale_factor}:ih*{scale_factor}")

        # Combine all video filters
        video_filter_string = ",".join(filters)
        cmd.extend(["-vf", video_filter_string])

        # ── Audio Filters ─────────────────────────────────────────────────
        audio_filters = []

        # Audio tempo adjustment (must match video speed)
        audio_filters.append(f"atempo={1.0 / speed_factor}")

        # Slight pitch shift to alter audio fingerprint
        pitch_cents = random.uniform(-10, 10)  # -10 to +10 cents
        audio_filters.append(f"rubberband=pitch={pitch_cents / 100}")

        audio_filter_string = ",".join(audio_filters)
        cmd.extend(["-af", audio_filter_string])

        # ── Output Settings ───────────────────────────────────────────────
        # Video codec
        cmd.extend([
            "-c:v", "libx264",
            "-preset", self.config.output_quality,
            "-crf", str(self.config.output_crf),
            "-pix_fmt", "yuv420p",
        ])

        # Audio codec
        cmd.extend([
            "-c:a", "aac",
            "-b:a", self.config.audio_bitrate,
            "-ar", str(self.config.audio_sample_rate),
        ])

        # Container format and metadata
        cmd.extend([
            "-f", self.config.output_format,
            "-movflags", "+faststart",
        ])

        # Random metadata to ensure unique file hash
        random_uid = hashlib.md5(str(random.random()).encode()).hexdigest()
        cmd.extend([
            "-metadata", f"comment=processed_{random_uid}",
            "-metadata", "encoder=TikTokAutoPipeline",
            "-metadata", f"creation_time={random.randint(2020, 2024)}-01-01T00:00:00.000000Z",
        ])

        # Output file
        cmd.append(str(output_path))

        return cmd

    def process(self, input_path: Path) -> Optional[Path]:
        """
        Process a video with AI mutations to create a unique digital footprint.
        
        Args:
            input_path: Path to the raw downloaded video.
            
        Returns:
            Path to the processed video, or None if processing failed.
        """
        if not input_path.exists():
            logger.error(f"Input video not found: {input_path}")
            return None

        logger.info(f"Starting video processing: {input_path.name}")

        # Generate output path
        output_filename = self._generate_output_filename(input_path)
        output_path = self.processed_storage / output_filename

        # Skip if already processed
        if output_path.exists():
            logger.info(f"Video already processed: {output_filename}")
            return output_path

        # Get video info for logging
        video_info = self._get_video_info(input_path)
        if video_info:
            streams = video_info.get("streams", [])
            for stream in streams:
                if stream.get("codec_type") == "video":
                    width = stream.get("width", "?")
                    height = stream.get("height", "?")
                    duration = stream.get("duration", "?")
                    logger.debug(f"Input: {width}x{height}, duration: {duration}s")
                    break

        # Build and execute FFmpeg command
        cmd = self._build_ffmpeg_command(input_path, output_path)

        logger.debug(f"FFmpeg command: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            if result.returncode != 0:
                logger.error(f"FFmpeg error: {result.stderr}")
                if output_path.exists():
                    output_path.unlink(missing_ok=True)
                return None

            # Verify output
            if not output_path.exists():
                logger.error("Output file was not created")
                return None

            output_size = output_path.stat().st_size
            if output_size < 1024:
                logger.error(f"Output file too small: {output_size} bytes")
                output_path.unlink(missing_ok=True)
                return None

            # Calculate new MD5 hash
            new_md5 = hashlib.md5(output_path.read_bytes()).hexdigest()
            old_md5 = hashlib.md5(input_path.read_bytes()).hexdigest()

            logger.info(
                f"Processing complete: {output_filename}\n"
                f"  Original MD5: {old_md5[:16]}...\n"
                f"  New MD5:      {new_md5[:16]}...\n"
                f"  Size: {output_size / 1024 / 1024:.2f} MB"
            )

            return output_path

        except subprocess.TimeoutExpired:
            logger.error("FFmpeg processing timed out")
            if output_path.exists():
                output_path.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"Processing error: {e}")
            if output_path.exists():
                output_path.unlink(missing_ok=True)

        return None

    async def cleanup_old_files(self, max_age_hours: int = 24) -> int:
        """Remove processed video files older than specified hours."""
        import asyncio
        import time

        removed = 0
        cutoff = time.time() - (max_age_hours * 3600)

        for file_path in self.processed_storage.glob("*.mp4"):
            try:
                file_stat = file_path.stat()
                if file_stat.st_mtime < cutoff:
                    file_path.unlink()
                    removed += 1
            except Exception as e:
                logger.debug(f"Error cleaning up {file_path}: {e}")

        if removed > 0:
            logger.info(f"Cleaned up {removed} old processed video files")

        return removed

    def get_mutation_summary(self, input_path: Path, output_path: Path) -> dict:
        """Get a summary of mutations applied to a video."""
        input_md5 = hashlib.md5(input_path.read_bytes()).hexdigest()
        output_md5 = hashlib.md5(output_path.read_bytes()).hexdigest()

        return {
            "input_md5": input_md5,
            "output_md5": output_md5,
            "md5_changed": input_md5 != output_md5,
            "input_size": input_path.stat().st_size,
            "output_size": output_path.stat().st_size,
        }
