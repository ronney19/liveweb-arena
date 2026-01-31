"""
Ground Truth Trigger System

Provides URL pattern matching for triggering GT collection.
Used by GTCollector to determine when to fetch API-based GT.

Note: GroundTruthManager has been replaced by GTCollector (gt_collector.py).
"""

import re
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional, Pattern
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class GTFailureType(Enum):
    """
    Types of ground truth extraction failures.

    Used to distinguish between valid and invalid evaluations:
    - DATA_NOT_COLLECTED: Agent didn't visit required pages (VALID evaluation, score=0)
    - SYSTEM_ERROR: Network/parsing/template errors (INVALID evaluation, sets error field)
    """
    DATA_NOT_COLLECTED = "data_not_collected"  # Agent capability issue
    SYSTEM_ERROR = "system_error"              # Mechanism/infrastructure issue


@dataclass
class GroundTruthResult:
    """
    Result of a ground truth fetch operation.

    Distinguishes between:
    - Success: value obtained
    - Retryable failure: temporary error (network timeout, rate limit)
    - Data not collected: agent didn't visit required pages (valid eval, score=0)
    - System error: infrastructure failure (invalid eval, sets error field)
    """
    success: bool
    value: Optional[Any] = None
    error: Optional[str] = None
    retryable: bool = False
    failure_type: Optional[GTFailureType] = None

    @classmethod
    def ok(cls, value: Any) -> "GroundTruthResult":
        """Successfully obtained ground truth value."""
        return cls(success=True, value=value)

    @classmethod
    def retry(cls, reason: str) -> "GroundTruthResult":
        """Retryable failure (network timeout, HTTP 5xx, rate limit)."""
        return cls(success=False, error=reason, retryable=True)

    @classmethod
    def fail(cls, reason: str) -> "GroundTruthResult":
        """
        Permanent failure - data not collected.

        Use this when agent didn't visit required pages or data is missing
        from collected cache. This is a VALID evaluation (agent capability issue),
        not a system error.

        For system errors (network, parsing, template bugs), use error() instead.
        """
        return cls(
            success=False,
            error=reason,
            retryable=False,
            failure_type=GTFailureType.DATA_NOT_COLLECTED,
        )

    @classmethod
    def not_collected(cls, reason: str) -> "GroundTruthResult":
        """
        Data not collected - agent didn't visit required pages.

        This is a VALID evaluation - the agent failed to complete navigation,
        which is an agent capability issue. Score will be 0, but no error field.

        Alias for fail() with explicit semantics.
        """
        return cls(
            success=False,
            error=reason,
            retryable=False,
            failure_type=GTFailureType.DATA_NOT_COLLECTED,
        )

    @classmethod
    def system_error(cls, reason: str) -> "GroundTruthResult":
        """
        System error - infrastructure/mechanism failure.

        Use this for network errors, parsing failures, template bugs, etc.
        This is an INVALID evaluation - the error field will be set.
        """
        return cls(
            success=False,
            error=reason,
            retryable=False,
            failure_type=GTFailureType.SYSTEM_ERROR,
        )

    def is_system_error(self) -> bool:
        """Check if this failure is a system error (invalid evaluation)."""
        return self.failure_type == GTFailureType.SYSTEM_ERROR

    def is_data_not_collected(self) -> bool:
        """Check if this failure is due to data not being collected (valid evaluation)."""
        return self.failure_type == GTFailureType.DATA_NOT_COLLECTED


class FetchStrategy(Enum):
    """
    DEPRECATED: FetchStrategy is no longer used.

    The GT collection system now uses a simpler approach:
    - Page extraction happens in real-time during navigation (newer data overwrites older)
    - API fetching happens at end of trajectory

    This enum is kept for backwards compatibility but has no effect.
    """

    FIRST = "first"      # Deprecated: was "use first trigger only"
    LAST = "last"        # Deprecated: was "use last trigger"
    ALL = "all"          # Deprecated: was "record all"


@dataclass
class TriggerConfig:
    """
    Configuration for ground truth triggering.

    Note: strategy is deprecated and ignored. The GT system now always:
    - Extracts from pages in real-time (newer overwrites older)
    - Fetches API data at end of trajectory
    """

    trigger: "GroundTruthTrigger"
    strategy: FetchStrategy = FetchStrategy.FIRST  # Deprecated, ignored
    min_fetch_interval: float = 120.0  # Deprecated, ignored


