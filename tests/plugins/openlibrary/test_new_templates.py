"""Tests for Open Library issue #4 templates after red-team adjustments.

Covers:
1. Template registration and generation invariants
2. book_comparison GT behavior and position-bias safeguards
3. author_editions author-filter semantics and GT behavior
4. Task registry wiring (IDs 82 and 84 only)
5. Shared helper behavior and serialization guarantees
"""

import asyncio
from typing import Any, Dict, List

import pytest

from liveweb_arena.core.gt_collector import GTSourceType, set_current_gt_collector
from liveweb_arena.core.task_registry import TaskRegistry
from liveweb_arena.core.validators.base import get_registered_templates
from liveweb_arena.plugins.openlibrary.templates.author_editions import (
    AUTHOR_POOL,
    OpenLibraryAuthorEditionsTemplate,
)
from liveweb_arena.plugins.openlibrary.templates.book_comparison import (
    ComparisonMetric,
    OpenLibraryBookComparisonTemplate,
)
from liveweb_arena.plugins.openlibrary.templates.book_stats import (
    BOOK_POOL as STATS_BOOK_POOL,
)
from liveweb_arena.plugins.openlibrary.templates.book_comparison import (
    BOOK_POOL as COMP_BOOK_POOL,
)


class _DummyCollector:
    def __init__(self, data: Dict[str, Dict[str, Any]]):
        self._data = data

    def get_collected_api_data(self) -> Dict[str, Dict[str, Any]]:
        return self._data


def _run_gt(data: Dict[str, Dict[str, Any]], coro):
    set_current_gt_collector(_DummyCollector(data))
    try:
        return asyncio.run(coro)
    finally:
        set_current_gt_collector(None)


def _make_search_entry(
    query: str, sort: str, works: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "query": query,
        "sort": sort,
        "works": {work["key"]: work for work in works},
    }


# ── 1. Template registration ──────────────────────────────────────────


@pytest.mark.parametrize("name", [
    "openlibrary_book_comparison",
    "openlibrary_author_editions",
])
def test_template_registered(name):
    templates = get_registered_templates()
    assert name in templates, f"template '{name}' not registered"


def test_search_ranking_template_not_registered():
    templates = get_registered_templates()
    assert "openlibrary_search_ranking" not in templates


# ── 2. Question generation invariants ─────────────────────────────────


SEEDS = [1, 42, 100, 999, 12345]


@pytest.mark.parametrize("seed", SEEDS)
def test_book_comparison_generate(seed):
    q = OpenLibraryBookComparisonTemplate().generate(seed)
    assert q.question_text
    assert "openlibrary.org" in q.start_url
    assert q.template_name == "openlibrary_book_comparison"
    assert "metric" in q.validation_info
    assert "book_a" in q.validation_info
    assert "book_b" in q.validation_info
    assert "book_a_query" in q.validation_info
    assert "book_b_query" in q.validation_info
    assert q.validation_info["book_a"] != q.validation_info["book_b"]
    assert q.validation_info["metric"] in {
        "ratings_count",
        "want_to_read_count",
        "already_read_count",
    }


@pytest.mark.parametrize("seed", SEEDS)
def test_author_editions_generate(seed):
    q = OpenLibraryAuthorEditionsTemplate().generate(seed)
    assert q.question_text
    assert "openlibrary.org" in q.start_url
    assert q.template_name == "openlibrary_author_editions"
    assert "author_name" in q.validation_info
    assert "author_query" in q.validation_info
    assert "search_query" in q.validation_info
    assert q.validation_info["search_query"].startswith('author:"')
    assert "q=author%3A%22" in q.start_url
    assert "sort=editions" in q.start_url


def test_book_comparison_metrics_do_not_include_edition_count():
    metric_names = {metric.value[0] for metric in ComparisonMetric}
    assert metric_names == {"ratings_count", "want_to_read_count", "already_read_count"}


# ── 3. book_comparison GT behavior ────────────────────────────────────


