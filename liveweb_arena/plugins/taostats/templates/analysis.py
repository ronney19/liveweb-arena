"""Analysis query template for Taostats - derived metrics and calculations"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
)
from liveweb_arena.core.gt_collector import GTSourceType, get_current_gt_collector
from .variables import _fetch_top_subnet_ids, _fetch_subnet_name


class AnalysisType(Enum):
    """Types of analysis questions - only metrics visible on taostats.io"""
    HIGHEST_PRICE_TO_STAKE = "highest_price_to_stake"
    LOWEST_PRICE_TO_STAKE = "lowest_price_to_stake"
    HIGHEST_TAO_IN = "highest_tao_in"
    HIGHEST_PRICE = "highest_price"
    LOWEST_PRICE = "lowest_price"


def _get_subnet_list(rng: random.Random, count: int) -> List[Tuple[int, str]]:
    """Dynamically fetch subnet IDs and names for analysis.

    Uses top 10 subnets by emission to ensure visibility on first page
    (taostats.io defaults to 10 rows sorted by emission).
    """
    subnet_ids = _fetch_top_subnet_ids(top_n=10)
    if len(subnet_ids) < count:
        count = len(subnet_ids)

    selected_ids = rng.sample(subnet_ids, count)
    return [(sid, _fetch_subnet_name(sid) or f"Subnet {sid}") for sid in selected_ids]


@register_template("taostats_analysis")
class AnalysisTemplate(QuestionTemplate):
    """
    Template for analysis questions requiring calculation.

    Uses taostats API data for ground truth (bound to page cache).
    """

    GT_SOURCE = GTSourceType.HYBRID

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

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        # Dynamically select 3-5 subnets from top subnets (visible on first page)
        num_subnets = rng.randint(3, 5)
        selected = _get_subnet_list(rng, num_subnets)
        if len(selected) < 2:
            raise RuntimeError("Could not fetch subnet data for analysis question generation")

        # Select analysis type
        analysis_types_list = list(AnalysisType)
        if variant is not None:
            analysis_type = analysis_types_list[variant % len(analysis_types_list)]
        else:
            analysis_type = rng.choice(analysis_types_list)
        patterns = self.PATTERNS[analysis_type]
        pattern = rng.choice(patterns)

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

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Calculate ground truth from collected API data (no network fallback)."""
        analysis_type = validation_info.get("analysis_type", "")
        subnet_ids = validation_info.get("subnet_ids", [])
        subnet_names = validation_info.get("subnet_names", [])

        if not subnet_ids or len(subnet_ids) != len(subnet_names):
            return GroundTruthResult.fail("Invalid subnet IDs or names")

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

        # Build subnet data list
        subnet_list = []
        missing = []
        for i, netuid in enumerate(subnet_ids):
            data = subnets_data.get(str(netuid), {})
            if not data:
                missing.append(f"SN{netuid} ({subnet_names[i]})")
                continue

            price = float(data.get("price", 0) or 0)
            tao_in = float(data.get("tao_in", 0) or 0)
            price_to_stake = price / tao_in if tao_in > 0 else 0

            subnet_list.append({
                "netuid": netuid,
                "name": subnet_names[i],
                "price": price,
                "tao_in": tao_in,
                "price_to_stake": price_to_stake,
            })

        if missing:
            return GroundTruthResult.fail(
                f"Subnets not found in collected data: {', '.join(missing)}"
            )

        if len(subnet_list) < 2:
            return GroundTruthResult.fail("Not enough subnet data for analysis")

        # Sort by the relevant metric
        sort_config = {
            "highest_price_to_stake": ("price_to_stake", True),
            "lowest_price_to_stake": ("price_to_stake", False),
            "highest_tao_in": ("tao_in", True),
            "highest_price": ("price", True),
            "lowest_price": ("price", False),
        }

        if analysis_type not in sort_config:
            return GroundTruthResult.fail(f"Unknown analysis type: {analysis_type}")

        sort_key, reverse = sort_config[analysis_type]
        subnet_list.sort(key=lambda x: x[sort_key], reverse=reverse)

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

        top_name = result.value
        answer_lower = answer.lower()

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

    def get_ground_truth_trigger(self, validation_info: dict) -> tuple:
        trigger = UrlPatternTrigger(domains=["taostats.io"])
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.LAST)

    @classmethod
    def get_cache_source(cls) -> str:
        return "taostats"

    def get_gt_source(self) -> GTSourceType:
        return self.GT_SOURCE
