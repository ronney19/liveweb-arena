"""Range count query template for Taostats - High difficulty

Questions require counting subnets within a specific range,
which cannot be solved by sorting any single column.
"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
)
from liveweb_arena.core.gt_collector import GTSourceType, get_current_gt_collector


class RangeType(Enum):
    """Types of range queries with (min, max) bounds"""
    # 24H change between -5% and 5% (stable)
    STABLE_24H = ("stable_24h", "price_change_24h", -5, 5, "24H change between -5% and 5%")
    # 24H change between -10% and -5% (moderate loss)
    MODERATE_LOSS_24H = ("moderate_loss_24h", "price_change_24h", -10, -5, "24H change between -10% and -5%")
    # 24H change between 5% and 10% (moderate gain)
    MODERATE_GAIN_24H = ("moderate_gain_24h", "price_change_24h", 5, 10, "24H change between 5% and 10%")
    # 1W change between -20% and -10%
    SIGNIFICANT_LOSS_1W = ("significant_loss_1w", "price_change_1w", -20, -10, "1W change between -20% and -10%")
    # Emission between 1% and 3%
    MID_EMISSION = ("mid_emission", "emission", 1, 3, "emission between 1% and 3%")

    def __init__(self, key: str, field: str, min_val: float, max_val: float, description: str):
        self._value_ = key
        self.field = field
        self.min_val = min_val
        self.max_val = max_val
        self.description = description


@register_template("taostats_range_count")
class RangeCountTemplate(QuestionTemplate):
    """
    Template for range-based count queries.

    High difficulty: requires checking if values fall within a range.
    Cannot be solved by sorting - must evaluate each row against bounds.
    """

    GT_SOURCE = GTSourceType.HYBRID

    PATTERNS: List[str] = [
        "How many subnets on taostats.io have {description}?",
        "Count the subnets with {description} on taostats.io/subnets.",
        "On taostats.io, how many subnets show {description}?",
        "Find the count of subnets where {description}. Check taostats.io.",
    ]

    def __init__(self):
        super().__init__("taostats_range_count")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        range_types = list(RangeType)
        if variant is not None:
            range_type = range_types[variant % len(range_types)]
        else:
            range_type = rng.choice(range_types)

        pattern = rng.choice(self.PATTERNS)
        question_text = pattern.format(description=range_type.description)

        validation_info = {
            "range_type": range_type.value,
            "field": range_type.field,
            "min_val": range_type.min_val,
            "max_val": range_type.max_val,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://taostats.io/subnets",
            variables={"range_type": range_type},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        min_val = validation_info.get("min_val", 0)
        max_val = validation_info.get("max_val", 0)
        field = validation_info.get("field", "")

        return f"""Task-Specific Rules (Range Count: {field} in [{min_val}, {max_val}]):
- Score 1.0: Exact count match
- Score 0.5: Count within ±2 of correct answer
- Score 0.0: Wrong count or no answer"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Calculate ground truth count from collected API data."""
        field = validation_info.get("field", "")
        min_val = validation_info.get("min_val", 0)
        max_val = validation_info.get("max_val", 0)

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

            value = data.get(field, 0)
            if value is None:
                value = 0
            value = float(value)

            # Check if value is within range (inclusive)
            if min_val <= value <= max_val:
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
        elif abs(actual_count - expected_count) <= 2:
            return ValidationResult(
                score=0.5,
                is_correct=False,
                expected=str(expected_count),
                actual=answer,
                details=f"Close count (within ±2): expected {expected_count}, got {actual_count}",
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
