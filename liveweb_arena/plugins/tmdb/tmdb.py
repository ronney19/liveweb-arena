"""TMDB plugin for movie data queries"""

import random
from typing import Dict, List

from liveweb_arena.plugins.base import BasePlugin, SubTask, ValidationResult
from liveweb_arena.core.validators.base import QuestionTemplate, get_registered_templates
from liveweb_arena.core.ground_truth_trigger import GroundTruthResult

# Import templates to trigger registration
from . import templates as _  # noqa: F401


class TMDBPlugin(BasePlugin):
    """
    Plugin for querying movie data from The Movie Database (TMDB).

    TMDB is a comprehensive movie database providing metadata including
    release dates, runtime, cast, crew, and more.

    Supported templates:
    - tmdb_movie_info: Basic movie information (Easy)
    - tmdb_movie_cast: Movie cast/credits (Medium)
    - tmdb_movie_comparison: Compare two movies (Hard)

    Ground truth is fetched from TMDB's API.
    """

    def __init__(self, templates: List[str] = None):
        self._template_instances: Dict[str, QuestionTemplate] = {}

        # Get tmdb templates from global registry
        registered = get_registered_templates()
        tmdb_templates = {
            k: v for k, v in registered.items()
            if k.startswith("tmdb_")
        }

        template_names = templates or list(tmdb_templates.keys())
        for name in template_names:
            if name in tmdb_templates:
                self._template_instances[name] = tmdb_templates[name]()

    @property
    def name(self) -> str:
        return "tmdb"

    @property
    def supported_sites(self) -> List[str]:
        return ["themoviedb.org", "www.themoviedb.org"]

    @property
    def blocked_url_patterns(self) -> List[str]:
        # Block API access to force agents to use the actual website
        return ["*api.themoviedb.org*"]

    @property
    def description(self) -> str:
        return "Query movie data including release dates, runtime, cast, and crew from TMDB"

    @property
    def usage_hint(self) -> str:
        return """## themoviedb.org (Movies)
- Website: https://www.themoviedb.org/movie/{movie_id}
- Shows release date, runtime, cast, crew, and more
- Example pages: /movie/872585 (Oppenheimer), /movie/550 (Fight Club)
- Use the search bar or navigate to movie pages directly
- Cast and crew info available on the main movie page or dedicated cast tab
"""

    async def generate_task(
        self,
        seed: int,
        template_name: str = None,
        variant: int = None,
    ) -> SubTask:
        """Generate a TMDB query task."""
        rng = random.Random(seed)

        if not self._template_instances:
            raise ValueError("No templates available")

        # Normalize template name: accept both "cast_position" and "tmdb_cast_position"
        selected_template_name = None
        if template_name:
            if template_name in self._template_instances:
                selected_template_name = template_name
            elif f"tmdb_{template_name}" in self._template_instances:
                selected_template_name = f"tmdb_{template_name}"

        if not selected_template_name:
            selected_template_name = rng.choice(list(self._template_instances.keys()))
        template = self._template_instances[selected_template_name]

        question = template.generate(seed, variant=variant)

        return SubTask(
            plugin_name=self.name,
            intent=question.question_text,
            validation_info={
                "template_name": selected_template_name,
                **question.validation_info,
            },
            answer_tag="",
            expected_steps=question.expected_steps,
        )

    async def validate_answer(
        self,
        answer: str,
        validation_info: dict
    ) -> ValidationResult:
        """Validate answer using the appropriate template."""
        template_name = validation_info.get("template_name", "tmdb_movie_info")
        template = self._template_instances.get(template_name)

        if template is None:
            template = list(self._template_instances.values())[0]

        result = await template.validate_answer(answer, validation_info)

        return ValidationResult(
            score=result.score,
            is_correct=result.is_correct,
            expected=result.expected,
            actual=result.actual,
            details=result.details,
        )

    async def get_ground_truth(self, validation_info: dict):
        """Get ground truth from the appropriate template."""
        template_name = validation_info.get("template_name", "tmdb_movie_info")
        template = self._template_instances.get(template_name)

        if template is None:
            return GroundTruthResult.fail(f"Unknown template: {template_name}")

        return await template.get_ground_truth(validation_info)

    def get_validation_rules(self, validation_info: dict) -> str:
        """Get validation rules from the appropriate template."""
        template_name = validation_info.get("template_name", "tmdb_movie_info")
        template = self._template_instances.get(template_name)

        if template is None:
            template = list(self._template_instances.values())[0]

        return template.get_validation_rules(validation_info)

    def get_ground_truth_trigger(self, validation_info: dict):
        """Get trigger from the appropriate template."""
        template_name = validation_info.get("template_name", "tmdb_movie_info")
        template = self._template_instances.get(template_name)

        if template is None:
            template = list(self._template_instances.values())[0]

        return template.get_ground_truth_trigger(validation_info)
