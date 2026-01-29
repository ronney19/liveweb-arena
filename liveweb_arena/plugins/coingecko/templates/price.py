"""Price query template for CoinGecko cryptocurrency data"""

import random
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
)
from ..api_client import CoinGeckoClient


class PriceMetric(Enum):
    """Types of price metrics"""
    CURRENT_PRICE = "current_price"
    CHANGE_24H = "change_24h"
    MARKET_CAP = "market_cap"


@dataclass
class CoinSpec:
    """Specification of a cryptocurrency"""
    coin_id: str  # CoinGecko API ID
    symbol: str   # Trading symbol (BTC, ETH, etc.)
    name: str     # Full name (Bitcoin, Ethereum, etc.)


@dataclass
class MetricSpec:
    """Specification of a price metric"""
    metric: PriceMetric
    display_name: str
    api_field: str
    is_percentage: bool = False


class CoinVariable:
    """Variable for cryptocurrency selection"""

    # Cryptocurrencies with stable CoinGecko IDs
    COINS: List[CoinSpec] = [
        # Top 10 by market cap
        CoinSpec("bitcoin", "BTC", "Bitcoin"),
        CoinSpec("ethereum", "ETH", "Ethereum"),
        CoinSpec("tether", "USDT", "Tether"),
        CoinSpec("ripple", "XRP", "XRP"),
        CoinSpec("solana", "SOL", "Solana"),
        CoinSpec("binancecoin", "BNB", "BNB"),
        CoinSpec("dogecoin", "DOGE", "Dogecoin"),
        CoinSpec("usd-coin", "USDC", "USD Coin"),
        CoinSpec("cardano", "ADA", "Cardano"),
        CoinSpec("staked-ether", "STETH", "Lido Staked Ether"),
        # Top 11-30
        CoinSpec("tron", "TRX", "TRON"),
        CoinSpec("avalanche-2", "AVAX", "Avalanche"),
        CoinSpec("chainlink", "LINK", "Chainlink"),
        CoinSpec("sui", "SUI", "Sui"),
        CoinSpec("stellar", "XLM", "Stellar"),
        CoinSpec("hedera-hashgraph", "HBAR", "Hedera"),
        CoinSpec("shiba-inu", "SHIB", "Shiba Inu"),
        CoinSpec("polkadot", "DOT", "Polkadot"),
        CoinSpec("litecoin", "LTC", "Litecoin"),
        CoinSpec("bitcoin-cash", "BCH", "Bitcoin Cash"),
        CoinSpec("uniswap", "UNI", "Uniswap"),
        CoinSpec("near", "NEAR", "NEAR Protocol"),
        CoinSpec("aptos", "APT", "Aptos"),
        CoinSpec("internet-computer", "ICP", "Internet Computer"),
        CoinSpec("pepe", "PEPE", "Pepe"),
        # AI & Compute tokens
        CoinSpec("bittensor", "TAO", "Bittensor"),
        CoinSpec("render-token", "RENDER", "Render"),
        CoinSpec("fetch-ai", "FET", "Fetch.ai"),
        CoinSpec("akash-network", "AKT", "Akash Network"),
        # DeFi & Layer 2
        CoinSpec("arbitrum", "ARB", "Arbitrum"),
        CoinSpec("optimism", "OP", "Optimism"),
        CoinSpec("polygon-ecosystem-token", "POL", "Polygon"),
        CoinSpec("aave", "AAVE", "Aave"),
        CoinSpec("maker", "MKR", "Maker"),
        # Other notable coins
        CoinSpec("cosmos", "ATOM", "Cosmos"),
        CoinSpec("filecoin", "FIL", "Filecoin"),
        CoinSpec("the-graph", "GRT", "The Graph"),
        CoinSpec("injective-protocol", "INJ", "Injective"),
        CoinSpec("monero", "XMR", "Monero"),
    ]

    def __init__(self, allowed_coins: List[str] = None):
        if allowed_coins:
            self.coins = [c for c in self.COINS if c.coin_id in allowed_coins]
        else:
            self.coins = self.COINS

    def sample(self, rng: random.Random) -> CoinSpec:
        return rng.choice(self.coins)

    def sample_pair(self, rng: random.Random) -> tuple:
        """Sample two different coins for comparison"""
        coins = rng.sample(self.coins, 2)
        return coins[0], coins[1]


