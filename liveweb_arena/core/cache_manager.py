"""
Cache Manager - Unified caching system for web pages and API data.

Operating Modes:
================
The system supports two modes controlled by Actor(use_cache=...):

1. CACHE MODE (use_cache=True):
   - API data: served from versioned snapshots
   - Web pages: served from HAR files (seed-independent, linked to API version)
   - Ensures data consistency between agent view and ground truth
   - Reduces website access (prevents IP blocking)
   - HAR filename based on API versions: coingeckoV5_stooqV3.har
   - When API cache refreshes, HAR is invalidated (new version number)

2. LIVE MODE (use_cache=False):
   - API data: real-time fetch
   - Web pages: real-time browser navigation
   - No caching, no HAR recording

Design Principles:
==================
1. SNAPSHOT CONSISTENCY - All data in a snapshot is from the same time point
2. ATOMIC REFRESH - Web pages and API data refresh together
3. VERSION ISOLATION - Each evaluation uses a fixed snapshot version
4. MULTI-PROCESS SAFE - File locks prevent concurrent write conflicts
5. PERSISTENT STORAGE - File-based cache survives restarts
6. HAR VALIDATION - Validates HAR files before playback, auto-deletes corrupted
7. SEED INDEPENDENCE - HAR files are shared across seeds (web content is seed-independent)

Pre-caching:
============
Run `python precache_har.py` to pre-cache all possible pages before evaluation.
This ensures all evaluations use playback mode for consistent results.
"""

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class CacheConfig:
    """Configuration for a cache source. TTL=0 means permanent cache."""
    ttl: int = 3600             # Seconds (0 = never expire)
    max_versions: int = 3
    preload: bool = True


# TTL presets (seconds)
TTL_1_HOUR = 3600
TTL_2_HOURS = 7200
TTL_PERMANENT = 0


def _get_ttl(default: int) -> int:
    """Get TTL from environment or use default. LIVEWEB_CACHE_PERMANENT=1 overrides to 0."""
    if os.environ.get("LIVEWEB_CACHE_PERMANENT", "").lower() in ("1", "true"):
        return TTL_PERMANENT
    return int(os.environ.get("LIVEWEB_CACHE_TTL", default))


def get_default_cache_configs() -> Dict[str, CacheConfig]:
    """Build cache configs with environment variable support."""
    return {
        "coingecko": CacheConfig(ttl=_get_ttl(TTL_1_HOUR), max_versions=3, preload=True),
        "stooq": CacheConfig(ttl=_get_ttl(TTL_1_HOUR), max_versions=3, preload=True),
        "taostats": CacheConfig(ttl=_get_ttl(TTL_1_HOUR), max_versions=2, preload=False),
        "tmdb": CacheConfig(ttl=_get_ttl(TTL_2_HOURS), max_versions=2, preload=True),
        "weather": CacheConfig(ttl=_get_ttl(TTL_2_HOURS), max_versions=2, preload=True),
    }


@dataclass
class SnapshotMetadata:
    """Metadata for a cache snapshot."""
    version: int
    timestamp: float
    source: str
    ttl: int
    page_keys: List[str] = field(default_factory=list)
    api_keys: List[str] = field(default_factory=list)

    def is_expired(self) -> bool:
        if self.ttl == 0:
            return False
        return time.time() - self.timestamp > self.ttl

    def age_seconds(self) -> float:
        """Return age of snapshot in seconds."""
        return time.time() - self.timestamp

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "timestamp": self.timestamp,
            "source": self.source,
            "ttl": self.ttl,
            "page_keys": self.page_keys,
            "api_keys": self.api_keys,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SnapshotMetadata":
        return cls(
            version=data["version"],
            timestamp=data["timestamp"],
            source=data["source"],
            ttl=data["ttl"],
            page_keys=data.get("page_keys", []),
            api_keys=data.get("api_keys", []),
        )


