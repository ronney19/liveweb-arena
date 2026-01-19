"""52-week high/low template for Stooq"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional
import aiohttp
import io
import csv
from datetime import datetime, timedelta

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from .variables import (
    US_STOCKS, INDICES, StockSpec, IndexSpec, InstrumentType,
)


class Week52QueryType(Enum):
    """Types of 52-week queries"""
    HIGH_PRICE = "high_price"  # What is the 52-week high?
    LOW_PRICE = "low_price"  # What is the 52-week low?
    DISTANCE_FROM_HIGH = "distance_from_high"  # How far from 52-week high (%)
    DISTANCE_FROM_LOW = "distance_from_low"  # How far from 52-week low (%)
    CLOSER_TO = "closer_to"  # Is price closer to high or low?


@register_template("stooq_52week")
class Stooq52WeekTemplate(QuestionTemplate):
    """
    Template for 52-week high/low queries on Stooq.

    Generates questions like:
    - "What is the 52-week high of Apple stock? Check stooq.com."
    - "What is the 52-week low of the S&P 500 index? Check stooq.com."
    - "How far is MSFT from its 52-week high? Use stooq.com."
    - "Is NVDA closer to its 52-week high or low? Check stooq.com."

    Ground truth is calculated from Stooq CSV historical data (last 252 trading days).
    """

    PATTERNS = {
        Week52QueryType.HIGH_PRICE: [
            "What is the 52-week high of {instrument}?",
            "Find the 52-week high price for {instrument}.",
            "What was the highest price of {instrument} in the past 52 weeks?",
            "What is {instrument}'s 52-week high?",
        ],
        Week52QueryType.LOW_PRICE: [
            "What is the 52-week low of {instrument}?",
            "Find the 52-week low price for {instrument}.",
            "What was the lowest price of {instrument} in the past 52 weeks?",
            "What is {instrument}'s 52-week low?",
        ],
        Week52QueryType.DISTANCE_FROM_HIGH: [
            "How far is {instrument} from its 52-week high in percentage?",
            "What percentage below its 52-week high is {instrument} trading?",
            "Calculate how much {instrument} is down from its 52-week high.",
            "Find the percentage difference between {instrument}'s current price and 52-week high.",
        ],
        Week52QueryType.DISTANCE_FROM_LOW: [
            "How far is {instrument} from its 52-week low in percentage?",
            "What percentage above its 52-week low is {instrument} trading?",
            "Calculate how much {instrument} is up from its 52-week low.",
            "Find the percentage difference between {instrument}'s current price and 52-week low.",
        ],
        Week52QueryType.CLOSER_TO: [
            "Is {instrument} closer to its 52-week high or 52-week low?",
            "Is {instrument} trading nearer to its 52-week high or low?",
            "Determine whether {instrument} is closer to its annual high or low.",
            "Is {instrument} nearer to its 52-week high or 52-week low?",
        ],
    }

    STOOQ_CSV_URL = "https://stooq.com/q/d/l/"

    def __init__(self):
        super().__init__("stooq_52week")

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
        query_type = rng.choice(list(Week52QueryType))

        # Build question
        patterns = self.PATTERNS[query_type]
        pattern = rng.choice(patterns)
        question_text = pattern.format(instrument=name)

        validation_info = {
            "symbol": symbol,
            "name": name,
            "query_type": query_type.value,
            "instrument_type": inst_type.value,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=f"https://stooq.com/q/?s={symbol}",
            variables={
                "instrument": instrument,
                "query_type": query_type,
            },
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        query_type = validation_info.get("query_type", "high_price")
        name = validation_info.get("name", "")

        rules_map = {
            "high_price": f"52-week high price of {name}",
            "low_price": f"52-week low price of {name}",
            "distance_from_high": f"percentage below 52-week high for {name}",
            "distance_from_low": f"percentage above 52-week low for {name}",
            "closer_to": f"whether {name} is closer to 52-week high or low",
        }

        rule = rules_map.get(query_type, query_type)

        if query_type in ["high_price", "low_price"]:
            return f"""Task-Specific Rules (52-Week {rule}):
- Score 1.0: Price matches within 5% tolerance
- Score 0.0: Price differs by more than 5%"""

        elif query_type in ["distance_from_high", "distance_from_low"]:
            return f"""Task-Specific Rules (52-Week {rule}):
- Score 1.0: Percentage matches within 5 percentage points
- Score 0.0: Percentage differs by more than 5 percentage points"""

        else:  # closer_to
            return f"""Task-Specific Rules (52-Week {rule}):
