"""Movie collection template for TMDB - MEDIUM-HARD DIFFICULTY"""

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
)
from ..api_client import TMDBClient


@dataclass
class CollectionMovieSpec:
    """Movie that belongs to a collection"""
    movie_id: str
    title: str
    collection_id: str
    collection_name: str


class CollectionMovieVariable:
    """Variable for movies that belong to collections"""

    # Movies that are part of well-known collections/franchises
    MOVIES: List[CollectionMovieSpec] = [
        # Marvel Cinematic Universe
        CollectionMovieSpec("299536", "Avengers: Infinity War", "86311", "The Avengers Collection"),
        CollectionMovieSpec("299534", "Avengers: Endgame", "86311", "The Avengers Collection"),
        CollectionMovieSpec("24428", "The Avengers", "86311", "The Avengers Collection"),
        # Fast & Furious
        CollectionMovieSpec("385687", "Fast X", "9485", "The Fast and the Furious Collection"),
        # Toy Story
        CollectionMovieSpec("862", "Toy Story", "10194", "Toy Story Collection"),
        # The Godfather
        CollectionMovieSpec("238", "The Godfather", "230", "The Godfather Collection"),
        CollectionMovieSpec("240", "The Godfather Part II", "230", "The Godfather Collection"),
        # Lord of the Rings
        CollectionMovieSpec("120", "The Lord of the Rings: The Fellowship of the Ring", "119", "The Lord of the Rings Collection"),
        CollectionMovieSpec("121", "The Lord of the Rings: The Two Towers", "119", "The Lord of the Rings Collection"),
        CollectionMovieSpec("122", "The Lord of the Rings: The Return of the King", "119", "The Lord of the Rings Collection"),
        # The Dark Knight
        CollectionMovieSpec("155", "The Dark Knight", "263", "The Dark Knight Collection"),
        # Back to the Future
        CollectionMovieSpec("105", "Back to the Future", "264", "Back to the Future Collection"),
        # Spider-Man (MCU)
        CollectionMovieSpec("569094", "Spider-Man: Across the Spider-Verse", "573436", "Spider-Man: Spider-Verse Collection"),
        CollectionMovieSpec("324857", "Spider-Man: Into the Spider-Verse", "573436", "Spider-Man: Spider-Verse Collection"),
        # John Wick
        CollectionMovieSpec("603692", "John Wick: Chapter 4", "404609", "John Wick Collection"),
        # Guardians of the Galaxy
        CollectionMovieSpec("118340", "Guardians of the Galaxy", "284433", "Guardians of the Galaxy Collection"),
        CollectionMovieSpec("447365", "Guardians of the Galaxy Vol. 3", "284433", "Guardians of the Galaxy Collection"),
        # Jurassic Park/World
        CollectionMovieSpec("329", "Jurassic Park", "328", "Jurassic Park Collection"),
        # Pirates of the Caribbean
        CollectionMovieSpec("22", "Pirates of the Caribbean: The Curse of the Black Pearl", "295", "Pirates of the Caribbean Collection"),
        # Harry Potter
        CollectionMovieSpec("671", "Harry Potter and the Philosopher's Stone", "1241", "Harry Potter Collection"),
        # Mission Impossible
        CollectionMovieSpec("575264", "Mission: Impossible - Dead Reckoning Part One", "87359", "Mission: Impossible Collection"),
        # Transformers
        CollectionMovieSpec("667538", "Transformers: Rise of the Beasts", "8650", "Transformers Collection"),
        # Indiana Jones
        CollectionMovieSpec("89", "Indiana Jones and the Last Crusade", "84", "Indiana Jones Collection"),
        # The Matrix
        CollectionMovieSpec("603", "The Matrix", "2344", "The Matrix Collection"),
        # Alien
        CollectionMovieSpec("348", "Alien", "8091", "Alien Collection"),
        # Terminator
        CollectionMovieSpec("280", "Terminator 2: Judgment Day", "528", "The Terminator Collection"),
    ]

    def sample(self, rng: random.Random) -> CollectionMovieSpec:
        """Sample a movie from a collection."""
        return rng.choice(self.MOVIES)


