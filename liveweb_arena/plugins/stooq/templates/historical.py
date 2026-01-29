"""Historical data template for Stooq - queries about past prices"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

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

    STOOQ_CSV_URL = "https://stooq.com/q/d/l/"

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

        Note: Historical queries require multi-day data which is not available in the standard
        collected cache that only stores current day data. This template only works in live mode.
        """
        symbol = validation_info.get("symbol", "")
        query_type = validation_info.get("query_type", "highest_close")
        num_days = validation_info.get("num_days", 5)

        if not symbol:
            return GroundTruthResult.fail("No symbol provided")

        return GroundTruthResult.fail(
            f"Historical query '{query_type}' for '{symbol}' over {num_days} days "
            "requires multi-day historical data. "
            "This data is not available in collected cache which only stores current day data. "
            "Historical templates are only supported in live mode."
        )

    def _parse_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

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

        ground_truth = result.value

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
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "stooq"
