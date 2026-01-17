"""Analysis query template for Taostats - derived metrics and calculations"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)


class AnalysisType(Enum):
    """Types of analysis questions"""
    HIGHEST_PRICE_TO_STAKE = "highest_price_to_stake"
    LOWEST_PRICE_TO_STAKE = "lowest_price_to_stake"
    HIGHEST_STAKE_EFFICIENCY = "highest_stake_efficiency"


# Subnets to analyze (need enough variation for interesting analysis)
ANALYSIS_SUBNETS = [
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


@register_template("taostats_analysis")
class AnalysisTemplate(QuestionTemplate):
    """
    Template for analysis questions requiring calculation.

    Tests AI's ability to:
    1. Navigate and find multiple data points
    2. Perform calculations or comparisons
    3. Draw conclusions from derived metrics

    Ground truth is calculated from Bittensor SDK data.
    """

    PATTERNS: Dict[AnalysisType, List[str]] = {
        AnalysisType.HIGHEST_PRICE_TO_STAKE: [
            "Among these subnets: {subnets}, which one has the highest price-to-TAO-staked ratio? Check taostats.io/subnets for price and TAO in data.",
            "Compare {subnets} on taostats.io/subnets. Which subnet has the highest alpha price relative to its TAO staked?",
        ],
        AnalysisType.LOWEST_PRICE_TO_STAKE: [
            "Among these subnets: {subnets}, which one has the lowest price-to-TAO-staked ratio? Check taostats.io/subnets.",
            "Compare {subnets} on taostats.io/subnets. Which subnet has the lowest alpha price relative to its TAO staked (best value)?",
        ],
        AnalysisType.HIGHEST_STAKE_EFFICIENCY: [
            "Among {subnets}, which subnet has the highest alpha-out to alpha-in ratio (stake efficiency)? Check taostats.io/subnets.",
            "Compare {subnets} on taostats.io. Which subnet converts alpha-in to alpha-out most efficiently?",
        ],
    }

    def __init__(self):
        super().__init__("taostats_analysis")

    def generate(self, seed: int) -> GeneratedQuestion:
        rng = random.Random(seed)

        # Select 3-5 subnets for comparison
        num_subnets = rng.randint(3, 5)
        selected = rng.sample(ANALYSIS_SUBNETS, num_subnets)

        analysis_type = rng.choice(list(AnalysisType))
        patterns = self.PATTERNS[analysis_type]
        pattern = rng.choice(patterns)

        # Format subnet list
        subnet_names = ", ".join([name for _, name in selected])
        question_text = pattern.format(subnets=subnet_names)

        validation_info = {
            "analysis_type": analysis_type.value,
            "subnet_ids": [id for id, _ in selected],
            "subnet_names": [name for _, name in selected],
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://taostats.io/subnets",
            variables={"analysis_type": analysis_type, "subnets": selected},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        analysis_type = validation_info.get("analysis_type", "")
        subnet_names = validation_info.get("subnet_names", [])
        subnets_str = ", ".join(subnet_names)

        if analysis_type == "highest_price_to_stake":
            return f"""Task-Specific Rules (Highest Price/Stake Ratio among {subnets_str}):
- Score 1.0: Agent correctly identifies the subnet with highest price-to-stake ratio
- Score 0.5: Agent identifies a subnet in top 2 by this metric
- Score 0.0: Wrong subnet or no clear answer"""

        if analysis_type == "lowest_price_to_stake":
            return f"""Task-Specific Rules (Lowest Price/Stake Ratio among {subnets_str}):
- Score 1.0: Agent correctly identifies the subnet with lowest price-to-stake ratio
- Score 0.5: Agent identifies a subnet in bottom 2 by this metric
- Score 0.0: Wrong subnet or no clear answer"""

        if analysis_type == "highest_stake_efficiency":
            return f"""Task-Specific Rules (Highest Stake Efficiency among {subnets_str}):
- Score 1.0: Agent correctly identifies the subnet with highest alpha-out/alpha-in ratio
- Score 0.5: Agent identifies a subnet in top 2 by this metric
- Score 0.0: Wrong subnet or no clear answer"""

        return ""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Optional[Tuple[str, List[str]]]:
        """
        Calculate ground truth by fetching subnet data and computing derived metrics.

        Returns:
            Tuple of (correct_answer_name, top_2_names) or None
        """
        try:
            import bittensor as bt

            subtensor = bt.Subtensor(network="finney")
            analysis_type = validation_info.get("analysis_type", "")
            subnet_ids = validation_info.get("subnet_ids", [])
            subnet_names = validation_info.get("subnet_names", [])

            if not subnet_ids or len(subnet_ids) != len(subnet_names):
                return None

            # Fetch data for each subnet
            subnet_data = []
            for i, netuid in enumerate(subnet_ids):
                try:
                    info = subtensor.subnet(netuid)
                    if info is None:
                        continue

                    price = float(info.price.tao) if info.price else 0
                    tao_in = float(info.tao_in.tao) if info.tao_in else 0
                    alpha_in = float(info.alpha_in.tao) if info.alpha_in else 0
                    alpha_out = float(info.alpha_out.tao) if info.alpha_out else 0

                    # Calculate derived metrics
                    price_to_stake = price / tao_in if tao_in > 0 else 0
                    stake_efficiency = alpha_out / alpha_in if alpha_in > 0 else 0

                    subnet_data.append({
                        "netuid": netuid,
                        "name": subnet_names[i],
                        "price": price,
                        "tao_in": tao_in,
                        "alpha_in": alpha_in,
                        "alpha_out": alpha_out,
                        "price_to_stake": price_to_stake,
                        "stake_efficiency": stake_efficiency,
                    })
                except Exception:
                    continue

            if len(subnet_data) < 2:
                return None

            # Sort by the relevant metric
            if analysis_type == "highest_price_to_stake":
                subnet_data.sort(key=lambda x: x["price_to_stake"], reverse=True)
            elif analysis_type == "lowest_price_to_stake":
                subnet_data.sort(key=lambda x: x["price_to_stake"], reverse=False)
            elif analysis_type == "highest_stake_efficiency":
                subnet_data.sort(key=lambda x: x["stake_efficiency"], reverse=True)
            else:
                return None

            top_name = subnet_data[0]["name"]
            top_2_names = [s["name"] for s in subnet_data[:2]]

            return (top_name, top_2_names)

        except Exception:
            return None

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate analysis answer"""
        ground_truth = await self.get_ground_truth(validation_info)

        if ground_truth is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable",
            )

        top_name, top_2_names = ground_truth
        answer_lower = answer.lower()

        # Check if correct subnet is mentioned
        if top_name.lower() in answer_lower:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=top_name,
                actual=answer,
                details="Correct - top subnet identified",
            )

        # Check if second-best is mentioned (partial credit)
        for name in top_2_names[1:]:
            if name.lower() in answer_lower:
                return ValidationResult(
                    score=0.5,
                    is_correct=False,
                    expected=top_name,
                    actual=answer,
                    details=f"Partial - {name} is #2, not #1",
                )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=top_name,
            actual=answer,
            details="Wrong subnet or not found in answer",
        )
