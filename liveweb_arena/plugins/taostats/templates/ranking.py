"""Subnet ranking query template for Taostats"""

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


class RankingMetric(Enum):
    """Metrics for subnet ranking queries"""
    PRICE = "price"
    TAO_STAKED = "tao_staked"


class RankPosition(Enum):
    """Ordinal positions for ranking queries (limited to top 5 for first-page visibility)"""
    SECOND = (2, "2nd", "second")
    THIRD = (3, "3rd", "third")
    FOURTH = (4, "4th", "fourth")
    FIFTH = (5, "5th", "fifth")

    def __init__(self, num: int, ordinal: str, word: str):
        self.num = num
        self.ordinal = ordinal
        self.word = word


@register_template("taostats_ranking")
class RankingTemplate(QuestionTemplate):
    """
    Template for subnet ranking queries.

    Uses taostats API data for ground truth.
    """

    GT_SOURCE = GTSourceType.HYBRID

    PATTERNS: Dict[RankingMetric, List[str]] = {
        RankingMetric.PRICE: [
            "Which subnet has the {position} highest alpha price on taostats.io?",
            "What subnet ranks #{rank_num} by alpha token price? Check taostats.io/subnets.",
            "Find the {position} most expensive subnet by alpha price on taostats.io.",
            "On taostats.io, which subnet has the {position} highest price?",
        ],
        RankingMetric.TAO_STAKED: [
            "Which subnet has the {position} most TAO staked? Check taostats.io/subnets.",
            "What subnet ranks #{rank_num} in terms of TAO staked on taostats.io?",
            "Find the subnet with the {position} highest TAO in value on taostats.io.",
            "Go to taostats.io and identify the {position} largest subnet by TAO staked.",
        ],
    }

    def __init__(self):
        super().__init__("taostats_ranking")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        metrics_list = list(RankingMetric)
        if variant is not None:
            metric = metrics_list[variant % len(metrics_list)]
        else:
            metric = rng.choice(metrics_list)
        position = rng.choice(list(RankPosition))
        patterns = self.PATTERNS[metric]
        pattern = rng.choice(patterns)

        question_text = pattern.format(
            position=rng.choice([position.ordinal, position.word]),
            rank_num=position.num
        )

        validation_info = {
            "metric": metric.value,
            "rank": position.num,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://taostats.io/subnets",
            variables={"metric": metric, "position": position},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        metric = validation_info.get("metric", "")
        rank = validation_info.get("rank", 0)

        metric_names = {
            "price": "alpha price",
            "tao_staked": "TAO staked",
        }
        metric_display = metric_names.get(metric, metric)

        return f"""Task-Specific Rules (Subnet Ranked #{rank} by {metric_display.title()}):
- Score 1.0: Agent correctly identifies the subnet at rank #{rank} by {metric_display}
- Score 0.0: Wrong subnet or no clear answer"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Calculate ground truth from collected API data (no network fallback)."""
        metric = validation_info.get("metric", "")
        target_rank = validation_info.get("rank", 2)

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

        if len(subnets_data) < target_rank:
            return GroundTruthResult.fail(f"Not enough subnets for rank {target_rank}")

        # Build and sort subnet list
        subnet_list = []
        for netuid, data in subnets_data.items():
            price = float(data.get("price", 0) or 0)
            tao_in = float(data.get("tao_in", 0) or 0)
            name = data.get("name", f"Subnet {netuid}")

            subnet_list.append({
                "netuid": netuid,
                "name": name,
                "price": price,
                "tao_staked": tao_in,
            })

        # Sort by the relevant metric
        sort_key = {
            "price": "price",
            "tao_staked": "tao_staked",
        }.get(metric, "price")

        subnet_list.sort(key=lambda x: x[sort_key], reverse=True)

        if target_rank <= len(subnet_list):
            return GroundTruthResult.ok(subnet_list[target_rank - 1]["name"])

        return GroundTruthResult.fail(f"Rank {target_rank} out of range")

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
            details=f"Expected {expected_name} at specified rank",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> tuple:
        trigger = UrlPatternTrigger(domains=["taostats.io"])
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.LAST)

    @classmethod
    def get_cache_source(cls) -> str:
        return "taostats"

    def get_gt_source(self) -> GTSourceType:
        return self.GT_SOURCE
