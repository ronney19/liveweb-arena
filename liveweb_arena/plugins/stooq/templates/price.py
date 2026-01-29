"""Price query template for Stooq financial data"""

import random
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult,
)
from liveweb_arena.plugins.stooq.api_client import StooqClient
from .variables import (
    StockVariable, IndexVariable, CurrencyVariable, CommodityVariable,
    PriceMetricVariable, StockSpec, IndexSpec, CurrencySpec, CommoditySpec,
    MetricSpec, PriceMetric, InstrumentType,
    US_STOCKS, INDICES, CURRENCIES, COMMODITIES,
)


@register_template("stooq_price")
class StooqPriceTemplate(QuestionTemplate):
    """
    Template for querying current prices on Stooq.

    Supports multiple instrument types:
    - Stocks (US, UK, DE)
    - Indices (DJI, SPX, FTSE, DAX, etc.)
    - Currency pairs (EUR/USD, GBP/USD, etc.)
    - Commodities (Gold, Oil, etc.)

    Ground truth is fetched from Stooq CSV download endpoint.
    """

    STOCK_PATTERNS = [
        "What is the {metric} of {instrument} stock?",
        "What is {instrument} trading at?",
        "Find the {metric} for {instrument}.",
        "What is the current {metric} of {instrument}?",
        "What's the latest {metric} of {instrument} stock?",
        "What's the current {metric} of {instrument}?",
    ]

    # Index patterns vary based on metric type
    INDEX_PRICE_PATTERNS = [
        "What is the current value of the {instrument}?",
        "What is the {instrument} at right now?",
        "Find the current {instrument} value.",
        "What is the current {instrument} index value?",
    ]

    INDEX_CHANGE_PATTERNS = [
        "What is the {metric} of the {instrument} today?",
        "Find the {metric} of {instrument} index.",
        "What's the {metric} of the {instrument}?",
        "What is today's {metric} for {instrument}?",
    ]

    CURRENCY_PATTERNS = [
        "What is the current {instrument} exchange rate?",
        "Find the {metric} for {instrument}.",
        "What is {instrument} trading at?",
        "What is the {instrument} rate?",
        "What's the current {instrument} price?",
    ]

    COMMODITY_PATTERNS = [
        "What is the current price of {instrument}?",
        "Find the {metric} for {instrument}.",
        "What is {instrument} trading at?",
        "What is the latest {instrument} price?",
        "What's the {metric} of {instrument}?",
    ]

    def __init__(self, instrument_types: List[InstrumentType] = None):
        super().__init__("stooq_price")
        self.instrument_types = instrument_types or [
            InstrumentType.STOCK,
            InstrumentType.INDEX,
        ]

        # Register variables
        self.register_variable(StockVariable())
        self.register_variable(IndexVariable())
        self.register_variable(CurrencyVariable())
        self.register_variable(CommodityVariable())
        self.register_variable(PriceMetricVariable())

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """
        Generate a Stooq price question.

        Args:
            seed: Random seed for reproducible generation
            variant: Optional variant index for selecting instrument type.
                     0=STOCK, 1=INDEX (within allowed instrument_types)
        """
        rng = random.Random(seed)

        # Select instrument type (use variant if provided)
        if variant is not None and self.instrument_types:
            inst_type = self.instrument_types[variant % len(self.instrument_types)]
        else:
            inst_type = rng.choice(self.instrument_types)

        # Sample metric first to determine patterns
        metric: MetricSpec = self._variables["metric"].sample(rng)
        is_change_metric = metric.metric in [PriceMetric.CHANGE_PERCENT, PriceMetric.CHANGE_ABSOLUTE]

        # Sample instrument based on type
        if inst_type == InstrumentType.STOCK:
            instrument = self._variables["stock"].sample(rng)
            patterns = self.STOCK_PATTERNS
            symbol = instrument.symbol
        elif inst_type == InstrumentType.INDEX:
            instrument = self._variables["index"].sample(rng)
            # Use appropriate patterns based on metric type
            patterns = self.INDEX_CHANGE_PATTERNS if is_change_metric else self.INDEX_PRICE_PATTERNS
            symbol = instrument.symbol
        elif inst_type == InstrumentType.CURRENCY:
            instrument = self._variables["currency"].sample(rng)
            patterns = self.CURRENCY_PATTERNS
            symbol = instrument.symbol
        else:  # COMMODITY
            instrument = self._variables["commodity"].sample(rng)
            patterns = self.COMMODITY_PATTERNS
            symbol = instrument.symbol

        # Build question
        pattern = rng.choice(patterns)
        question_text = pattern.format(
            instrument=instrument.display_name,
            metric=metric.display_name,
        )

        validation_info = {
            "symbol": symbol,
            "instrument_type": inst_type.value,
            "instrument_name": instrument.display_name,
            "metric": metric.metric.value,
            "is_percentage": metric.is_percentage,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=f"https://stooq.com/q/?s={symbol}",
            variables={
                "instrument": instrument,
                "metric": metric,
                "instrument_type": inst_type,
            },
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        metric = validation_info.get("metric", "last_price")
        is_percentage = validation_info.get("is_percentage", False)

        if is_percentage:
            return """Task-Specific Rules (Stooq - Percentage Change):
- Score 1.0: Values match within 0.5 percentage points (e.g., +1.5% vs +1.8%)
- Score 0.0: Difference exceeds 0.5 percentage points or wrong sign"""

        if metric == "last_price":
            return """Task-Specific Rules (Stooq - Current Price):
- Score 1.0: Price matches within 1% tolerance (markets fluctuate)
- Score 0.0: Price differs by more than 1% or format is incorrect
- Accept various formats: $255.53, 255.53, 255.53 USD"""

        return """Task-Specific Rules (Stooq - Price Data):
- Score 1.0: Values match within 2% tolerance
- Score 0.0: Values differ by more than 2%"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Get ground truth from collected API data (no network fallback)."""
        symbol = validation_info.get("symbol", "")
        metric = validation_info.get("metric", "last_price")
        if not symbol:
            return GroundTruthResult.fail("No symbol provided")

        from liveweb_arena.core.gt_collector import get_current_gt_collector
        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.fail("No GT collector")

        collected = gt_collector.get_collected_api_data()
        # Try both original and lowercase
        data = collected.get(symbol) or collected.get(symbol.lower())
        if not data:
            return GroundTruthResult.fail(
                f"Stooq data for '{symbol}' not collected. "
                f"Available: {list(collected.keys())[:10]}"
            )

        close = data.get("close")
        open_price = data.get("open")
        high = data.get("high")
        low = data.get("low")
        change = data.get("daily_change")
        change_pct = data.get("daily_change_pct")

        # Return the specific metric requested
        if metric == "last_price":
            if close is None:
                return GroundTruthResult.fail("Could not parse close price in collected data")
            return GroundTruthResult.ok(f"{close:.2f}")
        elif metric == "change_percent":
            if change_pct is None:
                return GroundTruthResult.fail("Could not calculate change percent from collected data")
            return GroundTruthResult.ok(f"{change_pct:+.2f}%")
        elif metric == "change_absolute":
            if change is None:
                return GroundTruthResult.fail("Could not calculate change from collected data")
            return GroundTruthResult.ok(f"{change:+.2f}")
        elif metric == "open":
            if open_price is None:
                return GroundTruthResult.fail("Could not parse open price in collected data")
            return GroundTruthResult.ok(f"{open_price:.2f}")
        elif metric == "high":
            if high is None:
                return GroundTruthResult.fail("Could not parse high price in collected data")
            return GroundTruthResult.ok(f"{high:.2f}")
        elif metric == "low":
            if low is None:
                return GroundTruthResult.fail("Could not parse low price in collected data")
            return GroundTruthResult.ok(f"{low:.2f}")
        else:
            if close is None:
                return GroundTruthResult.fail("Could not parse close price in collected data")
            return GroundTruthResult.ok(f"{close:.2f}")

    def _parse_float(self, value: Any) -> Optional[float]:
        """Parse a value to float, returning None if invalid"""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate price answer against ground truth"""
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
        metric = validation_info.get("metric", "last_price")

        # Parse expected value from ground truth string
        import re
        expected_numbers = re.findall(r'[-+]?\d*\.?\d+', ground_truth.replace(',', ''))
        if not expected_numbers:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Could not parse ground truth",
            )
        expected = float(expected_numbers[0])

        # Extract number from answer
        numbers = re.findall(r'[-+]?\d*\.?\d+', answer.replace(',', ''))
        if not numbers:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="No numeric value found in answer",
            )

        # Find the most likely match
        actual = None
        for n in numbers:
            try:
                val = float(n)
                # For percentages, look for small numbers
                if metric == "change_percent" and -50 < val < 50:
                    actual = val
                    break
                # For prices, look for reasonable values
                elif val > 0:
                    actual = val
            except ValueError:
                continue

        if actual is None:
            actual = float(numbers[0])

        # Calculate tolerance based on metric
        if metric == "change_percent":
            tolerance = 0.5  # 0.5 percentage points
            diff = abs(actual - expected)
        else:
            tolerance = abs(expected) * 0.02  # 2% tolerance for prices
            diff = abs(actual - expected)

        if diff <= tolerance:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=f"{actual:.2f}",
                details=f"Within tolerance (diff: {diff:.4f})",
            )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=ground_truth,
            actual=f"{actual:.2f}",
            details=f"Outside tolerance (diff: {diff:.4f})",
        )

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> tuple:
        """
        Stooq price: fetch when AI visits the specific symbol's page.

        Uses symbol-specific URL matching (e.g., ?s=aapl.us) to ensure
        ground truth is fetched at the exact moment AI observes that stock.

        Strategy: FIRST - single stock price is stable within session.
        """
        symbol = validation_info.get("symbol", "")
        trigger = UrlPatternTrigger(
            domains=["stooq.com"],
            url_contains=symbol if symbol else None,
        )
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)

    # === Cache Registration Methods ===
    # These methods make the template self-contained for caching.

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "stooq"

    def get_gt_source(self):
        """
        Stooq price template uses PAGE_ONLY extraction.

        All price metrics (last price, change, high, low, open) are visible
        on the stock/index page and extractable from the accessibility tree.
        """
        from liveweb_arena.core.gt_collector import GTSourceType
        return GTSourceType.PAGE_ONLY

    def get_page_fields(self):
        """Fields extractable from Stooq page."""
        return ["last_price", "change_percent", "change_absolute", "open", "high", "low"]

    @classmethod
    def get_cache_urls(cls) -> List[str]:
        """
        Generate URLs to cache based on Stooq variables.

        Each instrument has a page at https://stooq.com/q/?s={symbol}
        """
        urls = []
        for stock in US_STOCKS:
            urls.append(f"https://stooq.com/q/?s={stock.symbol}")
        for index in INDICES:
            urls.append(f"https://stooq.com/q/?s={index.symbol}")
        for currency in CURRENCIES:
            urls.append(f"https://stooq.com/q/?s={currency.symbol}")
        for commodity in COMMODITIES:
            urls.append(f"https://stooq.com/q/?s={commodity.symbol}")
        return urls

