"""Hybrid plugin for cross-site queries requiring multiple data sources"""

import random
from typing import Dict, List

from liveweb_arena.plugins.base import BasePlugin, SubTask, ValidationResult
from liveweb_arena.core.validators.base import QuestionTemplate, get_registered_templates

# Import templates to trigger registration
from . import templates as _  # noqa: F401


class HybridPlugin(BasePlugin):
    """
    Plugin for cross-site optimization tasks requiring exploration.

    This plugin generates RL-friendly tasks that cannot be solved by
    following a fixed path - they require exploring multiple options
    across different websites to find the optimal answer.

    Supported templates:
    - hybrid_top_performer: Find which asset has the best 24h performance

    Why this is RL-friendly (not just longer SFT):
    1. EXPLORATION REQUIRED - Must check multiple assets to find the best
    2. OPTIMIZATION OBJECTIVE - Find maximum, not just any valid answer
    3. NO FIXED PATH - Order of checking is a strategic choice
    4. POLICY LEARNING - Agent can learn adaptive strategies
    5. NON-DEMONSTRABLE - Expert demo doesn't generalize across instances
    """

    def __init__(self, templates: List[str] = None):
        self._template_instances: Dict[str, QuestionTemplate] = {}

        # Get hybrid templates from global registry
        registered = get_registered_templates()
        hybrid_templates = {
            k: v for k, v in registered.items()
            if k.startswith("hybrid_")
        }

        template_names = templates or list(hybrid_templates.keys())
        for name in template_names:
            if name in hybrid_templates:
                self._template_instances[name] = hybrid_templates[name]()

    @property
    def name(self) -> str:
        return "hybrid"

    @property
    def supported_sites(self) -> List[str]:
        return ["coingecko.com", "stooq.com"]

    @property
    def cache_sources(self) -> List[str]:
        return ["coingecko", "stooq"]

    @property
    def description(self) -> str:
        return "Cross-site queries requiring data from multiple sources (CoinGecko + Stooq)"

    @property
    def usage_hint(self) -> str:
        return """## Cross-Site Hybrid Queries
- Requires visiting multiple websites to gather data
- CoinGecko for crypto prices: coingecko.com/en/coins/{coin_id}
- Stooq for stock prices: stooq.com/q/?s={symbol}
- Agent must extract data from both and perform calculations
"""

    async def generate_task(
        self,
        seed: int,
        template_name: str = None,
        variant: int = None,
    ) -> SubTask:
        """Generate a hybrid cross-site query task."""
        rng = random.Random(seed)

        if not self._template_instances:
            raise ValueError("No templates available")

        # Normalize template name: accept both "top_performer" and "hybrid_top_performer"
        selected_template_name = None
        if template_name:
            if template_name in self._template_instances:
                selected_template_name = template_name
            elif f"hybrid_{template_name}" in self._template_instances:
                selected_template_name = f"hybrid_{template_name}"

        if not selected_template_name:
            selected_template_name = rng.choice(list(self._template_instances.keys()))

        template = self._template_instances[selected_template_name]

        # Generate question
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
        self, answer: str, validation_info: dict
    ) -> ValidationResult:
        """Validate answer using the appropriate template."""
        template_name = validation_info.get("template_name")
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
        from liveweb_arena.core.ground_truth_trigger import GroundTruthResult

        template_name = validation_info.get("template_name")
        template = self._template_instances.get(template_name)

        if template is None:
            return GroundTruthResult.fail(f"Unknown template: {template_name}")

        return await template.get_ground_truth(validation_info)

    def get_validation_rules(self, validation_info: dict) -> str:
        """Get validation rules from template."""
        template_name = validation_info.get("template_name")
        template = self._template_instances.get(template_name)

        if template is None:
            return ""

        return template.get_validation_rules(validation_info)

    def get_ground_truth_trigger(self, validation_info: dict):
        """Get trigger from template."""
        template_name = validation_info.get("template_name")
        template = self._template_instances.get(template_name)

        if template is None:
            return None

        return template.get_ground_truth_trigger(validation_info)
