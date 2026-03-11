"""CoinGecko API client with rate limiting and API key support"""

import json
import os
import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp

from liveweb_arena.plugins.base_client import APIFetchError, BaseAPIClient, RateLimiter, validate_api_response

logger = logging.getLogger(__name__)

CACHE_SOURCE = "coingecko"


class CoinGeckoClient(BaseAPIClient):
    """
    CoinGecko API client with Demo / Pro / Free tier support.

    - COINGECKO_DEMO_API_KEY: Demo API (same base URL as free, header x-cg-demo-api-key).
    - COINGECKO_API_KEY: Pro API (pro-api.coingecko.com, header x-cg-pro-api-key).
    - Neither: Free tier (api.coingecko.com, no key).
    """

    FREE_API_BASE = "https://api.coingecko.com/api/v3"
    PRO_API_BASE = "https://pro-api.coingecko.com/api/v3"

    # Free tier: 2s interval; Demo/Pro use shorter delay
    _rate_limiter = RateLimiter(min_interval=2.0)

    @classmethod
    def get_demo_api_key(cls) -> Optional[str]:
        """Get Demo API key from environment (x-cg-demo-api-key)."""
        return os.getenv("COINGECKO_DEMO_API_KEY")

    @classmethod
    def get_api_key(cls) -> Optional[str]:
        """Get Pro API key from environment (x-cg-pro-api-key)."""
        return os.getenv("COINGECKO_API_KEY")

    @classmethod
    def get_base_url(cls) -> str:
        """Get base URL: Demo key must use api.coingecko.com; Pro uses pro-api; else free tier."""
        if cls.get_demo_api_key():
            return cls.FREE_API_BASE  # Demo API requires api.coingecko.com (error 10011 otherwise)
        if cls.get_api_key():
            return cls.PRO_API_BASE
        return cls.FREE_API_BASE

    @classmethod
    def get_headers(cls) -> Dict[str, str]:
        """Get request headers. When Demo key is set use only x-cg-demo-api-key; else Pro."""
        headers = {"Accept": "application/json"}
        if cls.get_demo_api_key():
            headers["x-cg-demo-api-key"] = cls.get_demo_api_key()
        elif cls.get_api_key():
            headers["x-cg-pro-api-key"] = cls.get_api_key()
        return headers

    @classmethod
    async def _rate_limit(cls):
        """Apply rate limiting - Demo/Pro have minimal delay; Free tier 2s."""
        if cls.get_demo_api_key() or cls.get_api_key():
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
                                raise APIFetchError(f"CoinGecko retry failed: {retry_response.status}")
                            return await retry_response.json()

                    if response.status != 200:
                        raise APIFetchError(f"CoinGecko API error: {response.status}")
                    return await response.json()
        except APIFetchError:
            raise
        except Exception as e:
            raise APIFetchError(f"CoinGecko request failed: {e}") from e

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
            }
            if not CoinGeckoClient.get_demo_api_key():
                params["price_change_percentage"] = "24h,7d,30d"
            if CoinGeckoClient.get_demo_api_key():
                params["x_cg_demo_api_key"] = CoinGeckoClient.get_demo_api_key()

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
        raise APIFetchError(f"CoinGecko fetch failed: {e}") from e


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
    raise APIFetchError("CoinGecko homepage returned no coin data")


def _single_coin_params(coin_id: str) -> Dict[str, Any]:
    """Build request params for single-coin fetch. Demo API may reject some params."""
    params = {
        "vs_currency": "usd",
        "ids": coin_id,
        "order": "market_cap_desc",
        "per_page": 1,
        "page": 1,
        "sparkline": "false",
    }
    # Demo API on api.coingecko.com can return 400 when price_change_percentage is sent
    if not CoinGeckoClient.get_demo_api_key():
        params["price_change_percentage"] = "24h,7d,30d"
    return params


def _extract_usd_or_scalar(value: Any) -> Any:
    """If value is a dict with 'usd' key return it; otherwise return value (for API fields that can be scalar or per-currency)."""
    if value is None:
        return None
    if isinstance(value, dict) and "usd" in value:
        return value["usd"]
    return value


