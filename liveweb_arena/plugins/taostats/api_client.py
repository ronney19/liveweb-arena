"""Taostats API client using TaoMarketCap Internal API (no rate limiting, no API key)"""

import asyncio
from typing import Any, Dict, List, Optional
import aiohttp

from liveweb_arena.plugins.base_client import APIFetchError
from liveweb_arena.utils.logger import log

# Cache source name
CACHE_SOURCE = "taostats"

# TaoMarketCap Internal API - no API key required, no rate limiting
API_BASE_URL = "https://api.taomarketcap.com/internal/v1"

# Conversion factor: rao to TAO (1 TAO = 1e9 rao)
RAO_TO_TAO = 1e9


def _parse_subnet_data(subnet: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse subnet data from TaoMarketCap Internal API format.

    Args:
        subnet: Raw subnet data from API

    Returns:
        Normalized subnet data dict
    """
    netuid = subnet.get("netuid", 0)
    snapshot = subnet.get("latest_snapshot") or {}
    identities = snapshot.get("subnet_identities_v3") or {}
    dtao = snapshot.get("dtao") or {}

    # Get name from identities or fall back to symbol
    name = identities.get("subnetName", "") or snapshot.get("token_symbol", f"SN{netuid}")

    # Convert rao values to TAO
    subnet_tao = float(snapshot.get("subnet_tao", 0) or 0) / RAO_TO_TAO
    alpha_in = float(snapshot.get("subnet_alpha_in", 0) or 0) / RAO_TO_TAO
    volume = float(snapshot.get("subnet_volume", 0) or 0) / RAO_TO_TAO
    emission = float(snapshot.get("subnet_tao_in_emission", 0) or 0) / RAO_TO_TAO

    # Liquidity from dtao
    liquidity = float(dtao.get("taoLiquidity", 0) or 0) / RAO_TO_TAO

    # Price is already in TAO units
    price = float(snapshot.get("price", 0) or 0)

    # Calculate market cap (price * total alpha supply)
    alpha_out = float(snapshot.get("subnet_alpha_out", 0) or 0) / RAO_TO_TAO
    market_cap = price * alpha_out if price and alpha_out else 0

    return {
        "netuid": int(netuid),
        "name": name,
        "price": price,
        "tao_in": subnet_tao,
        "alpha_in": alpha_in,
        "market_cap": market_cap,
        # Price changes from dtao snapshot
        "price_change_1h": float(dtao.get("price_diff_hour", 0) or 0),
        "price_change_24h": float(dtao.get("price_diff_day", 0) or 0),
        "price_change_1w": float(dtao.get("price_diff_week", 0) or 0),
        "price_change_1m": float(dtao.get("price_diff_month", 0) or 0),
        # Volume and liquidity
        "volume_24h": volume,
        "liquidity": liquidity,
        # Owner and emission
        "owner": snapshot.get("subnet_owner", ""),
        "emission": emission,
        # Rank not directly available, will be calculated by templates if needed
        "rank": 0,
    }


async def fetch_all_subnets() -> Dict[str, Any]:
    """
    Fetch all subnets from TaoMarketCap Internal API.

    Returns:
        {
            "subnets": {
                "1": {"name": "...", "owner": "...", "price": ..., "tao_in": ...},
                ...
            }
        }
    """
    subnets = {}

    try:
        async with aiohttp.ClientSession() as session:
            # Fetch all subnets (paginated, get up to 200)
            async with session.get(
                f"{API_BASE_URL}/subnets",
                params={"limit": 200},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise APIFetchError(
                        f"status={resp.status}, body={body[:500]}",
                        source="taostats",
                        status_code=resp.status,
                    )

                data = await resp.json()
                results = data.get("results", [])

                for subnet in results:
                    netuid = str(subnet.get("netuid", ""))
                    if not netuid or netuid == "0":  # Skip root network
                        continue

                    subnets[netuid] = _parse_subnet_data(subnet)

    except APIFetchError:
        raise
    except Exception as e:
        raise APIFetchError(f"Unexpected error: {e}", source="taostats") from e

    if not subnets:
        raise APIFetchError("API returned no subnet data", source="taostats")

    return {"subnets": subnets}


async def fetch_single_subnet_data(subnet_id: str) -> Dict[str, Any]:
    """
    Fetch data for a single subnet.

    Args:
        subnet_id: Subnet ID (e.g., "27")

    Returns:
        Dict with subnet data

    Raises:
        APIFetchError: If API request fails
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_BASE_URL}/subnets/{subnet_id}",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise APIFetchError(
                        f"status={resp.status} for subnet_id={subnet_id}, body={body[:200]}",
                        source="taostats",
                        status_code=resp.status,
                    )

                subnet = await resp.json()
                return _parse_subnet_data(subnet)

    except APIFetchError:
        raise
    except Exception as e:
        raise APIFetchError(f"Failed to fetch subnet {subnet_id}: {e}", source="taostats") from e


