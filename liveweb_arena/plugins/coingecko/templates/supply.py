"""Supply information query template for CoinGecko - HIGH QUALITY"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, TriggerConfig, GroundTruthResult
)
from .price import CoinVariable, CoinSpec
from ..api_client import CoinGeckoClient


class SupplyMetric(Enum):
    """Types of supply metrics"""
    CIRCULATING = "circulating_supply"
    TOTAL = "total_supply"
    MAX = "max_supply"
    PERCENTAGE = "circulating_percentage"  # circulating / max


@register_template("coingecko_supply")
class CoinGeckoSupplyTemplate(QuestionTemplate):
    """
    Template for cryptocurrency supply queries - HIGH QUALITY.

    Tests understanding of tokenomics:
    - Circulating supply: Currently in circulation
    - Total supply: Total created (includes locked/burned)
    - Max supply: Maximum that can ever exist (for capped coins)
    - Circulating percentage: What % of max supply is circulating

    Examples:
    - What is Bitcoin's circulating supply?
    - How many Ethereum tokens are in circulation?
    - What percentage of Cardano's max supply is currently circulating?
    """

    CIRCULATING_PATTERNS = [
        "What is {coin}'s circulating supply?",
        "How many {coin} tokens are currently in circulation?",
        "What is the circulating supply of {coin}?",
    ]

    TOTAL_PATTERNS = [
        "What is {coin}'s total supply?",
        "How many {coin} tokens exist in total?",
        "What is the total supply of {coin}?",
    ]

    MAX_PATTERNS = [
        "What is {coin}'s maximum supply?",
        "What is the max supply cap for {coin}?",
        "How many {coin} tokens can ever exist?",
    ]

    PERCENTAGE_PATTERNS = [
        "What percentage of {coin}'s max supply is currently circulating?",
        "How much of {coin}'s maximum supply is in circulation?",
        "{coin}: what fraction of max supply is circulating?",
    ]

    # Coins with verified max supply caps (for max_supply and percentage questions)
    # Bitcoin: 21M, Cardano: 45B, Ripple: 100B, Litecoin: 84M
    # Avoided: Polkadot, Avalanche, Stellar, Chainlink, Uniswap (inflationary or no cap)
    COINS_WITH_MAX_SUPPLY = [
        "bitcoin", "cardano", "ripple", "litecoin",
    ]

    def __init__(self):
        super().__init__("coingecko_supply")
        self._coin_var = CoinVariable()

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a supply question."""
        rng = random.Random(seed)

        # Select metric type
        if variant is not None:
            metric_idx = variant % 4
            metrics = [SupplyMetric.CIRCULATING, SupplyMetric.TOTAL,
                      SupplyMetric.MAX, SupplyMetric.PERCENTAGE]
            metric = metrics[metric_idx]
        else:
            # Weight towards circulating (most common question)
            metric = rng.choices(
                [SupplyMetric.CIRCULATING, SupplyMetric.TOTAL,
                 SupplyMetric.MAX, SupplyMetric.PERCENTAGE],
                weights=[40, 20, 20, 20]
            )[0]

        # For max supply and percentage, use coins that have max supply
        if metric in [SupplyMetric.MAX, SupplyMetric.PERCENTAGE]:
            # Sample from coins with max supply
            coin = None
            for _ in range(20):
                c = self._coin_var.sample(rng)
                if c.coin_id in self.COINS_WITH_MAX_SUPPLY:
                    coin = c
                    break
            if coin is None:
                # Fallback to Bitcoin
                coin = CoinSpec("bitcoin", "BTC", "Bitcoin")
        else:
            coin = self._coin_var.sample(rng)

        # Select pattern
        if metric == SupplyMetric.CIRCULATING:
            patterns = self.CIRCULATING_PATTERNS
        elif metric == SupplyMetric.TOTAL:
            patterns = self.TOTAL_PATTERNS
        elif metric == SupplyMetric.MAX:
            patterns = self.MAX_PATTERNS
        else:
            patterns = self.PERCENTAGE_PATTERNS

        pattern = rng.choice(patterns)
        question_text = pattern.format(coin=coin.name)

        validation_info = {
            "coin_id": coin.coin_id,
            "coin_name": coin.name,
            "coin_symbol": coin.symbol,
            "metric_type": metric.value,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=f"https://www.coingecko.com/en/coins/{coin.coin_id}",
            variables={"coin": coin, "metric": metric},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        metric_type = validation_info.get("metric_type", "circulating_supply")

        if metric_type == "circulating_percentage":
            return """Task-Specific Rules (CoinGecko - Supply Percentage):
- Score 1.0: Percentage within 5pp of expected
- Score 0.0: More than 5pp off
- Accept formats: 90%, 90.5%, about 90%, approximately 90 percent"""

        return """Task-Specific Rules (CoinGecko - Supply):
- Supply numbers are large and change slowly
- Score 1.0: Value within 10% of expected
- Score 0.0: More than 10% off or wrong metric
- Accept formats: "21M", "21 million", "21,000,000", "21000000"
- Note: Circulating < Total <= Max (when max exists)"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Get supply data from collected API data (no network fallback)."""
        coin_id = validation_info.get("coin_id", "")
        metric_type = validation_info.get("metric_type", "circulating_supply")

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

        if metric_type == "circulating_supply":
            value = coin_data.get("circulating_supply")
        elif metric_type == "total_supply":
            value = coin_data.get("total_supply")
        elif metric_type == "max_supply":
            value = coin_data.get("max_supply")
        elif metric_type == "circulating_percentage":
            circulating = coin_data.get("circulating_supply")
            max_supply = coin_data.get("max_supply")
            if circulating is not None and max_supply is not None:
                percentage = (circulating / max_supply) * 100
                return GroundTruthResult.ok(f"{percentage:.1f}%")
            return GroundTruthResult.fail("Missing circulating or max supply data")
        else:
            return GroundTruthResult.fail(f"Unknown metric type: {metric_type}")

        if value is None:
            return GroundTruthResult.fail(f"Missing {metric_type} data in collected data")

        return GroundTruthResult.ok(self._format_supply(value))

    def _format_supply(self, value: float) -> str:
        """Format supply number for display."""
        if value >= 1e12:
            return f"{value/1e12:.2f} trillion"
        elif value >= 1e9:
            return f"{value/1e9:.2f} billion"
        elif value >= 1e6:
            return f"{value/1e6:.2f} million"
        else:
            return f"{value:,.0f}"

    def _parse_supply(self, text: str) -> Optional[float]:
        """Parse supply from text."""
        import re
        if not text:
            return None

        text = text.replace(",", "").strip().lower()

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

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate supply answer."""
        import re

        result = await self.get_ground_truth(validation_info)
        metric_type = validation_info.get("metric_type", "circulating_supply")

        if not result.success:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        ground_truth = result.value

        # Handle percentage
        if metric_type == "circulating_percentage":
            exp_match = re.search(r'([\d.]+)', ground_truth)
            act_match = re.search(r'([\d.]+)', answer)

            if exp_match and act_match:
                expected_pct = float(exp_match.group(1))
                actual_pct = float(act_match.group(1))
                diff = abs(expected_pct - actual_pct)

                if diff <= 5:
                    return ValidationResult(
                        score=1.0,
                        is_correct=True,
                        expected=ground_truth,
                        actual=answer,
                        details=f"Within 5pp tolerance (diff: {diff:.1f}pp)",
                    )

            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Percentage mismatch or could not parse",
            )

        # Handle supply numbers
        expected_val = self._parse_supply(ground_truth)
        actual_val = self._parse_supply(answer)

        if expected_val is None or actual_val is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Could not parse supply values",
            )

        diff_pct = abs(actual_val - expected_val) / expected_val * 100

        if diff_pct <= 10:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=answer,
                details=f"Within 10% tolerance (diff: {diff_pct:.1f}%)",
            )
        else:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details=f"Outside tolerance (diff: {diff_pct:.1f}%)",
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
        return TriggerConfig(trigger=trigger)

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "coingecko"

    def get_gt_source(self):
        """Supply data is visible on the coin page."""
        from liveweb_arena.core.gt_collector import GTSourceType
        return GTSourceType.PAGE_ONLY

    def get_page_fields(self):
        """Fields extractable from CoinGecko page."""
        return ["circulating_supply", "total_supply", "max_supply"]
