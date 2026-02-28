"""
Cache Module - On-demand page caching with file locking.

Design:
- Each URL gets its own directory based on URL structure
- HTML and API data are fetched together and stored atomically
- File locks ensure multi-process safety
- TTL-based expiration with automatic refresh

Directory structure:
    cache/
    └── www.coingecko.com/
        └── en/
            └── coins/
                └── bitcoin/
                    ├── page.json   # {url, html, api_data, fetched_at}
                    └── .lock
"""

import fcntl
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from liveweb_arena.plugins.base import BasePlugin

logger = logging.getLogger(__name__)

# Default TTL: 24 hours
DEFAULT_TTL = 24 * 3600


class CacheFatalError(Exception):
    """
    Raised when page caching fails due to network issues.

    This indicates the browser cannot load the page, making evaluation invalid.
    Evaluation should be terminated immediately.
    """

    def __init__(self, message: str, url: str = None):
        super().__init__(message)
        self.url = url


def log(tag: str, message: str):
    """Simple logging helper."""
    print(f"[{tag}] {message}")


@dataclass
class CachedPage:
    """Cached page data."""
    url: str
    html: str
    api_data: Optional[Dict[str, Any]]
    fetched_at: float
    accessibility_tree: Optional[str] = None  # Cached for deterministic evaluation
    need_api: bool = True  # Whether this page requires API data (default True for safety)

    def is_expired(self, ttl: int) -> bool:
        return time.time() > self.fetched_at + ttl

    def is_complete(self) -> bool:
        """Check if cache is complete (has API data if needed)."""
        if self.need_api:
            return self.api_data is not None and len(self.api_data) > 0
        return True

    def to_dict(self) -> dict:
        result = {
            "url": self.url,
            "html": self.html,
            "api_data": self.api_data,
            "fetched_at": self.fetched_at,
            "need_api": self.need_api,
        }
        if self.accessibility_tree:
            result["accessibility_tree"] = self.accessibility_tree
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "CachedPage":
        return cls(
            url=data["url"],
            html=data["html"],
            api_data=data.get("api_data"),
            fetched_at=data["fetched_at"],
            accessibility_tree=data.get("accessibility_tree"),
            need_api=data.get("need_api", True),  # Default True for old caches
        )


@dataclass
class PageRequirement:
    """Page caching requirement."""
    url: str
    need_api: bool = False

    @staticmethod
    def nav(url: str) -> "PageRequirement":
        """Create navigation page requirement (HTML only)."""
        return PageRequirement(url, need_api=False)

    @staticmethod
    def data(url: str) -> "PageRequirement":
        """Create data page requirement (HTML + API)."""
        return PageRequirement(url, need_api=True)


async def async_file_lock_acquire(lock_path: Path, timeout: float = 60.0) -> int:
    """
    Acquire file lock asynchronously (non-blocking with retry).

    Returns file descriptor that must be released with async_file_lock_release().

    This avoids blocking the event loop while waiting for the lock.
    """
    import asyncio

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()

    while True:
        fd = open(lock_path, 'w')
        try:
            # Try non-blocking lock
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd  # Lock acquired, return file object
        except BlockingIOError:
            fd.close()
            # Lock held by another process, wait and retry
            if time.time() - start > timeout:
                raise TimeoutError(f"Could not acquire lock {lock_path} within {timeout}s")
            await asyncio.sleep(0.1)  # Yield to event loop
        except Exception:
            fd.close()
            raise


def async_file_lock_release(fd):
    """Release file lock acquired by async_file_lock_acquire()."""
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
    finally:
        fd.close()


def safe_path_component(s: str) -> str:
    """Convert string to safe path component."""
    # Replace dangerous characters
    s = re.sub(r'[<>:"/\\|?*]', '_', s)
    s = s.replace(' ', '_')
    s = s.replace(',', '_')
    s = s.replace('&', '_')
    # Limit length
    if len(s) > 200:
        s = s[:200]
    return s


def normalize_url(url: str) -> str:
    """
    Normalize URL for cache lookup.

    Rules:
    1. Lowercase domain
    2. Remove default ports
    3. Remove tracking parameters
    4. Sort remaining query parameters
    5. Lowercase query parameter values (for case-insensitive matching)
    6. Normalize URL encoding (decode %XX, normalize + to space)
    """
    parsed = urlparse(url)

    # Lowercase domain
    domain = parsed.netloc.lower()

    # Remove default ports
    if domain.endswith(':80') or domain.endswith(':443'):
        domain = domain.rsplit(':', 1)[0]

    # Path: decode percent-encoding, normalize spaces to +
    path = unquote(parsed.path or '/').replace(' ', '+')

    # Filter, sort, and lowercase query parameters
    if parsed.query:
        params = []
        tracking = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term', 'ref', 'source'}
        for part in parsed.query.split('&'):
            if '=' in part:
                key = part.split('=')[0].lower()
                if key not in tracking:
                    # Lowercase key only, preserve value case
                    value = part.split('=', 1)[1]
                    params.append(f"{key}={value}")
            else:
                params.append(part.lower())
        query = '&'.join(sorted(params))
    else:
        query = ''

    result = f"{parsed.scheme}://{domain}{path}"
    if query:
        result += f"?{query}"
    return result


