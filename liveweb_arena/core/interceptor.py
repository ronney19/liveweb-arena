"""
Request Interceptor Module.

Intercepts browser requests and serves from cache.

Usage:
    interceptor = CacheInterceptor(cached_pages, allowed_domains)
    await page.route("**/*", interceptor.handle_route)
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from playwright.async_api import Route

from liveweb_arena.core.cache import CachedPage, CacheManager, normalize_url

logger = logging.getLogger(__name__)


def log(tag: str, message: str):
    """Simple logging helper."""
    print(f"[{tag}] {message}")


@dataclass
class InterceptorStats:
    """Statistics for request interception."""
    hits: int = 0
    misses: int = 0
    blocked: int = 0
    passed: int = 0
    errors: int = 0
    miss_urls: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        total = self.hits + self.misses + self.blocked + self.passed
        return {
            "hits": self.hits,
            "misses": self.misses,
            "blocked": self.blocked,
            "passed": self.passed,
            "errors": self.errors,
            "total": total,
            "hit_rate": self.hits / max(1, self.hits + self.misses),
            "miss_urls": self.miss_urls[:10],
        }


# Global storage for cached accessibility trees (URL -> tree)
# Used by browser to return deterministic content in cache mode
_cached_accessibility_trees: Dict[str, str] = {}


def get_cached_accessibility_tree(url: str) -> Optional[str]:
    """Get cached accessibility tree for URL."""
    normalized = normalize_url(url)
    return _cached_accessibility_trees.get(normalized)


def clear_cached_accessibility_trees():
    """Clear all cached accessibility trees."""
    _cached_accessibility_trees.clear()


class CacheInterceptor:
    """
    Intercepts browser requests and serves from cache.

    Behavior:
    - document requests: Serve from cache if available
    - static resources (css/js/images): Pass through to network
    - tracking/analytics: Block
    - other requests: Handle based on domain whitelist
    """

    # Patterns to always block (tracking, analytics, ads)
    BLOCK_PATTERNS = [
        # Google
        r"google-analytics\.com",
        r"googletagmanager\.com",
        r"googlesyndication\.com",
        r"googleadservices\.com",
        r"google\.com/recaptcha",
        r"doubleclick\.net",
        # Social widgets
        r"facebook\.com/tr",
        r"platform\.twitter\.com",
        r"syndication\.twitter\.com",
        # Analytics
        r"hotjar\.com",
        r"sentry\.io",
        r"analytics",
        r"tracking",
        r"pixel",
        r"beacon",
        # Ad networks & sync
        r"rubiconproject\.com",
        r"criteo\.com",
        r"3lift\.com",
        r"pubmatic\.com",
        r"media\.net",
        r"adnxs\.com",
        r"presage\.io",
        r"onetag-sys\.com",
        r"seedtag\.com",
        r"openx\.net",
        r"btloader\.com",
        r"cloudflare\.com/cdn-cgi/challenge",
        # Generic patterns
        r"usync",
        r"syncframe",
        r"user_sync",
        r"checksync",
        # Site-specific ads
        r"stooq\.com/ads/",
    ]

    # Patterns to always allow (static resources)
    STATIC_PATTERNS = [
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
        r"\.webp(\?|$)",
    ]

    def __init__(
        self,
        cached_pages: Dict[str, CachedPage],
        allowed_domains: Set[str],
        blocked_patterns: Optional[List[str]] = None,
        cache_manager: Optional[CacheManager] = None,
    ):
        """
        Initialize interceptor.

        Args:
            cached_pages: {normalized_url: CachedPage} mapping
            allowed_domains: Set of allowed domain names
            blocked_patterns: Additional URL patterns to block
            cache_manager: CacheManager for checking file cache
        """
        self.cached_pages = cached_pages
        self.allowed_domains = allowed_domains
        self.cache_manager = cache_manager
        self.stats = InterceptorStats()

        # Compile patterns
        all_block_patterns = list(self.BLOCK_PATTERNS)
        if blocked_patterns:
            for pattern in blocked_patterns:
                # Convert glob to regex with proper escaping
                # Escape all regex special chars except *, then replace * with .*
                regex_pattern = re.escape(pattern).replace(r"\*", ".*")
                all_block_patterns.append(regex_pattern)

        self._block_patterns = [re.compile(p, re.IGNORECASE) for p in all_block_patterns]
        self._static_patterns = [re.compile(p, re.IGNORECASE) for p in self.STATIC_PATTERNS]

        # Build URL lookup map (normalized_url -> CachedPage)
        self._url_map: Dict[str, CachedPage] = {}
        for url, page in cached_pages.items():
            self._url_map[normalize_url(url)] = page
            # Also add original URL
            self._url_map[normalize_url(page.url)] = page

    async def handle_route(self, route: Route):
        """Main route handler for Playwright."""
        request = route.request
        url = request.url
        resource_type = request.resource_type

        try:
            # Always allow about:blank
            if url.startswith("about:"):
                await route.continue_()
                return

            # Block tracking/analytics
            if self._should_block(url):
                self.stats.blocked += 1
                await route.abort("blockedbyclient")
                return

            # Handle by resource type
            if resource_type == "document":
                await self._handle_document(route, url)
            elif resource_type in ("stylesheet", "script", "image", "font"):
                await self._handle_static(route, url)
            elif resource_type in ("xhr", "fetch"):
                await self._handle_xhr(route, url)
            else:
                await self._handle_other(route, url)

        except Exception as e:
            logger.error(f"Interceptor error for {url}: {e}")
            self.stats.errors += 1
            try:
                await route.abort("failed")
            except Exception:
                pass

    async def _handle_document(self, route: Route, url: str):
        """Handle HTML document requests."""
        normalized = normalize_url(url)
        page = self._find_cached_page(url)

        if page:
            self.stats.hits += 1
            log("Intercept", f"HIT document - {self._url_display(url)}")

            # Store cached accessibility tree for deterministic evaluation
            if page.accessibility_tree:
                _cached_accessibility_trees[normalized] = page.accessibility_tree

            await route.fulfill(
                status=200,
                headers={"content-type": "text/html; charset=utf-8"},
                body=page.html,
            )
        else:
            self.stats.misses += 1
            self.stats.miss_urls.append(url)
            log("Intercept", f"MISS document - {self._url_display(url)}")

            # Check if domain is allowed
            if self._is_domain_allowed(url):
                # Allow through to network
                self.stats.passed += 1
                await route.continue_()
            else:
                # Block - domain not allowed
                await route.fulfill(
                    status=403,
                    headers={"content-type": "text/html"},
                    body=f"<html><body><h1>Domain not allowed</h1><p>{url}</p></body></html>",
                )

    async def _handle_static(self, route: Route, url: str):
        """Handle static resource requests."""
        # Always allow static resources through
        self.stats.passed += 1
        await route.continue_()

    async def _handle_xhr(self, route: Route, url: str):
        """Handle XHR/fetch requests."""
        # Check domain whitelist
        if self._is_domain_allowed(url):
            self.stats.passed += 1
            await route.continue_()
        else:
            self.stats.blocked += 1
            await route.abort("blockedbyclient")

    async def _handle_other(self, route: Route, url: str):
        """Handle other request types."""
        if self._is_domain_allowed(url):
            self.stats.passed += 1
            await route.continue_()
        else:
            self.stats.blocked += 1
            await route.abort("blockedbyclient")

    def _find_cached_page(self, url: str) -> Optional[CachedPage]:
        """Find cached page for URL.

        Only returns pages that are complete (have API data if needed).
        Incomplete pages are ignored to allow on_navigation to fetch fresh data.
        """
        normalized = normalize_url(url)
        parsed = urlparse(normalized)

        # Try exact match in memory
        if normalized in self._url_map:
            return self._url_map[normalized]

        # Try without www in memory
        if parsed.netloc.startswith("www."):
            no_www = normalized.replace("www.", "", 1)
            if no_www in self._url_map:
                return self._url_map[no_www]

        # Try with www in memory
        if not parsed.netloc.startswith("www."):
            with_www = normalized.replace("://", "://www.", 1)
            if with_www in self._url_map:
                return self._url_map[with_www]

        # Try file cache with all URL variations
        # Note: We only use complete pages from file cache.
        # Incomplete pages (need API but missing) are skipped - on_navigation
        # will fetch complete data and add to cached_pages.
        if self.cache_manager:
            # Try original URL
            page = self.cache_manager.get_cached(url)
            if page and not page.is_expired(self.cache_manager.ttl) and page.is_complete():
                self._url_map[normalized] = page
                return page

            # Try without www in file cache
            if parsed.netloc.startswith("www."):
                no_www_url = url.replace("www.", "", 1)
                page = self.cache_manager.get_cached(no_www_url)
                if page and not page.is_expired(self.cache_manager.ttl) and page.is_complete():
                    self._url_map[normalized] = page
                    return page

            # Try with www in file cache
            if not parsed.netloc.startswith("www."):
                with_www_url = url.replace("://", "://www.", 1)
                page = self.cache_manager.get_cached(with_www_url)
                if page and not page.is_expired(self.cache_manager.ttl) and page.is_complete():
                    self._url_map[normalized] = page
                    return page

        return None

    def _should_block(self, url: str) -> bool:
        """Check if URL should be blocked."""
        for pattern in self._block_patterns:
            if pattern.search(url):
                return True
        return False

    def _is_static(self, url: str) -> bool:
        """Check if URL is a static resource."""
        for pattern in self._static_patterns:
            if pattern.search(url):
                return True
        return False

    def _is_domain_allowed(self, url: str) -> bool:
        """Check if URL's domain is allowed."""
        if not self.allowed_domains:
            return True

        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            # Remove port
            if ":" in domain:
                domain = domain.split(":")[0]

            # Check exact match or subdomain match
            for allowed in self.allowed_domains:
                if domain == allowed or domain.endswith("." + allowed):
                    return True

            return False
        except Exception:
            return False

    def _url_display(self, url: str) -> str:
        """Get short display string for URL."""
        parsed = urlparse(url)
        domain = parsed.netloc
        path = parsed.path
        if len(path) > 40:
            path = path[:37] + "..."
        return f"{domain}{path}"

    def get_stats(self) -> dict:
        """Get interception statistics."""
        return self.stats.to_dict()

    def reset_stats(self):
        """Reset statistics."""
        self.stats = InterceptorStats()
