"""Extrema comparison template for Hacker News - HARD DIFFICULTY

RL-friendly design:
- Requires visiting ALL story detail pages within range to get comment counts
- Requires tracking max AND min values across pages
- Requires final calculation (difference or ratio)
- Dynamic data: values change frequently
- No sorting shortcut: must actually visit all pages
"""

import random
from enum import Enum
from typing import Any, Dict, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, TriggerConfig, GroundTruthResult
)
from liveweb_arena.core.gt_collector import GTSourceType, get_current_gt_collector


class MetricType(Enum):
    """Metrics for extrema comparison."""
    COMMENTS = ("comments", "comments", "descendants")  # value, display_name, api_field
    SCORE = ("score", "points", "score")

    def __init__(self, value: str, display_name: str, api_field: str):
        self._value_ = value
        self.display_name = display_name
        self.api_field = api_field


class ComparisonType(Enum):
    """Types of extrema comparison."""
    DIFFERENCE = ("difference", "difference between")
    RATIO = ("ratio", "ratio of")

    def __init__(self, value: str, display_phrase: str):
        self._value_ = value
        self.display_phrase = display_phrase


@register_template("hackernews_extrema_comparison")
class HackerNewsExtremaComparisonTemplate(QuestionTemplate):
    """
    Template for extrema comparison queries on HN stories.

    HARD difficulty: Requires visiting ALL stories in range to find min/max.

    RL value:
    - Exploration space: Must visit every page, no shortcuts
    - Delayed reward: Answer only available after visiting all pages
    - Memory required: Must track running max/min across visits
    - Computation: Must calculate difference or ratio at the end
    """

    GT_SOURCE = GTSourceType.PAGE_ONLY

    # Number of top stories to analyze
    STORY_COUNTS = [5, 7, 10]

    PATTERNS = {
        (MetricType.COMMENTS, ComparisonType.DIFFERENCE): [
            "Among the top {n} stories on HN, what's the difference between the highest and lowest comment counts?",
            "Look at the top {n} Hacker News stories. What's the gap between the most-commented and least-commented stories?",
            "On the HN front page, among the top {n} stories, how many more comments does the most-discussed have vs the least-discussed?",
        ],
        (MetricType.SCORE, ComparisonType.DIFFERENCE): [
            "Among the top {n} stories on HN, what's the difference between the highest and lowest scores?",
            "Look at the top {n} Hacker News stories. What's the gap between the highest-scoring and lowest-scoring stories?",
            "On the HN front page, among the top {n} stories, how many more points does the top-voted have vs the lowest?",
        ],
        (MetricType.COMMENTS, ComparisonType.RATIO): [
            "Among the top {n} stories on HN, what's the ratio of the highest to lowest comment count? (Answer as X.XX)",
            "Look at the top {n} Hacker News stories. How many times more comments does the most-commented have vs the least?",
            "On the HN front page, among the top {n} stories, divide the max comment count by the min. What's the ratio?",
        ],
        (MetricType.SCORE, ComparisonType.RATIO): [
            "Among the top {n} stories on HN, what's the ratio of the highest to lowest score? (Answer as X.XX)",
            "Look at the top {n} Hacker News stories. How many times higher is the top score vs the lowest?",
            "On the HN front page, among the top {n} stories, divide the max score by the min. What's the ratio?",
        ],
    }

    def __init__(self):
        super().__init__("hackernews_extrema_comparison")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate an extrema comparison question."""
        rng = random.Random(seed)

        # Select metric and comparison type
        metrics = list(MetricType)
        comparisons = list(ComparisonType)

        if variant is not None:
            metric_idx = variant % len(metrics)
            comp_idx = (variant // len(metrics)) % len(comparisons)
            metric = metrics[metric_idx]
            comparison = comparisons[comp_idx]
        else:
            metric = rng.choice(metrics)
            comparison = rng.choice(comparisons)

        # Select story count
        n = rng.choice(self.STORY_COUNTS)

        # Generate question
        patterns = self.PATTERNS[(metric, comparison)]
        pattern = rng.choice(patterns)
        question_text = pattern.format(n=n)

        validation_info = {
            "metric": metric.value,
            "metric_field": metric.api_field,
            "comparison": comparison.value,
            "story_count": n,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://news.ycombinator.com/",
            variables={"metric": metric, "comparison": comparison, "n": n},
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=n + 3,  # Homepage + n detail pages + processing
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        metric = validation_info.get("metric", "")
        comparison = validation_info.get("comparison", "")
        n = validation_info.get("story_count", 5)
        return f"""Task-Specific Rules (HN Extrema Comparison):
