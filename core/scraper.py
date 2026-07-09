"""
Trend discovery and video metadata harvesting module.

Monitors TikTok to discover viral content by extracting critical metadata
including view velocity, like-to-view ratios, share counts, and upload timestamps.
Filters results based on configurable engagement thresholds.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import quote, urlencode

import httpx
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class VideoMetadata:
    """Structured metadata for a discovered TikTok video."""

    video_id: str
    url: str
    author: str
    author_id: str
    description: str
    hashtags: list[str] = field(default_factory=list)
    created_at: Optional[datetime] = None
    view_count: int = 0
    like_count: int = 0
    share_count: int = 0
    comment_count: int = 0
    duration: int = 0
    music_title: str = ""
    music_author: str = ""
    is_verified: bool = False
    engagement_score: float = 0.0
    processed: bool = False

    @property
    def like_to_view_ratio(self) -> float:
        if self.view_count == 0:
            return 0.0
        return self.like_count / self.view_count

    @property
    def view_velocity(self) -> float:
        if not self.created_at:
            return 0.0
        hours_since_upload = max(
            (datetime.utcnow() - self.created_at).total_seconds() / 3600, 0.1
        )
        return self.view_count / hours_since_upload

    def meets_thresholds(self) -> bool:
        if self.view_count < settings.scraping.min_views:
            return False
        if self.like_to_view_ratio < settings.scraping.min_like_to_view_ratio:
            return False
        if self.share_count < settings.scraping.min_shares:
            return False
        if self.created_at:
            age_hours = (datetime.utcnow() - self.created_at).total_seconds() / 3600
            if age_hours > settings.scraping.min_views_timeframe_hours:
                return False
        return True

    def generate_id(self) -> str:
        unique_string = f"{self.video_id}_{self.author_id}"
        return hashlib.sha256(unique_string.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "url": self.url,
            "author": self.author,
            "author_id": self.author_id,
            "description": self.description,
            "hashtags": self.hashtags,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "share_count": self.share_count,
            "comment_count": self.comment_count,
            "duration": self.duration,
            "music_title": self.music_title,
            "music_author": self.music_author,
            "is_verified": self.is_verified,
            "engagement_score": self.engagement_score,
        }


class TrendScraper:
    """
    Scrapes TikTok for trending content based on engagement metrics.
    
    Uses a hybrid approach: yt-dlp for metadata extraction where possible,
    with Playwright as fallback for JavaScript-rendered pages.
    """

    def __init__(self) -> None:
        self.config = settings.scraping
        self._client: Optional[httpx.AsyncClient] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.config.request_timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.tiktok.com/",
                },
                follow_redirects=True,
            )
        return self._client

    async def _init_browser(self) -> BrowserContext:
        if self._context is None:
            playwright = await async_playwright().start()
            self._browser = await playwright.chromium.launch(
                headless=settings.upload.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="America/New_York",
            )
            # Inject anti-detection script
            await self._context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                window.chrome = { runtime: {} };
            """)
        return self._context

    async def discover_trending_hashtags(self) -> list[str]:
        """Fetch currently trending hashtags from TikTok."""
        logger.info("Discovering trending hashtags...")
        hashtags = []

        try:
            context = await self._init_browser()
            page = await context.new_page()

            await page.goto("https://www.tiktok.com/discover", wait_until="networkidle")
            await asyncio.sleep(random.uniform(2, 4))

            # Extract trending hashtags from the discover page
            hashtag_elements = await page.query_selector_all('a[href*="/tag/"]')
            for elem in hashtag_elements[: self.config.trending_hashtags_count * 2]:
                href = await elem.get_attribute("href")
                if href:
                    match = re.search(r"/tag/([^?/]+)", href)
                    if match:
                        tag = match.group(1)
                        if tag not in hashtags:
                            hashtags.append(tag)

            await page.close()

        except Exception as e:
            logger.error(f"Error discovering trending hashtags: {e}")
            # Fallback to default hashtags
            hashtags = ["viral", "trending", "fyp", "foryou", "trend"]

        logger.info(f"Discovered {len(hashtags[:self.config.trending_hashtags_count])} trending hashtags")
        return hashtags[: self.config.trending_hashtags_count]

    async def scrape_hashtag(self, hashtag: str, max_results: int = 10) -> list[VideoMetadata]:
        """Scrape videos from a specific hashtag page."""
        logger.info(f"Scraping hashtag: #{hashtag}")
        videos = []

        try:
            context = await self._init_browser()
            page = await context.new_page()

            encoded_tag = quote(hashtag)
            url = f"https://www.tiktok.com/tag/{encoded_tag}"
            await page.goto(url, wait_until="networkidle")
            await asyncio.sleep(random.uniform(3, 5))

            # Extract video data from the page
            video_links = await page.query_selector_all('a[href*="/video/"]')
            processed_ids = set()

            for link in video_links[:max_results * 2]:
                href = await link.get_attribute("href")
                if not href:
                    continue

                # Extract video ID from URL
                match = re.search(r"/video/(\d+)", href)
                if not match:
                    continue

                video_id = match.group(1)
                if video_id in processed_ids:
                    continue
                processed_ids.add(video_id)

                metadata = await self._extract_video_metadata(page, href, video_id)
                if metadata and metadata.meets_thresholds():
                    videos.append(metadata)

                if len(videos) >= max_results:
                    break

                await asyncio.sleep(random.uniform(0.5, 1.5))

            await page.close()

        except Exception as e:
            logger.error(f"Error scraping hashtag #{hashtag}: {e}")

        logger.info(f"Found {len(videos)} videos meeting thresholds from #{hashtag}")
        return videos

    async def scrape_creator(self, creator_username: str, max_results: int = 10) -> list[VideoMetadata]:
        """Scrape videos from a specific creator's profile."""
        logger.info(f"Scraping creator: @{creator_username}")
        videos = []

        try:
            context = await self._init_browser()
            page = await context.new_page()

            url = f"https://www.tiktok.com/@{creator_username}"
            await page.goto(url, wait_until="networkidle")
            await asyncio.sleep(random.uniform(3, 5))

            video_links = await page.query_selector_all('a[href*="/video/"]')
            processed_ids = set()

            for link in video_links[:max_results * 2]:
                href = await link.get_attribute("href")
                if not href:
                    continue

                match = re.search(r"/video/(\d+)", href)
                if not match:
                    continue

                video_id = match.group(1)
                if video_id in processed_ids:
                    continue
                processed_ids.add(video_id)

                metadata = await self._extract_video_metadata(page, href, video_id)
                if metadata and metadata.meets_thresholds():
                    videos.append(metadata)

                if len(videos) >= max_results:
                    break

                await asyncio.sleep(random.uniform(0.5, 1.5))

            await page.close()

        except Exception as e:
            logger.error(f"Error scraping creator @{creator_username}: {e}")

        logger.info(f"Found {len(videos)} videos meeting thresholds from @{creator_username}")
        return videos

    async def _extract_video_metadata(self, page: Page, url: str, video_id: str) -> Optional[VideoMetadata]:
        """Extract detailed metadata for a single video."""
        try:
            # Navigate to video page
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2, 3))

            # Extract data from embedded JSON or meta tags
            meta_data = await page.evaluate("""
                () => {
                    const data = {};
                    
                    // Try to get from SSR data
                    const ssrData = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
                    if (ssrData) {
                        try {
                            const parsed = JSON.parse(ssrData.textContent);
                            const videoDetail = parsed['__DEFAULT_SCOPE__']?.['webapp.video-detail'];
                            if (videoDetail?.itemInfo) {
                                return videoDetail.itemInfo;
                            }
                        } catch(e) {}
                    }
                    
                    // Fallback: extract from meta tags
                    const metaTags = document.querySelectorAll('meta[property^="og:"]');
                    metaTags.forEach(tag => {
                        data[tag.getAttribute('property')] = tag.getAttribute('content');
                    });
                    
                    return data;
                }
            """)

            # Parse the extracted data
            if isinstance(meta_data, dict):
                item_struct = meta_data.get("itemStruct", {})
                author = item_struct.get("author", {})
                stats = item_struct.get("stats", {})
                music = item_struct.get("music", {})

                # Parse create time
                create_time = item_struct.get("createTime", 0)
                created_at = None
                if create_time:
                    created_at = datetime.utcfromtimestamp(int(create_time))

                # Extract hashtags
                desc = item_struct.get("desc", "")
                hashtags = re.findall(r"#(\w+)", desc)
                clean_desc = re.sub(r"#\w+", "", desc).strip()

                metadata = VideoMetadata(
                    video_id=video_id,
                    url=url,
                    author=author.get("nickname", ""),
                    author_id=author.get("uniqueId", ""),
                    description=clean_desc,
                    hashtags=hashtags,
                    created_at=created_at,
                    view_count=int(stats.get("playCount", 0)),
                    like_count=int(stats.get("diggCount", 0)),
                    share_count=int(stats.get("shareCount", 0)),
                    comment_count=int(stats.get("commentCount", 0)),
                    duration=int(item_struct.get("video", {}).get("duration", 0)),
                    music_title=music.get("title", ""),
                    music_author=music.get("authorName", ""),
                    is_verified=author.get("verified", False),
                )

                # Calculate engagement score
                metadata.engagement_score = self._calculate_engagement_score(metadata)
                return metadata

        except Exception as e:
            logger.debug(f"Error extracting metadata for {video_id}: {e}")

        return None

    def _calculate_engagement_score(self, metadata: VideoMetadata) -> float:
        """Calculate a composite engagement score for ranking videos."""
        score = 0.0

        # View velocity (views per hour since upload)
        if metadata.created_at:
            hours = max((datetime.utcnow() - metadata.created_at).total_seconds() / 3600, 0.1)
            score += min(metadata.view_count / hours / 1000, 50)  # Cap at 50

        # Like-to-view ratio (higher is better)
        score += metadata.like_to_view_ratio * 100

        # Share count weight
        score += min(metadata.share_count / 100, 20)  # Cap at 20

        # Comment engagement
        if metadata.view_count > 0:
            comment_ratio = metadata.comment_count / metadata.view_count
            score += comment_ratio * 100

        # Verified bonus
        if metadata.is_verified:
            score *= 1.1

        return round(score, 2)

    async def discover_videos(self) -> list[VideoMetadata]:
        """
        Main discovery method. Scrapes trending hashtags and target creators
        to find videos meeting engagement thresholds.
        """
        logger.info("Starting video discovery phase...")
        all_videos: list[VideoMetadata] = []

        # Discover trending hashtags
        hashtags = await self.discover_trending_hashtags()

        # Scrape videos from trending hashtags
        for hashtag in hashtags:
            if len(all_videos) >= self.config.max_videos_per_run:
                break

            videos = await self.scrape_hashtag(
                hashtag,
                max_results=self.config.max_videos_per_run - len(all_videos),
            )
            all_videos.extend(videos)

            # Rate limiting
            await asyncio.sleep(random.uniform(3, 7))

        # Scrape target creators if configured
        for creator in self.config.target_creators:
            if len(all_videos) >= self.config.max_videos_per_run:
                break

            videos = await self.scrape_creator(
                creator,
                max_results=self.config.max_videos_per_run - len(all_videos),
            )
            all_videos.extend(videos)

            await asyncio.sleep(random.uniform(3, 7))

        # Sort by engagement score and deduplicate
        seen_ids = set()
        unique_videos = []
        for video in sorted(all_videos, key=lambda v: v.engagement_score, reverse=True):
            if video.video_id not in seen_ids:
                seen_ids.add(video.video_id)
                unique_videos.append(video)

        final_videos = unique_videos[: self.config.max_videos_per_run]
        logger.info(f"Discovery complete. Found {len(final_videos)} unique viral videos")

        return final_videos

    async def close(self) -> None:
        """Clean up resources."""
        if self._client:
            await self._client.aclose()
        if self._browser:
            await self._browser.close()

    async def __aenter__(self) -> TrendScraper:
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()
