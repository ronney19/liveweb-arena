"""
Browser request interceptor for snapshot cache.

Intercepts browser requests and serves from cache instead of network.
This replaces HAR-based recording/playback with direct cache serving.

Usage:
    from liveweb_arena.core.request_interceptor import RequestInterceptor

    interceptor = RequestInterceptor(snapshot)

    # In browser setup
    await page.route("**/*", interceptor.handle_route)

    # After evaluation
    stats = interceptor.get_stats()
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from playwright.async_api import Route, Request

from liveweb_arena.core.snapshot_cache import Snapshot
from liveweb_arena.utils.logger import log

logger = logging.getLogger(__name__)


@dataclass
class InterceptorStats:
    """Statistics for request interception."""
    hits: int = 0              # Cache hits (whitelisted domains)
    misses: int = 0            # Cache misses (whitelisted domains)
    blocked: int = 0           # Blocked requests (tracking, non-whitelisted)
    errors: int = 0
    passthrough: int = 0       # Static resources allowed through
    miss_urls: List[str] = field(default_factory=list)
    fatal_misses: List[str] = field(default_factory=list)  # Document misses in strict mode

    def to_dict(self) -> dict:
        # Only count whitelisted domain requests for hit rate
        whitelisted_requests = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "blocked": self.blocked,
            "passthrough": self.passthrough,
            "errors": self.errors,
            "total_requests": self.hits + self.misses + self.blocked + self.passthrough,
            "hit_rate": self.hits / max(1, whitelisted_requests),
            "miss_urls": self.miss_urls[:10],  # First 10 only
            "fatal_misses": self.fatal_misses,
        }


class RequestInterceptor:
    """
    Intercepts browser requests and serves from snapshot cache.

    Modes:
        - strict: Block all uncached requests (for evaluation)
        - permissive: Allow uncached requests (for debugging)
        - record: Allow and record to pending pool

    Request handling:
        - document (HTML): Serve from pages cache
        - xhr/fetch (API): Serve from page's api_responses
        - static (css/js/images): Allow through (or serve from assets cache)
        - tracking: Block
    """

    # Patterns to always block (tracking, analytics, ads)
    BLOCK_PATTERNS = [
        r"google-analytics\.com",
        r"googletagmanager\.com",
        r"googlesyndication\.com",
        r"googleadservices\.com",
        r"sentry\.io",
        r"doubleclick\.net",
        r"facebook\.com/tr",
        r"hotjar\.com",
        r"adtech\.",
        r"analytics",
        r"tracking",
        r"pixel",
        r"beacon",
    ]

    # Patterns to always allow (static resources)
    ALLOW_PATTERNS = [
        r"\.css(\?|$)",
        r"\.js(\?|$)",
        r"\.woff2?(\?|$)",
        r"\.ttf(\?|$)",
        r"\.png(\?|$)",
        r"\.jpg(\?|$)",
        r"\.jpeg(\?|$)",
        r"\.gif(\?|$)",
        r"\.svg(\?|$)",
        r"\.ico(\?|$)",
    ]

    def __init__(
        self,
        snapshot: Snapshot,
        mode: str = "strict",
        allowed_domains: Optional[Set[str]] = None,
        on_miss_callback: Optional[callable] = None,
        plugin_block_patterns: Optional[List[str]] = None,
    ):
        """
        Initialize interceptor.

        Args:
            snapshot: Snapshot to serve from
            mode: "strict" | "permissive" | "record"
            allowed_domains: Optional set of allowed domains (None = allow all)
            on_miss_callback: Called when cache miss occurs (for pending pool)
            plugin_block_patterns: Additional URL patterns to block (from plugins)
        """
        self.snapshot = snapshot
        self.mode = mode
        self.allowed_domains = allowed_domains
        self.on_miss_callback = on_miss_callback
        self.stats = InterceptorStats()

        # Current page context (set by document requests)
        self._current_page_url: Optional[str] = None
        self._current_page_data: Optional[Dict[str, Any]] = None

        # Compile patterns (default + plugin-specific)
        all_block_patterns = list(self.BLOCK_PATTERNS)
        if plugin_block_patterns:
            # Convert glob-style patterns to regex
            for pattern in plugin_block_patterns:
                # Convert * to .* for regex matching
                regex_pattern = pattern.replace("*", ".*")
                all_block_patterns.append(regex_pattern)
        self._block_patterns = [re.compile(p, re.IGNORECASE) for p in all_block_patterns]
        self._allow_patterns = [re.compile(p, re.IGNORECASE) for p in self.ALLOW_PATTERNS]

    async def handle_route(self, route: Route):
        """
        Main route handler for Playwright.

        Usage:
            await page.route("**/*", interceptor.handle_route)
        """
        request = route.request
        url = request.url
        resource_type = request.resource_type

        try:
            # Always allow about:blank
            if url == "about:blank" or url.startswith("about:"):
                await route.continue_()
                return

            # Check if should block (tracking scripts, etc.)
            if self._should_block(url):
                self.stats.blocked += 1
                await route.abort("blockedbyclient")
                return

            # Check domain allowlist if configured
            if self.allowed_domains and not self._is_domain_allowed(url):
                self.stats.blocked += 1
                await route.abort("blockedbyclient")
                return

            # Handle by resource type
            if resource_type == "document":
                await self._handle_document(route, url)
            elif resource_type in ("xhr", "fetch"):
                await self._handle_api(route, url)
            elif resource_type in ("stylesheet", "script", "image", "font"):
                await self._handle_static(route, url)
            else:
                await self._handle_other(route, url)

        except Exception as e:
            logger.error(f"Interceptor error for {url}: {e}")
            self.stats.errors += 1
            await route.abort("failed")

    def _is_domain_allowed(self, url: str) -> bool:
        """Check if URL's domain is in the allowed list."""
        if not self.allowed_domains:
            return True

        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            # Remove port if present
            if ":" in domain:
                domain = domain.split(":")[0]

            # Check if domain or any parent domain is allowed
            for allowed in self.allowed_domains:
                if domain == allowed or domain.endswith("." + allowed):
                    return True
            return False
        except Exception:
            return False

    async def _handle_document(self, route: Route, url: str):
        """Handle HTML document requests."""
        # Try multiple URL variants to find cached page
        page_data = self._try_url_variants(url)

        if page_data:
            self.stats.hits += 1
            self._current_page_url = url
            self._current_page_data = page_data

            log("Page", f"CACHE HIT - {url[:80]}", force=True)

            await route.fulfill(
                status=page_data.get("status", 200),
                headers={"content-type": "text/html; charset=utf-8"},
                body=page_data["html"],
            )
        else:
            await self._handle_miss(route, url, "document")

    async def _handle_api(self, route: Route, url: str):
        """Handle XHR/fetch API requests."""
        parsed = urlparse(url)
        api_path = parsed.path

        # Try to find in current page's captured responses
        if self._current_page_data:
            api_responses = self._current_page_data.get("api_responses", {})

            # Try exact path match
            if api_path in api_responses:
                self.stats.hits += 1
                logger.debug(f"[CACHE HIT] API: {api_path}")

                data = api_responses[api_path]
                await route.fulfill(
                    status=200,
                    headers={"content-type": "application/json"},
                    body=data if isinstance(data, str) else __import__("json").dumps(data),
                )
                return

            # Try partial path match
            for cached_path, data in api_responses.items():
                if cached_path.endswith(api_path) or api_path.endswith(cached_path):
                    self.stats.hits += 1
                    logger.debug(f"[CACHE HIT] API (partial): {api_path} -> {cached_path}")

                    await route.fulfill(
                        status=200,
                        headers={"content-type": "application/json"},
                        body=data if isinstance(data, str) else __import__("json").dumps(data),
                    )
                    return

        await self._handle_miss(route, url, "api")

    async def _handle_static(self, route: Route, url: str):
        """Handle static resource requests (CSS, JS, images)."""
        # Allow static resources through to network
        # These are typically CDN resources that don't need caching
        if self._should_allow(url):
            self.stats.passthrough += 1
            await route.continue_()
        else:
            # Non-allowlisted static resources in strict mode
            if self.mode == "strict":
                self.stats.passthrough += 1
                await route.continue_()  # Still allow static resources
            else:
                await route.continue_()

    async def _handle_other(self, route: Route, url: str):
        """Handle other request types (websocket, media, etc.)."""
        # Generally allow through
        self.stats.passthrough += 1
        await route.continue_()

    async def _handle_miss(self, route: Route, url: str, request_type: str):
        """Handle cache miss."""
        self.stats.misses += 1
        self.stats.miss_urls.append(url)

        # Callback for pending pool
        if self.on_miss_callback:
            try:
                self.on_miss_callback(url, request_type)
            except Exception as e:
                logger.error(f"Miss callback error: {e}")

        # Handle based on mode
        if self.mode == "strict":
            # In strict mode, document miss is a fatal error
            if request_type == "document":
                self.stats.fatal_misses.append(url)
                log("Page", f"CACHE MISS (FATAL) - {url}", force=True)
                # Return error page - evaluation will fail
                await route.fulfill(
                    status=503,
                    headers={"content-type": "text/html"},
                    body=f"<html><body><h1>Page not cached</h1><p>{url}</p></body></html>",
                )
            elif request_type == "api":
                # Log API misses so we can see what's not cached
                parsed = urlparse(url)
                log("API", f"CACHE MISS - {parsed.path[:60]}", force=True)
                await route.fulfill(
                    status=200,
                    headers={"content-type": "application/json"},
                    body="{}",
                )
            else:
                await route.abort("blockedbyclient")

        elif self.mode == "permissive":
            logger.debug(f"[CACHE MISS] {request_type}: {url[:60]}... (allowing through)")
            await route.continue_()

        else:  # record mode
            await route.continue_()

    def _should_block(self, url: str) -> bool:
        """Check if URL should be blocked."""
        for pattern in self._block_patterns:
            if pattern.search(url):
                return True
        return False

    def _should_allow(self, url: str) -> bool:
        """Check if URL should be allowed through."""
        for pattern in self._allow_patterns:
            if pattern.search(url):
                return True
        return False

    def _normalize_url(self, url: str) -> str:
        """Normalize URL for cache lookup."""
        parsed = urlparse(url)

        # Remove tracking parameters
        tracking_params = {"utm_source", "utm_medium", "utm_campaign", "ref", "source"}
        if parsed.query:
            params = []
            for param in parsed.query.split("&"):
                if "=" in param:
                    key = param.split("=")[0]
                    if key.lower() not in tracking_params:
                        params.append(param)
            query = "&".join(sorted(params))
        else:
            query = ""

        # Normalize domain (lowercase, remove www)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]

        path = parsed.path or "/"
        normalized = f"{parsed.scheme}://{domain}{path}"
        if query:
            normalized += f"?{query}"

        return normalized

    def _try_url_variants(self, url: str) -> Optional[Dict[str, Any]]:
        """Try multiple URL variants to find cached page."""
        # Try original URL
        page_data = self.snapshot.get_page(url)
        if page_data:
            return page_data

        # Try normalized URL
        normalized = self._normalize_url(url)
        if normalized != url:
            page_data = self.snapshot.get_page(normalized)
            if page_data:
                return page_data

        # Try with www prefix
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if not domain.startswith("www."):
            www_url = f"{parsed.scheme}://www.{domain}{parsed.path}"
            if parsed.query:
                www_url += f"?{parsed.query}"
            page_data = self.snapshot.get_page(www_url)
            if page_data:
                return page_data

        return None

    def get_stats(self) -> dict:
        """Get interception statistics."""
        return self.stats.to_dict()

    def reset_stats(self):
        """Reset statistics."""
        self.stats = InterceptorStats()


class CacheMissError(Exception):
    """Raised when a critical cache miss occurs in strict mode."""
    def __init__(self, url: str, request_type: str):
        self.url = url
        self.request_type = request_type
        super().__init__(f"Cache miss ({request_type}): {url}")


class PendingUrlPool:
    """
    Collects URLs that were missed during evaluation.
    These can be cached in future snapshot updates.
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        self._urls: Set[str] = set()

    def add(self, url: str, request_type: str = "unknown"):
        """Add a URL to the pending pool."""
        self._urls.add(url)

    def get_all(self) -> List[str]:
        """Get all pending URLs."""
        return list(self._urls)

    def clear(self):
        """Clear the pool."""
        self._urls.clear()

    def save(self):
        """Save to file."""
        import json
        from pathlib import Path

        path = Path(self.file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w') as f:
            json.dump({
                "urls": list(self._urls),
                "updated_at": __import__("time").time(),
            }, f, indent=2)

    def load(self):
        """Load from file."""
        import json
        from pathlib import Path

        path = Path(self.file_path)
        if path.exists():
            with open(path) as f:
                data = json.load(f)
                self._urls = set(data.get("urls", []))
