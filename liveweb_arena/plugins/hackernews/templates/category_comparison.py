"""Category comparison template for Hacker News - HARD DIFFICULTY

RL-friendly design:
- Requires navigation to TWO different category pages (Ask HN, Show HN, Jobs)
- Requires visiting detail pages in EACH category to get comment counts
- Requires comparison and calculation across categories
- Tests multi-page navigation with branching paths
- Dynamic data: rankings and values change frequently
"""

import random
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, TriggerConfig, GroundTruthResult
)
from liveweb_arena.core.gt_collector import GTSourceType, get_current_gt_collector


@dataclass
class CategorySpec:
    """Specification for an HN category."""
    name: str       # Display name
    slug: str       # URL slug
    api_key: str    # Key in collected data


# Available categories for comparison
CATEGORIES = [
    CategorySpec("Ask HN", "ask", "ask"),
    CategorySpec("Show HN", "show", "show"),
]
# Note: Jobs category excluded - job posts typically have no comments


class MetricType(Enum):
    """Metrics for category comparison."""
    COMMENTS = ("comments", "descendants", "more comments")
    SCORE = ("score", "score", "higher score")

    def __init__(self, value: str, api_field: str, comparison_phrase: str):
        self._value_ = value
        self.api_field = api_field
        self.comparison_phrase = comparison_phrase


class ComparisonMode(Enum):
    """Mode of comparison between categories."""
    WHICH_HIGHER = ("which_higher", "which has")
    DIFFERENCE = ("difference", "difference")
    SUM_COMPARE = ("sum_compare", "combined")

    def __init__(self, value: str, phrase: str):
        self._value_ = value
        self.phrase = phrase


def _get_category_pairs() -> List[Tuple[CategorySpec, CategorySpec]]:
    """Get all valid category pairs for comparison."""
    pairs = []
    for i, cat1 in enumerate(CATEGORIES):
        for cat2 in CATEGORIES[i + 1:]:
            pairs.append((cat1, cat2))
    return pairs


