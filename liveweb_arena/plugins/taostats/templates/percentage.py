"""Percentage calculation template for Taostats - High difficulty

Questions require calculating percentages across all subnets,
which cannot be solved by sorting - requires counting and division.
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


class PercentageType(Enum):
    """Types of percentage queries"""
    # Percentage of subnets with positive 24H change
    POSITIVE_24H_PCT = ("positive_24h_pct", "price_change_24h", ">", 0)
    # Percentage of subnets with negative 24H change
    NEGATIVE_24H_PCT = ("negative_24h_pct", "price_change_24h", "<", 0)
    # Percentage of subnets with positive 1W change
    POSITIVE_1W_PCT = ("positive_1w_pct", "price_change_1w", ">", 0)
    # Percentage of subnets with negative 1W change
    NEGATIVE_1W_PCT = ("negative_1w_pct", "price_change_1w", "<", 0)
    # Percentage of subnets with emission > 1%
    HIGH_EMISSION_PCT = ("high_emission_pct", "emission", ">", 1)

    def __init__(self, key: str, field: str, operator: str, threshold: float):
        self._value_ = key
        self.field = field
        self.operator = operator
        self.threshold = threshold


@register_template("taostats_percentage")
class PercentageTemplate(QuestionTemplate):
    """
    Template for percentage calculation queries.

    High difficulty: requires counting matching items and calculating percentage.
    Cannot be solved by sorting - requires aggregation across all rows.
    """

    GT_SOURCE = GTSourceType.HYBRID

    PATTERNS: Dict[PercentageType, List[str]] = {
        PercentageType.POSITIVE_24H_PCT: [
            "What percentage of subnets on taostats.io have positive 24H price change?",
            "On taostats.io/subnets, what percent of subnets are up in the last 24 hours?",
            "Calculate the percentage of subnets showing gains in 24H on taostats.io.",
        ],
        PercentageType.NEGATIVE_24H_PCT: [
            "What percentage of subnets on taostats.io have negative 24H price change?",
            "On taostats.io/subnets, what percent of subnets are down in the last 24 hours?",
            "Calculate the percentage of subnets showing losses in 24H on taostats.io.",
        ],
        PercentageType.POSITIVE_1W_PCT: [
            "What percentage of subnets on taostats.io have positive 1W price change?",
            "On taostats.io/subnets, what percent of subnets are up for the week?",
            "Calculate the percentage of subnets showing weekly gains on taostats.io.",
        ],
        PercentageType.NEGATIVE_1W_PCT: [
            "What percentage of subnets on taostats.io have negative 1W price change?",
            "On taostats.io/subnets, what percent of subnets are down for the week?",
            "Calculate the percentage of subnets showing weekly losses on taostats.io.",
        ],
        PercentageType.HIGH_EMISSION_PCT: [
            "What percentage of subnets on taostats.io have emission above 1%?",
            "On taostats.io/subnets, what percent of subnets have >1% emission?",
            "Calculate the percentage of high-emission (>1%) subnets on taostats.io.",
        ],
    }

    def __init__(self):
        super().__init__("taostats_percentage")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        pct_types = list(PercentageType)
        if variant is not None:
            pct_type = pct_types[variant % len(pct_types)]
        else:
            pct_type = rng.choice(pct_types)

        patterns = self.PATTERNS[pct_type]
        question_text = rng.choice(patterns)

        validation_info = {
            "pct_type": pct_type.value,
            "field": pct_type.field,
            "operator": pct_type.operator,
            "threshold": pct_type.threshold,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://taostats.io/subnets",
            variables={"pct_type": pct_type},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        field = validation_info.get("field", "")
        operator = validation_info.get("operator", "")
        threshold = validation_info.get("threshold", 0)

        return f"""Task-Specific Rules (Percentage: {field} {operator} {threshold}):
- Score 1.0: Percentage within ±5% of correct answer
- Score 0.5: Percentage within ±10% of correct answer
- Score 0.0: Wrong percentage or no answer"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Calculate ground truth percentage from collected API data."""
        field = validation_info.get("field", "")
        operator = validation_info.get("operator", "")
        threshold = validation_info.get("threshold", 0)

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

        total_count = 0
        match_count = 0

        for netuid, data in subnets_data.items():
            name = data.get("name", "")
            if not name or name.lower() == "unknown":
                continue

            total_count += 1

            value = data.get(field, 0)
            if value is None:
                value = 0
            value = float(value)

            match = False
            if operator == ">":
                match = value > threshold
            elif operator == "<":
                match = value < threshold
            elif operator == ">=":
                match = value >= threshold
            elif operator == "<=":
                match = value <= threshold

            if match:
                match_count += 1

        if total_count == 0:
            return GroundTruthResult.fail("No valid subnets found")

        percentage = (match_count / total_count) * 100
        return GroundTruthResult.ok(f"{percentage:.1f}")

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

        expected_pct = float(result.value)

        import re
        # Match percentages like "45%", "45.5%", "45.5", etc.
        numbers = re.findall(r'(\d+(?:\.\d+)?)\s*%?', answer)
        if not numbers:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=f"{expected_pct:.1f}%",
                actual=answer,
                details="No percentage found in answer",
            )

        # Find the most likely percentage value (should be between 0-100)
        actual_pct = None
        for n in numbers:
            try:
                val = float(n)
                if 0 <= val <= 100:
                    actual_pct = val
                    break
            except ValueError:
                continue

        if actual_pct is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=f"{expected_pct:.1f}%",
                actual=answer,
                details="No valid percentage (0-100) found in answer",
            )

        diff = abs(actual_pct - expected_pct)

        if diff <= 5:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=f"{expected_pct:.1f}%",
                actual=f"{actual_pct:.1f}%",
                details=f"Percentage within ±5%: expected {expected_pct:.1f}%, got {actual_pct:.1f}%",
            )
        elif diff <= 10:
            return ValidationResult(
                score=0.5,
                is_correct=False,
                expected=f"{expected_pct:.1f}%",
                actual=f"{actual_pct:.1f}%",
                details=f"Percentage within ±10%: expected {expected_pct:.1f}%, got {actual_pct:.1f}%",
            )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=f"{expected_pct:.1f}%",
            actual=f"{actual_pct:.1f}%",
            details=f"Wrong percentage: expected {expected_pct:.1f}%, got {actual_pct:.1f}%",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> TriggerConfig:
        trigger = UrlPatternTrigger(domains=["taostats.io"])
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.LAST)

    @classmethod
    def get_cache_source(cls) -> str:
        return "taostats"

    def get_gt_source(self) -> GTSourceType:
        return self.GT_SOURCE
