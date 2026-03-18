"""Intraday volatility and range position templates for Stooq - high difficulty derived metrics"""

import random
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, TriggerConfig, GroundTruthResult,
)
from liveweb_arena.core.gt_collector import GTSourceType

from .sector_analysis import ALL_STOCKS


def _name_matches(answer: str, expected: str) -> bool:
    """Check if expected name appears in answer, including common variations."""
    answer_lower = answer.lower()
    expected_lower = expected.lower()

    if expected_lower in answer_lower:
        return True

    variations = {
        "alphabet": ["google", "googl"],
        "meta": ["facebook", "meta platforms"],
        "jpmorgan": ["jpmorgan chase", "jpm", "jp morgan"],
        "ge aerospace": ["general electric", "ge"],
        "exxon mobil": ["exxon", "exxonmobil"],
        "johnson & johnson": ["j&j", "jnj"],
        "eli lilly": ["lilly"],
        "unitedhealth": ["unitedhealth group", "unh"],
        "bank of america": ["bofa", "bac"],
        "charles schwab": ["schwab"],
        "mcdonald's": ["mcdonalds", "mcd"],
    }
    for name, alts in variations.items():
        if expected_lower == name or expected_lower in alts:
            for alt in alts + [name]:
                if alt in answer_lower:
                    return True
    return False


def _fetch_instrument_fields(symbol: str) -> GroundTruthResult:
    """Fetch high/low/close from collected API data for a symbol."""
    from liveweb_arena.core.gt_collector import get_current_gt_collector
    gt_collector = get_current_gt_collector()
    if gt_collector is None:
        return GroundTruthResult.system_error("No GT collector")

    collected = gt_collector.get_collected_api_data()
    data = collected.get(symbol) or collected.get(symbol.lower())
    if not data:
        return GroundTruthResult.fail(
            f"Stooq data for '{symbol}' not collected. "
            f"Available: {list(collected.keys())[:10]}"
        )

    def _pf(v):
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    high = _pf(data.get("high"))
    low = _pf(data.get("low"))
    close = _pf(data.get("close"))

    if high is None or low is None or close is None:
        return GroundTruthResult.fail(
            f"Missing high/low/close for {symbol}: h={data.get('high')} l={data.get('low')} c={data.get('close')}"
        )

    return GroundTruthResult.ok({"high": high, "low": low, "close": close})


