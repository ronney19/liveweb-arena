"""Cast position template for TMDB - MEDIUM DIFFICULTY (Anti-memorization)"""

import random
from typing import Any, Dict, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
)
from .variables import MovieVariable, MovieSpec
from ..api_client import TMDBClient


def ordinal(n: int) -> str:
    """Convert number to ordinal string (1st, 2nd, 3rd, etc.)"""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


@register_template("tmdb_cast_position")
class TMDBCastPositionTemplate(QuestionTemplate):
    """
    Template for specific cast position queries - MEDIUM DIFFICULTY.

    These questions are hard to memorize because:
    1. Asking for 4th, 5th, 7th billed actors is uncommon knowledge
    2. The position numbers are randomly selected
    3. Requires actual browsing to find the answer

    Examples:
    - Who is the 4th billed actor in Inception?
    - Who is the 7th credited actor in The Avengers?
    - What is the billing position of [actor] in [movie]?
    """

    POSITION_PATTERNS = [
        "Who is the {position} billed actor in {movie}?",
        "Who is credited in the {position} position in {movie}'s cast?",
        "Name the {position} actor listed in {movie}'s credits.",
    ]

    # Positions 4-10 are rarely memorized
    POSITIONS = [4, 5, 6, 7, 8, 9, 10]

    def __init__(self):
        super().__init__("tmdb_cast_position")
        self._movie_var = MovieVariable()

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        movie = self._movie_var.sample(rng)

        if variant is not None:
            position = self.POSITIONS[variant % len(self.POSITIONS)]
        else:
            position = rng.choice(self.POSITIONS)

        question_text = self._build_question(movie, position, rng)
        start_url = f"https://www.themoviedb.org/movie/{movie.movie_id}"

        validation_info = {
            "movie_id": movie.movie_id,
            "movie_title": movie.title,
            "cast_position": position,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=start_url,
            variables={"movie": movie, "position": position},
            validation_info=validation_info,
            template_name=self.name,
            # Search movie (2-3) + navigate to page (1-2) + scroll/full cast (2-3) + count/extract (2) + submit (1)
            expected_steps=15,
        )

    def _build_question(
        self,
        movie: MovieSpec,
        position: int,
        rng: random.Random,
    ) -> str:
        pattern = rng.choice(self.POSITION_PATTERNS)
        return pattern.format(movie=movie.title, position=ordinal(position))

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        position = validation_info.get("cast_position", 5)
        movie = validation_info.get("movie_title", "the movie")

        return f"""Task-Specific Rules (TMDB - Cast Position):
- Find who is the {ordinal(position)} billed actor in {movie}
- Score 1.0: Correct actor name (case insensitive, partial name OK)
- Score 0.0: Wrong actor or position
- The position is based on TMDB's billing order
- Accept common name variations"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        movie_id = validation_info.get("movie_id", "")
        position = validation_info.get("cast_position", 5)

        if not movie_id:
            return GroundTruthResult.fail("No movie_id provided")

        try:
            data = await TMDBClient.get_movie_credits(movie_id)
            if not data:
                return GroundTruthResult.retry("No data returned from TMDB API")

            cast = data.get("cast", [])
            if len(cast) < position:
                return GroundTruthResult.fail(f"Movie has fewer than {position} cast members")

            # Position is 1-indexed, list is 0-indexed
            actor = cast[position - 1]
            character = actor.get("character", "")

            if character:
                return GroundTruthResult.ok(f"{actor['name']} (as {character})")
            return GroundTruthResult.ok(actor["name"])

        except Exception as e:
            return GroundTruthResult.retry(f"TMDB API error: {e}")

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        result = await self.get_ground_truth(validation_info)

        if not result.success:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        return self._validate_actor_name(answer, result.value)

    def _validate_actor_name(self, answer: str, expected: str) -> ValidationResult:
        import re

        answer_lower = answer.lower().strip()

        # Expected format: "Actor Name" or "Actor Name (as Character)"
        match = re.match(r"(.+?)\s*(?:\(as .+\))?$", expected)
        exp_name = match.group(1).lower() if match else expected.lower()

        # Check full name
        if exp_name in answer_lower:
            return ValidationResult(
                score=1.0, is_correct=True, expected=expected,
                actual=answer, details="Actor name matches",
            )

        # Check last name
        parts = exp_name.split()
        if len(parts) > 1:
            last_name = parts[-1]
            if last_name in answer_lower and len(last_name) > 3:
                return ValidationResult(
                    score=1.0, is_correct=True, expected=expected,
                    actual=answer, details="Actor last name matches",
                )

        return ValidationResult(
            score=0.0, is_correct=False, expected=expected,
            actual=answer, details="Actor name not found in answer",
        )

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> TriggerConfig:
        movie_id = validation_info.get("movie_id", "")
        trigger = UrlPatternTrigger(
            domains=["themoviedb.org"],
            url_contains=f"/movie/{movie_id}" if movie_id else None,
        )
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)
