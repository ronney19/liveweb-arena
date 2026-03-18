"""Taostats API client using TaoMarketCap Internal API (no rate limiting, no API key)"""

import asyncio
import contextvars
import json
import os
import time
from pathlib import Path
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


def _safe_float(value) -> Optional[float]:
    """Convert value to float, returning None for missing/invalid data."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


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

    # Convert rao values to TAO (None-safe: preserve None for missing data)
    _subnet_tao = _safe_float(snapshot.get("subnet_tao"))
    subnet_tao = _subnet_tao / RAO_TO_TAO if _subnet_tao is not None else None

    _alpha_in = _safe_float(snapshot.get("subnet_alpha_in"))
    alpha_in = _alpha_in / RAO_TO_TAO if _alpha_in is not None else None

    _volume = _safe_float(snapshot.get("subnet_volume"))
    volume = _volume / RAO_TO_TAO if _volume is not None else None

    _emission = _safe_float(snapshot.get("subnet_tao_in_emission"))
    emission = _emission / RAO_TO_TAO if _emission is not None else None

    # Liquidity from dtao
    _liquidity = _safe_float(dtao.get("taoLiquidity"))
    liquidity = _liquidity / RAO_TO_TAO if _liquidity is not None else None

    # Price is already in TAO units
    price = _safe_float(snapshot.get("price"))

    # Calculate market cap (price * total alpha supply)
    _alpha_out = _safe_float(snapshot.get("subnet_alpha_out"))
    alpha_out = _alpha_out / RAO_TO_TAO if _alpha_out is not None else None
    if price is not None and alpha_out is not None:
        market_cap = price * alpha_out
    else:
        market_cap = None

    return {
        "netuid": int(netuid),
        "name": name,
        "price": price,
        "tao_in": subnet_tao,
        "alpha_in": alpha_in,
        "market_cap": market_cap,
        # Price changes from dtao snapshot
        "price_change_1h": _safe_float(dtao.get("price_diff_hour")),
        "price_change_24h": _safe_float(dtao.get("price_diff_day")),
        "price_change_1w": _safe_float(dtao.get("price_diff_week")),
        "price_change_1m": _safe_float(dtao.get("price_diff_month")),
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

    Retries up to 3 times on transient errors (network, timeout, decode) so that
    GT collection and training dataset generation are more resilient.

    Returns:
        {
            "subnets": {
                "1": {"name": "...", "owner": "...", "price": ..., "tao_in": ...},
                ...
            }
        }
    """
    subnets = {}
    last_error = None
    max_attempts = 3

    for attempt in range(max_attempts):
        try:
            async with aiohttp.ClientSession() as session:
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

            if not subnets:
                raise APIFetchError("API returned no subnet data", source="taostats")

            return {"subnets": subnets}

        except APIFetchError:
            raise
        except Exception as e:
            last_error = e
            if attempt < max_attempts - 1:
                await asyncio.sleep(1.0 + attempt * 0.5)
                continue
            raise APIFetchError(f"Unexpected error: {e}", source="taostats") from last_error


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

_subnet_cache: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "_taostats_subnet_cache", default=None
)


async def _ensure_subnet_cache() -> Dict[str, Any]:
    """Ensure subnet cache is loaded."""
    cache = _subnet_cache.get()
    if cache is None:
        data = await fetch_all_subnets()
        cache = data.get("subnets", {})
        _subnet_cache.set(cache)
    return cache


def get_cached_subnets() -> Dict[str, Any]:
    """Get cached subnets (sync version for variable generation)."""
    return _subnet_cache.get() or {}


