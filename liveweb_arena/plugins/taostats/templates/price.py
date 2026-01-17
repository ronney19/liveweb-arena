"""TAO price query template for Taostats"""

import random
from typing import Any, Dict, List, Optional
import aiohttp

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)


@register_template("taostats_price")
class PriceTemplate(QuestionTemplate):
    """
    Template for TAO price queries.

    Ground truth is fetched from CoinGecko API (free, no auth required).
    """

    PATTERNS: List[str] = [
        "What is the current price of TAO in USD? Check taostats.io.",
        "Go to taostats.io and find the current TAO price.",
        "What is TAO trading at right now? Visit taostats.io to find out.",
    ]

    COINGECKO_API = "https://api.coingecko.com/api/v3/simple/price"

    def __init__(self):
        super().__init__("taostats_price")

    def generate(self, seed: int) -> GeneratedQuestion:
        rng = random.Random(seed)
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
- Score 0.5: Agent provides price within 15% of actual
- Score 0.0: No price, wrong currency, or more than 15% off"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Optional[float]:
        """Fetch TAO price from CoinGecko API"""
        try:
            async with aiohttp.ClientSession() as session:
                params = {
                    "ids": "bittensor",
                    "vs_currencies": "usd"
                }
                async with session.get(
                    self.COINGECKO_API,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status != 200:
                        return None
                    data = await response.json()
                    return data.get("bittensor", {}).get("usd")
        except Exception:
            return None

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate price answer"""
        import re

        ground_truth = await self.get_ground_truth(validation_info)

        if ground_truth is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable",
            )

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

        if pct_diff <= 5:
            score = 1.0
        elif pct_diff <= 15:
            score = 0.5
        else:
            score = 0.0

        return ValidationResult(
            score=score,
            is_correct=score >= 0.8,
            expected=f"${ground_truth:.2f}",
            actual=f"${agent_price:.2f}",
            details=f"Difference: {pct_diff:.1f}%",
        )
