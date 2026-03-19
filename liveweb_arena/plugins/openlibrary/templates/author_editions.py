"""Author editions aggregation template for Open Library - MEDIUM DIFFICULTY."""

import random
import re
from typing import Any, Dict, Optional
from urllib.parse import quote_plus

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
from .common import get_collected_data, parse_numeric

AUTHOR_POOL = [
    # --- Original pool (20) ---
    ("Charles Dickens", "charles dickens"),
    ("Jane Austen", "jane austen"),
    ("William Shakespeare", "william shakespeare"),
    ("Mark Twain", "mark twain"),
    ("Oscar Wilde", "oscar wilde"),
    ("Edgar Allan Poe", "edgar allan poe"),
    ("Virginia Woolf", "virginia woolf"),
    ("George Orwell", "george orwell"),
    ("Agatha Christie", "agatha christie"),
    ("Ernest Hemingway", "ernest hemingway"),
    ("Jules Verne", "jules verne"),
    ("H. G. Wells", "h g wells"),
    ("Arthur Conan Doyle", "arthur conan doyle"),
    ("Mary Shelley", "mary shelley"),
    ("Franz Kafka", "franz kafka"),
    ("Herman Melville", "herman melville"),
    ("Victor Hugo", "victor hugo"),
    ("Emily Bronte", "emily bronte"),
    ("Miguel de Cervantes", "miguel de cervantes"),
    ("Alexandre Dumas", "alexandre dumas"),
    # --- Expanded pool (50) ---
    ("Leo Tolstoy", "leo tolstoy"),
    ("Fyodor Dostoevsky", "fyodor dostoevsky"),
    ("Thomas Hardy", "thomas hardy"),
    ("Rudyard Kipling", "rudyard kipling"),
    ("Robert Louis Stevenson", "robert louis stevenson"),
    ("Louisa May Alcott", "louisa may alcott"),
    ("Nathaniel Hawthorne", "nathaniel hawthorne"),
    ("Walt Whitman", "walt whitman"),
    ("Henry James", "henry james"),
    ("Joseph Conrad", "joseph conrad"),
    ("Stephen King", "stephen king"),
    ("J.K. Rowling", "j k rowling"),
    ("Roald Dahl", "roald dahl"),
    ("Philip K. Dick", "philip k dick"),
    ("Isaac Asimov", "isaac asimov"),
    ("Ray Bradbury", "ray bradbury"),
    ("Kurt Vonnegut", "kurt vonnegut"),
    ("Toni Morrison", "toni morrison"),
    ("Gabriel Garcia Marquez", "gabriel garcia marquez"),
    ("Haruki Murakami", "haruki murakami"),
    ("F. Scott Fitzgerald", "f scott fitzgerald"),
    ("James Joyce", "james joyce"),
    ("Albert Camus", "albert camus"),
    ("Aldous Huxley", "aldous huxley"),
    ("George Bernard Shaw", "george bernard shaw"),
    ("Anton Chekhov", "anton chekhov"),
    ("Henrik Ibsen", "henrik ibsen"),
    ("Tennessee Williams", "tennessee williams"),
    ("Samuel Beckett", "samuel beckett"),
    ("P.G. Wodehouse", "p g wodehouse"),
    ("Chinua Achebe", "chinua achebe"),
    ("Salman Rushdie", "salman rushdie"),
    ("Ursula K. Le Guin", "ursula k le guin"),
    ("Philip Pullman", "philip pullman"),
    ("Neil Gaiman", "neil gaiman"),
    ("Terry Pratchett", "terry pratchett"),
    ("Margaret Atwood", "margaret atwood"),
    ("Octavia Butler", "octavia butler"),
    ("Kazuo Ishiguro", "kazuo ishiguro"),
    ("John Steinbeck", "john steinbeck"),
    ("William Faulkner", "william faulkner"),
    ("Ralph Waldo Emerson", "ralph waldo emerson"),
    ("Emily Dickinson", "emily dickinson"),
    ("Rabindranath Tagore", "rabindranath tagore"),
    ("Jorge Luis Borges", "jorge luis borges"),
    ("Italo Calvino", "italo calvino"),
    ("Umberto Eco", "umberto eco"),
    ("Paulo Coelho", "paulo coelho"),
    ("Isabel Allende", "isabel allende"),
    ("Chimamanda Ngozi Adichie", "chimamanda ngozi adichie"),
]

RESULT_COUNTS = [3, 5, 7, 10]

SORT_OPTIONS = [
    ("editions", "most editions"),
    ("new", "newest first"),
]

PATTERNS = [
    (
        'Search Open Library for books by "{author}" sorted by {sort_label}. '
        "What is the total number of editions across the top {n} results?"
    ),
    (
        'On Open Library, look up books by "{author}" ({sort_label}). '
        "Among the first {n} results, what is the combined edition count?"
    ),
    (
        'Find books by "{author}" on Open Library ({sort_label}). '
        "Among the first {n} results, what is the combined edition count?"
    ),
]


