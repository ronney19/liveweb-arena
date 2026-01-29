"""Price performance query template for CoinGecko - HIGH DIFFICULTY, MULTI-STEP"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
)
from liveweb_arena.core.gt_collector import GTSourceType
from .price import CoinVariable, CoinSpec
from ..api_client import CoinGeckoClient


class PerformanceType(Enum):
    """Types of performance queries - 7-day only (webpage shows signed values)"""
    SINGLE = "single_7d"  # Single coin 7-day change
    COMPARE = "compare_7d"  # Which coin performed better in 7 days


@register_template("coingecko_performance")
class CoinGeckoPerformanceTemplate(QuestionTemplate):
    """
    Template for 7-day price performance queries - HIGH DIFFICULTY, MULTI-STEP.

    Uses 7-day data because CoinGecko webpage shows signed percentages in
    descriptive text for 7d (e.g., "With a price decline of -18.30% in the last 7 days").
    30-day data only shows unsigned values in the table, making sign detection unreliable.

    Requires multi-step navigation:
    - Single coin: Navigate to coin page, find 7d performance data
    - Comparison: Navigate to both coins and compare their 7d changes

    Examples:
    - How much has Bitcoin's price changed in the last 7 days?
    - Which performed better over the last week: Ethereum or Solana?
    """

    GT_SOURCE = GTSourceType.API_ONLY  # 7d performance requires special API param

    SINGLE_PATTERNS = [
        "How much has {coin}'s price changed in the last 7 days?",
        "What is {coin}'s 7-day price performance?",
        "How did {coin} perform over the past week?",
        "{coin}'s price change in the last 7 days?",
        "What was {coin}'s weekly price movement?",
    ]

    COMPARE_PATTERNS = [
        "Which performed better over the last 7 days: {coin1} or {coin2}?",
        "Between {coin1} and {coin2}, which had better 7-day performance?",
        "Compare the 7-day performance of {coin1} vs {coin2}. Which did better?",
        "In the past week, did {coin1} or {coin2} have a higher price change?",
        "Which coin outperformed the other in the last 7 days: {coin1} or {coin2}?",
    ]

    # Good coin pairs for comparison (different sectors/sizes)
    COMPARISON_PAIRS = [
        ("bitcoin", "ethereum"),
        ("solana", "cardano"),
        ("ripple", "dogecoin"),
        ("polkadot", "chainlink"),
        ("avalanche-2", "near"),
        ("uniswap", "aave"),
        ("litecoin", "bitcoin-cash"),
        ("stellar", "algorand"),
        ("cosmos", "injective-protocol"),
        ("render-token", "fetch-ai"),
        ("bittensor", "near"),
        ("sui", "aptos"),
        ("arbitrum", "optimism"),
        ("polygon-ecosystem-token", "immutable-x"),
    ]

    def __init__(self):
        super().__init__("coingecko_performance")
        self._coin_var = CoinVariable()

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a 7-day performance question."""
        rng = random.Random(seed)

        # Select performance type
        if variant is not None:
            perf_type = PerformanceType.SINGLE if variant % 2 == 0 else PerformanceType.COMPARE
        else:
            # Weight towards comparison (more complex, as requested)
            perf_type = rng.choices(
                [PerformanceType.SINGLE, PerformanceType.COMPARE],
                weights=[40, 60]
            )[0]

        if perf_type == PerformanceType.SINGLE:
            return self._generate_single(rng)
        else:
            return self._generate_comparison(rng)

    def _generate_single(self, rng: random.Random) -> GeneratedQuestion:
        """Generate single coin 7-day performance question."""
        coin = self._coin_var.sample(rng)

        pattern = rng.choice(self.SINGLE_PATTERNS)
        question_text = pattern.format(coin=coin.name)

        validation_info = {
            "coin_id": coin.coin_id,
            "coin_name": coin.name,
            "coin_symbol": coin.symbol,
            "perf_type": PerformanceType.SINGLE.value,
            "period": "7d",
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=f"https://www.coingecko.com/en/coins/{coin.coin_id}",
            variables={"coin": coin, "perf_type": PerformanceType.SINGLE},
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=5,  # Single page navigation
        )

    def _generate_comparison(self, rng: random.Random) -> GeneratedQuestion:
        """Generate 7-day comparison performance question."""
        # Select a coin pair
        pair = rng.choice(self.COMPARISON_PAIRS)
        # Randomly swap order
        if rng.random() > 0.5:
            pair = (pair[1], pair[0])

        coin1_id, coin2_id = pair

        # Get coin specs
        coin1 = self._get_coin_spec(coin1_id)
        coin2 = self._get_coin_spec(coin2_id)

        pattern = rng.choice(self.COMPARE_PATTERNS)
        question_text = pattern.format(coin1=coin1.name, coin2=coin2.name)

        validation_info = {
            "coin1_id": coin1.coin_id,
            "coin1_name": coin1.name,
            "coin2_id": coin2.coin_id,
            "coin2_name": coin2.name,
            "perf_type": PerformanceType.COMPARE.value,
            "period": "7d",
        }

        # Start at first coin's page - agent needs to read then navigate to second coin
        return GeneratedQuestion(
            question_text=question_text,
            start_url=f"https://www.coingecko.com/en/coins/{coin1_id}",
            variables={"coin1": coin1, "coin2": coin2, "perf_type": PerformanceType.COMPARE},
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=8,  # read coin1 + goto coin2 + read coin2 + submit (start at coin1)
        )

    def _get_coin_spec(self, coin_id: str) -> CoinSpec:
        """Get CoinSpec for a coin ID."""
        coin_map = {
            "bitcoin": ("BTC", "Bitcoin"),
            "ethereum": ("ETH", "Ethereum"),
            "solana": ("SOL", "Solana"),
            "cardano": ("ADA", "Cardano"),
            "ripple": ("XRP", "XRP"),
            "dogecoin": ("DOGE", "Dogecoin"),
            "polkadot": ("DOT", "Polkadot"),
            "chainlink": ("LINK", "Chainlink"),
            "avalanche-2": ("AVAX", "Avalanche"),
            "near": ("NEAR", "NEAR Protocol"),
            "uniswap": ("UNI", "Uniswap"),
            "aave": ("AAVE", "Aave"),
            "litecoin": ("LTC", "Litecoin"),
            "bitcoin-cash": ("BCH", "Bitcoin Cash"),
            "stellar": ("XLM", "Stellar"),
            "algorand": ("ALGO", "Algorand"),
            "cosmos": ("ATOM", "Cosmos"),
            "injective-protocol": ("INJ", "Injective"),
            "render-token": ("RENDER", "Render"),
            "fetch-ai": ("FET", "Fetch.ai"),
            "bittensor": ("TAO", "Bittensor"),
            "sui": ("SUI", "Sui"),
            "aptos": ("APT", "Aptos"),
            "arbitrum": ("ARB", "Arbitrum"),
            "optimism": ("OP", "Optimism"),
            "polygon-ecosystem-token": ("POL", "Polygon"),
            "immutable-x": ("IMX", "Immutable"),
        }
        symbol, name = coin_map.get(coin_id, (coin_id.upper(), coin_id.title()))
        return CoinSpec(coin_id, symbol, name)

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        perf_type = validation_info.get("perf_type", "single_7d")

        if "compare" in perf_type:
            return """Task-Specific Rules (CoinGecko - 7-Day Performance Comparison):
- Answer must identify which coin performed better over 7 days
- Score 1.0: Correctly identifies the better performer (percentages within 2pp tolerance)
- Score 0.0: Wrong coin identified as better performer
- Accept formats: "Bitcoin", "BTC performed better", "Ethereum had less decline"
- Note: "Better" = higher percentage (less negative or more positive)"""

        return """Task-Specific Rules (CoinGecko - 7-Day Price Performance):
- Performance is the 7-day percentage price change
- Score 1.0: Percentage within 3pp of expected
- Score 0.0: More than 3pp off
- Accept formats: "+5.2%", "-3.1%", "up 5%", "down 3%", "declined 18%"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Get 7-day performance data from collected API data (no network fallback)."""
        perf_type = validation_info.get("perf_type", "single_7d")

        if "compare" in perf_type:
            return await self._get_comparison_truth(validation_info)
        else:
            return await self._get_single_truth(validation_info)

    async def _get_single_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Get ground truth for single coin 7-day performance from collected data."""
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
        change = coin_data.get("price_change_percentage_7d_in_currency")
        if change is None:
            return GroundTruthResult.fail("7-day price change data not available in collected data")

        sign = "+" if change >= 0 else ""
        return GroundTruthResult.ok(f"{sign}{change:.2f}%")

    async def _get_comparison_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Get ground truth for 7-day performance comparison from collected data."""
        coin1_id = validation_info.get("coin1_id", "")
        coin2_id = validation_info.get("coin2_id", "")
        coin1_name = validation_info.get("coin1_name", "")
        coin2_name = validation_info.get("coin2_name", "")

        if not coin1_id or not coin2_id:
            return GroundTruthResult.fail("Missing coin IDs for comparison")

        from liveweb_arena.core.gt_collector import get_current_gt_collector
        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.fail("No GT collector")

        collected = gt_collector.get_collected_api_data()
        missing = []
        if coin1_id not in collected:
            missing.append(coin1_id)
        if coin2_id not in collected:
            missing.append(coin2_id)
        if missing:
            return GroundTruthResult.fail(
                f"CoinGecko data for {missing} not collected. "
                f"Available: {list(collected.keys())[:10]}"
            )

        coin1_data = collected[coin1_id]
        coin2_data = collected[coin2_id]

        change1 = coin1_data.get("price_change_percentage_7d_in_currency")
        change2 = coin2_data.get("price_change_percentage_7d_in_currency")

        if change1 is None or change2 is None:
            return GroundTruthResult.fail("7-day price change data not available in collected data")

        # Determine winner (higher change is better)
        if change1 > change2:
            winner = coin1_name
            winner_change = change1
            loser_change = change2
        elif change2 > change1:
            winner = coin2_name
            winner_change = change2
            loser_change = change1
        else:
            return GroundTruthResult.ok(f"Tie: both at {change1:.2f}%")

        sign1 = "+" if winner_change >= 0 else ""
        sign2 = "+" if loser_change >= 0 else ""
        return GroundTruthResult.ok(
            f"{winner} ({sign1}{winner_change:.2f}% vs {sign2}{loser_change:.2f}%)"
        )

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate 7-day performance answer."""
        import re

        result = await self.get_ground_truth(validation_info)
        perf_type = validation_info.get("perf_type", "single_7d")

        if not result.success:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        ground_truth = result.value

        # Handle comparison
        if "compare" in perf_type:
            return self._validate_comparison(answer, ground_truth, validation_info)

        # Handle single coin percentage
        return self._validate_single(answer, ground_truth)

    def _validate_single(self, answer: str, ground_truth: str) -> ValidationResult:
        """Validate single coin 7-day performance answer."""
        import re

        # Parse expected percentage
        exp_match = re.search(r'([+-]?[\d.]+)', ground_truth)
        if not exp_match:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Could not parse expected percentage",
            )
        expected_pct = float(exp_match.group(1))

        # Parse actual percentage from answer
        act_match = re.search(r'([+-]?[\d.]+)\s*%?', answer)
        if not act_match:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Could not find percentage in answer",
            )
        actual_pct = float(act_match.group(1))

        # Handle sign: if answer says "down/decline/drop", negate positive values
        answer_lower = answer.lower()
        if any(word in answer_lower for word in ["down", "drop", "fell", "lost", "decline", "decrease"]):
            if actual_pct > 0:
                actual_pct = -actual_pct

        diff = abs(expected_pct - actual_pct)

        if diff <= 3:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=answer,
                details=f"Within 3pp tolerance (diff: {diff:.2f}pp)",
            )
        else:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details=f"Outside tolerance (diff: {diff:.2f}pp)",
            )

    def _validate_comparison(
        self,
        answer: str,
        ground_truth: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate 7-day comparison answer."""
        coin1_name = validation_info.get("coin1_name", "").lower()
        coin2_name = validation_info.get("coin2_name", "").lower()

        # Handle tie
        if "Tie" in ground_truth:
            answer_lower = answer.lower()
            if coin1_name in answer_lower or coin2_name in answer_lower or "tie" in answer_lower or "same" in answer_lower:
                return ValidationResult(
                    score=1.0,
                    is_correct=True,
                    expected=ground_truth,
                    actual=answer,
                    details="Tie correctly identified or either coin mentioned",
                )
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Neither coin identified in tie situation",
            )

        # Find winner in ground truth
        gt_lower = ground_truth.lower()
        winner = None
        if coin1_name in gt_lower.split("(")[0]:
            winner = coin1_name
        elif coin2_name in gt_lower.split("(")[0]:
            winner = coin2_name

        if not winner:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Could not determine winner from ground truth",
            )

        answer_lower = answer.lower()
        loser = coin2_name if winner == coin1_name else coin1_name

        # Check if answer mentions the winner as better
        if winner in answer_lower:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=answer,
                details=f"Correctly identified {winner} as better performer",
            )

        # Check if they mentioned the loser as worse
        if loser in answer_lower and any(word in answer_lower for word in ["worse", "lower", "less", "underperformed", "more"]):
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=answer,
                details=f"Correctly identified {loser} as worse performer",
            )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=ground_truth,
            actual=answer,
            details=f"Did not identify {winner} as the better performer",
        )

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> TriggerConfig:
        """Trigger when AI visits relevant coin pages."""
        perf_type = validation_info.get("perf_type", "single_7d")

        if "compare" in perf_type:
            coin1_id = validation_info.get("coin1_id", "")
            trigger = UrlPatternTrigger(
                domains=["coingecko.com"],
                url_contains=coin1_id if coin1_id else None,
            )
        else:
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
