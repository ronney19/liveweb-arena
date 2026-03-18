"""Tests for GTCollector — merge logic, title extraction, failure tracking, stats."""

import pytest

from liveweb_arena.core.gt_collector import GTCollector, GTSourceType, GTResult
from liveweb_arena.core.ground_truth_trigger import GroundTruthResult, GTFailureType


# ── Helpers ──────────────────────────────────────────────────────────

class FakeSubTask:
    """Minimal SubTask stand-in for testing."""
    def __init__(self, tag, plugin_name="test"):
        self.answer_tag = tag
        self.plugin_name = plugin_name
        self.intent = "test question"
        self.validation_info = {}
        self.expected_steps = 5


def _collector(tags=("answer1",)):
    subtasks = [FakeSubTask(t) for t in tags]
    return GTCollector(subtasks=subtasks, task_manager=None)


# ── _merge_api_data: CoinGecko ──────────────────────────────────────

class TestMergeCoinGecko:
    def test_homepage_adds_new_coins(self):
        c = _collector()
        result = c._merge_api_data(
            "https://www.coingecko.com/",
            {"coins": {"bitcoin": {"price": 100}, "ethereum": {"price": 50}}},
        )
        assert "+2 coins" in result
        assert "bitcoin" in c._collected_api_data
        assert "ethereum" in c._collected_api_data

    def test_homepage_skips_existing(self):
        c = _collector()
        c._collected_api_data["bitcoin"] = {"price": 99}
        result = c._merge_api_data(
            "https://www.coingecko.com/",
            {"coins": {"bitcoin": {"price": 100}, "ethereum": {"price": 50}}},
        )
        assert "+1 coins" in result
        # Existing bitcoin not overwritten
        assert c._collected_api_data["bitcoin"]["price"] == 99

    def test_detail_page_overwrites(self):
        c = _collector()
        c._collected_api_data["bitcoin"] = {"price": 99}
        result = c._merge_api_data(
            "https://www.coingecko.com/en/coins/bitcoin",
            {"id": "bitcoin", "price": 200},
        )
        assert result == "bitcoin"
        assert c._collected_api_data["bitcoin"]["price"] == 200


# ── _merge_api_data: Stooq ──────────────────────────────────────────

class TestMergeStooq:
    def test_homepage_adds_assets(self):
        c = _collector()
        result = c._merge_api_data(
            "https://stooq.com/",
            {"assets": {"aapl.us": {"close": 150}}},
        )
        assert "+1 assets" in result

    def test_detail_page_overwrites(self):
        c = _collector()
        c._collected_api_data["aapl.us"] = {"close": 140}
        result = c._merge_api_data(
            "https://stooq.com/q/?s=aapl.us",
            {"symbol": "aapl.us", "close": 155},
        )
        assert result == "aapl.us"
        assert c._collected_api_data["aapl.us"]["close"] == 155


# ── _merge_api_data: Taostats ───────────────────────────────────────

class TestMergeTaostats:
    def test_list_page_adds_subnets(self):
        c = _collector()
        result = c._merge_api_data(
            "https://taostats.io/subnets",
            {"subnets": {"1": {"name": "SN1"}, "2": {"name": "SN2"}}},
        )
        assert "+2 subnets" in result
        assert "taostats" in c._collected_api_data
        assert "1" in c._collected_api_data["taostats"]["subnets"]

    def test_list_page_skips_existing(self):
        c = _collector()
        c._collected_api_data["taostats"] = {"subnets": {"1": {"name": "OLD"}}}
        result = c._merge_api_data(
            "https://taostats.io/subnets",
            {"subnets": {"1": {"name": "NEW"}, "2": {"name": "SN2"}}},
        )
        assert "+1 subnets" in result
        assert c._collected_api_data["taostats"]["subnets"]["1"]["name"] == "OLD"

    def test_detail_page_overwrites(self):
        c = _collector()
        c._collected_api_data["taostats"] = {"subnets": {"1": {"name": "OLD"}}}
        result = c._merge_api_data(
            "https://taostats.io/subnet/1",
            {"netuid": 1, "name": "Alpha"},
        )
        assert "subnet[Alpha]" in result
        assert c._collected_api_data["taostats"]["subnets"]["1"]["name"] == "Alpha"


# ── _merge_api_data: HackerNews ─────────────────────────────────────

