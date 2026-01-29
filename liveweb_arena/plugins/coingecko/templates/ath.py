"""All-Time High (ATH) query template for CoinGecko - HIGH QUALITY"""

import random
from enum import Enum
from typing import Any, Dict, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
)
from .price import CoinVariable
from ..api_client import CoinGeckoClient


class ATHMetric(Enum):
    """Types of ATH metrics"""
    ATH_PRICE = "ath_price"  # The all-time high price
    ATH_CHANGE = "ath_change_percentage"  # % change from ATH


@register_template("coingecko_ath")
class CoinGeckoATHTemplate(QuestionTemplate):
    """
    Template for All-Time High queries - HIGH QUALITY.

    Tests understanding of historical price data:
    - ATH price: What was the highest price ever reached?
    - ATH change: How far below ATH is the current price?

    Examples:
    - What is Bitcoin's all-time high price?
    - How far is Ethereum from its all-time high?
    - What was Solana's ATH in USD?
    """

    ATH_PRICE_PATTERNS = [
        "What is {coin}'s all-time high price in USD?",
        "What was the highest price {coin} ever reached?",
        "What is the ATH (all-time high) for {coin}?",
        "{coin}'s all-time high price?",
    ]

    ATH_CHANGE_PATTERNS = [
        "How far is {coin} from its all-time high (in percentage)?",
        "What percentage below its ATH is {coin} currently trading?",
        "How much has {coin} dropped from its all-time high?",
        "{coin}: what's the percent change from ATH?",
    ]

    def __init__(self):
        super().__init__("coingecko_ath")
        self._coin_var = CoinVariable()

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate an ATH question."""
        rng = random.Random(seed)

        # Select metric type
        if variant is not None:
            metric = ATHMetric.ATH_PRICE if variant % 2 == 0 else ATHMetric.ATH_CHANGE
        else:
            # Weight towards ATH price (more common question)
            metric = rng.choices(
                [ATHMetric.ATH_PRICE, ATHMetric.ATH_CHANGE],
                weights=[60, 40]
            )[0]

        coin = self._coin_var.sample(rng)

        # Select pattern
        if metric == ATHMetric.ATH_PRICE:
            patterns = self.ATH_PRICE_PATTERNS
        else:
            patterns = self.ATH_CHANGE_PATTERNS

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
        metric_type = validation_info.get("metric_type", "ath_price")

        if metric_type == "ath_change_percentage":
            return """Task-Specific Rules (CoinGecko - ATH Change):
- ATH change percentage is negative (current price below ATH)
- Score 1.0: Percentage within 5pp of expected
- Score 0.0: More than 5pp off
- Accept formats: "-75%", "-75.5%", "75% below ATH", "down 75%"
- Note: Positive values also accepted (agent may report as distance)"""

        return """Task-Specific Rules (CoinGecko - ATH Price):
- ATH prices are historical and stable (don't change unless new ATH)
- Score 1.0: Price within 10% of expected
- Score 0.0: More than 10% off
- Accept formats: "$69,000", "69000", "$69K", "69 thousand"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Get ATH data from collected API data (no network fallback)."""
        coin_id = validation_info.get("coin_id", "")
        metric_type = validation_info.get("metric_type", "ath_price")

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

        if metric_type == "ath_price":
            ath = coin_data.get("ath")
            if ath is not None:
                return GroundTruthResult.ok(self._format_price(ath))
            return GroundTruthResult.fail("ATH data not available in collected data")

        elif metric_type == "ath_change_percentage":
            ath_change = coin_data.get("ath_change_percentage")
            if ath_change is not None:
                return GroundTruthResult.ok(f"{ath_change:.1f}%")
            return GroundTruthResult.fail("ATH change percentage not available in collected data")

        return GroundTruthResult.fail(f"Unknown metric type: {metric_type}")

    def _format_price(self, value: float) -> str:
        """Format price for display."""
        if value >= 1000:
            return f"${value:,.2f}"
        elif value >= 1:
            return f"${value:.2f}"
        elif value >= 0.01:
            return f"${value:.4f}"
        else:
            return f"${value:.8f}"

    def _parse_price(self, text: str) -> Optional[float]:
        """Parse price from text."""
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

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate ATH answer."""
        import re

        result = await self.get_ground_truth(validation_info)
        metric_type = validation_info.get("metric_type", "ath_price")

        if not result.success:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        ground_truth = result.value

        # Handle ATH change percentage
        if metric_type == "ath_change_percentage":
            exp_match = re.search(r'([-+]?[\d.]+)', ground_truth)
            act_match = re.search(r'([-+]?[\d.]+)', answer)

            if exp_match and act_match:
                expected_pct = float(exp_match.group(1))
                actual_pct = float(act_match.group(1))

                # Handle positive/negative variations
                # Expected is usually negative, but agent may report as positive
                if expected_pct < 0 and actual_pct > 0:
                    actual_pct = -actual_pct
                elif expected_pct > 0 and actual_pct < 0:
                    actual_pct = -actual_pct

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

        # Handle ATH price
        expected_val = self._parse_price(ground_truth)
        actual_val = self._parse_price(answer)

        if expected_val is None or actual_val is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Could not parse price values",
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
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "coingecko"

    def get_gt_source(self):
        """ATH data is visible on the coin page."""
        from liveweb_arena.core.gt_collector import GTSourceType
        return GTSourceType.PAGE_ONLY

    def get_page_fields(self):
        """Fields extractable from CoinGecko page."""
        return ["ath", "ath_change_percentage"]
