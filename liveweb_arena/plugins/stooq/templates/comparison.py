"""Comparison template for Stooq - compare multiple financial instruments"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
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
    }

    STOOQ_CSV_URL = "https://stooq.com/q/d/l/"

    def __init__(self):
        super().__init__("stooq_comparison")

    def generate(self, seed: int) -> GeneratedQuestion:
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

        # Select comparison type
        comparison_type = rng.choice(list(ComparisonType))
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

    async def _fetch_instrument_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch data for a single instrument"""
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

            if not rows:
                return None

            latest = rows[-1]
            result = {
                "symbol": symbol,
                "close": self._parse_float(latest.get("Close")),
                "volume": self._parse_float(latest.get("Volume")),
            }

            # Calculate change percent if we have previous data
            if len(rows) >= 2:
                prev = rows[-2]
                prev_close = self._parse_float(prev.get("Close"))
                if prev_close and result["close"]:
                    result["change_percent"] = ((result["close"] - prev_close) / prev_close) * 100

            return result

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
        Fetch data for all instruments and determine the winner.

        Returns:
            Winner name as string for LLM validation display.
        """
        symbols = validation_info.get("symbols", [])
        names = validation_info.get("names", [])
        comparison_type = validation_info.get("comparison_type", "higher_price")

        if not symbols or len(symbols) != len(names):
            return None

        # Fetch data for all instruments
        all_data = {}
        for symbol, name in zip(symbols, names):
            data = await self._fetch_instrument_data(symbol)
            if data:
                data["name"] = name
                all_data[name] = data

        if len(all_data) < 2:
            return None

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
            return None

        return winner["name"]

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate comparison answer"""
        winner_name = await self.get_ground_truth(validation_info)

        if winner_name is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable",
            )

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
