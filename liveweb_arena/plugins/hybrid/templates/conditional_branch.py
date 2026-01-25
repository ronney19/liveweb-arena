"""Conditional Branch Decision - Pure RL task with unpredictable paths"""

import random
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult,
)
from ..utils import get_crypto_24h_change, get_stooq_price, get_stooq_24h_change


class BranchType(Enum):
    """Types of conditional branches"""
    POSITIVE = "positive"   # condition > threshold
    NEGATIVE = "negative"   # condition < -threshold
    NEUTRAL = "neutral"     # -threshold <= condition <= threshold


@dataclass
class AssetSpec:
    """Specification for a tradeable asset"""
    asset_id: str
    name: str
    source: str  # "coingecko" or "stooq"
    symbol: str


# Condition assets (volatile crypto - good for branching)
CONDITION_ASSETS = [
    AssetSpec("bitcoin", "Bitcoin", "coingecko", ""),
    AssetSpec("ethereum", "Ethereum", "coingecko", ""),
    AssetSpec("solana", "Solana", "coingecko", ""),
    AssetSpec("dogecoin", "Dogecoin", "coingecko", ""),
    AssetSpec("cardano", "Cardano", "coingecko", ""),
    AssetSpec("avalanche-2", "Avalanche", "coingecko", ""),
    AssetSpec("polkadot", "Polkadot", "coingecko", ""),
    AssetSpec("sui", "Sui", "coingecko", ""),
]

# Target assets for each branch (different domains)
POSITIVE_TARGETS = [
    # Tech stocks - tend to correlate with crypto bull runs
    AssetSpec("nvda.us", "NVIDIA", "stooq", "nvda.us"),
    AssetSpec("tsla.us", "Tesla", "stooq", "tsla.us"),
    AssetSpec("amd.us", "AMD", "stooq", "amd.us"),
    AssetSpec("coin.us", "Coinbase", "stooq", "coin.us"),
    AssetSpec("meta.us", "Meta", "stooq", "meta.us"),
    AssetSpec("amzn.us", "Amazon", "stooq", "amzn.us"),
    AssetSpec("msft.us", "Microsoft", "stooq", "msft.us"),
]

NEGATIVE_TARGETS = [
    # Safe havens - tend to rise when crypto falls
    AssetSpec("xauusd", "Gold", "stooq", "xauusd"),
    AssetSpec("xagusd", "Silver", "stooq", "xagusd"),
    AssetSpec("tlt.us", "Treasury Bonds ETF", "stooq", "tlt.us"),
    AssetSpec("jpm.us", "JPMorgan", "stooq", "jpm.us"),
    AssetSpec("wmt.us", "Walmart", "stooq", "wmt.us"),
]

NEUTRAL_TARGETS = [
    # Market indices - for neutral conditions
    AssetSpec("^spx", "S&P 500", "stooq", "^spx"),
    AssetSpec("^dji", "Dow Jones", "stooq", "^dji"),
    AssetSpec("^ndx", "NASDAQ 100", "stooq", "^ndx"),
    AssetSpec("^dax", "DAX", "stooq", "^dax"),
    AssetSpec("^ukx", "FTSE 100", "stooq", "^ukx"),
    AssetSpec("^nkx", "Nikkei 225", "stooq", "^nkx"),
]


