"""
LLM integration for viral caption and hashtag generation.

Interfaces with generative AI models (OpenAI/Anthropic) to create unique,
high-retention titles, compelling hooks, and context-aware hashtags optimized
for the TikTok algorithm.
"""

from __future__ import annotations

import json
import re
from typing import Optional

import httpx

from config.settings import settings
from core.scraper import VideoMetadata
from utils.logger import get_logger

logger = get_logger(__name__)


class AICopywriter:
    """
    Generates viral-optimized captions and hashtags using LLMs.
    
    Supports multiple AI providers (OpenAI, Anthropic) with
    fallback and error handling.
    """

    def __init__(self) -> None:
        self.config = settings.ai
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    def _get_headers_openai(self) -> dict:
        return {
            "Authorization": f"Bearer {self.config.openai_api_key}",
            "Content-Type": "application/json",
        }

    def _get_headers_anthropic(self) -> dict:
        return {
            "x-api-key": self.config.anthropic_api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

    async def _call_openai(
        self, system_prompt: str, user_prompt: str
    ) -> Optional[str]:
        """Call OpenAI API for text generation."""
        if not self.config.openai_api_key:
            logger.warning("OpenAI API key not configured")
            return None

        try:
            client = self._get_client()
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=self._get_headers_openai(),
                json={
                    "model": self.config.openai_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": self.config.max_tokens,
                    "temperature": self.config.temperature,
                    "top_p": self.config.top_p,
                },
            )

            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return content.strip()
            else:
                logger.warning(f"OpenAI API error: {response.status_code} - {response.text}")

        except Exception as e:
            logger.error(f"OpenAI API call failed: {e}")

        return None

    async def _call_anthropic(
        self, system_prompt: str, user_prompt: str
    ) -> Optional[str]:
        """Call Anthropic API for text generation."""
        if not self.config.anthropic_api_key:
            logger.warning("Anthropic API key not configured")
            return None

        try:
            client = self._get_client()
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=self._get_headers_anthropic(),
                json={
                    "model": self.config.anthropic_model,
                    "max_tokens": self.config.max_tokens,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            )

            if response.status_code == 200:
                data = response.json()
                content = data["content"][0]["text"]
                return content.strip()
            else:
                logger.warning(
                    f"Anthropic API error: {response.status_code} - {response.text}"
                )

        except Exception as e:
            logger.error(f"Anthropic API call failed: {e}")

        return None

    async def _generate(
        self, system_prompt: str, user_prompt: str
    ) -> Optional[str]:
        """
        Generate text using configured AI provider with fallback.
        
        Priority:
        1. Configured primary provider
        2. Secondary provider (fallback)
        """
        result = None

        # Try primary provider
        if self.config.provider == "openai":
            result = await self._call_openai(system_prompt, user_prompt)
            if not result:
                logger.info("Falling back to Anthropic")
                result = await self._call_anthropic(system_prompt, user_prompt)
        elif self.config.provider == "anthropic":
            result = await self._call_anthropic(system_prompt, user_prompt)
            if not result:
                logger.info("Falling back to OpenAI")
                result = await self._call_openai(system_prompt, user_prompt)
        else:
            logger.error(f"Unknown AI provider: {self.config.provider}")
            # Try both
            result = await self._call_openai(system_prompt, user_prompt)
            if not result:
                result = await self._call_anthropic(system_prompt, user_prompt)

        return result

    async def generate_caption(self, metadata: VideoMetadata) -> str:
        """
        Generate a viral-optimized caption for a TikTok video.
        
        Args:
            metadata: Video metadata including description, hashtags, etc.
            
        Returns:
            Generated caption string.
        """
        logger.info(f"Generating caption for video {metadata.video_id}")

        # Build context from metadata
        context = self._build_context(metadata)

        user_prompt = f"""Create a viral TikTok caption based on this content:

Original description: {metadata.description}
Original hashtags: {' '.join(f'#{h}' for h in metadata.hashtags)}
Author: @{metadata.author_id}
Music: {metadata.music_title} by {metadata.music_author}
Views: {metadata.view_count:,}

Requirements:
- Under 150 characters (for the main hook)
- Include an emotional hook or curiosity gap
- Use power words that drive engagement
- Add a call-to-action (like "Wait for it..." or "This changed everything")
- Do NOT include hashtags in the caption (they'll be added separately)

Respond with ONLY the caption text, nothing else."""

        caption = await self._generate(
            self.config.caption_system_prompt,
            user_prompt,
        )

        if caption:
            # Clean up the caption
            caption = self._clean_caption(caption)
            logger.info(f"Generated caption: {caption[:80]}...")
            return caption
        else:
            # Fallback caption
            fallback = self._generate_fallback_caption(metadata)
            logger.warning(f"Using fallback caption: {fallback}")
            return fallback

    async def generate_hashtags(self, metadata: VideoMetadata) -> list[str]:
        """
        Generate optimized hashtags for a TikTok video.
        
        Args:
            metadata: Video metadata for context.
            
        Returns:
            List of hashtag strings (with # prefix).
        """
        logger.info(f"Generating hashtags for video {metadata.video_id}")

        user_prompt = f"""Generate 5-8 optimized TikTok hashtags for this content:

Description: {metadata.description}
Original hashtags: {' '.join(f'#{h}' for h in metadata.hashtags)}
Author niche: @{metadata.author_id}
Music: {metadata.music_title}

Requirements:
- Mix of broad trending tags (1-2M+ posts) and niche tags (10K-500K posts)
- Include at least 1 location-based or community tag
- Ensure relevance to the content
- Format: space-separated with # prefix
- Maximum 8 hashtags

Respond with ONLY the hashtags, space-separated. Example: #fyp #viral #trending"""

        result = await self._generate(
            self.config.hashtag_system_prompt,
            user_prompt,
        )

        if result:
            hashtags = self._extract_hashtags(result)
            if hashtags:
                logger.info(f"Generated {len(hashtags)} hashtags")
                return hashtags

        # Fallback hashtags
        fallback = self._generate_fallback_hashtags(metadata)
        logger.warning(f"Using fallback hashtags: {fallback}")
        return fallback

    async def generate_full_description(
        self, metadata: VideoMetadata
    ) -> tuple[str, list[str]]:
        """
        Generate both caption and hashtags in a single call for efficiency.
        
        Returns:
            Tuple of (caption, hashtags list).
        """
        logger.info(f"Generating full description for video {metadata.video_id}")

        context = self._build_context(metadata)

        user_prompt = f"""Create viral TikTok content for this video:

Original description: {metadata.description}
Original hashtags: {' '.join(f'#{h}' for h in metadata.hashtags)}
Author: @{metadata.author_id}
Music: {metadata.music_title} by {metadata.music_author}
Views: {metadata.view_count:,}
Engagement score: {metadata.engagement_score:.1f}

RESPOND IN THIS EXACT FORMAT (no other text):
CAPTION: <viral caption under 150 chars>
HASHTAGS: <5-8 space-separated hashtags with #>

Example:
CAPTION: Wait for it... this changed everything I thought I knew 😱
HASHTAGS: #fyp #viral #trending #lifehack #mustwatch"""

        result = await self._generate(
            "You are a TikTok viral content expert. Generate high-engagement captions and hashtags.",
            user_prompt,
        )

        if result:
            caption, hashtags = self._parse_full_response(result)
            if caption and hashtags:
                return caption, hashtags

        # Fallback: generate separately
        caption = await self.generate_caption(metadata)
        hashtags = await self.generate_hashtags(metadata)
        return caption, hashtags

    def _build_context(self, metadata: VideoMetadata) -> str:
        """Build a rich context string for the AI prompt."""
        parts = [
            f"Video by @{metadata.author_id}",
            f"Description: {metadata.description[:200]}",
            f"Hashtags: {', '.join(metadata.hashtags[:10])}",
            f"Music: {metadata.music_title}",
            f"Views: {metadata.view_count:,} | Likes: {metadata.like_count:,}",
        ]
        return "\n".join(parts)

    def _clean_caption(self, caption: str) -> str:
        """Clean and format the generated caption."""
        # Remove quotes if present
        caption = caption.strip('"\'')

        # Remove any "CAPTION:" prefix
        caption = re.sub(r"^(CAPTION[:\-]\s*)", "", caption, flags=re.IGNORECASE)

        # Remove hashtags (they go separately)
        caption = re.sub(r"#\w+", "", caption)

        # Normalize whitespace
        caption = " ".join(caption.split())

        # Truncate to 150 chars
        if len(caption) > 150:
            caption = caption[:147] + "..."

        return caption.strip()

    def _extract_hashtags(self, text: str) -> list[str]:
        """Extract hashtags from generated text."""
        # Remove any "HASHTAGS:" prefix
        text = re.sub(r"^(HASHTAGS[:\-]\s*)", "", text, flags=re.IGNORECASE)

        # Extract hashtags
        hashtags = re.findall(r"#\w+", text)

        # Validate and clean
        cleaned = []
        for tag in hashtags:
            tag = tag.lower().strip()
            if len(tag) > 2 and tag not in cleaned:
                cleaned.append(tag)

        return cleaned[:8]  # Max 8 hashtags

    def _parse_full_response(self, text: str) -> tuple[Optional[str], Optional[list[str]]]:
        """Parse a combined caption+hashtags response."""
        caption = None
        hashtags = None

        # Extract caption
        caption_match = re.search(
            r"CAPTION[:\-]\s*(.+?)(?:\nHASHTAGS:|$)", text, re.IGNORECASE | re.DOTALL
        )
        if caption_match:
            caption = self._clean_caption(caption_match.group(1))

        # Extract hashtags
        hashtags_match = re.search(
            r"HASHTAGS[:\-]\s*(.+?)(?:\n|$)", text, re.IGNORECASE | re.DOTALL
        )
        if hashtags_match:
            hashtags = self._extract_hashtags(hashtags_match.group(1))

        return caption, hashtags

    def _generate_fallback_caption(self, metadata: VideoMetadata) -> str:
        """Generate a basic fallback caption if AI fails."""
        hooks = [
            "This is wild 🔥",
            "Wait for it... 😱",
            "I can't believe this worked",
            "POV: you finally found out",
            "The way this changed everything",
            "Nobody talks about this 👀",
            "This deserves more attention",
            "The accuracy is unreal",
        ]
        import random
        hook = random.choice(hooks)

        if metadata.description:
            desc_preview = metadata.description[:60]
            return f"{hook}\n\n{desc_preview}"

        return hook

    def _generate_fallback_hashtags(self, metadata: VideoMetadata) -> list[str]:
        """Generate basic fallback hashtags if AI fails."""
        base_hashtags = ["#fyp", "#viral", "#trending", "#foryou", "#foryoupage"]

        # Add content-related hashtags from original
        for tag in metadata.hashtags[:3]:
            formatted = f"#{tag.lower()}" if not tag.startswith("#") else tag.lower()
            if formatted not in base_hashtags:
                base_hashtags.append(formatted)

        return base_hashtags[:8]

    async def close(self) -> None:
        """Clean up resources."""
        if self._client:
            await self._client.aclose()

    async def __aenter__(self) -> AICopywriter:
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()
