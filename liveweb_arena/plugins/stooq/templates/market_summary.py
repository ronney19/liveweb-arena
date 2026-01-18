"""Market summary template for Stooq - open-ended analysis questions"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional
import aiohttp
import io
import csv

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from .variables import INDICES, US_STOCKS


class MarketSummaryType(Enum):
    """Types of market summary questions"""
    US_INDICES = "us_indices"  # Summarize US market via indices
    TECH_STOCKS = "tech_stocks"  # Summarize tech sector
    MARKET_TREND = "market_trend"  # Analyze market direction


@register_template("stooq_market_summary")
class StooqMarketSummaryTemplate(QuestionTemplate):
    """
    Template for market summary questions requiring AI analysis.

    These questions don't have fixed expected answers. Instead:
    1. Ground truth provides actual market data (prices, changes)
    2. LLM validator judges if the answer correctly reflects the data

    Questions like:
    - "Summarize today's US market performance based on DJI, SPX, and NDX"
    - "Analyze the tech sector using AAPL, MSFT, NVDA, and GOOGL"
    - "Is the market trending up or down based on major indices?"
    """

    PATTERNS = {
        MarketSummaryType.US_INDICES: [
            "Summarize today's US stock market performance. Check stooq.com for DJI, SPX, and NDX values and their percentage changes. Describe if the market is up or down overall.",
            "Go to stooq.com and analyze the US market based on the Dow Jones (^dji), S&P 500 (^spx), and NASDAQ 100 (^ndx). Are they mostly up or down? By how much?",
            "What is the overall direction of the US stock market today? Check stooq.com for the major indices (DJI, SPX, NDX) and summarize their performance.",
        ],
        MarketSummaryType.TECH_STOCKS: [
            "Analyze the tech sector's performance today. Check stooq.com for AAPL, MSFT, NVDA, and GOOGL. Summarize which are gaining and which are losing.",
            "Go to stooq.com and check the major tech stocks (Apple, Microsoft, NVIDIA, Alphabet). How is the tech sector performing today?",
            "Summarize today's tech stock performance by checking AAPL, MSFT, NVDA, and GOOGL on stooq.com. Are tech stocks mostly up or down?",
        ],
        MarketSummaryType.MARKET_TREND: [
            "Based on the major US indices on stooq.com (DJI, SPX, NDX), is the market in an uptrend or downtrend today? Provide the actual percentage changes.",
            "Check stooq.com for the DJI, SPX, and NDX. Determine the market trend and report the percentage change for each index.",
            "Analyze the current market trend using data from stooq.com. Look at DJI, SPX, and NDX. Is the market bullish or bearish today?",
        ],
    }

    # Symbols to check for each summary type
    SYMBOLS = {
        MarketSummaryType.US_INDICES: ["^dji", "^spx", "^ndx"],
        MarketSummaryType.TECH_STOCKS: ["aapl.us", "msft.us", "nvda.us", "googl.us"],
        MarketSummaryType.MARKET_TREND: ["^dji", "^spx", "^ndx"],
    }

    STOOQ_CSV_URL = "https://stooq.com/q/d/l/"

    def __init__(self):
        super().__init__("stooq_market_summary")

    def generate(self, seed: int) -> GeneratedQuestion:
        rng = random.Random(seed)

        # Select summary type
        summary_type = rng.choice(list(MarketSummaryType))

        # Build question
        patterns = self.PATTERNS[summary_type]
        pattern = rng.choice(patterns)
        question_text = pattern

        symbols = self.SYMBOLS[summary_type]

        validation_info = {
            "summary_type": summary_type.value,
            "symbols": symbols,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://stooq.com/",
            variables={
                "summary_type": summary_type,
            },
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        summary_type = validation_info.get("summary_type", "us_indices")

        if summary_type == "us_indices":
            return """Task-Specific Rules (Market Summary - US Indices):
The ground truth contains actual data for DJI, SPX, and NDX including prices and percentage changes.
- Score 1.0: Answer correctly describes the overall market direction (up/down) AND includes reasonably accurate percentage changes (within 0.5% tolerance)
- Score 0.5: Answer correctly identifies market direction but percentage values are off by more than 0.5%
- Score 0.0: Answer incorrectly identifies market direction or provides completely wrong data

Key validation points:
1. If most indices are positive, answer should say market is UP
2. If most indices are negative, answer should say market is DOWN
3. Percentage changes should be approximately correct"""

        elif summary_type == "tech_stocks":
            return """Task-Specific Rules (Market Summary - Tech Stocks):
The ground truth contains actual data for AAPL, MSFT, NVDA, and GOOGL including prices and percentage changes.
- Score 1.0: Answer correctly summarizes which stocks are up/down AND sector direction
- Score 0.5: Answer partially correct (e.g., correct direction but wrong about specific stocks)
- Score 0.0: Answer fundamentally incorrect about sector performance

