"""Multi-condition query template for Taostats - High difficulty

Questions require checking multiple conditions simultaneously,
which cannot be solved by sorting a single column.
"""

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


class ConditionType(Enum):
    """Types of multi-condition queries"""
    # 24H positive AND 1W negative (reversal pattern)
    POSITIVE_24H_NEGATIVE_1W = ("positive_24h_negative_1w", "24H涨但1W跌")
    # 24H negative AND 1W positive (recovery pattern)
    NEGATIVE_24H_POSITIVE_1W = ("negative_24h_positive_1w", "24H跌但1W涨")
    # Both 24H and 1W positive (strong momentum)
    BOTH_POSITIVE = ("both_positive", "24H和1W都涨")
    # Both 24H and 1W negative (weak momentum)
    BOTH_NEGATIVE = ("both_negative", "24H和1W都跌")
    # High emission (>2%) but negative 24H
    HIGH_EMISSION_NEGATIVE = ("high_emission_negative", "高emission但24H跌")

    def __init__(self, value: str, description: str):
        self._value_ = value
        self.description = description


@register_template("taostats_multi_condition")
class MultiConditionTemplate(QuestionTemplate):
    """
    Template for multi-condition count queries.

    High difficulty: requires checking multiple columns simultaneously.
    Cannot be solved by sorting - must scan and evaluate each row.
    """

    GT_SOURCE = GTSourceType.HYBRID

    PATTERNS: Dict[ConditionType, List[str]] = {
        ConditionType.POSITIVE_24H_NEGATIVE_1W: [
            "How many subnets on taostats.io have positive 24H change but negative 1W change?",
            "Count subnets showing 24H gain but 1W loss on taostats.io/subnets.",
            "On taostats.io, how many subnets are up in the last 24 hours but down for the week?",
        ],
        ConditionType.NEGATIVE_24H_POSITIVE_1W: [
            "How many subnets on taostats.io have negative 24H change but positive 1W change?",
            "Count subnets showing 24H loss but 1W gain on taostats.io/subnets.",
            "On taostats.io, how many subnets are down today but still up for the week?",
        ],
        ConditionType.BOTH_POSITIVE: [
            "How many subnets have both positive 24H and positive 1W price changes? Check taostats.io.",
            "Count subnets on taostats.io that are up both in 24H and 1W timeframes.",
            "On taostats.io/subnets, how many subnets show gains in both 24H and 1W columns?",
        ],
        ConditionType.BOTH_NEGATIVE: [
            "How many subnets have both negative 24H and negative 1W price changes? Check taostats.io.",
            "Count subnets on taostats.io that are down in both 24H and 1W.",
            "On taostats.io/subnets, how many subnets show losses in both 24H and 1W columns?",
        ],
        ConditionType.HIGH_EMISSION_NEGATIVE: [
            "How many subnets with emission above 2% have negative 24H change? Check taostats.io.",
            "On taostats.io, count subnets with >2% emission that are down in 24H.",
            "Among high-emission (>2%) subnets on taostats.io, how many have negative 24H change?",
        ],
    }

    def __init__(self):
        super().__init__("taostats_multi_condition")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        conditions = list(ConditionType)
        if variant is not None:
            condition = conditions[variant % len(conditions)]
        else:
            condition = rng.choice(conditions)

        patterns = self.PATTERNS[condition]
        question_text = rng.choice(patterns)

        validation_info = {
            "condition_type": condition.value,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://taostats.io/subnets",
            variables={"condition": condition},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        condition = validation_info.get("condition_type", "")
        return f"""Task-Specific Rules (Multi-Condition Count: {condition}):
- Score 1.0: Exact count match
- Score 0.5: Count within ±3 of correct answer
- Score 0.0: Wrong count or no answer"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Calculate ground truth count from collected API data."""
        condition_type = validation_info.get("condition_type", "")

        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.fail("No GT collector")

        collected = gt_collector.get_collected_api_data()
        taostats_data = collected.get("taostats", {})
        subnets_data = taostats_data.get("subnets", {})

        from ..api_client import _normalize_emission
        subnets_data = _normalize_emission(subnets_data)

        if not subnets_data:
            return GroundTruthResult.fail("Taostats subnets data not collected")

        count = 0
        for netuid, data in subnets_data.items():
            name = data.get("name", "")
            if not name or name.lower() == "unknown":
                continue

            change_24h = float(data.get("price_change_24h", 0) or 0)
            change_1w = float(data.get("price_change_1w", 0) or 0)
            emission = float(data.get("emission", 0) or 0)

            match = False
            if condition_type == "positive_24h_negative_1w":
                match = change_24h > 0 and change_1w < 0
            elif condition_type == "negative_24h_positive_1w":
                match = change_24h < 0 and change_1w > 0
            elif condition_type == "both_positive":
                match = change_24h > 0 and change_1w > 0
            elif condition_type == "both_negative":
                match = change_24h < 0 and change_1w < 0
            elif condition_type == "high_emission_negative":
                match = emission > 2 and change_24h < 0

            if match:
                count += 1

        return GroundTruthResult.ok(str(count))

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

        expected_count = int(result.value)

        import re
        numbers = re.findall(r'\d+', answer)
        if not numbers:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=str(expected_count),
                actual=answer,
                details="No number found in answer",
            )

        actual_count = int(numbers[0])

        if actual_count == expected_count:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=str(expected_count),
                actual=answer,
                details="Exact count match",
            )
        elif abs(actual_count - expected_count) <= 3:
            return ValidationResult(
                score=0.5,
                is_correct=False,
                expected=str(expected_count),
                actual=answer,
                details=f"Close count (within ±3): expected {expected_count}, got {actual_count}",
            )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=str(expected_count),
            actual=answer,
            details=f"Wrong count: expected {expected_count}, got {actual_count}",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> TriggerConfig:
        trigger = UrlPatternTrigger(domains=["taostats.io"])
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.LAST)

    @classmethod
    def get_cache_source(cls) -> str:
        return "taostats"

    def get_gt_source(self) -> GTSourceType:
        return self.GT_SOURCE
