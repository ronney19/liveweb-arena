"""Multi-instrument ranking template for Stooq - complex questions requiring multiple data points"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import aiohttp
import asyncio
import io
import csv

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)


class RankingMetric(Enum):
    """Metrics for ranking instruments"""
    CHANGE_PERCENT = "change_percent"  # Today's percentage change
    CURRENT_PRICE = "current_price"  # Current price
    WEEK52_GAIN = "week52_gain"  # Gain from 52-week low (%)
    DISTANCE_FROM_HIGH = "distance_from_high"  # Distance from 52-week high (%)


class RankPosition(Enum):
    """Position in ranking to query"""
    FIRST = "1st"
    SECOND = "2nd"
    THIRD = "3rd"
    LAST = "last"
    SECOND_LAST = "2nd last"


# Predefined instrument groups for ranking questions
TECH_STOCKS = [
    ("aapl.us", "Apple"),
    ("msft.us", "Microsoft"),
    ("nvda.us", "NVIDIA"),
    ("googl.us", "Alphabet"),
    ("meta.us", "Meta"),
]

FINANCE_STOCKS = [
    ("jpm.us", "JPMorgan Chase"),
    ("v.us", "Visa"),
    ("wmt.us", "Walmart"),
    ("xom.us", "Exxon Mobil"),
    ("ko.us", "Coca-Cola"),
]

MIXED_STOCKS = [
    ("aapl.us", "Apple"),
    ("jpm.us", "JPMorgan Chase"),
    ("tsla.us", "Tesla"),
    ("dis.us", "Disney"),
    ("nke.us", "Nike"),
]

US_INDICES = [
    ("^dji", "Dow Jones"),
    ("^spx", "S&P 500"),
    ("^ndx", "NASDAQ 100"),
    ("^dax", "DAX"),
    ("^nkx", "Nikkei 225"),
]

INSTRUMENT_GROUPS = {
    "tech": ("major tech stocks", TECH_STOCKS),
    "finance": ("major stocks", FINANCE_STOCKS),
    "mixed": ("these stocks", MIXED_STOCKS),
    "indices": ("major indices", US_INDICES),
}


@register_template("stooq_ranking")
class StooqRankingTemplate(QuestionTemplate):
    """
    Template for complex ranking questions requiring multiple data points.

    This template tests the agent's ability to:
    1. Navigate to multiple pages and collect data
    2. Remember and compare values across instruments
    3. Correctly rank and identify specific positions

    Example questions:
    - "Among Apple, Microsoft, NVIDIA, Alphabet, and Meta, which has the 2nd highest gain today?"
    - "Which of the major indices is closest to its 52-week high?"
    - "Among tech stocks, which has the lowest current price?"
    """

    PATTERNS = {
        RankingMetric.CHANGE_PERCENT: {
            "highest": [
                "Among {instruments}, which has the {position} highest percentage gain today?",
                "Looking at {instruments}, which one has the {position} best performance today?",
                "Which of {instruments} has the {position} highest daily change?",
            ],
            "lowest": [
                "Among {instruments}, which has the {position} lowest percentage change today?",
                "Looking at {instruments}, which one has the {position} worst performance today?",
                "Which of {instruments} has the {position} biggest decline today?",
            ],
        },
        RankingMetric.CURRENT_PRICE: {
            "highest": [
                "Among {instruments}, which has the {position} highest stock price?",
                "Looking at {instruments}, which one has the {position} highest current price?",
                "Which of {instruments} is the {position} most expensive?",
            ],
            "lowest": [
                "Among {instruments}, which has the {position} lowest stock price?",
                "Looking at {instruments}, which one has the {position} lowest current price?",
                "Which of {instruments} is the {position} cheapest?",
            ],
        },
        RankingMetric.WEEK52_GAIN: {
            "highest": [
                "Among {instruments}, which has gained the {position} most from its 52-week low?",
                "Looking at {instruments}, which has the {position} highest gain from its 52-week low?",
                "Which of {instruments} has rallied the {position} most from its annual low?",
            ],
            "lowest": [
                "Among {instruments}, which has gained the {position} least from its 52-week low?",
                "Looking at {instruments}, which has the {position} smallest gain from its 52-week low?",
                "Which of {instruments} is {position} closest to its annual low?",
            ],
        },
        RankingMetric.DISTANCE_FROM_HIGH: {
            "highest": [
                "Among {instruments}, which is {position} furthest from its 52-week high?",
                "Looking at {instruments}, which is the {position} most below its 52-week high?",
                "Which of {instruments} has fallen the {position} most from its annual high?",
            ],
            "lowest": [
                "Among {instruments}, which is {position} closest to its 52-week high?",
                "Looking at {instruments}, which is the {position} nearest to its 52-week high?",
                "Which of {instruments} is {position} closest to its annual high?",
            ],
        },
    }

    STOOQ_CSV_URL = "https://stooq.com/q/d/l/"

    def __init__(self):
        super().__init__("stooq_ranking")

    def generate(self, seed: int) -> GeneratedQuestion:
        rng = random.Random(seed)

        # Select instrument group
        group_key = rng.choice(list(INSTRUMENT_GROUPS.keys()))
        group_desc, instruments = INSTRUMENT_GROUPS[group_key]

        # Select metric
        metric = rng.choice(list(RankingMetric))

        # Select ranking direction and position
        direction = rng.choice(["highest", "lowest"])

        # Map position based on direction
        if direction == "highest":
            position = rng.choice([RankPosition.FIRST, RankPosition.SECOND, RankPosition.THIRD])
        else:
            position = rng.choice([RankPosition.FIRST, RankPosition.SECOND, RankPosition.LAST])

        # Build instrument list string
        names = [inst[1] for inst in instruments]
        if len(names) > 2:
            instruments_str = ", ".join(names[:-1]) + ", and " + names[-1]
        else:
            instruments_str = " and ".join(names)

        # Format position for question
        if position == RankPosition.FIRST:
            position_str = ""  # "highest" / "lowest" already implies first
        elif position == RankPosition.LAST:
            position_str = ""
            # Flip direction for "last"
            direction = "lowest" if direction == "highest" else "highest"
        else:
            position_str = position.value + " "

        # Select pattern
        patterns = self.PATTERNS[metric][direction]
        pattern = rng.choice(patterns)
        question_text = pattern.format(instruments=instruments_str, position=position_str)

        # Clean up double spaces
        question_text = " ".join(question_text.split())

        validation_info = {
            "group_key": group_key,
            "instruments": instruments,
            "metric": metric.value,
            "direction": direction,
            "position": position.value,
        }

        # Calculate expected steps: 5 instruments × 2 steps (goto + read) + buffer
        num_instruments = len(instruments)
        expected_steps = num_instruments * 2 + 5  # ~15 steps for 5 instruments

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://stooq.com/",
            variables={
                "group": group_key,
                "metric": metric,
                "direction": direction,
                "position": position,
            },
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=expected_steps,
        )

    def get_expected_steps(self, validation_info: Dict[str, Any]) -> int:
        """Ranking requires visiting multiple pages - need more steps"""
        instruments = validation_info.get("instruments", [])
        num_instruments = len(instruments)
        # 2 steps per instrument (goto + read) + buffer
        return num_instruments * 2 + 5

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        metric = validation_info.get("metric", "change_percent")
        direction = validation_info.get("direction", "highest")
        position = validation_info.get("position", "1st")
        instruments = validation_info.get("instruments", [])

        names = [inst[1] for inst in instruments]
        instruments_str = ", ".join(names)

        metric_desc = {
            "change_percent": "daily percentage change",
            "current_price": "current price",
            "week52_gain": "gain from 52-week low",
            "distance_from_high": "distance from 52-week high",
        }.get(metric, metric)

        return f"""Task-Specific Rules (Ranking: {position} {direction} {metric_desc}):