def url_to_cache_dir(cache_dir: Path, url: str) -> Path:
    """
    Convert URL to cache directory path.

    Examples:
    https://www.coingecko.com/en/coins/bitcoin
    → cache/www.coingecko.com/en/coins/bitcoin/

    https://stooq.com/q/?s=aapl.us
    → cache/stooq.com/q/__s=aapl.us/
    """
    parsed = urlparse(url)

    # Domain (lowercase)
    domain = parsed.netloc.lower()
    if domain.endswith(':80') or domain.endswith(':443'):
        domain = domain.rsplit(':', 1)[0]

    # Path parts - decode percent-encoding, normalize spaces to +
    path = unquote(parsed.path).replace(' ', '+').strip('/')
    if path:
        path_parts = [safe_path_component(p) for p in path.split('/')]
    else:
        path_parts = ['_root_']

    # Query parameters (lowercase for case-insensitive matching)
    if parsed.query:
        query_part = '__' + safe_path_component(parsed.query.lower())
        path_parts[-1] = path_parts[-1] + query_part

    return cache_dir / domain / '/'.join(path_parts)


def url_display(url: str) -> str:
    """Get short display string for URL."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    path = parsed.path
    query = f"?{parsed.query}" if parsed.query else ""
    display = f"{domain}{path}{query}"
    if len(display) > 80:
        display = display[:77] + '...'
    return display


class CacheManager:
    """
    Unified cache manager.

    Features:
    - On-demand caching
    - File lock protection for multi-process safety
    - TTL-based expiration
    - API data caching for ground truth validation
    """

    def __init__(self, cache_dir: Path, ttl: int = DEFAULT_TTL):
        self.cache_dir = Path(cache_dir)
        self.ttl = ttl
        self._browser = None

    async def ensure_cached(
        self,
        pages: List[PageRequirement],
        plugin: "BasePlugin",
    ) -> Dict[str, CachedPage]:
        """
        Ensure specified pages are cached.

        Args:
            pages: List of page requirements
            plugin: Plugin for fetching API data

        Returns:
            {normalized_url: CachedPage} mapping
        """
        result = {}

        for page_req in pages:
            normalized = normalize_url(page_req.url)
            cached = await self._ensure_single(page_req.url, plugin, page_req.need_api)
            result[normalized] = cached

        return result

    async def _ensure_single(
        self,
        url: str,
        plugin: "BasePlugin",
        need_api: bool,
    ) -> CachedPage:
        """Ensure single URL is cached."""
        normalized = normalize_url(url)
        cache_dir = url_to_cache_dir(self.cache_dir, normalized)
        cache_file = cache_dir / "page.json"
        lock_file = cache_dir / ".lock"

        page_type = "data" if need_api else "nav"

        # 1. Quick check (no lock)
        cached = self._load_if_valid(cache_file, need_api)
        if cached:
            log("Cache", f"HIT {page_type} - {url_display(normalized)}")
            return cached

        # 2. Need update, acquire async lock (non-blocking to avoid deadlock)
        lock_fd = await async_file_lock_acquire(lock_file)
        try:
            # 3. Double check (another process may have updated)
            cached = self._load_if_valid(cache_file, need_api)
            if cached:
                log("Cache", f"HIT {page_type} (after lock) - {url_display(normalized)}")
                return cached

            # 4. Actually fetch - page and API in parallel when possible
            log("Cache", f"MISS {page_type} - fetching {url_display(normalized)}")
            start = time.time()

            import asyncio as _asyncio

            if need_api:
                # Fetch HTML and API data concurrently
                page_task = _asyncio.ensure_future(self._fetch_page(url, plugin))
                api_task = _asyncio.ensure_future(plugin.fetch_api_data(url))

                # Wait for both, collecting errors
                page_result = None
                page_error = None
                api_data = None
                api_error = None

                try:
                    page_result = await page_task
                except Exception as e:
                    page_error = e
                    # Cancel API task if page fails — no point caching without HTML
                    api_task.cancel()

                if page_error is None:
                    try:
                        api_data = await api_task
                    except Exception as e:
                        api_error = e

                if page_error is not None:
                    raise CacheFatalError(
                        f"Page fetch failed (browser cannot load): {page_error}",
                        url=url,
                    )
                html, accessibility_tree = page_result

                if api_error is not None:
                    raise CacheFatalError(
                        f"API data fetch failed (GT will be invalid): {api_error}",
                        url=url,
                    )
                if not api_data:
                    raise CacheFatalError(
                        f"API data is empty (GT will be invalid)",
                        url=url,
                    )
            else:
                try:
                    html, accessibility_tree = await self._fetch_page(url, plugin)
                except Exception as e:
                    raise CacheFatalError(
                        f"Page fetch failed (browser cannot load): {e}",
                        url=url,
                    )
                api_data = None

            cached = CachedPage(
                url=url,
                html=html,
                api_data=api_data,
                fetched_at=time.time(),
                accessibility_tree=accessibility_tree,
                need_api=need_api,
            )

            self._save(cache_file, cached)
            elapsed = time.time() - start
            log("Cache", f"SAVED {page_type} - {url_display(normalized)} ({elapsed:.1f}s)")
            return cached
        finally:
            async_file_lock_release(lock_fd)

    def _load_if_valid(self, cache_file: Path, need_api: bool) -> Optional[CachedPage]:
        """Load cache if valid."""
        if not cache_file.exists():
            return None

        try:
            cached = self._load(cache_file)
        except Exception as e:
            logger.warning(f"Failed to load cache {cache_file}: {e}")
            # Corrupted cache - delete it
            self._delete_cache(cache_file)
            return None

        if cached.is_expired(self.ttl):
            # Expired cache - delete it
            self._delete_cache(cache_file)
            return None

        # Check if cache is complete based on its own need_api flag
        # Also handle case where current request needs API but old cache doesn't have it
        if not cached.is_complete() or (need_api and not cached.api_data):
            log("Cache", f"Incomplete (missing API) - deleting {url_display(cached.url)}")
            self._delete_cache(cache_file)
            return None

        return cached

    def _delete_cache(self, cache_file: Path):
        """Delete cache file."""
        try:
            if cache_file.exists():
                cache_file.unlink()
        except Exception as e:
            logger.warning(f"Failed to delete cache {cache_file}: {e}")

    def _load(self, cache_file: Path) -> CachedPage:
        """Load cache from file."""
        with open(cache_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return CachedPage.from_dict(data)

    def _save(self, cache_file: Path, cached: CachedPage):
        """Save cache to file."""
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cached.to_dict(), f, ensure_ascii=False)

    async def _fetch_page(self, url: str, plugin=None) -> tuple:
        """
        Fetch page HTML and accessibility tree using Playwright.

        Args:
            url: Page URL to fetch
            plugin: Optional plugin for page setup (e.g., click "Show All")

        Returns:
            (html, accessibility_tree) tuple
        """
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                )
                page = await context.new_page()

                # Block tracking/ads to avoid networkidle delays
                from liveweb_arena.core.block_patterns import should_block_url

                async def _block_tracking(route):
                    if should_block_url(route.request.url):
                        await route.abort("blockedbyclient")
                    else:
                        await route.continue_()

                await page.route("**/*", _block_tracking)

                await page.goto(url, timeout=60000, wait_until="domcontentloaded")

                # Wait for network idle (short timeout: ads are blocked, so
                # legitimate content loads in ~3-4s; streaming endpoints like
                # aq*.stooq.com keep connections open indefinitely)
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

                # Plugin-specific page setup (e.g., click "ALL" to show all rows)
                if plugin and hasattr(plugin, 'setup_page_for_cache'):
                    try:
                        await plugin.setup_page_for_cache(page, url)
                    except Exception as e:
                        log("Cache", f"Page setup failed (continuing): {e}")

                # Scroll to trigger lazy loading
                for pos in [0, 500, 1000, 2000]:
                    await page.evaluate(f"window.scrollTo(0, {pos})")
                    await page.wait_for_timeout(300)

                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(500)

                html = await page.content()

                # Extract accessibility tree for deterministic caching
                a11y_tree = ""
                try:
                    a11y_snapshot = await page.accessibility.snapshot()
                    if a11y_snapshot:
                        a11y_tree = self._format_accessibility_tree(a11y_snapshot)
                except Exception:
                    pass

                # If accessibility tree is empty, get page text content
                if len(a11y_tree.strip()) < 100:
                    try:
                        page_text = await page.evaluate("""
                            () => {
                                const preElements = document.querySelectorAll('pre');
                                if (preElements.length > 0) {
                                    return Array.from(preElements).map(el => el.innerText).join('\\n');
                                }
                                return document.body.innerText || '';
                            }
                        """)
                        if page_text.strip():
                            if a11y_tree.strip():
                                a11y_tree += "\n\n--- Page Text Content ---\n" + page_text
                            else:
                                a11y_tree = page_text
                    except Exception:
                        pass

                await context.close()
                return html, a11y_tree

            finally:
                await browser.close()

    def _format_accessibility_tree(self, node: dict, indent: int = 0) -> str:
        """Format accessibility tree node recursively."""
        if not node:
            return ""

        lines = []
        prefix = "\t" * indent

        role = node.get("role", "")
        name = node.get("name", "")
        value = node.get("value", "")

        parts = [role]
        if name:
            parts.append(f'"{name}"')
        if value:
            parts.append(f'value="{value}"')

        lines.append(f"{prefix}{' '.join(parts)}")

        children = node.get("children", [])
        for child in children:
            lines.append(self._format_accessibility_tree(child, indent + 1))

        return "\n".join(lines)

    def get_cached(self, url: str) -> Optional[CachedPage]:
        """Get cached page without triggering update."""
        normalized = normalize_url(url)
        cache_dir = url_to_cache_dir(self.cache_dir, normalized)
        cache_file = cache_dir / "page.json"

        if not cache_file.exists():
            return None

        try:
            return self._load(cache_file)
        except Exception:
            return None