class GroundTruthTrigger(ABC):
    """Base class for ground truth fetch triggers."""

    @abstractmethod
    def matches(self, url: str) -> bool:
        """
        Check if the trigger condition is met.

        Args:
            url: The URL the agent just navigated to

        Returns:
            True if ground truth should be fetched now
        """
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of the trigger condition."""
        pass


class UrlPatternTrigger(GroundTruthTrigger):
    """
    Trigger based on URL pattern matching.

    Most common trigger type - fires when AI visits specific domains/paths.

    Examples:
        UrlPatternTrigger(domains=["wttr.in"])
        UrlPatternTrigger(domains=["stooq.com"], path_contains="/q/d/")
        UrlPatternTrigger(url_regex=r"wttr\\.in/[A-Za-z]+")
    """

    def __init__(
        self,
        domains: Optional[List[str]] = None,
        path_contains: Optional[str] = None,
        url_regex: Optional[str] = None,
        url_contains: Optional[str] = None,
    ):
        """
        Args:
            domains: List of domain names to match (e.g., ["wttr.in", "weather.com"])
            path_contains: String that must appear in URL path
            url_regex: Regex pattern for full URL matching
            url_contains: Simple substring match on full URL
        """
        self.domains = domains or []
        self.path_contains = path_contains
        self.url_regex: Optional[Pattern] = re.compile(url_regex) if url_regex else None
        self.url_contains = url_contains

    def matches(self, url: str) -> bool:
        if not url or url == "about:blank":
            return False

        try:
            parsed = urlparse(url)
        except Exception:
            return False

        # Check domain match
        if self.domains:
            domain_match = any(d in parsed.netloc for d in self.domains)
            if not domain_match:
                return False

        # Check path contains
        if self.path_contains:
            if self.path_contains not in parsed.path:
                return False

        # Check regex
        if self.url_regex:
            if not self.url_regex.search(url):
                return False

        # Check simple contains (with URL normalization for robust matching)
        if self.url_contains:
            if not self._normalized_contains(url, self.url_contains):
                return False

        return True

    def _normalized_contains(self, url: str, pattern: str) -> bool:
        """
        Check if URL contains pattern with normalization.

        Handles URL encoding variations:
        - "Hong Kong" vs "Hong+Kong" vs "Hong%20Kong"
        - Case differences in paths
        """
        from urllib.parse import unquote

        # Normalize URL: decode and replace + with space
        url_normalized = unquote(url.replace("+", " ")).lower()

        # Normalize pattern: decode and replace + with space
        pattern_normalized = unquote(pattern.replace("+", " ")).lower()

        return pattern_normalized in url_normalized

    @property
    def description(self) -> str:
        parts = []
        if self.domains:
            parts.append(f"domains: {self.domains}")
        if self.path_contains:
            parts.append(f"path contains: {self.path_contains}")
        if self.url_regex:
            parts.append(f"regex: {self.url_regex.pattern}")
        if self.url_contains:
            parts.append(f"contains: {self.url_contains}")
        return f"UrlPatternTrigger({', '.join(parts)})"


class UrlWithParamsTrigger(GroundTruthTrigger):
    """
    Trigger that requires specific URL parameters.

    Useful when the data source requires specific query parameters.

    Example:
        UrlWithParamsTrigger(
            domains=["stooq.com"],
            required_path="/q/d/",
            required_params=["s"]  # stock symbol parameter
        )
    """

    def __init__(
        self,
        domains: List[str],
        required_path: Optional[str] = None,
        required_params: Optional[List[str]] = None,
    ):
        self.domains = domains
        self.required_path = required_path
        self.required_params = required_params or []

    def matches(self, url: str) -> bool:
        if not url or url == "about:blank":
            return False

        try:
            parsed = urlparse(url)
        except Exception:
            return False

        # Check domain
        if not any(d in parsed.netloc for d in self.domains):
            return False

        # Check path
        if self.required_path and self.required_path not in parsed.path:
            return False

        # Check required params
        if self.required_params:
            query = parsed.query
            for param in self.required_params:
                if f"{param}=" not in query and not query.startswith(f"{param}="):
                    return False

        return True

    @property
    def description(self) -> str:
        return f"UrlWithParamsTrigger(domains={self.domains}, path={self.required_path})"


class CompositeTrigger(GroundTruthTrigger):
    """
    Combines multiple triggers with OR logic.

    Useful when data can come from multiple sources.

    Example:
        CompositeTrigger([
            UrlPatternTrigger(domains=["wttr.in"]),
            UrlPatternTrigger(domains=["weather.com"]),
        ])
    """

    def __init__(self, triggers: List[GroundTruthTrigger]):
        self.triggers = triggers

    def matches(self, url: str) -> bool:
        return any(t.matches(url) for t in self.triggers)

    @property
    def description(self) -> str:
        return f"CompositeTrigger(OR: {[t.description for t in self.triggers]})"