class FileLockManager:
    """Manages file-based locks for multi-process safety."""

    def __init__(self, lock_dir: Path):
        self.lock_dir = lock_dir
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self._locks: Dict[str, Any] = {}

    def _get_lock_path(self, source: str) -> Path:
        return self.lock_dir / f"{source}.lock"

    @contextmanager
    def read_lock(self, source: str):
        """Acquire a shared read lock."""
        lock_path = self._get_lock_path(source)
        lock_path.touch(exist_ok=True)

        with open(lock_path, "r") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def write_lock(self, source: str):
        """Acquire an exclusive write lock."""
        lock_path = self._get_lock_path(source)
        lock_path.touch(exist_ok=True)

        with open(lock_path, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class CacheManager:
    """
    Unified cache manager for web pages and API data.

    Key features:
    - Snapshot-based versioning ensures consistency
    - Atomic refresh of web + API data
    - Multi-process safe via file locks
    - Persistent file-based storage

    Usage:
        cache = CacheManager()

        # Get or refresh cache for a source
        version = cache.ensure_fresh("coingecko")

        # Read cached page
        html = cache.get_page("coingecko", "https://coingecko.com/en/coins/bitcoin", version)

        # Read cached API data
        data = cache.get_api_data("coingecko", "bitcoin_price", version)
    """

    def __init__(
        self,
        cache_dir: str = None,
        configs: Dict[str, CacheConfig] = None,
    ):
        # Cache directory from environment variable or default
        if cache_dir is None:
            cache_dir = os.environ.get("LIVEWEB_CACHE_DIR", "cache")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.configs = configs or get_default_cache_configs()
        self.lock_manager = FileLockManager(self.cache_dir / "locks")

        # In-memory cache of metadata for performance
        self._metadata_cache: Dict[str, SnapshotMetadata] = {}

        # Registered data fetchers
        self._page_fetchers: Dict[str, Callable] = {}
        self._api_fetchers: Dict[str, Callable] = {}

    def register_fetcher(
        self,
        source: str,
        page_fetcher: Callable = None,
        api_fetcher: Callable = None,
    ):
        """
        Register data fetchers for a source.

        Args:
            source: Cache source name (e.g., "coingecko")
            page_fetcher: Async function to fetch web pages
            api_fetcher: Async function to fetch API data
        """
        if page_fetcher:
            self._page_fetchers[source] = page_fetcher
        if api_fetcher:
            self._api_fetchers[source] = api_fetcher

    def _get_source_dir(self, source: str) -> Path:
        """Get the directory for a cache source."""
        return self.cache_dir / source

    def _get_snapshot_dir(self, source: str, version: int) -> Path:
        """Get the directory for a specific snapshot version."""
        return self._get_source_dir(source) / f"snapshot_v{version:04d}"

    def _get_current_link(self, source: str) -> Path:
        """Get the 'current' symlink path."""
        return self._get_source_dir(source) / "current"

    def _url_to_filename(self, url: str) -> str:
        """Convert URL to a safe filename."""
        # Use hash for uniqueness, keep some readable part
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        # Extract domain and path for readability
        from urllib.parse import urlparse
        parsed = urlparse(url)
        safe_path = parsed.path.replace("/", "_").strip("_")[:50]
        return f"{parsed.netloc}_{safe_path}_{url_hash}"

    def get_current_version(self, source: str) -> Optional[int]:
        """Get the current snapshot version for a source."""
        current_link = self._get_current_link(source)
        if not current_link.exists():
            return None

        try:
            target = current_link.resolve()
            # Extract version from directory name (snapshot_vXXXX)
            version_str = target.name.split("_v")[-1]
            return int(version_str)
        except (ValueError, OSError):
            return None

    def get_metadata(self, source: str, version: int = None) -> Optional[SnapshotMetadata]:
        """Get metadata for a snapshot."""
        if version is None:
            version = self.get_current_version(source)
        if version is None:
            return None

        # Check in-memory cache first
        cache_key = f"{source}_v{version}"
        if cache_key in self._metadata_cache:
            return self._metadata_cache[cache_key]

        # Read from file
        snapshot_dir = self._get_snapshot_dir(source, version)
        metadata_path = snapshot_dir / "metadata.json"

        if not metadata_path.exists():
            return None

        try:
            with open(metadata_path, "r") as f:
                data = json.load(f)
            metadata = SnapshotMetadata.from_dict(data)
            self._metadata_cache[cache_key] = metadata
            return metadata
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning(f"Failed to read metadata for {source} v{version}: {e}")
            return None

    def is_cache_valid(self, source: str) -> bool:
        """Check if the current cache for a source is valid (not expired)."""
        metadata = self.get_metadata(source)
        if metadata is None:
            return False
        return not metadata.is_expired()

    async def ensure_fresh(
        self,
        source: str,
        force_refresh: bool = False,
    ) -> int:
        """
        Ensure cache is fresh, refreshing if needed.

        Args:
            source: Cache source name
            force_refresh: Force refresh even if cache is valid

        Returns:
            Current snapshot version number
        """
        with self.lock_manager.write_lock(source):
            if not force_refresh and self.is_cache_valid(source):
                return self.get_current_version(source)

            # Need to refresh
            return await self._refresh_cache_locked(source)

    async def _refresh_cache_locked(self, source: str) -> int:
        """
        Refresh cache for a source (must hold write lock).

        This is async to properly handle async fetchers.
        """
        logger.info(f"Refreshing cache for {source}...")

        # Determine new version number
        current_version = self.get_current_version(source)
        new_version = (current_version or 0) + 1

        # Create new snapshot directory
        snapshot_dir = self._get_snapshot_dir(source, new_version)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        # Note: pages directory is no longer created - page caching now uses HAR files

        config = self.configs.get(source, CacheConfig())
        page_keys = []
        api_keys = []

        try:
            # Fetch and save API data
            if source in self._api_fetchers:
                api_data = await self._api_fetchers[source]()
                if api_data:
                    api_path = snapshot_dir / "api_data.json"
                    with open(api_path, "w") as f:
                        json.dump(api_data, f, indent=2)
                    api_keys = list(api_data.keys()) if isinstance(api_data, dict) else []

            # Fetch and save pages (if fetcher registered)
            if source in self._page_fetchers:
                pages = await self._page_fetchers[source]()
                if pages:
                    for url, content in pages.items():
                        filename = self._url_to_filename(url)
                        page_path = pages_dir / f"{filename}.html"
                        with open(page_path, "w", encoding="utf-8") as f:
                            f.write(content)
                        page_keys.append(url)

            # Save metadata
            metadata = SnapshotMetadata(
                version=new_version,
                timestamp=time.time(),
                source=source,
                ttl=config.ttl,
                page_keys=page_keys,
                api_keys=api_keys,
            )
            metadata_path = snapshot_dir / "metadata.json"
            with open(metadata_path, "w") as f:
                json.dump(metadata.to_dict(), f, indent=2)

            # Update current symlink atomically
            current_link = self._get_current_link(source)
            temp_link = current_link.with_suffix(".tmp")

            # Remove old temp link if exists
            if temp_link.exists() or temp_link.is_symlink():
                temp_link.unlink()

            # Create new symlink and rename atomically
            temp_link.symlink_to(snapshot_dir.name)
            temp_link.rename(current_link)

            # Cleanup old versions
            self._cleanup_old_versions(source, config.max_versions)

            # Update in-memory cache
            cache_key = f"{source}_v{new_version}"
            self._metadata_cache[cache_key] = metadata

            logger.info(f"Cache refreshed for {source}: v{new_version}")
            return new_version

        except Exception as e:
            # Cleanup failed snapshot
            logger.error(f"Failed to refresh cache for {source}: {e}")
            if snapshot_dir.exists():
                shutil.rmtree(snapshot_dir, ignore_errors=True)
            raise

    def _cleanup_old_versions(self, source: str, max_versions: int):
        """Remove old snapshot versions beyond max_versions."""
        source_dir = self._get_source_dir(source)
        if not source_dir.exists():
            return

        # Find all snapshot directories
        snapshots = []
        for item in source_dir.iterdir():
            if item.is_dir() and item.name.startswith("snapshot_v"):
                try:
                    version = int(item.name.split("_v")[-1])
                    snapshots.append((version, item))
                except ValueError:
                    continue

        # Sort by version descending
        snapshots.sort(key=lambda x: x[0], reverse=True)

        # Remove old versions
        for version, path in snapshots[max_versions:]:
            logger.debug(f"Removing old cache version: {source} v{version}")
            shutil.rmtree(path, ignore_errors=True)
            # Also remove from in-memory cache
            cache_key = f"{source}_v{version}"
            self._metadata_cache.pop(cache_key, None)

    def get_page(
        self,
        source: str,
        url: str,
        version: int = None,
    ) -> Optional[str]:
        """
        Get cached page content.

        Args:
            source: Cache source name
            url: Page URL
            version: Specific version (default: current)

        Returns:
            Page HTML content or None if not found
        """
        if version is None:
            version = self.get_current_version(source)
        if version is None:
            return None

        with self.lock_manager.read_lock(source):
            snapshot_dir = self._get_snapshot_dir(source, version)
            filename = self._url_to_filename(url)
            page_path = snapshot_dir / "pages" / f"{filename}.html"

            if not page_path.exists():
                return None

            try:
                with open(page_path, "r", encoding="utf-8") as f:
                    return f.read()
            except OSError as e:
                logger.warning(f"Failed to read cached page {url}: {e}")
                return None

    def get_api_data(
        self,
        source: str,
        key: str = None,
        version: int = None,
    ) -> Optional[Any]:
        """
        Get cached API data.

        Args:
            source: Cache source name
            key: Specific data key (default: return all data)
            version: Specific version (default: current)

        Returns:
            API data or None if not found
        """
        if version is None:
            version = self.get_current_version(source)
        if version is None:
            return None

        with self.lock_manager.read_lock(source):
            snapshot_dir = self._get_snapshot_dir(source, version)
            api_path = snapshot_dir / "api_data.json"

            if not api_path.exists():
                return None

            try:
                with open(api_path, "r") as f:
                    data = json.load(f)

                if key is None:
                    return data
                return data.get(key)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to read cached API data for {source}: {e}")
                return None

    def save_page(
        self,
        source: str,
        url: str,
        content: str,
        version: int = None,
    ):
        """
        Save a page to the current snapshot.

        Note: This should typically be done during refresh, not individually.
        """
        if version is None:
            version = self.get_current_version(source)
        if version is None:
            logger.warning(f"No current version for {source}, cannot save page")
            return

        with self.lock_manager.write_lock(source):
            snapshot_dir = self._get_snapshot_dir(source, version)
            pages_dir = snapshot_dir / "pages"
            pages_dir.mkdir(parents=True, exist_ok=True)

            filename = self._url_to_filename(url)
            page_path = pages_dir / f"{filename}.html"

            with open(page_path, "w", encoding="utf-8") as f:
                f.write(content)

    def save_api_data(
        self,
        source: str,
        data: Dict[str, Any],
        version: int = None,
    ):
        """
        Save API data to the current snapshot.

        Note: This should typically be done during refresh, not individually.
        """
        if version is None:
            version = self.get_current_version(source)
        if version is None:
            logger.warning(f"No current version for {source}, cannot save API data")
            return

        with self.lock_manager.write_lock(source):
            snapshot_dir = self._get_snapshot_dir(source, version)
            api_path = snapshot_dir / "api_data.json"

            # Merge with existing data if any
            existing = {}
            if api_path.exists():
                try:
                    with open(api_path, "r") as f:
                        existing = json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass

            existing.update(data)

            with open(api_path, "w") as f:
                json.dump(existing, f, indent=2)

    def get_cache_info(self, source: str = None) -> Dict[str, Any]:
        """Get information about cache status."""
        if source:
            sources = [source]
        else:
            sources = list(self.configs.keys())

        info = {}
        for src in sources:
            metadata = self.get_metadata(src)
            if metadata:
                info[src] = {
                    "version": metadata.version,
                    "timestamp": metadata.timestamp,
                    "age_seconds": metadata.age_seconds(),
                    "expired": metadata.is_expired(),
                    "ttl": metadata.ttl,
                    "page_count": len(metadata.page_keys),
                    "api_keys": metadata.api_keys,
                }
            else:
                info[src] = {"status": "no_cache"}

        return info

    def clear_cache(self, source: str = None):
        """Clear cache for a source or all sources."""
        if source:
            sources = [source]
        else:
            sources = list(self.configs.keys())

        for src in sources:
            with self.lock_manager.write_lock(src):
                source_dir = self._get_source_dir(src)
                if source_dir.exists():
                    shutil.rmtree(source_dir)
                # Clear in-memory cache
                keys_to_remove = [k for k in self._metadata_cache if k.startswith(f"{src}_")]
                for key in keys_to_remove:
                    del self._metadata_cache[key]

        logger.info(f"Cleared cache for: {sources}")


class PageCacheProxy:
    """
    Page caching proxy for browser requests.

    This proxy intercepts browser requests and:
    1. If page is cached, returns cached content
    2. If not cached, fetches from real site, caches it, then returns

    This ensures:
    - Consistent data during evaluation (agent sees same data as ground truth)
    - Reduced requests to real websites (prevents IP blocking)
    - Faster subsequent evaluations with same content
    """

    # User agent for fetching pages
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        cache_manager: "CacheManager",
        source: str,
        version: int,
        allowed_domains: Optional[Set[str]] = None,
    ):
        """
        Initialize page cache proxy.

        Args:
            cache_manager: Cache manager for persistent storage
            source: Cache source name (e.g., "pages")
            version: Snapshot version to use
            allowed_domains: If set, only cache pages from these domains
        """
        self.cache_manager = cache_manager
        self.source = source
        self.version = version
        self.allowed_domains = allowed_domains
        # In-memory cache for current session
        self._memory_cache: Dict[str, str] = {}
        self._fetch_lock = asyncio.Lock()
        # Track fetch stats
        self._stats = {"hits": 0, "misses": 0, "fetches": 0, "errors": 0}

    def _normalize_url(self, url: str) -> str:
        """Normalize URL for cache lookup (remove fragments, etc.)."""
        parsed = urlparse(url)
        # Remove fragment, keep everything else
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            normalized += f"?{parsed.query}"
        return normalized

    def _is_allowed_domain(self, url: str) -> bool:
        """Check if URL's domain is in allowed list."""
        if not self.allowed_domains:
            return True
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if ":" in domain:
                domain = domain.split(":")[0]
            for allowed in self.allowed_domains:
                if domain == allowed or domain.endswith("." + allowed):
                    return True
            return False
        except Exception:
            return False

    def get_cached(self, url: str) -> Optional[str]:
        """
        Get cached page content if available.

        Returns:
            Page content if cached, None otherwise
        """
        normalized = self._normalize_url(url)

        # Check memory cache first
        if normalized in self._memory_cache:
            self._stats["hits"] += 1
            return self._memory_cache[normalized]

        # Check persistent cache
        content = self.cache_manager.get_page(self.source, normalized, self.version)
        if content:
            self._memory_cache[normalized] = content
            self._stats["hits"] += 1
            return content

        self._stats["misses"] += 1
        return None

    async def fetch_and_cache(self, url: str) -> Optional[str]:
        """
        Fetch page from real site and cache it.

        Args:
            url: URL to fetch

        Returns:
            Page content if successful, None on error
        """
        if not self._is_allowed_domain(url):
            return None

        normalized = self._normalize_url(url)

        # Use lock to prevent duplicate fetches
        async with self._fetch_lock:
            # Double-check cache after acquiring lock
            cached = self.get_cached(url)
            if cached:
                return cached

            try:
                import aiohttp

                headers = {"User-Agent": self.USER_AGENT}
                timeout = aiohttp.ClientTimeout(total=30)

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url, headers=headers) as response:
                        if response.status == 200:
                            content = await response.text()
                            # Cache in memory
                            self._memory_cache[normalized] = content
                            # Cache to disk
                            self.cache_manager.save_page(
                                self.source, normalized, content, self.version
                            )
                            self._stats["fetches"] += 1
                            logger.debug(f"Cached page: {url[:80]}...")
                            return content
                        else:
                            logger.warning(f"Failed to fetch {url}: HTTP {response.status}")
                            self._stats["errors"] += 1
                            return None

            except Exception as e:
                logger.warning(f"Error fetching {url}: {e}")
                self._stats["errors"] += 1
                return None

    def get_stats(self) -> Dict[str, int]:
        """Get cache statistics."""
        return self._stats.copy()


