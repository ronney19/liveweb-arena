"""Shared URL blocking patterns for tracking, analytics, and ads.

Used by both CacheInterceptor (main browser) and CacheManager._fetch_page
(prefetch browser) to block tracking/ad requests that delay networkidle.
"""

import re
from typing import List

TRACKING_BLOCK_PATTERNS: List[str] = [
    # Google
    r"google-analytics\.com",
    r"googletagmanager\.com",
    r"googlesyndication\.com",
    r"googleadservices\.com",
    r"google\.com/recaptcha",
    r"doubleclick\.net",
    # Social widgets
    r"facebook\.com/tr",
    r"platform\.twitter\.com",
    r"syndication\.twitter\.com",
    # Analytics
    r"hotjar\.com",
    r"sentry\.io",
    r"analytics",
    r"tracking",
    r"pixel",
    r"beacon",
    # Ad networks & sync
    r"rubiconproject\.com",
    r"criteo\.com",
    r"3lift\.com",
    r"pubmatic\.com",
    r"media\.net",
    r"adnxs\.com",
    r"presage\.io",
    r"onetag-sys\.com",
    r"seedtag\.com",
    r"openx\.net",
    r"btloader\.com",
    r"tappx\.com",
    r"cloudflare\.com/cdn-cgi/challenge",
    # Generic patterns
    r"usync",
    r"syncframe",
    r"user_sync",
    r"checksync",
    # Site-specific ads
    r"stooq\.com/ads/",
]

_BLOCK_RE = re.compile("|".join(TRACKING_BLOCK_PATTERNS), re.IGNORECASE)


def should_block_url(url: str) -> bool:
    """Check if URL matches any tracking/ads pattern."""
    return bool(_BLOCK_RE.search(url))