@register_template("hackernews_category_comparison")
class HackerNewsCategoryComparisonTemplate(QuestionTemplate):
    """
    Template for cross-category comparison queries on HN.

    HARD difficulty: Requires visiting multiple category pages and detail pages.

    RL value:
    - Exploration space: Multiple navigation paths (which category first?)
    - Delayed reward: Must visit both categories before answering
    - Navigation skill: Tests understanding of site structure
    - Computation: Must compare values from different sources
    """

    GT_SOURCE = GTSourceType.PAGE_ONLY

    # Which rank positions to compare within categories
    RANK_OPTIONS = [1, 2, 3]

    PATTERNS = {
        (MetricType.COMMENTS, ComparisonMode.WHICH_HIGHER): [
            "Between the #{rank} story in {cat1} and the #{rank} story in {cat2}, which has more comments?",
            "Compare the #{rank} {cat1} story with the #{rank} {cat2} story on HN. Which one has more comments?",
            "On Hacker News, does the top-{rank} {cat1} post or the top-{rank} {cat2} post have more discussion?",
        ],
        (MetricType.SCORE, ComparisonMode.WHICH_HIGHER): [
            "Between the #{rank} story in {cat1} and the #{rank} story in {cat2}, which has a higher score?",
            "Compare the #{rank} {cat1} story with the #{rank} {cat2} story on HN. Which has more points?",
            "On Hacker News, does the top-{rank} {cat1} post or the top-{rank} {cat2} post have a higher score?",
        ],
        (MetricType.COMMENTS, ComparisonMode.DIFFERENCE): [
            "How many more comments does the #{rank} {cat1} story have compared to the #{rank} {cat2} story? (Answer with a signed number)",
            "What's the comment count difference between the #{rank} {cat1} post and the #{rank} {cat2} post on HN?",
            "Compare #{rank} in {cat1} vs #{rank} in {cat2}. What's the difference in comment counts?",
        ],
        (MetricType.SCORE, ComparisonMode.DIFFERENCE): [
            "How many more points does the #{rank} {cat1} story have compared to the #{rank} {cat2} story? (Answer with a signed number)",
            "What's the score difference between the #{rank} {cat1} post and the #{rank} {cat2} post on HN?",
            "Compare #{rank} in {cat1} vs #{rank} in {cat2}. What's the difference in scores?",
        ],
        (MetricType.COMMENTS, ComparisonMode.SUM_COMPARE): [
            "Add up the comments for the top {rank} stories in both {cat1} and {cat2}. Which category has more total comments?",
            "Sum the comment counts for #{rank_list} in {cat1} and {cat2}. Which section has higher combined discussion?",
            "Between {cat1} and {cat2}, which has more total comments across their top {rank} stories?",
        ],
        (MetricType.SCORE, ComparisonMode.SUM_COMPARE): [
            "Add up the scores for the top {rank} stories in both {cat1} and {cat2}. Which category has more total points?",
            "Sum the scores for #{rank_list} in {cat1} and {cat2}. Which section has higher combined score?",
            "Between {cat1} and {cat2}, which has more total points across their top {rank} stories?",
        ],
    }

    def __init__(self):
        super().__init__("hackernews_category_comparison")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a category comparison question."""
        rng = random.Random(seed)

        # Select categories
        pairs = _get_category_pairs()
        cat1, cat2 = rng.choice(pairs)

        # Randomly swap order for variety
        if rng.random() < 0.5:
            cat1, cat2 = cat2, cat1

        # Select metric and comparison mode
        metrics = list(MetricType)
        modes = list(ComparisonMode)

        if variant is not None:
            metric_idx = variant % len(metrics)
            mode_idx = (variant // len(metrics)) % len(modes)
            metric = metrics[metric_idx]
            mode = modes[mode_idx]
        else:
            metric = rng.choice(metrics)
            mode = rng.choice(modes)

        # Select rank
        rank = rng.choice(self.RANK_OPTIONS)

        # Generate question
        patterns = self.PATTERNS[(metric, mode)]
        pattern = rng.choice(patterns)

        # Format rank list for SUM_COMPARE mode
        rank_list = ", ".join(f"#{i}" for i in range(1, rank + 1))

        question_text = pattern.format(
            rank=rank,
            rank_list=rank_list,
            cat1=cat1.name,
            cat2=cat2.name,
        )

        validation_info = {
            "metric": metric.value,
            "metric_field": metric.api_field,
            "comparison_mode": mode.value,
            "rank": rank,
            "category1_name": cat1.name,
            "category1_slug": cat1.slug,
            "category2_name": cat2.name,
            "category2_slug": cat2.slug,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://news.ycombinator.com/",
            variables={"metric": metric, "mode": mode, "rank": rank, "categories": (cat1, cat2)},
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=12,  # Homepage + 2 category pages + detail pages
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        metric = validation_info.get("metric", "")
        mode = validation_info.get("comparison_mode", "")
        rank = validation_info.get("rank", 1)
        cat1 = validation_info.get("category1_name", "")
        cat2 = validation_info.get("category2_name", "")
        return f"""Task-Specific Rules (HN Category Comparison):