- Score 1.0: Correctly identifies whether closer to high or low
- Score 0.0: Wrong answer"""

    async def _fetch_52week_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetch 52-week data from Stooq CSV.

        Returns dict with: current_price, high_52w, low_52w
        """
        try:
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

            if len(rows) < 10:
                return None

            # Get last 252 trading days (approximately 1 year)
            year_data = rows[-252:] if len(rows) >= 252 else rows

            # Extract high and low from the period
            highs = []
            lows = []
            for row in year_data:
                high = self._parse_float(row.get("High"))
                low = self._parse_float(row.get("Low"))
                if high is not None:
                    highs.append(high)
                if low is not None:
                    lows.append(low)

            if not highs or not lows:
                return None

            # Current price is the last close
            current = self._parse_float(rows[-1].get("Close"))
            if current is None:
                return None

            return {
                "current_price": current,
                "high_52w": max(highs),
                "low_52w": min(lows),
            }

        except Exception:
            return None

    def _parse_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Optional[str]:
        """
        Calculate ground truth based on query type.

        Returns:
            - For high/low: price as string (e.g., "255.53")
            - For distance: percentage as string (e.g., "-5.23%" or "+12.45%")
            - For closer_to: "high" or "low"
        """
        symbol = validation_info.get("symbol", "")
        query_type = validation_info.get("query_type", "high_price")

        if not symbol:
            return None

        data = await self._fetch_52week_data(symbol)
        if data is None:
            return None

        current = data["current_price"]
        high = data["high_52w"]
        low = data["low_52w"]

        if query_type == "high_price":
            return f"{high:.2f}"

        elif query_type == "low_price":
            return f"{low:.2f}"

        elif query_type == "distance_from_high":
            # Percentage below high (usually negative or zero)
            pct = ((current - high) / high) * 100
            return f"{pct:.2f}%"

        elif query_type == "distance_from_low":
            # Percentage above low (usually positive)
            pct = ((current - low) / low) * 100
            return f"{pct:.2f}%"

        elif query_type == "closer_to":
            dist_to_high = abs(current - high)
            dist_to_low = abs(current - low)
            return "high" if dist_to_high < dist_to_low else "low"

        return None

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate 52-week answer"""
        import re

        ground_truth = await self.get_ground_truth(validation_info)
        query_type = validation_info.get("query_type", "high_price")

        if ground_truth is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable",
            )

        # Handle closer_to type (string match)
        if query_type == "closer_to":
            answer_lower = answer.lower()
            expected = ground_truth.lower()

            if expected == "high":
                is_correct = "high" in answer_lower and "low" not in answer_lower.replace("below", "")
                # More robust check
                if not is_correct:
                    is_correct = any(phrase in answer_lower for phrase in [
                        "closer to its 52-week high",
                        "closer to the 52-week high",
                        "nearer to its high",
                        "closer to high",
                        "near its high",
                    ])
            else:  # low
                is_correct = "low" in answer_lower and "high" not in answer_lower.replace("above", "")
                if not is_correct:
                    is_correct = any(phrase in answer_lower for phrase in [
                        "closer to its 52-week low",
                        "closer to the 52-week low",
                        "nearer to its low",
                        "closer to low",
                        "near its low",
                    ])

            return ValidationResult(
                score=1.0 if is_correct else 0.0,
                is_correct=is_correct,
                expected=f"closer to {ground_truth}",
                actual=answer,
                details="Correct" if is_correct else f"Expected: closer to {ground_truth}",
            )

        # Handle numeric types
        # Parse expected value
        expected_numbers = re.findall(r'[-+]?\d*\.?\d+', ground_truth.replace(',', ''))
        if not expected_numbers:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Could not parse ground truth",
            )
        expected_value = float(expected_numbers[0])

        # Extract numbers from answer
        answer_clean = answer.replace(',', '').replace('$', '')
        numbers = re.findall(r'[-+]?\d*\.?\d+', answer_clean)

        if not numbers:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="No numeric value found in answer",
            )

        # Find best matching number
        best_score = 0.0
        best_diff = float('inf')

        for num_str in numbers:
            try:
                num = float(num_str)

                if query_type in ["high_price", "low_price"]:
                    # Price: 5% tolerance (accounts for CSV vs webpage display differences)
                    if expected_value == 0:
                        continue
                    pct_diff = abs(num - expected_value) / expected_value * 100
                    if pct_diff <= 5:
                        score = 1.0
                    else:
                        score = 0.0
                    diff = pct_diff

                else:  # distance_from_high, distance_from_low
                    # Percentage: 5 percentage points tolerance
                    diff = abs(num - abs(expected_value))
                    if diff <= 5:
                        score = 1.0
                    else:
                        score = 0.0

                if score > best_score or (score == best_score and diff < best_diff):
                    best_score = score
                    best_diff = diff

            except ValueError:
                continue

        return ValidationResult(
            score=best_score,
            is_correct=best_score == 1.0,
            expected=ground_truth,
            actual=answer,
            details=f"Difference: {best_diff:.2f}" if best_diff < float('inf') else "No valid number found",
        )
