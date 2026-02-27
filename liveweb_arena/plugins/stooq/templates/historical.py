"""Historical data template for Stooq - queries about past prices"""

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
from .variables import (
    StockVariable, IndexVariable, US_STOCKS, INDICES,
    StockSpec, IndexSpec, InstrumentType,
    parse_float,
)


class HistoricalQueryType(Enum):
    """Types of historical data queries"""
    HIGHEST_CLOSE = "highest_close"  # Highest closing price in period
    LOWEST_CLOSE = "lowest_close"  # Lowest closing price in period
    AVERAGE_CLOSE = "average_close"  # Average closing price in period
    PRICE_RANGE = "price_range"  # Difference between high and low
    TOTAL_VOLUME = "total_volume"  # Total trading volume


@register_template("stooq_historical")
class StooqHistoricalTemplate(QuestionTemplate):
    """
    Template for historical data queries on Stooq.

    Generates questions about past price data:
    - "What was the highest closing price of AAPL in the last 5 trading days?"
    - "What was the average closing price of MSFT over the past week?"
    - "What was the price range (high-low) of GOOGL in the last 3 days?"

    Ground truth is calculated from Stooq CSV historical data.
    """

    GT_SOURCE = GTSourceType.API_ONLY  # Requires historical CSV data

    PATTERNS = {
        HistoricalQueryType.HIGHEST_CLOSE: [
            "What was the highest closing price of {instrument} in the last {days} trading days?",
            "Find the peak closing price of {instrument} over the past {days} trading days.",
            "What was the maximum close price for {instrument} in the last {days} days?",
        ],
        HistoricalQueryType.LOWEST_CLOSE: [
            "What was the lowest closing price of {instrument} in the last {days} trading days?",
            "Find the minimum closing price of {instrument} over the past {days} trading days.",
            "What was the lowest close for {instrument} in the last {days} days?",
        ],
        HistoricalQueryType.AVERAGE_CLOSE: [
            "What was the average closing price of {instrument} over the last {days} trading days?",
            "Calculate the mean closing price of {instrument} for the past {days} trading days.",
            "Find the average close of {instrument} over the last {days} days.",
        ],
        HistoricalQueryType.PRICE_RANGE: [
            "What was the price range (highest minus lowest close) of {instrument} in the last {days} trading days?",
            "Find the difference between the highest and lowest closing prices of {instrument} over {days} trading days.",
        ],
    }

    def __init__(self):
        super().__init__("stooq_historical")
        self.register_variable(StockVariable())
        self.register_variable(IndexVariable())

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """
        Generate a Stooq historical question.

        Args:
            seed: Random seed for reproducible generation
            variant: Optional variant index for selecting query type.
                     0=HIGHEST_CLOSE, 1=LOWEST_CLOSE, 2=AVERAGE_CLOSE, 3=PRICE_RANGE
        """
        rng = random.Random(seed)

        # Decide stock or index
        use_stock = rng.choice([True, False])

        if use_stock:
            instrument = rng.choice(US_STOCKS)
            symbol = instrument.symbol
            name = instrument.display_name
            inst_type = InstrumentType.STOCK
        else:
            instrument = rng.choice(INDICES)
            symbol = instrument.symbol
            name = instrument.display_name
            inst_type = InstrumentType.INDEX

        # Select query type (use variant if provided)
        query_types_list = [
            HistoricalQueryType.HIGHEST_CLOSE,
            HistoricalQueryType.LOWEST_CLOSE,
            HistoricalQueryType.AVERAGE_CLOSE,
            HistoricalQueryType.PRICE_RANGE,
        ]
        if variant is not None:
            query_type = query_types_list[variant % len(query_types_list)]
        else:
            query_type = rng.choice(query_types_list)

        # Select number of days (3-10 trading days)
        num_days = rng.randint(3, 10)

        # Build question
        patterns = self.PATTERNS[query_type]
        pattern = rng.choice(patterns)
        question_text = pattern.format(instrument=name, days=num_days)

        validation_info = {
            "symbol": symbol,
            "name": name,
            "query_type": query_type.value,
            "num_days": num_days,
            "instrument_type": inst_type.value,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=f"https://stooq.com/q/d/?s={symbol}",
            variables={
                "instrument": instrument,
                "query_type": query_type,
                "num_days": num_days,
            },
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=6,  # Single page but may need scroll
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        query_type = validation_info.get("query_type", "highest_close")
        num_days = validation_info.get("num_days", 5)
        name = validation_info.get("name", "")

        rules_map = {
            "highest_close": f"highest closing price of {name} over {num_days} trading days",
            "lowest_close": f"lowest closing price of {name} over {num_days} trading days",
            "average_close": f"average closing price of {name} over {num_days} trading days",
            "price_range": f"price range (high-low) of {name} over {num_days} trading days",
        }

        rule = rules_map.get(query_type, query_type)
        return f"""Task-Specific Rules (Stooq Historical - {rule}):
- Score 1.0: Value matches within 2% tolerance
- Score 0.0: Value differs by more than 2% or answer format is wrong
- For averages, accept values rounded to 2 decimal places"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """
        Calculate ground truth from collected API data.

        Uses the 'history' field from collected Stooq data which contains
        the last 30 days of price data.
        """
        symbol = validation_info["symbol"]
        query_type = validation_info["query_type"]
        num_days = validation_info["num_days"]

        if not symbol:
            return GroundTruthResult.fail("No symbol provided")

        # Get collected data from GT collector
        from liveweb_arena.core.gt_collector import get_current_gt_collector
        gt_collector = get_current_gt_collector()
        if not gt_collector:
            return GroundTruthResult.fail("GT collector not available")

        collected = gt_collector.get_collected_api_data()
        asset_data = collected.get(symbol)
        if not asset_data:
            # Try with .us suffix
            asset_data = collected.get(f"{symbol}.us")
        if not asset_data:
            available = list(collected.keys())[:5]
            return GroundTruthResult.fail(
                f"Stooq data for '{symbol}' not collected. Available: {available}"
            )

        # Get history from collected data
        history = asset_data.get("history", [])
        if not history or len(history) < num_days:
            return GroundTruthResult.fail(
                f"Insufficient historical data for '{symbol}': need {num_days} days, got {len(history)}"
            )

        # Get the last N days (most recent first after reversing)
        recent_days = history[-num_days:]
        closes = [day["close"] for day in recent_days if day.get("close") is not None]

        if len(closes) < num_days:
            return GroundTruthResult.fail(
                f"Missing close prices in historical data for '{symbol}'"
            )

        # Calculate result based on query type
        if query_type == "highest_close":
            result = max(closes)
        elif query_type == "lowest_close":
            result = min(closes)
        elif query_type == "average_close":
            result = sum(closes) / len(closes)
        elif query_type == "price_range":
            result = max(closes) - min(closes)
        else:
            return GroundTruthResult.fail(f"Unknown query type: {query_type}")

        return GroundTruthResult.ok(f"{result:.2f}")

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate historical data answer"""
        result = await self.get_ground_truth(validation_info)

        if not result.success:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        ground_truth = float(result.value)

        # Extract number from answer
        import re
        numbers = re.findall(r'[\d,]+\.?\d*', answer.replace(',', ''))
        if not numbers:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=f"{ground_truth:.2f}",
                actual=answer,
                details="No numeric value found in answer",
            )

        # Find the most likely match
        actual = None
        for n in numbers:
            try:
                val = float(n.replace(',', ''))
                if val > 0:
                    actual = val
                    break
            except ValueError:
                continue

        if actual is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=f"{ground_truth:.2f}",
                actual=answer,
                details="Could not parse numeric value",
            )

        # Calculate tolerance (2%)
        tolerance = abs(ground_truth) * 0.02
        diff = abs(actual - ground_truth)

        if diff <= tolerance:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=f"{ground_truth:.2f}",
                actual=f"{actual:.2f}",
                details=f"Within 2% tolerance (diff: {diff:.4f})",
            )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=f"{ground_truth:.2f}",
            actual=f"{actual:.2f}",
            details=f"Outside 2% tolerance (diff: {diff:.4f})",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> tuple:
        """
        Historical query: fetch when AI visits the specific symbol's page.

        Uses symbol-specific URL matching for precise synchronization.

        Strategy: FIRST - single stock query.
        """
        symbol = validation_info.get("symbol", "")
        trigger = UrlPatternTrigger(
            domains=["stooq.com"],
            url_contains=symbol if symbol else None,
        )
        return TriggerConfig(trigger=trigger)

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "stooq"
