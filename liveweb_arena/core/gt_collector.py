"""
Unified Ground Truth Collection System

Design principle: API DATA ONLY, NO FALLBACK
- Cache mode: API data is bound to page snapshots (same data source)
- Live mode: Page visit triggers API fetch (consistent data)
- All GT comes from API data - no regex-based page extraction
"""

import contextvars
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from liveweb_arena.core.ground_truth_trigger import TriggerConfig
from liveweb_arena.utils.logger import log

if TYPE_CHECKING:
    from liveweb_arena.core.task_manager import SubTask

logger = logging.getLogger(__name__)

# Per-task reference for hybrid utils to access collected API data.
# Uses contextvars so concurrent async evaluations each see their own collector.
_current_gt_collector: contextvars.ContextVar[Optional["GTCollector"]] = contextvars.ContextVar(
    "_current_gt_collector", default=None
)


def get_current_gt_collector() -> Optional["GTCollector"]:
    """Get the current GTCollector instance (async-safe)."""
    return _current_gt_collector.get()


def set_current_gt_collector(collector: Optional["GTCollector"]):
    """Set the current GTCollector instance (async-safe)."""
    _current_gt_collector.set(collector)


class GTSourceType(Enum):
    """
    Ground truth source type declaration.

    All types now use API data (no page extraction):
    - PAGE_ONLY: Uses collected API data from visited pages
    - API_ONLY: Uses API data (for complex aggregations)
    - HYBRID: Uses collected API data (same as PAGE_ONLY)
    """
    PAGE_ONLY = "page_only"
    API_ONLY = "api_only"
    HYBRID = "hybrid"


@dataclass
class GTResult:
    """Result of GT collection for a single subtask."""
    tag: str
    source_type: GTSourceType
    value: Optional[str] = None
    api_data: Optional[Any] = None
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    @property
    def success(self) -> bool:
        return self.value is not None


