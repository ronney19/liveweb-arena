"""24h Trading Volume query template for CoinGecko - LOW DIFFICULTY"""

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


@register_template("coingecko_volume")
class CoinGeckoVolumeTemplate(QuestionTemplate):
    """
    Template for 24h trading volume queries - LOW DIFFICULTY.

    Simple single-value lookup on coin page.

    Examples:
    - What is Bitcoin's 24-hour trading volume?
    - How much Ethereum was traded in the last 24 hours?
    """

    PATTERNS = [
        "What is {coin}'s 24-hour trading volume?",
        "How much {coin} was traded in the last 24 hours?",
        "What is the 24h trading volume for {coin}?",
        "What is {coin}'s daily trading volume?",
    ]

    def __init__(self):
        super().__init__("coingecko_volume")
        self._coin_var = CoinVariable()

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a trading volume question."""
        rng = random.Random(seed)
        coin = self._coin_var.sample(rng)

        pattern = rng.choice(self.PATTERNS)
        question_text = pattern.format(coin=coin.name)
        start_url = f"https://www.coingecko.com/en/coins/{coin.coin_id}"

        validation_info = {
            "coin_id": coin.coin_id,
            "coin_name": coin.name,
            "coin_symbol": coin.symbol,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=start_url,
            variables={"coin": coin},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        return """Task-Specific Rules (CoinGecko - 24h Volume):
- Trading volume fluctuates frequently
- Score 1.0: Values match within 20% tolerance
- Score 0.0: Values differ by more than 20%
- Accept formats: $1.2B, $1.2 billion, 1200000000, 1.2B USD"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Get 24h volume from collected API data (no network fallback)."""
        coin_id = validation_info.get("coin_id", "")
        if not coin_id:
            return GroundTruthResult.fail("No coin_id provided")

        # Get data from collected API data only
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
        volume = coin_data.get("total_volume")
        if volume is not None:
            if volume >= 1e9:
                return GroundTruthResult.ok(f"${volume/1e9:.2f} billion")
            elif volume >= 1e6:
                return GroundTruthResult.ok(f"${volume/1e6:.2f} million")
            else:
                return GroundTruthResult.ok(f"${volume:,.0f}")

        return GroundTruthResult.fail("Missing volume data in collected data")

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate trading volume answer."""
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
        expected_val = self._parse_volume(ground_truth)
        actual_val = self._parse_volume(answer)

        if expected_val is None or actual_val is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Could not parse volume values",
            )

        diff_pct = abs(actual_val - expected_val) / expected_val * 100

        if diff_pct <= 20:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=answer,
                details=f"Within 20% tolerance (diff: {diff_pct:.1f}%)",
            )
        else:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details=f"Outside tolerance (diff: {diff_pct:.1f}%)",
            )

    def _parse_volume(self, text: str) -> Optional[float]:
        """Parse volume from text."""
        import re
        if not text:
            return None

        text = text.replace(",", "").replace("$", "").strip().lower()

        multipliers = {
            "trillion": 1e12, "t": 1e12,
            "billion": 1e9, "b": 1e9,
            "million": 1e6, "m": 1e6,
            "thousand": 1e3, "k": 1e3,
        }

        multiplier = 1
        for word, mult in multipliers.items():
            if word in text:
                text = re.sub(rf'\s*{word}\s*', '', text)
                multiplier = mult
                break

        match = re.search(r'[\d.]+', text)
        if match:
            try:
                return float(match.group()) * multiplier
            except ValueError:
                pass
        return None

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
        """Trading volume is visible on the coin page."""
        from liveweb_arena.core.gt_collector import GTSourceType
        return GTSourceType.PAGE_ONLY

    def get_page_fields(self):
        """Fields extractable from CoinGecko page."""
        return ["total_volume"]
