"""
Per-Source Atomic Snapshot Cache System.

Design principles:
1. Per-source isolation: Each source has its own snapshot directory
2. Atomic per-source: API and page data for ONE source from same time window
3. Independent TTL: Each source can have different TTL and update independently
4. Incremental updates: Missing source only requires updating that source

Directory structure:
    cache/
    ├── coingecko/
    │   ├── current -> snapshot_xxx/
    │   └── snapshot_xxx/
    │       ├── meta.json       # Source metadata
    │       ├── api.json        # API data
    │       └── pages/          # Page data
    ├── stooq/
    │   └── ...
    └── weather/
        └── ...

Usage:
    manager = CacheManager(Path("cache"))

    # Ensure all required sources are cached
    manager.ensure_sources(["coingecko", "stooq"])

    # Get API data
    data = manager.get_api_data("coingecko")

    # Get page
    html = manager.get_page("https://coingecko.com/en/coins/bitcoin")
"""

import asyncio
import fcntl
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Default TTL: 24 hours
DEFAULT_TTL = 24 * 3600


@dataclass
class SourceMeta:
    """Metadata for a single source snapshot."""
    source: str
    snapshot_id: str
    created_at: float
    ttl: int
    api_item_count: int = 0
    page_count: int = 0

    @property
    def expires_at(self) -> float:
        return self.created_at + self.ttl

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def time_remaining(self) -> float:
        return max(0, self.expires_at - time.time())

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "ttl": self.ttl,
            "expires_at": self.expires_at,
            "api_item_count": self.api_item_count,
            "page_count": self.page_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SourceMeta":
        return cls(
            source=data["source"],
            snapshot_id=data["snapshot_id"],
            created_at=data["created_at"],
            ttl=data["ttl"],
            api_item_count=data.get("api_item_count", 0),
            page_count=data.get("page_count", 0),
        )


class SourceSnapshot:
    """
    Represents an atomic snapshot for a single source.

    Directory structure:
        snapshot_YYYYMMDD_HHMMSS/
        ├── meta.json       # Source metadata
        ├── api.json        # API data (flat, no wrapper)
        └── pages/          # Page data
            └── en_coins_bitcoin.json
    """

    def __init__(self, path: Path, meta: Optional[SourceMeta] = None):
        self.path = path
        self._meta = meta
        self._api_cache: Optional[dict] = None

    @property
    def id(self) -> str:
        return self.path.name

    @property
    def source(self) -> str:
        return self.meta.source

    @property
    def meta(self) -> SourceMeta:
        if self._meta is None:
            self._meta = self._load_meta()
        return self._meta

    def is_expired(self) -> bool:
        return self.meta.is_expired()

    def _load_meta(self) -> SourceMeta:
        meta_path = self.path / "meta.json"
        if not meta_path.exists():
            raise ValueError(f"Snapshot meta not found: {meta_path}")
        with open(meta_path) as f:
            return SourceMeta.from_dict(json.load(f))

    def _save_meta(self):
        meta_path = self.path / "meta.json"
        with open(meta_path, 'w') as f:
            json.dump(self.meta.to_dict(), f, indent=2)

    # === API Data ===

    def get_api_data(self) -> Optional[dict]:
        """Get all API data for this source."""
        if self._api_cache is not None:
            return self._api_cache

        api_path = self.path / "api.json"
        if not api_path.exists():
            return None

        with open(api_path) as f:
            self._api_cache = json.load(f)
        return self._api_cache

    def set_api_data(self, data: dict):
        """Save API data."""
        api_path = self.path / "api.json"
        with open(api_path, 'w') as f:
            json.dump(data, f)
        self._api_cache = data
        self._meta.api_item_count = len(data)

    # === Page Data ===

    def _url_to_page_path(self, url: str) -> Path:
        """Convert URL to page storage path."""
        parsed = urlparse(url)

        # Convert path to filename
        path = parsed.path.strip("/").replace("/", "_") or "index"
        if parsed.query:
            query_hash = hashlib.md5(parsed.query.encode()).hexdigest()[:8]
            path = f"{path}_{query_hash}"

        # Sanitize
        safe_path = "".join(c if c.isalnum() or c in "._-" else "_" for c in path)
        return self.path / "pages" / f"{safe_path}.json"

    def get_page(self, url: str) -> Optional[Dict[str, Any]]:
        """Get cached page data."""
        page_path = self._url_to_page_path(url)
        if not page_path.exists():
            return None
        with open(page_path) as f:
            return json.load(f)

    def set_page(
        self,
        url: str,
        html: str,
        status: int = 200,
        headers: Optional[Dict[str, str]] = None,
        api_responses: Optional[Dict[str, Any]] = None,
    ):
        """Save page data."""
        page_path = self._url_to_page_path(url)
        page_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "url": url,
            "fetched_at": time.time(),
            "status": status,
            "headers": headers or {},
            "html": html,
            "api_responses": api_responses or {},
        }

        with open(page_path, 'w') as f:
            json.dump(data, f)

    def has_page(self, url: str) -> bool:
        """Check if page is cached."""
        return self._url_to_page_path(url).exists()

    def count_pages(self) -> int:
        """Count cached pages."""
        pages_dir = self.path / "pages"
        if not pages_dir.exists():
            return 0
        return len(list(pages_dir.glob("*.json")))


