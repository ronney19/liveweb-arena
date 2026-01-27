"""Taostats plugin for Bittensor network data queries"""

import random
from typing import Dict, List, Type

from liveweb_arena.plugins.base import BasePlugin, SubTask, ValidationResult
from liveweb_arena.core.validators.base import QuestionTemplate, GeneratedQuestion, get_registered_templates
from liveweb_arena.core.ground_truth_trigger import GroundTruthResult

# Import templates to trigger registration
from . import templates as _  # noqa: F401


class TaostatsPlugin(BasePlugin):
    """
    Plugin for querying Bittensor network data from taostats.io.

    Taostats is a blockchain explorer and analytics platform for Bittensor,
    providing subnet data, validator info, and network statistics.

    Key pages:
    - /subnets - List of all subnets
    - /subnets/{id} - Subnet details
    - /validators - Validator list and stats
    """

    def __init__(self, templates: List[str] = None):
        self._template_instances: Dict[str, QuestionTemplate] = {}

        # Get taostats templates from global registry
        registered = get_registered_templates()
        taostats_templates = {
            k: v for k, v in registered.items()
            if k.startswith("taostats_")
        }

        template_names = templates or list(taostats_templates.keys())
        for name in template_names:
            if name in taostats_templates:
                self._template_instances[name] = taostats_templates[name]()

    @property
    def name(self) -> str:
        return "taostats"

    @property
    def supported_sites(self) -> List[str]:
        return ["taostats.io"]

    @property
    def cache_sources(self) -> List[str]:
        # Taostats uses live API, no caching needed
        return []

    @property
    def description(self) -> str:
        return "Query Bittensor network data including subnets, validators, and network statistics"

    @property
    def usage_hint(self) -> str:
        return """## taostats.io (Bittensor)
- /subnets - All subnets list (SN1, SN2...), sortable by market cap/price/emission
- /subnets/{id} - Subnet details: owner address, emission, price (e.g., /subnets/27)
- /validators - Validator list (tao.bot, Taostats, etc.)
- Note: "SN28" means subnet ID 28, find at /subnets/28
"""

    async def generate_task(
        self,
        seed: int,
        template_name: str = None,
        variant: int = None,
    ) -> SubTask:
        """
        Generate a Taostats query task.

        Args:
            seed: Random seed for task generation
            template_name: Specific template to use (e.g., "taostats_subnet_info")
            variant: Optional variant index for deterministic question type selection
        """
        rng = random.Random(seed)

        if not self._template_instances:
            raise ValueError("No templates available")

        # Normalize template name: accept both "price" and "taostats_price"
        selected_template_name = None
        if template_name:
            if template_name in self._template_instances:
                selected_template_name = template_name
            elif f"taostats_{template_name}" in self._template_instances:
                selected_template_name = f"taostats_{template_name}"

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
        )

    async def validate_answer(
        self, answer: str, validation_info: dict
    ) -> ValidationResult:
        """Validate answer - uses LLM validation for Taostats"""
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
        template_name = validation_info.get("template_name")
        template = self._template_instances.get(template_name)

        if template is None:
            return GroundTruthResult.fail(f"Unknown template: {template_name}")

        return await template.get_ground_truth(validation_info)

    def get_validation_rules(self, validation_info: dict) -> str:
        """Get validation rules from template"""
        template_name = validation_info.get("template_name")
        template = self._template_instances.get(template_name)

        if template is None:
            return ""

        return template.get_validation_rules(validation_info)

    def get_ground_truth_trigger(self, validation_info: dict):
        """Get trigger from template"""
        template_name = validation_info.get("template_name")
        template = self._template_instances.get(template_name)

        if template is None:
            return None

        return template.get_ground_truth_trigger(validation_info)
