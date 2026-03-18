"""Open Meteo API client.

Free weather API — no auth, no rate limits.
Docs: https://open-meteo.com/en/docs
"""

import logging
from typing import Any, ClassVar, Dict

import aiohttp

from liveweb_arena.plugins.base_client import APIFetchError, BaseAPIClient, RateLimiter

logger = logging.getLogger(__name__)

CACHE_SOURCE = "openmeteo"
API_BASE = "https://api.open-meteo.com/v1/forecast"


class OpenMeteoClient(BaseAPIClient):
    """Open Meteo API client with rate limiting and session reuse."""

    _rate_limiter: ClassVar[RateLimiter] = RateLimiter(min_interval=0.2)
    _session: aiohttp.ClientSession = None

    @classmethod
    async def _get_session(cls) -> aiohttp.ClientSession:
        if cls._session is None or cls._session.closed:
            cls._session = aiohttp.ClientSession(
                headers={"User-Agent": "LiveWebArena/1.0"},
            )
        return cls._session

    @classmethod
    async def close_session(cls):
        if cls._session and not cls._session.closed:
            await cls._session.close()
        cls._session = None

    @classmethod
    async def get(
        cls,
        params: Dict[str, Any],
        timeout: float = 15.0,
    ) -> Dict[str, Any]:
        """Fetch from Open Meteo API with rate limiting."""
        await cls._rate_limit()
        session = await cls._get_session()
        req_timeout = aiohttp.ClientTimeout(total=timeout)

        try:
            async with session.get(API_BASE, params=params, timeout=req_timeout) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise APIFetchError(
                        f"Open Meteo API returned {resp.status}: {text[:200]}",
                        source=CACHE_SOURCE,
                        status_code=resp.status,
                    )
                data = await resp.json(content_type=None)
                if not isinstance(data, dict) or "current_weather" not in data:
                    raise APIFetchError(
                        "Open Meteo API returned unexpected format",
                        source=CACHE_SOURCE,
                    )
                return data
        except APIFetchError:
            raise
        except Exception as e:
            raise APIFetchError(
                f"Open Meteo API request failed: {e}",
                source=CACHE_SOURCE,
            ) from e


async def fetch_forecast(
    latitude: float,
    longitude: float,
    forecast_days: int = 3,
) -> Dict[str, Any]:
    """
    Fetch weather forecast from Open Meteo API.

    Returns the full API response with current weather, hourly, and daily data.

    Raises:
        APIFetchError: If the API request fails
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current_weather": "true",
        "hourly": ",".join([
            "temperature_2m",
            "relative_humidity_2m",
            "wind_speed_10m",
            "precipitation_probability",
        ]),
        "daily": ",".join([
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_probability_max",
            "sunrise",
            "sunset",
        ]),
        "timezone": "auto",
        "forecast_days": forecast_days,
    }
    return await OpenMeteoClient.get(params)
