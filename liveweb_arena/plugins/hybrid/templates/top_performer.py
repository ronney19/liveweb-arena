"""Top Performer Search - RL-friendly cross-site optimization task"""

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult,
)
from ..utils import get_crypto_24h_change, get_stooq_24h_change


@dataclass
class AssetSpec:
    """Specification for a tradeable asset"""
    asset_id: str       # API identifier
    name: str           # Display name
    source: str         # "coingecko" or "stooq"
    symbol: str         # Trading symbol for Stooq


# Asset pool - mixed crypto and traditional finance
CRYPTO_ASSETS = [
    # Major cryptocurrencies (all cached in CoinGecko adapter)
    AssetSpec("bitcoin", "Bitcoin", "coingecko", ""),
    AssetSpec("ethereum", "Ethereum", "coingecko", ""),
    AssetSpec("solana", "Solana", "coingecko", ""),
    AssetSpec("ripple", "XRP", "coingecko", ""),
    AssetSpec("cardano", "Cardano", "coingecko", ""),
    AssetSpec("dogecoin", "Dogecoin", "coingecko", ""),
    AssetSpec("avalanche-2", "Avalanche", "coingecko", ""),
    AssetSpec("polkadot", "Polkadot", "coingecko", ""),
    AssetSpec("chainlink", "Chainlink", "coingecko", ""),
    AssetSpec("litecoin", "Litecoin", "coingecko", ""),
    AssetSpec("uniswap", "Uniswap", "coingecko", ""),
    AssetSpec("stellar", "Stellar", "coingecko", ""),
    AssetSpec("cosmos", "Cosmos", "coingecko", ""),
    AssetSpec("near", "NEAR", "coingecko", ""),
    AssetSpec("aptos", "Aptos", "coingecko", ""),
    AssetSpec("sui", "Sui", "coingecko", ""),
    AssetSpec("bittensor", "TAO", "coingecko", ""),
    # Additional cached coins
    AssetSpec("tether", "Tether", "coingecko", ""),
    AssetSpec("binancecoin", "BNB", "coingecko", ""),
    AssetSpec("tron", "TRON", "coingecko", ""),
    AssetSpec("bitcoin-cash", "Bitcoin Cash", "coingecko", ""),
    AssetSpec("internet-computer", "Internet Computer", "coingecko", ""),
    AssetSpec("filecoin", "Filecoin", "coingecko", ""),
    AssetSpec("hedera", "Hedera", "coingecko", ""),
]

TRADITIONAL_ASSETS = [
    # Commodities (using forex-style symbols that work on Stooq)
    AssetSpec("xauusd", "Gold", "stooq", "xauusd"),
    AssetSpec("xagusd", "Silver", "stooq", "xagusd"),
    # Indices
    AssetSpec("^spx", "S&P 500", "stooq", "^spx"),
    AssetSpec("^dji", "Dow Jones", "stooq", "^dji"),
    AssetSpec("^ndx", "NASDAQ 100", "stooq", "^ndx"),
    AssetSpec("^dax", "DAX", "stooq", "^dax"),
    AssetSpec("^ukx", "FTSE 100", "stooq", "^ukx"),
    AssetSpec("^nkx", "Nikkei 225", "stooq", "^nkx"),
    # US Stocks (all cached)
    AssetSpec("aapl.us", "Apple", "stooq", "aapl.us"),
    AssetSpec("msft.us", "Microsoft", "stooq", "msft.us"),
    AssetSpec("nvda.us", "NVIDIA", "stooq", "nvda.us"),
    AssetSpec("tsla.us", "Tesla", "stooq", "tsla.us"),
    AssetSpec("googl.us", "Google", "stooq", "googl.us"),
    AssetSpec("amzn.us", "Amazon", "stooq", "amzn.us"),
    AssetSpec("meta.us", "Meta", "stooq", "meta.us"),
    AssetSpec("jpm.us", "JPMorgan", "stooq", "jpm.us"),
    AssetSpec("v.us", "Visa", "stooq", "v.us"),
    AssetSpec("wmt.us", "Walmart", "stooq", "wmt.us"),
    AssetSpec("amd.us", "AMD", "stooq", "amd.us"),
    AssetSpec("coin.us", "Coinbase", "stooq", "coin.us"),
]


