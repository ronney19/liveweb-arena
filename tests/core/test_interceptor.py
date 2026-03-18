"""Tests for CacheInterceptor — URL blocking, domain allowlisting, cache lookup, error management."""

import pytest

from liveweb_arena.core.cache import CachedPage, CacheFatalError, normalize_url
from liveweb_arena.core.interceptor import CacheInterceptor, InterceptorStats


def _page(url, html="<html></html>", api_data=None, need_api=False):
    return CachedPage(url=url, html=html, api_data=api_data or {}, fetched_at=1.0, need_api=need_api)


def _interceptor(cached=None, domains=None, blocked=None, url_validator=None, offline=False):
    return CacheInterceptor(
        cached_pages=cached or {},
        allowed_domains=domains or set(),
        blocked_patterns=blocked,
        url_validator=url_validator,
        offline=offline,
    )


# ── _should_block ──────────────────────────────────────────────────

class TestShouldBlock:
    def test_blocks_google_analytics(self):
        i = _interceptor()
        assert i._should_block("https://www.google-analytics.com/collect?v=1")

    def test_blocks_custom_pattern(self):
        i = _interceptor(blocked=["*/tracking/*"])
        assert i._should_block("https://example.com/tracking/pixel.gif")

    def test_allows_normal_url(self):
        i = _interceptor()
        assert not i._should_block("https://www.coingecko.com/en/coins/bitcoin")

    def test_blocks_facebook_pixel(self):
        i = _interceptor()
        assert i._should_block("https://www.facebook.com/tr?id=123")


# ── _is_domain_allowed ────────────────────────────────────────────

class TestDomainAllowed:
    def test_exact_match(self):
        i = _interceptor(domains={"coingecko.com"})
        assert i._is_domain_allowed("https://coingecko.com/en/coins")

    def test_subdomain_match(self):
        i = _interceptor(domains={"coingecko.com"})
        assert i._is_domain_allowed("https://www.coingecko.com/en/coins")

    def test_rejects_unallowed(self):
        i = _interceptor(domains={"coingecko.com"})
        assert not i._is_domain_allowed("https://evil.com/phish")

    def test_empty_domains_allows_all(self):
        i = _interceptor(domains=set())
        assert i._is_domain_allowed("https://anything.com/page")

    def test_url_validator_callback(self):
        validator = lambda url: "special.com" in url
        i = _interceptor(domains={"example.com"}, url_validator=validator)
        assert i._is_domain_allowed("https://special.com/page")
        assert not i._is_domain_allowed("https://other.com/page")

    def test_port_stripped(self):
        i = _interceptor(domains={"localhost"})
        assert i._is_domain_allowed("http://localhost:8080/api")


# ── _find_cached_page ─────────────────────────────────────────────

class TestFindCachedPage:
    def test_exact_hit(self):
        url = "https://www.coingecko.com/en/coins/bitcoin"
        page = _page(url, api_data={"price": 100})
        i = _interceptor(cached={normalize_url(url): page})
        assert i._find_cached_page(url) is page

    def test_www_variant_hit(self):
        """查找 www 版本时能找到非 www 缓存。"""
        url_no_www = "https://coingecko.com/en/coins/bitcoin"
        page = _page(url_no_www, api_data={"price": 100})
        i = _interceptor(cached={normalize_url(url_no_www): page})
        found = i._find_cached_page("https://www.coingecko.com/en/coins/bitcoin")
        assert found is page

    def test_miss_returns_none(self):
        i = _interceptor()
        assert i._find_cached_page("https://example.com/missing") is None

    def test_incomplete_page_skipped(self):
        """need_api=True 但没有 api_data 的页面在所有查找步骤中都被跳过。"""
        url = "https://coingecko.com/en/coins/bitcoin"
        page = CachedPage(url=url, html="<h1>BTC</h1>", api_data=None, fetched_at=1.0, need_api=True)
        i = _interceptor(cached={normalize_url(url): page})
        assert i._find_cached_page(url) is None


# ── InterceptorStats ───────────────────────────────────────────────

class TestStats:
    def test_to_dict(self):
        s = InterceptorStats(hits=8, misses=2, blocked=5, passed=3)
        d = s.to_dict()
        assert d["hits"] == 8
        assert d["total"] == 18
        assert d["hit_rate"] == 0.8

    def test_hit_rate_zero_division(self):
        s = InterceptorStats()
        d = s.to_dict()
        assert d["hit_rate"] == 0.0


# ── Error management ──────────────────────────────────────────────

class TestErrorManagement:
    def test_get_and_clear(self):
        i = _interceptor()
        i._pending_error = CacheFatalError("timeout")
        err = i.get_and_clear_error()
        assert isinstance(err, CacheFatalError)
        assert i._pending_error is None

    def test_raise_if_error(self):
        i = _interceptor()
        i._pending_error = CacheFatalError("fail", url="https://x.com")
        with pytest.raises(CacheFatalError, match="fail"):
            i.raise_if_error()
        # 清除后不再抛出
        i.raise_if_error()

    def test_raise_wraps_generic_exception(self):
        i = _interceptor()
        i._pending_error = ValueError("bad")
        with pytest.raises(CacheFatalError):
            i.raise_if_error("https://x.com")


# ── Accessibility tree cache ───────────────────────────────────────

class TestAccessibilityTreeCache:
    def test_stores_and_retrieves(self):
        url = "https://coingecko.com/en/coins/bitcoin"
        page = _page(url, api_data={"p": 1})
        page.accessibility_tree = "heading: Bitcoin"
        i = _interceptor(cached={normalize_url(url): page})
        # 触发 _find_cached_page 不会自动存 a11y tree，需要通过 _handle_document
        # 但 get_accessibility_tree 从 _accessibility_trees 字典读
        i._accessibility_trees[normalize_url(url)] = page.accessibility_tree
        assert i.get_accessibility_tree(url) == "heading: Bitcoin"

    def test_cleanup(self):
        i = _interceptor()
        i._accessibility_trees["url1"] = "tree"
        i.cleanup()
        assert len(i._accessibility_trees) == 0
        assert len(i.cached_pages) == 0
