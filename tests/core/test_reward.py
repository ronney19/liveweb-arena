"""Tests for StepwiseRewardCalculator — step rewards, terminal rewards, and URL helpers."""

import pytest

from liveweb_arena.core.reward import (
    RewardConfig,
    RewardSignal,
    StepwiseRewardCalculator,
    is_detail_page,
)


@pytest.fixture
def calc():
    return StepwiseRewardCalculator(
        target_assets={"bitcoin", "ethereum"},
        required_domains={"www.coingecko.com"},
    )


# ── is_detail_page ────────────────────────────────────────────────

class TestIsDetailPage:
    def test_coingecko_coin(self):
        assert is_detail_page("https://www.coingecko.com/en/coins/bitcoin")

    def test_coingecko_no_en(self):
        assert is_detail_page("https://www.coingecko.com/coins/ethereum")

    def test_coingecko_homepage(self):
        assert not is_detail_page("https://www.coingecko.com/")

    def test_stooq_query(self):
        assert is_detail_page("https://stooq.com/q/?s=aapl.us")

    def test_stooq_daily(self):
        assert is_detail_page("https://stooq.com/q/d/?s=msft.us")

    def test_taostats_subnet(self):
        assert is_detail_page("https://taostats.io/subnet/1")

    def test_taostats_subnets(self):
        assert is_detail_page("https://taostats.io/subnets/18")

    def test_about_blank(self):
        assert not is_detail_page("about:blank")


# ── _extract_asset_from_url ───────────────────────────────────────

class TestExtractAsset:
    def test_coingecko(self):
        calc = StepwiseRewardCalculator()
        assert calc._extract_asset_from_url("https://www.coingecko.com/en/coins/bitcoin") == "bitcoin"

    def test_stooq(self):
        calc = StepwiseRewardCalculator()
        assert calc._extract_asset_from_url("https://stooq.com/q/?s=aapl.us") == "aapl.us"

    def test_taostats(self):
        calc = StepwiseRewardCalculator()
        assert calc._extract_asset_from_url("https://taostats.io/subnet/18") == "18"

    def test_unknown(self):
        calc = StepwiseRewardCalculator()
        assert calc._extract_asset_from_url("https://example.com/page") is None


# ── calculate_step_reward: 基本信号 ──────────────────────────────

class TestStepReward:
    def test_parse_failed(self, calc):
        r = calc.calculate_step_reward("about:blank", "Success", set(), parse_failed=True)
        signals = {s[0] for s in r.signals}
        assert RewardSignal.PARSE_FAILED.value in signals
        assert r.total < 0

    def test_blocked_url(self, calc):
        r = calc.calculate_step_reward("https://evil.com", "Blocked", set(), is_blocked=True)
        signals = {s[0] for s in r.signals}
        assert RewardSignal.BLOCKED_URL.value in signals
        assert r.total < 0

    def test_new_domain(self, calc):
        r = calc.calculate_step_reward("https://www.coingecko.com/", "Success", set())
        signals = {s[0] for s in r.signals}
        assert RewardSignal.NEW_DOMAIN.value in signals
        assert r.total > 0

    def test_repeated_url_penalty(self, calc):
        calc.calculate_step_reward("https://www.coingecko.com/en/coins/bitcoin", "Success", {"bitcoin"})
        r2 = calc.calculate_step_reward("https://www.coingecko.com/en/coins/bitcoin", "Success", {"bitcoin"})
        signals = {s[0] for s in r2.signals}
        assert RewardSignal.REPEATED_URL.value in signals

    def test_new_asset(self, calc):
        r = calc.calculate_step_reward("https://www.coingecko.com/", "Success", {"bitcoin"})
        signals = {s[0] for s in r.signals}
        assert RewardSignal.NEW_ASSET.value in signals

    def test_target_asset(self, calc):
        r = calc.calculate_step_reward(
            "https://www.coingecko.com/en/coins/bitcoin", "Success", {"bitcoin"}
        )
        signals = {s[0] for s in r.signals}
        assert RewardSignal.TARGET_ASSET.value in signals

    def test_all_targets_bonus(self, calc):
        calc.calculate_step_reward("https://www.coingecko.com/en/coins/bitcoin", "Success", {"bitcoin"})
        r = calc.calculate_step_reward(
            "https://www.coingecko.com/en/coins/ethereum", "Success", {"bitcoin", "ethereum"}
        )
        signals = {s[0] for s in r.signals}
        assert RewardSignal.ALL_TARGETS.value in signals

    def test_detail_page_visit(self, calc):
        r = calc.calculate_step_reward(
            "https://www.coingecko.com/en/coins/bitcoin", "Success", {"bitcoin"}
        )
        signals = {s[0] for s in r.signals}
        assert RewardSignal.DETAIL_PAGE_VISIT.value in signals

    def test_no_progress_penalty(self, calc):
        # 第一次访问获得 new_domain，第二次同URL无进展
        calc.calculate_step_reward("https://www.coingecko.com/", "Success", set())
        r = calc.calculate_step_reward("https://www.coingecko.com/", "Success", set())
        signals = {s[0] for s in r.signals}
        assert RewardSignal.NO_PROGRESS.value in signals

    def test_action_failed(self, calc):
        r = calc.calculate_step_reward("https://www.coingecko.com/", "Failed: element not found", set())
        signals = {s[0] for s in r.signals}
        assert RewardSignal.ACTION_FAILED.value in signals

    def test_clamp_bounds(self, calc):
        """奖励应被限制在 [min_step_reward, max_step_reward] 范围内。"""
        r = calc.calculate_step_reward(
            "https://www.coingecko.com/en/coins/bitcoin", "Success", {"bitcoin", "ethereum"}
        )
        assert r.total <= calc.config.max_step_reward
        assert r.total >= calc.config.min_step_reward


