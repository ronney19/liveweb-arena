"""Weather API client with caching support (wttr.in)"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp
import httpx

from liveweb_arena.utils.logger import log

logger = logging.getLogger(__name__)

# Cache source name
CACHE_SOURCE = "weather"

# Global cache context reference (set by env.py during evaluation)
_cache_context: Optional[Any] = None


def set_weather_cache_context(context: Optional[Any]):
    """Set the cache context for Weather API calls."""
    global _cache_context
    _cache_context = context


def get_weather_cache_context() -> Optional[Any]:
    """Get the current cache context."""
    return _cache_context


class WeatherClient:
    """
    Centralized wttr.in API client with caching support.

    Uses JSON format for structured weather data.
    """

    API_BASE = "https://wttr.in"

    # Rate limiting
    _last_request_time: float = 0
    _min_request_interval: float = 0.5  # seconds between requests
    _lock = asyncio.Lock()

    @classmethod
    async def _rate_limit(cls):
        """Apply rate limiting."""
        async with cls._lock:
            import time
            now = time.time()
            elapsed = now - cls._last_request_time
            if elapsed < cls._min_request_interval:
                await asyncio.sleep(cls._min_request_interval - elapsed)
            cls._last_request_time = time.time()

    @classmethod
    def _normalize_location(cls, location: str) -> str:
        """Normalize location string for cache key matching."""
        # Convert to lowercase and replace spaces with +
        normalized = location.lower().strip()
        normalized = normalized.replace(" ", "+")
        # Remove trailing country specifications for matching
        # e.g., "tokyo,japan" and "tokyo" should match
        return normalized

    @classmethod
    async def get_weather_data(
        cls,
        location: str,
        timeout: float = 15.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Get weather data for a location.

        Args:
            location: Location query (city name, airport code, etc.)
            timeout: Request timeout in seconds

        Returns:
            Weather JSON data or None on error
        """
        # Try cache first
        ctx = get_weather_cache_context()
        if ctx is not None:
            api_data = ctx.get_api_data("weather")
            if api_data:
                locations = api_data.get("locations", {})

                # Try exact match first
                location_data = locations.get(location)
                if location_data:
                    log("GT", f"CACHE HIT - Weather: {location}", force=True)
                    return location_data

                # Try normalized match
                normalized = cls._normalize_location(location)
                for cached_loc, cached_data in locations.items():
                    if cls._normalize_location(cached_loc) == normalized:
                        log("GT", f"CACHE HIT - Weather: {location} (normalized)", force=True)
                        return cached_data

                # Try partial match (city name without country)
                city_part = location.split(",")[0].strip().lower()
                for cached_loc, cached_data in locations.items():
                    cached_city = cached_loc.split(",")[0].strip().lower()
                    if cached_city == city_part:
                        log("GT", f"CACHE HIT - Weather: {location} (city match)", force=True)
                        return cached_data

                # Cache mode but data not found - this is an error
                log("GT", f"CACHE MISS - Weather: {location} not in cache ({len(locations)} locations cached)", force=True)
                return None

        # No cache context - use live API (non-cache mode)
        await cls._rate_limit()

        url = f"{cls.API_BASE}/{location}?format=j1"

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url)
                if response.status_code == 404:
                    logger.warning(f"Weather location not found: {location}")
                    return None
                if response.status_code != 200:
                    logger.warning(f"Weather API error for {location}: {response.status_code}")
                    return None
                return response.json()

        except httpx.TimeoutException:
            logger.warning(f"Weather timeout for {location}")
            return None
        except Exception as e:
            logger.warning(f"Weather error for {location}: {e}")
            return None


# ============================================================
# Cache Data Fetcher (used by snapshot_integration)
# ============================================================

def _get_all_locations() -> List[str]:
    """Get all location queries that need to be cached."""
    from .templates.variables import LocationVariable

    locations = []
    for region, cities in LocationVariable.CITY_SEEDS.items():
        for city, country in cities:
            query = f"{city},{country}".replace(" ", "+")
            locations.append(query)
    locations.extend(LocationVariable.AIRPORT_CODES)
    return locations


async def fetch_cache_api_data() -> Optional[Dict[str, Any]]:
    """
    Fetch weather data for all locations defined in variables.

    Returns data structure:
    {
        "_meta": {"source": "weather", "location_count": N},
        "locations": {
            "Tokyo,Japan": {<wttr.in JSON data>},
            "JFK": {<wttr.in JSON data>},
            ...
        }
    }
    """
    locations = _get_all_locations()
    logger.info(f"Fetching weather data for {len(locations)} locations...")

    result = {
        "_meta": {
            "source": CACHE_SOURCE,
            "location_count": 0,
        },
        "locations": {},
    }
    failed = 0

    # wttr.in is rate-limited, use low concurrency
    semaphore = asyncio.Semaphore(3)

    async def fetch_one(location: str):
        nonlocal failed
        async with semaphore:
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"https://wttr.in/{location}?format=j1"
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=20),
                        headers={"User-Agent": "curl/7.64.1"},
                    ) as response:
                        if response.status != 200:
                            failed += 1
                            return
                        data = await response.json()
                        result["locations"][location] = data
            except Exception as e:
                logger.debug(f"Failed to fetch weather for {location}: {e}")
                failed += 1

    await asyncio.gather(*[fetch_one(loc) for loc in locations])

    result["_meta"]["location_count"] = len(result["locations"])
    logger.info(f"Fetched {len(result['locations'])} weather locations ({failed} failed)")
    return result


async def fetch_single_location_data(location: str) -> Optional[Dict[str, Any]]:
    """
    Fetch weather data for a single location.

    Used by page-based cache: each page caches its own location's data.

    Args:
        location: Location query (e.g., "Tokyo,Japan", "JFK")

    Returns:
        Dict with weather JSON data, or empty dict on error
    """
    logger.debug(f"Fetching weather data for {location}...")

    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://wttr.in/{location}?format=j1"
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"User-Agent": "curl/7.64.1"},
            ) as response:
                if response.status != 200:
                    logger.warning(f"Weather error for {location}: {response.status}")
                    return {}
                return await response.json()

    except Exception as e:
        logger.debug(f"Failed to fetch weather for {location}: {e}")
        return {}