class CollectionQueryType:
    """Types of collection queries"""
    MOVIE_COUNT = "movie_count"        # How many movies in collection?
    FIRST_MOVIE = "first_movie"        # First movie in collection
    LATEST_MOVIE = "latest_movie"      # Latest movie in collection
    COLLECTION_NAME = "collection_name"  # What collection is this movie part of?


@register_template("tmdb_movie_collection")
class TMDBMovieCollectionTemplate(QuestionTemplate):
    """
    Template for movie collection/franchise queries - MEDIUM-HARD DIFFICULTY.

    Requires navigating to a movie page, finding its collection,
    and extracting information about the collection.

    Examples:
    - How many movies are in The Avengers Collection?
    - What was the first movie in the Back to the Future Collection?
    - What collection does The Dark Knight belong to?
    - What is the latest movie in the Harry Potter Collection?
    """

    COUNT_PATTERNS = [
        "How many movies are in the {collection} on TMDB?",
        "How many films are part of the {collection}?",
        "What is the total number of movies in the {collection}?",
    ]

    FIRST_PATTERNS = [
        "What was the first movie in the {collection}?",
        "Which movie started the {collection}?",
        "What is the earliest film in the {collection}?",
    ]

    LATEST_PATTERNS = [
        "What is the latest movie in the {collection}?",
        "What is the most recent film in the {collection}?",
        "Which movie is the newest in the {collection}?",
    ]

    COLLECTION_NAME_PATTERNS = [
        "What collection does {movie} belong to on TMDB?",
        "Which film series is {movie} part of?",
        "{movie} is part of which collection on TMDB?",
    ]

    QUERY_TYPES = [
        CollectionQueryType.MOVIE_COUNT,
        CollectionQueryType.FIRST_MOVIE,
        CollectionQueryType.LATEST_MOVIE,
        CollectionQueryType.COLLECTION_NAME,
    ]

    def __init__(self):
        super().__init__("tmdb_movie_collection")
        self._movie_var = CollectionMovieVariable()

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a collection question."""
        rng = random.Random(seed)

        movie = self._movie_var.sample(rng)

        if variant is not None:
            query_type = self.QUERY_TYPES[variant % len(self.QUERY_TYPES)]
        else:
            query_type = rng.choice(self.QUERY_TYPES)

        question_text = self._build_question(movie, query_type, rng)

        # Start at the movie page - agent needs to find collection
        start_url = f"https://www.themoviedb.org/movie/{movie.movie_id}"

        validation_info = {
            "movie_id": movie.movie_id,
            "movie_title": movie.title,
            "collection_id": movie.collection_id,
            "collection_name": movie.collection_name,
            "query_type": query_type,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=start_url,
            variables={"movie": movie, "query_type": query_type},
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=12,  # Search + navigate + find collection info
        )

    def _build_question(
        self,
        movie: CollectionMovieSpec,
        query_type: str,
        rng: random.Random,
    ) -> str:
        """Build question text based on query type."""
        if query_type == CollectionQueryType.MOVIE_COUNT:
            pattern = rng.choice(self.COUNT_PATTERNS)
            return pattern.format(collection=movie.collection_name)
        elif query_type == CollectionQueryType.FIRST_MOVIE:
            pattern = rng.choice(self.FIRST_PATTERNS)
            return pattern.format(collection=movie.collection_name)
        elif query_type == CollectionQueryType.LATEST_MOVIE:
            pattern = rng.choice(self.LATEST_PATTERNS)
            return pattern.format(collection=movie.collection_name)
        else:  # COLLECTION_NAME
            pattern = rng.choice(self.COLLECTION_NAME_PATTERNS)
            return pattern.format(movie=movie.title)

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        """Get validation rules for collection query."""
        query_type = validation_info.get("query_type", CollectionQueryType.MOVIE_COUNT)
        collection_name = validation_info.get("collection_name", "the collection")

        if query_type == CollectionQueryType.MOVIE_COUNT:
            return f"""Task-Specific Rules (TMDB - Collection Movie Count):