def _normalize_emission(subnets: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure emission values are percentages (sum to ~100), not absolute TAO.

    Returns a new dict to avoid mutating GT collector's shared data.
    """
    if not subnets:
        return subnets
    total = sum(
        float(s["emission"]) for s in subnets.values()
        if s.get("emission") is not None
    )
    # Absolute TAO values sum to <50; percentages sum to ~100
    if 0 < total < 50:
        import copy
        subnets = copy.deepcopy(subnets)
        for s in subnets.values():
            raw = s.get("emission")
            if raw is not None:
                s["emission"] = (float(raw) / total) * 100
    return subnets


def _filter_by_emission(subnets: Dict[str, Any]) -> Dict[str, Any]:
    """Filter subnets to top half by emission, removing low-activity noise subnets."""
    if not subnets:
        return subnets
    ranked = sorted(
        subnets.items(),
        key=lambda kv: float(kv[1]["emission"]) if kv[1].get("emission") is not None else -1,
        reverse=True,
    )
    keep = len(ranked) // 2
    filtered = dict(ranked[:keep])
    log("Filter", f"Emission top-half: {len(subnets)} → {len(filtered)} subnets")
    return filtered


def _get_file_cache_path() -> Path:
    """Get path for taostats subnet file cache."""
    cache_dir = os.environ.get("LIVEWEB_CACHE_DIR", "/var/lib/liveweb-arena/cache")
    return Path(cache_dir) / "_plugin_init" / "taostats_subnets.json"


def _get_cache_ttl() -> int:
    """Get cache TTL from environment."""
    from liveweb_arena.core.cache import DEFAULT_TTL
    return int(os.environ.get("LIVEWEB_CACHE_TTL", str(DEFAULT_TTL)))


def _is_file_cache_valid() -> bool:
    """Check if subnet file cache exists and is within TTL."""
    cache_file = _get_file_cache_path()
    if not cache_file.exists():
        return False
    try:
        cached = json.loads(cache_file.read_text())
        if time.time() - cached.get("_fetched_at", 0) < _get_cache_ttl():
            return bool(cached.get("subnets"))
    except Exception:
        pass
    return False


def _load_file_cache() -> Optional[dict]:
    """Load subnets from file cache if valid. Returns subnets dict or None."""
    cache_file = _get_file_cache_path()
    if not cache_file.exists():
        return None
    try:
        cached = json.loads(cache_file.read_text())
        if time.time() - cached.get("_fetched_at", 0) < _get_cache_ttl():
            subnets = cached.get("subnets", {})
            if subnets:
                return subnets
    except Exception:
        pass
    return None


def initialize_cache():
    """
    Initialize subnet cache synchronously.

    Must be called before generating taostats questions.
    Uses file lock to prevent multiple instances from fetching simultaneously.
    Checks file cache first, falls back to API fetch.
    """
    import fcntl

    if _subnet_cache.get() is not None:
        return  # Already initialized in this context

    # 1. Quick check without lock
    subnets = _load_file_cache()
    if subnets:
        log("Taostats", f"Loaded {len(subnets)} subnets from file cache")
        _subnet_cache.set(_filter_by_emission(subnets))
        return

    # 2. Acquire file lock — only one process fetches
    lock_path = _get_file_cache_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)

        # Re-check after lock — another process may have filled cache
        subnets = _load_file_cache()
        if subnets:
            log("Taostats", f"Loaded {len(subnets)} subnets (filled by another process)")
            _subnet_cache.set(_filter_by_emission(subnets))
            return

        # 3. Fetch from API
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, fetch_all_subnets())
                    data = future.result(timeout=60)
            else:
                data = loop.run_until_complete(fetch_all_subnets())
        except APIFetchError:
            raise
        except RuntimeError as e:
            if "no current event loop" in str(e).lower() or "no running event loop" in str(e).lower():
                data = asyncio.run(fetch_all_subnets())
            else:
                raise

        subnets = data.get("subnets", {})
        if not subnets:
            raise APIFetchError("API returned no subnet data", source="taostats")

        # 4. Write file cache
        try:
            cache_file = _get_file_cache_path()
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({
                "subnets": subnets,
                "_fetched_at": time.time(),
            }))
            log("Taostats", f"Saved {len(subnets)} subnets to file cache")
        except Exception:
            pass

        _subnet_cache.set(_filter_by_emission(subnets))
    finally:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        fd.close()