class MetricVariable:
    """Variable for metric selection"""

    METRICS: Dict[PriceMetric, MetricSpec] = {
        PriceMetric.CURRENT_PRICE: MetricSpec(
            PriceMetric.CURRENT_PRICE, "current price", "current_price"
        ),
        PriceMetric.CHANGE_24H: MetricSpec(
            PriceMetric.CHANGE_24H, "24-hour price change",
            "price_change_percentage_24h", is_percentage=True
        ),
        PriceMetric.MARKET_CAP: MetricSpec(
            PriceMetric.MARKET_CAP, "market cap", "market_cap"
        ),
    }

    def __init__(self, allowed_metrics: List[PriceMetric] = None):
        self.allowed_metrics = allowed_metrics or list(PriceMetric)

    def sample(self, rng: random.Random) -> MetricSpec:
        metric = rng.choice(self.allowed_metrics)
        return self.METRICS[metric]

    def sample_by_index(self, index: int) -> MetricSpec:
        metric = self.allowed_metrics[index % len(self.allowed_metrics)]
        return self.METRICS[metric]


@register_template("coingecko_price")
class CoinGeckoPriceTemplate(QuestionTemplate):
    """
    Template for cryptocurrency price queries.

    Uses CoinGecko API to fetch real-time price data.

    Examples:
    - What is the current price of Bitcoin?
    - What is Ethereum's 24-hour price change?
    - What is the market cap of Solana?
    """

    PRICE_PATTERNS = [
        "What is the current price of {coin}?",
        "What is {coin}'s current price in USD?",
        "How much is {coin} worth right now?",
        "What is {coin} trading at?",
    ]

    CHANGE_PATTERNS = [
        "What is {coin}'s 24-hour price change?",
        "How much has {coin} changed in the last 24 hours?",
        "What is the 24h change for {coin}?",
        "Is {coin} up or down in the last 24 hours, and by how much?",
    ]

    MARKET_CAP_PATTERNS = [
        "What is the market cap of {coin}?",
        "What is {coin}'s market capitalization?",
        "How much is {coin}'s total market cap?",
    ]

    def __init__(self):
        super().__init__("coingecko_price")
        self._coin_var = CoinVariable()
        self._metric_var = MetricVariable()

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a cryptocurrency price question."""
        rng = random.Random(seed)

        coin = self._coin_var.sample(rng)

        if variant is not None:
            metric = self._metric_var.sample_by_index(variant)
        else:
            metric = self._metric_var.sample(rng)

        question_text = self._build_question(coin, metric, rng)
        start_url = f"https://www.coingecko.com/en/coins/{coin.coin_id}"

        validation_info = {
            "coin_id": coin.coin_id,
            "coin_name": coin.name,
            "coin_symbol": coin.symbol,
            "metric_type": metric.metric.value,
            "api_field": metric.api_field,
            "is_percentage": metric.is_percentage,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=start_url,
            variables={"coin": coin, "metric": metric},
            validation_info=validation_info,
            template_name=self.name,
        )

    def _build_question(
        self,
        coin: CoinSpec,
        metric: MetricSpec,
        rng: random.Random,
    ) -> str:
        if metric.metric == PriceMetric.CURRENT_PRICE:
            patterns = self.PRICE_PATTERNS
        elif metric.metric == PriceMetric.CHANGE_24H:
            patterns = self.CHANGE_PATTERNS
        else:
            patterns = self.MARKET_CAP_PATTERNS

        pattern = rng.choice(patterns)
        return pattern.format(coin=coin.name)

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        metric_type = validation_info.get("metric_type", "current_price")
        is_percentage = validation_info.get("is_percentage", False)

        if is_percentage:
            return """Task-Specific Rules (CoinGecko - 24h Change):
- Score 1.0: Percentage values match within 2pp AND same direction (+/-)
- Score 0.0: Wrong direction or values differ by more than 2pp
- Accept formats: +5.2%, 5.2%, -3.1%, up 5%, down 3%"""

        if metric_type == "market_cap":
            return """Task-Specific Rules (CoinGecko - Market Cap):
- Score 1.0: Values match within 5% (market caps are large numbers)
- Score 0.0: Values differ by more than 5%
- Accept formats: $1.2T, $1.2 trillion, 1200000000000"""

        return """Task-Specific Rules (CoinGecko - Current Price):
