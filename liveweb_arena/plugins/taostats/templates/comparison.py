"""Subnet comparison template for Taostats"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, TriggerConfig, GroundTruthResult
)
from liveweb_arena.core.gt_collector import GTSourceType, get_current_gt_collector
from .variables import _fetch_active_subnet_ids, _fetch_subnet_name


class ComparisonMetric(Enum):
    """Metrics for subnet comparison - only metrics visible on taostats.io"""
    PRICE = "price"
    TAO_STAKED = "tao_staked"


def _get_subnet_pairs(rng: random.Random, count: int = 2) -> List[Tuple[int, str]]:
    """Dynamically fetch subnet IDs and names for comparison.

    Uses all active subnets (page shows ALL rows with ALL button).
    """
    subnet_ids = _fetch_active_subnet_ids()
    if len(subnet_ids) < count:
        return []

    selected_ids = rng.sample(subnet_ids, count)
    return [(sid, _fetch_subnet_name(sid) or f"Subnet {sid}") for sid in selected_ids]


@register_template("taostats_comparison")
class ComparisonTemplate(QuestionTemplate):
    """
    Template for comparing two subnets.

    Uses taostats API data for ground truth (bound to page cache).
    """

    GT_SOURCE = GTSourceType.HYBRID  # Uses collected API data from page visits

    PATTERNS: Dict[ComparisonMetric, List[str]] = {
        ComparisonMetric.PRICE: [
            "Between {subnet1} (SN{id1}) and {subnet2} (SN{id2}), which has a higher alpha price? Check taostats.io/subnets.",
            "Go to taostats.io/subnets and compare {subnet1} and {subnet2}. Which subnet has a higher price?",
            "Compare the alpha prices of {subnet1} and {subnet2} on taostats.io. Which is more expensive?",
            "Which subnet has the higher alpha token price: {subnet1} or {subnet2}?",
        ],
        ComparisonMetric.TAO_STAKED: [
            "Between {subnet1} (SN{id1}) and {subnet2} (SN{id2}), which has more TAO staked? Check taostats.io/subnets.",
            "Go to taostats.io/subnets and compare {subnet1} and {subnet2}. Which has higher TAO in?",
            "Compare {subnet1} and {subnet2}: which has more TAO deposited?",
            "Which subnet has attracted more TAO: {subnet1} or {subnet2}? Check taostats.io.",
        ],
    }

    def __init__(self):
        super().__init__("taostats_comparison")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """
        Generate a Taostats comparison question.

        Args:
            seed: Random seed for reproducible generation
            variant: Optional variant index for selecting comparison metric.
                     0=PRICE, 1=TAO_STAKED
        """
        rng = random.Random(seed)

        # Dynamically select two different subnets from top subnets
        selected = _get_subnet_pairs(rng, 2)
        if len(selected) < 2:
            raise RuntimeError("Could not fetch subnet data for comparison question generation")
        id1, name1 = selected[0]
        id2, name2 = selected[1]

        # Select metric (use variant if provided)
        metrics_list = list(ComparisonMetric)
        if variant is not None:
            metric = metrics_list[variant % len(metrics_list)]
        else:
            metric = rng.choice(metrics_list)
        patterns = self.PATTERNS[metric]
        pattern = rng.choice(patterns)

        question_text = pattern.format(
            subnet1=name1, id1=id1,
            subnet2=name2, id2=id2
        )

        validation_info = {
            "metric": metric.value,
            "subnet1_id": id1,
            "subnet1_name": name1,
            "subnet2_id": id2,
            "subnet2_name": name2,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://taostats.io/subnets",
            variables={"metric": metric, "subnets": selected},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        metric = validation_info.get("metric", "")
        name1 = validation_info.get("subnet1_name", "")
        name2 = validation_info.get("subnet2_name", "")

        metric_names = {
            "price": "Price",
            "tao_staked": "TAO Staked",
        }
        metric_display = metric_names.get(metric, metric)

        return f"""Task-Specific Rules ({metric_display} Comparison: {name1} vs {name2}):
- Score 1.0: Agent correctly identifies which subnet has higher {metric_display.lower()}
- Score 0.0: Wrong answer or no clear answer"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """
        Get ground truth by comparing two subnets from collected API data (no network fallback).

        Returns GroundTruthResult with the name of the subnet with higher value.
        """
        metric = validation_info.get("metric", "")
        id1 = validation_info.get("subnet1_id")
        id2 = validation_info.get("subnet2_id")
        name1 = validation_info.get("subnet1_name")
        name2 = validation_info.get("subnet2_name")

        # Get collected API data from GT collector
        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.fail("No GT collector")

        collected = gt_collector.get_collected_api_data()
        taostats_data = collected.get("taostats")
        if not taostats_data:
            return GroundTruthResult.fail("No taostats data collected — agent may not have visited taostats.io")
        subnets = taostats_data.get("subnets")
        if not subnets:
            return GroundTruthResult.fail("No subnets data in taostats collection")

        data1 = subnets.get(str(id1), {})
        data2 = subnets.get(str(id2), {})

        if not data1:
            return GroundTruthResult.fail(
                f"Subnet {id1} ({name1}) not found in collected data"
            )
        if not data2:
            return GroundTruthResult.fail(
                f"Subnet {id2} ({name2}) not found in collected data"
            )

        # Get values based on metric (explicit None check)
        metric_field = {"price": "price", "tao_staked": "tao_in"}.get(metric)
        if metric_field is None:
            return GroundTruthResult.fail(f"Unknown metric: {metric}")

        raw1 = data1.get(metric_field)
        raw2 = data2.get(metric_field)
        if raw1 is None:
            return GroundTruthResult.system_error(
                f"Missing '{metric_field}' for subnet {id1} ({name1})"
            )
        if raw2 is None:
            return GroundTruthResult.system_error(
                f"Missing '{metric_field}' for subnet {id2} ({name2})"
            )
        val1 = float(raw1)
        val2 = float(raw2)

        # Return name of subnet with higher value (handle ties)
        if val1 == val2:
            return GroundTruthResult.ok(f"TIE: {name1} and {name2} (both {val1})")
        return GroundTruthResult.ok(name1 if val1 > val2 else name2)

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate comparison answer"""
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
        name1 = validation_info.get("subnet1_name", "")
        name2 = validation_info.get("subnet2_name", "")
        answer_lower = answer.lower()

        # Handle tie: either subnet name is acceptable
        if ground_truth.startswith("TIE:"):
            if name1.lower() in answer_lower or name2.lower() in answer_lower:
                return ValidationResult(
                    score=1.0, is_correct=True, expected=ground_truth,
                    actual=answer, details="Tie - either answer accepted",
                )
            return ValidationResult(
                score=0.0, is_correct=False, expected=ground_truth,
                actual=answer, details="Tie but neither subnet mentioned",
            )

        # Check if correct subnet is mentioned
        if ground_truth.lower() in answer_lower:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=answer,
                details="Correct subnet identified",
            )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=ground_truth,
            actual=answer,
            details=f"Expected {ground_truth}",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> tuple:
        """Comparison: LAST for multi-page browsing."""
        trigger = UrlPatternTrigger(domains=["taostats.io"])
        return TriggerConfig(trigger=trigger)

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "taostats"

    def get_gt_source(self) -> GTSourceType:
        """Return GT source type."""
        return self.GT_SOURCE