class TestMergeHackerNews:
    def test_homepage_adds_stories(self):
        c = _collector()
        result = c._merge_api_data(
            "https://news.ycombinator.com/",
            {"stories": {"101": {"title": "Story A"}, "102": {"title": "Story B"}}},
        )
        assert "+2 stories" in result
        assert "101" in c._collected_api_data

    def test_category_page_stored_under_category_key(self):
        c = _collector()
        result = c._merge_api_data(
            "https://news.ycombinator.com/ask",
            {"stories": {"201": {"title": "Ask HN"}}, "category": "ask"},
        )
        assert "ask stories" in result
        assert "hn_category:ask" in c._collected_api_data
        # Individual stories NOT added to top-level
        assert "201" not in c._collected_api_data

    def test_story_detail_preserves_rank(self):
        c = _collector()
        c._collected_api_data["999"] = {"title": "Old", "rank": 3}
        result = c._merge_api_data(
            "https://news.ycombinator.com/item?id=999",
            {"id": 999, "title": "Updated Story"},
        )
        assert "story[999]" in result
        assert c._collected_api_data["999"]["rank"] == 3
        assert c._collected_api_data["999"]["title"] == "Updated Story"

    def test_user_page(self):
        c = _collector()
        result = c._merge_api_data(
            "https://news.ycombinator.com/user?id=pg",
            {"user": {"id": "pg", "karma": 99999}},
        )
        assert "user[pg]" in result
        assert "user:pg" in c._collected_api_data


# ── _merge_api_data: OpenLibrary ────────────────────────────────────

class TestMergeOpenLibrary:
    def test_search_page(self):
        c = _collector()
        result = c._merge_api_data(
            "https://openlibrary.org/search?q=python",
            {"works": {"w1": {"title": "Learn Python"}, "w2": {"title": "Py Cookbook"}}, "subject": None},
        )
        assert "+2 works" in result

    def test_subject_page(self):
        c = _collector()
        result = c._merge_api_data(
            "https://openlibrary.org/subjects/science",
            {"works": {"w1": {"title": "Physics"}}, "subject": "science"},
        )
        assert "subject: science" in result

    def test_work_detail(self):
        c = _collector()
        result = c._merge_api_data(
            "https://openlibrary.org/works/OL12345W",
            {"key": "/works/OL12345W", "title": "A Great Book"},
        )
        assert "work[A Great Book]" in result


# ── _merge_api_data: external pages ─────────────────────────────────

class TestMergeExternal:
    def test_external_page_with_title(self):
        c = _collector()
        result = c._merge_api_data(
            "https://blog.example.com/post",
            {"is_external": True, "url": "https://blog.example.com/post", "title": "My Blog Post", "hn_story_rank": 5},
        )
        assert "external[" in result
        assert "external:https://blog.example.com/post" in c._collected_api_data
        assert "hn_external:5" in c._collected_api_data

    def test_external_page_no_rank(self):
        c = _collector()
        result = c._merge_api_data(
            "https://example.com/article",
            {"is_external": True, "url": "https://example.com/article", "title": "Article"},
        )
        assert "external[" in result
        assert "hn_external:" not in str(c._collected_api_data.keys())


# ── _merge_api_data: edge cases ─────────────────────────────────────

class TestMergeEdgeCases:
    def test_rejects_non_dict(self):
        c = _collector()
        with pytest.raises(TypeError, match="expected dict"):
            c._merge_api_data("https://example.com", ["not", "a", "dict"])

    def test_unknown_domain_returns_none(self):
        c = _collector()
        result = c._merge_api_data("https://unknown.com/page", {"data": 1})
        assert result is None
        assert len(c._collected_api_data) == 0


# ── _extract_title_from_content ──────────────────────────────────────

class TestExtractTitle:
    def test_web_area_title(self):
        c = _collector()
        content = 'RootWebArea "Breaking News - CNN"\n  heading "Top Story"'
        title = c._extract_title_from_content(content)
        assert title == "Breaking News"

    def test_document_role(self):
        c = _collector()
        content = 'document "Article Title | Medium"\n  paragraph "text"'
        title = c._extract_title_from_content(content)
        assert title == "Article Title"

    def test_heading_role(self):
        c = _collector()
        content = 'navigation "Menu"\n  heading "A Detailed Analysis of Markets"'
        title = c._extract_title_from_content(content)
        assert title == "A Detailed Analysis of Markets"

    def test_heading_too_short_skipped(self):
        c = _collector()
        content = 'heading "Hi"'
        # "Hi" is < 5 chars, heading fallback skips it; plain text fallback also skips < 15 chars
        title = c._extract_title_from_content(content)
        assert title is None

    def test_none_input(self):
        c = _collector()
        assert c._extract_title_from_content(None) is None

    def test_empty_input(self):
        c = _collector()
        assert c._extract_title_from_content("") is None


