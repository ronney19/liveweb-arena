"""Multi-instrument ranking template for Stooq - complex questions requiring multiple data points"""

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

    GT_SOURCE = GTSourceType.API_ONLY  # Multi-instrument ranking

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

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """
        Generate a Stooq ranking question.

        Args:
            seed: Random seed for reproducible generation
            variant: Optional variant index for selecting ranking metric.
                     0=CHANGE_PERCENT, 1=CURRENT_PRICE, 2=WEEK52_GAIN, 3=DISTANCE_FROM_HIGH
        """
        rng = random.Random(seed)

        # Select instrument group
        group_key = rng.choice(list(INSTRUMENT_GROUPS.keys()))
        group_desc, instruments = INSTRUMENT_GROUPS[group_key]

        # Select metric (use variant if provided)
        metrics_list = list(RankingMetric)
        if variant is not None:
            metric = metrics_list[variant % len(metrics_list)]
        else:
            metric = rng.choice(metrics_list)

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

    async def _fetch_instrument_data(self, symbol: str, metric: str) -> GroundTruthResult:
        """Fetch data for a single instrument from collected API data (no network fallback).

        Note: Only current_price and change_percent metrics are supported.
        week52_gain and distance_from_high require historical data not available in collected cache.
        """
        # Check if metric requires historical data
        if metric in ["week52_gain", "distance_from_high"]:
            return GroundTruthResult.fail(
                f"Metric '{metric}' requires historical data not available in collected cache. "
                "Only 'current_price' and 'change_percent' are supported in cache mode."
            )

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

        current_price = self._parse_float(data.get("close"))
        change_percent = self._parse_float(data.get("daily_change_pct"))

        if current_price is None:
            return GroundTruthResult.fail(f"Could not parse price for {symbol}")

        return GroundTruthResult.ok({
            "symbol": symbol,
            "current_price": current_price,
            "change_percent": change_percent,
            "week52_gain": None,  # Not available in collected data
            "distance_from_high": None,  # Not available in collected data
        })

    def _parse_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """
        Calculate ground truth by fetching all instruments and ranking them.

        Returns GroundTruthResult with the name of the instrument at the specified ranking position.

        Note: In cache mode, only 'current_price' and 'change_percent' metrics are supported.
        Metrics requiring historical data (week52_gain, distance_from_high) will fail.
        """
        instruments = validation_info.get("instruments", [])
        metric = validation_info.get("metric", "change_percent")
        direction = validation_info.get("direction", "highest")
        position = validation_info.get("position", "1st")

        if not instruments:
            return GroundTruthResult.fail("No instruments provided")

        # Check if metric requires historical data
        if metric in ["week52_gain", "distance_from_high"]:
            return GroundTruthResult.fail(
                f"Metric '{metric}' requires historical data not available in collected cache. "
                "Only 'current_price' and 'change_percent' are supported in cache mode."
            )

        all_data = []
        errors = []
        for symbol, name in instruments:
            result = await self._fetch_instrument_data(symbol, metric)
            if result.success:
                data = result.value
                data["name"] = name
                all_data.append(data)
            else:
                errors.append(f"{symbol}: {result.error}")

        if len(all_data) < len(instruments):
            return GroundTruthResult.fail(
                f"Could not fetch data for all instruments. Errors: {'; '.join(errors)}"
            )

        def get_metric_value(d):
            return d.get(metric)

        valid_data = [d for d in all_data if get_metric_value(d) is not None]
        if len(valid_data) < len(instruments):
            return GroundTruthResult.fail(f"Missing {metric} data for some instruments")

        reverse = (direction == "highest")
        sorted_data = sorted(valid_data, key=lambda d: get_metric_value(d), reverse=reverse)

        position_map = {"1st": 0, "2nd": 1, "3rd": 2, "last": -1, "2nd last": -2}
        idx = position_map.get(position, 0)

        try:
            result = sorted_data[idx]
            return GroundTruthResult.ok(result["name"])
        except IndexError:
            return GroundTruthResult.fail(f"Invalid position: {position}")

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate ranking answer by checking if the correct instrument is named"""
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

    def get_ground_truth_trigger(self, validation_info: dict) -> tuple:
        """
        Ranking: AI visits multiple stock pages, use LAST.
        """
        trigger = UrlPatternTrigger(domains=["stooq.com"])
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.LAST)

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "stooq"

    def get_gt_source(self):
        """
        Ranking requires sorting multiple instruments by various metrics.
        Use API_ONLY for consistent ranking data.
        """
        from liveweb_arena.core.gt_collector import GTSourceType
        return GTSourceType.API_ONLY
