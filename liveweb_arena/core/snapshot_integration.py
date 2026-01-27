"""
Integration layer between snapshot cache and question templates.

This module dynamically collects cache requirements from registered templates,
making it possible to add new templates without modifying this file.

Templates should implement:
- get_cache_source() -> str: The cache source name (e.g., "coingecko")
- get_cache_urls() -> List[str]: URLs that need to be cached
- fetch_cache_api_data() -> Dict: API data to cache for ground truth
"""

import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable, Awaitable

logger = logging.getLogger(__name__)


# ============================================================
# Template Discovery
# ============================================================

def _ensure_templates_imported():
    """Import all plugin templates to trigger registration."""
    # Import plugins to trigger template registration
    try:
        import liveweb_arena.plugins.coingecko.templates  # noqa: F401
    except ImportError:
        pass
    try:
        import liveweb_arena.plugins.stooq.templates  # noqa: F401
    except ImportError:
        pass
    try:
        import liveweb_arena.plugins.weather.templates  # noqa: F401
    except ImportError:
        pass
    try:
        import liveweb_arena.plugins.tmdb.templates  # noqa: F401
    except ImportError:
        pass
    try:
        import liveweb_arena.plugins.taostats.templates  # noqa: F401
    except ImportError:
        pass
    try:
        import liveweb_arena.plugins.hybrid.templates  # noqa: F401
    except ImportError:
        pass


def get_templates_by_source() -> Dict[str, List[type]]:
    """
    Get all registered templates grouped by their cache source.

    Returns:
        Dict mapping source name to list of template classes
    """
    _ensure_templates_imported()

    from liveweb_arena.core.validators.base import get_registered_templates

    sources: Dict[str, List[type]] = {}

    for name, template_cls in get_registered_templates().items():
        if hasattr(template_cls, 'get_cache_source'):
            try:
                source = template_cls.get_cache_source()
                if source:
                    if source not in sources:
                        sources[source] = []
                    sources[source].append(template_cls)
            except Exception as e:
                logger.warning(f"Failed to get cache source from {name}: {e}")

    return sources


# ============================================================
# Dynamic URL Collection
# ============================================================

def get_urls_for_source(source: str) -> List[str]:
    """
    Get all URLs to cache for a source by collecting from templates.

    Args:
        source: Cache source name (e.g., "coingecko")

    Returns:
        List of unique URLs to cache
    """
    templates = get_templates_by_source().get(source, [])

    urls = []
    seen = set()

    for template_cls in templates:
        if hasattr(template_cls, 'get_cache_urls'):
            try:
                for url in template_cls.get_cache_urls():
                    if url not in seen:
                        urls.append(url)
                        seen.add(url)
            except Exception as e:
                logger.warning(f"Failed to get cache URLs from {template_cls.__name__}: {e}")

    return urls


# ============================================================
# Dynamic API Fetcher Collection
# ============================================================

def get_api_fetcher_for_source(source: str) -> Optional[Callable[[], Awaitable[Dict[str, Any]]]]:
    """
    Get the API fetcher function for a source from api_client modules.

    Each plugin's api_client.py should implement fetch_cache_api_data().

    Args:
        source: Cache source name

    Returns:
        Async function that fetches API data, or None
    """
    try:
        if source == "coingecko":
            from liveweb_arena.plugins.coingecko.api_client import fetch_cache_api_data
            return fetch_cache_api_data
        elif source == "stooq":
            from liveweb_arena.plugins.stooq.api_client import fetch_cache_api_data
            return fetch_cache_api_data
        elif source == "weather":
            from liveweb_arena.plugins.weather.api_client import fetch_cache_api_data
            return fetch_cache_api_data
        elif source == "tmdb":
            from liveweb_arena.plugins.tmdb.api_client import fetch_cache_api_data
            return fetch_cache_api_data
        elif source == "taostats":
            from liveweb_arena.plugins.taostats.api_client import fetch_cache_api_data
            return fetch_cache_api_data
    except ImportError as e:
        logger.warning(f"Failed to import fetch_cache_api_data for {source}: {e}")

    return None


def get_single_asset_fetcher_for_source(source: str) -> Optional[Callable[[str], Awaitable[Dict[str, Any]]]]:
    """
    Get the single-asset API fetcher function for a source.

    Used by page-based cache: each page fetches only its asset's data.

    Args:
        source: Cache source name

    Returns:
        Async function(asset_id: str) -> Dict that fetches single asset data, or None
    """
    try:
        if source == "coingecko":
            from liveweb_arena.plugins.coingecko.api_client import fetch_single_coin_data
            return fetch_single_coin_data
        elif source == "stooq":
            from liveweb_arena.plugins.stooq.api_client import fetch_single_asset_data
            return fetch_single_asset_data
        elif source == "weather":
            from liveweb_arena.plugins.weather.api_client import fetch_single_location_data
            return fetch_single_location_data
        elif source == "tmdb":
            from liveweb_arena.plugins.tmdb.api_client import fetch_single_movie_data
            return fetch_single_movie_data
        elif source == "taostats":
            from liveweb_arena.plugins.taostats.api_client import fetch_single_subnet_data
            return fetch_single_subnet_data
    except ImportError as e:
        logger.warning(f"Failed to import single asset fetcher for {source}: {e}")

    return None


