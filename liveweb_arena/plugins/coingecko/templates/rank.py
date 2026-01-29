"""Market rank query template for CoinGecko - HIGH DIFFICULTY"""

import random
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
)
from .price import CoinVariable, CoinSpec
from ..api_client import CoinGeckoClient


@register_template("coingecko_rank")
class CoinGeckoRankTemplate(QuestionTemplate):
    """
    Template for market cap rank queries - HIGH DIFFICULTY.

    Requires finding the rank of a coin, which may need scrolling
    through the market rankings page or finding it on the coin page.

    Examples:
    - What is Bitcoin's market cap rank?
    - What rank is Solana by market capitalization?
    - Where does Cardano rank in the crypto market?
    """

    PATTERNS = [
        "What is {coin}'s market cap rank?",
        "What rank is {coin} by market capitalization?",
        "Where does {coin} rank in the crypto market by market cap?",
        "What position is {coin} in the cryptocurrency rankings?",
    ]

    def __init__(self):
        super().__init__("coingecko_rank")
        self._coin_var = CoinVariable()

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a rank question."""
        rng = random.Random(seed)
        coin = self._coin_var.sample(rng)

        pattern = rng.choice(self.PATTERNS)
        question_text = pattern.format(coin=coin.name)

        validation_info = {
            "coin_id": coin.coin_id,
            "coin_name": coin.name,
            "coin_symbol": coin.symbol,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=f"https://www.coingecko.com/en/coins/{coin.coin_id}",
            variables={"coin": coin},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        return """Task-Specific Rules (CoinGecko - Market Rank):
- Ranks can change in real-time due to market cap fluctuations
- Score 1.0: Within 2 positions of expected
- Score 0.0: More than 2 positions off
- Accept formats: "#5", "5", "5th", "rank 5", "ranked #5", "position 5"
- Note: Lower rank number = higher market cap (rank 1 is highest)"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Get market rank from collected API data (no network fallback)."""
        coin_id = validation_info.get("coin_id", "")
        if not coin_id:
            return GroundTruthResult.fail("No coin_id provided")

        from liveweb_arena.core.gt_collector import get_current_gt_collector
        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.fail("No GT collector")

        collected = gt_collector.get_collected_api_data()
        if coin_id not in collected:
            return GroundTruthResult.fail(
                f"CoinGecko data for '{coin_id}' not collected. "
                f"Available: {list(collected.keys())[:10]}"
            )

        coin_data = collected[coin_id]
        rank = coin_data.get("market_cap_rank")
        if rank is not None:
            return GroundTruthResult.ok(f"#{rank}")

        return GroundTruthResult.fail("Missing market cap rank data in collected data")

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate rank answer."""
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
        # Parse expected rank
        expected_match = re.search(r'(\d+)', ground_truth)
        if not expected_match:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Could not parse expected rank",
            )
        expected_rank = int(expected_match.group(1))

        # Parse actual rank from answer
        actual_match = re.search(r'(\d+)', answer)
        if not actual_match:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Could not find rank number in answer",
            )
        actual_rank = int(actual_match.group(1))

        diff = abs(actual_rank - expected_rank)

        if diff <= 2:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=answer,
                details=f"Rank match (diff: {diff})",
            )
        else:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details=f"Outside tolerance (diff: {diff})",
            )

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> TriggerConfig:
        """Trigger when AI visits the coin's page."""
        coin_id = validation_info.get("coin_id", "")
        trigger = UrlPatternTrigger(
            domains=["coingecko.com"],
            url_contains=coin_id if coin_id else None,
        )
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "coingecko"

    def get_gt_source(self):
        """Market cap rank is visible on the coin page."""
        from liveweb_arena.core.gt_collector import GTSourceType
        return GTSourceType.PAGE_ONLY

    def get_page_fields(self):
        """Fields extractable from CoinGecko page."""
        return ["market_cap_rank"]
