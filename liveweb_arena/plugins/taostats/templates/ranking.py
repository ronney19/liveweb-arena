"""Subnet ranking query template for Taostats"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig
)


class RankingMetric(Enum):
    """Metrics for subnet ranking queries"""
    MARKET_CAP = "market_cap"
    PRICE = "price"
    TAO_STAKED = "tao_staked"


class RankPosition(Enum):
    """Ordinal positions for ranking queries"""
    SECOND = (2, "2nd", "second")
    THIRD = (3, "3rd", "third")
    FOURTH = (4, "4th", "fourth")
    FIFTH = (5, "5th", "fifth")
    TENTH = (10, "10th", "tenth")

    def __init__(self, num: int, ordinal: str, word: str):
        self.num = num
        self.ordinal = ordinal
        self.word = word


@register_template("taostats_ranking")
class RankingTemplate(QuestionTemplate):
    """
    Template for subnet ranking queries.

    Tests AI's ability to:
    1. Navigate to subnet list
    2. Sort by specific metric
    3. Identify subnet at specific rank position

    Ground truth calculated from Bittensor SDK.
    """

    PATTERNS: Dict[RankingMetric, List[str]] = {
        RankingMetric.MARKET_CAP: [
            "Which subnet has the {position} highest market cap on taostats.io?",
            "What is the {position} largest subnet by market cap? Check taostats.io/subnets.",
            "Find the subnet ranked #{rank_num} by market cap on taostats.io.",
            "Go to taostats.io/subnets and tell me which subnet is {position} in market cap.",
        ],
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
        """
        Generate a Taostats ranking question.

        Args:
            seed: Random seed for reproducible generation
            variant: Optional variant index for selecting ranking metric.
                     0=MARKET_CAP, 1=PRICE, 2=TAO_STAKED
        """
        rng = random.Random(seed)

        # Select metric (use variant if provided)
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
            "market_cap": "market cap",
            "price": "alpha price",
            "tao_staked": "TAO staked",
        }
        metric_display = metric_names.get(metric, metric)

        return f"""Task-Specific Rules (Subnet Ranked #{rank} by {metric_display.title()}):
- Score 1.0: Agent correctly identifies the subnet at rank #{rank} by {metric_display}
- Score 0.0: Wrong subnet or no clear answer

Note: Rankings may shift slightly due to real-time data. Accept if agent's answer matches
the expected subnet or is within ±1 rank position."""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Optional[str]:
        """
        Calculate ground truth by fetching all subnets and sorting by metric.

        Returns:
            Name of the subnet at the specified rank position.
        """
        try:
            import bittensor as bt

            subtensor = bt.Subtensor(network="finney")
            metric = validation_info.get("metric", "")
            target_rank = validation_info.get("rank", 2)

            # all_subnets() returns list of DynamicInfo objects directly
            all_subnet_info = subtensor.all_subnets()
            if not all_subnet_info:
                return None

            # Process all subnets
            subnet_data = []
            for info in all_subnet_info:
                if info.netuid == 0:  # Skip root network
                    continue
                try:
                    price = float(info.price.tao) if info.price else 0
                    tao_in = float(info.tao_in.tao) if info.tao_in else 0
                    # Market cap approximation
                    market_cap = price * tao_in

                    name = info.subnet_name or f"Subnet {info.netuid}"

                    subnet_data.append({
                        "netuid": info.netuid,
                        "name": name,
                        "price": price,
                        "tao_staked": tao_in,
                        "market_cap": market_cap,
                    })
                except Exception:
                    continue

            if len(subnet_data) < target_rank:
                return None

            # Sort by the relevant metric (descending)
            sort_key = {
                "market_cap": "market_cap",
                "price": "price",
                "tao_staked": "tao_staked",
            }.get(metric, "market_cap")

            subnet_data.sort(key=lambda x: x[sort_key], reverse=True)

            # Get subnet at target rank (1-indexed)
            if target_rank <= len(subnet_data):
                return subnet_data[target_rank - 1]["name"]

            return None

        except Exception:
            return None

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate ranking answer"""
        expected_name = await self.get_ground_truth(validation_info)

        if expected_name is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable",
            )

        answer_lower = answer.lower()

        # Check if expected subnet name is in answer
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
        """Ranking: LAST for multi-page ranking queries."""
        trigger = UrlPatternTrigger(domains=["taostats.io"])
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.LAST)
