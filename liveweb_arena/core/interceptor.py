"""
Request Interceptor Module.

Intercepts browser requests and serves from cache.

Usage:
    interceptor = CacheInterceptor(cached_pages, allowed_domains)
    await page.route("**/*", interceptor.handle_route)
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from playwright.async_api import Route

from liveweb_arena.core.block_patterns import TRACKING_BLOCK_PATTERNS
from liveweb_arena.core.cache import CachedPage, CacheFatalError, CacheManager, PageRequirement, normalize_url

logger = logging.getLogger(__name__)

# Pre-fetch timeout must be less than the main browser's NAVIGATION_TIMEOUT_MS (30s)
# so that route.abort() reaches the browser BEFORE page.goto() times out.
PREFETCH_TIMEOUT = 25


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
    BLOCK_PATTERNS = TRACKING_BLOCK_PATTERNS

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
        url_validator: Optional[callable] = None,
        plugin_resolver: Optional[Any] = None,
    ):
        """
        Initialize interceptor.

        Args:
            cached_pages: {normalized_url: CachedPage} mapping
            allowed_domains: Set of allowed domain names
            blocked_patterns: Additional URL patterns to block
            cache_manager: CacheManager for checking file cache
            url_validator: Optional callback (url: str) -> bool for dynamic URL validation.
                          Used by plugins that support external navigation (e.g., HN).
                          Called when domain is not in allowed_domains.
            plugin_resolver: Optional callback (url: str) -> Optional[BasePlugin].
                            Resolves URL to plugin for pre-fetch caching.
        """
        self.cached_pages = cached_pages
        self.allowed_domains = allowed_domains
        self.cache_manager = cache_manager
        self.url_validator = url_validator
        self.plugin_resolver = plugin_resolver
        self.stats = InterceptorStats()
        self._pending_error: Optional[Exception] = None
        # Per-evaluation storage for cached accessibility trees
        self._accessibility_trees: Dict[str, str] = {}

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
                # For document navigations (click-initiated), abort produces
                # chrome-error:// which the AI sees as a network error.
                # Use fulfill with HTML instead so the browser stays healthy.
                if resource_type == "document":
                    await route.fulfill(
                        status=403,
                        headers={"content-type": "text/html"},
                        body="<html><body><h1>Blocked</h1><p>URL blocked by policy.</p></body></html>",
                    )
                else:
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
            # Fallback: let the request through to network instead of aborting.
            # Aborting a click-initiated document navigation produces chrome-error://
            # which the AI sees as "Page failed to load - network error".
            try:
                await route.continue_()
            except Exception:
                try:
                    await route.abort("failed")
                except Exception:
                    pass

    async def _handle_document(self, route: Route, url: str):
        """Handle HTML document requests.

        Pre-fetch caching: on MISS, actively fetches via cache_manager and serves
        via route.fulfill(). The main browser never hits the network for plugin URLs.
        """
        normalized = normalize_url(url)
        page = self._find_cached_page(url)

        if page:
            self.stats.hits += 1

            # Store cached accessibility tree for deterministic evaluation
            if page.accessibility_tree:
                self._accessibility_trees[normalized] = page.accessibility_tree

            await route.fulfill(
                status=200,
                headers={"content-type": "text/html; charset=utf-8"},
                body=page.html,
            )
            return

        self.stats.misses += 1
        self.stats.miss_urls.append(url)
        log("Intercept", f"MISS document - {self._url_display(url)}")

        if not self._is_domain_allowed(url):
            await route.fulfill(
                status=403,
                headers={"content-type": "text/html"},
                body=f"<html><body><h1>Domain not allowed</h1><p>{url}</p></body></html>",
            )
            return

        # Pre-fetch caching: fetch via cache_manager, never let browser hit the network.
        # Timeout ensures route completes BEFORE main browser's goto times out (30s),
        # so route.abort() is received by the browser and triggers error detection.
        if self.cache_manager and self.plugin_resolver:
            plugin = self.plugin_resolver(url)
            if plugin:
                try:
                    need_api = plugin.needs_api_data(url)
                    page_req = PageRequirement.data(url) if need_api else PageRequirement.nav(url)
                    pages = await asyncio.wait_for(
                        self.cache_manager.ensure_cached([page_req], plugin),
                        timeout=PREFETCH_TIMEOUT,
                    )
                    self.cached_pages.update(pages)

                    cached = pages.get(normalize_url(url))
                    if cached and cached.html:
                        if cached.accessibility_tree:
                            self._accessibility_trees[normalized] = cached.accessibility_tree
                        await route.fulfill(
                            status=200,
                            headers={"content-type": "text/html; charset=utf-8"},
                            body=cached.html,
                        )
                        return
                except asyncio.TimeoutError:
                    self._pending_error = CacheFatalError(
                        f"Pre-fetch timeout ({PREFETCH_TIMEOUT}s)", url=url,
                    )
                    await route.abort("failed")
                    return
                except CacheFatalError as e:
                    self._pending_error = e
                    await route.abort("failed")
                    return
                except Exception as e:
                    self._pending_error = CacheFatalError(str(e), url=url)
                    await route.abort("failed")
                    return

        # Fallback: LIVE mode or URL without plugin → pass through to network
        self.stats.passed += 1
        await route.continue_()

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

        Lookup order:
        1. cached_pages dict (dynamically updated by pre-fetch caching)
        2. _url_map (built at __init__ from pre-cached pages)
        3. www variants of the above
        4. File cache fallback

        Only returns pages that are complete (have API data if needed).
        """
        normalized = normalize_url(url)
        parsed = urlparse(normalized)

        # 1. Check live cached_pages dict (dynamically updated)
        if normalized in self.cached_pages:
            page = self.cached_pages[normalized]
            if page.is_complete():
                return page

        # 2. Check _url_map (built at init time)
        if normalized in self._url_map:
            return self._url_map[normalized]

        # 3. Try www variants
        if parsed.netloc.startswith("www."):
            no_www = normalized.replace("www.", "", 1)
            if no_www in self.cached_pages:
                page = self.cached_pages[no_www]
                if page.is_complete():
                    return page
            if no_www in self._url_map:
                return self._url_map[no_www]
        else:
            with_www = normalized.replace("://", "://www.", 1)
            if with_www in self.cached_pages:
                page = self.cached_pages[with_www]
                if page.is_complete():
                    return page
            if with_www in self._url_map:
                return self._url_map[with_www]

        # 4. File cache fallback
        if self.cache_manager:
            for try_url in self._url_variants(url, parsed):
                page = self.cache_manager.get_cached(try_url)
                if page and not page.is_expired(self.cache_manager.ttl) and page.is_complete():
                    self._url_map[normalized] = page
                    return page

        return None

    @staticmethod
    def _url_variants(url: str, parsed) -> List[str]:
        """Generate URL variants for cache lookup (original, without www, with www)."""
        variants = [url]
        if parsed.netloc.startswith("www."):
            variants.append(url.replace("www.", "", 1))
        else:
            variants.append(url.replace("://", "://www.", 1))
        return variants

    def _should_block(self, url: str) -> bool:
        """Check if URL should be blocked."""
        for pattern in self._block_patterns:
            if pattern.search(url):
                return True
        return False

    def _is_domain_allowed(self, url: str) -> bool:
        """Check if URL's domain is allowed."""
        if not self.allowed_domains and not self.url_validator:
            return True

        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            # Remove port
            if ":" in domain:
                domain = domain.split(":")[0]

            # Check exact match or subdomain match against static whitelist
            for allowed in self.allowed_domains:
                if domain == allowed or domain.endswith("." + allowed):
                    return True

            # Try dynamic URL validator (for plugins with external navigation)
            if self.url_validator:
                try:
                    if self.url_validator(url):
                        return True
                except Exception as e:
                    logger.warning(f"URL validator exception for {url}: {e}")

            return False
        except Exception:
            return False

    def _url_display(self, url: str) -> str:
        """Get short display string for URL."""
        parsed = urlparse(url)
        domain = parsed.netloc
        path = parsed.path
        query = f"?{parsed.query}" if parsed.query else ""
        display = f"{domain}{path}{query}"
        if len(display) > 80:
            display = display[:77] + "..."
        return display

    def get_accessibility_tree(self, url: str) -> Optional[str]:
        """Get cached accessibility tree for a URL."""
        normalized = normalize_url(url)
        return self._accessibility_trees.get(normalized)

    def get_and_clear_error(self) -> Optional[Exception]:
        """Retrieve and clear any pending error from pre-fetch caching."""
        err = self._pending_error
        self._pending_error = None
        return err

    def raise_if_error(self, url: str = None) -> None:
        """Check for pending error and raise as CacheFatalError if present."""
        err = self._pending_error
        self._pending_error = None
        if err is not None:
            if isinstance(err, CacheFatalError):
                raise err
            raise CacheFatalError(str(err), url=url)

    def get_stats(self) -> dict:
        """Get interception statistics."""
        return self.stats.to_dict()

    def cleanup(self):
        """
        Release memory by clearing internal caches.

        Call this when the evaluation is complete to prevent memory leaks.
        The interceptor should not be used after calling this method.
        """
        self._url_map.clear()
        self._accessibility_trees.clear()
        self.cached_pages.clear()
        self.stats = InterceptorStats()
        self._pending_error = None
