"""Cross-sector/region analysis template for Stooq - high difficulty with anti-memorization"""

import random
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult,
)
from liveweb_arena.core.gt_collector import GTSourceType


class ComparisonMetric(Enum):
    """Metrics for comparison"""
    DAILY_CHANGE = "daily_change"  # Daily % change


# Large pool of US stocks for randomization (40+ stocks)
# This makes memorization practically impossible: C(40,3) * C(37,3) ≈ 77 million combinations
ALL_STOCKS = [
    # Technology
    ("aapl.us", "Apple"),
    ("msft.us", "Microsoft"),
    ("nvda.us", "NVIDIA"),
    ("googl.us", "Alphabet"),
    ("meta.us", "Meta"),
    ("avgo.us", "Broadcom"),
    ("orcl.us", "Oracle"),
    ("crm.us", "Salesforce"),
    ("adbe.us", "Adobe"),
    ("amd.us", "AMD"),
    ("intc.us", "Intel"),
    ("csco.us", "Cisco"),
    ("ibm.us", "IBM"),
    ("qcom.us", "Qualcomm"),
    # Finance
    ("jpm.us", "JPMorgan"),
    ("v.us", "Visa"),
    ("ma.us", "Mastercard"),
    ("bac.us", "Bank of America"),
    ("wfc.us", "Wells Fargo"),
    ("gs.us", "Goldman Sachs"),
    ("ms.us", "Morgan Stanley"),
    ("c.us", "Citigroup"),
    ("axp.us", "American Express"),
    ("schw.us", "Charles Schwab"),
    # Consumer
    ("amzn.us", "Amazon"),
    ("tsla.us", "Tesla"),
    ("wmt.us", "Walmart"),
    ("hd.us", "Home Depot"),
    ("ko.us", "Coca-Cola"),
    ("pep.us", "PepsiCo"),
    ("cost.us", "Costco"),
    ("mcd.us", "McDonald's"),
    ("nke.us", "Nike"),
    ("sbux.us", "Starbucks"),
    ("dis.us", "Disney"),
    ("tgt.us", "Target"),
    # Healthcare
    ("unh.us", "UnitedHealth"),
    ("jnj.us", "Johnson & Johnson"),
    ("lly.us", "Eli Lilly"),
    ("pfe.us", "Pfizer"),
    ("abbv.us", "AbbVie"),
    ("mrk.us", "Merck"),
    # Energy & Industrial
    ("xom.us", "Exxon Mobil"),
    ("cvx.us", "Chevron"),
    ("cat.us", "Caterpillar"),
    ("ba.us", "Boeing"),
    ("ge.us", "GE Aerospace"),
    ("ups.us", "UPS"),
]

# All major indices
ALL_INDICES = [
    ("^dji", "Dow Jones"),
    ("^spx", "S&P 500"),
    ("^ndx", "NASDAQ 100"),
    ("^dax", "DAX"),
    ("^ukx", "FTSE 100"),
    ("^cac", "CAC 40"),
    ("^nkx", "Nikkei 225"),
    ("^hsi", "Hang Seng"),
    ("^kospi", "KOSPI"),
]


