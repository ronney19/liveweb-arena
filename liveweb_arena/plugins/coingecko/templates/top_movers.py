"""Top Gainers/Losers query template for CoinGecko - MULTI-STEP INTERACTION"""

import random
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
)
from liveweb_arena.core.gt_collector import GTSourceType
from ..api_client import CoinGeckoClient


@register_template("coingecko_top_movers")
class CoinGeckoTopMoversTemplate(QuestionTemplate):
    """
    Template for top gainers/losers queries - REQUIRES MULTI-STEP INTERACTION.

    This template requires the agent to:
    1. Navigate to CoinGecko homepage or categories
    2. Find and click on "Top Gainers" or "Top Losers" section
    3. Identify the #1 coin and its percentage change

    Examples:
    - What cryptocurrency gained the most in the last 24 hours?
    - Which coin is the biggest loser today on CoinGecko?
    - What is the #1 top gainer on CoinGecko right now?
    """

    GT_SOURCE = GTSourceType.API_ONLY  # Requires sorting top 100 coins

    GAINER_PATTERNS = [
        "Among the top 100 cryptocurrencies by market cap, which one gained the most in the last 24 hours?",
        "What is the biggest 24h gainer among top 100 coins on CoinGecko?",
        "Find the top performing coin in the last 24 hours from CoinGecko's top 100 by market cap.",
        "Which top-100 market cap cryptocurrency has the highest 24h gain on CoinGecko?",
    ]

    LOSER_PATTERNS = [
        "Among the top 100 cryptocurrencies by market cap, which one lost the most in the last 24 hours?",
        "What is the biggest 24h loser among top 100 coins on CoinGecko?",
        "Find the worst performing coin in the last 24 hours from CoinGecko's top 100 by market cap.",
        "Which top-100 market cap cryptocurrency has the biggest 24h loss on CoinGecko?",
    ]

    def __init__(self):
        super().__init__("coingecko_top_movers")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a top movers question."""
        rng = random.Random(seed)

        # Select gainer or loser
        if variant is not None:
            is_gainer = (variant % 2) == 0
        else:
            is_gainer = rng.choice([True, False])

        if is_gainer:
            patterns = self.GAINER_PATTERNS
            query_type = "gainer"
        else:
            patterns = self.LOSER_PATTERNS
            query_type = "loser"

        pattern = rng.choice(patterns)

        validation_info = {
            "query_type": query_type,
        }

        # Start URL is the homepage - agent must navigate to find top movers
        return GeneratedQuestion(
            question_text=pattern,
            start_url="https://www.coingecko.com",
            variables={"query_type": query_type},
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=8,  # More steps needed for navigation
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        query_type = validation_info.get("query_type", "gainer")
        if query_type == "gainer":
            return """Task-Specific Rules (CoinGecko - Top Gainer):
- Answer must identify the top gainer coin
- Score 1.0: Correct coin name identified
- Score 0.0: Wrong coin or no answer
- Accept formats: "Bitcoin (+15.2%)", "BTC gained 15%", "Bitcoin is up 15.2%"
- Percentage tolerance: 10pp (data changes frequently)"""
        else:
            return """Task-Specific Rules (CoinGecko - Top Loser):
- Answer must identify the top loser coin
- Score 1.0: Correct coin name identified
- Score 0.0: Wrong coin or no answer
- Accept formats: "Bitcoin (-15.2%)", "BTC lost 15%", "Bitcoin is down 15.2%"
- Percentage tolerance: 10pp (data changes frequently)"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Get top gainer/loser from collected API data (no network fallback)."""
        query_type = validation_info.get("query_type", "gainer")

        from liveweb_arena.core.gt_collector import get_current_gt_collector
        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.fail("No GT collector")

        collected = gt_collector.get_collected_api_data()
        if not collected:
            return GroundTruthResult.fail("No coins collected. Agent must visit CoinGecko.")

        # Convert collected dict to list for sorting
        valid_coins = []
        for coin_id, data in collected.items():
            if isinstance(data, dict) and data.get("price_change_percentage_24h") is not None:
                valid_coins.append(data)

        if not valid_coins:
            return GroundTruthResult.fail("No valid coins with 24h change data in collected")

        # Sort by 24h change
        if query_type == "gainer":
            sorted_coins = sorted(
                valid_coins,
                key=lambda x: x.get("price_change_percentage_24h", 0),
                reverse=True
            )
        else:
            sorted_coins = sorted(
                valid_coins,
                key=lambda x: x.get("price_change_percentage_24h", 0),
                reverse=False
            )

        top_coin = sorted_coins[0]
        name = top_coin.get("name", "Unknown")
        change = top_coin.get("price_change_percentage_24h", 0)

        if query_type == "gainer":
            return GroundTruthResult.ok(f"{name} (+{change:.2f}%)")
        else:
            return GroundTruthResult.ok(f"{name} ({change:.2f}%)")

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate top mover answer."""
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
        # Parse expected: "CoinName (+/-XX.XX%)"
        exp_match = re.match(r'(.+?)\s*\(([+-]?\d+\.?\d*)%\)', ground_truth)
        if not exp_match:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Could not parse ground truth",
            )

        expected_name = exp_match.group(1).lower().strip()
        expected_pct = float(exp_match.group(2))

        # Check if answer contains the coin name
        answer_lower = answer.lower()
        name_found = expected_name in answer_lower

        # Also check for common variations
        if not name_found:
            # Try first word of expected name
            first_word = expected_name.split()[0]
            name_found = first_word in answer_lower

        # Parse percentage from answer
        pct_match = re.search(r'([+-]?\d+\.?\d*)\s*%', answer)
        if pct_match:
            actual_pct = float(pct_match.group(1))
            # Handle negative detection from context
            if any(w in answer_lower for w in ["down", "lost", "fell", "dropped", "-"]):
                if actual_pct > 0:
                    actual_pct = -actual_pct
        else:
            actual_pct = None

        if name_found:
            # If correct coin is identified, give full score
            # Percentage differences are expected due to real-time data changes
            if actual_pct is not None:
                diff = abs(actual_pct - expected_pct)
                return ValidationResult(
                    score=1.0,
                    is_correct=True,
                    expected=ground_truth,
                    actual=answer,
                    details=f"Correct coin identified (percentage diff: {diff:.1f}pp)",
                )
            else:
                return ValidationResult(
                    score=1.0,
                    is_correct=True,
                    expected=ground_truth,
                    actual=answer,
                    details="Correct coin identified",
                )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=ground_truth,
            actual=answer,
            details="Wrong coin or could not identify coin name",
        )

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> TriggerConfig:
        """
        Trigger when AI visits the gainers/losers page.

        URLs to match:
        - /en/crypto-gainers-losers
        - /en/coins/trending
        - Homepage with sorting
        """
        trigger = UrlPatternTrigger(
            domains=["coingecko.com"],
            url_contains="gainer",  # Matches gainers-losers page
        )
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "coingecko"

    def get_gt_source(self):
        """
        Top movers requires sorting 100 coins by 24h change.
        Use API_ONLY because we need aggregated market data.
        """
        from liveweb_arena.core.gt_collector import GTSourceType
        return GTSourceType.API_ONLY
