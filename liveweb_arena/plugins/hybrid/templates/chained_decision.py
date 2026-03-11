"""Chained Decision - Multi-step decision tree navigation"""

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, TriggerConfig, GroundTruthResult,
)
from liveweb_arena.core.gt_collector import GTSourceType
from ..utils import get_crypto_24h_change, get_stooq_price, get_stooq_24h_change


@dataclass
class AssetSpec:
    """Asset specification"""
    asset_id: str
    name: str
    source: str
    symbol: str


# First-level condition assets (crypto)
LEVEL1_ASSETS = [
    AssetSpec("bitcoin", "Bitcoin", "coingecko", ""),
    AssetSpec("ethereum", "Ethereum", "coingecko", ""),
    AssetSpec("solana", "Solana", "coingecko", ""),
]

# Second-level condition assets (crypto, different from level 1)
LEVEL2_ASSETS = [
    AssetSpec("cardano", "Cardano", "coingecko", ""),
    AssetSpec("ripple", "XRP", "coingecko", ""),
    AssetSpec("dogecoin", "Dogecoin", "coingecko", ""),
    AssetSpec("polkadot", "Polkadot", "coingecko", ""),
]

# Target assets for different branches (stocks)
BRANCH_TARGETS = {
    "both_up": [
        AssetSpec("nvda.us", "NVIDIA", "stooq", "nvda.us"),
        AssetSpec("tsla.us", "Tesla", "stooq", "tsla.us"),
    ],
    "first_up_second_down": [
        AssetSpec("msft.us", "Microsoft", "stooq", "msft.us"),
        AssetSpec("aapl.us", "Apple", "stooq", "aapl.us"),
    ],
    "first_down": [
        AssetSpec("jpm.us", "JPMorgan", "stooq", "jpm.us"),
        AssetSpec("wmt.us", "Walmart", "stooq", "wmt.us"),
    ],
    "neutral": [
        AssetSpec("ko.us", "Coca-Cola", "stooq", "ko.us"),
        AssetSpec("pep.us", "PepsiCo", "stooq", "pep.us"),
    ],
}