def get_single_asset_fetchers() -> Dict[str, Callable[[str], Awaitable[Dict[str, Any]]]]:
    """
    Get single-asset API fetcher functions for all sources.

    Returns:
        Dict mapping source name to async fetcher function(asset_id) -> Dict
    """
    fetchers = {}
    for source in get_available_sources():
        fetcher = get_single_asset_fetcher_for_source(source)
        if fetcher:
            fetchers[source] = fetcher
    return fetchers


# ============================================================
# Source Registry (Auto-populated from templates)
# ============================================================

def get_available_sources() -> List[str]:
    """Get list of all available cache sources from templates."""
    return list(get_templates_by_source().keys())


def get_api_fetchers() -> Dict[str, Callable[[], Awaitable[Dict[str, Any]]]]:
    """
    Get API fetcher functions for all sources.

    Returns:
        Dict mapping source name to async fetcher function
    """
    fetchers = {}
    for source in get_available_sources():
        fetcher = get_api_fetcher_for_source(source)
        if fetcher:
            fetchers[source] = fetcher
    return fetchers


def get_url_generators() -> Dict[str, Callable[[], List[str]]]:
    """
    Get URL generator functions for all sources.

    Returns:
        Dict mapping source name to function that returns URLs
    """
    generators = {}
    for source in get_available_sources():
        # Create a closure to capture the source name
        def make_generator(s):
            return lambda: get_urls_for_source(s)
        generators[source] = make_generator(source)
    return generators


# ============================================================
# Setup Helper
# ============================================================

def setup_snapshot_cache_manager(
    cache_dir: Optional[Path] = None,
    sources: Optional[List[str]] = None,
    ttl: int = 6 * 3600,
):
    """
    Create and configure a SnapshotCacheManager with all fetchers registered.

    Args:
        cache_dir: Cache directory (default: project_root/cache)
        sources: List of sources to enable (default: all from templates)
        ttl: Cache TTL in seconds (default: 6 hours)

    Returns:
        Configured SnapshotCacheManager
    """
    from liveweb_arena.core.snapshot_cache import SnapshotCacheManager

    if cache_dir is None:
        cache_dir = Path(__file__).parent.parent.parent / "cache"

    if sources is None:
        sources = get_available_sources()

    manager = SnapshotCacheManager(
        cache_dir=cache_dir,
        ttl=ttl,
        sources=sources,
    )

    # Register API fetchers from templates
    api_fetchers = get_api_fetchers()
    for source in sources:
        if source in api_fetchers:
            manager.register_api_fetcher(source, api_fetchers[source])

    # Register URL generators from templates
    url_generators = get_url_generators()
    for source in sources:
        if source in url_generators:
            manager.register_url_generator(source, url_generators[source])

    return manager


# ============================================================
# Data Access Helpers
# ============================================================

class SnapshotDataAccessor:
    """
    Provides convenient access to snapshot data for plugins.

    This class wraps a Snapshot and provides methods matching
    the existing API client interfaces.
    """

    def __init__(self, snapshot):
        from liveweb_arena.core.snapshot_cache import Snapshot
        self.snapshot: Snapshot = snapshot

    # CoinGecko
    def get_coingecko_coin(self, coin_id: str) -> Optional[Dict]:
        """Get CoinGecko coin data."""
        data = self.snapshot.get_api_data("coingecko")
        if data and "coins" in data:
            return data["coins"].get(coin_id)
        return None

    def get_all_coingecko_coins(self) -> Dict[str, Dict]:
        """Get all CoinGecko coin data."""
        data = self.snapshot.get_api_data("coingecko")
        if data and "coins" in data:
            return data["coins"]
        return {}

    # Stooq
    def get_stooq_asset(self, symbol: str) -> Optional[Dict]:
        """Get Stooq asset data."""
        data = self.snapshot.get_api_data("stooq")
        if data and "assets" in data:
            return data["assets"].get(symbol)
        return None

    def get_all_stooq_assets(self) -> Dict[str, Dict]:
        """Get all Stooq asset data."""
        data = self.snapshot.get_api_data("stooq")
        if data and "assets" in data:
            return data["assets"]
        return {}

    # Weather
    def get_weather(self, location: str) -> Optional[Dict]:
        """Get weather data for a location."""
        data = self.snapshot.get_api_data("weather")
        if data and "locations" in data:
            return data["locations"].get(location)
        return None

    # TMDB
    def get_tmdb_movie(self, movie_id: int) -> Optional[Dict]:
        """Get TMDB movie data."""
        data = self.snapshot.get_api_data("tmdb")
        if data and "movies" in data:
            return data["movies"].get(str(movie_id))
        return None

    def get_tmdb_person(self, person_id: int) -> Optional[Dict]:
        """Get TMDB person data."""
        data = self.snapshot.get_api_data("tmdb")
        if data and "persons" in data:
            return data["persons"].get(str(person_id))
        return None