# ── 累积上限 ──────────────────────────────────────────────────────

class TestCumulativeCap:
    def test_cumulative_cap_enforced(self):
        """连续获得正奖励最终应触发累积上限。"""
        config = RewardConfig(max_cumulative_step_reward=0.2)
        calc = StepwiseRewardCalculator(config=config, target_assets={"a", "b", "c", "d", "e"})

        total = 0.0
        for i in range(20):
            r = calc.calculate_step_reward(
                f"https://site{i}.com/asset{i}", "Success", {f"asset{i}"}
            )
            total += r.total

        assert total <= config.max_cumulative_step_reward + abs(config.min_step_reward) * 20


# ── calculate_terminal_reward ─────────────────────────────────────

class TestTerminalReward:
    def test_success(self):
        calc = StepwiseRewardCalculator()
        r = calc.calculate_terminal_reward(validation_score=1.0, steps_used=5, max_steps=30, truncated=False)
        signals = {s[0] for s in r.signals}
        assert RewardSignal.TASK_SUCCESS.value in signals
        assert RewardSignal.EARLY_COMPLETION.value in signals
        assert r.total > 0

    def test_partial(self):
        calc = StepwiseRewardCalculator()
        r = calc.calculate_terminal_reward(validation_score=0.5, steps_used=20, max_steps=30, truncated=False)
        signals = {s[0] for s in r.signals}
        assert RewardSignal.TASK_PARTIAL.value in signals
        assert r.total > 0

    def test_truncated(self):
        calc = StepwiseRewardCalculator()
        r = calc.calculate_terminal_reward(validation_score=0.0, steps_used=30, max_steps=30, truncated=True)
        signals = {s[0] for s in r.signals}
        assert RewardSignal.MAX_STEPS.value in signals
        assert r.total < 0

    def test_early_completion_only_on_success(self):
        calc = StepwiseRewardCalculator()
        r = calc.calculate_terminal_reward(validation_score=0.5, steps_used=3, max_steps=30, truncated=False)
        signals = {s[0] for s in r.signals}
        assert RewardSignal.EARLY_COMPLETION.value not in signals

    def test_no_early_bonus_if_many_steps(self):
        calc = StepwiseRewardCalculator()
        r = calc.calculate_terminal_reward(validation_score=1.0, steps_used=25, max_steps=30, truncated=False)
        signals = {s[0] for s in r.signals}
        assert RewardSignal.EARLY_COMPLETION.value not in signals


# ── reset ─────────────────────────────────────────────────────────

class TestReset:
    def test_reset_clears_state(self, calc):
        calc.calculate_step_reward("https://www.coingecko.com/en/coins/bitcoin", "Success", {"bitcoin"})
        calc.reset()
        state = calc.get_state()
        assert state["visited_urls"] == 0
        assert state["collected_assets"] == 0
        assert state["cumulative_step_reward"] == 0.0