Instruments: {instruments_str}

- Score 1.0: Correctly identifies the instrument at the specified ranking position
- Score 0.0: Wrong instrument or unable to determine ranking

The agent must:
1. Check each instrument on stooq.com
2. Collect the relevant metric for all instruments
3. Rank them correctly and identify the {position} {direction}"""

    async def _fetch_instrument_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch comprehensive data for a single instrument"""
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

            if len(rows) < 2:
                return None

            latest = rows[-1]
            prev = rows[-2]

            current_price = self._parse_float(latest.get("Close"))
            prev_close = self._parse_float(prev.get("Close"))

            if current_price is None:
                return None

            # Calculate daily change
            change_percent = None
            if prev_close and prev_close != 0:
                change_percent = ((current_price - prev_close) / prev_close) * 100

            # Calculate 52-week metrics
            year_data = rows[-252:] if len(rows) >= 252 else rows
            highs = []
            lows = []
            for row in year_data:
                high = self._parse_float(row.get("High"))
                low = self._parse_float(row.get("Low"))
                if high is not None:
                    highs.append(high)
                if low is not None:
                    lows.append(low)

            week52_high = max(highs) if highs else None
            week52_low = min(lows) if lows else None

            week52_gain = None
            distance_from_high = None

            if week52_low and week52_low != 0:
                week52_gain = ((current_price - week52_low) / week52_low) * 100

            if week52_high and week52_high != 0:
                distance_from_high = ((current_price - week52_high) / week52_high) * 100

            return {
                "symbol": symbol,
                "current_price": current_price,
                "change_percent": change_percent,
                "week52_gain": week52_gain,
                "distance_from_high": distance_from_high,
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
        Calculate ground truth by fetching all instruments and ranking them.

        Returns the name of the instrument at the specified ranking position.
        """
        instruments = validation_info.get("instruments", [])
        metric = validation_info.get("metric", "change_percent")
        direction = validation_info.get("direction", "highest")
        position = validation_info.get("position", "1st")

        if not instruments:
            return None

        # Fetch data for all instruments
        all_data = []
        for symbol, name in instruments:
            data = await self._fetch_instrument_data(symbol)
            if data:
                data["name"] = name
                all_data.append(data)

        if len(all_data) < len(instruments):
            # Need data for all instruments
            return None

        # Get the metric value for sorting
        def get_metric_value(d):
            return d.get(metric)

        # Filter out None values
        valid_data = [d for d in all_data if get_metric_value(d) is not None]
        if len(valid_data) < len(instruments):
            return None

        # Sort by metric
        reverse = (direction == "highest")
        sorted_data = sorted(valid_data, key=lambda d: get_metric_value(d), reverse=reverse)

        # Get position index
        position_map = {
            "1st": 0,
            "2nd": 1,
            "3rd": 2,
            "last": -1,
            "2nd last": -2,
        }
        idx = position_map.get(position, 0)

        try:
            result = sorted_data[idx]
            return result["name"]
        except IndexError:
            return None

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate ranking answer by checking if the correct instrument is named"""
        ground_truth = await self.get_ground_truth(validation_info)

        if ground_truth is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable",
            )

        answer_lower = answer.lower()
        expected_lower = ground_truth.lower()

        # Check for exact or partial match
        is_correct = expected_lower in answer_lower

        # Also check for common variations
        if not is_correct:
            # Handle special cases
            variations = {
                "alphabet": ["google", "googl"],
                "meta": ["facebook", "meta platforms"],
                "jpmorgan chase": ["jpmorgan", "jpm", "jp morgan"],
                "s&p 500": ["s&p", "spx", "sp500"],
                "dow jones": ["djia", "dji", "dow"],
                "nasdaq 100": ["nasdaq", "ndx"],
            }
            for name, alts in variations.items():
                if expected_lower == name or expected_lower in alts:
                    for alt in alts + [name]:
                        if alt in answer_lower:
                            is_correct = True
                            break

        return ValidationResult(
            score=1.0 if is_correct else 0.0,
            is_correct=is_correct,
            expected=ground_truth,
            actual=answer,
            details="Correct instrument identified" if is_correct else f"Expected: {ground_truth}",
        )
