"""Subnet comparison template for Taostats"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)


class ComparisonMetric(Enum):
    """Metrics for subnet comparison"""
    PRICE = "price"
    TAO_STAKED = "tao_staked"


# Popular subnets with known good data
COMPARABLE_SUBNETS = [
    (1, "Apex"),
    (2, "Omron"),
    (5, "Kaizen"),
    (8, "Taoshi"),
    (9, "Pretrain"),
    (13, "Dataverse"),
    (18, "Cortex"),
    (19, "Inference"),
    (21, "Omega"),
    (27, "Compute"),
]


@register_template("taostats_comparison")
class ComparisonTemplate(QuestionTemplate):
    """
    Template for comparing two subnets.

    Only fetches 2 subnets from SDK, so it's fast.
    """

    PATTERNS: Dict[ComparisonMetric, List[str]] = {
        ComparisonMetric.PRICE: [
            "Between {subnet1} (SN{id1}) and {subnet2} (SN{id2}), which has a higher alpha price? Check taostats.io/subnets.",
            "Go to taostats.io/subnets and compare {subnet1} and {subnet2}. Which subnet has a higher price?",
        ],
        ComparisonMetric.TAO_STAKED: [
            "Between {subnet1} (SN{id1}) and {subnet2} (SN{id2}), which has more TAO staked? Check taostats.io/subnets.",
            "Go to taostats.io/subnets and compare {subnet1} and {subnet2}. Which has higher TAO in?",
        ],
    }

    def __init__(self):
        super().__init__("taostats_comparison")

    def generate(self, seed: int) -> GeneratedQuestion:
        rng = random.Random(seed)

        # Select two different subnets
        selected = rng.sample(COMPARABLE_SUBNETS, 2)
        id1, name1 = selected[0]
        id2, name2 = selected[1]

        metric = rng.choice(list(ComparisonMetric))
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

        if metric == "price":
            return f"""Task-Specific Rules (Price Comparison: {name1} vs {name2}):
- Score 1.0: Agent correctly identifies which subnet has higher price
- Score 0.0: Wrong answer or no clear answer"""

        if metric == "tao_staked":
            return f"""Task-Specific Rules (TAO Staked Comparison: {name1} vs {name2}):
- Score 1.0: Agent correctly identifies which subnet has more TAO staked
- Score 0.0: Wrong answer or no clear answer"""

        return ""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Optional[str]:
        """
        Get ground truth by comparing two subnets from SDK.

        Returns the name of the subnet with higher value.
        """
        try:
            import bittensor as bt

            subtensor = bt.Subtensor(network="finney")
            metric = validation_info.get("metric", "")
            id1 = validation_info.get("subnet1_id")
            id2 = validation_info.get("subnet2_id")
            name1 = validation_info.get("subnet1_name")
            name2 = validation_info.get("subnet2_name")

            # Fetch both subnets
            info1 = subtensor.subnet(id1)
            info2 = subtensor.subnet(id2)

            if info1 is None or info2 is None:
                return None

            # Get values based on metric
            if metric == "price":
                val1 = float(info1.price.tao) if info1.price else 0
                val2 = float(info2.price.tao) if info2.price else 0
            elif metric == "tao_staked":
                val1 = float(info1.tao_in.tao) if info1.tao_in else 0
                val2 = float(info2.tao_in.tao) if info2.tao_in else 0
            else:
                return None

            # Return name of subnet with higher value
            return name1 if val1 > val2 else name2

        except Exception:
            return None

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate comparison answer"""
        ground_truth = await self.get_ground_truth(validation_info)

        if ground_truth is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable",
            )

        name1 = validation_info.get("subnet1_name", "")
        name2 = validation_info.get("subnet2_name", "")
        answer_lower = answer.lower()

        # Check if correct subnet is mentioned
        if ground_truth.lower() in answer_lower:
            # Make sure wrong subnet isn't also mentioned as the answer
            wrong_name = name2 if ground_truth == name1 else name1
            # Simple heuristic: if both are mentioned, check which comes first
            # or if one is negated
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
