"""Weather API client with caching support (wttr.in)"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import aiohttp
from liveweb_arena.plugins.base_client import APIFetchError, BaseAPIClient, RateLimiter, validate_api_response

logger = logging.getLogger(__name__)

CACHE_SOURCE = "weather"


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
                    path_part = quote(location, safe="+")
                    url = f"https://wttr.in/{path_part}?format=j1"
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=45),
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


def _format_error(location: str, e: Exception) -> str:
    """Build a clear error message including exception type and cause."""
    msg = str(e).strip()
    if not msg:
        msg = repr(e)
    return f"{type(e).__name__} for {location}: {msg}"


async def fetch_single_location_data(location: str) -> Dict[str, Any]:
    """
    Fetch weather data for a single location.

    Used by page-based cache: each page caches its own location's data.

    Args:
        location: Location query (e.g., "Tokyo,Japan", "JFK", "Mexico+City,Mexico")

    Returns:
        Dict with weather JSON data

    Raises:
        APIFetchError: If API request fails or returns invalid data
    """
    # Encode for path: keep + for spaces, encode comma and other chars (e.g. Mexico+City,Mexico)
    path_part = quote(location, safe="+")
    url = f"https://wttr.in/{path_part}?format=j1"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=45),
                headers={"User-Agent": "curl/7.64.1"},
            ) as response:
                body = await response.text()
                if response.status != 200:
                    raise APIFetchError(
                        f"status={response.status} for location={location}. body={body[:200]!r}",
                        source="weather",
                        status_code=response.status,
                    )
                try:
                    data = json.loads(body)
                except json.JSONDecodeError as json_err:
                    raise APIFetchError(
                        f"Invalid JSON for {location}: {_format_error(location, json_err)}. "
                        f"Response preview: {body[:300]!r}",
                        source="weather",
                    ) from json_err
                validate_api_response(data, dict, f"location={location}")
                return data

    except APIFetchError:
        raise
    except Exception as e:
        raise APIFetchError(_format_error(location, e), source="weather") from e
