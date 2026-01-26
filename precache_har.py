#!/usr/bin/env python3
"""
Pre-cache HAR files by visiting all possible pages.

This script visits all pages that might be accessed during evaluation,
creating a comprehensive HAR cache that can be reused across all seeds.

Usage:
    python precache_har.py [--sources coingecko,stooq,weather,tmdb,taostats]
"""

import asyncio
import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from liveweb_arena.core.cache_manager import CacheManager, EvaluationCacheContext


def get_all_urls_for_source(source: str) -> list:
    """Get all URLs that should be pre-cached for a source."""
    urls = []

    if source == "coingecko":
        try:
            from liveweb_arena.plugins.coingecko.templates.price import CoinVariable
            for coin in CoinVariable.COINS:
                urls.append(f"https://www.coingecko.com/en/coins/{coin.coin_id}")
        except ImportError:
            print(f"Warning: Could not import CoinGecko variables")

    elif source == "stooq":
        try:
            from liveweb_arena.plugins.stooq.templates.variables import (
                US_STOCKS, INDICES, CURRENCIES, COMMODITIES
            )
            for stock in US_STOCKS:
                urls.append(f"https://stooq.com/q/?s={stock.symbol}")
            for index in INDICES:
                urls.append(f"https://stooq.com/q/?s={index.symbol}")
            for currency in CURRENCIES:
                urls.append(f"https://stooq.com/q/?s={currency.symbol}")
            for commodity in COMMODITIES:
                urls.append(f"https://stooq.com/q/?s={commodity.symbol}")
        except ImportError:
            print(f"Warning: Could not import Stooq variables")

    elif source == "weather":
        try:
            from liveweb_arena.plugins.weather.templates.variables import LocationVariable
            for region, cities in LocationVariable.CITY_SEEDS.items():
                for city, country in cities:
                    query = f"{city},{country}".replace(" ", "+")
                    urls.append(f"https://wttr.in/{query}")
                    urls.append(f"https://v2.wttr.in/{query}")
            for code in LocationVariable.AIRPORT_CODES:
                urls.append(f"https://wttr.in/{code}")
                urls.append(f"https://v2.wttr.in/{code}")
        except ImportError:
            print(f"Warning: Could not import Weather variables")

    elif source == "tmdb":
        try:
            from liveweb_arena.plugins.tmdb.templates.variables import (
                CACHED_MOVIES, CACHED_PERSONS
            )
            for movie in CACHED_MOVIES:
                urls.append(f"https://www.themoviedb.org/movie/{movie.id}")
                urls.append(f"https://www.themoviedb.org/movie/{movie.id}/cast")
            for person in CACHED_PERSONS:
                urls.append(f"https://www.themoviedb.org/person/{person.id}")
        except ImportError:
            print(f"Warning: Could not import TMDB variables")

    elif source == "taostats":
        urls.append("https://taostats.io/")
        urls.append("https://taostats.io/subnets")
        # Add specific subnet pages
        for i in range(1, 50):  # Subnets 1-49
            urls.append(f"https://taostats.io/subnets/{i}")

    return urls


async def precache_source(source: str, cache_manager: CacheManager):
    """Pre-cache all pages for a source."""
    from playwright.async_api import async_playwright

    urls = get_all_urls_for_source(source)
    if not urls:
        print(f"No URLs found for source: {source}")
        return

    print(f"\nPre-caching {len(urls)} URLs for {source}...")

    # Create cache context using async context manager
    async with EvaluationCacheContext(cache_manager, [source]) as cache_context:
        # Get HAR path
        har_path, har_mode = cache_context.get_har_cache_info()
        print(f"HAR file: {har_path}")
        print(f"Mode: {har_mode}")

        if har_mode == "playback":
            print(f"HAR already exists, skipping pre-cache for {source}")
            return

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                record_har_path=str(har_path),
                record_har_mode="minimal",
            )
            page = await context.new_page()

            success = 0
            failed = 0

            for i, url in enumerate(urls, 1):
                try:
                    print(f"  [{i}/{len(urls)}] {url[:60]}...", end=" ", flush=True)
                    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(1000)  # Wait for dynamic content
                    print("✓")
                    success += 1
                except Exception as e:
                    print(f"✗ ({type(e).__name__})")
                    failed += 1

            await context.close()
            await browser.close()

            print(f"\nCompleted {source}: {success} success, {failed} failed")
            print(f"HAR saved to: {har_path}")


async def main():
    parser = argparse.ArgumentParser(description="Pre-cache HAR files")
    parser.add_argument(
        "--sources",
        default="coingecko,stooq",
        help="Comma-separated list of sources to pre-cache"
    )
    parser.add_argument(
        "--cache-dir",
        default="cache",
        help="Cache directory path"
    )
    args = parser.parse_args()

    sources = [s.strip() for s in args.sources.split(",")]
    cache_dir = Path(args.cache_dir)

    print(f"Pre-caching HAR for sources: {sources}")
    print(f"Cache directory: {cache_dir}")

    # Initialize cache manager
    cache_manager = CacheManager(cache_dir)

    # Ensure API cache is fresh first
    for source in sources:
        print(f"\nEnsuring fresh API cache for {source}...")
        try:
            version = await cache_manager.ensure_fresh(source)
            print(f"  API version: {version}")
        except Exception as e:
            print(f"  Warning: Could not refresh API cache: {e}")

    # Pre-cache each source
    for source in sources:
        await precache_source(source, cache_manager)

    print("\n" + "=" * 50)
    print("Pre-caching complete!")
    print("HAR files are now independent of seed.")


if __name__ == "__main__":
    asyncio.run(main())
