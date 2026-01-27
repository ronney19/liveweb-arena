"""CoinGecko plugin for cryptocurrency market data queries"""

import random
from typing import Dict, List

from liveweb_arena.plugins.base import BasePlugin, SubTask, ValidationResult
from liveweb_arena.core.validators.base import QuestionTemplate, get_registered_templates
from liveweb_arena.core.ground_truth_trigger import GroundTruthResult

# Import templates to trigger registration
from . import templates as _  # noqa: F401


class CoinGeckoPlugin(BasePlugin):
    """
    Plugin for querying cryptocurrency market data from CoinGecko.

    CoinGecko is a cryptocurrency data platform providing real-time prices,
    market cap, trading volume, and historical data for thousands of tokens.

    Supported templates:
    - coingecko_price: Current price queries
    - coingecko_change: 24h price change queries
    - coingecko_comparison: Compare two cryptocurrencies

    Ground truth is fetched from CoinGecko's free API.
    """

    def __init__(self, templates: List[str] = None):
        self._template_instances: Dict[str, QuestionTemplate] = {}

        # Get coingecko templates from global registry
        registered = get_registered_templates()
        coingecko_templates = {
            k: v for k, v in registered.items()
            if k.startswith("coingecko_")
        }

        template_names = templates or list(coingecko_templates.keys())
        for name in template_names:
            if name in coingecko_templates:
                self._template_instances[name] = coingecko_templates[name]()

    @property
    def name(self) -> str:
        return "coingecko"

    @property
    def supported_sites(self) -> List[str]:
        return ["coingecko.com"]

    @property
    def blocked_url_patterns(self) -> List[str]:
        # Block API access to force agents to use the actual website
        # Also block user-related/session-related endpoints that don't need caching
        return [
            "*api.coingecko.com*",
            "*geckoterminal*",
            "*/tagmetrics/*",
            "*/accounts/*",
            "*/onboarding/*",
            "*/sentiment_votes/*",
            "*/portfolios/*",
            "*/portfolio_summary*",
            "*/price_charts/*",
            "*-emoji-*",  # Block emoji/icon requests
        ]

    @property
    def description(self) -> str:
        return "Query cryptocurrency market data including prices, market cap, and 24h changes from CoinGecko"

    @property
    def usage_hint(self) -> str:
        return """## coingecko.com (Cryptocurrency)
- Website: https://www.coingecko.com/en/coins/{coin_id}
- Shows price, market cap, 24h change, volume, supply info
- Example pages: /en/coins/bitcoin, /en/coins/ethereum, /en/coins/solana
- Use the search bar or navigate to coin pages directly
"""

    async def generate_task(
        self,
        seed: int,
        template_name: str = None,
        variant: int = None,
    ) -> SubTask:
        """Generate a CoinGecko query task."""
        rng = random.Random(seed)

        if not self._template_instances:
            raise ValueError("No templates available")

        # Normalize template name: accept both "comparison" and "coingecko_comparison"
        selected_template_name = None
        if template_name:
            if template_name in self._template_instances:
                selected_template_name = template_name
            elif f"coingecko_{template_name}" in self._template_instances:
                selected_template_name = f"coingecko_{template_name}"

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
        template_name = validation_info.get("template_name", "coingecko_price")
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
        template_name = validation_info.get("template_name", "coingecko_price")
        template = self._template_instances.get(template_name)

        if template is None:
            return GroundTruthResult.fail(f"Unknown template: {template_name}")

        return await template.get_ground_truth(validation_info)

    def get_validation_rules(self, validation_info: dict) -> str:
        """Get validation rules from the appropriate template."""
        template_name = validation_info.get("template_name", "coingecko_price")
        template = self._template_instances.get(template_name)

        if template is None:
            template = list(self._template_instances.values())[0]

        return template.get_validation_rules(validation_info)

    def get_ground_truth_trigger(self, validation_info: dict):
        """Get trigger from the appropriate template."""
        template_name = validation_info.get("template_name", "coingecko_price")
        template = self._template_instances.get(template_name)

        if template is None:
            template = list(self._template_instances.values())[0]

        return template.get_ground_truth_trigger(validation_info)
