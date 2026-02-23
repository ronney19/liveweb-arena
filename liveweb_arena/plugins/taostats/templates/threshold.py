"""Threshold query template for Taostats - Higher difficulty"""

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


class ThresholdMetric(Enum):
    """Metrics for threshold queries"""
    PRICE_CHANGE_24H = ("24H", "price_change_24h", "%")
    PRICE_CHANGE_1W = ("1W", "price_change_1w", "%")
    EMISSION = ("emission", "emission", "%")

    def __init__(self, display: str, api_field: str, unit: str):
        self.display = display
        self.api_field = api_field
        self.unit = unit


class ThresholdDirection(Enum):
    """Direction for threshold comparison"""
    ABOVE = ("above", "greater than", "more than", lambda x, t: x > t)
    BELOW = ("below", "less than", "under", lambda x, t: x < t)

    def __init__(self, name: str, phrase1: str, phrase2: str, compare_fn):
        self.dir_name = name
        self.phrase1 = phrase1
        self.phrase2 = phrase2
        self.compare = compare_fn


@register_template("taostats_threshold")
class ThresholdTemplate(QuestionTemplate):
    """
    Template for threshold-based count queries.

    Higher difficulty: requires counting subnets that meet a threshold condition.
    Example: "How many subnets have 24H change above 5%?"
    """

    GT_SOURCE = GTSourceType.HYBRID

    # Threshold values to use (reasonable ranges for each metric)
    THRESHOLDS = {
        "price_change_24h": [1, 2, 5, -1, -2, -5],
        "price_change_1w": [5, 10, 20, -5, -10],
        "emission": [1, 2, 3, 5],
    }

    PATTERNS: List[str] = [
        "How many subnets on taostats.io have {metric} {direction} {threshold}{unit}?",
        "Count the subnets with {metric} change {phrase} {threshold}{unit} on taostats.io.",
        "On taostats.io/subnets, how many subnets show {metric} {direction} {threshold}{unit}?",
        "What is the count of subnets with {metric} {phrase} {threshold}{unit}?",
    ]

    def __init__(self):
        super().__init__("taostats_threshold")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        # Select metric and direction
        metrics = list(ThresholdMetric)
        directions = list(ThresholdDirection)

        if variant is not None:
            metric = metrics[variant % len(metrics)]
            direction = directions[(variant // len(metrics)) % len(directions)]
        else:
            metric = rng.choice(metrics)
            direction = rng.choice(directions)

        # Select appropriate threshold
        thresholds = self.THRESHOLDS.get(metric.api_field, [5])
        # For BELOW direction, prefer negative thresholds for price changes
        if direction == ThresholdDirection.BELOW and "price_change" in metric.api_field:
            negative_thresholds = [t for t in thresholds if t < 0]
            if negative_thresholds:
                thresholds = negative_thresholds
        threshold = rng.choice(thresholds)

        # Generate question text
        pattern = rng.choice(self.PATTERNS)
        question_text = pattern.format(
            metric=metric.display,
            direction=direction.dir_name,
            phrase=rng.choice([direction.phrase1, direction.phrase2]),
            threshold=threshold,
            unit=metric.unit,
        )

        validation_info = {
            "metric": metric.api_field,
            "metric_display": metric.display,
            "threshold": threshold,
            "direction": direction.dir_name,
            "unit": metric.unit,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://taostats.io/subnets",
            variables={"metric": metric, "direction": direction, "threshold": threshold},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        metric = validation_info.get("metric_display", "")
        threshold = validation_info.get("threshold", 0)
        direction = validation_info.get("direction", "")
        unit = validation_info.get("unit", "")

        return f"""Task-Specific Rules (Count subnets with {metric} {direction} {threshold}{unit}):
- Score 1.0: Agent provides the exact count
- Score 0.5: Agent provides count within ±2 of correct answer
- Score 0.0: Wrong count or no answer"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Calculate ground truth count from collected API data."""
        metric_field = validation_info.get("metric", "price_change_24h")
        threshold = validation_info.get("threshold", 0)
        direction = validation_info.get("direction", "above")

        # Get collected API data
        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.fail("No GT collector")

        collected = gt_collector.get_collected_api_data()
        taostats_data = collected.get("taostats", {})
        subnets_data = taostats_data.get("subnets", {})

        from ..api_client import _normalize_emission
        subnets_data = _normalize_emission(subnets_data)

        if not subnets_data:
            return GroundTruthResult.fail(
                f"Taostats subnets data not collected. "
                f"Available keys: {list(collected.keys())[:10]}"
            )

        # Count subnets meeting threshold
        compare_fn = (lambda x, t: x > t) if direction == "above" else (lambda x, t: x < t)
        count = 0

        for netuid, data in subnets_data.items():
            name = data.get("name", "")
            if not name or name.lower() == "unknown":
                continue

            value = data.get(metric_field, 0)
            if value is None:
                value = 0

            if compare_fn(float(value), threshold):
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

        # Extract number from answer
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