Key validation points:
1. Correctly identify which stocks are gaining vs losing
2. Correctly summarize overall tech sector direction
3. Percentage changes should be approximately correct"""

        else:  # MARKET_TREND
            return """Task-Specific Rules (Market Summary - Market Trend):
The ground truth contains actual data for major indices.
- Score 1.0: Answer correctly identifies the trend (uptrend/downtrend/mixed) with supporting data
- Score 0.5: Answer identifies correct trend but supporting data is incomplete
- Score 0.0: Answer identifies wrong trend direction

Key validation points:
1. Uptrend = most indices positive
2. Downtrend = most indices negative
3. Mixed = some up, some down significantly"""

    async def _fetch_instrument_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch data for a single instrument"""
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

            latest = rows[-1]
            result = {
                "symbol": symbol,
                "close": self._parse_float(latest.get("Close")),
                "date": latest.get("Date", ""),
            }

            # Calculate change percent
            if len(rows) >= 2:
                prev = rows[-2]
                prev_close = self._parse_float(prev.get("Close"))
                if prev_close and result["close"]:
                    change = result["close"] - prev_close
                    change_pct = (change / prev_close) * 100
                    result["change"] = change
                    result["change_percent"] = change_pct

            return result

        except Exception:
            return None

    def _parse_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Optional[str]:
        """
        Fetch market data for all symbols and return readable summary string.

        Returns a string like: "Market direction: UP. Data: aapl.us: 255.53 (+1.04%), ..."
        """
        symbols = validation_info.get("symbols", [])
        summary_type = validation_info.get("summary_type", "us_indices")

        if not symbols:
            return None

        # Fetch data for all symbols
        all_data = {}
        for symbol in symbols:
            data = await self._fetch_instrument_data(symbol)
            if data:
                all_data[symbol] = data

        if len(all_data) < len(symbols) // 2 + 1:
            # Need at least half the data
            return None

        # Calculate summary statistics
        changes = [d.get("change_percent", 0) for d in all_data.values() if d.get("change_percent") is not None]

        if not changes:
            return None

        positive_count = sum(1 for c in changes if c > 0)
        negative_count = sum(1 for c in changes if c < 0)
        avg_change = sum(changes) / len(changes)

        # Determine overall direction
        if positive_count > negative_count:
            direction = "UP"
        elif negative_count > positive_count:
            direction = "DOWN"
        else:
            direction = "MIXED"

        # Build human-readable summary as ground truth
        data_summary = []
        for symbol, data in all_data.items():
            change_pct = data.get("change_percent", 0)
            close = data.get("close", 0)
            sign = "+" if change_pct >= 0 else ""
            data_summary.append(f"{symbol}: {close:.2f} ({sign}{change_pct:.2f}%)")

        # Return a simple, readable ground truth string for LLM validation
        return f"Market direction: {direction}. Data: {', '.join(data_summary)}"

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """
        Validate market summary answer.

        This uses simple heuristics for validation since it's a summary question.
        The LLM validator will also use the ground truth for more nuanced judgment.
        """
        ground_truth = await self.get_ground_truth(validation_info)

        if ground_truth is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable",
            )

        # Parse direction from ground truth string (format: "Market direction: UP/DOWN/MIXED. Data: ...")
        direction = "MIXED"
        if "direction: UP" in ground_truth:
            direction = "UP"
        elif "direction: DOWN" in ground_truth:
            direction = "DOWN"

        answer_lower = answer.lower()

        # Check if direction is correctly identified
        direction_correct = False
        if direction == "UP":
            direction_correct = any(word in answer_lower for word in ["up", "gain", "positive", "bullish", "higher", "rise", "green"])
        elif direction == "DOWN":
            direction_correct = any(word in answer_lower for word in ["down", "loss", "negative", "bearish", "lower", "fall", "red", "decline"])
        else:  # MIXED
            direction_correct = any(word in answer_lower for word in ["mixed", "flat", "unchanged", "neutral"])

        # Check if any actual values are mentioned
        import re
        numbers_in_answer = re.findall(r'[-+]?\d*\.?\d+%?', answer)
        has_values = len(numbers_in_answer) > 0

        # Score based on direction correctness and value presence
        if direction_correct and has_values:
            score = 1.0
            details = f"Correctly identified {direction} market with values"
        elif direction_correct:
            score = 0.5
            details = f"Correctly identified {direction} market but missing specific values"
        else:
            score = 0.0
            details = f"Incorrect direction. Actual market: {direction}"

        return ValidationResult(
            score=score,
            is_correct=score >= 0.5,
            expected=ground_truth,
            actual=answer,
            details=details,
        )