# ── on_page_visit (sync parts) ──────────────────────────────────────

class TestOnPageVisit:
    @pytest.mark.asyncio
    async def test_skips_about_blank(self):
        c = _collector()
        await c.on_page_visit("about:blank", "content")
        assert len(c._page_contents) == 0

    @pytest.mark.asyncio
    async def test_stores_content(self):
        c = _collector()
        await c.on_page_visit("https://example.com", "tree content", api_data=None)
        assert c._page_contents["https://example.com"] == "tree content"

    @pytest.mark.asyncio
    async def test_tracks_visited_urls(self):
        c = _collector(tags=("answer1", "answer2"))
        await c.on_page_visit("https://example.com", "c", api_data=None)
        assert "https://example.com" in c._visited_urls["answer1"]
        assert "https://example.com" in c._visited_urls["answer2"]


# ── get_gt_for_subtask / failure tracking ────────────────────────────

class TestGTRetrieval:
    def test_returns_api_result(self):
        c = _collector()
        c._api_results["answer1"] = "42"
        st = FakeSubTask("answer1")
        assert c.get_gt_for_subtask(st) == "42"

    def test_returns_none_when_missing(self):
        c = _collector()
        st = FakeSubTask("answer1")
        assert c.get_gt_for_subtask(st) is None

    def test_failure_reason_from_stored_result(self):
        c = _collector()
        c._gt_failures["answer1"] = GroundTruthResult.fail("No data collected")
        st = FakeSubTask("answer1")
        assert "No data collected" in c.get_failure_reason(st)

    def test_failure_reason_no_data(self):
        c = _collector()
        st = FakeSubTask("answer1")
        reason = c.get_failure_reason(st)
        assert "No API data collected" in reason

    def test_is_system_error_true(self):
        c = _collector()
        c._gt_failures["answer1"] = GroundTruthResult.system_error("Network timeout")
        st = FakeSubTask("answer1")
        assert c.is_system_error(st) is True

    def test_is_system_error_false_for_not_collected(self):
        c = _collector()
        c._gt_failures["answer1"] = GroundTruthResult.not_collected("Agent didn't visit")
        st = FakeSubTask("answer1")
        assert c.is_system_error(st) is False

    def test_is_system_error_false_when_no_failure(self):
        c = _collector()
        c._api_results["answer1"] = "ok"
        st = FakeSubTask("answer1")
        assert c.is_system_error(st) is False


# ── get_stats ────────────────────────────────────────────────────────

class TestStats:
    def test_basic_stats(self):
        c = _collector(tags=("a1", "a2"))
        c._api_results["a1"] = "val"
        c._collected_api_data["btc"] = {"price": 100}
        c._collected_api_data["eth"] = {"price": 50}
        stats = c.get_stats()
        assert stats["total_subtasks"] == 2
        assert stats["api_fetches"] == 1
        assert stats["collected_assets"] == 2


# ── cleanup ──────────────────────────────────────────────────────────

class TestCleanup:
    def test_cleanup_clears_all(self):
        c = _collector(tags=("a1",))
        c._api_results["a1"] = "val"
        c._collected_api_data["btc"] = {}
        c._page_contents["url"] = "tree"
        c.cleanup()
        assert len(c._api_results) == 0
        assert len(c._collected_api_data) == 0
        assert len(c._page_contents) == 0
        assert len(c.subtasks) == 0
        assert c._task_manager is None


# ── GTResult dataclass ───────────────────────────────────────────────

class TestGTResult:
    def test_success_property(self):
        r = GTResult(tag="a1", source_type=GTSourceType.PAGE_ONLY, value="42")
        assert r.success is True

    def test_failure_property(self):
        r = GTResult(tag="a1", source_type=GTSourceType.PAGE_ONLY, error="failed")
        assert r.success is False
