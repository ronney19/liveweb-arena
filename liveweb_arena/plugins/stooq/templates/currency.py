"""Currency conversion template for Stooq"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional
import aiohttp
import io
import csv

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
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

    def generate(self, seed: int) -> GeneratedQuestion:
        rng = random.Random(seed)

        # Select a currency pair
        currency = rng.choice(CURRENCIES)

        # Select conversion direction
        direction = rng.choice(list(ConversionDirection))

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

    async def _fetch_exchange_rate(self, symbol: str) -> Optional[float]:
        """Fetch current exchange rate from Stooq CSV"""
        try:
            async with aiohttp.ClientSession() as session:
                params = {"s": symbol, "i": "d"}
                async with session.get(
                    self.STOOQ_CSV_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    if response.status != 200:
                        return None
                    csv_text = await response.text()

            reader = csv.DictReader(io.StringIO(csv_text))
            rows = list(reader)

            if not rows:
                return None

            # Get latest close price (exchange rate)
            latest = rows[-1]
            rate = float(latest.get("Close", 0))
            return rate if rate > 0 else None

        except Exception:
            return None

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Optional[str]:
        """
        Calculate converted amount based on exchange rate.

        Returns:
            Converted amount as formatted string (e.g., "1159.94 USD")
        """
        symbol = validation_info.get("symbol", "")
        amount = validation_info.get("amount", 0)
        direction = validation_info.get("direction", "")
        base = validation_info.get("base", "")
        quote = validation_info.get("quote", "")

        rate = await self._fetch_exchange_rate(symbol)
        if rate is None:
            return None

        # Calculate conversion
        # Exchange rate format: EUR/USD = 1.16 means 1 EUR = 1.16 USD
        if direction == "base_to_quote":
            # Converting base to quote: amount * rate
            result = amount * rate
            result_currency = quote
        else:
            # Converting quote to base: amount / rate
            result = amount / rate
            result_currency = base

        return f"{result:.2f} {result_currency}"

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate currency conversion answer"""
        import re

        ground_truth = await self.get_ground_truth(validation_info)

        if ground_truth is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable",
            )

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
