"""Taostats API client using official taostats.io API"""

import logging
import os
from typing import Any, Dict, List, Optional
import aiohttp

logger = logging.getLogger(__name__)

# Cache source name
CACHE_SOURCE = "taostats"

# API configuration
API_BASE_URL = "https://api.taostats.io/api"
API_KEY = os.environ.get("TAOSTATS_API_KEY", "")


def _get_headers() -> Dict[str, str]:
    """Get API request headers."""
    return {
        "Authorization": API_KEY,
        "Content-Type": "application/json",
    }


async def fetch_all_subnets() -> Dict[str, Any]:
    """
    Fetch all subnets from taostats API.

    Returns:
        {
            "subnets": {
                "1": {"name": "...", "owner": "...", "price": ..., "tao_in": ...},
                ...
            }
        }
    """
    if not API_KEY:
        logger.warning("TAOSTATS_API_KEY not set")
        return {"subnets": {}}

    subnets = {}

    try:
        async with aiohttp.ClientSession() as session:
            # Fetch pool data first (has name, price, etc.)
            async with session.get(
                f"{API_BASE_URL}/dtao/pool/latest/v1",
                headers=_get_headers(),
                params={"limit": 200}
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Pool API error: {resp.status}")
                    return {"subnets": {}}

                pool_data = await resp.json()

                for pool in pool_data.get("data", []):
                    netuid = str(pool.get("netuid", ""))
                    if not netuid or netuid == "0":  # Skip root network
                        continue

                    subnets[netuid] = {
                        "netuid": int(netuid),
                        "name": pool.get("name", ""),
                        "price": float(pool.get("price", 0) or 0),
                        "tao_in": float(pool.get("total_tao", 0) or 0),
                        "alpha_in": float(pool.get("alpha_in_pool", 0) or 0),
                        "market_cap": float(pool.get("market_cap", 0) or 0),
                    }

            # Fetch subnet data for owner info
            async with session.get(
                f"{API_BASE_URL}/subnet/latest/v1",
                headers=_get_headers(),
                params={"limit": 200}
            ) as resp:
                if resp.status == 200:
                    subnet_data = await resp.json()

                    for subnet in subnet_data.get("data", []):
                        netuid = str(subnet.get("netuid", ""))
                        if netuid in subnets:
                            subnets[netuid]["owner"] = subnet.get("owner", {}).get("ss58", "")
                            subnets[netuid]["emission"] = subnet.get("emission", 0)

    except Exception as e:
        logger.error(f"Failed to fetch subnets: {e}")
        return {"subnets": {}}

    return {"subnets": subnets}


async def fetch_single_subnet_data(subnet_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch data for a single subnet.

    Args:
        subnet_id: Subnet ID (e.g., "27")

    Returns:
        Dict with subnet data, or empty dict on error
    """
    if not API_KEY:
        logger.warning("TAOSTATS_API_KEY not set")
        return {}

    try:
        result = {"netuid": int(subnet_id)}

        async with aiohttp.ClientSession() as session:
            # Fetch pool data (has name, price, etc.)
            async with session.get(
                f"{API_BASE_URL}/dtao/pool/latest/v1",
                headers=_get_headers(),
                params={"netuid": subnet_id}
            ) as resp:
                if resp.status == 200:
                    pool_data = await resp.json()
                    items = pool_data.get("data", [])
                    if items:
                        pool = items[0]
                        result["name"] = pool.get("name", "")
                        result["price"] = float(pool.get("price", 0) or 0)
                        result["tao_in"] = float(pool.get("total_tao", 0) or 0)
                        result["alpha_in"] = float(pool.get("alpha_in_pool", 0) or 0)
                        result["market_cap"] = float(pool.get("market_cap", 0) or 0)

            # Fetch subnet info for owner
            async with session.get(
                f"{API_BASE_URL}/subnet/latest/v1",
                headers=_get_headers(),
                params={"netuid": subnet_id}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("data", [])
                    if items:
                        subnet = items[0]
                        result["owner"] = subnet.get("owner", {}).get("ss58", "")
                        result["emission"] = subnet.get("emission", 0)

            return result if len(result) > 1 else {}

    except Exception as e:
        logger.error(f"Failed to fetch subnet {subnet_id}: {e}")
        return {}


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
    import asyncio

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
    except RuntimeError:
        # No event loop exists, create one
        data = asyncio.run(fetch_all_subnets())

    _subnet_cache = data.get("subnets", {})
    if not _subnet_cache:
        raise RuntimeError("Failed to initialize taostats subnet cache - API returned no data")