- Metric: {metric}, Comparison: {comparison}
- Must analyze top {n} stories on HN front page
- For difference: Score 1.0 if within ±5% of correct value
- For ratio: Score 1.0 if within ±0.2 of correct value
- Score 0.5: Within ±20% (difference) or ±0.5 (ratio)
- Score 0.0: Outside tolerance or no answer
- Note: Must visit each story's detail page to get comment counts"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Calculate ground truth from collected API data."""
        metric_field = validation_info.get("metric_field", "descendants")
        comparison = validation_info.get("comparison", "difference")
        n = validation_info.get("story_count", 5)

        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.system_error("No GT collector")

        collected = gt_collector.get_collected_api_data()
        if not collected:
            return GroundTruthResult.fail("No HN data collected")

        # Get stories with rank and the required metric field
        stories = []
        for story_id, data in collected.items():
            if not isinstance(data, dict):
                continue
            if story_id.startswith("user:") or story_id.startswith("hn_category:"):
                continue
            if story_id.startswith("external:") or story_id.startswith("hn_external:"):
                continue

            rank = data.get("rank")
            if rank is None or rank > n:
                continue

            value = data.get(metric_field)
            if value is None:
                continue

            stories.append({
                "rank": rank,
                "value": int(value),
            })

        if len(stories) < n:
            available_ranks = sorted([s["rank"] for s in stories])
            return GroundTruthResult.not_collected(
                f"Only {len(stories)} stories have {metric_field} data (need {n}). "
                f"Available ranks: {available_ranks}. "
                f"Agent may need to visit more story detail pages."
            )

        # Sort by rank and take top n
        stories.sort(key=lambda x: x["rank"])
        stories = stories[:n]

        # Find min and max
        values = [s["value"] for s in stories]
        max_val = max(values)
        min_val = min(values)

        if comparison == "difference":
            result = max_val - min_val
            return GroundTruthResult.ok(str(result))
        else:  # ratio
            if min_val == 0:
                # Handle division by zero - use "infinity" indicator
                return GroundTruthResult.ok("inf")
            result = round(max_val / min_val, 2)
            return GroundTruthResult.ok(str(result))

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate answer."""
        result = await self.get_ground_truth(validation_info)

        if not result.success:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        expected_str = result.value
        comparison = validation_info.get("comparison", "difference")

        # Handle infinity case
        if expected_str == "inf":
            answer_lower = answer.lower()
            if "inf" in answer_lower or "undefined" in answer_lower or "zero" in answer_lower:
                return ValidationResult(
                    score=1.0,
                    is_correct=True,
                    expected="infinity (min is 0)",
                    actual=answer,
                    details="Correctly identified division by zero case",
                )
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected="infinity (min is 0)",
                actual=answer,
                details="Min value is 0, expected infinity/undefined answer",
            )

        expected_value = float(expected_str)

        # Extract number from answer
        import re
        numbers = re.findall(r'[\d.]+', answer)
        if not numbers:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=expected_str,
                actual=answer,
                details="No number found in answer",
            )

        try:
            actual_value = float(numbers[0])
        except ValueError:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=expected_str,
                actual=answer,
                details="Could not parse number from answer",
            )

        # Calculate tolerance based on comparison type
        if comparison == "difference":
            # 5% tolerance for exact, 20% for partial
            if expected_value == 0:
                diff_pct = 0 if actual_value == 0 else 100
            else:
                diff_pct = abs(actual_value - expected_value) / expected_value * 100

            if diff_pct <= 5:
                return ValidationResult(
                    score=1.0,
                    is_correct=True,
                    expected=expected_str,
                    actual=answer,
                    details=f"Within 5% tolerance (diff: {diff_pct:.1f}%)",
                )
            elif diff_pct <= 20:
                return ValidationResult(
                    score=0.5,
                    is_correct=False,
                    expected=expected_str,
                    actual=answer,
                    details=f"Within 20% tolerance (diff: {diff_pct:.1f}%)",
                )
        else:  # ratio
            # 0.2 tolerance for exact, 0.5 for partial
            diff = abs(actual_value - expected_value)

            if diff <= 0.2:
                return ValidationResult(
                    score=1.0,
                    is_correct=True,
                    expected=expected_str,
                    actual=answer,
                    details=f"Within ±0.2 tolerance (diff: {diff:.2f})",
                )
            elif diff <= 0.5:
                return ValidationResult(
                    score=0.5,
                    is_correct=False,
                    expected=expected_str,
                    actual=answer,
                    details=f"Within ±0.5 tolerance (diff: {diff:.2f})",
                )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=expected_str,
            actual=answer,
            details=f"Outside tolerance: expected {expected_value}, got {actual_value}",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> TriggerConfig:
        """Trigger on HN domain visits."""
        trigger = UrlPatternTrigger(domains=["news.ycombinator.com"])
        return TriggerConfig(trigger=trigger)

    @classmethod
    def get_cache_source(cls) -> str:
        return "hackernews"

    def get_gt_source(self) -> GTSourceType:
        return self.GT_SOURCE

