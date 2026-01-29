"""Currency conversion template for Stooq"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult,
)
from .variables import CURRENCIES, CurrencySpec


class ConversionDirection(Enum):
    """Direction of currency conversion"""
    BASE_TO_QUOTE = "base_to_quote"  # e.g., EUR to USD
    QUOTE_TO_BASE = "quote_to_base"  # e.g., USD to EUR


# Common amounts for currency conversion questions
AMOUNTS = [100, 500, 1000, 2000, 5000, 10000]


@register_template("stooq_currency")
class StooqCurrencyTemplate(QuestionTemplate):
    """
    Template for currency conversion questions on Stooq.

    Generates questions like:
    - "If I have 1000 USD, how many Euros can I get? Check EUR/USD on stooq.com."
    - "Convert 500 GBP to USD using today's exchange rate on stooq.com."
    - "What is 2000 JPY worth in USD? Check stooq.com for the current rate."

    Ground truth is calculated from Stooq CSV exchange rate data.
    """

    PATTERNS = {
        ConversionDirection.BASE_TO_QUOTE: [
            "If I have {amount} {base}, how many {quote} can I get?",
            "Convert {amount} {base} to {quote} using today's exchange rate.",
            "What is {amount} {base} worth in {quote}?",
            "How much {quote} would I get for {amount} {base}?",
        ],
        ConversionDirection.QUOTE_TO_BASE: [
            "If I have {amount} {quote}, how many {base} can I get?",
            "Convert {amount} {quote} to {base} using today's exchange rate.",
            "What is {amount} {quote} worth in {base}?",
            "How much {base} would I get for {amount} {quote}?",
        ],
    }

    STOOQ_CSV_URL = "https://stooq.com/q/d/l/"

    def __init__(self):
        super().__init__("stooq_currency")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """
        Generate a Stooq currency conversion question.

        Args:
            seed: Random seed for reproducible generation
            variant: Optional variant index for selecting conversion direction.
                     0=BASE_TO_QUOTE, 1=QUOTE_TO_BASE
        """
        rng = random.Random(seed)

        # Select a currency pair
        currency = rng.choice(CURRENCIES)

        # Select conversion direction (use variant if provided)
        directions_list = list(ConversionDirection)
        if variant is not None:
            direction = directions_list[variant % len(directions_list)]
        else:
            direction = rng.choice(directions_list)

        # Select amount
        amount = rng.choice(AMOUNTS)

        # Build question
        patterns = self.PATTERNS[direction]
        pattern = rng.choice(patterns)

        question_text = pattern.format(
            amount=amount,
            base=currency.base,
            quote=currency.quote,
            pair=currency.display_name,
        )

        validation_info = {
            "symbol": currency.symbol,
            "base": currency.base,
            "quote": currency.quote,
            "amount": amount,
            "direction": direction.value,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://stooq.com/",
            variables={
                "currency": currency,
                "direction": direction,
                "amount": amount,
            },
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        amount = validation_info.get("amount", 0)
        base = validation_info.get("base", "")
        quote = validation_info.get("quote", "")
        direction = validation_info.get("direction", "")

        if direction == "base_to_quote":
            conversion = f"{amount} {base} to {quote}"
        else:
            conversion = f"{amount} {quote} to {base}"

        return f"""Task-Specific Rules (Currency Conversion: {conversion}):
- Score 1.0: Agent provides correct converted amount within 3% tolerance
- Score 0.0: Wrong conversion, wrong currency, or more than 3% off

The agent must:
1. Find the current exchange rate on stooq.com
2. Calculate the conversion correctly
3. Provide a clear numeric answer"""

    async def _fetch_exchange_rate(self, symbol: str) -> GroundTruthResult:
        """Fetch current exchange rate from collected API data (no network fallback)."""
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

        rate = data.get("close")
        if not rate or rate <= 0:
            return GroundTruthResult.fail("Invalid exchange rate in collected data")
        return GroundTruthResult.ok(rate)

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """
        Calculate converted amount based on exchange rate.

        Returns GroundTruthResult with converted amount as string (e.g., "1159.94 USD")
        """
        symbol = validation_info.get("symbol", "")
        amount = validation_info.get("amount", 0)
        direction = validation_info.get("direction", "")
        base = validation_info.get("base", "")
        quote = validation_info.get("quote", "")

        result = await self._fetch_exchange_rate(symbol)
        if not result.success:
            return result

        rate = result.value
        if direction == "base_to_quote":
            converted = amount * rate
            result_currency = quote
        else:
            converted = amount / rate
            result_currency = base

        return GroundTruthResult.ok(f"{converted:.2f} {result_currency}")

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate currency conversion answer"""
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

        # Parse expected value
        expected_match = re.match(r'([\d.]+)\s*(\w+)', ground_truth)
        if not expected_match:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="Failed to parse ground truth",
            )

        expected_value = float(expected_match.group(1))
        expected_currency = expected_match.group(2)

        # Extract numbers from answer
        answer_clean = answer.replace(',', '')
        numbers = re.findall(r'[\d.]+', answer_clean)

        if not numbers:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=ground_truth,
                actual=answer,
                details="No numeric value found in answer",
            )

        # Find the best matching number
        best_score = 0.0
        best_diff = float('inf')

        for num_str in numbers:
            try:
                num = float(num_str)
                if num <= 0:
                    continue

                pct_diff = abs(num - expected_value) / expected_value * 100

                if pct_diff <= 3:
                    score = 1.0
                else:
                    score = 0.0

                if score > best_score or (score == best_score and pct_diff < best_diff):
                    best_score = score
                    best_diff = pct_diff

            except ValueError:
                continue

        return ValidationResult(
            score=best_score,
            is_correct=best_score == 1.0,
            expected=ground_truth,
            actual=answer,
            details=f"Difference: {best_diff:.1f}%" if best_diff < float('inf') else "No valid number found",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> tuple:
        """
        Currency query: fetch when AI visits the specific currency pair's page.

        Uses symbol-specific URL matching for precise synchronization.

        Strategy: FIRST - single currency pair query.
        """
        symbol = validation_info.get("symbol", "")
        trigger = UrlPatternTrigger(
            domains=["stooq.com"],
            url_contains=symbol if symbol else None,
        )
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "stooq"

    def get_gt_source(self):
        """Exchange rate is visible on the currency pair page."""
        from liveweb_arena.core.gt_collector import GTSourceType
        return GTSourceType.PAGE_ONLY

    def get_page_fields(self):
        """Fields extractable from Stooq page."""
        return ["current_price", "change_percent"]