@register_template("stooq_sector_analysis")
class StooqSectorAnalysisTemplate(QuestionTemplate):
    """
    Template for cross-asset analysis with anti-memorization design.

    Anti-memorization features:
    1. Random asset selection from 40+ stocks (billions of combinations)
    2. Questions require reporting individual values for each asset
    3. Validation checks both intermediate data AND final comparison

    Example question:
    "Check the daily percentage change for each stock and report:
     Group A: Apple, Microsoft, NVIDIA
     Group B: JPMorgan, Visa, Goldman Sachs
     Report each stock's percentage change, then determine which group has the higher average."

    Validation:
    - Extracts reported values from answer
    - Compares each value against current API data (5% tolerance)
    - Scores based on: data accuracy (60%) + correct comparison (40%)
    """

    GT_SOURCE = GTSourceType.API_ONLY  # Cross-sector multi-asset comparison

    STOOQ_CSV_URL = "https://stooq.com/q/d/l/"

    def __init__(self):
        super().__init__("stooq_sector_analysis")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """
        Generate a Stooq sector analysis question.

        Args:
            seed: Random seed for reproducible generation
            variant: Optional variant index for selecting question type.
                     0=stocks comparison, 1=indices comparison
        """
        rng = random.Random(seed)

        # Decide: stocks or indices (use variant if provided)
        if variant is not None:
            use_indices = (variant % 2) == 1
        else:
            use_indices = rng.random() < 0.3  # 30% chance for indices

        if use_indices:
            return self._generate_index_question(rng)
        else:
            return self._generate_stock_question(rng)

    def _generate_stock_question(self, rng: random.Random) -> GeneratedQuestion:
        """Generate stock comparison with random selection"""
        # Randomly select group sizes (3-4 stocks each)
        group1_size = rng.choice([3, 4])
        group2_size = rng.choice([3, 4])

        # Random selection from pool
        shuffled = ALL_STOCKS.copy()
        rng.shuffle(shuffled)

        group1 = shuffled[:group1_size]
        group2 = shuffled[group1_size:group1_size + group2_size]

        # Build question that requires individual values
        names1 = [inst[1] for inst in group1]
        names2 = [inst[1] for inst in group2]

        question_text = self._build_question_text(names1, names2, rng)

        all_instruments = group1 + group2
        expected_steps = len(all_instruments) * 2 + 5

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://stooq.com/",
            variables={
                "type": "stocks",
                "group1": group1,
                "group2": group2,
            },
            validation_info={
                "type": "stocks",
                "group1_instruments": group1,
                "group2_instruments": group2,
                "group1_names": names1,
                "group2_names": names2,
            },
            template_name=self.name,
            expected_steps=expected_steps,
        )

    def _generate_index_question(self, rng: random.Random) -> GeneratedQuestion:
        """Generate index comparison with random selection"""
        # Randomly select 3 indices per group
        shuffled = ALL_INDICES.copy()
        rng.shuffle(shuffled)

        group1 = shuffled[:3]
        group2 = shuffled[3:6]

        names1 = [inst[1] for inst in group1]
        names2 = [inst[1] for inst in group2]

        question_text = self._build_question_text(names1, names2, rng, is_index=True)

        all_instruments = group1 + group2
        expected_steps = len(all_instruments) * 2 + 5

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://stooq.com/",
            variables={
                "type": "indices",
                "group1": group1,
                "group2": group2,
            },
            validation_info={
                "type": "indices",
                "group1_instruments": group1,
                "group2_instruments": group2,
                "group1_names": names1,
                "group2_names": names2,
            },
            template_name=self.name,
            expected_steps=expected_steps,
        )

    def _build_question_text(
        self, names1: List[str], names2: List[str], rng: random.Random, is_index: bool = False
    ) -> str:
        """Build question that requires reporting individual values"""
        asset_type = "index" if is_index else "stock"
        list1 = ", ".join(names1)
        list2 = ", ".join(names2)

        patterns = [
            f"Find the daily percentage change for each {asset_type} below:\n"
            f"Group A: {list1}\n"
            f"Group B: {list2}\n"
            f"Report each {asset_type}'s percentage change, calculate the average for each group, "
            f"and determine which group has the higher average daily change.",

            f"Look up the daily percentage change of these {asset_type}s:\n"
            f"Group A: {list1}\n"
            f"Group B: {list2}\n"
            f"List each {asset_type}'s change (%), compute both group averages, and identify which group performs better.",

            f"What is the daily change (%) for each of the following:\n"
            f"Group A: {list1}\n"
            f"Group B: {list2}\n"
            f"Provide each individual percentage change, the group averages, and conclude which group has higher average performance.",
        ]

        return rng.choice(patterns)

    def get_expected_steps(self, validation_info: Dict[str, Any]) -> int:
        """Calculate expected steps"""
        group1 = validation_info.get("group1_instruments", [])
        group2 = validation_info.get("group2_instruments", [])
        return (len(group1) + len(group2)) * 2 + 5

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        names1 = validation_info.get("group1_names", [])
        names2 = validation_info.get("group2_names", [])

        return f"""Task-Specific Rules (Cross-Asset Analysis with Data Verification):

Assets to check:
- Group A: {', '.join(names1)}
- Group B: {', '.join(names2)}

Scoring (total 1.0):
- Individual data accuracy (0.6): Each reported % change compared to API
  - Within 1%: full credit for that asset
  - Within 3%: partial credit
  - Beyond 3%: no credit
- Correct comparison (0.4): Identifying which group has higher average

The agent MUST report individual percentage changes for verification."""

    async def _fetch_daily_change(self, symbol: str) -> GroundTruthResult:
        """Fetch daily percentage change from collected API data (no network fallback)."""
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

        change_pct = data.get("daily_change_pct")
        if change_pct is None:
            return GroundTruthResult.fail(f"No daily_change_pct in collected data for {symbol}")

        return GroundTruthResult.ok(change_pct)

    def _parse_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    async def _fetch_all_ground_truth(
        self, validation_info: Dict[str, Any]
    ) -> GroundTruthResult:
        """Fetch ground truth for all instruments"""
        group1 = validation_info.get("group1_instruments", [])
        group2 = validation_info.get("group2_instruments", [])

        result = {
            "group1_data": {},
            "group2_data": {},
            "group1_avg": None,
            "group2_avg": None,
            "winner": None,
        }

        has_retryable_error = False

        g1_values = []
        for symbol, name in group1:
            fetch_result = await self._fetch_daily_change(symbol)
            if fetch_result.success:
                result["group1_data"][name] = fetch_result.value
                g1_values.append(fetch_result.value)
            elif fetch_result.retryable:
                has_retryable_error = True

        g2_values = []
        for symbol, name in group2:
            fetch_result = await self._fetch_daily_change(symbol)
            if fetch_result.success:
                result["group2_data"][name] = fetch_result.value
                g2_values.append(fetch_result.value)
            elif fetch_result.retryable:
                has_retryable_error = True

        if g1_values:
            result["group1_avg"] = sum(g1_values) / len(g1_values)
        if g2_values:
            result["group2_avg"] = sum(g2_values) / len(g2_values)

        if result["group1_avg"] is not None and result["group2_avg"] is not None:
            if result["group1_avg"] > result["group2_avg"]:
                result["winner"] = "A"
            elif result["group2_avg"] > result["group1_avg"]:
                result["winner"] = "B"
            else:
                result["winner"] = "tie"

        if result["winner"] is None:
            if has_retryable_error:
                return GroundTruthResult.retry("Failed to fetch enough data for comparison")
            return GroundTruthResult.fail("Could not determine winner - insufficient data")

        return GroundTruthResult.ok(result)

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Get ground truth summary string"""
        result = await self._fetch_all_ground_truth(validation_info)

        if not result.success:
            return result

        data = result.value
        g1_str = ", ".join(f"{k}: {v:+.2f}%" for k, v in data["group1_data"].items())
        g2_str = ", ".join(f"{k}: {v:+.2f}%" for k, v in data["group2_data"].items())

        return GroundTruthResult.ok(
            f"Group A ({data['group1_avg']:+.2f}%): {g1_str} | "
            f"Group B ({data['group2_avg']:+.2f}%): {g2_str} | "
            f"Winner: Group {data['winner']}"
        )

    def _extract_reported_values(self, answer: str, names: List[str]) -> Dict[str, float]:
        """Extract reported percentage values from answer"""
        reported = {}
        answer_lower = answer.lower()

        for name in names:
            name_lower = name.lower()
            # Look for patterns like "Apple: -1.5%" or "Apple (-1.5%)" or "Apple: down 1.5%"
            patterns = [
                rf"{re.escape(name_lower)}[:\s]+([+-]?\d+\.?\d*)%",
                rf"{re.escape(name_lower)}[:\s]*\(([+-]?\d+\.?\d*)%\)",
                rf"{re.escape(name_lower)}[:\s]+([+-]?\d+\.?\d*)\s*%",
            ]

            for pattern in patterns:
                match = re.search(pattern, answer_lower)
                if match:
                    try:
                        reported[name] = float(match.group(1))
                        break
                    except ValueError:
                        continue

        return reported

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate with intermediate data verification"""
        gt_result = await self._fetch_all_ground_truth(validation_info)
        gt_str_result = await self.get_ground_truth(validation_info)

        if not gt_result.success:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Ground truth unavailable: {gt_result.error}",
            )

        ground_truth_data = gt_result.value
        ground_truth_str = gt_str_result.value if gt_str_result.success else None

        names1 = validation_info.get("group1_names", [])
        names2 = validation_info.get("group2_names", [])

        # Extract reported values from answer
        reported1 = self._extract_reported_values(answer, names1)
        reported2 = self._extract_reported_values(answer, names2)

        # Score individual data accuracy (60% of total)
        data_score = 0.0
        data_details = []
        total_assets = len(names1) + len(names2)

        for name in names1:
            expected = ground_truth_data["group1_data"].get(name)
            reported = reported1.get(name)
            asset_score, detail = self._score_individual_value(name, expected, reported)
            data_score += asset_score / total_assets
            data_details.append(detail)

        for name in names2:
            expected = ground_truth_data["group2_data"].get(name)
            reported = reported2.get(name)
            asset_score, detail = self._score_individual_value(name, expected, reported)
            data_score += asset_score / total_assets
            data_details.append(detail)

        # Score comparison accuracy (40% of total)
        comparison_score = 0.0
        answer_lower = answer.lower()

        winner = ground_truth_data["winner"]
        if winner == "A":
            if "group a" in answer_lower or "a has" in answer_lower or "a is higher" in answer_lower:
                comparison_score = 1.0
            elif "group b" in answer_lower or "b has" in answer_lower or "b is higher" in answer_lower:
                comparison_score = 0.0
            else:
                # Check if more group A names mentioned positively
                comparison_score = 0.5  # Partial if unclear
        elif winner == "B":
            if "group b" in answer_lower or "b has" in answer_lower or "b is higher" in answer_lower:
                comparison_score = 1.0
            elif "group a" in answer_lower or "a has" in answer_lower or "a is higher" in answer_lower:
                comparison_score = 0.0
            else:
                comparison_score = 0.5

        # Combined score: 60% data + 40% comparison
        total_score = (data_score * 0.6) + (comparison_score * 0.4)

        details = (
            f"Data accuracy: {data_score:.1%} | "
            f"Comparison: {'correct' if comparison_score == 1.0 else 'incorrect/unclear'} | "
            f"Reported values: {len(reported1) + len(reported2)}/{total_assets}"
        )

        return ValidationResult(
            score=total_score,
            is_correct=total_score >= 0.8,
            expected=ground_truth_str,
            actual=answer,
            details=details,
        )

    def _score_individual_value(
        self, name: str, expected: Optional[float], reported: Optional[float]
    ) -> Tuple[float, str]:
        """Score a single reported value against expected"""
        if expected is None:
            return 0.0, f"{name}: no ground truth"

        if reported is None:
            return 0.0, f"{name}: not reported"

        diff = abs(reported - expected)

        if diff <= 1.0:  # Within 1 percentage point
            return 1.0, f"{name}: ✓ ({reported:+.2f}% vs {expected:+.2f}%)"
        elif diff <= 3.0:  # Within 3 percentage points
            return 0.5, f"{name}: ~ ({reported:+.2f}% vs {expected:+.2f}%)"
        else:
            return 0.0, f"{name}: ✗ ({reported:+.2f}% vs {expected:+.2f}%)"

    def get_ground_truth_trigger(self, validation_info: dict) -> TriggerConfig:
        """
        Sector analysis: AI visits multiple pages, use ALL for range.

        Strategy: ALL - capture data across multiple page visits to
        account for real-time fluctuations. Duplicates will be deduplicated.
        """
        return TriggerConfig(
            trigger=UrlPatternTrigger(domains=["stooq.com"]),
            strategy=FetchStrategy.ALL,
            min_fetch_interval=60.0,
        )

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "stooq"