@register_template("hybrid_conditional_branch")
class HybridConditionalBranchTemplate(QuestionTemplate):
    """
    Pure RL task with unpredictable execution paths.

    The agent must:
    1. Check a condition (crypto's 24h change)
    2. Based on the condition, decide which asset to query
    3. Report the correct target asset's data

    WHY THIS IS RL-ONLY (NOT SFT-TRAINABLE):
    =========================================
    1. PATH IS RUNTIME-DETERMINED
       - The correct branch depends on REAL-TIME market data
       - At training time, you don't know which path to demonstrate
       - SFT trajectory for "Bitcoin up" is useless when "Bitcoin down"

    2. DEMONSTRATION DOESN'T GENERALIZE
       - Expert demo: "Bitcoin +3% → check NVIDIA → report $140"
       - This demo is WRONG when Bitcoin is -1% (should check Gold)
       - SFT learns the specific sequence, not the conditional logic

    3. SPARSE, DELAYED REWARD
       - No intermediate signal for "correctly identified condition"
       - Only final answer matters
       - RL can learn from this; SFT needs step-by-step supervision

    4. STRATEGY LEARNING REQUIRED
       - Optimal: Check condition FIRST, then go to correct target
       - Suboptimal: Check all assets (wastes steps)
       - RL discovers efficient strategy; SFT copies inefficient demos

    5. COUNTER-EXAMPLE FOR SFT ADVOCATES:
       Q: "Can't we just train on all 3 branches?"
       A: No, because:
          - Real threshold values vary (2%, 3%, 5%)
          - Condition assets vary (BTC, ETH, SOL, DOGE)
          - Target assets vary
          - Combinatorial explosion of trajectories
          - Still doesn't teach the CONDITIONAL LOGIC

    EXPECTED RL BEHAVIOR:
    - Learn to check condition first (efficient)
    - Learn threshold interpretation
    - Learn to navigate to correct target based on condition
    - Generalize to new condition/target combinations
    """

    # Threshold for positive/negative classification
    THRESHOLDS = [2.0, 2.5, 3.0]  # percentage points

    def __init__(self):
        super().__init__("hybrid_conditional_branch")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a conditional branch task."""
        rng = random.Random(seed)

        # Select condition asset
        condition_asset = rng.choice(CONDITION_ASSETS)

        # Select threshold
        threshold = rng.choice(self.THRESHOLDS)

        # Select one target from each branch
        positive_target = rng.choice(POSITIVE_TARGETS)
        negative_target = rng.choice(NEGATIVE_TARGETS)
        neutral_target = rng.choice(NEUTRAL_TARGETS)

        # Build question text
        question_text = self._build_question(
            condition_asset, threshold,
            positive_target, negative_target, neutral_target, rng
        )

        # Start URL - CoinGecko homepage (need to check condition first)
        start_url = "https://www.coingecko.com/"

        validation_info = {
            "condition_asset": {
                "asset_id": condition_asset.asset_id,
                "name": condition_asset.name,
                "source": condition_asset.source,
            },
            "threshold": threshold,
            "positive_target": {
                "asset_id": positive_target.asset_id,
                "name": positive_target.name,
                "source": positive_target.source,
                "symbol": positive_target.symbol,
            },
            "negative_target": {
                "asset_id": negative_target.asset_id,
                "name": negative_target.name,
                "source": negative_target.source,
                "symbol": negative_target.symbol,
            },
            "neutral_target": {
                "asset_id": neutral_target.asset_id,
                "name": neutral_target.name,
                "source": neutral_target.source,
                "symbol": neutral_target.symbol,
            },
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=start_url,
            variables={
                "condition": condition_asset,
                "threshold": threshold,
                "targets": [positive_target, negative_target, neutral_target],
            },
            validation_info=validation_info,
            template_name=self.name,
            # Multi-site navigation: CoinGecko (2-3) + Stooq (2-3) + submit (1)
            # Allow extra steps for search/navigation retries
            expected_steps=10,
        )

    def _build_question(
        self,
        condition: AssetSpec,
        threshold: float,
        positive: AssetSpec,
        negative: AssetSpec,
        neutral: AssetSpec,
        rng: random.Random,
    ) -> str:
        """Build the conditional question text."""
        patterns = [
            (
                f"Check {condition.name}'s 24-hour performance. "
                f"If it's up more than {threshold}%, report {positive.name}'s current price. "
                f"If it's down more than {threshold}%, report {negative.name}'s current price. "
                f"Otherwise, report {neutral.name}'s 24-hour change."
            ),
            (
                f"First, look up {condition.name}'s daily change. "
                f"If {condition.name} gained over {threshold}% today, tell me {positive.name}'s stock price. "
                f"If {condition.name} lost over {threshold}% today, tell me {negative.name}'s price. "
                f"If neither, tell me how {neutral.name} performed today (percentage)."
            ),
            (
                f"Based on {condition.name}'s 24h performance: "
                f"(a) If up >{threshold}% → {positive.name}'s price; "
                f"(b) If down >{threshold}% → {negative.name}'s price; "
                f"(c) Otherwise → {neutral.name}'s daily change percentage."
            ),
        ]
        return rng.choice(patterns)

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        condition = validation_info.get("condition_asset", {}).get("name", "")
        threshold = validation_info.get("threshold", 2.0)
        positive = validation_info.get("positive_target", {}).get("name", "")
        negative = validation_info.get("negative_target", {}).get("name", "")
        neutral = validation_info.get("neutral_target", {}).get("name", "")

        return f"""Task-Specific Rules (Hybrid - Conditional Branch):
