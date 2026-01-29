"""CoinGecko API client with rate limiting and API key support"""

import os
import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp

from liveweb_arena.plugins.base_client import BaseAPIClient, RateLimiter

logger = logging.getLogger(__name__)

CACHE_SOURCE = "coingecko"


class CoinGeckoClient(BaseAPIClient):
    """
    CoinGecko API client with Pro/Free tier support.

    Set COINGECKO_API_KEY environment variable to use Pro API.
    """

    FREE_API_BASE = "https://api.coingecko.com/api/v3"
    PRO_API_BASE = "https://pro-api.coingecko.com/api/v3"

    # Free tier: 2s interval; Pro tier uses override in _rate_limit
    _rate_limiter = RateLimiter(min_interval=2.0)

    @classmethod
    def get_api_key(cls) -> Optional[str]:
        """Get API key from environment."""
        return os.getenv("COINGECKO_API_KEY")

    @classmethod
    def get_base_url(cls) -> str:
        """Get base URL based on API key availability."""
        return cls.PRO_API_BASE if cls.get_api_key() else cls.FREE_API_BASE

    @classmethod
    def get_headers(cls) -> Dict[str, str]:
        """Get request headers, including API key if available."""
        headers = {"Accept": "application/json"}
        api_key = cls.get_api_key()
        if api_key:
            headers["x-cg-pro-api-key"] = api_key
        return headers

    @classmethod
    async def _rate_limit(cls):
        """Apply rate limiting - Pro tier has minimal delay."""
        if cls.get_api_key():
            await asyncio.sleep(0.1)
        else:
            await cls._rate_limiter.wait()

    @classmethod
    async def get(
        cls,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 15.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Make GET request to CoinGecko API.

        Args:
            endpoint: API endpoint (e.g., "/coins/markets")
            params: Query parameters
            timeout: Request timeout in seconds

        Returns:
            JSON response or None on error
        """
        await cls._rate_limit()

        url = f"{cls.get_base_url()}{endpoint}"
        headers = cls.get_headers()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    if response.status == 429:
                        # Rate limited - wait and retry once
                        await asyncio.sleep(5)
                        async with session.get(
                            url,
                            params=params,
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=timeout),
                        ) as retry_response:
                            if retry_response.status != 200:
                                return None
                            return await retry_response.json()

                    if response.status != 200:
                        return None
                    return await response.json()
        except Exception:
            return None

    @classmethod
    async def get_coin_market_data(
        cls,
        coin_ids: str,
        vs_currency: str = "usd",
    ) -> Optional[list]:
        """
        Get market data for specified coins.

        Args:
            coin_ids: Comma-separated coin IDs (e.g., "bitcoin,ethereum")
            vs_currency: Target currency

        Returns:
            List of coin market data or None
        """
        params = {
            "vs_currency": vs_currency,
            "ids": coin_ids,
            "order": "market_cap_desc",
            "sparkline": "false",
        }
        return await cls.get("/coins/markets", params)

    @classmethod
    async def get_simple_price(
        cls,
        coin_ids: str,
        vs_currencies: str = "usd",
    ) -> Optional[dict]:
        """
        Get simple price data for specified coins.

        Args:
            coin_ids: Comma-separated coin IDs (e.g., "bitcoin,ethereum")
            vs_currencies: Comma-separated currencies (e.g., "usd,eur")

        Returns:
            Dict of prices or None
        """
        params = {
            "ids": coin_ids,
            "vs_currencies": vs_currencies,
        }
        return await cls.get("/simple/price", params)


# ============================================================
# Cache Data Fetcher (used by snapshot_integration)
# ============================================================

async def fetch_cache_api_data() -> Optional[Dict[str, Any]]:
    """
    Fetch CoinGecko market data for all coins defined in variables.

    Returns data structure:
    {
        "_meta": {"source": "coingecko", "coin_count": N},
        "coins": {
            "bitcoin": {<market_data>},
            "ethereum": {<market_data>},
            ...
        }
    }
    """
    from .templates.price import CoinVariable

    coins = [coin.coin_id for coin in CoinVariable.COINS]
    logger.info(f"Fetching CoinGecko data for {len(coins)} coins...")

    try:
        async with aiohttp.ClientSession() as session:
            # Use CoinGeckoClient's API key if available
            headers = CoinGeckoClient.get_headers()
            base_url = CoinGeckoClient.get_base_url()

            params = {
                "vs_currency": "usd",
                "ids": ",".join(coins),
                "order": "market_cap_desc",
                "per_page": 100,
                "page": 1,
                "sparkline": "false",
                "price_change_percentage": "24h,7d,30d",
            }

            async with session.get(
                f"{base_url}/coins/markets",
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise Exception(f"API error: {response.status}")
                data = await response.json()

        # Organize by coin_id for easy lookup
        result = {
            "_meta": {
                "source": CACHE_SOURCE,
                "endpoint": "coins/markets",
                "coin_count": len(data),
            },
            "coins": {},
        }
        for coin in data:
            coin_id = coin.get("id")
            if coin_id:
                result["coins"][coin_id] = coin

        logger.info(f"Fetched {len(result['coins'])} coins from CoinGecko")
        return result

    except Exception as e:
        logger.error(f"CoinGecko fetch failed: {e}")
        return {"_meta": {"source": CACHE_SOURCE, "coin_count": 0}, "coins": {}}


async def fetch_homepage_api_data() -> Dict[str, Any]:
    """
    Fetch API data for CoinGecko homepage (all coins).

    Returns homepage format:
    {
        "coins": {
            "bitcoin": {<market_data>},
            "ethereum": {<market_data>},
            ...
        }
    }
    """
    data = await fetch_cache_api_data()
    if data and data.get("coins"):
        return {"coins": data["coins"]}
    return {"coins": {}}


async def fetch_single_coin_data(coin_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch market data for a single coin.

    Used by page-based cache: each page caches its own coin's data.

    Args:
        coin_id: CoinGecko coin ID (e.g., "bitcoin", "ethereum")

    Returns:
        Dict with coin market data, or empty dict on error
    """
    try:
        async with aiohttp.ClientSession() as session:
            headers = CoinGeckoClient.get_headers()
            base_url = CoinGeckoClient.get_base_url()

            params = {
                "vs_currency": "usd",
                "ids": coin_id,
                "order": "market_cap_desc",
                "per_page": 1,
                "page": 1,
                "sparkline": "false",
                "price_change_percentage": "24h,7d,30d",
            }

            # Rate limit
            await CoinGeckoClient._rate_limit()

            async with session.get(
                f"{base_url}/coins/markets",
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    logger.warning(f"CoinGecko API error for {coin_id}: {response.status}")
                    return {}
                data = await response.json()

        if not data:
            return {}

        # Return the single coin's data
        return data[0] if data else {}

    except Exception as e:
        logger.error(f"CoinGecko fetch failed for {coin_id}: {e}")
        return {}
