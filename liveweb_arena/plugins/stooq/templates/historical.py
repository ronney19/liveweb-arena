"""Historical data template for Stooq - queries about past prices"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
import aiohttp
import io
import csv

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
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

    def generate(self, seed: int) -> GeneratedQuestion:
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

        # Select query type
        query_type = rng.choice([
            HistoricalQueryType.HIGHEST_CLOSE,
            HistoricalQueryType.LOWEST_CLOSE,
            HistoricalQueryType.AVERAGE_CLOSE,
            HistoricalQueryType.PRICE_RANGE,
        ])

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

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Optional[float]:
        """
        Calculate ground truth from historical CSV data.
        """
        symbol = validation_info.get("symbol", "")
        query_type = validation_info.get("query_type", "highest_close")
        num_days = validation_info.get("num_days", 5)

        if not symbol:
            return None

        try:
            # Fetch CSV data
            async with aiohttp.ClientSession() as session:
                params = {"s": symbol, "i": "d"}
                async with session.get(
                    self.STOOQ_CSV_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    if response.status != 200:
                        return None
                    csv_text = await response.text()

            reader = csv.DictReader(io.StringIO(csv_text))
            rows = list(reader)

            if len(rows) < num_days:
                return None

            # Get the last N days of data
            recent_data = rows[-num_days:]
            closes = []
            for row in recent_data:
                close = self._parse_float(row.get("Close"))
                if close is not None:
                    closes.append(close)

            if not closes:
                return None

            # Calculate result based on query type
            if query_type == "highest_close":
                return max(closes)
            elif query_type == "lowest_close":
                return min(closes)
            elif query_type == "average_close":
                return sum(closes) / len(closes)
            elif query_type == "price_range":
                return max(closes) - min(closes)
            else:
                return None

        except Exception:
            return None

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
        ground_truth = await self.get_ground_truth(validation_info)

        if ground_truth is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable",
            )

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
