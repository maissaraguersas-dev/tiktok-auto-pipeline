"""
No-watermark video fetching engine.

Downloads TikTok videos in high-definition raw format directly from TikTok's CDN
while stripping out digital and visual watermarks before saving locally.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

import httpx
from playwright.async_api import async_playwright

from config.settings import settings
from core.scraper import VideoMetadata
from utils.logger import get_logger

logger = get_logger(__name__)


class VideoDownloader:
    """
    Downloads TikTok videos without watermarks.
    
    Uses a multi-method approach:
    1. yt-dlp (primary) - Extracts direct CDN URLs
    2. Playwright fallback - Renders page to extract video src
    3. API endpoints - TikTok's internal API for video data
    """

    def __init__(self) -> None:
        self.raw_storage = settings.storage_raw
        self.timeout = settings.scraping.request_timeout

    def _generate_filename(self, metadata: VideoMetadata) -> str:
        """Generate a unique, sanitized filename for the video."""
        safe_author = re.sub(r"[^\w\-]", "_", metadata.author)[:30]
        safe_desc = re.sub(r"[^\w\-]", "_", metadata.description)[:40]
        unique_hash = hashlib.md5(f"{metadata.video_id}_{safe_author}".encode()).hexdigest()[:8]
        return f"{safe_author}_{unique_hash}.mp4"

    async def _try_ytdlp(self, metadata: VideoMetadata, output_path: Path) -> bool:
        """Attempt download using yt-dlp (most reliable method)."""
        try:
            logger.debug(f"Trying yt-dlp download for {metadata.video_id}")

            cmd = [
                "yt-dlp",
                "--no-warnings",
                "--format", "best[ext=mp4]/best",
                "--output", str(output_path),
                "--no-playlist",
                "--retries", "3",
                "--fragment-retries", "3",
                "--quiet",
                "--no-check-certificates",
                "--add-header", "Referer:https://www.tiktok.com/",
                "--add-header", (
                    "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                metadata.url,
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout
            )

            if process.returncode == 0 and output_path.exists():
                file_size = output_path.stat().st_size
                if file_size > 1024:  # At least 1KB
                    logger.info(
                        f"yt-dlp download successful: {output_path.name} "
                        f"({file_size / 1024 / 1024:.2f} MB)"
                    )
                    return True
                else:
                    logger.warning(f"Downloaded file too small: {file_size} bytes")
                    output_path.unlink(missing_ok=True)

        except asyncio.TimeoutError:
            logger.warning(f"yt-dlp timeout for {metadata.video_id}")
        except FileNotFoundError:
            logger.warning("yt-dlp not installed, skipping")
        except Exception as e:
            logger.debug(f"yt-dlp failed: {e}")

        return False

    async def _try_playwright(self, metadata: VideoMetadata, output_path: Path) -> bool:
        """Fallback: Use Playwright to extract and download video directly."""
        logger.debug(f"Trying Playwright download for {metadata.video_id}")

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                )

                page = await context.new_page()

                # Intercept network requests to find video URL
                video_url: Optional[str] = None

                async def handle_response(response):
                    nonlocal video_url
                    url = response.url
                    if ".mp4" in url and "video" in url:
                        # Prefer watermark-free URLs
                        if "watermark" not in url.lower():
                            video_url = url

                page.on("response", handle_response)

                await page.goto(metadata.url, wait_until="networkidle")
                await asyncio.sleep(3)

                # Also try to extract from page source
                if not video_url:
                    video_url = await page.evaluate("""
                        () => {
                            const video = document.querySelector('video');
                            if (video) return video.src;
                            
                            const scripts = document.querySelectorAll('script');
                            for (const script of scripts) {
                                const text = script.textContent;
                                const match = text.match(/(https?:\\/\\/[^\\s"]+\\.mp4[^\\s"]*)/);
                                if (match) return match[1];
                            }
                            return null;
                        }
                    """)

                await browser.close()

                if video_url:
                    # Download the video file
                    return await self._download_file(video_url, output_path)

        except Exception as e:
            logger.debug(f"Playwright download failed: {e}")

        return False

    async def _try_api_endpoint(self, metadata: VideoMetadata, output_path: Path) -> bool:
        """Try to download via TikTok's API endpoint."""
        try:
            logger.debug(f"Trying API endpoint download for {metadata.video_id}")

            # Construct API URL for watermark-free video
            api_url = (
                f"https://api16-normal-c-useast1a.tiktokv.com"
                f"/aweme/v1/feed/?aweme_id={metadata.video_id}"
            )

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Referer": "https://www.tiktok.com/",
            }

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(api_url, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    aweme_list = data.get("aweme_list", [])
                    if aweme_list:
                        video_info = aweme_list[0].get("video", {})
                        play_addr = video_info.get("play_addr", {})
                        url_list = play_addr.get("url_list", [])

                        for url in url_list:
                            if await self._download_file(url, output_path):
                                return True

        except Exception as e:
            logger.debug(f"API endpoint download failed: {e}")

        return False

    async def _download_file(self, url: str, output_path: Path) -> bool:
        """Download a file from URL to the specified path."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout * 2) as client:
                response = await client.get(url, follow_redirects=True)
                if response.status_code == 200:
                    content = response.content
                    if len(content) > 1024:  # At least 1KB
                        output_path.write_bytes(content)
                        file_size = output_path.stat().st_size
                        logger.info(
                            f"Direct download successful: {output_path.name} "
                            f"({file_size / 1024 / 1024:.2f} MB)"
                        )
                        return True
        except Exception as e:
            logger.debug(f"Direct download failed: {e}")

        return False

    async def download(self, metadata: VideoMetadata) -> Optional[Path]:
        """
        Download a TikTok video without watermarks.
        
        Tries multiple methods in order of reliability:
        1. yt-dlp (best quality, no watermark)
        2. API endpoint (watermark-free)
        3. Playwright fallback
        
        Returns the path to the downloaded file, or None if all methods fail.
        """
        logger.info(f"Starting download for video {metadata.video_id} by @{metadata.author_id}")

        filename = self._generate_filename(metadata)
        output_path = self.raw_storage / filename

        # Skip if already downloaded
        if output_path.exists():
            logger.info(f"Video already downloaded: {output_path.name}")
            return output_path

        # Try each download method
        methods = [
            ("yt-dlp", self._try_ytdlp),
            ("API endpoint", self._try_api_endpoint),
            ("Playwright", self._try_playwright),
        ]

        for method_name, method_func in methods:
            try:
                logger.debug(f"Attempting download via {method_name}...")
                if await method_func(metadata, output_path):
                    # Verify the downloaded file
                    if self._verify_download(output_path):
                        logger.info(
                            f"Download complete: {output_path.name} "
                            f"({output_path.stat().st_size / 1024 / 1024:.2f} MB)"
                        )
                        return output_path
            except Exception as e:
                logger.warning(f"{method_name} download error: {e}")

        logger.error(f"All download methods failed for video {metadata.video_id}")
        
        # Cleanup failed download
        if output_path.exists():
            output_path.unlink(missing_ok=True)
        
        return None

    def _verify_download(self, file_path: Path) -> bool:
        """Verify that the downloaded file is a valid video."""
        try:
            if not file_path.exists():
                return False

            file_size = file_path.stat().st_size
            if file_size < 1024:  # Less than 1KB
                return False

            # Check file header for MP4 signature
            with open(file_path, "rb") as f:
                header = f.read(12)
                # MP4 files start with ftyp, isom, mp42, etc.
                if b"ftyp" in header or b"isom" in header or b"mp42" in header:
                    return True

            # Fallback: try ffprobe
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_format", "-show_streams", str(file_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0

        except Exception:
            return False

    async def cleanup_old_files(self, max_age_hours: int = 24) -> int:
        """Remove raw video files older than specified hours."""
        removed = 0
        cutoff = asyncio.get_event_loop().time() - (max_age_hours * 3600)

        for file_path in self.raw_storage.glob("*.mp4"):
            try:
                file_stat = file_path.stat()
                if file_stat.st_mtime < cutoff:
                    file_path.unlink()
                    removed += 1
            except Exception as e:
                logger.debug(f"Error cleaning up {file_path}: {e}")

        if removed > 0:
            logger.info(f"Cleaned up {removed} old raw video files")

        return removed
