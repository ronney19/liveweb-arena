"""Network-level query templates for Taostats"""

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


class NetworkMetric(Enum):
    """Network-level metrics"""
    SUBNET_COUNT = "subnet_count"


@register_template("taostats_network")
class NetworkTemplate(QuestionTemplate):
    """
    Template for network-level queries on Taostats.

    Uses taostats API data for ground truth.
    """

    GT_SOURCE = GTSourceType.HYBRID

    PATTERNS: Dict[NetworkMetric, List[str]] = {
        NetworkMetric.SUBNET_COUNT: [
            "How many subnets currently exist on Bittensor? Check taostats.io/subnets.",
            "What is the total number of subnets on the Bittensor network? Visit taostats.io/subnets.",
            "Go to taostats.io/subnets and count how many subnets are registered.",
            "Find the current number of active subnets on taostats.io.",
            "How many subnets are listed on taostats.io/subnets?",
            "What's the total subnet count shown on taostats.io?",
            "Count the number of registered subnets on the Bittensor network.",
        ],
    }

    def __init__(self):
        super().__init__("taostats_network")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        # Only subnet_count is supported (current_block requires different API)
        metric = NetworkMetric.SUBNET_COUNT
        patterns = self.PATTERNS[metric]
        question_text = rng.choice(patterns)

        validation_info = {
            "metric": metric.value,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://taostats.io/subnets",
            variables={"metric": metric},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        return """Task-Specific Rules (Subnet Count):
- Score 1.0: Agent provides subnet count within 5% tolerance
- Score 0.0: Count differs by more than 5% or no number"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Fetch ground truth from collected API data (no network fallback)."""
        metric = validation_info.get("metric", "")

        if metric == "subnet_count":
            # Get collected API data
            gt_collector = get_current_gt_collector()
            if gt_collector is None:
                return GroundTruthResult.fail("No GT collector")

            collected = gt_collector.get_collected_api_data()
            taostats_data = collected.get("taostats", {})
            subnets_data = taostats_data.get("subnets", {})

            if not subnets_data:
                return GroundTruthResult.fail(
                    f"Taostats subnets data not collected. "
                    f"Available keys: {list(collected.keys())[:10]}"
                )

            return GroundTruthResult.ok(len(subnets_data))

        return GroundTruthResult.fail(f"Unknown metric: {metric}")

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        import re

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
        numbers = re.findall(r'[\d,]+', answer.replace(',', ''))
        if not numbers:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=str(ground_truth),
                actual=answer,
                details="No number found in answer",
            )

        agent_number = None
        for n in numbers:
            try:
                num = int(n.replace(',', ''))
                if num > 0:
                    agent_number = num
                    break
            except ValueError:
                continue

        if agent_number is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=str(ground_truth),
                actual=answer,
                details="No valid number found",
            )

        diff = abs(agent_number - ground_truth)
        tolerance = max(5, int(ground_truth * 0.05))
        score = 1.0 if diff <= tolerance else 0.0

        return ValidationResult(
            score=score,
            is_correct=score >= 0.8,
            expected=str(ground_truth),
            actual=str(agent_number),
            details=f"Difference: {abs(agent_number - ground_truth)}",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> tuple:
        trigger = UrlPatternTrigger(domains=["taostats.io"])
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)

    @classmethod
    def get_cache_source(cls) -> str:
        return "taostats"

    def get_gt_source(self):
        return self.GT_SOURCE
