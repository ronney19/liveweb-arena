"""Stooq plugin for financial market data queries"""

import random
from typing import Dict, List

from liveweb_arena.plugins.base import BasePlugin, SubTask, ValidationResult
from liveweb_arena.core.validators.base import QuestionTemplate, get_registered_templates

# Import templates to trigger registration
from . import templates as _  # noqa: F401


class StooqPlugin(BasePlugin):
    """
    Plugin for querying financial market data from stooq.com.

    Stooq is a financial data portal providing real-time and historical
    prices for stocks, indices, currencies, and commodities.

    Supported templates:
    - stooq_price: Current price queries for individual instruments
    - stooq_comparison: Compare multiple instruments
    - stooq_historical: Historical data queries
    - stooq_market_summary: Market overview and analysis questions

    Ground truth is fetched from Stooq's CSV download endpoint.
    """

    def __init__(self, templates: List[str] = None):
        self._template_instances: Dict[str, QuestionTemplate] = {}

        # Get stooq templates from global registry
        registered = get_registered_templates()
        stooq_templates = {
            k: v for k, v in registered.items()
            if k.startswith("stooq_")
        }

        template_names = templates or list(stooq_templates.keys())
        for name in template_names:
            if name in stooq_templates:
                self._template_instances[name] = stooq_templates[name]()

    @property
    def name(self) -> str:
        return "stooq"

    @property
    def supported_sites(self) -> List[str]:
        return ["stooq.com"]

    @property
    def description(self) -> str:
        return "Query financial market data including stocks, indices, currencies, and commodities from stooq.com"

    @property
    def usage_hint(self) -> str:
        return """## stooq.com (Finance)
- /q/?s={symbol} - Quote: price, change, 52-week high/low
- Indices: ^dji, ^spx, ^dax, ^hsi, ^kospi, ^nkx (use ^ prefix)
- Stocks: aapl.us, msft.us | Forex: eurusd | Commodities: gc.f
- /q/d/?s={symbol} - Historical data table
"""

    async def generate_task(
        self,
        seed: int,
        template_name: str = None,
        metric: str = None,
    ) -> SubTask:
        """
        Generate a Stooq query task.

        Args:
            seed: Random seed for task generation
            template_name: Specific template to use (e.g., "stooq_price")
            metric: Not used for Stooq (kept for API compatibility)
        """
        rng = random.Random(seed)

        if not self._template_instances:
            raise ValueError("No templates available")

        # Select template
        if template_name and template_name in self._template_instances:
            selected_template_name = template_name
        else:
            selected_template_name = rng.choice(list(self._template_instances.keys()))

        template = self._template_instances[selected_template_name]

        # Generate question
        question = template.generate(seed)

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
        """Validate answer using the appropriate template"""
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
        """Get ground truth from the appropriate template"""
        template_name = validation_info.get("template_name")
        template = self._template_instances.get(template_name)

        if template is None:
            return None

        return await template.get_ground_truth(validation_info)

    def get_validation_rules(self, validation_info: dict) -> str:
        """Get validation rules from template"""
        template_name = validation_info.get("template_name")
        template = self._template_instances.get(template_name)

        if template is None:
            return ""

        return template.get_validation_rules(validation_info)
