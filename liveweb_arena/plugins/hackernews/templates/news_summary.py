"""HackerNews news summary template - MEDIUM-HARD difficulty

RL-friendly design:
- Tests dual abilities: information extraction + summarization
- Variable story counts (3, 5, 7, 10) prevent memorization
- LLM-based validation with structured scoring rubric
- Requires understanding content, not just copying text
- Multiple summary types add variation

Evaluation dimensions:
- Story coverage (40%): Agent mentions all required stories
- Topic accuracy (30%): Descriptions reflect actual content
- Factual accuracy (20%): Numbers (score/comments) approximately correct
- Organization (10%): Clear, readable structure
"""

import json
import random
from typing import Any, Dict, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, TriggerConfig, GroundTruthResult
)
from liveweb_arena.core.gt_collector import GTSourceType, get_current_gt_collector


@register_template("hackernews_news_summary")
class HackerNewsNewsSummaryTemplate(QuestionTemplate):
    """
    HN news summary template - tests information extraction + summarization.

    MEDIUM-HARD difficulty: Requires reading multiple stories and
    synthesizing them into a coherent summary.

    RL value:
    - Story count varies (3-10), impossible to memorize all combinations
    - Must understand content to summarize accurately
    - Gradual scoring based on coverage and accuracy
    - No single correct answer format
    """

    GT_SOURCE = GTSourceType.PAGE_ONLY

    # Story count options (varied for training diversity)
    STORY_COUNTS = [3, 5, 7, 10]

    # Summary type definitions
    SUMMARY_TYPES = {
        "brief": "one-sentence summary per story",
        "detailed": "summary with engagement metrics (score, comments)",
        "thematic": "grouped by topic/theme",
    }

    # Question patterns by summary type
    PATTERNS = {
        "brief": [
            "Summarize the top {n} stories on Hacker News in one sentence each.",
            "Give me a quick overview of the top {n} HN stories right now.",
            "What are the top {n} trending topics on Hacker News? Brief summary please.",
        ],
        "detailed": [
            "Summarize the top {n} HN stories with their scores and comment counts.",
            "Write a news briefing covering the top {n} Hacker News stories, including engagement metrics.",
            "Describe the top {n} HN stories in detail, mentioning how popular each one is.",
        ],
        "thematic": [
            "Group the top {n} HN stories by topic and summarize each group.",
            "What themes emerge from the top {n} Hacker News stories? Summarize by category.",
            "Analyze the top {n} HN stories - what topics are trending?",
        ],
    }

    def __init__(self):
        super().__init__("hackernews_news_summary")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a news summary question."""
        rng = random.Random(seed)

        # Select summary type
        summary_types = list(self.SUMMARY_TYPES.keys())
        if variant is not None:
            summary_type = summary_types[variant % len(summary_types)]
        else:
            summary_type = rng.choice(summary_types)

        # Select story count
        n = rng.choice(self.STORY_COUNTS)

        # Generate question
        patterns = self.PATTERNS[summary_type]
        pattern = rng.choice(patterns)
        question_text = pattern.format(n=n)

        validation_info = {
            "story_count": n,
            "summary_type": summary_type,
        }

        # Expected steps: homepage visit + reading time
        expected_steps = 5 + (n // 2)

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://news.ycombinator.com/",
            variables={"story_count": n, "summary_type": summary_type},
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=expected_steps,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        """Return LLM validation rules for news summary."""
        n = validation_info.get("story_count", 5)
        summary_type = validation_info.get("summary_type", "brief")
        type_desc = self.SUMMARY_TYPES.get(summary_type, "summary")

        return f"""Task-Specific Rules (HN News Summary):
- Task: Summarize top {n} stories from Hacker News ({type_desc})
- Summary type: {summary_type}

Ground Truth is a JSON object containing:
- stories: array of story objects with rank, title, score, comments, author
- story_count: number of stories required ({n})
- summary_type: type of summary requested ({summary_type})

Evaluation Criteria (total 1.0):

1. Story Coverage (40%):
   - Full marks: All {n} stories mentioned (by title or clear topic reference)
   - Partial: Proportional to coverage (e.g., 4/{n} = 0.8 * 0.4)
   - Zero: No stories from GT mentioned

