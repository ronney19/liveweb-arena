"""Book comparison template for Open Library - MEDIUM DIFFICULTY."""

import random
from enum import Enum
from typing import Any, Dict, Optional

from liveweb_arena.core.ground_truth_trigger import (
    GroundTruthResult,
    TriggerConfig,
    UrlPatternTrigger,
)
from liveweb_arena.core.gt_collector import GTSourceType
from liveweb_arena.core.validators.base import (
    GeneratedQuestion,
    QuestionTemplate,
    ValidationResult,
    register_template,
)
from .book_stats import BOOK_POOL
from .common import get_collected_data, iter_collected_works, normalize_text, parse_numeric, titles_match


class ComparisonMetric(Enum):
    """Dynamic metrics suitable for two-book comparisons."""

    RATINGS_COUNT = ("ratings_count", "number of ratings")
    WANT_TO_READ = ("want_to_read_count", "want-to-read count")
    ALREADY_READ = ("already_read_count", "already-read count")


PATTERNS = [
    (
        "On Open Library, which book has a higher {metric_label}: "
        "\"{book_a}\" or \"{book_b}\"? Answer with the book title only."
    ),
    (
        "Compare \"{book_a}\" and \"{book_b}\" on Open Library. "
        "Which one has more {metric_label}? Reply with just the title."
    ),
    (
        "Between \"{book_a}\" and \"{book_b}\" on Open Library, which title "
        "has the larger {metric_label}? Return only the title."
    ),
]


@register_template("openlibrary_book_comparison")
class OpenLibraryBookComparisonTemplate(QuestionTemplate):
    """Compare one dynamic metric between two books."""

    GT_SOURCE = GTSourceType.PAGE_ONLY

    def __init__(self):
        super().__init__("openlibrary_book_comparison")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        metrics = list(ComparisonMetric)
        metric = metrics[variant % len(metrics)] if variant is not None else rng.choice(metrics)

        (book_a_title, book_a_query), (book_b_title, book_b_query) = rng.sample(BOOK_POOL, 2)

        # Randomly swap order to prevent systematic position bias
        if rng.random() > 0.5:
            book_a_title, book_a_query, book_b_title, book_b_query = (
                book_b_title, book_b_query, book_a_title, book_a_query,
            )

        pattern = rng.choice(PATTERNS)
        question_text = (
            pattern.format(
                book_a=book_a_title,
                book_b=book_b_title,
                metric_label=metric.value[1],
            )
            + " If equal, choose the title that comes first alphabetically."
        )

        start_url = f"https://openlibrary.org/search?q={book_a_query.replace(' ', '+')}"

        return GeneratedQuestion(
            question_text=question_text,
            start_url=start_url,
            variables={
                "metric": metric.value[0],
                "book_a": book_a_title,
                "book_b": book_b_title,
            },
            validation_info={
                "metric": metric.value[0],
                "metric_label": metric.value[1],
                "book_a": book_a_title,
                "book_a_query": book_a_query,
                "book_b": book_b_title,
                "book_b_query": book_b_query,
            },
            template_name=self.name,
            expected_steps=8,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        metric_label = validation_info.get("metric_label", "")
        book_a = validation_info.get("book_a", "")
        book_b = validation_info.get("book_b", "")
        return f"""Task-Specific Rules (Open Library Book Comparison):
- Compare: "{book_a}" vs "{book_b}"
- Metric: {metric_label}
- Score 1.0: Correct winning book title
- Score 0.5: N/A
- Score 0.0: Wrong book or no answer
- Tie rule: if equal metric values, alphabetically earlier title is correct"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        collected = get_collected_data()
        if not collected:
            return GroundTruthResult.fail("No Open Library data collected")

        metric = validation_info.get("metric")
        book_a = validation_info.get("book_a")
        book_b = validation_info.get("book_b")
        if not isinstance(metric, str) or not isinstance(book_a, str) or not isinstance(book_b, str):
            return GroundTruthResult.fail("Missing or invalid comparison inputs")

        value_a = self._find_metric_for_book(collected, book_a, metric)
        value_b = self._find_metric_for_book(collected, book_b, metric)

        if value_a is None or value_b is None:
            missing = []
            if value_a is None:
                missing.append(f"'{book_a}'")
            if value_b is None:
                missing.append(f"'{book_b}'")
            return GroundTruthResult.not_collected(
                f"Missing metric '{metric}' for {', '.join(missing)} in collected pages"
            )

        if value_a == value_b:
            winner = min(book_a, book_b, key=lambda title: title.casefold())
        else:
            winner = book_a if value_a > value_b else book_b
        return GroundTruthResult.ok(winner)

    @staticmethod
    def _find_metric_for_book(
        collected: Dict[str, Dict[str, Any]],
        title: str,
        metric: str,
    ) -> Optional[float]:
        latest_value: Optional[float] = None
        latest_match_quality = 0

        for work in iter_collected_works(collected):
            work_title = work.get("title")
            if not isinstance(work_title, str) or not titles_match(title, work_title):
                continue

            value = parse_numeric(work.get(metric))
            if value is None:
                continue

            # Exact normalized title match is preferred over substring match.
            quality = 2 if normalize_text(title) == normalize_text(work_title) else 1
            if quality >= latest_match_quality:
                latest_match_quality = quality
                latest_value = value

        return latest_value

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any],
    ) -> ValidationResult:
        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=None,
            actual=answer,
            details="Use LLM validation",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> TriggerConfig:
        trigger = UrlPatternTrigger(domains=["openlibrary.org"])
        return TriggerConfig(trigger=trigger)

    @classmethod
    def get_cache_source(cls) -> str:
        return "openlibrary"

    def get_gt_source(self) -> GTSourceType:
        return self.GT_SOURCE
