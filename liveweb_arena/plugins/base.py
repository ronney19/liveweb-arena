"""
Base Plugin Interface.

Defines the interface that all website plugins must implement.

To create a new plugin:
1. Create a directory under plugins/
2. Create plugin.py implementing BasePlugin
3. Create templates/*.py with question templates

Example:
    class CoinGeckoPlugin(BasePlugin):
        name = "coingecko"
        allowed_domains = ["coingecko.com", "www.coingecko.com"]

        async def fetch_api_data(self, url: str) -> Dict[str, Any]:
            # Extract coin_id from URL and fetch from CoinGecko API
            coin_id = self._extract_coin_id(url)
            return await CoinGeckoClient.get_coin_data(coin_id)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from liveweb_arena.core.cache import normalize_url


@dataclass
class SubTask:
    """
    A single sub-task within a composite task.

    This is a bridge structure for backward compatibility during migration.
    New code should use GeneratedQuestion directly.
    """
    plugin_name: str
    intent: str
    validation_info: dict
    answer_tag: str  # "answer1"..."answer4"
    expected_steps: int = 5
    # Reference to the generated question (new architecture)
    question: Optional[Any] = None


@dataclass
class ValidationResult:
    """
    Result of answer validation.

    Kept for backward compatibility with existing code.
    """
    score: float
    is_correct: bool
    expected: Optional[str]
    actual: Optional[str]
    details: Optional[str] = None


class BasePlugin(ABC):
    """
    Base class for all website plugins.

    Each plugin must define:
    - name: Unique plugin identifier
    - allowed_domains: List of domains the agent can visit
    - fetch_api_data(): Method to get API data for a page URL

    Optional:
    - blocked_url_patterns: URL patterns to block (e.g., direct API access)
    - normalize_url(): Custom URL normalization logic
    """

    # ===== Required class attributes =====

    name: str
    """Unique plugin name (e.g., 'coingecko', 'stooq')"""

    allowed_domains: List[str]
    """List of allowed domain names (e.g., ['coingecko.com', 'www.coingecko.com'])"""

    # ===== Required methods =====

    @abstractmethod
    async def fetch_api_data(self, url: str) -> Dict[str, Any]:
        """
        Fetch API data for a given page URL.

        This method should:
        1. Extract the asset identifier from the URL (e.g., coin_id, stock_symbol)
        2. Call the appropriate API to get the data
        3. Return the complete API response (not just specific fields)

        Args:
            url: The page URL (e.g., https://www.coingecko.com/en/coins/bitcoin)

        Returns:
            Complete API data dictionary for the asset

        Example:
            async def fetch_api_data(self, url: str) -> Dict[str, Any]:
                coin_id = self._extract_coin_id(url)  # "bitcoin"
                return await CoinGeckoClient.get_coin_market_data(coin_id)
        """
        pass

    # ===== Optional methods (with default implementations) =====

    def get_blocked_patterns(self) -> List[str]:
        """
        Return URL patterns to block during evaluation.

        Use this to prevent agents from accessing APIs directly,
        forcing them to interact with the actual website.

        Returns:
            List of URL patterns (supports * wildcard)
            Example: ["*api.coingecko.com*"]
        """
        return []

    def normalize_url(self, url: str) -> str:
        """
        Normalize URL for cache lookup.

        Override to implement custom normalization logic.
        Default implementation: lowercase domain, remove tracking params.

        Args:
            url: Original URL

        Returns:
            Normalized URL
        """
        return normalize_url(url)

    def needs_api_data(self, url: str) -> bool:
        """
        Check if this URL needs API data for ground truth.

        Override to return False for navigation/list pages that don't
        contain evaluatable data (e.g., homepage, search pages).

        Default: True (all pages need API data)

        Args:
            url: The page URL

        Returns:
            True if API data is needed, False for navigation-only pages
        """
        return True

    # ===== Deprecated methods (for backward compatibility) =====
    # These will be removed after full migration

    @property
    def supported_sites(self) -> List[str]:
        """Deprecated: Use allowed_domains instead."""
        return self.allowed_domains

    @property
    def description(self) -> str:
        """Deprecated: Plugin description."""
        return f"{self.name} plugin"

    @property
    def usage_hint(self) -> str:
        """Deprecated: Usage hint for the agent."""
        return f"Use {', '.join(self.allowed_domains)} to find information."

    @property
    def blocked_url_patterns(self) -> List[str]:
        """Deprecated: Use get_blocked_patterns() instead."""
        return self.get_blocked_patterns()

    @property
    def cache_sources(self) -> List[str]:
        """Deprecated: Cache sources for this plugin."""
        return [self.name]

    async def generate_task(self, seed: int, template_name: str = None, variant: int = None) -> SubTask:
        """
        Generate a task using registered templates.

        Args:
            seed: Random seed for reproducibility
            template_name: Specific template name (optional)
            variant: Template variant index (optional)

        Returns:
            SubTask with question and validation info
        """
        from liveweb_arena.core.validators.base import get_registered_templates, get_template
        import random

        # Get templates for this plugin
        all_templates = get_registered_templates()
        plugin_templates = {
            name: cls for name, cls in all_templates.items()
            if hasattr(cls, 'get_cache_source') and cls.get_cache_source() == self.name
        }

        if not plugin_templates:
            raise ValueError(f"No templates registered for plugin {self.name}")

        # Select template
        if template_name:
            # Try exact name first, then with plugin prefix
            template_cls = get_template(template_name)
            if not template_cls:
                # Try with plugin prefix (e.g., "rank" -> "coingecko_rank")
                prefixed_name = f"{self.name}_{template_name}"
                template_cls = get_template(prefixed_name)
            if not template_cls:
                raise ValueError(f"Template not found: {template_name}")
        else:
            rng = random.Random(seed)
            template_cls = rng.choice(list(plugin_templates.values()))

        # Generate question
        template = template_cls()
        question = template.generate(seed, variant=variant)

        # Ensure template_name is in validation_info for later use
        validation_info = dict(question.validation_info)
        validation_info["template_name"] = question.template_name

        return SubTask(
            plugin_name=self.name,
            intent=question.question_text,
            validation_info=validation_info,
            answer_tag="answer1",
            expected_steps=question.expected_steps,
            question=question,
        )

    async def validate_answer(self, answer: str, validation_info: dict) -> ValidationResult:
        """Validate answer using template's validator."""
        from liveweb_arena.core.validators.base import get_template

        template_name = validation_info.get("template_name") or validation_info.get("_template_name")
        if not template_name:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="No template name in validation_info",
            )

        template_cls = get_template(template_name)
        if not template_cls:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Template not found: {template_name}",
            )

        template = template_cls()
        return await template.validate_answer(answer, validation_info)

    async def get_ground_truth(self, validation_info: dict):
        """Get ground truth using template's method."""
        from liveweb_arena.core.validators.base import get_template

        template_name = validation_info.get("template_name") or validation_info.get("_template_name")
        if not template_name:
            return None

        template_cls = get_template(template_name)
        if not template_cls:
            return None

        template = template_cls()
        result = await template.get_ground_truth(validation_info)
        return result.value if result and result.success else None

    def get_validation_rules(self, validation_info: dict) -> str:
        """Get validation rules from template."""
        from liveweb_arena.core.validators.base import get_template

        template_name = validation_info.get("template_name") or validation_info.get("_template_name")
        if not template_name:
            return ""

        template_cls = get_template(template_name)
        if not template_cls:
            return ""

        template = template_cls()
        if hasattr(template, 'get_validation_rules'):
            return template.get_validation_rules(validation_info)
        return ""

    def get_ground_truth_trigger(self, validation_info: dict):
        """Get ground truth trigger configuration from template."""
        from liveweb_arena.core.validators.base import get_template

        template_name = validation_info.get("template_name") or validation_info.get("_template_name")
        if not template_name:
            return None

        template_cls = get_template(template_name)
        if not template_cls:
            return None

        template = template_cls()
        if hasattr(template, 'get_ground_truth_trigger'):
            return template.get_ground_truth_trigger(validation_info)
        return None

    def get_gt_source(self, validation_info: dict):
        """
        Get GT source type from template.

        Returns:
            GTSourceType enum value (PAGE_ONLY, API_ONLY, or HYBRID)
        """
        from liveweb_arena.core.validators.base import get_template
        from liveweb_arena.core.gt_collector import GTSourceType

        template_name = validation_info.get("template_name") or validation_info.get("_template_name")
        if not template_name:
            return GTSourceType.PAGE_ONLY

        template_cls = get_template(template_name)
        if not template_cls:
            return GTSourceType.PAGE_ONLY

        template = template_cls()
        if hasattr(template, 'get_gt_source'):
            return template.get_gt_source()
        return GTSourceType.PAGE_ONLY
