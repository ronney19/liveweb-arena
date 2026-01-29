"""TAO price query template for Taostats"""

import random
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
)


@register_template("taostats_price")
class PriceTemplate(QuestionTemplate):
    """
    Template for TAO price queries.

    Ground truth is fetched from CoinGecko API.
    """

    PATTERNS: List[str] = [
        "What is the current price of TAO in USD? Check taostats.io.",
        "Go to taostats.io and find the current TAO price.",
        "What is TAO trading at right now? Visit taostats.io to find out.",
        "Find the current Bittensor (TAO) price on taostats.io.",
        "How much is 1 TAO worth in USD? Check taostats.io.",
        "What's the live TAO/USD price shown on taostats.io?",
        "Look up the current market price of TAO on taostats.io.",
        "Navigate to taostats.io and report the TAO price in dollars.",
        "What is the current USD value of one Bittensor token?",
    ]

    def __init__(self):
        super().__init__("taostats_price")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """
        Generate a Taostats price question.

        Args:
            seed: Random seed for reproducible generation
            variant: Optional variant index for selecting question pattern
        """
        rng = random.Random(seed)
        if variant is not None:
            question_text = self.PATTERNS[variant % len(self.PATTERNS)]
        else:
            question_text = rng.choice(self.PATTERNS)

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://taostats.io",
            variables={},
            validation_info={"metric": "tao_price"},
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        return """Task-Specific Rules (TAO Price):
- Score 1.0: Agent provides price within 5% of actual
- Score 0.0: No price, wrong currency, or more than 5% off"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Fetch TAO price from collected API data (no network fallback)."""
        from liveweb_arena.core.gt_collector import get_current_gt_collector

        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.fail("No GT collector")

        collected = gt_collector.get_collected_api_data()

        # TAO data might be stored under "bittensor" (CoinGecko coin_id)
        if "bittensor" in collected:
            coin_data = collected["bittensor"]
            price = coin_data.get("current_price")
            if price is not None:
                return GroundTruthResult.ok(price)

        # Or check taostats data
        taostats_data = collected.get("taostats", {})
        if "tao_price" in taostats_data:
            return GroundTruthResult.ok(taostats_data["tao_price"])

        return GroundTruthResult.fail(
            f"TAO price not found in collected data. "
            f"Available keys: {list(collected.keys())[:10]}"
        )

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate price answer"""
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
        # Extract price from answer (handle formats like $450, 450.50, etc.)
        # Remove commas and find decimal numbers
        clean_answer = answer.replace(',', '')
        numbers = re.findall(r'[\d.]+', clean_answer)

        agent_price = None
        for n in numbers:
            try:
                price = float(n)
                # Sanity check - TAO price should be in reasonable range
                if 10 < price < 10000:
                    agent_price = price
                    break
            except ValueError:
                continue

        if agent_price is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=f"${ground_truth:.2f}",
                actual=answer,
                details="No valid price found in answer",
            )

        # Calculate percentage difference
        pct_diff = abs(agent_price - ground_truth) / ground_truth * 100

        # Binary scoring: within 5% tolerance is correct, otherwise wrong
        score = 1.0 if pct_diff <= 5 else 0.0

        return ValidationResult(
            score=score,
            is_correct=score >= 0.8,
            expected=f"${ground_truth:.2f}",
            actual=f"${agent_price:.2f}",
            details=f"Difference: {pct_diff:.1f}%",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> tuple:
        """TAO price: trigger when AI visits taostats.io."""
        trigger = UrlPatternTrigger(domains=["taostats.io"])
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "taostats"
