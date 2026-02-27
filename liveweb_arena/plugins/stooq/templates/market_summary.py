"""Market summary template for Stooq - open-ended analysis questions"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, TriggerConfig, GroundTruthResult,
)
from liveweb_arena.core.gt_collector import GTSourceType
from .variables import INDICES, US_STOCKS, parse_float


class MarketSummaryType(Enum):
    """Types of market summary questions"""
    US_INDICES = "us_indices"  # Summarize US market via indices
    TECH_STOCKS = "tech_stocks"  # Summarize tech sector
    MARKET_TREND = "market_trend"  # Analyze market direction


@register_template("stooq_market_summary")
class StooqMarketSummaryTemplate(QuestionTemplate):
    """
    Template for market summary questions requiring AI analysis.

    These questions don't have fixed expected answers. Instead:
    1. Ground truth provides actual market data (prices, changes)
    2. LLM validator judges if the answer correctly reflects the data

    Questions like:
    - "Summarize today's US market performance based on DJI, SPX, and NDX"
    - "Analyze the tech sector using AAPL, MSFT, NVDA, and GOOGL"
    - "Is the market trending up or down based on major indices?"
    """

    GT_SOURCE = GTSourceType.API_ONLY  # Multi-instrument aggregation

    PATTERNS = {
        MarketSummaryType.US_INDICES: [
            "Summarize today's US stock market performance based on DJI, SPX, and NDX values and their percentage changes. Is the market up or down overall?",
            "Analyze the US market based on the Dow Jones (^dji), S&P 500 (^spx), and NASDAQ 100 (^ndx). Are they mostly up or down? By how much?",
            "What is the overall direction of the US stock market today? Check the major indices (DJI, SPX, NDX) and summarize their performance.",
        ],
        MarketSummaryType.TECH_STOCKS: [
            "Analyze the tech sector's performance today based on AAPL, MSFT, NVDA, and GOOGL. Summarize which are gaining and which are losing.",
            "Check the major tech stocks (Apple, Microsoft, NVIDIA, Alphabet). How is the tech sector performing today?",
            "Summarize today's tech stock performance based on AAPL, MSFT, NVDA, and GOOGL. Are tech stocks mostly up or down?",
        ],
        MarketSummaryType.MARKET_TREND: [
            "Based on the major US indices (DJI, SPX, NDX), is the market in an uptrend or downtrend today? Provide the actual percentage changes.",
            "Check the DJI, SPX, and NDX. Determine the market trend and report the percentage change for each index.",
            "Analyze the current market trend based on DJI, SPX, and NDX. Is the market bullish or bearish today?",
        ],
    }

    # Symbols to check for each summary type
    SYMBOLS = {
        MarketSummaryType.US_INDICES: ["^dji", "^spx", "^ndx"],
        MarketSummaryType.TECH_STOCKS: ["aapl.us", "msft.us", "nvda.us", "googl.us"],
        MarketSummaryType.MARKET_TREND: ["^dji", "^spx", "^ndx"],
    }

    def __init__(self):
        super().__init__("stooq_market_summary")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """
        Generate a Stooq market summary question.

        Args:
            seed: Random seed for reproducible generation
            variant: Optional variant index for selecting summary type.
                     0=US_INDICES, 1=TECH_STOCKS, 2=MARKET_TREND
        """
        rng = random.Random(seed)

        # Select summary type (use variant if provided)
        summary_types_list = list(MarketSummaryType)
        if variant is not None:
            summary_type = summary_types_list[variant % len(summary_types_list)]
        else:
            summary_type = rng.choice(summary_types_list)

        # Build question
        patterns = self.PATTERNS[summary_type]
        pattern = rng.choice(patterns)
        question_text = pattern

        symbols = self.SYMBOLS[summary_type]

        validation_info = {
            "summary_type": summary_type.value,
            "symbols": symbols,
        }

        # 2 steps per symbol (goto + read) + buffer
        expected_steps = len(symbols) * 2 + 4

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://stooq.com/",
            variables={
                "summary_type": summary_type,
            },
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=expected_steps,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        summary_type = validation_info.get("summary_type", "us_indices")

        if summary_type == "us_indices":
            return """Task-Specific Rules (Market Summary - US Indices):
The ground truth contains actual data for DJI, SPX, and NDX including prices and percentage changes.
- Score 1.0: Answer correctly describes the overall market direction (up/down) AND includes reasonably accurate percentage changes (within 0.5% tolerance)
- Score 0.5: Answer correctly identifies market direction but percentage values are off by more than 0.5%
- Score 0.0: Answer incorrectly identifies market direction or provides completely wrong data

Key validation points:
1. If most indices are positive, answer should say market is UP
2. If most indices are negative, answer should say market is DOWN
3. Percentage changes should be approximately correct"""

        elif summary_type == "tech_stocks":
            return """Task-Specific Rules (Market Summary - Tech Stocks):
The ground truth contains actual data for AAPL, MSFT, NVDA, and GOOGL including prices and percentage changes.
- Score 1.0: Answer correctly summarizes which stocks are up/down AND sector direction
- Score 0.5: Answer partially correct (e.g., correct direction but wrong about specific stocks)
- Score 0.0: Answer fundamentally incorrect about sector performance

