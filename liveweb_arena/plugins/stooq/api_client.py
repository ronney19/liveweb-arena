"""Stooq API client with caching support"""

import asyncio
import contextvars
import logging
from typing import Any, Dict, List, Optional

import aiohttp

from liveweb_arena.plugins.base_client import BaseAPIClient, RateLimiter

logger = logging.getLogger(__name__)

CACHE_SOURCE = "stooq"

# Rate limit tracking - once hit, don't retry until reset.
# Per-context: each evaluation gets its own rate limit state via contextvars,
# so concurrent evaluations don't interfere with each other.
_rate_limited: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_stooq_rate_limited", default=False
)


class StooqRateLimitError(Exception):
    """Raised when Stooq API rate limit is exceeded."""
    pass


def _parse_stooq_csv(csv_text: str, symbol: str = "") -> Optional[Dict[str, Any]]:
    """
    Parse Stooq CSV response into price data dict.

    Args:
        csv_text: Raw CSV text from Stooq API
        symbol: Optional symbol to include in result

    Returns:
        Dict with price data or None if parsing fails.
        Includes 'history' field with recent daily data for historical queries.
    """
    # Normalize line endings
    csv_text = csv_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = csv_text.strip().split("\n")

    if len(lines) < 2:
        return None

    headers = lines[0].lower().split(",")
    today_values = lines[-1].split(",")
    today_data = dict(zip(headers, today_values))

    def parse_float(val):
        try:
            return float(val) if val else None
        except (ValueError, TypeError):
            return None

    close = parse_float(today_data.get("close"))
    if close is None:
        return None

    # Calculate daily change from previous day
    daily_change = None
    daily_change_pct = None
    if len(lines) >= 3:
        prev_values = lines[-2].split(",")
        prev_data = dict(zip(headers, prev_values))
        prev_close = parse_float(prev_data.get("close"))
        if prev_close and prev_close > 0:
            daily_change = close - prev_close
            daily_change_pct = (daily_change / prev_close) * 100

    result = {
        "date": today_data.get("date", ""),
        "open": parse_float(today_data.get("open")),
        "high": parse_float(today_data.get("high")),
        "low": parse_float(today_data.get("low")),
        "close": close,
        "volume": parse_float(today_data.get("volume")),
        "daily_change": daily_change,
        "daily_change_pct": daily_change_pct,
    }
    if symbol:
        result["symbol"] = symbol

    # Parse historical data (last 30 days for historical queries)
    history = []
    data_lines = lines[1:]  # Skip header
    for line in data_lines[-30:]:  # Last 30 days
        values = line.split(",")
        if len(values) >= len(headers):
            row_data = dict(zip(headers, values))
            row_close = parse_float(row_data.get("close"))
            if row_close is not None:
                history.append({
                    "date": row_data.get("date", ""),
                    "open": parse_float(row_data.get("open")),
                    "high": parse_float(row_data.get("high")),
                    "low": parse_float(row_data.get("low")),
                    "close": row_close,
                    "volume": parse_float(row_data.get("volume")),
                })
    result["history"] = history

    return result


class StooqClient(BaseAPIClient):
    """Stooq CSV API client with rate limiting."""

    CSV_URL = "https://stooq.com/q/d/l/"
    _rate_limiter = RateLimiter(min_interval=0.5)

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
        # If already rate limited, raise immediately
        if _rate_limited.get():
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
                _rate_limited.set(True)
                logger.error("Stooq API daily limit exceeded!")
                raise StooqRateLimitError(
                    "Stooq API daily limit exceeded. Wait for reset or use cached data."
                )

            return _parse_stooq_csv(csv_text, symbol)

        except asyncio.TimeoutError:
            logger.warning(f"Stooq timeout for {symbol}")
            return None
        except Exception as e:
            logger.warning(f"Stooq error for {symbol}: {e}")
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
                        if "Exceeded the daily hits limit" in text:
                            failed += 1
                            return

                        parsed = _parse_stooq_csv(text, symbol)
                        if parsed:
                            result["assets"][symbol] = parsed

            except Exception:
                failed += 1

    # Fetch all with concurrency control
    await asyncio.gather(*[fetch_one(s) for s in assets])

    result["_meta"]["asset_count"] = len(result["assets"])
    logger.info(f"Fetched {len(result['assets'])} assets from Stooq ({failed} failed)")
    return result


async def fetch_homepage_api_data() -> Dict[str, Any]:
    """
    Fetch API data for Stooq homepage (all assets).

    Returns homepage format:
    {
        "assets": {
            "aapl.us": {<price_data>},
            "gc.f": {<price_data>},
            ...
        }
    }
    """
    data = await fetch_cache_api_data()
    if data and data.get("assets"):
        return {"assets": data["assets"]}
    return {"assets": {}}


async def fetch_single_asset_data(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Fetch price data for a single asset.

    Tries the symbol as-is first, then with common suffixes (.us)
    since Stooq's CSV API requires suffixed symbols for some markets.
    """
    if _rate_limited.get():
        raise StooqRateLimitError("Stooq API rate limited (persistent for this session)")

    # Try .us suffix first for bare symbols (canonical form for US stocks)
    variants = [symbol]
    if "." not in symbol and not symbol.startswith("^"):
        variants = [f"{symbol}.us", symbol]

    for sym in variants:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://stooq.com/q/d/l/?s={sym}&i=d"
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as response:
                    if response.status != 200:
                        continue

                    text = await response.text()
                    if "Exceeded the daily hits limit" in text:
                        _rate_limited.set(True)
                        raise StooqRateLimitError("Stooq API daily limit exceeded")

                    if "No data" in text:
                        continue

                    result = _parse_stooq_csv(text, sym)
                    if result:
                        return result

        except Exception:
            continue

    return {}