def test_book_comparison_distinct_books_all_seeds():
    tmpl = OpenLibraryBookComparisonTemplate()
    for seed in range(1, 30):
        q = tmpl.generate(seed)
        assert q.validation_info["book_a"] != q.validation_info["book_b"], (
            f"seed={seed}: same book selected twice"
        )


def test_book_comparison_position_swap_occurs():
    tmpl = OpenLibraryBookComparisonTemplate()
    pairs = set()
    for seed in range(1, 50):
        q = tmpl.generate(seed)
        pairs.add((q.validation_info["book_a"], q.validation_info["book_b"]))
    assert len(pairs) > 10, "Position bias: too few unique ordered pairs"


def test_book_comparison_picks_higher_metric():
    tmpl = OpenLibraryBookComparisonTemplate()
    collected = {
        "ol:search:a": _make_search_entry("poetry", "editions", [
            {"key": "/works/OL1W", "rank": 1, "title": "Pride and Prejudice", "ratings_count": 1200},
            {"key": "/works/OL2W", "rank": 2, "title": "Jane Eyre", "ratings_count": 900},
        ]),
    }
    result = _run_gt(collected, tmpl.get_ground_truth({
        "metric": "ratings_count", "book_a": "Pride and Prejudice", "book_b": "Jane Eyre",
    }))
    assert result.success is True
    assert result.value == "Pride and Prejudice"


def test_book_comparison_reverse_winner():
    tmpl = OpenLibraryBookComparisonTemplate()
    collected = {
        "ol:search:a": _make_search_entry("classics", "editions", [
            {"key": "/works/OL1W", "rank": 1, "title": "Fahrenheit 451", "want_to_read_count": 300},
            {"key": "/works/OL2W", "rank": 2, "title": "Dune", "want_to_read_count": 800},
        ]),
    }
    result = _run_gt(collected, tmpl.get_ground_truth({
        "metric": "want_to_read_count", "book_a": "Fahrenheit 451", "book_b": "Dune",
    }))
    assert result.success is True
    assert result.value == "Dune"


def test_book_comparison_tie_breaks_alphabetically():
    tmpl = OpenLibraryBookComparisonTemplate()
    collected = {
        "ol:search:a": _make_search_entry("classics", "editions", [
            {"key": "/works/OL3W", "rank": 1, "title": "Pride and Prejudice", "already_read_count": 1000},
            {"key": "/works/OL4W", "rank": 2, "title": "Jane Eyre", "already_read_count": 1000},
        ]),
    }
    result = _run_gt(collected, tmpl.get_ground_truth({
        "metric": "already_read_count", "book_a": "Pride and Prejudice", "book_b": "Jane Eyre",
    }))
    assert result.success is True
    assert result.value == "Jane Eyre"  # alphabetically earlier


def test_book_comparison_not_collected_missing_book():
    tmpl = OpenLibraryBookComparisonTemplate()
    collected = {
        "ol:search:a": _make_search_entry("classics", "editions", [
            {"key": "/works/OL1W", "rank": 1, "title": "Fahrenheit 451", "ratings_count": 300},
        ]),
    }
    result = _run_gt(collected, tmpl.get_ground_truth({
        "metric": "ratings_count", "book_a": "Fahrenheit 451", "book_b": "Nonexistent Book",
    }))
    assert result.success is False
    assert result.is_data_not_collected()


def test_book_comparison_no_collected_data():
    tmpl = OpenLibraryBookComparisonTemplate()
    result = _run_gt({}, tmpl.get_ground_truth({
        "metric": "ratings_count", "book_a": "X", "book_b": "Y",
    }))
    assert result.success is False


def test_book_comparison_string_metric_value():
    tmpl = OpenLibraryBookComparisonTemplate()
    collected = {
        "ol:search:a": _make_search_entry("q", "e", [
            {"key": "/works/OL1W", "rank": 1, "title": "Book A", "already_read_count": "1,200"},
            {"key": "/works/OL2W", "rank": 2, "title": "Book B", "already_read_count": "900"},
        ]),
    }
    result = _run_gt(collected, tmpl.get_ground_truth({
        "metric": "already_read_count", "book_a": "Book A", "book_b": "Book B",
    }))
    assert result.success is True
    assert result.value == "Book A"


