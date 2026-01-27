"""Stooq API client with caching support"""

import asyncio
import csv
import io
import logging
from typing import Any, Dict, List, Optional

import aiohttp

from liveweb_arena.utils.logger import log

logger = logging.getLogger(__name__)

# Cache source name
CACHE_SOURCE = "stooq"

# Global cache context reference (set by env.py during evaluation)
_cache_context: Optional[Any] = None

# Rate limit tracking - once hit, don't retry until reset
_rate_limited: bool = False


class StooqRateLimitError(Exception):
    """Raised when Stooq API rate limit is exceeded."""
    pass


def set_stooq_cache_context(context: Optional[Any]):
    """Set the cache context for Stooq API calls."""
    global _cache_context
    _cache_context = context


def get_stooq_cache_context() -> Optional[Any]:
    """Get the current cache context."""
    return _cache_context


def is_stooq_rate_limited() -> bool:
    """Check if Stooq API is currently rate limited."""
    return _rate_limited


def reset_stooq_rate_limit():
    """Reset rate limit flag (e.g., after daily reset)."""
    global _rate_limited
    _rate_limited = False


class StooqClient:
    """
    Centralized Stooq API client with caching support.

    Uses CSV download endpoint for price data.
    """

    CSV_URL = "https://stooq.com/q/d/l/"

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
    async def get_price_data(
        cls,
        symbol: str,
        timeout: float = 15.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Get price data for a symbol.

        Args:
            symbol: Stooq symbol (e.g., "gc.f", "^spx", "aapl.us")
            timeout: Request timeout in seconds

        Returns:
            Dict with price data or None on error:
            {
                "symbol": str,
                "date": str,
                "open": float,
                "high": float,
                "low": float,
                "close": float,
                "volume": float or None,
                "daily_change": float or None,
                "daily_change_pct": float or None,
            }

        Raises:
            StooqRateLimitError: If API rate limit is exceeded
        """
        global _rate_limited

        # Try cache first
        ctx = get_stooq_cache_context()
        if ctx is not None:
            api_data = ctx.get_api_data("stooq")
            if api_data:
                assets = api_data.get("assets", {})
                asset_data = assets.get(symbol)
                if asset_data:
                    log("GT", f"CACHE HIT - Stooq: {symbol}", force=True)
                    return asset_data

                # Cache mode but data not found - this is an error
                log("GT", f"CACHE MISS - Stooq: {symbol} not in cache ({len(assets)} assets cached)", force=True)
                return None
            else:
                log("GT", f"Stooq api_data empty - rebuild cache with --force", force=True)

        # No cache context - use live API (non-cache mode)
        # If already rate limited, raise immediately
        if _rate_limited:
            raise StooqRateLimitError(
                "Stooq API daily limit exceeded. Cache is empty. "
                "Wait for daily reset or manually populate cache."
            )

        # Fall back to live API
        await cls._rate_limit()

        try:
            async with aiohttp.ClientSession() as session:
                params = {"s": symbol, "i": "d"}
                async with session.get(
                    cls.CSV_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    if response.status != 200:
                        logger.warning(f"Stooq error for {symbol}: {response.status}")
                        return None
                    csv_text = await response.text()

            # Check for rate limit error
            if "Exceeded the daily hits limit" in csv_text:
                _rate_limited = True
                logger.error(
                    "Stooq API daily limit exceeded! No more API calls will succeed "
                    "until the limit resets (typically at midnight UTC)."
                )
                raise StooqRateLimitError(
                    "Stooq API daily limit exceeded. Wait for reset or use cached data."
                )

            # Normalize line endings (Windows -> Unix)
            csv_text = csv_text.replace("\r\n", "\n").replace("\r", "\n")
            reader = csv.DictReader(io.StringIO(csv_text))
            rows = list(reader)

            if not rows:
                return None

            latest = rows[-1]

            def parse_float(val):
                try:
                    return float(val) if val else None
                except (ValueError, TypeError):
                    return None

            close = parse_float(latest.get("Close"))
            open_price = parse_float(latest.get("Open"))
            high = parse_float(latest.get("High"))
            low = parse_float(latest.get("Low"))
            volume = parse_float(latest.get("Volume"))

            # Calculate daily change if we have previous data
            daily_change = None
            daily_change_pct = None
            if len(rows) >= 2:
                prev = rows[-2]
                prev_close = parse_float(prev.get("Close"))
                if prev_close and close:
                    daily_change = close - prev_close
                    daily_change_pct = (daily_change / prev_close) * 100

            return {
                "symbol": symbol,
                "date": latest.get("Date"),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "daily_change": daily_change,
                "daily_change_pct": daily_change_pct,
            }

        except asyncio.TimeoutError:
            logger.warning(f"Stooq timeout for {symbol}")
            return None
        except Exception as e:
            logger.warning(f"Stooq error for {symbol}: {e}")
            return None

    @classmethod
    async def get_historical_data(
        cls,
        symbol: str,
        timeout: float = 15.0,
    ) -> Optional[list]:
        """
        Get historical price data for a symbol.

        Args:
            symbol: Stooq symbol
            timeout: Request timeout in seconds

        Returns:
            List of daily price records or None on error
        """
        # Historical data is not cached (too large), always fetch live
        await cls._rate_limit()

        try:
            async with aiohttp.ClientSession() as session:
                params = {"s": symbol, "i": "d"}
                async with session.get(
                    cls.CSV_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    if response.status != 200:
                        return None
                    csv_text = await response.text()

            # Normalize line endings (Windows -> Unix)
            csv_text = csv_text.replace("\r\n", "\n").replace("\r", "\n")
            reader = csv.DictReader(io.StringIO(csv_text))
            return list(reader)

        except Exception as e:
            logger.warning(f"Stooq historical error for {symbol}: {e}")
            return None


# ============================================================
# Cache Data Fetcher (used by snapshot_integration)
# ============================================================

def _get_all_symbols() -> List[str]:
    """Get all symbols that need to be cached."""
    from .templates.variables import US_STOCKS, INDICES, CURRENCIES, COMMODITIES

    symbols = []
    symbols.extend(s.symbol for s in US_STOCKS)
    symbols.extend(s.symbol for s in INDICES)
    symbols.extend(s.symbol for s in CURRENCIES)
    symbols.extend(s.symbol for s in COMMODITIES)
    return symbols


async def fetch_cache_api_data() -> Optional[Dict[str, Any]]:
    """
    Fetch Stooq price data for all assets defined in variables.

    Returns data structure:
    {
        "_meta": {"source": "stooq", "asset_count": N},
        "assets": {
            "aapl.us": {"date": ..., "open": ..., "close": ..., "daily_change_pct": ...},
            ...
        }
    }
    """
    assets = _get_all_symbols()
    logger.info(f"Fetching Stooq data for {len(assets)} assets...")

    result = {
        "_meta": {
            "source": CACHE_SOURCE,
            "asset_count": 0,
        },
        "assets": {},
    }
    failed = 0

    # Rate limit: max 5 concurrent requests
    semaphore = asyncio.Semaphore(5)

    async def fetch_one(symbol: str):
        nonlocal failed
        async with semaphore:
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=15),
                        headers={"User-Agent": "Mozilla/5.0"},
                    ) as response:
                        if response.status != 200:
                            failed += 1
                            return

                        text = await response.text()

                        # Check for rate limit
                        if "Exceeded the daily hits limit" in text:
                            logger.warning(f"Stooq rate limit exceeded for {symbol}")
                            failed += 1
                            return

                        # Parse CSV
                        lines = text.strip().split("\n")
                        if len(lines) < 2:
                            failed += 1
                            return

                        headers = lines[0].lower().split(",")

                        # Get last row (today)
                        today_values = lines[-1].split(",")
                        today_data = dict(zip(headers, today_values))

                        close = float(today_data.get("close", 0))

                        # Calculate daily change if we have previous day data
                        daily_change = None
                        daily_change_pct = None
                        if len(lines) >= 3:  # header + at least 2 data rows
                            prev_values = lines[-2].split(",")
                            prev_data = dict(zip(headers, prev_values))
                            prev_close = float(prev_data.get("close", 0))
                            if prev_close > 0:
                                daily_change = close - prev_close
                                daily_change_pct = (daily_change / prev_close) * 100

                        result["assets"][symbol] = {
                            "date": today_data.get("date", ""),
                            "open": float(today_data.get("open", 0)),
                            "high": float(today_data.get("high", 0)),
                            "low": float(today_data.get("low", 0)),
                            "close": close,
                            "volume": float(today_data.get("volume", 0) or 0),
                            "daily_change": daily_change,
                            "daily_change_pct": daily_change_pct,
                        }

            except Exception as e:
                logger.debug(f"Failed to fetch {symbol}: {e}")
                failed += 1

    # Fetch all with concurrency control
    await asyncio.gather(*[fetch_one(s) for s in assets])

    result["_meta"]["asset_count"] = len(result["assets"])
    logger.info(f"Fetched {len(result['assets'])} assets from Stooq ({failed} failed)")
    return result


async def fetch_single_asset_data(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Fetch price data for a single asset.

    Used by page-based cache: each page caches its own asset's data.

    Args:
        symbol: Stooq symbol (e.g., "aapl.us", "gc.f")

    Returns:
        Dict with asset price data, or empty dict on error
    """
    global _rate_limited

    if _rate_limited:
        logger.warning(f"Stooq rate limited, skipping {symbol}")
        return {}

    logger.debug(f"Fetching Stooq data for {symbol}...")

    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "Mozilla/5.0"},
            ) as response:
                if response.status != 200:
                    logger.warning(f"Stooq error for {symbol}: {response.status}")
                    return {}

                text = await response.text()

                # Check for rate limit
                if "Exceeded the daily hits limit" in text:
                    _rate_limited = True
                    logger.warning(f"Stooq rate limit exceeded")
                    return {}

                # Parse CSV
                lines = text.strip().split("\n")
                if len(lines) < 2:
                    return {}

                headers = lines[0].lower().split(",")

                # Get last row (today)
                today_values = lines[-1].split(",")
                today_data = dict(zip(headers, today_values))

                close = float(today_data.get("close", 0))

                # Calculate daily change if we have previous day data
                daily_change = None
                daily_change_pct = None
                if len(lines) >= 3:  # header + at least 2 data rows
                    prev_values = lines[-2].split(",")
                    prev_data = dict(zip(headers, prev_values))
                    prev_close = float(prev_data.get("close", 0))
                    if prev_close > 0:
                        daily_change = close - prev_close
                        daily_change_pct = (daily_change / prev_close) * 100

                return {
                    "symbol": symbol,
                    "date": today_data.get("date", ""),
                    "open": float(today_data.get("open", 0)),
                    "high": float(today_data.get("high", 0)),
                    "low": float(today_data.get("low", 0)),
                    "close": close,
                    "volume": float(today_data.get("volume", 0) or 0),
                    "daily_change": daily_change,
                    "daily_change_pct": daily_change_pct,
                }

    except Exception as e:
        logger.debug(f"Failed to fetch {symbol}: {e}")
        return {}
