"""Comparison template for Stooq - compare multiple financial instruments"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult,
)
from liveweb_arena.core.gt_collector import GTSourceType
from .variables import (
    StockVariable, IndexVariable, US_STOCKS, INDICES,
    StockSpec, IndexSpec, InstrumentType,
)


class ComparisonType(Enum):
    """Types of comparison questions"""
    HIGHER_PRICE = "higher_price"
    LOWER_PRICE = "lower_price"
    BETTER_PERFORMANCE = "better_performance"  # Higher % change
    WORSE_PERFORMANCE = "worse_performance"  # Lower % change
    HIGHER_VOLUME = "higher_volume"


@register_template("stooq_comparison")
class StooqComparisonTemplate(QuestionTemplate):
    """
    Template for comparing multiple instruments on Stooq.

    Generates questions like:
    - "Which stock has a higher price: AAPL or MSFT?"
    - "Compare the daily performance of NVDA, GOOGL, and AMZN. Which performed best?"
    - "Among DJI, SPX, and NDX, which index had the largest gain today?"

    Ground truth is fetched from Stooq CSV endpoint for all compared instruments.
    """

    GT_SOURCE = GTSourceType.API_ONLY  # Multi-instrument comparison

    PATTERNS = {
        ComparisonType.HIGHER_PRICE: [
            "Which has a higher current price: {instruments}?",
            "Compare the prices of {instruments}. Which is trading higher?",
            "Among {instruments}, which has the highest current price?",
        ],
        ComparisonType.LOWER_PRICE: [
            "Which has a lower current price: {instruments}?",
            "Compare the prices of {instruments}. Which is trading lower?",
            "Among {instruments}, which has the lowest current price?",
        ],
        ComparisonType.BETTER_PERFORMANCE: [
            "Which performed better today: {instruments}?",
            "Compare the daily performance of {instruments}. Which gained the most?",
            "Among {instruments}, which had the best performance today?",
        ],
        ComparisonType.WORSE_PERFORMANCE: [
            "Which performed worse today: {instruments}?",
            "Compare the daily performance of {instruments}. Which lost the most?",
            "Among {instruments}, which had the worst performance today?",
        ],
        ComparisonType.HIGHER_VOLUME: [
            "Which has higher trading volume today: {instruments}?",
            "Compare the trading volumes of {instruments}. Which was traded more?",
            "Among {instruments}, which has the highest trading volume?",
        ],
    }

    def __init__(self):
        super().__init__("stooq_comparison")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """
        Generate a Stooq comparison question.

        Args:
            seed: Random seed for reproducible generation
            variant: Optional variant index for selecting comparison type.
                     0=HIGHER_PRICE, 1=LOWER_PRICE, 2=BETTER_PERFORMANCE,
                     3=WORSE_PERFORMANCE, 4=HIGHER_VOLUME
        """
        rng = random.Random(seed)

        # Decide whether to compare stocks or indices
        compare_stocks = rng.choice([True, False])

        # Select 2-3 instruments to compare
        num_instruments = rng.randint(2, 3)

        if compare_stocks:
            instruments = rng.sample(US_STOCKS, num_instruments)
            symbols = [s.symbol for s in instruments]
            names = [s.display_name for s in instruments]
            inst_type = InstrumentType.STOCK
        else:
            instruments = rng.sample(INDICES, num_instruments)
            symbols = [i.symbol for i in instruments]
            names = [i.display_name for i in instruments]
            inst_type = InstrumentType.INDEX

        # Select comparison type (use variant if provided)
        comparison_types_list = list(ComparisonType)
        if variant is not None:
            comparison_type = comparison_types_list[variant % len(comparison_types_list)]
        else:
            comparison_type = rng.choice(comparison_types_list)

        if comparison_type == ComparisonType.HIGHER_VOLUME:
            # Volume comparison only for stocks
            if not compare_stocks:
                comparison_type = ComparisonType.BETTER_PERFORMANCE

        # Build question
        patterns = self.PATTERNS.get(comparison_type, self.PATTERNS[ComparisonType.HIGHER_PRICE])
        pattern = rng.choice(patterns)
        instruments_str = ", ".join(names[:-1]) + " or " + names[-1] if len(names) > 1 else names[0]
        question_text = pattern.format(instruments=instruments_str)

        validation_info = {
            "symbols": symbols,
            "names": names,
            "comparison_type": comparison_type.value,
            "instrument_type": inst_type.value,
        }

        # 2 steps per instrument (goto + read) + buffer
        expected_steps = num_instruments * 2 + 4

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://stooq.com/",
            variables={
                "instruments": instruments,
                "comparison_type": comparison_type,
            },
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=expected_steps,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        comparison_type = validation_info.get("comparison_type", "higher_price")
        names = validation_info.get("names", [])
        names_str = ", ".join(names)

        rules_map = {
            "higher_price": f"highest current price among {names_str}",
            "lower_price": f"lowest current price among {names_str}",
            "better_performance": f"best percentage change (highest gain or smallest loss) among {names_str}",
            "worse_performance": f"worst percentage change (biggest loss or smallest gain) among {names_str}",
            "higher_volume": f"highest trading volume among {names_str}",
        }

        rule = rules_map.get(comparison_type, comparison_type)
        return f"""Task-Specific Rules (Stooq Comparison - {rule}):