- Cryptocurrency prices are highly volatile
- Score 1.0: Values match within 5% tolerance
- Score 0.0: Values differ by more than 5%
- Accept formats: $45,123.45, 45123.45, $45,123"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Get price data from collected API data (no network fallback)."""
        coin_id = validation_info.get("coin_id", "")
        metric_type = validation_info.get("metric_type", "current_price")

        if not coin_id:
            return GroundTruthResult.fail("No coin_id provided")

        # Get data from collected API data only (no network fallback)
        coin_data = None
        from liveweb_arena.core.gt_collector import get_current_gt_collector
        gt_collector = get_current_gt_collector()
        if gt_collector is not None:
            collected = gt_collector.get_collected_api_data()
            if coin_id in collected:
                coin_data = collected[coin_id]

        if coin_data is None:
            collected_keys = list(collected.keys())[:10] if gt_collector else []
            return GroundTruthResult.fail(
                f"CoinGecko data for '{coin_id}' not collected. "
                f"Agent must visit the page. Collected: {collected_keys}"
            )

        # Extract value based on metric type
        if metric_type == "current_price":
            price = coin_data.get("current_price")
            if price is not None:
                # Format with appropriate decimal places for small prices
                if price >= 1:
                    return GroundTruthResult.ok(f"${price:,.2f}")
                elif price >= 0.01:
                    return GroundTruthResult.ok(f"${price:.4f}")
                elif price >= 0.0001:
                    return GroundTruthResult.ok(f"${price:.6f}")
                else:
                    # Very small prices (meme coins)
                    return GroundTruthResult.ok(f"${price:.10f}")

        elif metric_type == "change_24h":
            change = coin_data.get("price_change_percentage_24h")
            if change is not None:
                sign = "+" if change >= 0 else ""
                return GroundTruthResult.ok(f"{sign}{change:.2f}%")

        elif metric_type == "market_cap":
            cap = coin_data.get("market_cap")
            if cap is not None:
                if cap >= 1e12:
                    return GroundTruthResult.ok(f"${cap/1e12:.2f} trillion")
                elif cap >= 1e9:
                    return GroundTruthResult.ok(f"${cap/1e9:.2f} billion")
                else:
                    return GroundTruthResult.ok(f"${cap:,.0f}")

        return GroundTruthResult.fail(f"Missing {metric_type} data in collected data")

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate cryptocurrency price answer."""
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
        metric_type = validation_info.get("metric_type", "current_price")
        is_percentage = validation_info.get("is_percentage", False)

        if is_percentage:
            return self._validate_percentage(answer, ground_truth)
        elif metric_type == "market_cap":
            return self._validate_market_cap(answer, ground_truth)
        else:
            return self._validate_price(answer, ground_truth)

    def _parse_number(self, text: str) -> Optional[float]:
        """Parse a number from text, handling various formats."""
        if not text:
            return None

        text = text.replace(",", "").replace("$", "").strip()

        # Handle trillion/billion/million
        multipliers = {
            "trillion": 1e12, "t": 1e12,
            "billion": 1e9, "b": 1e9,
            "million": 1e6, "m": 1e6,
            "thousand": 1e3, "k": 1e3,
        }

        text_lower = text.lower()
        multiplier = 1
        for word, mult in multipliers.items():
            if word in text_lower:
                text = re.sub(rf'\s*{word}\s*', '', text_lower, flags=re.IGNORECASE)
                multiplier = mult
                break

        # Extract number
        match = re.search(r'[-+]?\d*\.?\d+', text)
        if match:
            try:
                return float(match.group()) * multiplier
            except ValueError:
                pass
        return None

    def _validate_price(self, answer: str, expected: str) -> ValidationResult:
        """Validate current price answer."""
        expected_val = self._parse_number(expected)
        actual_val = self._parse_number(answer)

        if expected_val is None or actual_val is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=expected,
                actual=answer,
                details="Could not parse numeric values",
            )

        diff_pct = abs(actual_val - expected_val) / expected_val * 100

        if diff_pct <= 5:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=expected,
                actual=answer,
                details=f"Within 5% tolerance (diff: {diff_pct:.1f}%)",
            )
        else:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=expected,
                actual=answer,
                details=f"Outside tolerance (diff: {diff_pct:.1f}%)",
            )

    def _validate_percentage(self, answer: str, expected: str) -> ValidationResult:
        """Validate percentage change answer."""
        # Parse expected
        exp_match = re.search(r'([+-]?\d*\.?\d+)', expected.replace(",", ""))
        if not exp_match:
            return ValidationResult(
                score=0.0, is_correct=False, expected=expected,
                actual=answer, details="Could not parse expected percentage",
            )
        expected_val = float(exp_match.group(1))
        if "-" in expected and expected_val > 0:
            expected_val = -expected_val

        # Parse answer - look for percentage or keywords
        answer_lower = answer.lower()
        actual_val = None

        # Check for up/down keywords
        is_up = any(w in answer_lower for w in ["up", "increase", "gain", "positive", "+"])
        is_down = any(w in answer_lower for w in ["down", "decrease", "loss", "negative", "fell", "dropped"])

        # Extract number
        num_match = re.search(r'(\d+\.?\d*)\s*%?', answer.replace(",", ""))
        if num_match:
            actual_val = float(num_match.group(1))
            # Apply direction
            if is_down and actual_val > 0:
                actual_val = -actual_val
            elif is_up and actual_val < 0:
                actual_val = abs(actual_val)
            # If no direction keyword but expected is negative, check for minus sign
            elif "-" in answer and actual_val > 0:
                actual_val = -actual_val

        if actual_val is None:
            return ValidationResult(
                score=0.0, is_correct=False, expected=expected,
                actual=answer, details="Could not parse percentage value",
            )

        # Check direction
        same_direction = (expected_val >= 0 and actual_val >= 0) or (expected_val < 0 and actual_val < 0)
        diff = abs(actual_val - expected_val)

        if same_direction and diff <= 2:
            return ValidationResult(
                score=1.0, is_correct=True, expected=expected,
                actual=answer, details=f"Within 2pp tolerance (diff: {diff:.2f}pp)",
            )
        else:
            return ValidationResult(
                score=0.0, is_correct=False, expected=expected,
                actual=answer, details=f"Outside tolerance or wrong direction (diff: {diff:.2f}pp)",
            )

    def _validate_market_cap(self, answer: str, expected: str) -> ValidationResult:
        """Validate market cap answer."""
        expected_val = self._parse_number(expected)
        actual_val = self._parse_number(answer)

        if expected_val is None or actual_val is None:
            return ValidationResult(
                score=0.0, is_correct=False, expected=expected,
                actual=answer, details="Could not parse market cap values",
            )

        diff_pct = abs(actual_val - expected_val) / expected_val * 100

        if diff_pct <= 5:
            return ValidationResult(
                score=1.0, is_correct=True, expected=expected,
                actual=answer, details=f"Within 5% tolerance (diff: {diff_pct:.1f}%)",
            )
        else:
            return ValidationResult(
                score=0.0, is_correct=False, expected=expected,
                actual=answer, details=f"Outside tolerance (diff: {diff_pct:.1f}%)",
            )

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> TriggerConfig:
        """
        CoinGecko: fetch when AI visits the coin's page or API.

        Strategy: FIRST - crypto prices are volatile but we compare at fetch time.
        """
        coin_id = validation_info.get("coin_id", "")
        trigger = UrlPatternTrigger(
            domains=["coingecko.com", "api.coingecko.com"],
            url_contains=coin_id if coin_id else None,
        )
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)

    # === Cache Registration Methods ===
    # These methods make the template self-contained for caching.
    # Adding a new template only requires implementing these methods.

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "coingecko"

    def get_gt_source(self):
        """
        CoinGecko price template uses PAGE_ONLY extraction.

        Price, 24h change, and market cap are all visible on the coin's page
        and can be extracted from the accessibility tree.
        """
        from liveweb_arena.core.gt_collector import GTSourceType
        return GTSourceType.PAGE_ONLY

    def get_page_fields(self):
        """Fields extractable from CoinGecko page."""
        return ["current_price", "change_24h", "market_cap"]

    @classmethod
    def get_cache_urls(cls) -> List[str]:
        """
        Generate URLs to cache based on CoinVariable.COINS.

        Each coin has a page at https://www.coingecko.com/en/coins/{coin_id}
        """
        return [
            f"https://www.coingecko.com/en/coins/{coin.coin_id}"
            for coin in CoinVariable.COINS
        ]

