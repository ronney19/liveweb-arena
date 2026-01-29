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
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import quote, unquote, urlparse

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


@contextmanager
def file_lock(lock_path: Path):
    """Cross-process file lock."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, 'w') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


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
                    # Lowercase the entire parameter (key=value)
                    params.append(part.lower())
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
    if len(path) > 40:
        path = path[:37] + '...'
    return f"{domain}{path}"


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

        # 2. Need update, acquire lock
        with file_lock(lock_file):
            # 3. Double check (another process may have updated)
            cached = self._load_if_valid(cache_file, need_api)
            if cached:
                log("Cache", f"HIT {page_type} (after lock) - {url_display(normalized)}")
                return cached

            # 4. Actually fetch - both page and API must succeed
            log("Cache", f"MISS {page_type} - fetching {url_display(normalized)}")
            start = time.time()

            # Fetch page HTML and accessibility tree - must succeed
            try:
                html, accessibility_tree = await self._fetch_page(url)
            except Exception as e:
                raise CacheFatalError(
                    f"Page fetch failed (browser cannot load): {e}",
                    url=url,
                )

            # Fetch API data for data pages - must succeed if required
            api_data = None
            if need_api:
                try:
                    api_data = await plugin.fetch_api_data(url)
                except Exception as e:
                    raise CacheFatalError(
                        f"API data fetch failed (GT will be invalid): {e}",
                        url=url,
                    )
                # API data must not be empty
                if not api_data:
                    raise CacheFatalError(
                        f"API data is empty (GT will be invalid)",
                        url=url,
                    )

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

    async def _fetch_page(self, url: str) -> tuple:
        """
        Fetch page HTML and accessibility tree using Playwright.

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

                await page.goto(url, timeout=60000, wait_until="domcontentloaded")

                # Wait for network idle
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass

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


# Global instance
_cache_manager: Optional[CacheManager] = None


def get_cache_manager() -> CacheManager:
    """Get global cache manager instance."""
    global _cache_manager
    if _cache_manager is None:
        cache_dir = Path(__file__).parent.parent.parent / "cache"
        _cache_manager = CacheManager(cache_dir)
    return _cache_manager


def set_cache_manager(manager: CacheManager):
    """Set global cache manager instance."""
    global _cache_manager
    _cache_manager = manager