class SourceSnapshotManager:
    """Manages snapshots for a single source."""

    def __init__(self, source_dir: Path, source: str, ttl: int = DEFAULT_TTL):
        self.source_dir = source_dir
        self.source = source
        self.ttl = ttl

    def get_current(self) -> Optional[SourceSnapshot]:
        """Get current active snapshot for this source."""
        current_link = self.source_dir / "current"

        if not current_link.exists():
            return None

        if current_link.is_symlink():
            target = current_link.resolve()
        else:
            target = current_link

        if not target.exists():
            return None

        try:
            return SourceSnapshot(target)
        except Exception as e:
            logger.warning(f"Failed to load {self.source} snapshot: {e}")
            return None

    def create_snapshot(self) -> SourceSnapshot:
        """Create a new empty snapshot."""
        self.source_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        snapshot_id = f"snapshot_{timestamp}"
        snapshot_path = self.source_dir / snapshot_id
        snapshot_path.mkdir(parents=True, exist_ok=True)

        meta = SourceMeta(
            source=self.source,
            snapshot_id=snapshot_id,
            created_at=time.time(),
            ttl=self.ttl,
        )

        snapshot = SourceSnapshot(snapshot_path, meta)
        snapshot._save_meta()

        logger.info(f"Created {self.source} snapshot: {snapshot_id}")
        return snapshot

    def activate_snapshot(self, snapshot: SourceSnapshot):
        """Atomically activate a snapshot."""
        current_link = self.source_dir / "current"
        tmp_link = self.source_dir / "current.tmp"

        if tmp_link.exists() or tmp_link.is_symlink():
            tmp_link.unlink()

        tmp_link.symlink_to(snapshot.path.name)
        tmp_link.rename(current_link)

        logger.info(f"Activated {self.source} snapshot: {snapshot.id}")
        self._cleanup_old_snapshots()

    def _cleanup_old_snapshots(self, max_keep: int = 3):
        """Remove old snapshots."""
        if not self.source_dir.exists():
            return

        snapshots = []
        for d in self.source_dir.iterdir():
            if d.is_dir() and d.name.startswith("snapshot_"):
                try:
                    meta_path = d / "meta.json"
                    if meta_path.exists():
                        with open(meta_path) as f:
                            created_at = json.load(f).get("created_at", 0)
                        snapshots.append((d, created_at))
                except Exception:
                    pass

        snapshots.sort(key=lambda x: x[1], reverse=True)

        current = self.get_current()
        current_path = current.path if current else None

        for snapshot_path, _ in snapshots[max_keep:]:
            if snapshot_path != current_path:
                try:
                    import shutil
                    shutil.rmtree(snapshot_path)
                    logger.info(f"Removed old {self.source} snapshot: {snapshot_path.name}")
                except Exception as e:
                    logger.warning(f"Failed to remove snapshot: {e}")