class GTCollector:
    """
    Unified GT collector that manages GT collection for all subtasks.

    All GT data comes from API:
    - Collected API data from page visits (cache mode)
    - Direct API calls (live mode or complex templates)
    """

    def __init__(self, subtasks: List["SubTask"], task_manager=None):
        self.subtasks = subtasks
        self._task_manager = task_manager

        # API fetch results per subtask tag
        self._api_results: Dict[str, Any] = {}

        # GT failures per subtask tag - stores full GroundTruthResult for failure type info
        self._gt_failures: Dict[str, "GroundTruthResult"] = {}

        # Track visited URLs for each subtask
        self._visited_urls: Dict[str, List[str]] = {st.answer_tag: [] for st in subtasks}

        # Collected API data from page visits {asset_id: {field: value}}
        self._collected_api_data: Dict[str, Dict[str, Any]] = {}

        # Page content (accessibility tree) from visits {url: content}
        self._page_contents: Dict[str, str] = {}

        # Observation errors during GT collection
        self._observation_errors: List[str] = []

    def record_observation_error(self, url: str, error: str):
        """Record an error that occurred during observation callback."""
        self._observation_errors.append(f"{url}: {error}")

    def _get_source_type(self, subtask: "SubTask") -> GTSourceType:
        """Get GT source type for a subtask."""
        if self._task_manager is None:
            return GTSourceType.API_ONLY

        plugin = self._task_manager.get_plugin(subtask.plugin_name)
        if plugin is None:
            return GTSourceType.API_ONLY

        if hasattr(plugin, 'get_gt_source'):
            return plugin.get_gt_source(subtask.validation_info)

        return GTSourceType.API_ONLY

    async def on_page_visit(
        self,
        url: str,
        content: str,
        api_data: Optional[Dict[str, Any]] = None,
    ):
        """
        Handle page visit - merge API data from cache and store page content.

        Args:
            url: The URL being visited
            content: Accessibility tree content (stored for GT extraction)
            api_data: Page-bound API data from cache
        """
        if not url or url == "about:blank":
            return

        # Store page content for GT extraction
        if content:
            self._page_contents[url] = content

        # For external pages, extract title from accessibility tree
        if api_data and api_data.get("is_external") and content:
            title = self._extract_title_from_content(content)
            if title:
                api_data["title"] = title

        # Merge API data and log in one step
        collected_info = self._merge_api_data(url, api_data) if api_data else None

        # Single-line log: URL + collection info
        url_short = url.split("//")[-1][:50]
        if collected_info:
            log("GT", f"Visit {url_short} → {collected_info}")
        # Skip logging for pages without API data (navigation pages)

        # Track visited URLs
        for subtask in self.subtasks:
            self._visited_urls[subtask.answer_tag].append(url)

    def _extract_title_from_content(self, content: str) -> Optional[str]:
        """
        Extract page title from accessibility tree or page content.

        Handles multiple formats:
        1. Accessibility tree: WebArea "Page Title" or RootWebArea "Page Title"
        2. Accessibility tree: heading "Title"
        3. Plain text: Look for lines that look like article titles

        Args:
            content: Accessibility tree or page content string

        Returns:
            Extracted title or None
        """
        import re

        if not content:
            return None

        # Try to find WebArea with title (first line usually)
        # Format: WebArea "Page Title" or RootWebArea "Page Title"
        match = re.search(r'(?:Root)?WebArea\s+"([^"]+)"', content)
        if match:
            title = match.group(1).strip()
            # Remove common site suffixes (e.g., "Title | GitHub", "Title - Site")
            title = re.sub(r'\s*[|\-–—]\s*[^|\-–—]+$', '', title).strip()
            if title:
                return title

        # Try to find document role with name
        match = re.search(r'document\s+"([^"]+)"', content, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            title = re.sub(r'\s*[|\-–—]\s*[^|\-–—]+$', '', title).strip()
            if title:
                return title

        # Try to find heading role with title
        match = re.search(r'heading\s+"([^"]+)"', content, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            title = re.sub(r'\s*[|\-–—]\s*[^|\-–—]+$', '', title).strip()
            if title and len(title) >= 5:
                return title

        # Fallback for plain text content: look for article title patterns
        # Scan lines and score them by how likely they are to be titles
        lines = content.split('\n')
        candidates = []

        for i, line in enumerate(lines[:25]):  # Check first 25 lines
            line = line.strip()

            # Skip very short or very long lines
            if len(line) < 15 or len(line) > 250:
                continue
            # Skip lines that look like navigation/metadata
            if re.match(r'^(home|about|contact|login|sign|menu|nav|©|copyright|\d{1,2}[/-]\d{1,2}|rss)', line.lower()):
                continue
            # Skip lines that look like domain names or URLs
            if re.match(r'^[\w.-]+\.(com|org|net|io|dev|co|blog|site|app|ai)(/.*)?$', line.lower()):
                continue
            # Skip lines that look like code/technical content
            if re.match(r'^[0-9A-Fa-f\s?*]+$', line):
                continue
            # Skip lines that are just dates
            if re.match(r'^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[\s\d,]+\d{4}$', line.lower()):
                continue

            # Clean up the line
            title = re.sub(r'\s*[|\-–—]\s*[^|\-–—]+$', '', line).strip()
            if not title or len(title) < 15:
                continue

            # Score the candidate
            score = 0
            words = title.split()

            # Prefer longer lines (more likely to be article titles)
            if len(title) >= 30:
                score += 3
            elif len(title) >= 20:
                score += 2
            else:
                score += 1

            # Prefer lines with more words (3+ words is typical for titles)
            if len(words) >= 5:
                score += 3
            elif len(words) >= 4:
                score += 2
            elif len(words) >= 3:
                score += 1
            else:
                continue  # Skip lines with fewer than 3 words

            # Lines with quotes often are article titles
            if '"' in title or '"' in title or '"' in title or "'" in title:
                score += 2

            # Lines appearing later (past header/nav) are more likely to be content titles
            if i >= 3:
                score += 1

            candidates.append((score, i, title))

        # Return the highest scoring candidate
        if candidates:
            candidates.sort(key=lambda x: (-x[0], x[1]))  # Highest score first, then earliest
            return candidates[0][2]

        return None

    def _merge_api_data(self, url: str, api_data: Dict[str, Any]) -> Optional[str]:
        """
        Merge API data from page cache into collected data.

        Rules:
        - Homepage bulk data: Only add NEW assets, don't overwrite existing
        - Detail page data: Always overwrite (more accurate/recent)

        Returns:
            Description of what was collected, or None
        """
        # Validate api_data type - must be dict, never silently ignore bad data
        if not isinstance(api_data, dict):
            raise TypeError(f"api_data is {type(api_data).__name__}, expected dict for {url[:80]}")

        url_lower = url.lower()

        if "coingecko.com" in url_lower:
            if "coins" in api_data:
                # Homepage: bulk coins - only add new, don't overwrite
                added = 0
                for coin_id, data in api_data["coins"].items():
                    if coin_id not in self._collected_api_data:
                        self._collected_api_data[coin_id] = data
                        added += 1
                if added > 0:
                    return f"+{added} coins (total {len(self._collected_api_data)})"
                return f"0 new (already have {len(api_data['coins'])} coins)"
            elif "id" in api_data:
                # Detail page: always overwrite (more accurate)
                coin_id = api_data["id"]
                self._collected_api_data[coin_id] = api_data
                return coin_id

        elif "stooq.com" in url_lower:
            if "assets" in api_data:
                # Homepage: bulk assets - only add new, don't overwrite
                added = 0
                for symbol, data in api_data["assets"].items():
                    if symbol not in self._collected_api_data:
                        self._collected_api_data[symbol] = data
                        added += 1
                if added > 0:
                    return f"+{added} assets (total {len(self._collected_api_data)})"
                return f"0 new (already have {len(api_data['assets'])} assets)"
            elif "symbol" in api_data:
                # Detail page: always overwrite (more accurate)
                symbol = api_data["symbol"]
                self._collected_api_data[symbol] = api_data
                return symbol

        elif "wttr.in" in url_lower or "weather" in url_lower:
            # Extract location from api_data["location"], URL path, or nearest_area
            location = api_data.get("location")
            if not location:
                # Try to extract from URL path (e.g., wttr.in/Hong+Kong)
                from urllib.parse import urlparse, unquote
                parsed = urlparse(url)
                path = unquote(parsed.path).strip('/')
                if path and not path.startswith('?'):
                    location = path.replace('+', ' ')
            if not location:
                # Try to extract from nearest_area
                nearest = api_data.get("nearest_area", [{}])
                if nearest and isinstance(nearest, list) and len(nearest) > 0:
                    area_name = nearest[0].get("areaName", [{}])
                    if area_name and isinstance(area_name, list) and len(area_name) > 0:
                        location = area_name[0].get("value", "")

            if location and ("weather" in api_data or "current_condition" in api_data):
                self._collected_api_data[location] = api_data
                return f"weather[{location}]"

        elif "taostats" in url_lower:
            # Homepage/list page: {"subnets": {...}} — add new only, never overwrite
            if "subnets" in api_data:
                subnets = api_data["subnets"]
                if "taostats" not in self._collected_api_data:
                    self._collected_api_data["taostats"] = {"subnets": {}}
                existing = self._collected_api_data["taostats"].setdefault("subnets", {})
                added = 0
                for netuid, data in subnets.items():
                    if netuid not in existing:
                        existing[netuid] = data
                        added += 1
                return f"+{added} subnets (total {len(existing)})"
            # Detail page: {"netuid": ..., "name": ..., ...} — always overwrite
            elif "netuid" in api_data:
                netuid = str(api_data["netuid"])
                if "taostats" not in self._collected_api_data:
                    self._collected_api_data["taostats"] = {"subnets": {}}
                self._collected_api_data["taostats"]["subnets"][netuid] = api_data
                name = api_data.get("name", f"SN{netuid}")
                return f"subnet[{name}]"

        elif "news.ycombinator.com" in url_lower:
            if "stories" in api_data:
                # Check if this is a category page (ask, show, jobs) or homepage
                category = api_data.get("category")  # "ask", "show", "jobs", or None
                if category is not None:
                    # Category page: store under category key ONLY.
                    # Do NOT store as individual entries — category-internal ranks
                    # (rank 1 in Ask != rank 1 on homepage) would contaminate
                    # homepage-based templates (extrema, multi_condition, summary).
                    category_key = f"hn_category:{category}"
                    self._collected_api_data[category_key] = api_data
                    return f"+{len(api_data['stories'])} {category} stories"
                else:
                    # Homepage: store individual stories
                    added = 0
                    for story_id, data in api_data["stories"].items():
                        if story_id not in self._collected_api_data:
                            self._collected_api_data[story_id] = data
                            added += 1
                    if added > 0:
                        return f"+{added} stories"
                    return None
            elif "id" in api_data and "title" in api_data:
                # Story detail page: merge with existing data, preserving rank
                story_id = str(api_data["id"])
                existing = self._collected_api_data.get(story_id, {})
                # Copy to avoid mutating cached/shared api_data reference
                merged = dict(api_data)
                if "rank" in existing and "rank" not in merged:
                    merged["rank"] = existing["rank"]
                self._collected_api_data[story_id] = merged
                return f"story[{story_id}]"
            elif "user" in api_data:
                # User page
                username = api_data["user"].get("id", "unknown")
                self._collected_api_data[f"user:{username}"] = api_data
                return f"user[{username}]"

        elif "open-meteo.com" in url_lower:
            # Each page visit has a unique location via _location_key
            loc_key = api_data.get("_location_key")
            if loc_key and "current_weather" in api_data:
                store_key = f"openmeteo:{loc_key}"
                self._collected_api_data[store_key] = api_data
                temp = api_data["current_weather"].get("temperature", "?")
                return f"weather[{loc_key}] {temp}°C"

        elif "openlibrary.org" in url_lower:
            if "works" in api_data and isinstance(api_data["works"], dict):
                # Search or subject page: store under URL key
                # Templates iterate collected data looking for "works" dict
                url_key = f"ol:{url_lower}"
                self._collected_api_data[url_key] = api_data
                count = len(api_data["works"])
                subject = api_data.get("subject")
                if subject:
                    return f"+{count} works (subject: {subject})"
                return f"+{count} works (search)"
            elif "key" in api_data and "title" in api_data:
                # Work detail page
                work_key = api_data["key"]
                self._collected_api_data[f"ol:{work_key}"] = api_data
                return f"work[{api_data['title'][:30]}]"

        # Handle external pages (from HN story links)
        if api_data.get("is_external"):
            external_url = api_data.get("url", url)
            title = api_data.get("title")
            hn_story_rank = api_data.get("hn_story_rank")
            if title:
                # Store under external:{url} key
                self._collected_api_data[f"external:{external_url}"] = api_data
                # Also store under hn_external:{rank} for easy template lookup
                if hn_story_rank:
                    self._collected_api_data[f"hn_external:{hn_story_rank}"] = api_data
                return f"external[{title[:30]}...]"

        return None

    def get_collected_api_data(self) -> Dict[str, Dict[str, Any]]:
        """Get all collected API data from page visits."""
        return self._collected_api_data

    async def _fetch_api_gt(self, subtask: "SubTask"):
        """Fetch GT from API for a subtask."""
        tag = subtask.answer_tag

        if self._task_manager is None:
            return

        plugin = self._task_manager.get_plugin(subtask.plugin_name)
        if plugin is None:
            return

        try:
            result = await plugin.get_ground_truth(subtask.validation_info)

            from liveweb_arena.core.ground_truth_trigger import GroundTruthResult, GTFailureType
            if isinstance(result, GroundTruthResult):
                if result.success:
                    if result.value is not None:
                        self._api_results[tag] = result.value
                        val_str = str(result.value)[:60]
                        log("GT", f"[{tag}] = {val_str}{'...' if len(str(result.value)) > 60 else ''}")
                    else:
                        self._gt_failures[tag] = GroundTruthResult.system_error(
                            f"success=True but value is None"
                        )
                        log("GT", f"[{tag}] FAILED: success=True but value is None")
                else:
                    # Store the full result to preserve failure_type
                    self._gt_failures[tag] = result
                    log("GT", f"[{tag}] FAILED: {result.error}")
            else:
                if result is not None:
                    self._api_results[tag] = result
                    val_str = str(result)[:60]
                    log("GT", f"[{tag}] = {val_str}{'...' if len(str(result)) > 60 else ''}")
                else:
                    self._gt_failures[tag] = GroundTruthResult.not_collected(
                        f"returned None"
                    )
                    log("GT", f"[{tag}] FAILED: returned None")

        except Exception as e:
            # Exception during GT fetch is a system error
            from liveweb_arena.core.ground_truth_trigger import GroundTruthResult
            self._gt_failures[tag] = GroundTruthResult.system_error(str(e))
            logger.error(f"GT fetch failed for {tag}: {e}")
            raise

    async def fetch_remaining_api_gt(self):
        """Fetch API GT for all templates (PAGE_ONLY, API_ONLY, and HYBRID all use collected API data)."""
        for subtask in self.subtasks:
            tag = subtask.answer_tag
            if tag not in self._api_results:
                await self._fetch_api_gt(subtask)

    def get_gt_for_subtask(self, subtask: "SubTask") -> Optional[str]:
        """Get GT value for a subtask."""
        tag = subtask.answer_tag
        source_type = self._get_source_type(subtask)

        # All source types now use API results
        return self._api_results.get(tag)

    def get_failure_reason(self, subtask: "SubTask") -> str:
        """Get detailed reason why GT collection failed."""
        tag = subtask.answer_tag
        visited = self._visited_urls.get(tag, [])

        # Check if we have stored failure information
        if tag in self._gt_failures:
            return self._gt_failures[tag].error or "Unknown failure"

        if tag in self._api_results:
            # Tag exists but value is falsy (None, "", etc.)
            value = self._api_results[tag]
            return f"API returned invalid data: {repr(value)}"

        collected = list(self._collected_api_data.keys())[:5]
        if collected:
            return f"API GT not fetched. Collected data: {collected}. Visited: {visited[:3]}"
        return f"No API data collected. Visited: {visited[:3]}"

    def get_failure_result(self, subtask: "SubTask") -> Optional["GroundTruthResult"]:
        """
        Get the full GroundTruthResult for a failed subtask.

        Returns None if the subtask succeeded or has no stored failure info.
        """
        tag = subtask.answer_tag
        return self._gt_failures.get(tag)

    def is_system_error(self, subtask: "SubTask") -> bool:
        """
        Check if the GT failure for this subtask is a system error.

        System errors indicate invalid evaluations (network, parsing, template bugs).
        Returns False if the subtask succeeded or failure is due to data not collected.
        """
        from liveweb_arena.core.ground_truth_trigger import GTFailureType

        tag = subtask.answer_tag
        if tag in self._gt_failures:
            result = self._gt_failures[tag]
            return result.failure_type == GTFailureType.SYSTEM_ERROR
        return False

    def get_stats(self) -> Dict[str, Any]:
        """Get collection statistics."""
        stats = {
            "total_subtasks": len(self.subtasks),
            "api_fetches": len(self._api_results),
            "collected_assets": len(self._collected_api_data),
        }

        by_type = {t: 0 for t in GTSourceType}
        for subtask in self.subtasks:
            source_type = self._get_source_type(subtask)
            by_type[source_type] += 1

        stats["by_source_type"] = {t.value: c for t, c in by_type.items()}
        return stats

    def cleanup(self):
        """
        Release memory by clearing internal data structures.

        Call this when the evaluation is complete to prevent memory leaks.
        The collector should not be used after calling this method.
        """
        self._api_results.clear()
        self._gt_failures.clear()
        self._visited_urls.clear()
        self._collected_api_data.clear()
        self._page_contents.clear()
        self.subtasks = []
        self._task_manager = None
