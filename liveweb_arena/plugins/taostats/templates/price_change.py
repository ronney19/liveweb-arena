"""Price change ranking template for Taostats - Higher difficulty"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
)
from liveweb_arena.core.gt_collector import GTSourceType, get_current_gt_collector


class TimeFrame(Enum):
    """Time frames for price change queries"""
    HOUR_1 = ("1h", "1H", "1-hour", "price_change_1h")
    HOUR_24 = ("24h", "24H", "24-hour", "price_change_24h")
    WEEK_1 = ("1w", "1W", "1-week", "price_change_1w")
    MONTH_1 = ("1m", "1M", "1-month", "price_change_1m")

    def __init__(self, short: str, column: str, display: str, api_field: str):
        self.short = short
        self.column = column
        self.display = display
        self.api_field = api_field


class Direction(Enum):
    """Direction for ranking (gainers vs losers)"""
    GAINER = ("gainer", "highest", True)
    LOSER = ("loser", "lowest", False)

    def __init__(self, name: str, adjective: str, descending: bool):
        self.dir_name = name
        self.adjective = adjective
        self.descending = descending


@register_template("taostats_price_change")
class PriceChangeTemplate(QuestionTemplate):
    """
    Template for price change ranking queries.

    Higher difficulty: requires finding subnets by price change percentage.
    Agent must look at the correct time column (1H, 24H, 1W, 1M) and rank subnets.
    """

    GT_SOURCE = GTSourceType.HYBRID

    PATTERNS: List[str] = [
        "Which subnet has the {adjective} {timeframe} price change on taostats.io/subnets?",
        "Find the top {direction} by {timeframe} price change on taostats.io.",
        "On taostats.io/subnets, which subnet {verb} the most in {timeframe}?",
        "What subnet shows the {adjective} {timeframe} change on taostats.io?",
        "Check taostats.io/subnets - which subnet is the biggest {direction} over {timeframe}?",
    ]

    def __init__(self):
        super().__init__("taostats_price_change")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        # Select timeframe and direction
        timeframes = list(TimeFrame)
        directions = list(Direction)

        if variant is not None:
            timeframe = timeframes[variant % len(timeframes)]
            direction = directions[(variant // len(timeframes)) % len(directions)]
        else:
            timeframe = rng.choice(timeframes)
            direction = rng.choice(directions)

        # Generate question text
        pattern = rng.choice(self.PATTERNS)
        verb = "gained" if direction == Direction.GAINER else "lost"

        question_text = pattern.format(
            adjective=direction.adjective,
            timeframe=timeframe.display,
            direction=direction.dir_name,
            verb=verb,
        )

        validation_info = {
            "timeframe": timeframe.api_field,
            "timeframe_display": timeframe.display,
            "direction": direction.dir_name,
            "descending": direction.descending,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://taostats.io/subnets",
            variables={"timeframe": timeframe, "direction": direction},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        timeframe = validation_info.get("timeframe_display", "")
        direction = validation_info.get("direction", "")

        return f"""Task-Specific Rules (Top {direction.title()} by {timeframe} Price Change):
- Score 1.0: Agent correctly identifies the subnet with the {direction} {timeframe} price change
- Score 0.0: Wrong subnet or no clear answer"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Calculate ground truth from collected API data."""
        timeframe_field = validation_info.get("timeframe", "price_change_24h")
        descending = validation_info.get("descending", True)

        # Get collected API data
        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.fail("No GT collector")

        collected = gt_collector.get_collected_api_data()
        taostats_data = collected.get("taostats", {})
        subnets_data = taostats_data.get("subnets", {})

        from ..api_client import _filter_by_emission
        subnets_data = _filter_by_emission(subnets_data)

        if not subnets_data:
            return GroundTruthResult.fail(
                f"Taostats subnets data not collected. "
                f"Available keys: {list(collected.keys())[:10]}"
            )

        # Build and sort subnet list by price change
        subnet_list = []
        for netuid, data in subnets_data.items():
            name = data.get("name", "")
            # Skip subnets without meaningful names
            if not name or name.lower() == "unknown":
                continue

            change = data.get(timeframe_field, 0)
            if change is None:
                change = 0

            subnet_list.append({
                "netuid": netuid,
                "name": name,
                "change": float(change),
            })

        if not subnet_list:
            return GroundTruthResult.fail("No valid subnets found")

        # Sort by change
        subnet_list.sort(key=lambda x: x["change"], reverse=descending)

        # Return top result
        top_subnet = subnet_list[0]
        return GroundTruthResult.ok(top_subnet["name"])

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        result = await self.get_ground_truth(validation_info)

        if not result.success:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        expected_name = result.value
        answer_lower = answer.lower()

        if expected_name.lower() in answer_lower:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=expected_name,
                actual=answer,
                details="Correct subnet identified",
            )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=expected_name,
            actual=answer,
            details=f"Expected {expected_name} as top subnet",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> TriggerConfig:
        trigger = UrlPatternTrigger(domains=["taostats.io"])
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.LAST)

    @classmethod
    def get_cache_source(cls) -> str:
        return "taostats"

    def get_gt_source(self) -> GTSourceType:
        return self.GT_SOURCE