@register_template("hybrid_top_performer")
class HybridTopPerformerTemplate(QuestionTemplate):
    """
    RL-friendly cross-site optimization task.

    The agent must find which asset has the highest 24h percentage change
    among a mixed set of cryptocurrencies and traditional assets.

    Why this is RL-friendly (not just longer SFT):
    1. EXPLORATION REQUIRED - Must check multiple assets to find the best
    2. OPTIMIZATION OBJECTIVE - Find maximum, not just any valid answer
    3. NO FIXED PATH - Order of checking is a strategic choice
    4. POLICY LEARNING - Agent can learn heuristics:
       - "Crypto is more volatile, check first"
       - "If found +10%, others unlikely to beat it"
    5. CROSS-SITE - Data spread across CoinGecko and Stooq
    6. NON-DEMONSTRABLE - Expert demo for one instance doesn't generalize
       because optimal strategy depends on actual market values

    SFT limitation: Can only teach "check all in order X", but optimal
    order varies. RL can learn adaptive strategies.
    """

    PATTERNS = [
        "Which of these assets has the highest 24-hour percentage change: {assets}?",
        "Among {assets}, which one gained the most in the last 24 hours?",
        "Find the best performer in the last 24 hours: {assets}.",
        "Which asset has the best daily performance: {assets}?",
    ]

    def __init__(self):
        super().__init__("hybrid_top_performer")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a top performer search task."""
        rng = random.Random(seed)

        # Select 2-3 crypto and 2-3 traditional assets
        num_crypto = rng.randint(2, 3)
        num_traditional = rng.randint(2, 3)

        selected_crypto = rng.sample(CRYPTO_ASSETS, num_crypto)
        selected_traditional = rng.sample(TRADITIONAL_ASSETS, num_traditional)

        all_assets = selected_crypto + selected_traditional
        rng.shuffle(all_assets)  # Randomize order in question

        # Build question with asset names only
        asset_names = [a.name for a in all_assets]
        assets_str = ", ".join(asset_names[:-1]) + f", or {asset_names[-1]}"

        pattern = rng.choice(self.PATTERNS)
        question_text = pattern.format(assets=assets_str)

        # Start URL - let agent choose where to start
        # Default to CoinGecko homepage as neutral starting point
        start_url = "https://www.coingecko.com/"

        validation_info = {
            "assets": [
                {
                    "asset_id": a.asset_id,
                    "name": a.name,
                    "source": a.source,
                    "symbol": a.symbol,
                }
                for a in all_assets
            ],
            "asset_names": asset_names,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=start_url,
            variables={"assets": all_assets},
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=len(all_assets) * 2 + 2,  # Roughly 2 steps per asset + overhead
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        asset_names = validation_info.get("asset_names", [])
        return f"""Task-Specific Rules (Hybrid - Top Performer Search):
