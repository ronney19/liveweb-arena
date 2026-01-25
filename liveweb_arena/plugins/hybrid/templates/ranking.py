"""Asset Ranking - Cross-site comparison task requiring exploration and memory"""

import random
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

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
    asset_id: str
    name: str
    source: str  # "coingecko" or "stooq"
    symbol: str


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


@register_template("hybrid_ranking")
class HybridRankingTemplate(QuestionTemplate):
    """
    Cross-site ranking task requiring exploration and comparison.

    The agent must rank multiple assets by their 24h performance,
    which requires:
    1. Visiting multiple pages across different sites
    2. Extracting and remembering performance values
    3. Comparing and ordering all values
    4. Outputting a correctly ordered list

    Scoring uses ranking correlation - partial credit for partially
    correct orderings.
    """

    PATTERNS = [
        "Rank these assets by their 24-hour performance from best to worst: {assets}.",
        "Order the following by today's percentage change (highest first): {assets}.",
        "Sort these assets from best to worst daily performer: {assets}.",
    ]

    def __init__(self):
        super().__init__("hybrid_ranking")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a ranking task with 4-5 mixed assets."""
        rng = random.Random(seed)

        # Select 2 crypto and 2-3 traditional assets
        num_crypto = 2
        num_traditional = rng.randint(2, 3)

        selected_crypto = rng.sample(CRYPTO_ASSETS, num_crypto)
        selected_traditional = rng.sample(TRADITIONAL_ASSETS, num_traditional)

        all_assets = selected_crypto + selected_traditional
        rng.shuffle(all_assets)

        asset_names = [a.name for a in all_assets]
        assets_str = ", ".join(asset_names)

        pattern = rng.choice(self.PATTERNS)
        question_text = pattern.format(assets=assets_str)

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
            expected_steps=len(all_assets) * 2 + 3,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        asset_names = validation_info.get("asset_names", [])
        return f"""Task-Specific Rules (Hybrid - Asset Ranking):
- Rank assets by 24-hour percentage change from best to worst
- Assets: {', '.join(asset_names)}
- Score 1.0: Perfect ranking (all positions correct)
- Score 0.5: Most positions correct (Kendall tau >= 0.6)
- Score 0.0: Poor ranking (Kendall tau < 0.6)
- Output format: ordered list, e.g., "1. Asset A, 2. Asset B, ..." """

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Fetch all asset performances and return correct ranking."""
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
                else:
                    change = await get_stooq_24h_change(symbol)

                results.append({"name": name, "change": change})
            except RuntimeError as e:
                # RuntimeError means we've exhausted all retries
                errors.append(f"{name}: {str(e)}")
            except Exception as e:
                errors.append(f"{name}: {str(e)}")

        # All assets must have data for fair evaluation
        if errors:
            error_msg = "; ".join(errors)
            return GroundTruthResult.retry(f"Could not fetch all asset data: {error_msg}")

        if len(results) < 2:
            return GroundTruthResult.fail("Insufficient assets for ranking")

        # Sort by change descending
        sorted_results = sorted(results, key=lambda x: x["change"], reverse=True)

        # Build ground truth string
        ranking_str = " > ".join([f"{r['name']}({r['change']:+.2f}%)" for r in sorted_results])
        names_only = [r["name"] for r in sorted_results]

        return GroundTruthResult.ok(f"{ranking_str} | Order: {names_only}")

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate ranking using Kendall tau correlation."""
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
        expected_order = self._parse_expected_order(ground_truth)
        actual_order = self._parse_answer_order(answer, expected_order)

        if not actual_order or len(actual_order) < 2:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Could not parse ranking from answer",
            )

        tau = self._kendall_tau(expected_order, actual_order)

        if tau >= 0.99:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=answer,
                details=f"Perfect ranking (tau={tau:.2f})",
            )
        elif tau >= 0.6:
            return ValidationResult(
                score=0.5,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details=f"Partial ranking (tau={tau:.2f})",
            )
        else:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details=f"Poor ranking (tau={tau:.2f})",
            )

    def _parse_expected_order(self, ground_truth: str) -> List[str]:
        """Extract ordered list from ground truth."""
        # Format: "... | Order: ['A', 'B', 'C']"
        match = re.search(r"Order:\s*\[([^\]]+)\]", ground_truth)
        if match:
            items = match.group(1)
            return [s.strip().strip("'\"") for s in items.split(",")]
        return []

    def _parse_answer_order(self, answer: str, expected_names: List[str]) -> List[str]:
        """Extract ordering from answer based on known asset names."""
        answer_lower = answer.lower()

        # Build name variations for matching
        variations = {
            "bitcoin": ["btc"], "ethereum": ["eth"], "solana": ["sol"],
            "xrp": ["ripple"], "cardano": ["ada"], "dogecoin": ["doge"],
            "avalanche": ["avax"], "polkadot": ["dot"], "chainlink": ["link"],
            "litecoin": ["ltc"], "uniswap": ["uni"], "stellar": ["xlm"],
            "cosmos": ["atom"], "near": [], "aptos": ["apt"], "sui": [],
            "tao": ["bittensor"],
            "gold": ["xau"], "silver": ["xag"], "crude oil": ["oil", "crude", "wti"],
            "natural gas": ["gas", "natgas"], "copper": ["hg"],
            "s&p 500": ["s&p", "sp500", "spx"], "dow jones": ["dow", "dji", "djia"],
            "nasdaq 100": ["nasdaq", "ndx"], "dax": [], "ftse 100": ["ftse", "ukx"],
            "apple": ["aapl"], "microsoft": ["msft"], "nvidia": ["nvda"],
            "tesla": ["tsla"], "google": ["googl", "alphabet"], "amazon": ["amzn"],
            "meta": ["facebook"], "jpmorgan": ["jpm"], "visa": [], "walmart": ["wmt"],
        }
        name_map = {}
        for name in expected_names:
            name_lower = name.lower()
            name_map[name_lower] = name
            if name_lower in variations:
                for var in variations[name_lower]:
                    name_map[var] = name

        # Find positions of each asset in the answer
        positions = []
        for variant, canonical in name_map.items():
            pos = answer_lower.find(variant)
            if pos >= 0:
                # Avoid duplicates
                if not any(p[1] == canonical for p in positions):
                    positions.append((pos, canonical))

        # Sort by position in answer
        positions.sort(key=lambda x: x[0])
        return [p[1] for p in positions]

    def _kendall_tau(self, expected: List[str], actual: List[str]) -> float:
        """Calculate Kendall tau correlation between two rankings."""
        # Create position maps
        exp_pos = {name: i for i, name in enumerate(expected)}

        # Filter actual to only include items in expected
        actual_filtered = [a for a in actual if a in exp_pos]

        if len(actual_filtered) < 2:
            return 0.0

        n = len(actual_filtered)
        concordant = 0
        discordant = 0

        for i in range(n):
            for j in range(i + 1, n):
                a_i, a_j = actual_filtered[i], actual_filtered[j]
                exp_i, exp_j = exp_pos.get(a_i, 0), exp_pos.get(a_j, 0)

                # In actual, i comes before j
                # Check if same order in expected
                if exp_i < exp_j:
                    concordant += 1
                elif exp_i > exp_j:
                    discordant += 1

        total = concordant + discordant
        if total == 0:
            return 1.0

        return (concordant - discordant) / total

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> TriggerConfig:
        """Trigger on Stooq visit (typically visited after CoinGecko)."""
        trigger = UrlPatternTrigger(domains=["stooq.com"])
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)