async def fetch_homepage_api_data() -> Dict[str, Any]:
    """
    Fetch all subnets data for homepage.

    Returns data in format compatible with cache system:
    {
        "subnets": {
            "1": {"name": "...", "owner": "...", "price": ..., "tao_in": ...},
            ...
        }
    }
    """
    return await fetch_all_subnets()


# ============================================================
# Helper functions for templates
# ============================================================

_subnet_cache: Optional[Dict[str, Any]] = None


async def _ensure_subnet_cache() -> Dict[str, Any]:
    """Ensure subnet cache is loaded."""
    global _subnet_cache
    if _subnet_cache is None:
        data = await fetch_all_subnets()
        _subnet_cache = data.get("subnets", {})
    return _subnet_cache


def get_cached_subnets() -> Dict[str, Any]:
    """Get cached subnets (sync version for variable generation)."""
    global _subnet_cache
    return _subnet_cache or {}


async def get_active_subnet_ids() -> List[int]:
    """Get list of active subnet IDs."""
    subnets = await _ensure_subnet_cache()
    return [int(k) for k in subnets.keys() if k != "0"]


async def get_subnet_name(subnet_id: int) -> str:
    """Get subnet name by ID."""
    subnets = await _ensure_subnet_cache()
    subnet = subnets.get(str(subnet_id), {})
    return subnet.get("name", "")


async def get_subnet_data(subnet_id: int) -> Dict[str, Any]:
    """Get full subnet data by ID."""
    subnets = await _ensure_subnet_cache()
    return subnets.get(str(subnet_id), {})


def _normalize_emission(subnets: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure emission values are percentages (sum to ~100), not absolute TAO."""
    if not subnets:
        return subnets
    total = sum(float(s.get("emission", 0) or 0) for s in subnets.values())
    # Absolute TAO values sum to <10; percentages sum to ~100
    if 0 < total < 50:
        for s in subnets.values():
            raw = float(s.get("emission", 0) or 0)
            s["emission"] = (raw / total) * 100
    return subnets


def _filter_by_emission(subnets: Dict[str, Any]) -> Dict[str, Any]:
    """Filter subnets to top half by emission, removing low-activity noise subnets."""
    if not subnets:
        return subnets
    ranked = sorted(subnets.items(), key=lambda kv: kv[1].get("emission", 0), reverse=True)
    keep = len(ranked) // 2
    filtered = dict(ranked[:keep])
    log("Filter", f"Emission top-half: {len(subnets)} → {len(filtered)} subnets")
    return filtered


def clear_cache():
    """Clear the subnet cache."""
    global _subnet_cache
    _subnet_cache = None


def initialize_cache():
    """
    Initialize subnet cache synchronously.

    Must be called before generating taostats questions.
    Uses asyncio.run() to call async API.
    """
    global _subnet_cache
    if _subnet_cache is not None:
        return  # Already initialized

    try:
        # Try to get existing event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If loop is running, create a new task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, fetch_all_subnets())
                data = future.result(timeout=60)
        else:
            data = loop.run_until_complete(fetch_all_subnets())
    except APIFetchError:
        raise  # API errors propagate directly
    except RuntimeError as e:
        # Only handle "no event loop" errors, re-raise others
        if "no current event loop" in str(e).lower() or "no running event loop" in str(e).lower():
            data = asyncio.run(fetch_all_subnets())
        else:
            raise

    _subnet_cache = data.get("subnets", {})
    if not _subnet_cache:
        raise APIFetchError("API returned no subnet data", source="taostats")

    _subnet_cache = _filter_by_emission(_subnet_cache)