# ── 4. author_editions GT behavior ────────────────────────────────────


def test_author_editions_sums_first_n_results():
    tmpl = OpenLibraryAuthorEditionsTemplate()
    collected = {
        "ol:search:dickens": _make_search_entry('author:"charles dickens"', "editions", [
            {"key": "/works/OL10W", "rank": 1, "title": "A Tale of Two Cities", "edition_count": 100},
            {"key": "/works/OL11W", "rank": 2, "title": "Oliver Twist", "edition_count": 200},
            {"key": "/works/OL12W", "rank": 3, "title": "Great Expectations", "edition_count": 300},
        ]),
    }
    result = _run_gt(collected, tmpl.get_ground_truth({
        "author_name": "Charles Dickens", "author_query": "charles dickens",
        "search_query": 'author:"charles dickens"',
        "sort": "editions", "work_count": 2,
    }))
    assert result.success is True
    assert result.value == "300"


def test_author_editions_top_3():
    tmpl = OpenLibraryAuthorEditionsTemplate()
    collected = {
        "ol:search:dickens": _make_search_entry('author:"charles dickens"', "editions", [
            {"key": "/works/OL10W", "rank": 1, "title": "A", "edition_count": 1000},
            {"key": "/works/OL11W", "rank": 2, "title": "B", "edition_count": 900},
            {"key": "/works/OL12W", "rank": 3, "title": "C", "edition_count": 800},
            {"key": "/works/OL13W", "rank": 4, "title": "D", "edition_count": 700},
        ]),
    }
    result = _run_gt(collected, tmpl.get_ground_truth({
        "author_name": "Charles Dickens", "author_query": "charles dickens",
        "search_query": 'author:"charles dickens"',
        "sort": "editions", "work_count": 3,
    }))
    assert result.success is True
    assert result.value == "2700"


def test_author_editions_matches_author_filter_query_with_case_and_spacing():
    tmpl = OpenLibraryAuthorEditionsTemplate()
    collected = {
        "ol:search:twain": _make_search_entry('AUTHOR: "Mark Twain"', "editions", [
            {"key": "/works/OL20W", "rank": 1, "title": "Huck Finn", "edition_count": 400},
            {"key": "/works/OL21W", "rank": 2, "title": "Tom Sawyer", "edition_count": 200},
        ]),
    }
    result = _run_gt(collected, tmpl.get_ground_truth({
        "author_name": "Mark Twain", "author_query": "mark twain",
        "search_query": 'author:"mark twain"',
        "sort": "editions", "work_count": 2,
    }))
    assert result.success is True
    assert result.value == "600"


def test_author_editions_matches_punctuated_author_filter():
    tmpl = OpenLibraryAuthorEditionsTemplate()
    collected = {
        "ol:search:wells": _make_search_entry('author:"h.g. wells"', "editions", [
            {"key": "/works/OL30W", "rank": 1, "title": "War of the Worlds", "edition_count": 600},
            {"key": "/works/OL31W", "rank": 2, "title": "Time Machine", "edition_count": 400},
        ]),
    }
    result = _run_gt(collected, tmpl.get_ground_truth({
        "author_name": "H. G. Wells", "author_query": "h g wells",
        "search_query": 'author:"h g wells"',
        "sort": "editions", "work_count": 2,
    }))
    assert result.success is True
    assert result.value == "1000"


def test_author_editions_rejects_plain_text_query_without_author_filter():
    tmpl = OpenLibraryAuthorEditionsTemplate()
    collected = {
        "ol:search:wells": _make_search_entry("mark twain", "editions", [
            {"key": "/works/OL30W", "rank": 1, "title": "X", "edition_count": 600},
            {"key": "/works/OL31W", "rank": 2, "title": "Y", "edition_count": 400},
        ]),
    }
    result = _run_gt(collected, tmpl.get_ground_truth({
        "author_name": "Mark Twain", "author_query": "mark twain",
        "search_query": 'author:"mark twain"',
        "sort": "editions", "work_count": 2,
    }))
    assert result.success is False
    assert result.is_data_not_collected()