class CacheManager:
    """
    Main cache manager coordinating multiple source snapshots.

    Usage:
        manager = CacheManager(Path("cache"))

        # Register fetchers
        manager.register_source("coingecko", api_fetcher, url_generator)

        # Ensure sources are ready
        manager.ensure_sources(["coingecko", "stooq"])

        # Get data
        data = manager.get_api_data("coingecko")
        page = manager.get_page("https://coingecko.com/...")
    """

    # Domain to source mapping
    DOMAIN_TO_SOURCE = {
        "coingecko.com": "coingecko",
        "www.coingecko.com": "coingecko",
        "stooq.com": "stooq",
        "www.stooq.com": "stooq",
        "wttr.in": "weather",
        "themoviedb.org": "tmdb",
        "www.themoviedb.org": "tmdb",
    }

    def __init__(self, cache_dir: Path, ttl: int = DEFAULT_TTL):
        self.cache_dir = Path(cache_dir)
        self.ttl = ttl

        self._source_managers: Dict[str, SourceSnapshotManager] = {}
        self._api_fetchers: Dict[str, Callable] = {}
        self._url_generators: Dict[str, Callable] = {}

    def _get_source_manager(self, source: str) -> SourceSnapshotManager:
        """Get or create manager for a source."""
        if source not in self._source_managers:
            source_dir = self.cache_dir / source
            self._source_managers[source] = SourceSnapshotManager(
                source_dir, source, self.ttl
            )
        return self._source_managers[source]

    def register_source(
        self,
        source: str,
        api_fetcher: Callable,
        url_generator: Optional[Callable] = None,
    ):
        """Register fetchers for a source."""
        self._api_fetchers[source] = api_fetcher
        if url_generator:
            self._url_generators[source] = url_generator

    def register_api_fetcher(self, source: str, fetcher: Callable):
        """Register API fetcher (compatibility method)."""
        self._api_fetchers[source] = fetcher

    def register_url_generator(self, source: str, generator: Callable):
        """Register URL generator (compatibility method)."""
        self._url_generators[source] = generator

    # === Snapshot Management ===

    def get_source_snapshot(self, source: str) -> Optional[SourceSnapshot]:
        """Get current snapshot for a source."""
        manager = self._get_source_manager(source)
        return manager.get_current()

    def ensure_sources(
        self,
        sources: List[str],
        force_update: bool = False
    ) -> Dict[str, SourceSnapshot]:
        """
        Ensure all specified sources have valid snapshots.

        Args:
            sources: List of source names
            force_update: Force update all sources

        Returns:
            Dict mapping source name to snapshot
        """
        result = {}
        sources_to_update = []

        for source in sources:
            snapshot = self.get_source_snapshot(source)
            if force_update or snapshot is None or snapshot.is_expired():
                sources_to_update.append(source)
            else:
                result[source] = snapshot
                logger.info(
                    f"{source}: Using existing snapshot "
                    f"(expires in {snapshot.meta.time_remaining()/3600:.1f}h)"
                )

        if sources_to_update:
            updated = self._update_sources_with_lock(sources_to_update)
            result.update(updated)

        return result

    def update_sources(self, sources: List[str], force: bool = True) -> Dict[str, SourceSnapshot]:
        """Force update specified sources."""
        return self._update_sources_with_lock(sources, force=force)

    def _update_sources_with_lock(
        self,
        sources: List[str],
        force: bool = False
    ) -> Dict[str, SourceSnapshot]:
        """Update sources with lock protection."""
        result = {}

        for source in sources:
            lock_path = self.cache_dir / source / "create.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)

            with open(lock_path, 'w') as lock_file:
                logger.info(f"[{source}] Acquiring lock...")
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

                try:
                    # Double-check after lock (skip if forcing)
                    if not force:
                        manager = self._get_source_manager(source)
                        snapshot = manager.get_current()
                        if snapshot and not snapshot.is_expired():
                            logger.info(f"[{source}] Already updated by another process")
                            result[source] = snapshot
                            continue

                    # Create new snapshot
                    logger.info(f"[{source}] Creating snapshot...")

                    try:
                        loop = asyncio.get_running_loop()
                        import nest_asyncio
                        nest_asyncio.apply()
                        snapshot = asyncio.run(self._create_source_snapshot(source))
                    except RuntimeError:
                        snapshot = asyncio.run(self._create_source_snapshot(source))

                    result[source] = snapshot

                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

        return result

    async def _create_source_snapshot(self, source: str) -> SourceSnapshot:
        """Create snapshot for a single source."""
        manager = self._get_source_manager(source)
        snapshot = manager.create_snapshot()

        try:
            # Fetch API data
            if source in self._api_fetchers:
                logger.info(f"[{source}] Fetching API data...")
                fetcher = self._api_fetchers[source]
                data = await fetcher()
                snapshot.set_api_data(data)
                logger.info(f"[{source}] API: {len(data)} items")

            # Fetch pages
            if source in self._url_generators:
                urls = self._url_generators[source]()
                if urls:
                    logger.info(f"[{source}] Fetching {len(urls)} pages...")
                    await self._fetch_pages(snapshot, urls)
                    snapshot._meta.page_count = snapshot.count_pages()
                    logger.info(f"[{source}] Pages: {snapshot._meta.page_count} cached")

            snapshot._save_meta()
            manager.activate_snapshot(snapshot)

            logger.info(f"[{source}] Done")
            return snapshot

        except Exception as e:
            logger.error(f"{source}: Snapshot creation failed: {e}")
            import shutil
            if snapshot.path.exists():
                shutil.rmtree(snapshot.path)
            raise

    async def _fetch_pages(self, snapshot: SourceSnapshot, urls: List[str]):
        """Fetch pages for a source."""
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

            api_responses: Dict[str, Any] = {}

            async def handle_response(response):
                if response.request.resource_type in ("xhr", "fetch"):
                    try:
                        content_type = response.headers.get("content-type", "")
                        if "application/json" in content_type:
                            body = await response.json()
                            parsed = urlparse(response.url)
                            api_responses[parsed.path] = body
                    except Exception:
                        pass

            page.on("response", handle_response)

            success = 0
            failed = 0

            for i, url in enumerate(urls, 1):
                api_responses.clear()

                try:
                    logger.info(f"  [{i}/{len(urls)}] {url}")

                    await page.goto(url, timeout=30000, wait_until="domcontentloaded")

                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass

                    # Scroll to trigger lazy loading
                    for pos in [0, 500, 1000, 2000]:
                        await page.evaluate(f"window.scrollTo(0, {pos})")
                        await page.wait_for_timeout(500)

                    await page.evaluate("window.scrollTo(0, 0)")
                    await page.wait_for_timeout(500)

                    html = await page.content()

                    snapshot.set_page(
                        url=url,
                        html=html,
                        status=200,
                        api_responses=dict(api_responses),
                    )
                    success += 1

                except Exception as e:
                    logger.warning(f"  [{i}/{len(urls)}] FAILED: {url} - {e}")
                    failed += 1

            await context.close()
            await browser.close()

            logger.info(f"  Pages cached: {success} success, {failed} failed")

    # === Data Access ===

    def get_api_data(self, source: str) -> Optional[dict]:
        """Get API data for a source."""
        snapshot = self.get_source_snapshot(source)
        if snapshot is None:
            return None
        return snapshot.get_api_data()

    def get_page(self, url: str) -> Optional[Dict[str, Any]]:
        """Get cached page data by URL."""
        source = self._url_to_source(url)
        if source is None:
            return None

        snapshot = self.get_source_snapshot(source)
        if snapshot is None:
            return None

        return snapshot.get_page(url)

    def _url_to_source(self, url: str) -> Optional[str]:
        """Map URL to source name."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        return self.DOMAIN_TO_SOURCE.get(domain)

    # === Status ===

    def get_status(self, sources: Optional[List[str]] = None) -> dict:
        """Get cache status for specified sources."""
        if sources is None:
            # Auto-discover from existing cache directories
            sources = []
            if self.cache_dir.exists():
                for d in self.cache_dir.iterdir():
                    if d.is_dir() and (d / "current").exists():
                        sources.append(d.name)

        result = {
            "cache_dir": str(self.cache_dir),
            "sources": {},
        }

        all_exist = True
        any_expired = False

        for source in sources:
            snapshot = self.get_source_snapshot(source)
            if snapshot is None:
                result["sources"][source] = {"exists": False}
                all_exist = False
            else:
                is_expired = snapshot.is_expired()
                if is_expired:
                    any_expired = True
                result["sources"][source] = {
                    "exists": True,
                    "snapshot_id": snapshot.id,
                    "created_at": snapshot.meta.created_at,
                    "expires_at": snapshot.meta.expires_at,
                    "is_expired": is_expired,
                    "time_remaining_hours": snapshot.meta.time_remaining() / 3600,
                    "api_items": snapshot.meta.api_item_count,
                    "pages": snapshot.meta.page_count,
                }

        result["all_exist"] = all_exist
        result["any_expired"] = any_expired

        return result


# === Compatibility Layer ===
# These classes maintain backward compatibility with existing code

class Snapshot:
    """
    Compatibility wrapper that aggregates multiple SourceSnapshots.

    Provides the same interface as the old Snapshot class.
    """

    def __init__(self, cache_manager: CacheManager, sources: List[str]):
        self._manager = cache_manager
        self._sources = sources
        self._source_snapshots: Dict[str, SourceSnapshot] = {}

        for source in sources:
            snapshot = cache_manager.get_source_snapshot(source)
            if snapshot:
                self._source_snapshots[source] = snapshot

    @property
    def id(self) -> str:
        """Composite ID from all source snapshots."""
        ids = [s.id for s in self._source_snapshots.values()]
        return "+".join(sorted(ids)) if ids else "empty"

    @property
    def meta(self) -> "SnapshotMeta":
        """Aggregate metadata."""
        if not self._source_snapshots:
            return SnapshotMeta("", 0, DEFAULT_TTL, {})

        # Use earliest expiration
        min_expires = min(s.meta.expires_at for s in self._source_snapshots.values())
        min_created = min(s.meta.created_at for s in self._source_snapshots.values())

        sources_info = {}
        for source, snapshot in self._source_snapshots.items():
            sources_info[source] = {
                "api_items": snapshot.meta.api_item_count,
                "pages": snapshot.meta.page_count,
            }

        return SnapshotMeta(
            snapshot_id=self.id,
            created_at=min_created,
            ttl=int(min_expires - min_created),
            sources=sources_info,
        )

    def is_expired(self) -> bool:
        """Check if any source is expired."""
        if not self._source_snapshots:
            return True
        return any(s.is_expired() for s in self._source_snapshots.values())

    def get_api_data(self, source: str, key: Optional[str] = None) -> Optional[Any]:
        """Get API data from appropriate source snapshot."""
        snapshot = self._source_snapshots.get(source)
        if snapshot is None:
            return None

        data = snapshot.get_api_data()
        if data is None:
            return None

        if key is None:
            return data
        return data.get(key)

    def get_page(self, url: str) -> Optional[Dict[str, Any]]:
        """Get page from appropriate source snapshot."""
        return self._manager.get_page(url)

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregate statistics."""
        stats = {
            "snapshot_id": self.id,
            "created_at": self.meta.created_at,
            "expires_at": self.meta.expires_at,
            "is_expired": self.is_expired(),
            "time_remaining": self.meta.time_remaining(),
            "sources": {},
        }

        for source, snapshot in self._source_snapshots.items():
            stats["sources"][source] = {
                "api_items": snapshot.meta.api_item_count,
                "pages": snapshot.meta.page_count,
            }

        return stats


