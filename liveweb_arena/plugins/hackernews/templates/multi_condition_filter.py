"""Multi-condition filter template for Hacker News - HARD DIFFICULTY

RL-friendly design:
- Requires visiting multiple story detail pages to get comment counts
- Requires checking TWO conditions simultaneously (cannot sort single column)
- Large exploration space: different strategies for scanning stories
- Dynamic data: comment counts change frequently
- Combinatorial question space: threshold combinations prevent memorization
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


class ConditionType(Enum):
    """Types of multi-condition filters for HN stories."""
    # High comments but low score (underrated discussions)
    HIGH_COMMENTS_LOW_SCORE = ("high_comments_low_score", "comments > {t1} AND score < {t2}")
    # Low comments but high score (viral with few discussions)
    LOW_COMMENTS_HIGH_SCORE = ("low_comments_high_score", "comments < {t1} AND score > {t2}")
    # Both high (hot topics)
    BOTH_HIGH = ("both_high", "comments > {t1} AND score > {t2}")
    # Score-to-comments ratio check (engagement analysis)
    HIGH_ENGAGEMENT = ("high_engagement", "score > {t1} AND comments > {t2}")

    def __init__(self, value: str, pattern: str):
        self._value_ = value
        self.pattern = pattern


# Threshold configurations for each condition type
# Designed to produce non-trivial counts (not 0 or all)
THRESHOLDS = {
    ConditionType.HIGH_COMMENTS_LOW_SCORE: [
        (50, 200),   # comments > 50 AND score < 200
        (100, 300),  # comments > 100 AND score < 300
        (30, 150),   # comments > 30 AND score < 150
    ],
    ConditionType.LOW_COMMENTS_HIGH_SCORE: [
        (20, 300),   # comments < 20 AND score > 300
        (10, 400),   # comments < 10 AND score > 400
        (30, 250),   # comments < 30 AND score > 250
    ],
    ConditionType.BOTH_HIGH: [
        (50, 200),   # comments > 50 AND score > 200
        (100, 300),  # comments > 100 AND score > 300
        (30, 150),   # comments > 30 AND score > 150
    ],
    ConditionType.HIGH_ENGAGEMENT: [
        (200, 50),   # score > 200 AND comments > 50
        (300, 100),  # score > 300 AND comments > 100
        (150, 30),   # score > 150 AND comments > 30
    ],
}


@register_template("hackernews_multi_condition_filter")
class HackerNewsMultiConditionFilterTemplate(QuestionTemplate):
    """
    Template for multi-condition count queries on HN stories.

    HARD difficulty: Requires checking multiple conditions simultaneously.
    Cannot be solved by sorting - must scan and evaluate each story.

    RL value:
    - Exploration space: Many valid action sequences
    - Delayed reward: Must visit multiple pages before answering
    - Strategy optimization: Can skip obviously non-matching stories
    - Uncertainty: Cannot predict answer without full scan
    """

    GT_SOURCE = GTSourceType.PAGE_ONLY

    # Number of top stories to check (subset of front page)
    STORY_COUNTS = [10, 15, 20]

    PATTERNS = {
        ConditionType.HIGH_COMMENTS_LOW_SCORE: [
            "Among the top {n} stories on HN, how many have more than {t1} comments but a score under {t2}?",
            "Count how many of the top {n} Hacker News stories have {t1}+ comments AND score below {t2}.",
            "On the HN front page, among the top {n} stories, how many show high discussion ({t1}+ comments) but moderate votes (score < {t2})?",
        ],
        ConditionType.LOW_COMMENTS_HIGH_SCORE: [
            "Among the top {n} stories on HN, how many have fewer than {t1} comments but a score over {t2}?",
            "Count how many of the top {n} Hacker News stories have under {t1} comments AND score above {t2}.",
            "On the HN front page, among the top {n} stories, how many are viral (score > {t2}) with few comments (< {t1})?",
        ],
        ConditionType.BOTH_HIGH: [
            "Among the top {n} stories on HN, how many have both {t1}+ comments AND {t2}+ score?",
            "Count how many of the top {n} Hacker News stories have at least {t1} comments and at least {t2} points.",
            "On the HN front page, among the top {n} stories, how many are hot topics with both high comments ({t1}+) and high score ({t2}+)?",
        ],
        ConditionType.HIGH_ENGAGEMENT: [
            "Among the top {n} stories on HN, how many have a score above {t1} AND more than {t2} comments?",
            "Count how many of the top {n} Hacker News stories score over {t1} and have {t2}+ comments.",
            "On the HN front page, among the top {n} stories, how many show high engagement (score > {t1}, comments > {t2})?",
        ],
    }

    def __init__(self):
        super().__init__("hackernews_multi_condition_filter")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a multi-condition filter question."""
        rng = random.Random(seed)

        # Select condition type
        conditions = list(ConditionType)
        if variant is not None:
            condition = conditions[variant % len(conditions)]
        else:
            condition = rng.choice(conditions)

        # Select story count
        n = rng.choice(self.STORY_COUNTS)

        # Select thresholds
        thresholds = THRESHOLDS[condition]
        t1, t2 = rng.choice(thresholds)

        # Generate question
        patterns = self.PATTERNS[condition]
        pattern = rng.choice(patterns)
        question_text = pattern.format(n=n, t1=t1, t2=t2)

        validation_info = {
            "condition_type": condition.value,
            "story_count": n,
            "threshold1": t1,
            "threshold2": t2,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://news.ycombinator.com/",
            variables={"condition": condition, "n": n, "t1": t1, "t2": t2},
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=15,  # Homepage + multiple detail pages
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        condition = validation_info.get("condition_type", "")
        n = validation_info.get("story_count", 10)
        t1 = validation_info.get("threshold1", 0)
        t2 = validation_info.get("threshold2", 0)
        return f"""Task-Specific Rules (HN Multi-Condition Filter):
- Condition: {condition} with thresholds {t1}, {t2}
- Must check top {n} stories on HN front page
- Score 1.0: Exact count match
- Score 0.5: Count within ±2 of correct answer
- Score 0.0: Wrong count or no answer
- Note: Comment counts require visiting each story's detail page"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Calculate ground truth count from collected API data."""
        condition_type = validation_info.get("condition_type", "")
        n = validation_info.get("story_count", 10)
        t1 = validation_info.get("threshold1", 0)
        t2 = validation_info.get("threshold2", 0)

        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.system_error("No GT collector")

        collected = gt_collector.get_collected_api_data()
        if not collected:
            return GroundTruthResult.fail("No HN data collected")

        # Get stories with rank and required fields
        stories = []
        for story_id, data in collected.items():
            # Skip non-story entries
            if not isinstance(data, dict):
                continue
            if story_id.startswith("user:") or story_id.startswith("hn_category:"):
                continue
            if story_id.startswith("external:") or story_id.startswith("hn_external:"):
                continue

            rank = data.get("rank")
            if rank is None or rank > n:
                continue

            score = data.get("score")
            descendants = data.get("descendants")

            # Both fields required for evaluation
            if score is None or descendants is None:
                continue

            stories.append({
                "rank": rank,
                "score": score,
                "comments": descendants,
            })

        if len(stories) < n:
            available_ranks = sorted([s["rank"] for s in stories])
            return GroundTruthResult.not_collected(
                f"Only {len(stories)} stories have complete data (need {n}). "
                f"Available ranks: {available_ranks}. "
                f"Agent may need to visit more story detail pages."
            )

        # Sort by rank and take top n
        stories.sort(key=lambda x: x["rank"])
        stories = stories[:n]

        # Count matching stories
        count = 0
        for story in stories:
            score = story["score"]
            comments = story["comments"]

            match = False
            if condition_type == "high_comments_low_score":
                match = comments > t1 and score < t2
            elif condition_type == "low_comments_high_score":
                match = comments < t1 and score > t2
            elif condition_type == "both_high":
                match = comments > t1 and score > t2
            elif condition_type == "high_engagement":
                match = score > t1 and comments > t2

            if match:
                count += 1

        return GroundTruthResult.ok(str(count))

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate count answer."""
        result = await self.get_ground_truth(validation_info)

        if not result.success:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        expected_count = int(result.value)

        import re
        numbers = re.findall(r'\d+', answer)
        if not numbers:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=str(expected_count),
                actual=answer,
                details="No number found in answer",
            )

        actual_count = int(numbers[0])

        if actual_count == expected_count:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=str(expected_count),
                actual=answer,
                details="Exact count match",
            )
        elif abs(actual_count - expected_count) <= 2:
            return ValidationResult(
                score=0.5,
                is_correct=False,
                expected=str(expected_count),
                actual=answer,
                details=f"Close count (within ±2): expected {expected_count}, got {actual_count}",
            )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=str(expected_count),
            actual=answer,
            details=f"Wrong count: expected {expected_count}, got {actual_count}",
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