- Score 1.0: Agent correctly identifies the instrument with {rule}
- Score 0.0: Wrong instrument or no clear answer provided
- The answer must clearly state which instrument wins the comparison"""

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
            "volume": data.get("volume"),
            "change_percent": data.get("daily_change_pct"),
        }

        return GroundTruthResult.ok(result)

    def _parse_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """
        Fetch data for all instruments and determine the winner.

        Returns GroundTruthResult with winner name as string.
        """
        symbols = validation_info.get("symbols", [])
        names = validation_info.get("names", [])
        comparison_type = validation_info.get("comparison_type", "higher_price")

        if not symbols or len(symbols) != len(names):
            return GroundTruthResult.fail("Invalid symbols/names configuration")

        # Fetch data for all instruments
        all_data = {}
        has_retryable_error = False
        for symbol, name in zip(symbols, names):
            result = await self._fetch_instrument_data(symbol)
            if result.success:
                data = result.value
                data["name"] = name
                all_data[name] = data
            elif result.retryable:
                has_retryable_error = True

        if len(all_data) < 2:
            if has_retryable_error:
                return GroundTruthResult.retry("Failed to fetch enough instrument data")
            return GroundTruthResult.fail("Could not fetch data for at least 2 instruments")

        # Determine winner based on comparison type
        if comparison_type == "higher_price":
            winner = max(all_data.values(), key=lambda x: x.get("close", 0) or 0)
        elif comparison_type == "lower_price":
            winner = min(all_data.values(), key=lambda x: x.get("close", float('inf')) or float('inf'))
        elif comparison_type == "better_performance":
            winner = max(all_data.values(), key=lambda x: x.get("change_percent", -float('inf')) or -float('inf'))
        elif comparison_type == "worse_performance":
            winner = min(all_data.values(), key=lambda x: x.get("change_percent", float('inf')) or float('inf'))
        elif comparison_type == "higher_volume":
            winner = max(all_data.values(), key=lambda x: x.get("volume", 0) or 0)
        else:
            return GroundTruthResult.fail(f"Unknown comparison type: {comparison_type}")

        return GroundTruthResult.ok(winner["name"])

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate comparison answer"""
        result = await self.get_ground_truth(validation_info)

        if not result.success:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        winner_name = result.value

        answer_lower = answer.lower()
        names = validation_info.get("names", [])

        # Check if the winning instrument is mentioned in the answer
        if winner_name.lower() in answer_lower:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=winner_name,
                actual=answer,
                details="Correct answer",
            )

        # Check for partial matches (e.g., "AAPL" instead of "Apple (AAPL)")
        # Extract ticker symbol from winner name like "Apple (AAPL)"
        if "(" in winner_name and ")" in winner_name:
            ticker = winner_name.split("(")[-1].rstrip(")")
            if ticker.lower() in answer_lower:
                return ValidationResult(
                    score=1.0,
                    is_correct=True,
                    expected=winner_name,
                    actual=answer,
                    details="Correct (matched by ticker)",
                )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=winner_name,
            actual=answer,
            details=f"Expected {winner_name} but not found in answer",
        )

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> tuple:
        """
        Comparison: AI visits multiple stock pages, use LAST.

        Strategy: LAST - AI gathers data from multiple pages,
        last fetch is closest to answer submission.
        """
        trigger = UrlPatternTrigger(domains=["stooq.com"])
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.LAST)

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "stooq"

    def get_gt_source(self):
        """
        Comparison requires fetching data for multiple instruments simultaneously.
        Use API_ONLY for consistent comparison at the same time point.
        """
        from liveweb_arena.core.gt_collector import GTSourceType
        return GTSourceType.API_ONLY