@dataclass
class SnapshotMeta:
    """Compatibility metadata class."""
    snapshot_id: str
    created_at: float
    ttl: int
    sources: Dict[str, Dict[str, Any]]

    @property
    def expires_at(self) -> float:
        return self.created_at + self.ttl

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def time_remaining(self) -> float:
        return max(0, self.expires_at - time.time())


class SnapshotCacheManager:
    """
    Compatibility wrapper around CacheManager.

    Provides the same interface as the old SnapshotCacheManager.
    """

    def __init__(
        self,
        cache_dir: Path,
        ttl: int = DEFAULT_TTL,
        sources: Optional[List[str]] = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.ttl = ttl
        self.sources = sources or []
        self._manager = CacheManager(cache_dir, ttl)

    def register_api_fetcher(self, source: str, fetcher: Callable):
        self._manager.register_api_fetcher(source, fetcher)

    def register_url_generator(self, source: str, generator: Callable):
        self._manager.register_url_generator(source, generator)

    def ensure_cache(self, force_update: bool = False) -> Snapshot:
        """Ensure all configured sources have valid cache."""
        self._manager.ensure_sources(self.sources, force_update)
        return Snapshot(self._manager, self.sources)

    def update_cache(self) -> Snapshot:
        """Force update all configured sources."""
        self._manager.update_sources(self.sources)
        return Snapshot(self._manager, self.sources)

    def get_current_snapshot(self) -> Optional[Snapshot]:
        """Get current composite snapshot."""
        # Check if all sources exist
        for source in self.sources:
            if self._manager.get_source_snapshot(source) is None:
                return None
        return Snapshot(self._manager, self.sources)

    def get_cache_status(self) -> dict:
        """Get cache status."""
        status = self._manager.get_status(self.sources)

        # Add compatibility fields
        if status["all_exist"] and not status["any_expired"]:
            # Find earliest expiration
            min_expires = float('inf')
            min_created = float('inf')
            for info in status["sources"].values():
                if info.get("exists"):
                    min_expires = min(min_expires, info["expires_at"])
                    min_created = min(min_created, info["created_at"])

            status["exists"] = True
            status["snapshot_id"] = "+".join(
                info["snapshot_id"]
                for info in status["sources"].values()
                if info.get("exists")
            )
            status["created_at"] = min_created
            status["expires_at"] = min_expires
            status["is_expired"] = False
            status["time_remaining_hours"] = (min_expires - time.time()) / 3600
        else:
            status["exists"] = False
            status["snapshot_id"] = None

        return status

    # For compatibility with snapshot_manager attribute access
    @property
    def snapshot_manager(self):
        return self


class SnapshotCacheContext:
    """
    Cache context wrapper for plugins.

    Provides interface for API clients to access cache data.
    """

    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot

    def get_api_data(self, source: str) -> Optional[Dict[str, Any]]:
        """Get API data for a source."""
        return self.snapshot.get_api_data(source)


# Global instance
_cache_manager: Optional[SnapshotCacheManager] = None
_page_cache_manager = None  # PageCacheManager instance


def get_snapshot_cache_manager():
    """
    Get global cache manager instance.

    Returns PageCacheManagerWrapper if page-based cache is available,
    otherwise returns SnapshotCacheManager.
    """
    global _cache_manager, _page_cache_manager

    # Prefer page-based cache if available
    if _page_cache_manager is not None:
        from liveweb_arena.core.snapshot_integration import get_available_sources
        sources = get_available_sources()
        return PageCacheManagerWrapper(_page_cache_manager, sources)

    # Fall back to old snapshot cache
    if _cache_manager is None:
        cache_dir = Path(__file__).parent.parent.parent / "cache"
        _cache_manager = SnapshotCacheManager(cache_dir)
    return _cache_manager


def set_snapshot_cache_manager(manager: SnapshotCacheManager):
    """Set global cache manager instance."""
    global _cache_manager
    _cache_manager = manager


def set_page_cache_manager(manager):
    """Set global page cache manager instance for page-based caching."""
    global _page_cache_manager
    _page_cache_manager = manager


def get_page_cache_manager():
    """Get global page cache manager instance."""
    return _page_cache_manager


class PageCacheSnapshot:
    """
    Snapshot wrapper for page-based cache.

    Provides the same interface as Snapshot but backed by PageCacheManager.
    """

    def __init__(self, page_manager, sources: List[str]):
        self._manager = page_manager
        self._sources = sources

    @property
    def id(self) -> str:
        return f"page_cache_{int(time.time())}"

    @property
    def meta(self) -> SnapshotMeta:
        return SnapshotMeta(
            snapshot_id=self.id,
            created_at=time.time(),
            ttl=DEFAULT_TTL,
            sources={s: {} for s in self._sources},
        )

    def is_expired(self) -> bool:
        return False

    def get_api_data(self, source: str, key: Optional[str] = None) -> Optional[Any]:
        """Get API data from page-based cache."""
        data = self._manager.get_api_data(source)
        if data is None:
            return None
        if key is None:
            return data
        return data.get(key)

    def get_page(self, url: str) -> Optional[Dict[str, Any]]:
        """Get page from page-based cache."""
        return self._manager.get_page(url)


class PageCacheManagerWrapper:
    """
    Wrapper that makes PageCacheManager look like SnapshotCacheManager.

    Used for backward compatibility with templates.
    """

    def __init__(self, page_manager, sources: List[str]):
        self._manager = page_manager
        self._sources = sources

    def get_current_snapshot(self) -> Optional[PageCacheSnapshot]:
        """Get current snapshot from page-based cache."""
        return PageCacheSnapshot(self._manager, self._sources)

    def get_cache_status(self) -> dict:
        """Get cache status."""
        return self._manager.get_status(self._sources)
