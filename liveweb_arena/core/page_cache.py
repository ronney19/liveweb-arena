"""
Page-based Cache System.

Key design principle: Each page has its own API data slice
- No global api.json
- Each page.json contains: HTML + api_data (for that specific asset)
- Page and API data are fetched at the same time, ensuring consistency

Directory structure:
    cache/
    ├── coingecko/pages/
    │   ├── bitcoin.json      # {html, api_data: {bitcoin data}, fetched_at}
    │   └── ethereum.json     # {html, api_data: {ethereum data}, fetched_at}
    ├── stooq/pages/
    │   └── aapl.us.json      # {html, api_data: {aapl.us data}, fetched_at}
    └── weather/pages/
        └── tokyo.json        # {html, api_data: {tokyo weather}, fetched_at}

Ground Truth: Always from the page's api_data (guaranteed consistent with HTML)
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse, unquote

from liveweb_arena.utils.logger import log

logger = logging.getLogger(__name__)

# Default page TTL: 24 hours
DEFAULT_PAGE_TTL = 24 * 3600


@dataclass
class PageData:
    """
    Cached page data with its API slice.

    HTML and api_data are fetched at the same time, ensuring consistency.
    """
    url: str
    html: str
    api_data: Dict[str, Any] = field(default_factory=dict)  # API data for this specific asset
    asset_id: str = ""  # The asset ID extracted from URL (e.g., "bitcoin", "aapl.us")
    xhr_responses: Dict[str, Any] = field(default_factory=dict)
    status: int = 200
    fetched_at: float = field(default_factory=time.time)

    def is_expired(self, ttl: int = DEFAULT_PAGE_TTL) -> bool:
        return time.time() > (self.fetched_at + ttl)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "html": self.html,
            "api_data": self.api_data,
            "asset_id": self.asset_id,
            "xhr_responses": self.xhr_responses,
            "status": self.status,
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PageData":
        return cls(
            url=data["url"],
            html=data["html"],
            api_data=data.get("api_data", {}),
            asset_id=data.get("asset_id", ""),
            xhr_responses=data.get("xhr_responses", {}),
            status=data.get("status", 200),
            fetched_at=data.get("fetched_at", 0),
        )


class SourceCache:
    """Cache for a single data source."""

    def __init__(self, source_dir: Path, source: str, ttl: int = DEFAULT_PAGE_TTL):
        self.source_dir = Path(source_dir)
        self.source = source
        self.ttl = ttl
        self._pages: Dict[str, PageData] = {}

    @property
    def pages_dir(self) -> Path:
        return self.source_dir / "pages"

    def _url_to_filename(self, url: str) -> str:
        """Convert URL to safe filename."""
        parsed = urlparse(url)
        path = parsed.path.strip("/").replace("/", "_") or "index"
        if parsed.query:
            query_hash = hashlib.md5(parsed.query.encode()).hexdigest()[:8]
            path = f"{path}_{query_hash}"
        safe_path = "".join(c if c.isalnum() or c in "._-" else "_" for c in path)
        return f"{safe_path}.json"

    def _page_path(self, url: str) -> Path:
        return self.pages_dir / self._url_to_filename(url)

    def get_page(self, url: str) -> Optional[PageData]:
        """Get page data."""
        if url in self._pages:
            return self._pages[url]

        page_path = self._page_path(url)
        if not page_path.exists():
            return None

        try:
            with open(page_path) as f:
                page = PageData.from_dict(json.load(f))
            self._pages[url] = page
            return page
        except Exception as e:
            logger.warning(f"Failed to load page {url}: {e}")
            return None

    def set_page(self, page: PageData):
        """Save page data."""
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        page_path = self._page_path(page.url)
        with open(page_path, 'w') as f:
            json.dump(page.to_dict(), f)
        self._pages[page.url] = page

    def is_page_fresh(self, url: str) -> bool:
        """Check if page exists and is not expired."""
        page = self.get_page(url)
        if page is None:
            return False
        return not page.is_expired(self.ttl)

    def get_stale_urls(self, urls: List[str]) -> List[str]:
        """Get URLs that need refreshing."""
        return [url for url in urls if not self.is_page_fresh(url)]

    def get_api_data_for_url(self, url: str) -> Optional[Dict[str, Any]]:
        """Get API data from a cached page."""
        page = self.get_page(url)
        if page is None:
            return None
        return page.api_data

    def get_aggregated_api_data(self) -> Dict[str, Any]:
        """
        Aggregate API data from all cached pages.

        Returns a structure like:
        {
            "asset_id_1": {<api_data>},
            "asset_id_2": {<api_data>},
            ...
        }

        This is keyed by asset_id for easy lookup.
        """
        result = {}
        if not self.pages_dir.exists():
            logger.debug(f"[CACHE] pages_dir does not exist: {self.pages_dir}")
            return result

        pages_with_api = 0
        pages_without_api = 0
        for page_file in self.pages_dir.glob("*.json"):
            try:
                with open(page_file) as f:
                    data = json.load(f)
                asset_id = data.get("asset_id", "")
                api_data = data.get("api_data", {})
                if asset_id and api_data:
                    result[asset_id] = api_data
                    pages_with_api += 1
                else:
                    pages_without_api += 1
                    if not asset_id:
                        logger.debug(f"[CACHE] Page missing asset_id: {page_file.name}")
                    elif not api_data:
                        logger.debug(f"[CACHE] Page missing api_data: {page_file.name} (asset: {asset_id})")
            except Exception as e:
                logger.debug(f"Failed to load page {page_file}: {e}")

        if pages_without_api > 0:
            log(
                "Cache",
                f"{self.source}: {pages_with_api} pages with api_data, "
                f"{pages_without_api} without - run 'python eval.py --update-cache-only --force' to rebuild",
                force=True,
            )

        return result

    def get_stats(self) -> dict:
        """Get cache statistics."""
        page_count = 0
        if self.pages_dir.exists():
            page_count = len(list(self.pages_dir.glob("*.json")))

        return {
            "source": self.source,
            "page_count": page_count,
            "ttl_hours": self.ttl / 3600,
            "exists": page_count > 0,
            "is_expired": False,  # Page-based cache doesn't expire as a whole
            "time_remaining_hours": self.ttl / 3600 if page_count > 0 else 0,
        }


class PageCacheManager:
    """
    Main cache manager.

    Each page is cached with its corresponding API data slice.
    """

    # Domain to source mapping
    DOMAIN_TO_SOURCE = {
        "coingecko.com": "coingecko",
        "www.coingecko.com": "coingecko",
        "stooq.com": "stooq",
        "www.stooq.com": "stooq",
        "wttr.in": "weather",
        "v2.wttr.in": "weather",
        "themoviedb.org": "tmdb",
        "www.themoviedb.org": "tmdb",
        "taostats.io": "taostats",
        "www.taostats.io": "taostats",
    }

    # Source to wrapper key mapping (how templates expect data to be structured)
    SOURCE_WRAPPER_KEYS = {
        "coingecko": "coins",
        "stooq": "assets",
        "weather": "locations",
        "tmdb": "movies",
        "taostats": "subnets",
    }

    def __init__(self, cache_dir: Path, ttl: int = DEFAULT_PAGE_TTL):
        self.cache_dir = Path(cache_dir)
        self.ttl = ttl

        self._sources: Dict[str, SourceCache] = {}
        self._api_fetchers: Dict[str, Callable] = {}  # source -> async fn(asset_id) -> data
        self._url_generators: Dict[str, Callable] = {}
        self._asset_extractors: Dict[str, Callable] = {}  # source -> fn(url) -> asset_id

    def _get_source(self, source: str) -> SourceCache:
        """Get or create source cache."""
        if source not in self._sources:
            self._sources[source] = SourceCache(
                self.cache_dir / source,
                source,
                self.ttl,
            )
        return self._sources[source]

    def register_api_fetcher(self, source: str, fetcher: Callable):
        """
        Register API data fetcher for a source.

        fetcher: async fn(asset_id: str) -> Dict[str, Any]
        """
        self._api_fetchers[source] = fetcher

    def register_url_generator(self, source: str, generator: Callable):
        """Register URL generator for a source."""
        self._url_generators[source] = generator

    def register_asset_extractor(self, source: str, extractor: Callable):
        """
        Register asset ID extractor for a source.

        extractor: fn(url: str) -> str (asset_id)
        """
        self._asset_extractors[source] = extractor

    # === Asset ID Extraction ===

    def _extract_asset_id(self, source: str, url: str) -> Optional[str]:
        """Extract asset ID from URL."""
        # Use registered extractor if available
        if source in self._asset_extractors:
            return self._asset_extractors[source](url)

        # Default extractors
        parsed = urlparse(url)
        path = unquote(parsed.path)

        if source == "coingecko":
            # https://www.coingecko.com/en/coins/bitcoin -> bitcoin
            match = re.search(r'/coins/([^/]+)', path)
            return match.group(1) if match else None

        elif source == "stooq":
            # https://stooq.com/q/?s=aapl.us -> aapl.us
            if parsed.query:
                for part in parsed.query.split('&'):
                    if part.startswith('s='):
                        return part[2:]
            return None

        elif source == "weather":
            # https://wttr.in/Tokyo,Japan -> Tokyo,Japan
            # https://wttr.in/Tokyo,Japan?format=j1 -> Tokyo,Japan
            path_part = path.strip('/')
            return path_part if path_part else None

        elif source == "tmdb":
            # https://www.themoviedb.org/movie/872585 -> 872585
            # https://www.themoviedb.org/movie/872585-oppenheimer -> 872585
            match = re.search(r'/movie/(\d+)', path)
            return match.group(1) if match else None

        elif source == "taostats":
            # https://taostats.io/subnets/27 -> 27
            match = re.search(r'/subnets/(\d+)', path)
            return match.group(1) if match else None

        return None

    # === Data Access ===

    def get_api_data(self, source: str, url: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get API data for a source.

        If url is provided, returns the api_data from that specific page cache.
        If url is None, aggregates all page api_data into the expected structure
        for backward compatibility with templates.

        Returns structure like:
        {
            "coins": {"bitcoin": {...}, "ethereum": {...}},  # for coingecko
            "assets": {"aapl.us": {...}},  # for stooq
            ...
        }
        """
        cache = self._get_source(source)

        if url:
            return cache.get_api_data_for_url(url)

        # Aggregate all page api_data into expected structure
        aggregated = cache.get_aggregated_api_data()
        if not aggregated:
            log("Cache", f"No api_data for {source} - rebuild with --force", force=True)
            return None

        # Wrap in expected structure
        wrapper_key = self.SOURCE_WRAPPER_KEYS.get(source, "data")
        return {
            "_meta": {
                "source": source,
                "aggregated": True,
                "item_count": len(aggregated),
            },
            wrapper_key: aggregated,
        }

    def get_page(self, url: str) -> Optional[PageData]:
        """Get page data by URL."""
        source = self._url_to_source(url)
        if source is None:
            return None
        return self._get_source(source).get_page(url)

    def _url_to_source(self, url: str) -> Optional[str]:
        """Map URL to source name."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        return self.DOMAIN_TO_SOURCE.get(domain)

    # === Update Logic ===

    async def update_source(
        self,
        source: str,
        force: bool = False,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> dict:
        """
        Update a single source.

        For each URL:
        1. Extract asset_id from URL
        2. Fetch API data for that asset
        3. Fetch page HTML
        4. Save together (guaranteed consistency)
        """
        cache = self._get_source(source)
        stats = {
            "source": source,
            "pages_updated": 0,
            "pages_skipped": 0,
            "pages_failed": 0,
        }

        if source not in self._url_generators:
            logger.warning(f"No URL generator for {source}")
            return stats

        all_urls = self._url_generators[source]()
        if not all_urls:
            logger.info(f"[{source}] No URLs to cache")
            return stats

        # Find pages to update
        if force:
            urls_to_update = all_urls
        else:
            urls_to_update = cache.get_stale_urls(all_urls)

        stats["pages_skipped"] = len(all_urls) - len(urls_to_update)

        if not urls_to_update:
            logger.info(f"[{source}] All {len(all_urls)} pages are fresh")
            return stats

        logger.info(f"[{source}] Updating {len(urls_to_update)}/{len(all_urls)} pages")

        # Fetch pages with their API data
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            )
            page = await context.new_page()

            xhr_responses: Dict[str, Any] = {}

            async def handle_response(response):
                if response.request.resource_type in ("xhr", "fetch"):
                    try:
                        content_type = response.headers.get("content-type", "")
                        if "application/json" in content_type:
                            body = await response.json()
                            parsed = urlparse(response.url)
                            xhr_responses[parsed.path] = body
                    except Exception:
                        pass

            page.on("response", handle_response)

            max_retries = 3
            page_timeout = 60000  # 60 seconds

            for i, url in enumerate(urls_to_update, 1):
                xhr_responses.clear()

                if progress_callback:
                    progress_callback(url, i, len(urls_to_update))

                # Extract asset ID
                asset_id = self._extract_asset_id(source, url)
                logger.info(f"  [{i}/{len(urls_to_update)}] {url} (asset: {asset_id})")

                # Retry loop
                success = False
                last_error = None

                for attempt in range(max_retries):
                    try:
                        # Fetch API data for this specific asset
                        api_data = {}
                        if asset_id and source in self._api_fetchers:
                            try:
                                api_data = await self._api_fetchers[source](asset_id)
                                if api_data:
                                    # Log key fields to verify data
                                    sample_keys = list(api_data.keys())[:3]
                                    logger.info(f"    API data fetched for {asset_id}: {sample_keys}...")
                                else:
                                    logger.warning(f"    API returned empty for {asset_id}")
                            except Exception as e:
                                logger.warning(f"    API fetch failed for {asset_id}: {e}")
                        elif not asset_id:
                            logger.warning(f"    Could not extract asset_id from {url}")
                        elif source not in self._api_fetchers:
                            logger.debug(f"    No API fetcher registered for {source}")

                        # Fetch page HTML
                        await page.goto(url, timeout=page_timeout, wait_until="domcontentloaded")

                        try:
                            await page.wait_for_load_state("networkidle", timeout=15000)
                        except Exception:
                            pass

                        # Scroll to trigger lazy loading
                        for pos in [0, 500, 1000, 2000]:
                            await page.evaluate(f"window.scrollTo(0, {pos})")
                            await page.wait_for_timeout(500)

                        await page.evaluate("window.scrollTo(0, 0)")
                        await page.wait_for_timeout(500)

                        html = await page.content()

                        # Save page with its API data (same timestamp = consistent)
                        page_data = PageData(
                            url=url,
                            html=html,
                            api_data=api_data or {},
                            asset_id=asset_id or "",
                            xhr_responses=dict(xhr_responses),
                            status=200,
                        )
                        cache.set_page(page_data)
                        stats["pages_updated"] += 1
                        success = True
                        break

                    except Exception as e:
                        last_error = e
                        if attempt < max_retries - 1:
                            logger.warning(f"    Retry {attempt + 1}/{max_retries} for {url}")
                            await asyncio.sleep(2)  # Wait before retry

                if not success:
                    logger.warning(f"  [{i}/{len(urls_to_update)}] FAILED after {max_retries} attempts: {url} - {last_error}")
                    stats["pages_failed"] += 1

            await context.close()
            await browser.close()

        logger.info(
            f"[{source}] Done: {stats['pages_updated']} updated, "
            f"{stats['pages_skipped']} skipped, {stats['pages_failed']} failed"
        )
        return stats

    async def update_all_sources(
        self,
        sources: List[str],
        force: bool = False,
        concurrent: bool = True,
    ) -> Dict[str, dict]:
        """Update multiple sources."""
        if concurrent:
            tasks = [self.update_source(source, force) for source in sources]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            stats = {}
            for source, result in zip(sources, results):
                if isinstance(result, Exception):
                    logger.error(f"[{source}] Update failed: {result}")
                    stats[source] = {"error": str(result)}
                else:
                    stats[source] = result
            return stats
        else:
            stats = {}
            for source in sources:
                try:
                    stats[source] = await self.update_source(source, force)
                except Exception as e:
                    logger.error(f"[{source}] Update failed: {e}")
                    stats[source] = {"error": str(e)}
            return stats

    # === Status ===

    def get_status(self, sources: Optional[List[str]] = None) -> dict:
        """Get cache status."""
        if sources is None:
            sources = []
            if self.cache_dir.exists():
                for d in self.cache_dir.iterdir():
                    if d.is_dir() and (d / "pages").exists():
                        sources.append(d.name)

        result = {
            "cache_dir": str(self.cache_dir),
            "ttl_hours": self.ttl / 3600,
            "sources": {},
        }

        for source in sources:
            cache = self._get_source(source)
            result["sources"][source] = cache.get_stats()

        return result
