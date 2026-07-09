"""
IP rotation, authentication, and health checks.

Manages proxy pools for anonymous requests with:
- Automatic rotation
- Health monitoring
- Failure tracking
- Authentication support
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Proxy:
    """Represents a single proxy server."""

    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    protocol: str = "http"
    weight: float = 1.0  # For weighted rotation
    consecutive_failures: int = 0
    last_used: Optional[float] = None
    last_success: Optional[float] = None
    average_response_time: float = 0.0
    is_active: bool = True

    @property
    def url(self) -> str:
        """Get the full proxy URL."""
        auth = ""
        if self.username and self.password:
            auth = f"{self.username}:{self.password}@"
        return f"{self.protocol}://{auth}{self.host}:{self.port}"

    @property
    def is_healthy(self) -> bool:
        """Check if proxy is healthy."""
        if not self.is_active:
            return False
        if self.consecutive_failures >= settings.proxy.max_failures:
            return False
        return True

    def record_success(self, response_time: float) -> None:
        """Record a successful request."""
        self.consecutive_failures = 0
        self.last_success = time.time()
        # Update average response time (exponential moving average)
        self.average_response_time = (
            0.7 * self.average_response_time + 0.3 * response_time
        )
        self.is_active = True

    def record_failure(self) -> None:
        """Record a failed request."""
        self.consecutive_failures += 1
        self.last_used = time.time()
        if self.consecutive_failures >= settings.proxy.max_failures:
            self.is_active = False
            logger.warning(
                f"Proxy {self.host}:{self.port} deactivated after "
                f"{self.consecutive_failures} consecutive failures"
            )

    def __hash__(self) -> int:
        return hash(f"{self.host}:{self.port}")

    def __eq__(self, other) -> bool:
        if not isinstance(other, Proxy):
            return False
        return self.host == other.host and self.port == other.port


class ProxyManager:
    """
    Manages a pool of proxy servers with rotation and health checks.
    
    Features:
    - Weighted round-robin rotation
    - Automatic health monitoring
    - Cool-down for failed proxies
    - Support for authenticated proxies
    - External proxy list loading
    """

    def __init__(self) -> None:
        self.config = settings.proxy
        self._proxies: list[Proxy] = []
        self._current_index: int = 0
        self._last_rotation: float = 0
        self._lock = asyncio.Lock()

        if self.config.enabled:
            self._load_proxies()

    def _load_proxies(self) -> None:
        """Load proxies from various sources."""
        if self.config.proxy_host and self.config.proxy_port:
            # Single proxy configuration
            self._proxies.append(
                Proxy(
                    host=self.config.proxy_host,
                    port=self.config.proxy_port,
                    username=self.config.proxy_username,
                    password=self.config.proxy_password,
                )
            )

        # Load from file
        if self.config.proxy_list_file:
            self._load_from_file(Path(self.config.proxy_list_file))

        # Load from URL
        if self.config.proxy_list_url:
            # Async loading would be better, but for init we skip
            logger.debug("Proxy list URL configured, will load on first use")

        if not self._proxies and self.config.enabled:
            logger.warning(
                "Proxy enabled but no proxies configured. "
                "Set PROXY_HOST/PROXY_PORT or PROXY_LIST_FILE."
            )
        else:
            logger.info(f"Loaded {len(self._proxies)} proxies")

    def _load_from_file(self, file_path: Path) -> None:
        """Load proxies from a file (one per line)."""
        try:
            with open(file_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    proxy = self._parse_proxy_line(line)
                    if proxy:
                        self._proxies.append(proxy)
        except FileNotFoundError:
            logger.warning(f"Proxy list file not found: {file_path}")
        except Exception as e:
            logger.error(f"Error loading proxy file: {e}")

    def _parse_proxy_line(self, line: str) -> Optional[Proxy]:
        """Parse a proxy from a line of text."""
        try:
            # Format: protocol://username:password@host:port
            # or: host:port
            # or: protocol://host:port

            if "://" in line:
                parsed = urlparse(line)
                protocol = parsed.scheme or "http"
                host = parsed.hostname
                port = parsed.port or 8080
                username = parsed.username
                password = parsed.password
            else:
                # Simple host:port format
                parts = line.split(":")
                host = parts[0]
                port = int(parts[1]) if len(parts) > 1 else 8080
                protocol = "http"
                username = None
                password = None

            if host and port:
                return Proxy(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    protocol=protocol,
                )
        except Exception as e:
            logger.debug(f"Failed to parse proxy line '{line}': {e}")

        return None

    async def _load_from_url(self) -> None:
        """Async load proxies from a URL."""
        if not self.config.proxy_list_url:
            return

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(self.config.proxy_list_url)
                if response.status_code == 200:
                    lines = response.text.strip().split("\n")
                    new_proxies = []
                    for line in lines:
                        proxy = self._parse_proxy_line(line.strip())
                        if proxy and proxy not in self._proxies:
                            new_proxies.append(proxy)

                    self._proxies.extend(new_proxies)
                    logger.info(f"Loaded {len(new_proxies)} proxies from URL")
        except Exception as e:
            logger.error(f"Error loading proxies from URL: {e}")

    def get_proxy(self) -> Optional[Proxy]:
        """
        Get the next proxy using weighted round-robin.
        
        Returns:
            Proxy instance or None if no proxies available.
        """
        if not self.config.enabled or not self._proxies:
            return None

        healthy_proxies = [p for p in self._proxies if p.is_healthy]
        if not healthy_proxies:
            logger.warning("No healthy proxies available, resetting all")
            for p in self._proxies:
                p.is_active = True
                p.consecutive_failures = 0
            healthy_proxies = self._proxies

        # Weighted selection
        total_weight = sum(p.weight for p in healthy_proxies)
        if total_weight == 0:
            selected = random.choice(healthy_proxies)
        else:
            r = random.uniform(0, total_weight)
            cumulative = 0
            for proxy in healthy_proxies:
                cumulative += proxy.weight
                if r <= cumulative:
                    selected = proxy
                    break
            else:
                selected = healthy_proxies[-1]

        selected.last_used = time.time()
        return selected

    def get_proxy_dict(self) -> Optional[dict]:
        """
        Get proxy configuration as a dictionary for httpx/requests.
        
        Returns:
            Dict with 'http' and 'https' keys, or None.
        """
        proxy = self.get_proxy()
        if not proxy:
            return None

        proxy_url = proxy.url
        return {
            "http://": proxy_url,
            "https://": proxy_url,
        }

    async def rotate(self) -> Optional[Proxy]:
        """Force rotate to a new proxy."""
        async with self._lock:
            self._current_index = (self._current_index + 1) % max(len(self._proxies), 1)
            self._last_rotation = time.time()
            return self.get_proxy()

    def record_success(self, proxy: Proxy, response_time: float) -> None:
        """Record a successful request through a proxy."""
        proxy.record_success(response_time)

    def record_failure(self, proxy: Proxy) -> None:
        """Record a failed request through a proxy."""
        proxy.record_failure()

    async def health_check_all(self) -> dict:
        """
        Perform health checks on all proxies.
        
        Returns:
            Dict with health check results.
        """
        results = {
            "total": len(self._proxies),
            "healthy": 0,
            "unhealthy": 0,
            "details": [],
        }

        async def check_proxy(proxy: Proxy) -> dict:
            start = time.time()
            try:
                proxy_url = proxy.url
                async with httpx.AsyncClient(
                    proxies={"http://": proxy_url, "https://": proxy_url},
                    timeout=settings.proxy.timeout,
                    verify=settings.proxy.verify_ssl,
                ) as client:
                    response = await client.get("https://httpbin.org/ip")
                    elapsed = time.time() - start

                    if response.status_code == 200:
                        proxy.record_success(elapsed)
                        return {
                            "proxy": f"{proxy.host}:{proxy.port}",
                            "status": "healthy",
                            "response_time_ms": round(elapsed * 1000, 2),
                            "ip": response.json().get("origin", "unknown"),
                        }
            except Exception as e:
                proxy.record_failure()
                return {
                    "proxy": f"{proxy.host}:{proxy.port}",
                    "status": "unhealthy",
                    "error": str(e),
                }

        # Check all proxies concurrently
        tasks = [check_proxy(p) for p in self._proxies]
        check_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in check_results:
            if isinstance(result, Exception):
                results["details"].append({"status": "error", "error": str(result)})
                results["unhealthy"] += 1
            else:
                results["details"].append(result)
                if result["status"] == "healthy":
                    results["healthy"] += 1
                else:
                    results["unhealthy"] += 1

        logger.info(
            f"Proxy health check: {results['healthy']}/{results['total']} healthy"
        )
        return results

    def get_stats(self) -> dict:
        """Get proxy pool statistics."""
        return {
            "total_proxies": len(self._proxies),
            "healthy_proxies": sum(1 for p in self._proxies if p.is_healthy),
            "unhealthy_proxies": sum(1 for p in self._proxies if not p.is_healthy),
            "enabled": self.config.enabled,
            "provider": self.config.provider,
            "proxies": [
                {
                    "host": p.host,
                    "port": p.port,
                    "healthy": p.is_healthy,
                    "failures": p.consecutive_failures,
                    "avg_response_ms": round(p.average_response_time * 1000, 2),
                    "protocol": p.protocol,
                }
                for p in self._proxies
            ],
        }

    def get_current_proxy_url(self) -> Optional[str]:
        """Get the current proxy URL."""
        proxy = self.get_proxy()
        return proxy.url if proxy else None
