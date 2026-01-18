"""Analysis query template for Taostats - derived metrics and calculations"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from .variables import _fetch_active_subnet_ids, _fetch_subnet_name


class AnalysisType(Enum):
    """Types of analysis questions - only metrics visible on taostats.io"""
    HIGHEST_PRICE_TO_STAKE = "highest_price_to_stake"
    LOWEST_PRICE_TO_STAKE = "lowest_price_to_stake"
    # Note: HIGHEST_STAKE_EFFICIENCY removed - alpha_out not shown on website
    HIGHEST_TAO_IN = "highest_tao_in"
    HIGHEST_PRICE = "highest_price"
    LOWEST_PRICE = "lowest_price"


def _get_subnet_list(rng: random.Random, count: int) -> List[Tuple[int, str]]:
    """Dynamically fetch subnet IDs and names for analysis."""
    subnet_ids = _fetch_active_subnet_ids()
    if len(subnet_ids) < count:
        count = len(subnet_ids)

    selected_ids = rng.sample(subnet_ids, count)
    return [(sid, _fetch_subnet_name(sid) or f"Subnet {sid}") for sid in selected_ids]


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
            "Looking at {subnets}, find which has the best price-to-stake ratio.",
        ],
        AnalysisType.LOWEST_PRICE_TO_STAKE: [
            "Among these subnets: {subnets}, which one has the lowest price-to-TAO-staked ratio? Check taostats.io/subnets.",
            "Compare {subnets} on taostats.io/subnets. Which subnet has the lowest alpha price relative to its TAO staked (best value)?",
            "Looking at {subnets}, which offers the best value (lowest price per TAO staked)?",
        ],
        AnalysisType.HIGHEST_TAO_IN: [
            "Among {subnets}, which subnet has the most TAO staked? Check taostats.io/subnets.",
            "Compare {subnets} on taostats.io. Which has attracted the highest TAO deposits?",
            "Looking at {subnets}, find the subnet with highest TAO in value.",
        ],
        AnalysisType.HIGHEST_PRICE: [
            "Among {subnets}, which subnet has the highest alpha price? Check taostats.io/subnets.",
            "Compare {subnets} on taostats.io. Which has the most expensive alpha token?",
            "Looking at {subnets}, which has the highest priced alpha token?",
        ],
        AnalysisType.LOWEST_PRICE: [
            "Among {subnets}, which subnet has the lowest alpha price? Check taostats.io/subnets.",
            "Compare {subnets} on taostats.io. Which has the cheapest alpha token?",
            "Looking at {subnets}, which has the lowest priced alpha token?",
        ],
    }

    def __init__(self):
        super().__init__("taostats_analysis")

    def generate(self, seed: int) -> GeneratedQuestion:
        rng = random.Random(seed)

        # Dynamically select 3-5 subnets for comparison
        num_subnets = rng.randint(3, 5)
        selected = _get_subnet_list(rng, num_subnets)
        if len(selected) < 2:
            # Fallback if network fetch fails
            selected = [(1, "Subnet 1"), (2, "Subnet 2"), (3, "Subnet 3")]

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

        type_rules = {
            "highest_price_to_stake": "highest price-to-stake ratio",
            "lowest_price_to_stake": "lowest price-to-stake ratio",
            "highest_tao_in": "highest TAO staked",
            "highest_price": "highest alpha price",
            "lowest_price": "lowest alpha price",
        }

        rule = type_rules.get(analysis_type, analysis_type)
        return f"""Task-Specific Rules ({rule.title()} among {subnets_str}):
- Score 1.0: Agent correctly identifies the subnet with {rule}
- Score 0.0: Wrong subnet or no clear answer"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Optional[str]:
        """
        Calculate ground truth by fetching subnet data and computing derived metrics.

        Returns:
            Name of the winning subnet (simple string for clear LLM validation)
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

                    # Calculate derived metrics
                    price_to_stake = price / tao_in if tao_in > 0 else 0

                    subnet_data.append({
                        "netuid": netuid,
                        "name": subnet_names[i],
                        "price": price,
                        "tao_in": tao_in,
                        "price_to_stake": price_to_stake,
                    })
                except Exception:
                    continue

            if len(subnet_data) < 2:
                return None

            # Sort by the relevant metric
            sort_config = {
                "highest_price_to_stake": ("price_to_stake", True),
                "lowest_price_to_stake": ("price_to_stake", False),
                "highest_tao_in": ("tao_in", True),
                "highest_price": ("price", True),
                "lowest_price": ("price", False),
            }

            if analysis_type not in sort_config:
                return None

            sort_key, reverse = sort_config[analysis_type]
            subnet_data.sort(key=lambda x: x[sort_key], reverse=reverse)

            return subnet_data[0]["name"]

        except Exception:
            return None

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate analysis answer"""
        top_name = await self.get_ground_truth(validation_info)

        if top_name is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable",
            )

        answer_lower = answer.lower()

        # Binary scoring: correct subnet or wrong
        if top_name.lower() in answer_lower:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=top_name,
                actual=answer,
                details="Correct subnet identified",
            )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=top_name,
            actual=answer,
            details="Wrong subnet or not found in answer",
        )