def _normalize_coins_id_response(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize /coins/{id} response to the flat shape expected by templates
    (same as /coins/markets single item: id, symbol, name, current_price, market_cap, etc.).
    When market_cap is 0, use fully_diluted_valuation.usd as fallback (API returns 0 for some coins e.g. Maker).
    """
    mid = raw.get("market_data") or {}
    cp = mid.get("current_price") or {}
    mcap = mid.get("market_cap") or {}
    fdv = mid.get("fully_diluted_valuation") or {}
    market_cap_usd = (mcap.get("usd") or 0) if isinstance(mcap, dict) else (mcap or 0)
    if market_cap_usd == 0 and isinstance(fdv, dict) and fdv.get("usd"):
        market_cap_usd = fdv["usd"]
    current_price = (cp.get("usd")) if isinstance(cp, dict) else cp
    total_vol = mid.get("total_volume") or {}
    total_volume = (total_vol.get("usd")) if isinstance(total_vol, dict) else total_vol
    # API can return market_cap_rank at top level or inside market_data
    rank = raw.get("market_cap_rank")
    if rank is None and isinstance(mid.get("market_cap_rank"), (int, float)):
        rank = int(mid["market_cap_rank"])
    # Supply fields for coingecko_supply template (from market_data)
    total_supply = mid.get("total_supply")
    circulating_supply = mid.get("circulating_supply")
    max_supply = mid.get("max_supply")
    # ATH fields for coingecko_ath template (market_data can have ath/ath_change_percentage as dict with usd)
    ath_raw = mid.get("ath")
    ath = ath_raw.get("usd") if isinstance(ath_raw, dict) else ath_raw
    ath_change_raw = mid.get("ath_change_percentage")
    ath_change_percentage = (
        ath_change_raw.get("usd") if isinstance(ath_change_raw, dict) else ath_change_raw
    )
    return {
        "id": raw.get("id", ""),
        "symbol": raw.get("symbol", ""),
        "name": raw.get("name", ""),
        "current_price": current_price,
        "market_cap": market_cap_usd,
        "total_volume": total_volume,
        "price_change_percentage_24h": mid.get("price_change_percentage_24h"),
        # 7d change for coingecko_performance template (market_data may have _in_currency dict with usd)
        "price_change_percentage_7d_in_currency": _extract_usd_or_scalar(
            mid.get("price_change_percentage_7d_in_currency") or mid.get("price_change_percentage_7d")
        ),
        "market_cap_rank": int(rank) if rank is not None else None,
        "total_supply": total_supply,
        "circulating_supply": circulating_supply,
        "max_supply": max_supply,
        "ath": ath,
        "ath_change_percentage": ath_change_percentage,
    }


async def _fetch_single_coin_via_coins_id(coin_id: str) -> Dict[str, Any]:
    """
    Fetch one coin via GET /coins/{id} (works well with Demo API; returns full market_data).
    Returns normalized flat dict compatible with /coins/markets item shape.
    """
    base_url = CoinGeckoClient.get_base_url()
    headers = CoinGeckoClient.get_headers()
    params: Dict[str, Any] = {
        "localization": "false",
        "tickers": "false",
        "market_data": "true",
        "community_data": "false",
        "developer_data": "false",
    }
    if CoinGeckoClient.get_demo_api_key():
        params["x_cg_demo_api_key"] = CoinGeckoClient.get_demo_api_key()
    await CoinGeckoClient._rate_limit()
    url = f"{base_url}/coins/{coin_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            body = await response.text()
            if response.status != 200:
                try:
                    err = json.loads(body) if body else {}
                    msg = err.get("error", err.get("message", body)) or body
                except Exception:
                    msg = body or f"status={response.status}"
                raise APIFetchError(
                    f"status={response.status} for coin_id={coin_id}: {msg}",
                    source="coingecko",
                    status_code=response.status,
                )
            data = json.loads(body) if body else None
    if not data or not isinstance(data, dict):
        raise APIFetchError(f"Empty or invalid response for coin_id={coin_id}", source="coingecko")
    normalized = _normalize_coins_id_response(data)
    # When API returns null for market_cap_rank (e.g. Maker), derive from /coins/markets list
    if normalized.get("market_cap_rank") is None:
        rank = await _fetch_market_cap_rank_from_list(coin_id)
        if rank is not None:
            normalized["market_cap_rank"] = rank
    return normalized


async def _fetch_market_cap_rank_from_list(coin_id: str) -> Optional[int]:
    """
    Fetch /coins/markets and return market_cap_rank for coin_id.
    Used when /coins/{id} returns market_cap_rank null (e.g. Maker on Demo API).
    First tries single-coin request; if rank is null, fetches top 250 and uses list position.
    """
    base_url = CoinGeckoClient.get_base_url()
    headers = CoinGeckoClient.get_headers()

    def mk_params(include_ids: bool) -> Dict[str, Any]:
        p: Dict[str, Any] = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 250 if not include_ids else 1,
            "page": 1,
            "sparkline": "false",
        }
        if include_ids:
            p["ids"] = coin_id
        if not CoinGeckoClient.get_demo_api_key():
            p["price_change_percentage"] = "24h,7d,30d"
        if CoinGeckoClient.get_demo_api_key():
            p["x_cg_demo_api_key"] = CoinGeckoClient.get_demo_api_key()
        return p

    try:
        # Try single-coin request first (may include market_cap_rank)
        await CoinGeckoClient._rate_limit()
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base_url}/coins/markets",
                params=mk_params(include_ids=True),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    return None
                data = await response.json()
        if isinstance(data, list) and len(data) > 0:
            item = data[0]
            if isinstance(item, dict) and item.get("id") == coin_id:
                rank = item.get("market_cap_rank")
                if isinstance(rank, (int, float)) and rank > 0:
                    return int(rank)
        # Rank null or missing: fetch top 250 and use position
        await CoinGeckoClient._rate_limit()
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base_url}/coins/markets",
                params=mk_params(include_ids=False),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    return None
                data = await response.json()
        if not isinstance(data, list):
            return None
        for i, item in enumerate(data):
            if isinstance(item, dict) and item.get("id") == coin_id:
                rank = item.get("market_cap_rank")
                if isinstance(rank, (int, float)) and rank > 0:
                    return int(rank)
                return i + 1
        return None
    except Exception:
        return None


async def fetch_single_coin_data(coin_id: str) -> Dict[str, Any]:
    """
    Fetch market data for a single coin.

    Used by page-based cache: each page caches its own coin's data.
    When using Demo API, uses GET /coins/{id} so we get full market_data and can use
    fully_diluted_valuation as fallback when market_cap is 0 (e.g. Maker).

    Args:
        coin_id: CoinGecko coin ID (e.g., "bitcoin", "ethereum")

    Returns:
        Dict with coin market data (flat shape: id, current_price, market_cap, etc.)

    Raises:
        APIFetchError: If API request fails or returns invalid data
    """
    if CoinGeckoClient.get_demo_api_key():
        return await _fetch_single_coin_via_coins_id(coin_id)

    try:
        async with aiohttp.ClientSession() as session:
            headers = CoinGeckoClient.get_headers()
            base_url = CoinGeckoClient.get_base_url()
            params = _single_coin_params(coin_id)

            await CoinGeckoClient._rate_limit()

            async with session.get(
                f"{base_url}/coins/markets",
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                body = await response.text()
                if response.status != 200:
                    try:
                        err_json = json.loads(body) if body else {}
                        msg = err_json.get("error", err_json.get("message", body)) or body
                    except Exception:
                        msg = body or f"status={response.status}"
                    raise APIFetchError(
                        f"status={response.status} for coin_id={coin_id}: {msg}",
                        source="coingecko",
                        status_code=response.status,
                    )
                data = json.loads(body) if body else None

        if not data:
            raise APIFetchError(f"Empty response for coin_id={coin_id}", source="coingecko")

        validate_api_response(data, list, f"coin_id={coin_id}")
        validate_api_response(data[0], dict, f"coin_id={coin_id} first element")
        return data[0]

    except APIFetchError:
        raise
    except Exception as e:
        raise APIFetchError(f"Unexpected error for {coin_id}: {e}", source="coingecko") from e
