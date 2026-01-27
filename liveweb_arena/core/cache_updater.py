"""
Cache Updater Module.

Provides page-based cache management with incremental updates.

Features:
- Per-page TTL: Skip pages that are still fresh
- Incremental updates: Continue from where interrupted
- Concurrent sources: Different sources update in parallel
- API sync: API data refreshed during page updates

Usage:
    from liveweb_arena.core.cache_updater import CacheUpdater, get_cache_updater

    # Get global instance
    updater = get_cache_updater()

    # Ensure cache is ready (incremental update)
    cache = updater.ensure_ready()

    # Force full update
    cache = updater.ensure_ready(force_update=True)

Environment Variables:
    LIVEWEB_CACHE_STRATEGY: "startup" | "periodic" | "manual" (default: startup)
    LIVEWEB_CACHE_SOURCES: comma-separated sources (default: all)
    LIVEWEB_CACHE_TTL: TTL in hours (default: 24)
    LIVEWEB_CACHE_DIR: cache directory (default: ./cache)
"""

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default TTL: 24 hours
DEFAULT_TTL = 24 * 3600


class CacheStrategy(Enum):
    """Cache update strategy."""
    STARTUP = "startup"      # Check at startup only
    PERIODIC = "periodic"    # Periodic background updates
    MANUAL = "manual"        # Manual control only


@dataclass
class CacheSnapshot:
    """
    Cache snapshot providing access to cached data.

    Compatibility wrapper that provides the same interface as the old Snapshot.
    """
    _manager: "PageCacheManager"
    sources: List[str]

    @property
    def id(self) -> str:
        return f"cache_{int(time.time())}"

    @property
    def meta(self) -> "CacheSnapshotMeta":
        return CacheSnapshotMeta(self._manager, self.sources)

    def is_expired(self) -> bool:
        """Check if any source needs update."""
        status = self._manager.get_status(self.sources)
        for source, info in status.get("sources", {}).items():
            if info.get("api_expired", True):
                return True
        return False

    def get_api_data(self, source: str, key: Optional[str] = None) -> Optional[Any]:
        """Get API data from a source."""
        data = self._manager.get_api_data(source)
        if data is None:
            return None
        if key is None:
            return data
        return data.get(key)

    def get_page(self, url: str) -> Optional[Dict[str, Any]]:
        """Get page data."""
        page = self._manager.get_page(url)
        if page is None:
            return None
        return page.to_dict()

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        status = self._manager.get_status(self.sources)
        return {
            "snapshot_id": self.id,
            "created_at": time.time(),
            "expires_at": time.time() + DEFAULT_TTL,
            "is_expired": self.is_expired(),
            "time_remaining": DEFAULT_TTL,
            "sources": {
                source: {
                    "api_items": info.get("api_item_count", 0),
                    "pages": info.get("page_count", 0),
                }
                for source, info in status.get("sources", {}).items()
            },
        }


@dataclass
class CacheSnapshotMeta:
    """Metadata for cache snapshot."""
    _manager: "PageCacheManager"
    sources: List[str]

    @property
    def created_at(self) -> float:
        return time.time()

    @property
    def expires_at(self) -> float:
        return time.time() + DEFAULT_TTL

    def is_expired(self) -> bool:
        return False

    def time_remaining(self) -> float:
        return DEFAULT_TTL


# Import PageCacheManager lazily to avoid circular imports
def _get_page_cache_manager_class():
    from liveweb_arena.core.page_cache import PageCacheManager
    return PageCacheManager