2. Topic Accuracy (30%):
   - Full marks: Story descriptions accurately reflect titles/topics
   - Partial: Some stories mischaracterized or vague
   - Zero: Descriptions completely wrong or irrelevant

3. Factual Accuracy (20%):
   - Full marks: Scores/comments within ±30% when mentioned
   - Partial: Some numbers significantly off
   - Zero: Completely wrong numbers
   - Note: If summary doesn't mention numbers, give partial credit (0.1)

4. Organization (10%):
   - Full marks: Clear structure, easy to read, appropriate format
   - Partial: Somewhat disorganized or wrong format for type
   - Zero: Incoherent or unreadable

Calculate weighted total. Output JSON: {{"score": <0.0-1.0>, "reasoning": "<brief explanation>"}}"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Get structured GT from collected story data."""
        n = validation_info.get("story_count", 5)
        summary_type = validation_info.get("summary_type", "brief")

        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.system_error("No GT collector available")

        collected = gt_collector.get_collected_api_data()
        if not collected:
            return GroundTruthResult.not_collected(
                "No HN data collected. Agent needs to visit HN homepage."
            )

        # Extract story data
        stories = []
        for key, data in collected.items():
            # Skip non-story entries
            if not isinstance(data, dict):
                continue
            if key.startswith(("user:", "hn_category:", "external:", "hn_external:")):
                continue

            # Must have title and rank to be a story
            title = data.get("title")
            rank = data.get("rank")
            if not title or rank is None:
                continue

            score = data.get("score")
            descendants = data.get("descendants")
            if score is None or descendants is None:
                continue  # Skip stories with incomplete data

            stories.append({
                "rank": rank,
                "title": title,
                "score": score,
                "comments": descendants,
                "author": data.get("by", "unknown"),  # Display-only
                "url": data.get("url", ""),  # Display-only
            })

        # Sort by rank and take top n
        stories.sort(key=lambda x: x["rank"])
        stories = stories[:n]

        if len(stories) < n:
            available_ranks = sorted([s["rank"] for s in stories])
            return GroundTruthResult.not_collected(
                f"Only {len(stories)} stories collected (need {n}). "
                f"Available ranks: {available_ranks}. "
                f"Agent needs to visit HN homepage to see all stories."
            )

        # Build structured GT
        gt_data = {
            "stories": stories,
            "story_count": n,
            "summary_type": summary_type,
        }

        return GroundTruthResult.ok(json.dumps(gt_data, ensure_ascii=False))

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """
        Validate answer using LLM.

        This template uses LLM validation (via eval.py's LLMValidator),
        so this method is primarily for fallback/direct validation.
        """
        result = await self.get_ground_truth(validation_info)

        if not result.success:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        # For direct validation (without LLM), do basic coverage check
        gt_data = json.loads(result.value)
        stories = gt_data["stories"]

        # Count how many story titles are mentioned in the answer
        answer_lower = answer.lower()
        covered = 0
        for story in stories:
            title = story["title"].lower()
            # Check if title words appear in answer (fuzzy match)
            title_words = [w for w in title.split() if len(w) > 3]
            matches = sum(1 for w in title_words if w in answer_lower)
            if matches >= len(title_words) * 0.5:
                covered += 1

        coverage_ratio = covered / len(stories) if stories else 0

        if coverage_ratio >= 0.8:
            return ValidationResult(
                score=coverage_ratio * 0.4 + 0.3,  # Partial score
                is_correct=True,
                expected=f"{len(stories)} stories",
                actual=answer[:200],
                details=f"Covered {covered}/{len(stories)} stories (basic check, use LLM for full eval)",
            )

        return ValidationResult(
            score=coverage_ratio * 0.4,
            is_correct=False,
            expected=f"{len(stories)} stories",
            actual=answer[:200],
            details=f"Covered only {covered}/{len(stories)} stories",
        )

    def get_ground_truth_trigger(self, validation_info: Dict[str, Any]) -> TriggerConfig:
        """Trigger on HN domain visits."""
        trigger = UrlPatternTrigger(domains=["news.ycombinator.com"])
        return TriggerConfig(trigger=trigger)

    @classmethod
    def get_cache_source(cls) -> str:
        return "hackernews"

    def get_gt_source(self) -> GTSourceType:
        return self.GT_SOURCE