@register_template("openlibrary_author_editions")
class OpenLibraryAuthorEditionsTemplate(QuestionTemplate):
    """Aggregate edition counts across top author search results."""

    GT_SOURCE = GTSourceType.PAGE_ONLY

    def __init__(self):
        super().__init__("openlibrary_author_editions")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)
        author_name, author_query = rng.choice(AUTHOR_POOL)
        count = RESULT_COUNTS[variant % len(RESULT_COUNTS)] if variant is not None else rng.choice(RESULT_COUNTS)
        sort_key, sort_label = rng.choice(SORT_OPTIONS)
        search_query = f'author:"{author_query}"'

        pattern = rng.choice(PATTERNS)
        question_text = pattern.format(
            author=author_name, n=count, sort_label=sort_label,
        )
        query_encoded = quote_plus(search_query)
        start_url = f"https://openlibrary.org/search?q={query_encoded}&sort={sort_key}"

        return GeneratedQuestion(
            question_text=question_text,
            start_url=start_url,
            variables={
                "author": author_name,
                "work_count": count,
                "sort": sort_key,
            },
            validation_info={
                "author_name": author_name,
                "author_query": author_query,
                "search_query": search_query,
                "sort": sort_key,
                "work_count": count,
            },
            template_name=self.name,
            expected_steps=7,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        author = validation_info.get("author_name", "")
        count = validation_info.get("work_count", "")
        return f"""Task-Specific Rules (Open Library Author Editions):
- Author query: "{author}"
- Sum target: first {count} results sorted by editions
- Score 1.0: Exact summed edition count
- Score 0.5: Within ±1 of correct total
- Score 0.0: Wrong total or no answer"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        collected = get_collected_data()
        if not collected:
            return GroundTruthResult.fail("No Open Library data collected")

        author_name = validation_info.get("author_name")
        author_query = validation_info.get("author_query")
        search_query = validation_info.get("search_query")
        sort = validation_info.get("sort")
        work_count = validation_info.get("work_count")
        if (
            not isinstance(author_name, str)
            or not isinstance(author_query, str)
            or (search_query is not None and not isinstance(search_query, str))
            or not isinstance(sort, str)
            or not isinstance(work_count, int)
        ):
            return GroundTruthResult.fail("Missing or invalid author aggregation inputs")
        if work_count <= 0:
            return GroundTruthResult.fail(f"Invalid work_count: {work_count}")

        if not search_query:
            search_query = f'author:"{author_query}"'

        data = self._find_author_search_entry(
            collected, search_query=search_query, sort=sort,
        )
        if data is None:
            ol_keys = [k for k in collected if k.startswith("ol:")][:5]
            return GroundTruthResult.not_collected(
                f"Did not collect search data for author '{author_name}' "
                f"sorted by '{sort}'. Collected OL keys: {ol_keys}"
            )

        works_dict = data.get("works")
        if not isinstance(works_dict, dict):
            return GroundTruthResult.fail("Collected search data missing works dictionary")
        if len(works_dict) < work_count:
            return GroundTruthResult.fail(
                f"Only {len(works_dict)} works collected for '{author_query}', need {work_count}"
            )

        ranked_works = []
        for work in works_dict.values():
            rank = work.get("rank")
            if not isinstance(rank, int):
                return GroundTruthResult.fail("Encountered work without integer rank")
            ranked_works.append(work)
        ranked_works.sort(key=lambda work: work["rank"])
        ranked_works = ranked_works[:work_count]

        total_editions = 0
        for work in ranked_works:
            title = work.get("title", "<unknown>")
            edition_count = parse_numeric(work.get("edition_count"))
            if edition_count is None:
                return GroundTruthResult.fail(f"Missing edition_count for work '{title}'")
            total_editions += int(edition_count)

        return GroundTruthResult.ok(str(total_editions))

    @staticmethod
    def _normalize_author_fragment(value: str) -> str:
        """Normalize author text by stripping punctuation and collapsing whitespace."""
        return " ".join(re.findall(r"[a-z0-9]+", value.lower()))

    @classmethod
    def _extract_author_filter(cls, query: str) -> Optional[str]:
        """
        Extract normalized author text from author-filter queries.

        Accepts query forms like:
        - author:"mark twain"
        - AUTHOR: "Mark Twain"
        - author:'h.g. wells'
        """
        cleaned = query.strip().lower()
        if not cleaned:
            return None

        match = re.match(r"^author\s*:\s*(.+)$", cleaned)
        if not match:
            return None

        rhs = match.group(1).strip()
        if len(rhs) >= 2 and rhs[0] == rhs[-1] and rhs[0] in {'"', "'"}:
            rhs = rhs[1:-1].strip()

        normalized = cls._normalize_author_fragment(rhs)
        return normalized or None

    @classmethod
    def _find_author_search_entry(
        cls,
        collected: Dict[str, Dict[str, Any]],
        *,
        search_query: str,
        sort: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Find search data for an author-filtered search query.

        We intentionally require author-filter syntax to keep page semantics
        aligned with the question ("books by <author>").
        """
        target_author = cls._extract_author_filter(search_query)
        if not target_author:
            return None

        matched_entry: Optional[Dict[str, Any]] = None

        for key, entry in collected.items():
            if not key.startswith("ol:") or not isinstance(entry, dict):
                continue
            works = entry.get("works")
            if not isinstance(works, dict):
                continue
            if entry.get("sort") != sort:
                continue

            entry_query = str(entry.get("query", ""))
            if not entry_query.strip():
                continue

            entry_author = cls._extract_author_filter(entry_query)
            if entry_author == target_author:
                matched_entry = entry

        return matched_entry

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
