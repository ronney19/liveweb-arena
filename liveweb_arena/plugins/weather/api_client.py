"""Weather API client with caching support (wttr.in)"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp
import httpx

from liveweb_arena.plugins.base_client import BaseAPIClient, RateLimiter
from liveweb_arena.utils.logger import log

logger = logging.getLogger(__name__)

CACHE_SOURCE = "weather"


class WeatherClient(BaseAPIClient):
    """wttr.in API client with rate limiting."""

    API_BASE = "https://wttr.in"
    _rate_limiter = RateLimiter(min_interval=0.5)

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
            except Exception:
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

    except Exception:
        return {}
