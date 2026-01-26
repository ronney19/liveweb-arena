"""Price comparison template for CoinGecko - MEDIUM DIFFICULTY"""

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


@register_template("coingecko_comparison")
class CoinGeckoComparisonTemplate(QuestionTemplate):
    """
    Template for comparing two cryptocurrencies - MEDIUM DIFFICULTY.

    Requires visiting two coin pages and comparing values.

    Examples:
    - Which has a higher price, Bitcoin or Ethereum?
    - Is Solana's market cap larger than Cardano's?
    - Which coin has more 24h trading volume, DOGE or SHIB?
    """

    # Comparison types
    PRICE_PATTERNS = [
        "Which has a higher price, {coin1} or {coin2}?",
        "Is {coin1} more expensive than {coin2}?",
        "Between {coin1} and {coin2}, which one costs more?",
    ]

    MARKET_CAP_PATTERNS = [
        "Which has a larger market cap, {coin1} or {coin2}?",
        "Is {coin1}'s market cap bigger than {coin2}'s?",
        "Between {coin1} and {coin2}, which has higher market capitalization?",
    ]

    VOLUME_PATTERNS = [
        "Which has more 24h trading volume, {coin1} or {coin2}?",
        "Is {coin1}'s daily volume higher than {coin2}'s?",
        "Between {coin1} and {coin2}, which was traded more in the last 24 hours?",
    ]

    def __init__(self):
        super().__init__("coingecko_comparison")
        self._coin_var = CoinVariable()

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a comparison question."""
        rng = random.Random(seed)

        # Sample two different coins
        coin1, coin2 = self._coin_var.sample_pair(rng)

        # Select comparison type
        if variant is not None:
            comp_type = ["price", "market_cap", "volume"][variant % 3]
        else:
            comp_type = rng.choice(["price", "market_cap", "volume"])

        if comp_type == "price":
            patterns = self.PRICE_PATTERNS
        elif comp_type == "market_cap":
            patterns = self.MARKET_CAP_PATTERNS
        else:
            patterns = self.VOLUME_PATTERNS

        pattern = rng.choice(patterns)
        question_text = pattern.format(coin1=coin1.name, coin2=coin2.name)

        validation_info = {
            "coin1_id": coin1.coin_id,
            "coin1_name": coin1.name,
            "coin2_id": coin2.coin_id,
            "coin2_name": coin2.name,
            "comparison_type": comp_type,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=f"https://www.coingecko.com/en/coins/{coin1.coin_id}",
            variables={"coin1": coin1, "coin2": coin2, "comp_type": comp_type},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        coin1 = validation_info.get("coin1_name", "Coin1")
        coin2 = validation_info.get("coin2_name", "Coin2")
        return f"""Task-Specific Rules (CoinGecko - Comparison):
- The answer must clearly state which coin ({coin1} or {coin2}) is higher/larger
- Score 1.0: Correct coin identified
- Score 0.0: Wrong coin identified or unclear answer
- Accept formats: "{coin1}", "{coin1} is higher", "{coin1} has more", "Yes" (if question is "Is X > Y?")"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Fetch comparison data from CoinGecko API."""
        coin1_id = validation_info.get("coin1_id", "")
        coin2_id = validation_info.get("coin2_id", "")
        coin1_name = validation_info.get("coin1_name", "")
        coin2_name = validation_info.get("coin2_name", "")
        comp_type = validation_info.get("comparison_type", "price")

        if not coin1_id or not coin2_id:
            return GroundTruthResult.fail("Missing coin IDs")

        try:
            data = await CoinGeckoClient.get_coin_market_data(f"{coin1_id},{coin2_id}")

            if not data or len(data) < 2:
                return GroundTruthResult.retry("Incomplete data from CoinGecko API")

            # Find each coin's data
            coin1_data = next((d for d in data if d["id"] == coin1_id), None)
            coin2_data = next((d for d in data if d["id"] == coin2_id), None)

            if not coin1_data or not coin2_data:
                return GroundTruthResult.fail("Could not find data for both coins")

            # Get comparison values
            if comp_type == "price":
                val1 = coin1_data.get("current_price")
                val2 = coin2_data.get("current_price")
                if val1 is None or val2 is None:
                    return GroundTruthResult.fail(f"Missing price data: {coin1_name}={val1}, {coin2_name}={val2}")
            elif comp_type == "market_cap":
                val1 = coin1_data.get("market_cap")
                val2 = coin2_data.get("market_cap")
                # market_cap can be 0 or None for some coins (e.g., no circulating supply data)
                if val1 is None or val2 is None or val1 == 0 or val2 == 0:
                    return GroundTruthResult.fail(
                        f"Invalid market cap data: {coin1_name}={val1}, {coin2_name}={val2}. "
                        "Some coins lack circulating supply data."
                    )
            else:  # volume
                val1 = coin1_data.get("total_volume")
                val2 = coin2_data.get("total_volume")
                if val1 is None or val2 is None:
                    return GroundTruthResult.fail(f"Missing volume data: {coin1_name}={val1}, {coin2_name}={val2}")

            if val1 > val2:
                return GroundTruthResult.ok(f"{coin1_name} (${val1:,.2f} vs ${val2:,.2f})")
            else:
                return GroundTruthResult.ok(f"{coin2_name} (${val2:,.2f} vs ${val1:,.2f})")

        except Exception as e:
            return GroundTruthResult.retry(f"API error: {e}")

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate comparison answer."""
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
        coin1_name = validation_info.get("coin1_name", "").lower()
        coin2_name = validation_info.get("coin2_name", "").lower()

        # Extract winner from ground truth
        winner = ground_truth.split(" (")[0].lower()
        answer_lower = answer.lower()

        # Check if answer mentions the correct coin
        if winner in answer_lower:
            # Make sure it's not saying the wrong one is higher
            other = coin2_name if winner == coin1_name else coin1_name
            # Check for negation patterns
            if other in answer_lower:
                # Both mentioned - check context
                winner_pos = answer_lower.find(winner)
                other_pos = answer_lower.find(other)
                # Simple heuristic: winner should be mentioned first or in affirmative context
                if winner_pos < other_pos:
                    return ValidationResult(
                        score=1.0,
                        is_correct=True,
                        expected=ground_truth,
                        actual=answer,
                        details="Correct coin identified",
                    )
            else:
                return ValidationResult(
                    score=1.0,
                    is_correct=True,
                    expected=ground_truth,
                    actual=answer,
                    details="Correct coin identified",
                )

        # Check for Yes/No answers (for "Is X > Y?" questions)
        is_coin1_winner = winner == coin1_name
        if "yes" in answer_lower and is_coin1_winner:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=answer,
                details="Correct (Yes = coin1 is higher)",
            )
        if "no" in answer_lower and not is_coin1_winner:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=answer,
                details="Correct (No = coin2 is higher)",
            )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=ground_truth,
            actual=answer,
            details="Wrong coin identified or unclear answer",
        )

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> TriggerConfig:
        """Trigger when AI visits either coin's page."""
        coin1_id = validation_info.get("coin1_id", "")
        coin2_id = validation_info.get("coin2_id", "")
        # Trigger on visiting second coin (means AI has seen both)
        trigger = UrlPatternTrigger(
            domains=["coingecko.com"],
            url_contains=coin2_id if coin2_id else None,
        )
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)
