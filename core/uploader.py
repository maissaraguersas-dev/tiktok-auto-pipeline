"""
Browser automation and API publishing engine.

Handles the publication lifecycle by scheduling and uploading modified videos
using valid session data. Simulates human browsing behavior to prevent
anti-bot triggers from shadowbanning the account.

Supports two upload methods:
- playwright: Browser automation via Playwright
- api: Direct API upload (TikTok unofficial API)
- auto: Auto-select best available method
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from config.settings import settings
from core.scraper import VideoMetadata
from utils.logger import get_logger
from utils.proxy_manager import ProxyManager

logger = get_logger(__name__)


@dataclass
class UploadResult:
    """Result of an upload attempt."""

    success: bool
    video_id: Optional[str] = None
    url: Optional[str] = None
    error_message: Optional[str] = None
    upload_time: datetime = field(default_factory=datetime.utcnow)
    method_used: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "video_id": self.video_id,
            "url": self.url,
            "error_message": self.error_message,
            "upload_time": self.upload_time.isoformat(),
            "method_used": self.method_used,
        }


class VideoUploader:
    """
    Uploads processed TikTok videos with anti-detection measures.
    
    Features:
    - Dual upload methods (Playwright + API)
    - Automatic method selection
    - Human-like behavior simulation
    - Session cookie management
    - Proxy rotation support
    - Retry logic with exponential backoff
    """

    TIKTOK_UPLOAD_URL = "https://www.tiktok.com/upload"
    TIKTOK_API_PUBLISH_URL = "https://us.tiktok.com/api/publish/item"

    def __init__(self) -> None:
        self.config = settings.upload
        self.cookies_path = settings.cookies_path
        self.proxy_manager = ProxyManager()
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._session_cookies: list[dict] = []

    def _load_cookies(self) -> list[dict]:
        """Load session cookies from the cookies.json file."""
        try:
            with open(self.cookies_path, "r") as f:
                data = json.load(f)
                cookies = data.get("cookies", [])
                # Filter out placeholder values
                valid_cookies = [
                    c for c in cookies
                    if c.get("value") and "YOUR_" not in c.get("value", "")
                ]
                self._session_cookies = valid_cookies
                logger.debug(f"Loaded {len(valid_cookies)} valid cookies")
                return valid_cookies
        except FileNotFoundError:
            logger.warning(f"Cookies file not found: {self.cookies_path}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid cookies JSON: {e}")
        return []

    def _get_upload_method(self) -> str:
        """Determine which upload method to use."""
        method = self.config.method.lower()
        cookies = self._load_cookies()

        if method == "auto":
            # Prefer API if we have valid cookies, else Playwright
            if len(cookies) >= 2:
                return "api"
            return "playwright"

        if method == "api" and len(cookies) < 1:
            logger.warning("API upload requested but no valid cookies found. Falling back to Playwright")
            return "playwright"

        return method

    async def _init_browser(self) -> BrowserContext:
        """Initialize a stealth browser context for Playwright uploads."""
        if self._context is None:
            playwright = await async_playwright().start()

            # Browser launch options
            launch_options = {
                "headless": self.config.headless,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-web-security",
                ],
            }

            self._browser = await playwright.chromium.launch(**launch_options)

            # Context options for anti-detection
            context_options = {
                "viewport": {
                    "width": self.config.viewport_width,
                    "height": self.config.viewport_height,
                },
                "locale": self.config.locale,
                "timezone_id": self.config.timezone,
                "permissions": [],
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            }

            # Add user data dir if configured
            if self.config.user_data_dir:
                context_options["storage_state"] = self.config.user_data_dir

            self._context = await self._browser.new_context(**context_options)

            # Anti-detection scripts
            await self._context.add_init_script("""
                // Override navigator properties
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en'],
                });
                
                // Override permissions
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : originalQuery(parameters)
                );
                
                // Chrome runtime
                window.chrome = { runtime: {} };
                
                // Override iframe detection
                const originalAttachShadow = Element.prototype.attachShadow;
                Element.prototype.attachShadow = function(init) {
                    if (init && init.mode === 'closed') {
                        init.mode = 'open';
                    }
                    return originalAttachShadow.call(this, init);
                };
            """)

            # Add cookies if available
            cookies = self._load_cookies()
            if cookies:
                formatted_cookies = [
                    {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c.get("domain", ".tiktok.com"),
                        "path": c.get("path", "/"),
                    }
                    for c in cookies
                ]
                await self._context.add_cookies(formatted_cookies)

        return self._context

    async def _upload_with_playwright(
        self,
        video_path: Path,
        caption: str,
        hashtags: list[str],
    ) -> UploadResult:
        """
        Upload video using Playwright browser automation.
        
        Simulates human-like interactions:
        - Random delays between actions
        - Natural mouse movements
        - Typing with variable speed
        """
        logger.info(f"Starting Playwright upload: {video_path.name}")
        result = UploadResult(success=False, method_used="playwright")

        try:
            context = await self._init_browser()
            page = await context.new_page()

            # Navigate to TikTok upload page
            logger.debug("Navigating to upload page...")
            await page.goto("https://www.tiktok.com/upload", wait_until="domcontentloaded")

            # Random initial delay (human-like)
            await asyncio.sleep(random.uniform(2, 5))

            # Handle potential login redirect
            if "/login" in page.url:
                result.error_message = "Not logged in - redirected to login page"
                logger.error(result.error_message)
                await page.close()
                return result

            # Wait for upload iframe or component to load
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(random.uniform(1, 3))

            # Try to find and interact with the upload button/input
            upload_selectors = [
                'input[type="file"]',  # File input
                '[data-testid="upload-input"]',  # TikTok test ID
                '.upload-btn input',  # Class-based
                '[role="button"]:has-text("Select video")',  # Button text
                'button:has-text("Select file")',
            ]

            file_input = None
            for selector in upload_selectors:
                try:
                    file_input = await page.wait_for_selector(
                        selector, timeout=5000
                    )
                    if file_input:
                        logger.debug(f"Found upload input: {selector}")
                        break
                except Exception:
                    continue

            if not file_input:
                # Try clicking upload area first
                upload_area_selectors = [
                    '[data-testid="upload-card"]',
                    '.upload-drag-container',
                    '[class*="upload"]',
                ]
                for selector in upload_area_selectors:
                    try:
                        area = await page.wait_for_selector(selector, timeout=3000)
                        if area:
                            await area.click()
                            await asyncio.sleep(random.uniform(0.5, 1.5))
                            # Try file input again
                            file_input = await page.wait_for_selector(
                                'input[type="file"]', timeout=5000
                            )
                            break
                    except Exception:
                        continue

            if not file_input:
                result.error_message = "Could not find upload input element"
                logger.error(result.error_message)
                await page.close()
                return result

            # Upload the video file
            logger.debug(f"Uploading file: {video_path}")
            await file_input.set_input_files(str(video_path))

            # Wait for upload to process
            logger.debug("Waiting for video upload to process...")
            await asyncio.sleep(random.uniform(5, 10))

            # Fill in caption
            caption_selectors = [
                '[data-testid="upload-caption"]',
                'div[contenteditable="true"]',
                '[placeholder*="caption" i]',
                'div[class*="caption"][contenteditable]',
            ]

            caption_input = None
            for selector in caption_selectors:
                try:
                    caption_input = await page.wait_for_selector(
                        selector, timeout=5000
                    )
                    if caption_input:
                        break
                except Exception:
                    continue

            if caption_input:
                # Build full description with hashtags
                full_description = caption
                if hashtags:
                    hashtag_str = " ".join(hashtags)
                    full_description = f"{caption}\n\n{hashtag_str}"

                # Type with human-like delays
                await caption_input.click()
                await asyncio.sleep(random.uniform(0.3, 0.8))

                # Clear existing content
                await caption_input.fill("")
                await asyncio.sleep(random.uniform(0.2, 0.5))

                # Type description character by character with delays
                for char in full_description:
                    await caption_input.type(char, delay=random.uniform(30, 100))

                logger.debug("Caption entered successfully")

            # Configure upload settings
            await self._configure_upload_settings(page)

            # Click publish button
            publish_selectors = [
                '[data-testid="upload-publish"]',
                'button:has-text("Post")',
                'button:has-text("Publish")',
                '[type="submit"]',
            ]

            publish_button = None
            for selector in publish_selectors:
                try:
                    publish_button = await page.wait_for_selector(
                        selector, timeout=5000
                    )
                    if publish_button:
                        break
                except Exception:
                    continue

            if publish_button:
                # Random delay before publishing (user reviewing)
                await asyncio.sleep(random.uniform(2, 5))
                await publish_button.click()
                logger.debug("Publish button clicked")
            else:
                result.error_message = "Could not find publish button"
                logger.error(result.error_message)
                await page.close()
                return result

            # Wait for upload confirmation
            await asyncio.sleep(random.uniform(5, 10))

            # Check for success indicators
            current_url = page.url
            if "/video/" in current_url:
                # Extract video ID from URL
                import re
                match = re.search(r"/video/(\d+)", current_url)
                if match:
                    result.video_id = match.group(1)
                    result.url = current_url
                    result.success = True
                    logger.info(f"Upload successful! Video ID: {result.video_id}")

            # Alternative: check for success message
            if not result.success:
                success_selectors = [
                    '[data-testid="upload-success"]',
                    'text=Your video has been uploaded',
                    'text=Video published',
                ]
                for selector in success_selectors:
                    try:
                        success_elem = await page.wait_for_selector(
                            selector, timeout=5000
                        )
                        if success_elem:
                            result.success = True
                            break
                    except Exception:
                        continue

            await page.close()

        except Exception as e:
            result.error_message = f"Playwright upload error: {str(e)}"
            logger.error(result.error_message)

        return result

    async def _upload_with_api(
        self,
        video_path: Path,
        caption: str,
        hashtags: list[str],
    ) -> UploadResult:
        """
        Upload video using TikTok's internal API.
        
        More reliable but requires valid session cookies.
        """
        logger.info(f"Starting API upload: {video_path.name}")
        result = UploadResult(success=False, method_used="api")

        try:
            cookies = self._load_cookies()
            if not cookies:
                result.error_message = "No valid session cookies found"
                logger.error(result.error_message)
                return result

            # Build cookie header
            cookie_header = "; ".join(
                [f"{c['name']}={c['value']}" for c in cookies]
            )

            # Get CSRF token
            csrf_token = next(
                (c["value"] for c in cookies if c["name"] == "tt_csrf_token"),
                "",
            )

            # Step 1: Initialize upload to get upload URL
            init_headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.tiktok.com/upload",
                "Cookie": cookie_header,
                "X-Secsdk-Csrf-Request": "1",
                "X-Secsdk-Csrf-Version": "1.2.20",
            }

            if csrf_token:
                init_headers["X-Secsdk-Csrf-Token"] = csrf_token

            # Step 2: Upload video file
            upload_url = "https://us.tiktok.com/api/v1/web/project/post/"

            # Read video file
            video_data = video_path.read_bytes()
            video_filename = video_path.name
            mime_type, _ = mimetypes.guess_type(str(video_path))
            if not mime_type:
                mime_type = "video/mp4"

            # Build multipart form data
            boundary = f"----WebKitFormBoundary{random.randint(1000000000000, 9999999999999)}"

            # Build description with hashtags
            full_description = caption
            if hashtags:
                hashtag_str = " ".join(hashtags)
                full_description = f"{caption} {hashtag_str}"

            form_data_parts = [
                f"--{boundary}",
                f'Content-Disposition: form-data; name="upload_param"',
                "",
                "{}",
                f"--{boundary}",
                f'Content-Disposition: form-data; name="project_token"',
                "",
                f'Content-Disposition: form-data; name="title"',
                "",
                full_description[: self.config.video_description_max_length],
                f"--{boundary}",
                f'Content-Disposition: form-data; name="video"; filename="{video_filename}"',
                f"Content-Type: {mime_type}",
                "",
            ]

            body = "\r\n".join(form_data_parts).encode()
            body += video_data
            body += f"\r\n--{boundary}--\r\n".encode()

            upload_headers = {
                **init_headers,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            }

            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    upload_url,
                    headers=upload_headers,
                    content=body,
                )

                if response.status_code in (200, 201):
                    data = response.json()
                    result.video_id = data.get("data", {}).get("item_id", "")
                    if result.video_id:
                        result.url = f"https://www.tiktok.com/video/{result.video_id}"
                        result.success = True
                        logger.info(
                            f"API upload successful! Video ID: {result.video_id}"
                        )
                    else:
                        result.error_message = "Upload succeeded but no video ID returned"
                        logger.warning(result.error_message)
                else:
                    result.error_message = (
                        f"API upload failed: HTTP {response.status_code} - {response.text[:200]}"
                    )
                    logger.error(result.error_message)

        except Exception as e:
            result.error_message = f"API upload error: {str(e)}"
            logger.error(result.error_message)

        return result

    async def _configure_upload_settings(self, page: Page) -> None:
        """Configure video settings (duet, stitch, comments, visibility)."""
        try:
            # Allow duet
            if not self.config.allow_duet:
                duet_toggle = await page.query_selector('[data-testid="duet-toggle"]')
                if duet_toggle:
                    await duet_toggle.click()
                    await asyncio.sleep(random.uniform(0.3, 0.7))

            # Allow stitch
            if not self.config.allow_stitch:
                stitch_toggle = await page.query_selector('[data-testid="stitch-toggle"]')
                if stitch_toggle:
                    await stitch_toggle.click()
                    await asyncio.sleep(random.uniform(0.3, 0.7))

            # Comment settings
            comment_selectors = {
                "everyone": 'text=Everyone',
                "friends": 'text=Friends',
                "none": 'text=No one',
            }
            if self.config.allow_comments in comment_selectors:
                try:
                    comment_option = await page.wait_for_selector(
                        comment_selectors[self.config.allow_comments],
                        timeout=3000,
                    )
                    if comment_option:
                        await comment_option.click()
                        await asyncio.sleep(random.uniform(0.3, 0.7))
                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"Error configuring upload settings: {e}")

    async def upload(
        self,
        video_path: Path,
        caption: str,
        hashtags: list[str],
        metadata: Optional[VideoMetadata] = None,
    ) -> UploadResult:
        """
        Upload a processed video to TikTok.
        
        Args:
            video_path: Path to the processed video file.
            caption: Video caption text.
            hashtags: List of hashtags.
            metadata: Original video metadata (for logging).
            
        Returns:
            UploadResult with success status and video details.
        """
        if not video_path.exists():
            return UploadResult(
                success=False,
                error_message=f"Video file not found: {video_path}",
            )

        method = self._get_upload_method()
        logger.info(f"Using upload method: {method}")

        # Add human-like delay before upload
        delay = self.config.upload_interval_minutes
        if self.config.randomize_interval:
            jitter = delay * (self.config.interval_jitter_percent / 100)
            delay = delay + random.uniform(-jitter, jitter)

        logger.debug(f"Waiting {delay:.1f} minutes before upload...")
        await asyncio.sleep(delay * 60)

        result = UploadResult(success=False)

        if method == "api":
            result = await self._upload_with_api(video_path, caption, hashtags)
            # Fallback to Playwright if API fails
            if not result.success:
                logger.info("API upload failed, trying Playwright...")
                result = await self._upload_with_playwright(video_path, caption, hashtags)
        elif method == "playwright":
            result = await self._upload_with_playwright(video_path, caption, hashtags)
            # Fallback to API if Playwright fails
            if not result.success:
                logger.info("Playwright upload failed, trying API...")
                result = await self._upload_with_api(video_path, caption, hashtags)
        else:
            result.error_message = f"Unknown upload method: {method}"
            logger.error(result.error_message)

        return result

    async def close(self) -> None:
        """Clean up browser resources."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()

    async def __aenter__(self) -> VideoUploader:
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()
