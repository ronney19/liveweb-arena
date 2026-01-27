"""TMDB API client with rate limiting, Bearer token auth, and caching support"""

import os
import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp

from liveweb_arena.utils.logger import log

logger = logging.getLogger(__name__)

# Cache source name
CACHE_SOURCE = "tmdb"

# Global cache context reference (set by env.py during evaluation)
_cache_context: Optional[Any] = None


def set_tmdb_cache_context(context: Optional[Any]):
    """Set the cache context for TMDB API calls."""
    global _cache_context
    _cache_context = context


def get_tmdb_cache_context() -> Optional[Any]:
    """Get the current cache context."""
    return _cache_context


class TMDBClient:
    """
    Centralized TMDB API client.

    Uses Bearer token authentication via TMDB_API_KEY environment variable.
    TMDB rate limit: 40 requests per 10 seconds.
    """

    API_BASE = "https://api.themoviedb.org/3"

    # Rate limiting
    _last_request_time: float = 0
    _min_request_interval: float = 0.25  # 4 requests per second to stay under limit
    _lock = asyncio.Lock()

    @classmethod
    def get_api_key(cls) -> Optional[str]:
        """Get API key from environment."""
        return os.getenv("TMDB_API_KEY")

    @classmethod
    def get_headers(cls) -> Dict[str, str]:
        """Get request headers with Bearer token."""
        api_key = cls.get_api_key()
        headers = {
            "Accept": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

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
    async def get(
        cls,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 15.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Make GET request to TMDB API.

        Args:
            endpoint: API endpoint (e.g., "/movie/550")
            params: Query parameters
            timeout: Request timeout in seconds

        Returns:
            JSON response or None on error
        """
        await cls._rate_limit()

        url = f"{cls.API_BASE}{endpoint}"
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
    async def get_movie(cls, movie_id: str) -> Optional[Dict[str, Any]]:
        """
        Get movie details by ID.

        Args:
            movie_id: TMDB movie ID

        Returns:
            Movie data dict or None
        """
        # Try cache first
        ctx = get_tmdb_cache_context()
        if ctx is not None:
            api_data = ctx.get_api_data("tmdb")
            if api_data:
                movies = api_data.get("movies", {})
                movie_data = movies.get(str(movie_id))
                if movie_data:
                    log("GT", f"CACHE HIT - TMDB movie: {movie_id}", force=True)
                    # Return movie info without credits
                    return {k: v for k, v in movie_data.items() if k != "credits"}

                # Cache mode but data not found - this is an error
                log("GT", f"CACHE MISS - TMDB movie: {movie_id} not in cache ({len(movies)} movies cached)", force=True)
                return None

        # No cache context - use live API
        return await cls.get(f"/movie/{movie_id}")

    @classmethod
    async def get_movie_credits(cls, movie_id: str) -> Optional[Dict[str, Any]]:
        """
        Get movie credits (cast and crew) by ID.

        Args:
            movie_id: TMDB movie ID

        Returns:
            Credits data dict or None
        """
        # Try cache first
        ctx = get_tmdb_cache_context()
        if ctx is not None:
            api_data = ctx.get_api_data("tmdb")
            if api_data:
                movies = api_data.get("movies", {})
                movie_data = movies.get(str(movie_id))
                if movie_data and "credits" in movie_data:
                    log("GT", f"CACHE HIT - TMDB credits: {movie_id}", force=True)
                    return movie_data["credits"]

                # Cache mode but data not found - this is an error
                log("GT", f"CACHE MISS - TMDB credits: {movie_id} not in cache ({len(movies)} movies cached)", force=True)
                return None

        # No cache context - use live API
        return await cls.get(f"/movie/{movie_id}/credits")

    @classmethod
    async def get_movie_with_credits(cls, movie_id: str) -> Optional[Dict[str, Any]]:
        """
        Get movie details with appended credits.

        Args:
            movie_id: TMDB movie ID

        Returns:
            Movie data with credits or None
        """
        # Try cache first
        ctx = get_tmdb_cache_context()
        if ctx is not None:
            api_data = ctx.get_api_data("tmdb")
            if api_data:
                movies = api_data.get("movies", {})
                movie_data = movies.get(str(movie_id))
                if movie_data and "credits" in movie_data:
                    log("GT", f"CACHE HIT - TMDB movie+credits: {movie_id}", force=True)
                    return movie_data

                # Cache mode but data not found - this is an error
                log("GT", f"CACHE MISS - TMDB movie+credits: {movie_id} not in cache ({len(movies)} movies cached)", force=True)
                return None

        # No cache context - use live API
        return await cls.get(f"/movie/{movie_id}", params={"append_to_response": "credits"})


# ============================================================
# Cache Data Fetcher (used by snapshot_integration)
# ============================================================

async def fetch_cache_api_data() -> Optional[Dict[str, Any]]:
    """
    Fetch TMDB movie data for all movies defined in variables.

    Returns data structure:
    {
        "_meta": {"source": "tmdb", "movie_count": N},
        "movies": {
            "872585": {
                "id": 872585,
                "title": "Oppenheimer",
                ...
                "credits": {"cast": [...], "crew": [...]}
            },
            ...
        }
    }
    """
    from .templates.variables import MovieVariable

    api_key = TMDBClient.get_api_key()
    if not api_key:
        logger.warning("TMDB_API_KEY not set, skipping TMDB cache")
        return {"_meta": {"source": CACHE_SOURCE, "movie_count": 0}, "movies": {}}

    movies_list = MovieVariable.MOVIES
    logger.info(f"Fetching TMDB data for {len(movies_list)} movies...")

    result = {
        "_meta": {"source": CACHE_SOURCE, "movie_count": 0},
        "movies": {},
    }
    failed = 0

    # Rate limit: 4 requests per second (TMDB limit is 40 per 10 sec)
    semaphore = asyncio.Semaphore(4)

    async def fetch_movie(movie):
        nonlocal failed
        async with semaphore:
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"https://api.themoviedb.org/3/movie/{movie.movie_id}"
                    headers = TMDBClient.get_headers()
                    params = {"append_to_response": "credits"}

                    async with session.get(
                        url,
                        params=params,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as response:
                        if response.status == 429:
                            # Rate limited - wait and retry
                            await asyncio.sleep(5)
                            async with session.get(
                                url,
                                params=params,
                                headers=headers,
                                timeout=aiohttp.ClientTimeout(total=15),
                            ) as retry:
                                if retry.status != 200:
                                    failed += 1
                                    return
                                data = await retry.json()
                        elif response.status != 200:
                            failed += 1
                            return
                        else:
                            data = await response.json()

                        result["movies"][movie.movie_id] = data
                        await asyncio.sleep(0.25)  # Rate limiting

            except Exception as e:
                logger.debug(f"Failed to fetch TMDB {movie.movie_id}: {e}")
                failed += 1

    await asyncio.gather(*[fetch_movie(m) for m in movies_list])

    result["_meta"]["movie_count"] = len(result["movies"])
    logger.info(f"Fetched {len(result['movies'])} movies from TMDB ({failed} failed)")
    return result


async def fetch_single_movie_data(movie_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch movie data with credits for a single movie.

    Used by page-based cache: each page caches its own movie's data.

    Args:
        movie_id: TMDB movie ID (e.g., "872585")

    Returns:
        Dict with movie data including credits, or empty dict on error
    """
    api_key = TMDBClient.get_api_key()
    if not api_key:
        logger.warning("TMDB_API_KEY not set")
        return {}

    logger.debug(f"Fetching TMDB data for movie {movie_id}...")

    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.themoviedb.org/3/movie/{movie_id}"
            headers = TMDBClient.get_headers()
            params = {"append_to_response": "credits"}

            # Rate limit
            await TMDBClient._rate_limit()

            async with session.get(
                url,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status == 429:
                    # Rate limited - wait and retry
                    await asyncio.sleep(5)
                    async with session.get(
                        url,
                        params=params,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as retry:
                        if retry.status != 200:
                            return {}
                        return await retry.json()
                elif response.status != 200:
                    logger.warning(f"TMDB error for {movie_id}: {response.status}")
                    return {}
                return await response.json()

    except Exception as e:
        logger.debug(f"Failed to fetch TMDB {movie_id}: {e}")
        return {}
