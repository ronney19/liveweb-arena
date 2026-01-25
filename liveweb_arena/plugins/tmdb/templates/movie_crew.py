"""Movie crew template for TMDB - MEDIUM-HARD DIFFICULTY"""

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
)
from .variables import MovieVariable, MovieSpec
from ..api_client import TMDBClient


@dataclass
class CrewRole:
    """Specification of a crew role"""
    jobs: List[str]    # TMDB job field values (multiple possible)
    display_name: str  # Human-readable name
    department: str    # TMDB department

    @property
    def job(self) -> str:
        """Primary job name for display."""
        return self.jobs[0]


class CrewRoleVariable:
    """Variable for crew role selection"""

    ROLES: List[CrewRole] = [
        CrewRole(["Director of Photography"], "cinematographer", "Camera"),
        CrewRole(["Original Music Composer"], "composer", "Sound"),
        CrewRole(["Screenplay", "Writer", "Story"], "screenwriter", "Writing"),
        CrewRole(["Producer"], "producer", "Production"),
        CrewRole(["Editor"], "editor", "Editing"),
        CrewRole(["Production Design"], "production designer", "Art"),
        CrewRole(["Costume Design"], "costume designer", "Costume & Make-Up"),
    ]

    def __init__(self, allowed_roles: List[str] = None):
        if allowed_roles:
            self.roles = [r for r in self.ROLES if r.job in allowed_roles]
        else:
            self.roles = self.ROLES

    def sample(self, rng: random.Random) -> CrewRole:
        """Sample a crew role."""
        return rng.choice(self.roles)

    def sample_by_index(self, index: int) -> CrewRole:
        """Sample a specific role by index."""
        return self.roles[index % len(self.roles)]


@register_template("tmdb_movie_crew")
class TMDBMovieCrewTemplate(QuestionTemplate):
    """
    Template for movie crew queries - MEDIUM-HARD DIFFICULTY.

    Requires navigating to the full cast & crew page to find specific
    crew members like cinematographer, composer, editor, etc.

    Examples:
    - Who was the cinematographer for Inception?
    - Who composed the music for Interstellar?
    - Who wrote the screenplay for Pulp Fiction?
    - Who was the editor of The Dark Knight?
    """

    CINEMATOGRAPHER_PATTERNS = [
        "Who was the cinematographer for {movie}?",
        "Who served as director of photography on {movie}?",
        "Who shot {movie}?",
    ]

    COMPOSER_PATTERNS = [
        "Who composed the music for {movie}?",
        "Who wrote the original score for {movie}?",
        "Who was the composer of {movie}?",
    ]

    SCREENWRITER_PATTERNS = [
        "Who wrote the screenplay for {movie}?",
        "Who was the screenwriter of {movie}?",
        "Who wrote {movie}?",
    ]

    PRODUCER_PATTERNS = [
        "Who produced {movie}?",
        "Who was the producer of {movie}?",
        "Name a producer of {movie}.",
    ]

    EDITOR_PATTERNS = [
        "Who edited {movie}?",
        "Who was the editor of {movie}?",
        "Who served as film editor on {movie}?",
    ]

    PRODUCTION_DESIGNER_PATTERNS = [
        "Who was the production designer for {movie}?",
        "Who did the production design for {movie}?",
    ]

    COSTUME_DESIGNER_PATTERNS = [
        "Who designed the costumes for {movie}?",
        "Who was the costume designer for {movie}?",
    ]

    def __init__(self):
        super().__init__("tmdb_movie_crew")
        self._movie_var = MovieVariable()
        self._role_var = CrewRoleVariable()

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a movie crew question."""
        rng = random.Random(seed)

        movie = self._movie_var.sample(rng)

        if variant is not None:
            role = self._role_var.sample_by_index(variant)
        else:
            role = self._role_var.sample(rng)

        question_text = self._build_question(movie, role, rng)
        start_url = f"https://www.themoviedb.org/movie/{movie.movie_id}"

        validation_info = {
            "movie_id": movie.movie_id,
            "movie_title": movie.title,
            "crew_job": role.job,
            "crew_role_name": role.display_name,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=start_url,
            variables={"movie": movie, "role": role},
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=12,  # Search + navigate + find crew section
        )

    def _build_question(
        self,
        movie: MovieSpec,
        role: CrewRole,
        rng: random.Random,
    ) -> str:
        """Build question text based on crew role."""
        pattern_map = {
            "Director of Photography": self.CINEMATOGRAPHER_PATTERNS,
            "Original Music Composer": self.COMPOSER_PATTERNS,
            "Screenplay": self.SCREENWRITER_PATTERNS,
            "Producer": self.PRODUCER_PATTERNS,
            "Editor": self.EDITOR_PATTERNS,
            "Production Design": self.PRODUCTION_DESIGNER_PATTERNS,
            "Costume Design": self.COSTUME_DESIGNER_PATTERNS,
        }

        patterns = pattern_map.get(role.job, [f"Who was the {role.display_name} for {{movie}}?"])
        pattern = rng.choice(patterns)
        return pattern.format(movie=movie.title)

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        """Get validation rules for crew member."""
        role_name = validation_info.get("crew_role_name", "crew member")
        movie = validation_info.get("movie_title", "the movie")

        return f"""Task-Specific Rules (TMDB - Movie Crew):
