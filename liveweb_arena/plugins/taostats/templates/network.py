"""Network-level query templates for Taostats"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
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
        ],
        NetworkMetric.CURRENT_BLOCK: [
            "What is the current block number on Bittensor? Check taostats.io.",
            "Go to taostats.io and find the latest block number.",
        ],
    }

    def __init__(self):
        super().__init__("taostats_network")

    def generate(self, seed: int) -> GeneratedQuestion:
        rng = random.Random(seed)

        metric = rng.choice(list(NetworkMetric))
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
- Score 1.0: Agent provides correct subnet count (within ±2 of actual)
- Score 0.5: Agent provides a count close to actual (within ±10)
- Score 0.0: No number or clearly wrong count"""

        if metric == "current_block":
            return """Task-Specific Rules (Current Block):
- Score 1.0: Agent provides block number close to current (within 100 blocks)
- Score 0.5: Agent provides a reasonable block number (within 1000 blocks)
- Score 0.0: No number or clearly wrong"""

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

        # Calculate score based on tolerance
        if metric == "subnet_count":
            diff = abs(agent_number - ground_truth)
            if diff <= 2:
                score = 1.0
            elif diff <= 10:
                score = 0.5
            else:
                score = 0.0
        elif metric == "current_block":
            diff = abs(agent_number - ground_truth)
            if diff <= 100:
                score = 1.0
            elif diff <= 1000:
                score = 0.5
            else:
                score = 0.0
        else:
            score = 0.0

        return ValidationResult(
            score=score,
            is_correct=score >= 0.8,
            expected=str(ground_truth),
            actual=str(agent_number),
            details=f"Difference: {abs(agent_number - ground_truth)}",
        )
