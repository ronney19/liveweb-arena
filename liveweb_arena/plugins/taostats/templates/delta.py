"""Delta/difference query template for Taostats - High difficulty

Questions require calculating differences between two metrics,
which cannot be solved by sorting any single column.
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


class DeltaType(Enum):
    """Types of delta/difference queries"""
    # Largest gap between 24H and 1W change (momentum shift)
    MAX_24H_1W_GAP = ("max_24h_1w_gap", "24H与1W差距最大")
    # Smallest gap between 24H and 1W change (consistent trend)
    MIN_24H_1W_GAP = ("min_24h_1w_gap", "24H与1W差距最小")
    # Most improved: 24H much better than 1W
    MOST_IMPROVED = ("most_improved", "24H比1W改善最多")
    # Most declined: 24H much worse than 1W
    MOST_DECLINED = ("most_declined", "24H比1W恶化最多")

    def __init__(self, value: str, description: str):
        self._value_ = value
        self.description = description


@register_template("taostats_delta")
class DeltaTemplate(QuestionTemplate):
    """
    Template for delta/difference queries between timeframes.

    High difficulty: requires calculating difference between two columns.
    Cannot be solved by sorting - must compute delta for each row.
    """

    GT_SOURCE = GTSourceType.HYBRID

    PATTERNS: Dict[DeltaType, List[str]] = {
        DeltaType.MAX_24H_1W_GAP: [
            "Which subnet has the largest gap between its 24H and 1W price change? Check taostats.io.",
            "On taostats.io/subnets, find the subnet with biggest difference between 24H and 1W change.",
            "Which subnet shows the most divergent 24H vs 1W performance on taostats.io?",
        ],
        DeltaType.MIN_24H_1W_GAP: [
            "Which subnet has the smallest gap between its 24H and 1W price change? Check taostats.io.",
            "On taostats.io/subnets, find the subnet with smallest difference between 24H and 1W.",
            "Which subnet has the most consistent 24H vs 1W change on taostats.io?",
        ],
        DeltaType.MOST_IMPROVED: [
            "Which subnet improved the most from 1W to 24H? (24H change minus 1W change is highest) Check taostats.io.",
            "On taostats.io, find the subnet where 24H performance exceeds 1W by the largest margin.",
            "Which subnet shows the biggest improvement in 24H compared to its 1W trend?",
        ],
        DeltaType.MOST_DECLINED: [
            "Which subnet declined the most from 1W to 24H? (24H change minus 1W change is lowest) Check taostats.io.",
            "On taostats.io, find the subnet where 24H performance is worst compared to 1W.",
            "Which subnet shows the biggest decline in 24H compared to its 1W trend?",
        ],
    }

    def __init__(self):
        super().__init__("taostats_delta")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        delta_types = list(DeltaType)
        if variant is not None:
            delta_type = delta_types[variant % len(delta_types)]
        else:
            delta_type = rng.choice(delta_types)

        patterns = self.PATTERNS[delta_type]
        question_text = rng.choice(patterns)

        validation_info = {
            "delta_type": delta_type.value,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://taostats.io/subnets",
            variables={"delta_type": delta_type},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        delta_type = validation_info.get("delta_type", "")
        return f"""Task-Specific Rules (Delta Query: {delta_type}):
- Score 1.0: Correctly identifies the subnet
- Score 0.0: Wrong subnet or no clear answer"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Calculate ground truth from collected API data."""
        delta_type = validation_info.get("delta_type", "")

        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.fail("No GT collector")

        collected = gt_collector.get_collected_api_data()
        taostats_data = collected.get("taostats", {})
        subnets_data = taostats_data.get("subnets", {})

        from ..api_client import _filter_by_emission
        subnets_data = _filter_by_emission(subnets_data)

        if not subnets_data:
            return GroundTruthResult.fail("Taostats subnets data not collected")

        subnet_list = []
        for netuid, data in subnets_data.items():
            name = data.get("name", "")
            if not name or name.lower() == "unknown":
                continue

            change_24h = float(data.get("price_change_24h", 0) or 0)
            change_1w = float(data.get("price_change_1w", 0) or 0)

            # Calculate delta (24H - 1W)
            delta = change_24h - change_1w
            abs_delta = abs(delta)

            subnet_list.append({
                "name": name,
                "change_24h": change_24h,
                "change_1w": change_1w,
                "delta": delta,
                "abs_delta": abs_delta,
            })

        if not subnet_list:
            return GroundTruthResult.fail("No valid subnets found")

        # Sort based on delta type
        if delta_type == "max_24h_1w_gap":
            subnet_list.sort(key=lambda x: x["abs_delta"], reverse=True)
        elif delta_type == "min_24h_1w_gap":
            subnet_list.sort(key=lambda x: x["abs_delta"], reverse=False)
        elif delta_type == "most_improved":
            subnet_list.sort(key=lambda x: x["delta"], reverse=True)
        elif delta_type == "most_declined":
            subnet_list.sort(key=lambda x: x["delta"], reverse=False)
        else:
            return GroundTruthResult.fail(f"Unknown delta type: {delta_type}")

        return GroundTruthResult.ok(subnet_list[0]["name"])

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
            details=f"Expected {expected_name}",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> TriggerConfig:
        trigger = UrlPatternTrigger(domains=["taostats.io"])
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.LAST)

    @classmethod
    def get_cache_source(cls) -> str:
        return "taostats"

    def get_gt_source(self) -> GTSourceType:
        return self.GT_SOURCE