@register_template("hybrid_chained_decision")
class HybridChainedDecisionTemplate(QuestionTemplate):
    """
    Multi-step decision tree navigation task.

    The agent must:
    1. Check first condition (e.g., BTC change)
    2. Based on result, decide whether to check second condition
    3. Based on both conditions, navigate to correct target
    4. Report the target's data

    DECISION TREE STRUCTURE:
    ========================
                    [Check Asset1]
                    /      |      \\
                 >+T%    -T%~+T%   <-T%
                  |        |        |
            [Check Asset2] |    [Target C]
              /    \\       |
           >+T%   <+T%     |
            |       |      |
        [Target A] [Target B] [Target D]

    WHY THIS IS DIFFERENT FROM conditional_branch:
    ===============================================
    conditional_branch: 1 condition -> 3 branches -> done
    chained_decision:   1 condition -> branch -> 2nd condition -> more branches

    The key difference is DEPTH:
    - conditional_branch has depth 1
    - chained_decision has depth 2

    This means:
    - 4 possible end states vs 3
    - More complex navigation
    - Can't be solved with single if-else logic
    - Agent must learn to chain decisions

    Scoring:
    - 1.0: Correct final target + correct value
    - 0.5: Correct branch path, wrong value
    - 0.0: Wrong branch path
    """

    GT_SOURCE = GTSourceType.API_ONLY

    THRESHOLDS = [2.0, 3.0, 4.0]  # Percentage thresholds

    def __init__(self):
        super().__init__("hybrid_chained_decision")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a chained decision task."""
        rng = random.Random(seed)

        # Select assets for each level
        level1_asset = rng.choice(LEVEL1_ASSETS)
        level2_asset = rng.choice(LEVEL2_ASSETS)

        # Select threshold
        threshold = rng.choice(self.THRESHOLDS)

        # Select targets for each branch
        both_up_target = rng.choice(BRANCH_TARGETS["both_up"])
        first_up_second_down_target = rng.choice(BRANCH_TARGETS["first_up_second_down"])
        first_down_target = rng.choice(BRANCH_TARGETS["first_down"])
        neutral_target = rng.choice(BRANCH_TARGETS["neutral"])

        # Build question text
        question_text = (
            f"Follow this decision process:\n"
            f"1. First, check {level1_asset.name}'s 24h change.\n"
            f"2. If {level1_asset.name} is UP more than {threshold}%, "
            f"then check {level2_asset.name}:\n"
            f"   - If {level2_asset.name} is also UP more than {threshold}%, "
            f"report {both_up_target.name}'s stock price.\n"
            f"   - Otherwise, report {first_up_second_down_target.name}'s stock price.\n"
            f"3. If {level1_asset.name} is DOWN more than {threshold}%, "
            f"report {first_down_target.name}'s stock price.\n"
            f"4. Otherwise (neutral), report {neutral_target.name}'s stock price."
        )

        start_url = "https://www.coingecko.com/"

        # Include source and symbol so generate_gt can add required Stooq/Coingecko URLs
        # for GT collection (dataset generation); otherwise hybrid_chained_decision
        # would never visit the level1/level2/target pages and GT fails with
        # "Agent did not visit Stooq page for 'X'".
        validation_info = {
            "level1_asset": {
                "asset_id": level1_asset.asset_id,
                "name": level1_asset.name,
                "source": level1_asset.source,
                "symbol": level1_asset.symbol or level1_asset.asset_id,
            },
            "level2_asset": {
                "asset_id": level2_asset.asset_id,
                "name": level2_asset.name,
                "source": level2_asset.source,
                "symbol": level2_asset.symbol or level2_asset.asset_id,
            },
            "threshold": threshold,
            "targets": {
                "both_up": {
                    "asset_id": both_up_target.asset_id,
                    "name": both_up_target.name,
                    "symbol": both_up_target.symbol,
                    "source": both_up_target.source,
                },
                "first_up_second_down": {
                    "asset_id": first_up_second_down_target.asset_id,
                    "name": first_up_second_down_target.name,
                    "symbol": first_up_second_down_target.symbol,
                    "source": first_up_second_down_target.source,
                },
                "first_down": {
                    "asset_id": first_down_target.asset_id,
                    "name": first_down_target.name,
                    "symbol": first_down_target.symbol,
                    "source": first_down_target.source,
                },
                "neutral": {
                    "asset_id": neutral_target.asset_id,
                    "name": neutral_target.name,
                    "symbol": neutral_target.symbol,
                    "source": neutral_target.source,
                },
            },
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=start_url,
            variables={
                "level1": level1_asset,
                "level2": level2_asset,
                "threshold": threshold,
            },
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=12,  # More steps due to chained decisions
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        l1 = validation_info.get("level1_asset", {}).get("name", "")
        l2 = validation_info.get("level2_asset", {}).get("name", "")
        t = validation_info.get("threshold", 3.0)
        targets = validation_info.get("targets", {})

        return f"""Task-Specific Rules (Hybrid - Chained Decision):
Decision Tree:
  1. Check {l1}'s 24h change
  2. If {l1} > +{t}%:
     - Check {l2}'s 24h change
     - If {l2} > +{t}%: Report {targets.get('both_up', {}).get('name', '')}
     - Else: Report {targets.get('first_up_second_down', {}).get('name', '')}
  3. If {l1} < -{t}%: Report {targets.get('first_down', {}).get('name', '')}
  4. Else: Report {targets.get('neutral', {}).get('name', '')}