def test_author_editions_not_collected_wrong_author():
    tmpl = OpenLibraryAuthorEditionsTemplate()
    collected = {
        "ol:search:dickens": _make_search_entry('author:"charles dickens"', "editions", [
            {"key": "/works/OL10W", "rank": 1, "title": "X", "edition_count": 100},
        ]),
    }
    result = _run_gt(collected, tmpl.get_ground_truth({
        "author_name": "Unknown Author", "author_query": "unknown author",
        "search_query": 'author:"unknown author"',
        "sort": "editions", "work_count": 3,
    }))
    assert result.success is False
    assert result.is_data_not_collected()


def test_author_editions_missing_edition_count():
    tmpl = OpenLibraryAuthorEditionsTemplate()
    collected = {
        "ol:search:dickens": _make_search_entry('author:"charles dickens"', "editions", [
            {"key": "/works/OL10W", "rank": 1, "title": "A", "edition_count": 100},
            {"key": "/works/OL11W", "rank": 2, "title": "B"},
        ]),
    }
    result = _run_gt(collected, tmpl.get_ground_truth({
        "author_name": "Charles Dickens", "author_query": "charles dickens",
        "search_query": 'author:"charles dickens"',
        "sort": "editions", "work_count": 2,
    }))
    assert result.success is False


# ── 5. Task registry ──────────────────────────────────────────────────


def test_task_registry_template_ids():
    assert TaskRegistry.TEMPLATES[82] == ("openlibrary", "openlibrary_book_comparison")
    assert 83 not in TaskRegistry.TEMPLATES
    assert TaskRegistry.TEMPLATES[84] == ("openlibrary", "openlibrary_author_editions")


def test_task_registry_version_entry():
    found = any(sorted(v) == [82, 84] for v in TaskRegistry.TEMPLATE_VERSIONS)
    assert found, "No TEMPLATE_VERSIONS entry for [82, 84]"


def test_task_registry_stats():
    stats = TaskRegistry.get_stats()
    assert stats["num_templates"] >= 47
    assert stats["num_combinations"] > 0


# ── 6. Cross-template consistency ─────────────────────────────────────


def test_book_comparison_reuses_book_stats_pool():
    assert COMP_BOOK_POOL is STATS_BOOK_POOL


@pytest.mark.parametrize("cls", [
    OpenLibraryBookComparisonTemplate,
    OpenLibraryAuthorEditionsTemplate,
])
def test_gt_source_is_page_only(cls):
    assert cls().get_gt_source() == GTSourceType.PAGE_ONLY


@pytest.mark.parametrize("cls", [
    OpenLibraryBookComparisonTemplate,
    OpenLibraryAuthorEditionsTemplate,
])
def test_cache_source_is_openlibrary(cls):
    assert cls.get_cache_source() == "openlibrary"


def test_author_pool_size():
    assert len(AUTHOR_POOL) >= 20


def test_titles_match_rejects_short_substring():
    from liveweb_arena.plugins.openlibrary.templates.common import titles_match
    assert not titles_match("The Road", "On the Road")
    assert not titles_match("On the Road", "The Road")


def test_titles_match_accepts_close_length_variants():
    from liveweb_arena.plugins.openlibrary.templates.common import titles_match
    assert titles_match("Fahrenheit 451", "Fahrenheit 451")
    assert not titles_match("Fahrenheit 451", "Fahrenheit 451 A Novel")
    assert titles_match("Catch-22", "Catch 22")


def test_all_validation_info_values_are_serializable():
    templates = [
        OpenLibraryBookComparisonTemplate(),
        OpenLibraryAuthorEditionsTemplate(),
    ]
    for tmpl in templates:
        q = tmpl.generate(seed=1)
        for key, val in q.validation_info.items():
            assert isinstance(val, (str, int, float, bool, type(None))), (
                f"{tmpl.name}.validation_info['{key}'] = {type(val).__name__} "
                f"(not JSON-serializable)"
            )