- First check {condition}'s 24h change to determine which branch
- If {condition} > +{threshold}% → Report {positive}'s price
- If {condition} < -{threshold}% → Report {negative}'s price
- Otherwise → Report {neutral}'s 24h change percentage
- Score 1.0: Correct branch AND correct value (within 5% for price, 2pp for percentage)
- Score 0.0: Wrong branch OR wrong value
- Must actually check the condition, not guess"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Determine correct branch and fetch target value."""
        condition_asset = validation_info.get("condition_asset", {})
        threshold = validation_info.get("threshold", 2.0)

        # Step 1: Get condition value (with retry)
        try:
            condition_change = await get_crypto_24h_change(
                condition_asset.get("asset_id", "")
            )
        except RuntimeError as e:
            # RuntimeError means we've exhausted all retries
            return GroundTruthResult.fail(f"API error after retries: {e}")
        except Exception as e:
            return GroundTruthResult.fail(f"Unexpected error fetching condition: {e}")

        # Step 2: Determine branch
        if condition_change > threshold:
            branch = BranchType.POSITIVE
            target = validation_info.get("positive_target", {})
            value_type = "price"
        elif condition_change < -threshold:
            branch = BranchType.NEGATIVE
            target = validation_info.get("negative_target", {})
            value_type = "price"
        else:
            branch = BranchType.NEUTRAL
            target = validation_info.get("neutral_target", {})
            value_type = "change"

        # Step 3: Fetch target value (with retry)
        try:
            if value_type == "price":
                target_value = await get_stooq_price(target.get("symbol", ""))
                formatted_value = f"${target_value:,.2f}"
            else:
                target_value = await get_stooq_24h_change(target.get("symbol", ""))
                formatted_value = f"{target_value:+.2f}%"
        except RuntimeError as e:
            # RuntimeError means we've exhausted all retries
            return GroundTruthResult.fail(f"API error after retries: {e}")
        except Exception as e:
            return GroundTruthResult.fail(f"Unexpected error fetching target: {e}")

        condition_name = condition_asset.get("name", "")
        target_name = target.get("name", "")

        return GroundTruthResult.ok(
            f"{target_name}: {formatted_value} | "
            f"Branch: {branch.value} ({condition_name} was {condition_change:+.2f}%, threshold ±{threshold}%)"
        )

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate that agent took correct branch and reported correct value."""
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
        # Format: "TargetName: $XXX.XX | Branch: positive (Condition was +X.XX%)"
        # or:     "TargetName: +X.XX% | Branch: neutral (Condition was +X.XX%)"

        # Extract expected target name
        target_match = re.match(r"([^:]+):\s*", ground_truth)
        if not target_match:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Could not parse ground truth",
            )

        expected_target = target_match.group(1).lower()
        answer_lower = answer.lower()

        # Check if answer mentions the correct target
        target_mentioned = expected_target in answer_lower

        # Also check common variations
        target_variations = {
            "nvidia": ["nvda", "nvidia"],
            "tesla": ["tsla", "tesla"],
            "amd": ["amd"],
            "coinbase": ["coin", "coinbase"],
            "gold": ["gold", "xau", "gc"],
            "silver": ["silver", "xag", "si"],
            "treasury bonds etf": ["tlt", "treasury", "bonds"],
            "s&p 500": ["s&p", "sp500", "spx"],
            "dow jones": ["dow", "dji", "djia"],
            "nasdaq 100": ["nasdaq", "ndx"],
        }

        if not target_mentioned:
            for canonical, variations in target_variations.items():
                if canonical in expected_target:
                    for var in variations:
                        if var in answer_lower:
                            target_mentioned = True
                            break
                    if target_mentioned:
                        break

        if not target_mentioned:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Wrong branch: reported wrong asset",
            )

        # Check if value is approximately correct
        # Extract expected value from ground truth
        value_match = re.search(r":\s*\$?([\d,]+\.?\d*)\s*%?", ground_truth)
        if not value_match:
            # If we can't parse expected value but target is correct, give credit
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=answer,
                details="Correct branch and target identified",
            )

        expected_value_str = value_match.group(1).replace(",", "")
        expected_value = float(expected_value_str)

        # Extract actual value from answer
        actual_match = re.search(r"\$?([\d,]+\.?\d*)\s*%?", answer.replace(",", ""))
        if not actual_match:
            # Target mentioned but no value - partial credit not given in RL design
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Correct target but no value found in answer",
            )

        actual_value = float(actual_match.group(1))

        # Determine tolerance based on value type (price vs percentage)
        is_percentage = "%" in ground_truth.split("|")[0]
        if is_percentage:
            tolerance = 2.0  # 2 percentage points for percentage
            diff = abs(actual_value - expected_value)
            within_tolerance = diff <= tolerance
        else:
            tolerance = 0.05  # 5% for price
            diff = abs(actual_value - expected_value) / expected_value if expected_value else 0
            within_tolerance = diff <= tolerance

        if within_tolerance:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=answer,
                details="Correct branch and value",
            )
        else:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details=f"Correct branch but value off (expected {expected_value}, got {actual_value})",
            )

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> TriggerConfig:
        """Trigger on Stooq visit (agent should visit after checking condition)."""
        trigger = UrlPatternTrigger(domains=["stooq.com"])
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)
