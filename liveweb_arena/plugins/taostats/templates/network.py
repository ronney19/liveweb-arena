"""Network-level query templates for Taostats"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig
)


class NetworkMetric(Enum):
    """Network-level metrics"""
    SUBNET_COUNT = "subnet_count"
    CURRENT_BLOCK = "current_block"


@register_template("taostats_network")
class NetworkTemplate(QuestionTemplate):
    """
    Template for network-level queries on Taostats.

    Ground truth is fetched from Bittensor SDK.
    """

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
        NetworkMetric.CURRENT_BLOCK: [
            "What is the current block number on Bittensor? Check taostats.io.",
            "Go to taostats.io and find the latest block number.",
            "What's the current block height on the Bittensor network?",
            "Find the latest finalized block number on taostats.io.",
            "What block is Bittensor currently at? Check taostats.io.",
            "Look up the current chain height on taostats.io.",
        ],
    }

    def __init__(self):
        super().__init__("taostats_network")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """
        Generate a Taostats network question.

        Args:
            seed: Random seed for reproducible generation
            variant: Optional variant index for selecting network metric.
                     0=SUBNET_COUNT, 1=CURRENT_BLOCK
        """
        rng = random.Random(seed)

        # Select metric (use variant if provided)
        metrics_list = list(NetworkMetric)
        if variant is not None:
            metric = metrics_list[variant % len(metrics_list)]
        else:
            metric = rng.choice(metrics_list)
        patterns = self.PATTERNS[metric]
        question_text = rng.choice(patterns)

        validation_info = {
            "metric": metric.value,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://taostats.io/subnets" if metric == NetworkMetric.SUBNET_COUNT else "https://taostats.io",
            variables={"metric": metric},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        metric = validation_info.get("metric", "")

        if metric == "subnet_count":
            return """Task-Specific Rules (Subnet Count):
- Score 1.0: Agent provides subnet count within 5% tolerance
- Score 0.0: Count differs by more than 5% or no number"""

        if metric == "current_block":
            return """Task-Specific Rules (Current Block):
- Score 1.0: Agent provides block number close to current (within 200 blocks)
- Score 0.0: No number or wrong block"""

        return ""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Optional[Any]:
        """Fetch ground truth from Bittensor SDK"""
        try:
            import bittensor as bt

            subtensor = bt.Subtensor(network="finney")
            metric = validation_info.get("metric", "")

            if metric == "subnet_count":
                # Get all subnet netuids
                netuids = subtensor.get_all_subnets_netuid()
                return len(netuids)

            elif metric == "current_block":
                block = subtensor.get_current_block()
                return block

            return None

        except Exception:
            return None

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate answer against ground truth"""
        import re

        ground_truth = await self.get_ground_truth(validation_info)
        metric = validation_info.get("metric", "")

        if ground_truth is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable",
            )

        # Extract number from answer
        numbers = re.findall(r'[\d,]+', answer.replace(',', ''))
        if not numbers:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=str(ground_truth),
                actual=answer,
                details="No number found in answer",
            )

        # Get the first reasonable number
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

        # Binary scoring
        diff = abs(agent_number - ground_truth)
        if metric == "subnet_count":
            # Allow ±5% or ±5 subnets tolerance (webpage vs SDK may differ)
            tolerance = max(5, int(ground_truth * 0.05))
            score = 1.0 if diff <= tolerance else 0.0
        elif metric == "current_block":
            score = 1.0 if diff <= 200 else 0.0  # Block number can vary slightly
        else:
            score = 0.0

        return ValidationResult(
            score=score,
            is_correct=score >= 0.8,
            expected=str(ground_truth),
            actual=str(agent_number),
            details=f"Difference: {abs(agent_number - ground_truth)}",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> tuple:
        """Taostats network: trigger when AI visits taostats.io."""
        trigger = UrlPatternTrigger(domains=["taostats.io"])
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)