- Count the number of movies in {collection_name}
- Score 1.0: Exact count match
- Score 0.5: Off by 1 (collections may update)
- Score 0.0: Count differs by more than 1
- Only count movies, not TV specials"""

        if query_type == CollectionQueryType.FIRST_MOVIE:
            return f"""Task-Specific Rules (TMDB - First Movie in Collection):
- Find the earliest released movie in {collection_name}
- Score 1.0: Correct movie title (case insensitive)
- Score 0.0: Wrong movie
- Determined by release date"""

        if query_type == CollectionQueryType.LATEST_MOVIE:
            return f"""Task-Specific Rules (TMDB - Latest Movie in Collection):
- Find the most recently released movie in {collection_name}
- Score 1.0: Correct movie title (case insensitive)
- Score 0.5: A recent movie but not the absolute latest
- Score 0.0: Wrong or old movie"""

        return f"""Task-Specific Rules (TMDB - Collection Name):
- Identify which collection the movie belongs to
- Score 1.0: Correct collection name (partial match OK)
- Score 0.0: Wrong collection or no collection identified
- The collection name should match TMDB's official collection name"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Fetch collection data from TMDB API."""
        collection_id = validation_info.get("collection_id", "")
        query_type = validation_info.get("query_type", CollectionQueryType.MOVIE_COUNT)
        collection_name = validation_info.get("collection_name", "")

        if not collection_id:
            return GroundTruthResult.fail("No collection_id provided")

        # For COLLECTION_NAME query, we just return the stored collection name
        if query_type == CollectionQueryType.COLLECTION_NAME:
            if collection_name:
                return GroundTruthResult.ok(collection_name)
            return GroundTruthResult.fail("No collection name available")

        try:
            # Fetch collection details
            data = await TMDBClient.get(f"/collection/{collection_id}")
            if not data:
                return GroundTruthResult.retry("No data returned from TMDB API")

            parts = data.get("parts", [])
            if not parts:
                return GroundTruthResult.fail("No movies found in collection")

            # Sort by release date
            movies_with_dates = [
                m for m in parts
                if m.get("release_date")
            ]
            movies_sorted = sorted(
                movies_with_dates,
                key=lambda m: m.get("release_date", ""),
            )

            if query_type == CollectionQueryType.MOVIE_COUNT:
                return GroundTruthResult.ok(str(len(parts)))

            elif query_type == CollectionQueryType.FIRST_MOVIE:
                if movies_sorted:
                    first = movies_sorted[0]
                    return GroundTruthResult.ok(
                        f"{first.get('title')} ({first.get('release_date', '')[:4]})"
                    )
                return GroundTruthResult.fail("Could not determine first movie")

            elif query_type == CollectionQueryType.LATEST_MOVIE:
                # Filter to only released movies
                from datetime import datetime
                today = datetime.now().strftime("%Y-%m-%d")
                released = [
                    m for m in movies_sorted
                    if m.get("release_date", "") <= today
                ]
                if released:
                    latest = released[-1]
                    return GroundTruthResult.ok(
                        f"{latest.get('title')} ({latest.get('release_date', '')[:4]})"
                    )
                return GroundTruthResult.fail("Could not determine latest movie")

            return GroundTruthResult.fail(f"Unknown query type: {query_type}")

        except Exception as e:
            return GroundTruthResult.retry(f"TMDB API error: {e}")

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate collection answer."""
        result = await self.get_ground_truth(validation_info)

        if not result.success:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        ground_truth = result.value
        query_type = validation_info.get("query_type", CollectionQueryType.MOVIE_COUNT)

        if query_type == CollectionQueryType.MOVIE_COUNT:
            return self._validate_count(answer, ground_truth)
        elif query_type == CollectionQueryType.COLLECTION_NAME:
            return self._validate_collection_name(answer, ground_truth)
        else:
            return self._validate_movie_title(answer, ground_truth)

    def _validate_count(self, answer: str, expected: str) -> ValidationResult:
        """Validate movie count answer."""
        import re

        try:
            exp_count = int(expected)
        except ValueError:
            return ValidationResult(
                score=0.0, is_correct=False, expected=expected,
                actual=answer, details="Could not parse expected count",
            )

        # Find number in answer
        num_match = re.search(r"\b(\d+)\b", answer)
        if not num_match:
            return ValidationResult(
                score=0.0, is_correct=False, expected=expected,
                actual=answer, details="Could not find a number in answer",
            )

        ans_count = int(num_match.group(1))
        diff = abs(ans_count - exp_count)

        if diff == 0:
            return ValidationResult(
                score=1.0, is_correct=True, expected=expected,
                actual=answer, details="Exact count match",
            )
        elif diff == 1:
            return ValidationResult(
                score=0.5, is_correct=False, expected=expected,
                actual=answer, details="Count off by 1 (collection may have updated)",
            )
        else:
            return ValidationResult(
                score=0.0, is_correct=False, expected=expected,
                actual=answer, details=f"Count off by {diff}",
            )

    def _validate_movie_title(self, answer: str, expected: str) -> ValidationResult:
        """Validate movie title answer."""
        import re

        answer_lower = answer.lower().strip()

        # Expected format: "Movie Title (YYYY)"
        match = re.match(r"(.+?)\s*\((\d{4})\)", expected)
        if match:
            exp_title = match.group(1).lower()
            exp_year = match.group(2)
        else:
            exp_title = expected.lower()
            exp_year = None

        # Check if title appears in answer
        if exp_title in answer_lower:
            return ValidationResult(
                score=1.0, is_correct=True, expected=expected,
                actual=answer, details="Movie title matches",
            )

        # Check for significant title words
        title_words = [w for w in exp_title.split() if len(w) > 3]
        if title_words:
            matches = sum(1 for w in title_words if w in answer_lower)
            if matches >= len(title_words) * 0.6:
                return ValidationResult(
                    score=1.0, is_correct=True, expected=expected,
                    actual=answer, details="Most title words match",
                )

        # Check year for partial credit
        if exp_year and exp_year in answer:
            return ValidationResult(
                score=0.3, is_correct=False, expected=expected,
                actual=answer, details="Year matches but title doesn't",
            )

        return ValidationResult(
            score=0.0, is_correct=False, expected=expected,
            actual=answer, details="Movie title not found in answer",
        )

    def _validate_collection_name(self, answer: str, expected: str) -> ValidationResult:
        """Validate collection name answer."""
        answer_lower = answer.lower().strip()
        expected_lower = expected.lower()

        # Check full collection name
        if expected_lower in answer_lower:
            return ValidationResult(
                score=1.0, is_correct=True, expected=expected,
                actual=answer, details="Collection name matches",
            )

        # Check key words (e.g., "Avengers" from "The Avengers Collection")
        # Remove common words like "The", "Collection"
        key_words = [
            w for w in expected_lower.split()
            if w not in ["the", "collection", "a", "an"]
        ]
        if key_words:
            matches = sum(1 for w in key_words if w in answer_lower)
            if matches >= len(key_words) * 0.5:
                return ValidationResult(
                    score=1.0, is_correct=True, expected=expected,
                    actual=answer, details="Key collection words match",
                )

        return ValidationResult(
            score=0.0, is_correct=False, expected=expected,
            actual=answer, details="Collection name not found in answer",
        )

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> TriggerConfig:
        """Trigger when agent visits the collection page."""
        collection_id = validation_info.get("collection_id", "")
        movie_id = validation_info.get("movie_id", "")

        # Trigger on either collection page or movie page
        trigger = UrlPatternTrigger(
            domains=["themoviedb.org"],
            url_contains=f"/collection/{collection_id}" if collection_id else f"/movie/{movie_id}",
        )
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)
