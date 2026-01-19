"""Price query template for Stooq financial data"""

import random
from typing import Any, Dict, List, Optional
import aiohttp
import io
import csv

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from .variables import (
    StockVariable, IndexVariable, CurrencyVariable, CommodityVariable,
    PriceMetricVariable, StockSpec, IndexSpec, CurrencySpec, CommoditySpec,
    MetricSpec, PriceMetric, InstrumentType,
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

    STOOQ_CSV_URL = "https://stooq.com/q/d/l/"

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

    def generate(self, seed: int) -> GeneratedQuestion:
        rng = random.Random(seed)

        # Select instrument type
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

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Optional[str]:
        """
        Fetch ground truth from Stooq CSV download endpoint.

        Returns the specific metric value as a string for LLM validation.
        """
        symbol = validation_info.get("symbol", "")
        metric = validation_info.get("metric", "last_price")
        if not symbol:
            return None

        try:
            # Fetch CSV data
            async with aiohttp.ClientSession() as session:
                params = {"s": symbol, "i": "d"}  # daily data
                async with session.get(
                    self.STOOQ_CSV_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    if response.status != 200:
                        return None
                    csv_text = await response.text()

            # Parse CSV
            reader = csv.DictReader(io.StringIO(csv_text))
            rows = list(reader)

            if not rows:
                return None

            # Get most recent data (last row)
            latest = rows[-1]

            # Extract price data
            close = self._parse_float(latest.get("Close"))
            open_price = self._parse_float(latest.get("Open"))
            high = self._parse_float(latest.get("High"))
            low = self._parse_float(latest.get("Low"))

            # Calculate change if we have previous data
            change_pct = None
            change = None
            if len(rows) >= 2:
                prev = rows[-2]
                prev_close = self._parse_float(prev.get("Close"))
                if prev_close and close:
                    change = close - prev_close
                    change_pct = (change / prev_close) * 100

            # Return the specific metric requested
            if metric == "last_price":
                return f"{close:.2f}" if close else None
            elif metric == "change_percent":
                return f"{change_pct:+.2f}%" if change_pct is not None else None
            elif metric == "change_absolute":
                return f"{change:+.2f}" if change is not None else None
            elif metric == "open":
                return f"{open_price:.2f}" if open_price else None
            elif metric == "high":
                return f"{high:.2f}" if high else None
            elif metric == "low":
                return f"{low:.2f}" if low else None
            else:
                return f"{close:.2f}" if close else None

        except Exception:
            return None

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
        ground_truth = await self.get_ground_truth(validation_info)

        if ground_truth is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable",
            )

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