Score: 1.0 for correct path + value, 0.5 for correct path only"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Navigate decision tree and get correct target."""
        level1 = validation_info["level1_asset"]
        level2 = validation_info["level2_asset"]
        threshold = validation_info["threshold"]
        targets = validation_info["targets"]

        # Get level 1 condition
        try:
            level1_change = await get_crypto_24h_change(level1.get("asset_id", ""))
        except Exception as e:
            return GroundTruthResult.retry(f"Level1 fetch failed: {e}")

        # Navigate decision tree
        if level1_change > threshold:
            # Level 1 is UP - need to check level 2
            try:
                level2_change = await get_crypto_24h_change(level2.get("asset_id", ""))
            except Exception as e:
                return GroundTruthResult.retry(f"Level2 fetch failed: {e}")

            if level2_change > threshold:
                branch = "both_up"
                path = f"{level1.get('name')}={level1_change:+.2f}% > +{threshold}%, {level2.get('name')}={level2_change:+.2f}% > +{threshold}%"
            else:
                branch = "first_up_second_down"
                path = f"{level1.get('name')}={level1_change:+.2f}% > +{threshold}%, {level2.get('name')}={level2_change:+.2f}% <= +{threshold}%"

        elif level1_change < -threshold:
            branch = "first_down"
            path = f"{level1.get('name')}={level1_change:+.2f}% < -{threshold}%"

        else:
            branch = "neutral"
            path = f"{level1.get('name')}={level1_change:+.2f}% in neutral zone"

        # Get target value
        target = targets[branch]
        try:
            price = await get_stooq_price(target.get("symbol", ""))
        except Exception as e:
            return GroundTruthResult.retry(f"Target fetch failed: {e}")

        return GroundTruthResult.ok(
            f"{target.get('name')}: ${price:,.2f} | "
            f"Branch: {branch} | Path: {path}"
        )

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate chained decision answer."""
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
        answer_lower = answer.lower()

        # Parse GT
        name_match = re.search(r"^([^:]+):", ground_truth)
        price_match = re.search(r"\$([\d,]+\.?\d*)", ground_truth)
        branch_match = re.search(r"Branch:\s*(\w+)", ground_truth)

        if not name_match or not branch_match:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Could not parse ground truth",
            )

        expected_name = name_match.group(1).strip().lower()
        expected_branch = branch_match.group(1)

        # Get all target names to check for wrong branch
        targets = validation_info["targets"]
        all_names = {
            branch: info.get("name", "").lower()
            for branch, info in targets.items()
        }

        # Check which target was mentioned
        mentioned_branch = None
        for branch, name in all_names.items():
            if name in answer_lower:
                mentioned_branch = branch
                break

        if mentioned_branch is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Could not identify which target was mentioned",
            )

        if mentioned_branch != expected_branch:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details=f"Wrong branch: mentioned {all_names[mentioned_branch]}, expected {expected_name}",
            )

        # Correct branch - check value
        if price_match:
            expected_price = float(price_match.group(1).replace(",", ""))
            value_match = re.search(r"\$?([\d,]+\.?\d*)", answer.replace(",", ""))

            if value_match:
                actual_price = float(value_match.group(1))
                tolerance = expected_price * 0.05  # 5% tolerance

                if abs(actual_price - expected_price) <= tolerance:
                    return ValidationResult(
                        score=1.0,
                        is_correct=True,
                        expected=ground_truth,
                        actual=answer,
                        details="Correct branch and price",
                    )
                else:
                    return ValidationResult(
                        score=0.5,
                        is_correct=False,
                        expected=ground_truth,
                        actual=answer,
                        details=f"Correct branch, price off (${actual_price} vs ${expected_price})",
                    )

        return ValidationResult(
            score=0.5,
            is_correct=False,
            expected=ground_truth,
            actual=answer,
            details="Correct branch but no price found",
        )

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> TriggerConfig:
        """Trigger on Stooq visit."""
        trigger = UrlPatternTrigger(domains=["stooq.com"])
        return TriggerConfig(trigger=trigger)

    @classmethod
    def get_cache_source(cls) -> str:
        return "hybrid"

    # === Step-wise Reward Interface ===

    def get_target_assets(self, validation_info: Dict[str, Any]) -> set:
        """Return all condition and target assets."""
        targets = set()
        # Condition assets
        targets.add(validation_info.get("level1_asset", {}).get("asset_id", ""))
        targets.add(validation_info.get("level2_asset", {}).get("asset_id", ""))
        # Target assets
        for branch_info in validation_info.get("targets", {}).values():
            targets.add(branch_info.get("asset_id", ""))
        return targets - {""}

    def get_required_domains(self, validation_info: Dict[str, Any]) -> set:
        """Requires CoinGecko and Stooq."""
        return {"coingecko.com", "stooq.com"}

    def get_reward_overrides(self) -> Optional[Dict[str, float]]:
        """Chained decision - reward intermediate conditions."""
        return {
            "target_asset_reward": 0.15,  # Lower per-asset (more assets)
            "all_targets_bonus": 0.20,
        }
