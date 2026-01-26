"""
Cache Adapters - Source-specific cache implementations.

Each adapter knows how to:
1. Fetch all relevant API data for a source
2. Fetch all relevant web pages for a source
3. Map between URLs and cache keys

IMPORTANT: Cache adapters import their entity lists directly from plugin variables.
This ensures the cached entities always match what templates can generate.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp

from .cache_manager import CacheManager, get_cache_manager

logger = logging.getLogger(__name__)


def _get_coingecko_coins() -> List[str]:
    """Get coin IDs from CoinGecko plugin variables."""
    try:
        from liveweb_arena.plugins.coingecko.templates.price import CoinVariable
        return [coin.coin_id for coin in CoinVariable.COINS]
    except ImportError:
        logger.warning("Could not import CoinGecko variables, using fallback")
        return ["bitcoin", "ethereum", "solana"]


def _get_stooq_assets() -> List[str]:
    """Get asset symbols from Stooq plugin variables."""
    try:
        from liveweb_arena.plugins.stooq.templates.variables import (
            US_STOCKS, INDICES, CURRENCIES, COMMODITIES
        )
        assets = []
        assets.extend([s.symbol for s in US_STOCKS])
        assets.extend([i.symbol for i in INDICES])
        assets.extend([c.symbol for c in CURRENCIES])
        assets.extend([c.symbol for c in COMMODITIES])
        return assets
    except ImportError:
        logger.warning("Could not import Stooq variables, using fallback")
        return ["^spx", "^dji", "aapl.us", "msft.us"]


def _get_weather_locations() -> List[str]:
    """Get location queries from Weather plugin variables."""
    try:
        from liveweb_arena.plugins.weather.templates.variables import LocationVariable
        locations = []
        # Add all cities from CITY_SEEDS
        for region, cities in LocationVariable.CITY_SEEDS.items():
            for city, country in cities:
                # Format: City,Country with spaces replaced by +
                query = f"{city},{country}".replace(" ", "+")
                locations.append(query)
        # Add airport codes
        locations.extend(LocationVariable.AIRPORT_CODES)
        return locations
    except ImportError:
        logger.warning("Could not import Weather variables, using fallback")
        return ["Tokyo,Japan", "New+York,USA", "London,UK"]


class CoinGeckoCacheAdapter:
    """
    Cache adapter for CoinGecko.

    Caches:
    - Market data for top coins (prices, 24h change, market cap, etc.)
    - Coin detail pages

    NOTE: Coin list is imported from plugin variables to ensure consistency.
    """

    SOURCE = "coingecko"
    API_BASE = "https://api.coingecko.com/api/v3"

    def __init__(self, cache_manager: CacheManager = None):
        self.cache = cache_manager or get_cache_manager()
        # Import coins from plugin variables (single source of truth)
        self.cached_coins = _get_coingecko_coins()
        self._register_fetchers()

    def _register_fetchers(self):
        """Register API and page fetchers with cache manager."""
        self.cache.register_fetcher(
            self.SOURCE,
            api_fetcher=self._fetch_all_api_data,
            page_fetcher=None,  # Pages fetched on-demand
        )

    async def _fetch_all_api_data(self) -> Dict[str, Any]:
        """Fetch all coin market data from CoinGecko API."""
        logger.info(f"Fetching CoinGecko market data for {len(self.cached_coins)} coins...")

        try:
            async with aiohttp.ClientSession() as session:
                # Fetch market data for all cached coins
                params = {
                    "vs_currency": "usd",
                    "ids": ",".join(self.cached_coins),
                    "order": "market_cap_desc",
                    "per_page": 100,
                    "page": 1,
                    "sparkline": "false",
                    "price_change_percentage": "24h,7d,30d",
                }

                async with session.get(
                    f"{self.API_BASE}/coins/markets",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status != 200:
                        logger.error(f"CoinGecko API error: {response.status}")
                        return {}

                    data = await response.json()

            # Organize data by coin_id for easy lookup
            result = {
                "_meta": {
                    "source": "coingecko",
                    "endpoint": "coins/markets",
                    "coin_count": len(data),
                },
                "coins": {},
            }

            for coin in data:
                coin_id = coin.get("id")
                if coin_id:
                    result["coins"][coin_id] = coin

            logger.info(f"Cached {len(result['coins'])} coins from CoinGecko")
            return result

        except Exception as e:
            logger.error(f"Failed to fetch CoinGecko data: {e}")
            return {}

    def get_coin_data(self, coin_id: str, version: int = None) -> Optional[Dict]:
        """Get cached coin data."""
        api_data = self.cache.get_api_data(self.SOURCE, version=version)
        if not api_data:
            return None
        return api_data.get("coins", {}).get(coin_id)

    def get_all_coins_data(self, version: int = None) -> Dict[str, Dict]:
        """Get all cached coin data."""
        api_data = self.cache.get_api_data(self.SOURCE, version=version)
        if not api_data:
            return {}
        return api_data.get("coins", {})


class StooqCacheAdapter:
    """
    Cache adapter for Stooq.

    Caches:
    - Price data for stocks, indices, currencies, commodities

    NOTE: Asset list is imported from plugin variables to ensure consistency.
    """

    SOURCE = "stooq"
    CSV_URL = "https://stooq.com/q/d/l/"

    def __init__(self, cache_manager: CacheManager = None):
        self.cache = cache_manager or get_cache_manager()
        # Import assets from plugin variables (single source of truth)
        self.cached_assets = _get_stooq_assets()
        self._register_fetchers()

    def _register_fetchers(self):
        """Register API fetcher with cache manager."""
        self.cache.register_fetcher(
            self.SOURCE,
            api_fetcher=self._fetch_all_api_data,
            page_fetcher=None,
        )

    async def _fetch_all_api_data(self) -> Dict[str, Any]:
        """Fetch price data for all cached assets from Stooq."""
        logger.info(f"Fetching Stooq price data for {len(self.cached_assets)} assets...")

        result = {
            "_meta": {
                "source": "stooq",
                "asset_count": 0,
            },
            "assets": {},
        }

        # Semaphore to limit concurrent requests (reduced to avoid rate limits)
        semaphore = asyncio.Semaphore(2)
        rate_limited = False

        async def fetch_one(session: aiohttp.ClientSession, symbol: str):
            """Fetch data for a single symbol."""
            nonlocal rate_limited
            if rate_limited:
                return None

            async with semaphore:
                # Add delay between requests to avoid rate limiting
                await asyncio.sleep(0.5)

                try:
                    params = {"s": symbol, "i": "d"}
                    async with session.get(
                        self.CSV_URL,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as response:
                        if response.status != 200:
                            logger.warning(f"Stooq error for {symbol}: {response.status}")
                            return None

                        csv_text = await response.text()

                    # Check for rate limit error
                    if "Exceeded the daily hits limit" in csv_text:
                        logger.error(f"Stooq rate limit exceeded - stopping all requests")
                        rate_limited = True
                        return None

                    # Parse CSV (handle both Windows and Unix line endings)
                    csv_text = csv_text.replace("\r\n", "\n").replace("\r", "\n")
                    lines = csv_text.strip().split("\n")
                    if len(lines) < 2:
                        logger.warning(f"Stooq {symbol}: insufficient data lines ({len(lines)})")
                        return None

                    # Get headers and last row
                    headers = [h.strip() for h in lines[0].split(",")]
                    last_row = [v.strip() for v in lines[-1].split(",")]

                    if len(last_row) < len(headers):
                        return None

                    data = dict(zip(headers, last_row))

                    # Calculate daily change if we have previous day
                    current_close = float(data.get("Close", 0))
                    daily_change = None

                    if len(lines) >= 3:
                        prev_row = [v.strip() for v in lines[-2].split(",")]
                        if len(prev_row) >= len(headers):
                            prev_data = dict(zip(headers, prev_row))
                            prev_close = float(prev_data.get("Close", 0))
                            if prev_close > 0:
                                daily_change = ((current_close - prev_close) / prev_close) * 100

                    return {
                        "symbol": symbol,
                        "date": data.get("Date"),
                        "open": float(data.get("Open", 0)),
                        "high": float(data.get("High", 0)),
                        "low": float(data.get("Low", 0)),
                        "close": current_close,
                        "volume": float(data.get("Volume", 0)) if data.get("Volume") else None,
                        "daily_change_pct": daily_change,
                    }

                except asyncio.TimeoutError:
                    logger.warning(f"Stooq timeout for {symbol}")
                    return None
                except Exception as e:
                    logger.warning(f"Failed to fetch Stooq data for {symbol}: {e}")
                    return None

        async with aiohttp.ClientSession() as session:
            # Fetch all in parallel with concurrency limit
            tasks = [fetch_one(session, symbol) for symbol in self.cached_assets]
            results = await asyncio.gather(*tasks)

            # Collect successful results
            for symbol, data in zip(self.cached_assets, results):
                if data:
                    result["assets"][symbol] = data

        result["_meta"]["asset_count"] = len(result["assets"])
        logger.info(f"Cached {len(result['assets'])} assets from Stooq")
        return result

    def get_asset_data(self, symbol: str, version: int = None) -> Optional[Dict]:
        """Get cached asset data."""
        api_data = self.cache.get_api_data(self.SOURCE, version=version)
        if not api_data:
            return None
        return api_data.get("assets", {}).get(symbol)

    def get_all_assets_data(self, version: int = None) -> Dict[str, Dict]:
        """Get all cached asset data."""
        api_data = self.cache.get_api_data(self.SOURCE, version=version)
        if not api_data:
            return {}
        return api_data.get("assets", {})


class WeatherCacheAdapter:
    """
    Cache adapter for wttr.in weather data.

    Caches:
    - Weather data for major world cities
    - Airport locations

    NOTE: Location list is imported from plugin variables to ensure consistency.
    """

    SOURCE = "weather"
    API_BASE = "https://wttr.in"

    def __init__(self, cache_manager: CacheManager = None):
        self.cache = cache_manager or get_cache_manager()
        # Import locations from plugin variables (single source of truth)
        self.cached_locations = _get_weather_locations()
        self._register_fetchers()

    def _register_fetchers(self):
        """Register API fetcher with cache manager."""
        self.cache.register_fetcher(
            self.SOURCE,
            api_fetcher=self._fetch_all_api_data,
            page_fetcher=None,
        )

    async def _fetch_all_api_data(self) -> Dict[str, Any]:
        """Fetch weather data for all cached locations."""
        logger.info(f"Fetching weather data for {len(self.cached_locations)} locations...")

        result = {
            "_meta": {
                "source": "weather",
                "location_count": 0,
            },
            "locations": {},
        }

        # Semaphore to limit concurrent requests
        semaphore = asyncio.Semaphore(5)

        async def fetch_one(session: aiohttp.ClientSession, location: str):
            """Fetch weather data for a single location."""
            async with semaphore:
                try:
                    url = f"{self.API_BASE}/{location}?format=j1"
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as response:
                        if response.status != 200:
                            logger.warning(f"Weather error for {location}: {response.status}")
                            return None
                        return await response.json()

                except asyncio.TimeoutError:
                    logger.warning(f"Weather timeout for {location}")
                    return None
                except Exception as e:
                    logger.warning(f"Failed to fetch weather for {location}: {e}")
                    return None

        async with aiohttp.ClientSession() as session:
            tasks = [fetch_one(session, loc) for loc in self.cached_locations]
            results = await asyncio.gather(*tasks)

            for location, data in zip(self.cached_locations, results):
                if data:
                    result["locations"][location] = data

        result["_meta"]["location_count"] = len(result["locations"])
        logger.info(f"Cached weather data for {len(result['locations'])} locations")
        return result


class TMDBCacheAdapter:
    """
    Cache adapter for TMDB movie data.

    Caches:
    - Movie details and credits for template movies
    """

    SOURCE = "tmdb"
    API_BASE = "https://api.themoviedb.org/3"

    # Movies to cache (from TMDB plugin variables)
    CACHED_MOVIES = [
        # 2020s hits
        "872585", "569094", "385687", "447365", "502356",
        "603692", "926393", "667538", "346698", "614930",
        # 2010s blockbusters
        "299536", "299534", "27205", "157336", "284053",
        "284052", "118340", "281957", "68718", "24428",
        # Classic films
        "238", "240", "278", "155", "550",
        "680", "13", "578", "597", "429",
        # Award winners
        "496243", "359724", "466272", "497", "389",
        "122", "120", "121",
        # Animation
        "862", "105", "324857", "508947",
        # International
        "372058", "129", "311324",
    ]

    def __init__(self, cache_manager: CacheManager = None):
        self.cache = cache_manager or get_cache_manager()
        self._register_fetchers()

    def _register_fetchers(self):
        """Register API fetcher with cache manager."""
        self.cache.register_fetcher(
            self.SOURCE,
            api_fetcher=self._fetch_all_api_data,
            page_fetcher=None,
        )

    def _get_api_key(self) -> Optional[str]:
        """Get TMDB API key from environment."""
        import os
        return os.getenv("TMDB_API_KEY")

    async def _fetch_all_api_data(self) -> Dict[str, Any]:
        """Fetch movie data for all cached movies."""
        logger.info("Fetching TMDB movie data...")

        api_key = self._get_api_key()
        if not api_key:
            logger.warning("TMDB_API_KEY not set, skipping TMDB cache")
            return {}

        result = {
            "_meta": {
                "source": "tmdb",
                "movie_count": 0,
            },
            "movies": {},
        }

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        # Semaphore to limit concurrent requests
        semaphore = asyncio.Semaphore(5)

        async def fetch_one(session: aiohttp.ClientSession, movie_id: str):
            """Fetch movie data with credits."""
            async with semaphore:
                try:
                    url = f"{self.API_BASE}/movie/{movie_id}"
                    params = {"append_to_response": "credits"}
                    async with session.get(
                        url,
                        params=params,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as response:
                        if response.status != 200:
                            logger.warning(f"TMDB error for movie {movie_id}: {response.status}")
                            return None
                        return await response.json()

                except asyncio.TimeoutError:
                    logger.warning(f"TMDB timeout for movie {movie_id}")
                    return None
                except Exception as e:
                    logger.warning(f"Failed to fetch TMDB movie {movie_id}: {e}")
                    return None

        async with aiohttp.ClientSession() as session:
            tasks = [fetch_one(session, movie_id) for movie_id in self.CACHED_MOVIES]
            results = await asyncio.gather(*tasks)

            for movie_id, data in zip(self.CACHED_MOVIES, results):
                if data:
                    result["movies"][movie_id] = data

        result["_meta"]["movie_count"] = len(result["movies"])
        logger.info(f"Cached {len(result['movies'])} movies from TMDB")
        return result


class CacheAdapterRegistry:
    """Registry for all cache adapters."""

    def __init__(self, cache_manager: CacheManager = None):
        self.cache = cache_manager or get_cache_manager()
        self.adapters: Dict[str, Any] = {}

    def register(self, adapter):
        """Register an adapter."""
        self.adapters[adapter.SOURCE] = adapter

    def get(self, source: str):
        """Get adapter by source name."""
        return self.adapters.get(source)

    def initialize_all(self):
        """Initialize all default adapters."""
        self.register(CoinGeckoCacheAdapter(self.cache))
        self.register(StooqCacheAdapter(self.cache))
        self.register(WeatherCacheAdapter(self.cache))
        self.register(TMDBCacheAdapter(self.cache))
        return self

    async def refresh_all(self, sources: List[str] = None):
        """Refresh cache for specified sources or all."""
        if sources is None:
            sources = list(self.adapters.keys())

        for source in sources:
            try:
                await self.cache.ensure_fresh(source, force_refresh=True)
            except Exception as e:
                logger.error(f"Failed to refresh cache for {source}: {e}")


# Global adapter registry
_global_registry: Optional[CacheAdapterRegistry] = None


def get_adapter_registry() -> CacheAdapterRegistry:
    """Get the global adapter registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = CacheAdapterRegistry().initialize_all()
    return _global_registry