Key validation points:
1. Correctly identify which stocks are gaining vs losing
2. Correctly summarize overall tech sector direction
3. Percentage changes should be approximately correct"""

        else:  # MARKET_TREND
            return """Task-Specific Rules (Market Summary - Market Trend):
The ground truth contains actual data for major indices.
- Score 1.0: Answer correctly identifies the trend (uptrend/downtrend/mixed) with supporting data
- Score 0.5: Answer identifies correct trend but supporting data is incomplete
- Score 0.0: Answer identifies wrong trend direction

Key validation points:
1. Uptrend = most indices positive
2. Downtrend = most indices negative
3. Mixed = some up, some down significantly"""

    async def _fetch_instrument_data(self, symbol: str) -> GroundTruthResult:
        """Fetch data for a single instrument from collected API data (no network fallback)."""
        from liveweb_arena.core.gt_collector import get_current_gt_collector
        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.fail("No GT collector")

        collected = gt_collector.get_collected_api_data()
        # Try both original and lowercase
        data = collected.get(symbol) or collected.get(symbol.lower())
        if not data:
            return GroundTruthResult.fail(
                f"Stooq data for '{symbol}' not collected. "
                f"Available: {list(collected.keys())[:10]}"
            )

        result = {
            "symbol": symbol,
            "close": data.get("close"),
            "date": data.get("date", ""),
            "change": data.get("daily_change"),
            "change_percent": data.get("daily_change_pct"),
        }

        return GroundTruthResult.ok(result)

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """
        Fetch market data for all symbols and return readable summary string.

        Returns GroundTruthResult with string like: "Market direction: UP. Data: aapl.us: 255.53 (+1.04%), ..."
        """
        symbols = validation_info["symbols"]
        summary_type = validation_info["summary_type"]

        if not symbols:
            return GroundTruthResult.fail("No symbols provided")

        all_data = {}
        has_retryable_error = False
        for symbol in symbols:
            result = await self._fetch_instrument_data(symbol)
            if result.success:
                all_data[symbol] = result.value
            elif result.retryable:
                has_retryable_error = True

        if len(all_data) < len(symbols) // 2 + 1:
            if has_retryable_error:
                return GroundTruthResult.retry("Failed to fetch enough market data")
            return GroundTruthResult.fail("Could not fetch data for enough symbols")

        changes = [d["change_percent"] for d in all_data.values() if d.get("change_percent") is not None]

        if not changes:
            return GroundTruthResult.fail("No change data available")

        positive_count = sum(1 for c in changes if c > 0)
        negative_count = sum(1 for c in changes if c < 0)

        if positive_count > negative_count:
            direction = "UP"
        elif negative_count > positive_count:
            direction = "DOWN"
        else:
            direction = "MIXED"

        data_summary = []
        for symbol, data in all_data.items():
            change_pct = data.get("change_percent")
            close = data.get("close")
            if change_pct is None or close is None:
                continue
            sign = "+" if change_pct >= 0 else ""
            data_summary.append(f"{symbol}: {close:.2f} ({sign}{change_pct:.2f}%)")

        return GroundTruthResult.ok(f"Market direction: {direction}. Data: {', '.join(data_summary)}")

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """
        Validate market summary answer.

        This uses simple heuristics for validation since it's a summary question.
        The LLM validator will also use the ground truth for more nuanced judgment.
        """
        result = await self.get_ground_truth(validation_info)

        if not result.success:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        ground_truth = result.value

        # Parse direction from ground truth string (format: "Market direction: UP/DOWN/MIXED. Data: ...")
        direction = "MIXED"
        if "direction: UP" in ground_truth:
            direction = "UP"
        elif "direction: DOWN" in ground_truth:
            direction = "DOWN"

        answer_lower = answer.lower()

        # Check if direction is correctly identified
        direction_correct = False
        if direction == "UP":
            direction_correct = any(word in answer_lower for word in ["up", "gain", "positive", "bullish", "higher", "rise", "green"])
        elif direction == "DOWN":
            direction_correct = any(word in answer_lower for word in ["down", "loss", "negative", "bearish", "lower", "fall", "red", "decline"])
        else:  # MIXED
            direction_correct = any(word in answer_lower for word in ["mixed", "flat", "unchanged", "neutral"])

        # Check if any actual values are mentioned
        import re
        numbers_in_answer = re.findall(r'[-+]?\d*\.?\d+%?', answer)
        has_values = len(numbers_in_answer) > 0

        # Score based on direction correctness and value presence
        if direction_correct and has_values:
            score = 1.0
            details = f"Correctly identified {direction} market with values"
        elif direction_correct:
            score = 0.5
            details = f"Correctly identified {direction} market but missing specific values"
        else:
            score = 0.0
            details = f"Incorrect direction. Actual market: {direction}"

        return ValidationResult(
            score=score,
            is_correct=score >= 0.5,
            expected=ground_truth,
            actual=answer,
            details=details,
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> tuple:
        """Market summary: LAST for multi-page analysis."""
        trigger = UrlPatternTrigger(domains=["stooq.com"])
        return TriggerConfig(trigger=trigger)

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "stooq"

    def get_gt_source(self):
        """
        Market summary requires aggregating multiple instruments.
        Use API_ONLY for consistent multi-instrument data.
        """
        from liveweb_arena.core.gt_collector import GTSourceType
        return GTSourceType.API_ONLY
