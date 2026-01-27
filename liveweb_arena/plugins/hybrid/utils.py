"""Shared utilities for hybrid plugin templates."""

import asyncio
import logging
import time
from typing import Any, Callable, Optional, TypeVar

from liveweb_arena.plugins.coingecko.api_client import CoinGeckoClient
from liveweb_arena.plugins.stooq.api_client import StooqClient, StooqRateLimitError
from liveweb_arena.utils.logger import progress, progress_done, is_verbose

logger = logging.getLogger(__name__)

T = TypeVar('T')

# Thread-local cache context (set during evaluation)
_cache_context: Optional[Any] = None


def set_cache_context(context: Optional[Any]):
    """Set the cache context for current evaluation."""
    global _cache_context
    _cache_context = context


def get_cache_context() -> Optional[Any]:
    """Get the current cache context."""
    return _cache_context


async def retry_with_backoff(
    func: Callable[[], T],
    max_retries: int = 10,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    operation_name: str = "operation",
) -> T:
    """
    Retry an async operation with exponential backoff.

    Args:
        func: Async function to retry
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay cap in seconds
        operation_name: Name for logging purposes

    Returns:
        Result of the function

    Raises:
        RuntimeError: If all retries fail
        StooqRateLimitError: If Stooq rate limit is hit (no retry)
    """
    last_exception = None
    start_time = time.time()

    for attempt in range(max_retries):
        try:
            if is_verbose():
                elapsed = time.time() - start_time
                progress("GT", elapsed, 120, f"[{attempt+1}/{max_retries}] {operation_name}")
            result = await func()
            if result is not None:
                if is_verbose():
                    progress_done("GT", f"{operation_name} done in {time.time()-start_time:.1f}s")
                return result
            raise ValueError(f"{operation_name} returned None")
        except StooqRateLimitError:
            logger.error(f"{operation_name}: Stooq rate limit exceeded - stopping retries")
            raise
        except Exception as e:
            last_exception = e
            if attempt < max_retries - 1:
                delay = min(base_delay * (2 ** attempt), max_delay)
                logger.warning(
                    f"{operation_name} failed (attempt {attempt + 1}/{max_retries}): {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                # Show progress during wait
                wait_start = time.time()
                while time.time() - wait_start < delay:
                    if is_verbose():
                        elapsed = time.time() - start_time
                        progress("GT", elapsed, 120, f"[{attempt+1}/{max_retries}] retry wait {operation_name}")
                    await asyncio.sleep(min(1.0, delay - (time.time() - wait_start)))
            else:
                logger.error(f"{operation_name} failed after {max_retries} attempts: {e}")

    raise RuntimeError(f"{operation_name} failed after {max_retries} retries: {last_exception}")


async def get_crypto_24h_change(coin_id: str) -> float:
    """
    Get 24h percentage change from CoinGecko with retry.

    Uses cache if available, otherwise falls back to live API.

    Args:
        coin_id: CoinGecko coin identifier

    Returns:
        24h percentage change

    Raises:
        RuntimeError: If all retries fail
    """
    # Try cache first
    ctx = get_cache_context()
    if ctx is not None:
        api_data = ctx.get_api_data("coingecko")
        if api_data:
            coins = api_data.get("coins", {})
            coin_data = coins.get(coin_id)
            if coin_data:
                change = coin_data.get("price_change_percentage_24h")
                if change is not None:
                    logger.debug(f"Cache hit: CoinGecko {coin_id} change={change}")
                    return change
            logger.debug(f"Cache miss for CoinGecko {coin_id}, falling back to API")

    # Fall back to live API with retry
    async def fetch():
        data = await CoinGeckoClient.get_coin_market_data(coin_id)
        if data and len(data) > 0:
            change = data[0].get("price_change_percentage_24h")
            if change is not None:
                return change
        return None

    return await retry_with_backoff(
        fetch,
        max_retries=10,
        base_delay=1.0,
        operation_name=f"CoinGecko fetch {coin_id}",
    )


async def get_stooq_price(symbol: str) -> float:
    """
    Get current price from Stooq with retry.

    Uses StooqClient which handles cache internally.

    Args:
        symbol: Stooq symbol

    Returns:
        Current price

    Raises:
        RuntimeError: If all retries fail
    """
    async def fetch():
        data = await StooqClient.get_price_data(symbol)
        if data:
            price = data.get("close")
            if price is not None:
                return price
        return None

    return await retry_with_backoff(
        fetch,
        max_retries=10,
        base_delay=1.0,
        operation_name=f"Stooq price {symbol}",
    )


async def get_stooq_24h_change(symbol: str) -> float:
    """
    Get daily percentage change from Stooq with retry.

    Uses StooqClient which handles cache internally.

    Args:
        symbol: Stooq symbol

    Returns:
        Daily percentage change

    Raises:
        RuntimeError: If all retries fail
    """
    async def fetch():
        data = await StooqClient.get_price_data(symbol)
        if data:
            change = data.get("daily_change_pct")
            if change is not None:
                return change
        return None

    return await retry_with_backoff(
        fetch,
        max_retries=10,
        base_delay=1.0,
        operation_name=f"Stooq change {symbol}",
    )
