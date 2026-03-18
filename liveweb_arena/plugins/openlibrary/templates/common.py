"""Shared helpers for Open Library templates."""

from typing import Any, Dict, Iterator, Optional

from liveweb_arena.core.gt_collector import get_current_gt_collector


def normalize_text(value: str) -> str:
    """Normalize text for robust matching.

    Hyphens are converted to spaces so 'Catch-22' and 'Catch 22' normalize
    identically.
    """
    spaced = value.replace("-", " ")
    collapsed = " ".join(spaced.split())
    return "".join(ch.lower() for ch in collapsed if ch.isalnum() or ch == " ").strip()


def titles_match(expected: str, actual: str) -> bool:
    """Fuzzy title comparison resilient to punctuation and casing.

    Uses a length-ratio guard for substring matching: the shorter normalized
    string must be at least 85% of the longer one to qualify as a match.
    This prevents 'the road' from matching 'on the road'.
    """
    lhs = normalize_text(expected)
    rhs = normalize_text(actual)
    if not lhs or not rhs:
        return False
    if lhs == rhs:
        return True
    shorter, longer = (lhs, rhs) if len(lhs) <= len(rhs) else (rhs, lhs)
    if shorter not in longer:
        return False
    return len(shorter) / len(longer) >= 0.85


def parse_numeric(value: Any) -> Optional[float]:
    """Convert API values to float; returns None for non-numeric values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def get_collected_data() -> Optional[Dict[str, Dict[str, Any]]]:
    """Get collected API data for PAGE_ONLY templates."""
    collector = get_current_gt_collector()
    if collector is None:
        return None
    return collector.get_collected_api_data()


def find_search_entry(
    collected: Dict[str, Dict[str, Any]],
    *,
    query: str,
    sort: str,
) -> Optional[Dict[str, Any]]:
    """
    Find collected Open Library search data for a specific query and sort.

    Returns the most recent matching entry if multiple pages were visited.
    """
    target_query = query.strip().lower()
    matched: Optional[Dict[str, Any]] = None
    for key, entry in collected.items():
        if not key.startswith("ol:") or not isinstance(entry, dict):
            continue
        works = entry.get("works")
        if not isinstance(works, dict):
            continue
        entry_query = str(entry.get("query", "")).strip().lower()
        entry_sort = entry.get("sort")
        if entry_query == target_query and entry_sort == sort:
            matched = entry
    return matched


def iter_collected_works(collected: Dict[str, Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    """Iterate work-level records from collected Open Library data."""
    for entry in collected.values():
        if not isinstance(entry, dict):
            continue
        works = entry.get("works")
        if isinstance(works, dict):
            for work in works.values():
                if isinstance(work, dict):
                    yield work
            continue

        if "key" in entry and "title" in entry:
            yield entry