class CacheUpdater:
    """
    Manages page-based cache with incremental updates.

    Thread-safe for use in both sync and async contexts.
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        sources: Optional[List[str]] = None,
        ttl_hours: float = 24,
        strategy: CacheStrategy = CacheStrategy.STARTUP,
        update_interval_minutes: float = 30,
    ):
        """
        Initialize cache updater.

        Args:
            cache_dir: Cache directory path
            sources: List of cache sources
            ttl_hours: Cache TTL in hours
            strategy: Update strategy
            update_interval_minutes: Background update check interval
        """
        self.cache_dir = cache_dir or Path(__file__).parent.parent.parent / "cache"
        self.sources = sources or []
        self.ttl_seconds = int(ttl_hours * 3600)
        self.strategy = strategy
        self.update_interval = update_interval_minutes * 60

        self._manager: Optional["PageCacheManager"] = None
        self._lock = threading.Lock()

        # Background updater state
        self._background_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._update_callbacks: List[Callable] = []

        self._initialized = False

    def _get_manager(self) -> "PageCacheManager":
        """Get or create the cache manager."""
        if self._manager is None:
            PageCacheManager = _get_page_cache_manager_class()
            self._manager = PageCacheManager(self.cache_dir, self.ttl_seconds)

            # Register fetchers from snapshot_integration
            from liveweb_arena.core.snapshot_integration import (
                get_single_asset_fetcher_for_source,
                get_urls_for_source,
            )

            for source in self.sources:
                # Register single-asset API fetcher (signature: async fn(asset_id) -> Dict)
                fetcher = get_single_asset_fetcher_for_source(source)
                if fetcher:
                    self._manager.register_api_fetcher(source, fetcher)

                # Register URL generator
                def make_url_generator(s):
                    return lambda: get_urls_for_source(s)
                self._manager.register_url_generator(source, make_url_generator(source))

            # Register with snapshot_cache for template access
            from liveweb_arena.core.snapshot_cache import set_page_cache_manager
            set_page_cache_manager(self._manager)

        return self._manager

    def ensure_ready(self, force_update: bool = False) -> CacheSnapshot:
        """
        Ensure cache is ready with incremental updates.

        Only updates pages that are expired. Skips fresh pages.

        Args:
            force_update: Force update all pages regardless of TTL

        Returns:
            CacheSnapshot instance
        """
        manager = self._get_manager()

        # Run update in async context
        try:
            loop = asyncio.get_running_loop()
            import nest_asyncio
            nest_asyncio.apply()
        except RuntimeError:
            pass

        async def do_update():
            return await manager.update_all_sources(
                self.sources,
                force=force_update,
                concurrent=True,
            )

        asyncio.run(do_update())
        self._initialized = True

        return CacheSnapshot(manager, self.sources)

    def get_snapshot(self) -> Optional[CacheSnapshot]:
        """Get current cache snapshot."""
        if not self._initialized:
            return None
        manager = self._get_manager()
        return CacheSnapshot(manager, self.sources)

    def get_status(self) -> dict:
        """Get cache status information."""
        manager = self._get_manager()
        status = manager.get_status(self.sources)

        # Add compatibility fields
        all_exist = True
        any_expired = False

        for source, info in status.get("sources", {}).items():
            if not info.get("api_exists"):
                all_exist = False
            if info.get("api_expired"):
                any_expired = True

        status["exists"] = all_exist
        status["is_expired"] = any_expired
        status["strategy"] = self.strategy.value

        if all_exist and not any_expired:
            status["time_remaining_hours"] = self.ttl_seconds / 3600
        else:
            status["time_remaining_hours"] = 0

        return status

    def update_cache(self) -> CacheSnapshot:
        """Force update all sources."""
        return self.ensure_ready(force_update=True)

    def on_update(self, callback: Callable):
        """Register a callback for cache updates."""
        self._update_callbacks.append(callback)

    # === Background Updater ===

    def start_background_updater(self):
        """Start background cache updater thread."""
        if self.strategy != CacheStrategy.PERIODIC:
            logger.debug(f"Background updater not started (strategy={self.strategy.value})")
            return

        if self._background_thread and self._background_thread.is_alive():
            logger.warning("Background updater already running")
            return

        self._stop_event.clear()
        self._background_thread = threading.Thread(
            target=self._background_update_loop,
            name="CacheUpdater",
            daemon=True,
        )
        self._background_thread.start()
        logger.info(f"Background cache updater started (interval={self.update_interval/60:.0f}min)")

    def stop(self):
        """Stop background updater and cleanup."""
        self._stop_event.set()
        if self._background_thread and self._background_thread.is_alive():
            self._background_thread.join(timeout=5)
            logger.info("Background cache updater stopped")

    def _background_update_loop(self):
        """Background thread loop for periodic cache updates."""
        while not self._stop_event.is_set():
            try:
                if self._stop_event.wait(timeout=self.update_interval):
                    break

                logger.info("Background cache update triggered")
                self.ensure_ready()

            except Exception as e:
                logger.error(f"Background update error: {e}")
                self._stop_event.wait(timeout=60)


# === Compatibility Aliases ===

# For backward compatibility with code using Snapshot
Snapshot = CacheSnapshot


# === Global Instance ===

_global_updater: Optional[CacheUpdater] = None
_global_lock = threading.Lock()


def get_cache_updater() -> CacheUpdater:
    """Get global CacheUpdater instance."""
    global _global_updater

    with _global_lock:
        if _global_updater is None:
            _global_updater = _create_updater_from_env()
        return _global_updater


def set_cache_updater(updater: CacheUpdater):
    """Set global CacheUpdater instance."""
    global _global_updater
    with _global_lock:
        _global_updater = updater


def _create_updater_from_env() -> CacheUpdater:
    """Create CacheUpdater from environment variables."""
    # Strategy
    strategy_str = os.environ.get("LIVEWEB_CACHE_STRATEGY", "startup").lower()
    try:
        strategy = CacheStrategy(strategy_str)
    except ValueError:
        logger.warning(f"Invalid cache strategy '{strategy_str}', using 'startup'")
        strategy = CacheStrategy.STARTUP

    # Sources
    sources_str = os.environ.get("LIVEWEB_CACHE_SOURCES", "")
    if sources_str:
        sources = [s.strip() for s in sources_str.split(",") if s.strip()]
    else:
        # Use all available sources
        from liveweb_arena.core.snapshot_integration import get_available_sources
        sources = get_available_sources()

    # TTL
    ttl_hours = float(os.environ.get("LIVEWEB_CACHE_TTL", "24"))

    # Cache dir
    cache_dir_str = os.environ.get("LIVEWEB_CACHE_DIR")
    cache_dir = Path(cache_dir_str) if cache_dir_str else None

    # Update interval
    update_interval = float(os.environ.get("LIVEWEB_CACHE_UPDATE_INTERVAL", "30"))

    logger.info(f"Cache updater config: strategy={strategy.value}, sources={sources}, ttl={ttl_hours}h")

    return CacheUpdater(
        cache_dir=cache_dir,
        sources=sources,
        ttl_hours=ttl_hours,
        strategy=strategy,
        update_interval_minutes=update_interval,
    )


# === Convenience Functions ===

def ensure_cache_ready(force_update: bool = False) -> CacheSnapshot:
    """Convenience function to ensure cache is ready."""
    return get_cache_updater().ensure_ready(force_update)


def get_current_snapshot() -> Optional[CacheSnapshot]:
    """Convenience function to get current snapshot."""
    return get_cache_updater().get_snapshot()


def get_cache_status() -> dict:
    """Convenience function to get cache status."""
    return get_cache_updater().get_status()