@register_template("stooq_volatility")
class StooqVolatilityTemplate(QuestionTemplate):
    """
    Template: which stock has the widest/narrowest intraday price spread?

    Metric: (high - low) / close * 100
    Difficulty: Hard — requires extracting 3 fields per stock from multiple pages,
    then computing a derived metric no page directly displays.
    """

    GT_SOURCE = GTSourceType.API_ONLY

    PATTERNS = {
        "widest": [
            "Among {instruments}, which stock has the widest intraday price spread as a percentage of its closing price today?",
            "Looking at {instruments}, which one has the largest difference between today's high and low relative to its close?",
            "Which of {instruments} shows the biggest intraday price range as a percentage of close today?",
            "Of {instruments}, which has the highest (high minus low) divided by close percentage today?",
        ],
        "narrowest": [
            "Among {instruments}, which stock has the narrowest intraday price spread as a percentage of its closing price today?",
            "Looking at {instruments}, which one has the smallest difference between today's high and low relative to its close?",
            "Which of {instruments} shows the tightest intraday price range as a percentage of close today?",
            "Of {instruments}, which has the lowest (high minus low) divided by close percentage today?",
        ],
    }

    def __init__(self):
        super().__init__("stooq_volatility")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        shuffled = ALL_STOCKS.copy()
        rng.shuffle(shuffled)
        instruments = shuffled[:5]

        direction = rng.choice(["widest", "narrowest"])

        names = [inst[1] for inst in instruments]
        instruments_str = ", ".join(names[:-1]) + ", and " + names[-1]

        pattern = rng.choice(self.PATTERNS[direction])
        question_text = pattern.format(instruments=instruments_str)

        expected_steps = len(instruments) * 2 + 5

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://stooq.com/",
            variables={
                "instruments": instruments,
                "direction": direction,
            },
            validation_info={
                "instruments": instruments,
                "direction": direction,
            },
            template_name=self.name,
            expected_steps=expected_steps,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        instruments = validation_info.get("instruments", [])
        direction = validation_info.get("direction", "widest")
        names = [inst[1] for inst in instruments]

        return f"""Task-Specific Rules (Volatility: {direction} intraday spread):
Instruments: {', '.join(names)}

Metric: (high - low) / close * 100
- Score 1.0: Correctly identifies the stock with the {direction} spread
- Score 0.0: Wrong stock or unable to determine

The agent must:
1. Visit each stock's page on stooq.com
2. Collect high, low, and close for each
3. Compute (high - low) / close * 100 for each
4. Identify the {direction} spread"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        instruments = validation_info["instruments"]
        direction = validation_info["direction"]

        if not instruments:
            return GroundTruthResult.fail("No instruments provided")

        spreads = []
        errors = []
        for symbol, name in instruments:
            result = _fetch_instrument_fields(symbol)
            if not result.success:
                errors.append(f"{symbol}: {result.error}")
                continue
            d = result.value
            spread = (d["high"] - d["low"]) / d["close"] * 100
            spreads.append((name, spread))

        if len(spreads) < len(instruments):
            return GroundTruthResult.fail(
                f"Could not fetch all instruments. Errors: {'; '.join(errors)}"
            )

        reverse = (direction == "widest")
        spreads.sort(key=lambda x: x[1], reverse=reverse)
        return GroundTruthResult.ok(spreads[0][0])

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        result = await self.get_ground_truth(validation_info)

        if not result.success:
            return ValidationResult(
                score=0.0, is_correct=False, expected=None, actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        expected = result.value
        is_correct = _name_matches(answer, expected)

        return ValidationResult(
            score=1.0 if is_correct else 0.0,
            is_correct=is_correct,
            expected=expected,
            actual=answer,
            details="Correct stock identified" if is_correct else f"Expected: {expected}",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> TriggerConfig:
        return TriggerConfig(
            trigger=UrlPatternTrigger(domains=["stooq.com"]),
        )

    @classmethod
    def get_cache_source(cls) -> str:
        return "stooq"

    def get_gt_source(self):
        return GTSourceType.API_ONLY


@register_template("stooq_range_position")
class StooqRangePositionTemplate(QuestionTemplate):
    """
    Template: which stock is trading closest to its daily high/low?

    Metric: (close - low) / (high - low)  (0=at low, 1=at high)
    Edge case: high == low -> range_pos = 0.5
    Difficulty: Hard — same 3-field extraction + derived computation.
    """

    GT_SOURCE = GTSourceType.API_ONLY

    PATTERNS = {
        "closest_to_high": [
            "Among {instruments}, which stock is trading closest to its daily high today?",
            "Looking at {instruments}, which one closed nearest to its intraday high?",
            "Which of {instruments} finished the day closest to its highest price today?",
            "Of {instruments}, which stock's closing price is nearest its daily high?",
        ],
        "closest_to_low": [
            "Among {instruments}, which stock is trading closest to its daily low today?",
            "Looking at {instruments}, which one closed nearest to its intraday low?",
            "Which of {instruments} finished the day closest to its lowest price today?",
            "Of {instruments}, which stock's closing price is nearest its daily low?",
        ],
    }

    def __init__(self):
        super().__init__("stooq_range_position")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        shuffled = ALL_STOCKS.copy()
        rng.shuffle(shuffled)
        instruments = shuffled[:5]

        direction = rng.choice(["closest_to_high", "closest_to_low"])

        names = [inst[1] for inst in instruments]
        instruments_str = ", ".join(names[:-1]) + ", and " + names[-1]

        pattern = rng.choice(self.PATTERNS[direction])
        question_text = pattern.format(instruments=instruments_str)

        expected_steps = len(instruments) * 2 + 5

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://stooq.com/",
            variables={
                "instruments": instruments,
                "direction": direction,
            },
            validation_info={
                "instruments": instruments,
                "direction": direction,
            },
            template_name=self.name,
            expected_steps=expected_steps,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        instruments = validation_info.get("instruments", [])
        direction = validation_info.get("direction", "closest_to_high")
        names = [inst[1] for inst in instruments]
        target = "high" if direction == "closest_to_high" else "low"

        return f"""Task-Specific Rules (Range Position: closest to daily {target}):
Instruments: {', '.join(names)}

Metric: (close - low) / (high - low)  (0=at low, 1=at high)
- Score 1.0: Correctly identifies the stock closest to its daily {target}
- Score 0.0: Wrong stock or unable to determine

The agent must:
1. Visit each stock's page on stooq.com
2. Collect high, low, and close for each
3. Compute range position for each
4. Identify which is closest to the daily {target}"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        instruments = validation_info["instruments"]
        direction = validation_info["direction"]

        if not instruments:
            return GroundTruthResult.fail("No instruments provided")

        positions = []
        errors = []
        for symbol, name in instruments:
            result = _fetch_instrument_fields(symbol)
            if not result.success:
                errors.append(f"{symbol}: {result.error}")
                continue
            d = result.value
            if d["high"] == d["low"]:
                range_pos = 0.5
            else:
                range_pos = (d["close"] - d["low"]) / (d["high"] - d["low"])
            positions.append((name, range_pos))

        if len(positions) < len(instruments):
            return GroundTruthResult.fail(
                f"Could not fetch all instruments. Errors: {'; '.join(errors)}"
            )

        if direction == "closest_to_high":
            positions.sort(key=lambda x: x[1], reverse=True)
        else:
            positions.sort(key=lambda x: x[1])

        return GroundTruthResult.ok(positions[0][0])

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        result = await self.get_ground_truth(validation_info)

        if not result.success:
            return ValidationResult(
                score=0.0, is_correct=False, expected=None, actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        expected = result.value
        is_correct = _name_matches(answer, expected)

        return ValidationResult(
            score=1.0 if is_correct else 0.0,
            is_correct=is_correct,
            expected=expected,
            actual=answer,
            details="Correct stock identified" if is_correct else f"Expected: {expected}",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> TriggerConfig:
        return TriggerConfig(
            trigger=UrlPatternTrigger(domains=["stooq.com"]),
        )

    @classmethod
    def get_cache_source(cls) -> str:
        return "stooq"

    def get_gt_source(self):
        return GTSourceType.API_ONLY