- Find the {role_name} for {movie}
- Score 1.0: Correct name mentioned (case insensitive, partial name OK if unique)
- Score 0.5: One of multiple {role_name}s mentioned (for roles with multiple people)
- Score 0.0: Wrong person or unable to identify
- For roles with multiple people (e.g., multiple producers), any correct name scores 1.0
- Accept common name variations (e.g., "Hans Zimmer" or "Zimmer")"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Fetch crew data from TMDB API."""
        movie_id = validation_info.get("movie_id", "")
        crew_job = validation_info.get("crew_job", "")

        if not movie_id or not crew_job:
            return GroundTruthResult.fail("Missing movie_id or crew_job")

        try:
            data = await TMDBClient.get_movie_credits(movie_id)
            if not data:
                return GroundTruthResult.retry("No data returned from TMDB API")

            crew = data.get("crew", [])

            # Find the role info to get all possible job titles
            role_var = CrewRoleVariable()
            role_info = next((r for r in role_var.ROLES if crew_job in r.jobs), None)

            if role_info:
                # Search for all job titles in this role
                matches = [
                    c["name"] for c in crew
                    if c.get("job") in role_info.jobs
                ]
            else:
                # Fallback to exact match
                matches = [c["name"] for c in crew if c.get("job") == crew_job]

            # Remove duplicates while preserving order
            seen = set()
            unique_matches = []
            for name in matches:
                if name not in seen:
                    seen.add(name)
                    unique_matches.append(name)

            if unique_matches:
                return GroundTruthResult.ok(", ".join(unique_matches[:3]))

            return GroundTruthResult.fail(f"No crew member found for job: {crew_job}")

        except Exception as e:
            return GroundTruthResult.retry(f"TMDB API error: {e}")

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate crew member answer."""
        result = await self.get_ground_truth(validation_info)

        if not result.success:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        return self._validate_crew_name(answer, result.value)

    def _validate_crew_name(self, answer: str, expected: str) -> ValidationResult:
        """Validate crew member name."""
        answer_lower = answer.lower().strip()

        # Expected format: "Name1, Name2, Name3" for multiple crew
        crew_members = [name.strip().lower() for name in expected.split(",")]

        matched = []
        for name in crew_members:
            # Check full name
            if name in answer_lower:
                matched.append(name)
                continue

            # Check last name (for famous crew members)
            parts = name.split()
            if len(parts) > 1:
                last_name = parts[-1]
                if last_name in answer_lower and len(last_name) > 3:
                    matched.append(name)

        if matched:
            if len(matched) == len(crew_members) or len(crew_members) == 1:
                return ValidationResult(
                    score=1.0, is_correct=True, expected=expected,
                    actual=answer, details=f"Crew member(s) matched: {', '.join(matched)}",
                )
            else:
                # Partial match for multiple crew
                return ValidationResult(
                    score=0.5, is_correct=False, expected=expected,
                    actual=answer, details=f"Partial match: {len(matched)}/{len(crew_members)} matched",
                )

        return ValidationResult(
            score=0.0, is_correct=False, expected=expected,
            actual=answer, details="Crew member name not found in answer",
        )

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> TriggerConfig:
        """Trigger when agent visits the movie's TMDB page or crew page."""
        movie_id = validation_info.get("movie_id", "")
        trigger = UrlPatternTrigger(
            domains=["themoviedb.org"],
            url_contains=f"/movie/{movie_id}" if movie_id else None,
        )
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)