- Find which asset has the highest 24-hour percentage change
- Assets to compare: {', '.join(asset_names)}
- Data sources: CoinGecko (crypto), Stooq (stocks/commodities/indices)
- Score 1.0: Correctly identify the top performer
- Score 0.0: Wrong answer
- Must compare actual 24h change values, not guess"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Fetch 24h change for all assets and find the best performer."""
        assets = validation_info.get("assets", [])
        if not assets:
            return GroundTruthResult.fail("No assets provided")

        results = []
        errors = []

        for asset in assets:
            source = asset["source"]
            asset_id = asset["asset_id"]
            name = asset["name"]
            symbol = asset.get("symbol", "")

            try:
                if source == "coingecko":
                    change = await get_crypto_24h_change(asset_id)
                else:  # stooq
                    change = await get_stooq_24h_change(symbol)

                results.append({
                    "name": name,
                    "change": change,
                    "source": source,
                })
            except RuntimeError as e:
                # RuntimeError means we've exhausted all retries
                errors.append(f"{name}: {str(e)}")
            except Exception as e:
                errors.append(f"{name}: {str(e)}")

        # All assets must have data for fair evaluation
        if errors:
            error_msg = "; ".join(errors)
            return GroundTruthResult.retry(f"Could not fetch all asset data: {error_msg}")

        if not results:
            return GroundTruthResult.fail("No assets data fetched")

        # Find the best performer
        best = max(results, key=lambda x: x["change"])

        # Build detailed ground truth
        sorted_results = sorted(results, key=lambda x: x["change"], reverse=True)
        details = ", ".join([f"{r['name']}: {r['change']:+.2f}%" for r in sorted_results])

        return GroundTruthResult.ok(f"{best['name']} ({best['change']:+.2f}%) | All: {details}")

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate that the agent identified the correct top performer."""
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
        # Expected format: "AssetName (+X.XX%) | All: ..."
        expected_name = ground_truth.split(" (")[0].lower()

        answer_lower = answer.lower()

        # Check if the answer contains the expected asset name
        if expected_name in answer_lower:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=answer,
                details="Correctly identified top performer",
            )

        # Check for common variations
        name_variations = {
            "bitcoin": ["btc", "bitcoin"],
            "ethereum": ["eth", "ethereum"],
            "solana": ["sol", "solana"],
            "xrp": ["xrp", "ripple"],
            "cardano": ["ada", "cardano"],
            "dogecoin": ["doge", "dogecoin"],
            "avalanche": ["avax", "avalanche"],
            "polkadot": ["dot", "polkadot"],
            "chainlink": ["link", "chainlink"],
            "litecoin": ["ltc", "litecoin"],
            "uniswap": ["uni", "uniswap"],
            "stellar": ["xlm", "stellar"],
            "cosmos": ["atom", "cosmos"],
            "near": ["near"],
            "aptos": ["apt", "aptos"],
            "sui": ["sui"],
            "tao": ["tao", "bittensor"],
            "gold": ["gold", "xau"],
            "silver": ["silver", "xag"],
            "crude oil": ["oil", "crude", "wti"],
            "natural gas": ["gas", "natgas"],
            "copper": ["copper", "hg"],
            "s&p 500": ["s&p", "spx", "sp500", "s&p 500"],
            "dow jones": ["dow", "dji", "djia"],
            "nasdaq 100": ["nasdaq", "ndx", "nasdaq 100"],
            "dax": ["dax"],
            "ftse 100": ["ftse", "ukx"],
            "apple": ["apple", "aapl"],
            "microsoft": ["microsoft", "msft"],
            "nvidia": ["nvidia", "nvda"],
            "tesla": ["tesla", "tsla"],
            "google": ["google", "googl", "alphabet"],
            "amazon": ["amazon", "amzn"],
            "meta": ["meta", "facebook"],
            "jpmorgan": ["jpmorgan", "jpm"],
            "visa": ["visa"],
            "walmart": ["walmart", "wmt"],
        }

        for canonical, variations in name_variations.items():
            if canonical in expected_name:
                for var in variations:
                    if var in answer_lower:
                        return ValidationResult(
                            score=1.0,
                            is_correct=True,
                            expected=ground_truth,
                            actual=answer,
                            details=f"Correctly identified top performer (matched '{var}')",
                        )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=ground_truth,
            actual=answer,
            details="Did not identify the correct top performer",
        )

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> TriggerConfig:
        """
        Trigger after visiting enough pages.

        For this task, we want to fetch ground truth after the agent
        has had a chance to explore. We trigger on any Stooq visit
        since that's typically visited after CoinGecko.
        """
        trigger = UrlPatternTrigger(
            domains=["stooq.com"],
        )
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)