class EvaluationCacheContext:
    """
    Context manager for evaluation that locks cache versions.

    Ensures that all cache reads during an evaluation use the same
    snapshot version, even if the cache is refreshed during evaluation.

    Also provides a page cache proxy for browser request caching, which:
    - Caches pages visited during evaluation to reduce real website access
    - Ensures consistency between agent view and ground truth data

    Usage:
        async with EvaluationCacheContext(cache_manager, ["coingecko", "stooq"]) as ctx:
            # All cache reads will use the versions locked at context entry
            page = ctx.get_page("coingecko", url)
            data = ctx.get_api_data("coingecko", key)

            # Get page cache proxy for browser
            proxy = ctx.get_page_cache_proxy(allowed_domains=["coingecko.com"])
            # Pass to browser session for request interception
    """

    def __init__(
        self,
        cache_manager: CacheManager,
        sources: List[str],
        ensure_fresh: bool = True,
    ):
        self.cache_manager = cache_manager
        self.sources = sources
        self._ensure_fresh = ensure_fresh
        self.locked_versions: Dict[str, int] = {}
        self._page_cache_proxy: Optional[PageCacheProxy] = None

    async def __aenter__(self):
        """Lock cache versions for all required sources."""
        for source in self.sources:
            try:
                if self._ensure_fresh:
                    # Ensure cache is fresh and get version
                    version = await self.cache_manager.ensure_fresh(source)
                else:
                    version = self.cache_manager.get_current_version(source)

                if version is not None:
                    self.locked_versions[source] = version
                    logger.debug(f"Locked cache version: {source} v{version}")
            except Exception as e:
                logger.warning(f"Failed to initialize cache for {source}: {e}")
                # Continue without this source - will fall back to live API

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Release (nothing to do, versions are just recorded)."""
        pass

    def get_page(self, source: str, url: str) -> Optional[str]:
        """Get page using locked version."""
        version = self.locked_versions.get(source)
        return self.cache_manager.get_page(source, url, version)

    def get_api_data(self, source: str, key: str = None) -> Optional[Any]:
        """Get API data using locked version."""
        version = self.locked_versions.get(source)
        return self.cache_manager.get_api_data(source, key, version)

    def get_locked_version(self, source: str) -> Optional[int]:
        """Get the locked version for a source."""
        return self.locked_versions.get(source)

    def get_page_cache_proxy(
        self,
        allowed_domains: Optional[List[str]] = None,
    ) -> PageCacheProxy:
        """
        Get the page cache proxy for browser request caching.

        Creates a proxy that caches pages during evaluation. The proxy uses
        a dedicated "pages" source in the cache manager.

        Args:
            allowed_domains: If set, only cache pages from these domains

        Returns:
            PageCacheProxy instance for this evaluation context
        """
        if self._page_cache_proxy is None:
            # Use a dedicated "pages" source for page caching
            # Create version based on current timestamp to isolate sessions
            source = "pages"
            version = self.cache_manager.get_current_version(source)
            if version is None:
                # Create initial version for pages source
                version = 1
                snapshot_dir = self.cache_manager._get_snapshot_dir(source, version)
                snapshot_dir.mkdir(parents=True, exist_ok=True)
                (snapshot_dir / "pages").mkdir(exist_ok=True)

            domain_set = set(d.lower() for d in allowed_domains) if allowed_domains else None
            self._page_cache_proxy = PageCacheProxy(
                cache_manager=self.cache_manager,
                source=source,
                version=version,
                allowed_domains=domain_set,
            )

        return self._page_cache_proxy

    def get_page_cache_stats(self) -> Optional[Dict[str, int]]:
        """Get page cache statistics if proxy was used."""
        if self._page_cache_proxy:
            return self._page_cache_proxy.get_stats()
        return None

    def get_har_cache_info(self, seed: int = None) -> tuple:
        """
        Get HAR cache path and mode for browser session.

        HAR files are stored per-source-version (NOT per-seed) because web page
        content is independent of seed. This allows:
        - Pre-caching all possible pages once
        - Reusing HAR across different seeds
        - Consistent evaluation (always playback mode after initial caching)

        Args:
            seed: Deprecated, kept for backwards compatibility but ignored

        Returns:
            (har_path, har_mode) tuple
        """
        cache_dir = self.cache_manager.cache_dir / "har"
        cache_dir.mkdir(parents=True, exist_ok=True)

        # HAR filename based on locked API versions only (not seed)
        # Example: coingeckov5_stooqv3.har
        version_str = "_".join(f"{s}v{v}" for s, v in sorted(self.locked_versions.items()))
        har_filename = f"{version_str}.har" if version_str else "default.har"
        har_path = cache_dir / har_filename

        if har_path.exists():
            # Validate HAR file before using for playback
            if self._validate_har_file(har_path):
                return har_path, "playback"
            else:
                # Invalid HAR file - delete and re-record
                logger.warning(f"Invalid HAR file detected, will re-record: {har_path}")
                try:
                    har_path.unlink()
                except OSError:
                    pass
                return har_path, "record"
        else:
            return har_path, "record"

    def _validate_har_file(self, har_path: Path) -> bool:
        """
        Validate that a HAR file is complete and usable.

        Checks:
        1. File is valid JSON
        2. Has proper HAR structure (log.entries)
        3. Has at least one entry (not empty recording)
        4. File is not suspiciously small (< 1KB might be corrupted)

        Args:
            har_path: Path to HAR file

        Returns:
            True if HAR is valid, False otherwise
        """
        try:
            # Check minimum file size (corrupted/incomplete files are often tiny)
            file_size = har_path.stat().st_size
            if file_size < 1024:  # Less than 1KB is suspicious
                logger.debug(f"HAR file too small ({file_size} bytes): {har_path}")
                return False

            # Parse and validate structure
            with open(har_path, "r") as f:
                har_data = json.load(f)

            # Check HAR structure
            if "log" not in har_data:
                logger.debug(f"HAR file missing 'log' key: {har_path}")
                return False

            entries = har_data.get("log", {}).get("entries", [])
            if not entries:
                logger.debug(f"HAR file has no entries: {har_path}")
                return False

            # Valid HAR file
            logger.debug(f"HAR file valid ({len(entries)} entries, {file_size/1024:.1f}KB): {har_path}")
            return True

        except json.JSONDecodeError as e:
            logger.debug(f"HAR file is not valid JSON: {har_path} - {e}")
            return False
        except OSError as e:
            logger.debug(f"Cannot read HAR file: {har_path} - {e}")
            return False

    def acquire_har_lock(self, har_path: Path, timeout: float = 5.0) -> bool:
        """
        Try to acquire a lock for HAR recording.

        Prevents concurrent recordings to the same HAR file.

        Args:
            har_path: Path to HAR file
            timeout: Max seconds to wait for lock

        Returns:
            True if lock acquired, False if another process is recording
        """
        lock_path = har_path.with_suffix(".lock")
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                # Attempt to create lock file exclusively
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return True
            except FileExistsError:
                # Lock exists - check if it's stale (> 10 minutes old)
                try:
                    lock_age = time.time() - lock_path.stat().st_mtime
                    if lock_age > 600:  # 10 minutes
                        logger.warning(f"Removing stale HAR lock: {lock_path}")
                        lock_path.unlink()
                        continue
                except OSError:
                    pass
                time.sleep(0.5)
            except OSError:
                return False

        return False

    def release_har_lock(self, har_path: Path):
        """Release HAR recording lock."""
        lock_path = har_path.with_suffix(".lock")
        try:
            lock_path.unlink()
        except OSError:
            pass

    def get_har_stats(self) -> dict:
        """
        Get statistics about HAR cache.

        Returns:
            Dict with HAR cache statistics
        """
        cache_dir = self.cache_manager.cache_dir / "har"
        if not cache_dir.exists():
            return {"total_files": 0, "total_size_mb": 0, "seeds": []}

        har_files = list(cache_dir.glob("*.har"))
        total_size = sum(f.stat().st_size for f in har_files)

        # Extract seed numbers from filenames
        seeds = []
        for f in har_files:
            parts = f.stem.split("_")
            if len(parts) >= 2 and parts[0] == "seed":
                try:
                    seeds.append(int(parts[1]))
                except ValueError:
                    pass

        return {
            "total_files": len(har_files),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "seeds": sorted(seeds),
            "cache_dir": str(cache_dir),
        }


# Global cache manager instance
_global_cache_manager: Optional[CacheManager] = None


def get_cache_manager() -> CacheManager:
    """Get the global cache manager instance."""
    global _global_cache_manager
    if _global_cache_manager is None:
        _global_cache_manager = CacheManager()
    return _global_cache_manager


def set_cache_manager(manager: CacheManager):
    """Set the global cache manager instance."""
    global _global_cache_manager
    _global_cache_manager = manager
