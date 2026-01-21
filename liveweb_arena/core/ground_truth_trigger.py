"""
Ground Truth Trigger System

Allows each question template to define when ground truth should be fetched,
ensuring synchronization between AI observation and ground truth data.

Design Principles:
1. Trigger conditions must be unavoidable for task completion
2. Each template defines its own trigger logic
3. Different fetch strategies: FIRST, LAST, ALL (for range validation)
4. Fallback: fetch at end if never triggered
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Pattern
from urllib.parse import urlparse


class FetchStrategy(Enum):
    """Strategy for when to fetch ground truth on multiple triggers."""

    FIRST = "first"      # Use first trigger only (default, simplest)
    LAST = "last"        # Use last trigger (if AI refines search)
    ALL = "all"          # Record all, use range for validation


@dataclass
class TriggerConfig:
    """Configuration for ground truth triggering."""

    trigger: "GroundTruthTrigger"
    strategy: FetchStrategy = FetchStrategy.FIRST
    min_fetch_interval: float = 120.0  # Minimum seconds between fetches for LAST/ALL


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


@dataclass
class GroundTruthFetch:
    """A single ground truth fetch record."""
    url: str
    value: Any
    error: Optional[str] = None
    timestamp: Optional[float] = None  # Unix timestamp when fetch occurred


@dataclass
class GroundTruthState:
    """Tracks ground truth fetching state for a subtask."""

    subtask_tag: str
    trigger: GroundTruthTrigger
    strategy: FetchStrategy = FetchStrategy.FIRST
    fetches: List[GroundTruthFetch] = field(default_factory=list)
    min_fetch_interval: float = 60.0  # Minimum seconds between fetches for ALL strategy

    @property
    def triggered(self) -> bool:
        """Whether at least one trigger occurred."""
        return len(self.fetches) > 0

    @property
    def last_fetch_time(self) -> Optional[float]:
        """Get timestamp of the most recent fetch."""
        if not self.fetches:
            return None
        return self.fetches[-1].timestamp

    @property
    def ground_truth(self) -> Any:
        """
        Get ground truth based on strategy.

        FIRST: Return first fetch value
        LAST: Return last fetch value
        ALL: Return deduplicated list of unique values (for range validation)
        """
        if not self.fetches:
            return None

        valid_fetches = [f for f in self.fetches if f.error is None]
        if not valid_fetches:
            return None

        if self.strategy == FetchStrategy.FIRST:
            return valid_fetches[0].value
        elif self.strategy == FetchStrategy.LAST:
            return valid_fetches[-1].value
        else:  # ALL - deduplicate by value
            seen = set()
            unique_values = []
            for f in valid_fetches:
                # Use string representation for deduplication
                val_key = str(f.value)
                if val_key not in seen:
                    seen.add(val_key)
                    unique_values.append(f.value)
            return unique_values

    @property
    def ground_truth_range(self) -> Optional[tuple]:
        """
        Get min/max range for ALL strategy (numeric values only).

        Returns:
            (min, max) tuple or None if not applicable
        """
        if self.strategy != FetchStrategy.ALL:
            return None

        values = self.ground_truth
        if not values:
            return None

        try:
            numeric = [float(v) if isinstance(v, (int, float, str)) else None for v in values]
            numeric = [v for v in numeric if v is not None]
            if numeric:
                return (min(numeric), max(numeric))
        except (ValueError, TypeError):
            pass

        return None

    def should_fetch_again(self) -> bool:
        """Whether to fetch on next trigger based on strategy and rate limiting."""
        import time

        if self.strategy == FetchStrategy.FIRST:
            return not self.triggered

        # LAST or ALL: check rate limiting
        if not self.triggered:
            return True

        # Enforce minimum interval between fetches
        last_time = self.last_fetch_time
        if last_time is not None:
            elapsed = time.time() - last_time
            if elapsed < self.min_fetch_interval:
                return False

        return True


class GroundTruthManager:
    """
    Manages ground truth fetching for all subtasks during evaluation.

    Usage:
        manager = GroundTruthManager(task_manager)
        manager.register_subtasks(task.subtasks)

        # In agent loop, after each navigation:
        await manager.check_triggers(current_url)

        # At end, ensure all are fetched:
        await manager.fetch_remaining()

        # Get results:
        ground_truths = manager.get_ground_truths()
    """

    def __init__(self, task_manager=None):
        self.states: Dict[str, GroundTruthState] = {}
        self._fetch_funcs: Dict[str, Callable] = {}
        self._task_manager = task_manager

    def register_subtasks(self, subtasks: list) -> None:
        """
        Register all subtasks for ground truth monitoring.

        Args:
            subtasks: List of SubTask objects
        """
        if self._task_manager is None:
            raise ValueError("task_manager required for register_subtasks")

        for subtask in subtasks:
            plugin = self._task_manager.get_plugin(subtask.plugin_name)
            config = plugin.get_ground_truth_trigger(subtask.validation_info)

            if config is None:
                continue

            # Create fetch function with captured values
            async def make_fetch_func(p=plugin, vi=subtask.validation_info):
                return await p.get_ground_truth(vi)

            self.states[subtask.answer_tag] = GroundTruthState(
                subtask_tag=subtask.answer_tag,
                trigger=config.trigger,
                strategy=config.strategy,
                min_fetch_interval=config.min_fetch_interval,
            )
            self._fetch_funcs[subtask.answer_tag] = make_fetch_func

    async def check_triggers(self, url: str, max_retries: int = 3) -> List[str]:
        """
        Check if any triggers match the current URL and fetch ground truth.

        Args:
            url: The URL the agent just navigated to
            max_retries: Maximum retry attempts for network errors

        Returns:
            List of subtask tags that were triggered and fetched
        """
        import asyncio
        import time

        triggered = []

        for tag, state in self.states.items():
            if not state.trigger.matches(url):
                continue

            if not state.should_fetch_again():
                continue

            # Fetch ground truth with retry for network errors
            fetch = GroundTruthFetch(url=url, value=None, timestamp=time.time())
            last_error = None

            for attempt in range(max_retries):
                try:
                    fetch_func = self._fetch_funcs[tag]
                    fetch.value = await fetch_func()
                    fetch.error = None  # Clear any previous error
                    break
                except Exception as e:
                    last_error = e
                    # Retry on network-related errors
                    error_name = type(e).__name__
                    if any(err in error_name for err in ['Timeout', 'Connect', 'Network']):
                        if attempt < max_retries - 1:
                            await asyncio.sleep(1.0 * (attempt + 1))
                            continue
                    # Non-retryable error or max retries reached
                    fetch.error = str(e)
                    break

            state.fetches.append(fetch)
            triggered.append(tag)

        return triggered

    async def fetch_remaining(self, subtasks: list = None, max_retries: int = 3):
        """
        Fetch ground truth for any subtasks that were never triggered.

        Also handles subtasks without triggers (legacy) if subtasks list is provided.

        Args:
            subtasks: Optional list of SubTask objects for legacy handling
            max_retries: Maximum retry attempts for network errors
        """
        import asyncio
        import time

        # Fetch for registered but not triggered
        for tag, state in self.states.items():
            if state.triggered:
                continue

            fetch = GroundTruthFetch(url="fallback", value=None, timestamp=time.time())

            for attempt in range(max_retries):
                try:
                    fetch_func = self._fetch_funcs[tag]
                    fetch.value = await fetch_func()
                    fetch.error = None
                    break
                except Exception as e:
                    error_name = type(e).__name__
                    if any(err in error_name for err in ['Timeout', 'Connect', 'Network']):
                        if attempt < max_retries - 1:
                            await asyncio.sleep(1.0 * (attempt + 1))
                            continue
                    fetch.error = str(e)
                    break

            state.fetches.append(fetch)

        # Handle subtasks without triggers (legacy)
        if subtasks and self._task_manager:
            for subtask in subtasks:
                if subtask.answer_tag in self.states:
                    continue  # Already registered

                plugin = self._task_manager.get_plugin(subtask.plugin_name)

                # Create state and fetch
                state = GroundTruthState(
                    subtask_tag=subtask.answer_tag,
                    trigger=None,
                    strategy=FetchStrategy.FIRST,
                )
                fetch = GroundTruthFetch(url="legacy", value=None, timestamp=time.time())

                for attempt in range(max_retries):
                    try:
                        fetch.value = await plugin.get_ground_truth(subtask.validation_info)
                        fetch.error = None
                        break
                    except Exception as e:
                        error_name = type(e).__name__
                        if any(err in error_name for err in ['Timeout', 'Connect', 'Network']):
                            if attempt < max_retries - 1:
                                await asyncio.sleep(1.0 * (attempt + 1))
                                continue
                        fetch.error = str(e)
                        break

                state.fetches.append(fetch)
                self.states[subtask.answer_tag] = state

    def get_ground_truths(self) -> Dict[str, Any]:
        """Get all ground truths as a dict."""
        return {
            tag: state.ground_truth
            for tag, state in self.states.items()
        }

    def get_ground_truth_ranges(self) -> Dict[str, Optional[tuple]]:
        """Get ground truth ranges for ALL strategy subtasks."""
        return {
            tag: state.ground_truth_range
            for tag, state in self.states.items()
            if state.strategy == FetchStrategy.ALL
        }

    def get_stats(self) -> dict:
        """Get statistics about ground truth fetching."""
        triggered_count = sum(
            1 for s in self.states.values()
            if s.triggered and s.fetches[0].url != "fallback"
        )
        fallback_count = sum(
            1 for s in self.states.values()
            if s.triggered and s.fetches[0].url == "fallback"
        )
        multi_fetch_count = sum(
            1 for s in self.states.values()
            if len(s.fetches) > 1
        )
        error_count = sum(
            1 for s in self.states.values()
            if any(f.error for f in s.fetches)
        )

        return {
            "total": len(self.states),
            "triggered": triggered_count,
            "fallback": fallback_count,
            "multi_fetch": multi_fetch_count,
            "errors": error_count,
        }

    def get_fetch_details(self) -> Dict[str, List[dict]]:
        """Get detailed fetch history for debugging."""
        return {
            tag: [
                {
                    "url": f.url,
                    "value": f.value,
                    "error": f.error,
                    "timestamp": f.timestamp,
                }
                for f in state.fetches
            ]
            for tag, state in self.states.items()
        }

    def get_fetch_summary(self) -> List[str]:
        """
        Get formatted fetch summary with deduplication and timestamps.

        Returns:
            List of formatted log lines
        """
        import time

        lines = []
        for tag, state in self.states.items():
            if not state.fetches:
                continue

            errors = [f for f in state.fetches if f.error]
            if errors:
                for f in errors:
                    lines.append(f"Fetch error [{tag}]: {f.error}")
                continue

            # Deduplicate by value
            seen = set()
            unique_fetches = []
            for f in state.fetches:
                val_key = str(f.value)
                if val_key not in seen:
                    seen.add(val_key)
                    unique_fetches.append(f)

            for f in unique_fetches:
                ts = time.strftime("%H:%M:%S", time.localtime(f.timestamp)) if f.timestamp else "?"
                val_str = str(f.value)[:60] if f.value else "None"
                suffix = "..." if f.value and len(str(f.value)) > 60 else ""
                lines.append(f"Fetch [{tag}] @{ts}: {val_str}{suffix}")

        return lines
