"""Book stats template for Open Library - EASY DIFFICULTY

RL-friendly design:
- Requires searching for a book and navigating to its page
- Dynamic data: edition counts grow, ratings change, read counts increase
- Large entity pool: thousands of well-known books across genres
- Combinatorial question space: book × metric prevents memorization
"""

import random
from enum import Enum
from typing import Any, Dict, Optional

from .common import titles_match
from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, TriggerConfig, GroundTruthResult,
)
from liveweb_arena.core.gt_collector import GTSourceType, get_current_gt_collector


class BookMetric(Enum):
    """Metrics that can be queried for a book."""
    EDITION_COUNT = ("edition_count", "number of editions")
    RATINGS_COUNT = ("ratings_count", "number of ratings")
    WANT_TO_READ = ("want_to_read_count", "number of people who want to read it")
    ALREADY_READ = ("already_read_count", "number of people who have already read it")


# Books with reliable data - title, search query, work key for verification
# Chosen for high edition counts and active reader engagement (dynamic metrics)
BOOK_POOL = [
    ("Fahrenheit 451", "fahrenheit 451"),
    ("1984", "1984 orwell"),
    ("Brave New World", "brave new world"),
    ("The Great Gatsby", "the great gatsby"),
    ("To Kill a Mockingbird", "to kill a mockingbird"),
    ("Pride and Prejudice", "pride and prejudice"),
    ("The Catcher in the Rye", "catcher in the rye"),
    ("Lord of the Flies", "lord of the flies"),
    ("Animal Farm", "animal farm orwell"),
    ("Jane Eyre", "jane eyre"),
    ("Wuthering Heights", "wuthering heights"),
    ("Dracula", "dracula stoker"),
    ("Frankenstein", "frankenstein shelley"),
    ("The Hobbit", "the hobbit"),
    ("Dune", "dune herbert"),
    ("Slaughterhouse-Five", "slaughterhouse five"),
    ("The Hitchhiker's Guide to the Galaxy", "hitchhikers guide galaxy"),
    ("Catch-22", "catch 22 heller"),
    ("One Hundred Years of Solitude", "one hundred years solitude"),
    ("The Road", "the road mccarthy"),
    ("Gone Girl", "gone girl flynn"),
    ("The Martian", "the martian weir"),
    ("Project Hail Mary", "project hail mary"),
    ("Ender's Game", "enders game"),
    ("The Name of the Wind", "name of the wind"),
    ("Neuromancer", "neuromancer"),
    ("Do Androids Dream of Electric Sheep?", "do androids dream electric sheep"),
    ("The Left Hand of Darkness", "left hand of darkness"),
    ("Foundation", "foundation asimov"),
    ("Beloved", "beloved morrison"),
]

PATTERNS = {
    BookMetric.EDITION_COUNT: [
        "How many editions does \"{title}\" have on Open Library?",
        "What is the total number of editions of \"{title}\" listed on Open Library?",
        "On Open Library, how many editions are there for \"{title}\"?",
    ],
    BookMetric.RATINGS_COUNT: [
        "How many ratings does \"{title}\" have on Open Library?",
        "What is the total number of ratings for \"{title}\" on Open Library?",
        "On Open Library, how many users have rated \"{title}\"?",
    ],
    BookMetric.WANT_TO_READ: [
        "How many people want to read \"{title}\" on Open Library?",
        "What is the \"Want to Read\" count for \"{title}\" on Open Library?",
        "On Open Library, how many users have marked \"{title}\" as want to read?",
    ],
    BookMetric.ALREADY_READ: [
        "How many people have already read \"{title}\" on Open Library?",
        "What is the \"Already Read\" count for \"{title}\" on Open Library?",
        "On Open Library, how many users have marked \"{title}\" as already read?",
    ],
}


@register_template("openlibrary_book_stats")
class OpenLibraryBookStatsTemplate(QuestionTemplate):
    """
    Template for single-book stat queries on Open Library.

    EASY difficulty: Navigate to a book page and read a single metric.

    RL value:
    - Search interaction: Must type query and select correct result
    - Dynamic data: Edition counts and read counts change over time
    - Large entity pool: 30 books × 4 metrics = 120 question variants
    - All metrics are dynamic (no static facts like publication year)
    """

    GT_SOURCE = GTSourceType.PAGE_ONLY

    def __init__(self):
        super().__init__("openlibrary_book_stats")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        metrics = list(BookMetric)
        if variant is not None:
            metric = metrics[variant % len(metrics)]
        else:
            metric = rng.choice(metrics)

        title, search_query = rng.choice(BOOK_POOL)

        pattern = rng.choice(PATTERNS[metric])
        question_text = pattern.format(title=title)

        start_url = f"https://openlibrary.org/search?q={search_query.replace(' ', '+')}"

        validation_info = {
            "metric": metric.value[0],
            "metric_label": metric.value[1],
            "book_title": title,
            "search_query": search_query,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=start_url,
            variables={"metric": metric, "title": title},
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=5,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        metric_label = validation_info.get("metric_label", "")
        title = validation_info.get("book_title", "")
        return f"""Task-Specific Rules (Open Library Book Stats):
- Book: "{title}"
- Metric: {metric_label}
- Score 1.0: Within ±5% of correct value
- Score 0.5: Within ±15% of correct value
- Score 0.0: Wrong value or no answer
- Data is on the book's Open Library page (search → click book)"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        metric = validation_info.get("metric", "")
        title = validation_info.get("book_title", "")

        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.system_error("No GT collector")

        collected = gt_collector.get_collected_api_data()
        if not collected:
            return GroundTruthResult.fail("No Open Library data collected")

        # Search collected data for the target book
        for _url_key, data in collected.items():
            if not isinstance(data, dict):
                continue

            # Check search results (contains "works" dict)
            works = data.get("works")
            if isinstance(works, dict):
                for _work_key, work in works.items():
                    if not isinstance(work, dict):
                        continue
                    work_title = work.get("title", "")
                    if titles_match(title, work_title):
                        value = work.get(metric)
                        if value is not None:
                            return GroundTruthResult.ok(str(value))

            # Check direct work data (from work detail page)
            work_title = data.get("title", "")
            if titles_match(title, work_title):
                value = data.get(metric)
                if value is not None:
                    return GroundTruthResult.ok(str(value))

        return GroundTruthResult.not_collected(
            f"Book '{title}' not found in collected data. "
            f"Agent needs to search and visit the book page."
        )

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Not used — the pipeline uses LLM-based validation via get_validation_rules()."""
        return ValidationResult(
            score=0.0, is_correct=False, expected=None, actual=answer,
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