- Comparing {cat1} vs {cat2}, metric: {metric}, mode: {mode}
- Rank position: {rank}
- For WHICH_HIGHER: Score 1.0 if correct category named
- For DIFFERENCE: Score 1.0 if within ±5 of correct value
- For SUM_COMPARE: Score 1.0 if correct category named
- Score 0.0: Wrong answer
- Note: Must visit both category pages and relevant detail pages"""

    def _get_category_story_value(
        self,
        collected: Dict[str, Any],
        category_slug: str,
        rank: int,
        metric_field: str,
    ) -> Optional[int]:
        """Get metric value for a story at given rank in category."""
        # Look for category-specific data
        category_key = f"hn_category:{category_slug}"
        if category_key in collected:
            category_data = collected[category_key]
            stories = category_data.get("stories")
            if not stories:
                return None
            for story_id, story_data in stories.items():
                if isinstance(story_data, dict) and story_data.get("rank") == rank:
                    value = story_data.get(metric_field)
                    if value is not None:
                        return int(value)

        return None

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Calculate ground truth from collected API data."""
        metric_field = validation_info.get("metric_field", "descendants")
        mode = validation_info.get("comparison_mode", "which_higher")
        rank = validation_info.get("rank", 1)
        cat1_slug = validation_info.get("category1_slug", "")
        cat2_slug = validation_info.get("category2_slug", "")
        cat1_name = validation_info.get("category1_name", "")
        cat2_name = validation_info.get("category2_name", "")

        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.system_error("No GT collector")

        collected = gt_collector.get_collected_api_data()
        if not collected:
            return GroundTruthResult.fail("No HN data collected")

        # For SUM_COMPARE, we need all ranks from 1 to rank
        if mode == "sum_compare":
            sum1 = 0
            sum2 = 0
            missing1 = []
            missing2 = []

            for r in range(1, rank + 1):
                val1 = self._get_category_story_value(collected, cat1_slug, r, metric_field)
                val2 = self._get_category_story_value(collected, cat2_slug, r, metric_field)

                if val1 is not None:
                    sum1 += val1
                else:
                    missing1.append(r)

                if val2 is not None:
                    sum2 += val2
                else:
                    missing2.append(r)

            if missing1 or missing2:
                msg_parts = []
                if missing1:
                    msg_parts.append(f"{cat1_name} missing ranks: {missing1}")
                if missing2:
                    msg_parts.append(f"{cat2_name} missing ranks: {missing2}")
                return GroundTruthResult.not_collected(
                    f"Incomplete data. {'; '.join(msg_parts)}. "
                    f"Agent needs to visit both category pages and story details."
                )

            if sum1 == sum2:
                return GroundTruthResult.ok(f"TIE: {cat1_name} and {cat2_name} (both {sum1})")
            return GroundTruthResult.ok(cat1_name if sum1 > sum2 else cat2_name)

        # For single-rank comparisons
        val1 = self._get_category_story_value(collected, cat1_slug, rank, metric_field)
        val2 = self._get_category_story_value(collected, cat2_slug, rank, metric_field)

        if val1 is None:
            return GroundTruthResult.not_collected(
                f"#{rank} story in {cat1_name} not found. "
                f"Agent needs to visit {cat1_name} category page."
            )

        if val2 is None:
            return GroundTruthResult.not_collected(
                f"#{rank} story in {cat2_name} not found. "
                f"Agent needs to visit {cat2_name} category page."
            )

        if mode == "which_higher":
            if val1 == val2:
                return GroundTruthResult.ok(f"TIE: {cat1_name} and {cat2_name} (both {val1})")
            return GroundTruthResult.ok(cat1_name if val1 > val2 else cat2_name)
        else:  # difference
            diff = val1 - val2
            return GroundTruthResult.ok(str(diff))

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

        expected = result.value
        mode = validation_info.get("comparison_mode", "which_higher")
        cat1_name = validation_info.get("category1_name", "")
        cat2_name = validation_info.get("category2_name", "")

        answer_lower = answer.lower()

        if mode in ("which_higher", "sum_compare"):
            # Handle tie: either category name is acceptable
            if expected.startswith("TIE:"):
                if cat1_name.lower() in answer_lower or cat2_name.lower() in answer_lower:
                    return ValidationResult(
                        score=1.0, is_correct=True, expected=expected,
                        actual=answer, details="Tie - either answer accepted",
                    )
                return ValidationResult(
                    score=0.0, is_correct=False, expected=expected,
                    actual=answer, details="Tie but neither category mentioned",
                )

            # Check if correct category is mentioned
            expected_lower = expected.lower()
            if expected_lower in answer_lower:
                return ValidationResult(
                    score=1.0,
                    is_correct=True,
                    expected=expected,
                    actual=answer,
                    details="Correct category identified",
                )

            # Check for the other category being mentioned (wrong answer)
            other = cat2_name if expected == cat1_name else cat1_name
            if other.lower() in answer_lower:
                return ValidationResult(
                    score=0.0,
                    is_correct=False,
                    expected=expected,
                    actual=answer,
                    details=f"Wrong category: expected {expected}",
                )

            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=expected,
                actual=answer,
                details="Could not identify category in answer",
            )

        else:  # difference mode
            import re
            # Handle signed numbers
            numbers = re.findall(r'-?\d+', answer)
            if not numbers:
                return ValidationResult(
                    score=0.0,
                    is_correct=False,
                    expected=expected,
                    actual=answer,
                    details="No number found in answer",
                )

            try:
                actual_value = int(numbers[0])
                expected_value = int(expected)
            except ValueError:
                return ValidationResult(
                    score=0.0,
                    is_correct=False,
                    expected=expected,
                    actual=answer,
                    details="Could not parse numbers",
                )

            diff = abs(actual_value - expected_value)

            if diff <= 5:
                return ValidationResult(
                    score=1.0,
                    is_correct=True,
                    expected=expected,
                    actual=answer,
                    details=f"Within ±5 tolerance (diff: {diff})",
                )

            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=expected,
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

