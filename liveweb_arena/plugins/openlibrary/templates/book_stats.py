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
    # --- Original pool (30) ---
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
    # --- Classic Literature (25) ---
    ("War and Peace", "war and peace tolstoy"),
    ("Crime and Punishment", "crime and punishment dostoevsky"),
    ("Great Expectations", "great expectations dickens"),
    ("A Tale of Two Cities", "a tale of two cities dickens"),
    ("Moby Dick", "moby dick melville"),
    ("The Odyssey", "the odyssey homer"),
    ("Don Quixote", "don quixote cervantes"),
    ("Les Miserables", "les miserables hugo"),
    ("Anna Karenina", "anna karenina tolstoy"),
    ("The Brothers Karamazov", "the brothers karamazov"),
    ("Oliver Twist", "oliver twist dickens"),
    ("David Copperfield", "david copperfield dickens"),
    ("The Count of Monte Cristo", "the count of monte cristo"),
    ("Madame Bovary", "madame bovary flaubert"),
    ("The Iliad", "the iliad homer"),
    ("Ulysses", "ulysses joyce"),
    ("The Metamorphosis", "the metamorphosis kafka"),
    ("Heart of Darkness", "heart of darkness conrad"),
    ("The Picture of Dorian Gray", "the picture of dorian gray"),
    ("The Scarlet Letter", "the scarlet letter hawthorne"),
    ("The Adventures of Tom Sawyer", "the adventures of tom sawyer"),
    ("Robinson Crusoe", "robinson crusoe defoe"),
    ("Gulliver's Travels", "gullivers travels swift"),
    ("The Canterbury Tales", "the canterbury tales chaucer"),
    ("Paradise Lost", "paradise lost milton"),
    # --- Modern Fiction (25) ---
    ("The Kite Runner", "the kite runner"),
    ("Life of Pi", "life of pi martel"),
    ("The Girl with the Dragon Tattoo", "the girl with the dragon tattoo"),
    ("The Da Vinci Code", "the da vinci code"),
    ("The Alchemist", "the alchemist coelho"),
    ("Memoirs of a Geisha", "memoirs of a geisha"),
    ("The Book Thief", "the book thief"),
    ("Water for Elephants", "water for elephants"),
    ("A Thousand Splendid Suns", "a thousand splendid suns"),
    ("Never Let Me Go", "never let me go ishiguro"),
    ("Atonement", "atonement mcewan"),
    ("Cloud Atlas", "cloud atlas mitchell"),
    ("The Name of the Rose", "the name of the rose eco"),
    ("One Flew Over the Cuckoo's Nest", "one flew over the cuckoos nest"),
    ("Cat's Cradle", "cats cradle vonnegut"),
    ("The Bell Jar", "the bell jar plath"),
    ("On the Road", "on the road kerouac"),
    ("A Clockwork Orange", "a clockwork orange"),
    ("Lord of the Rings", "lord of the rings tolkien"),
    ("The Handmaid's Tale", "the handmaids tale"),
    ("The Color Purple", "the color purple walker"),
    ("The Secret Garden", "the secret garden"),
    ("Little Women", "little women alcott"),
    ("The Grapes of Wrath", "the grapes of wrath steinbeck"),
    ("Of Mice and Men", "of mice and men steinbeck"),
    # --- Science Fiction / Fantasy (25) ---
    ("2001: A Space Odyssey", "2001 a space odyssey clarke"),
    ("Ringworld", "ringworld niven"),
    ("Snow Crash", "snow crash stephenson"),
    ("The Dispossessed", "the dispossessed le guin"),
    ("Rendezvous with Rama", "rendezvous with rama clarke"),
    ("The Martian Chronicles", "the martian chronicles bradbury"),
    ("Childhood's End", "childhoods end clarke"),
    ("I, Robot", "i robot asimov"),
    ("The War of the Worlds", "the war of the worlds wells"),
    ("The Time Machine", "the time machine wells"),
    ("Twenty Thousand Leagues Under the Sea", "twenty thousand leagues under the sea"),
    ("A Wizard of Earthsea", "a wizard of earthsea le guin"),
    ("American Gods", "american gods gaiman"),
    ("Good Omens", "good omens pratchett gaiman"),
    ("Coraline", "coraline gaiman"),
    ("The Colour of Magic", "the colour of magic pratchett"),
    ("Guards! Guards!", "guards guards pratchett"),
    ("Small Gods", "small gods pratchett"),
    ("Stardust", "stardust gaiman"),
    ("The Ocean at the End of the Lane", "the ocean at the end of the lane"),
    ("The Caves of Steel", "the caves of steel asimov"),
    ("Hyperion", "hyperion simmons"),
    ("The Forever War", "the forever war haldeman"),
    ("The Sirens of Titan", "the sirens of titan vonnegut"),
    ("Contact", "contact carl sagan"),
    # --- Non-fiction (25) ---
    ("Sapiens", "sapiens harari"),
    ("A Brief History of Time", "a brief history of time hawking"),
    ("The Art of War", "the art of war sun tzu"),
    ("Guns, Germs, and Steel", "guns germs and steel"),
    ("Thinking, Fast and Slow", "thinking fast and slow kahneman"),
    ("The Selfish Gene", "the selfish gene dawkins"),
    ("Silent Spring", "silent spring carson"),
    ("The Origin of Species", "the origin of species darwin"),
    ("The Prince", "the prince machiavelli"),
    ("Freakonomics", "freakonomics"),
    ("Outliers", "outliers gladwell"),
    ("Quiet", "quiet susan cain"),
    ("Educated", "educated tara westover"),
    ("The Diary of a Young Girl", "the diary of a young girl anne frank"),
    ("Night", "night elie wiesel"),
    ("Man's Search for Meaning", "mans search for meaning frankl"),
    ("The Elements of Style", "the elements of style strunk"),
    ("A Room of One's Own", "a room of ones own woolf"),
    ("The Communist Manifesto", "the communist manifesto marx"),
    ("Walden", "walden thoreau"),
    ("The Republic", "the republic plato"),
    ("Meditations", "meditations marcus aurelius"),
    ("The Wealth of Nations", "the wealth of nations smith"),
    ("Common Sense", "common sense thomas paine"),
    ("I Know Why the Caged Bird Sings", "i know why the caged bird sings"),
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
    - Large entity pool: 130 books × 4 metrics = 520 question variants
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
