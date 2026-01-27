"""Base plugin interface and data structures"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, TYPE_CHECKING, Union

# Import ValidationResult from validators to avoid duplication
from ..core.validators.base import ValidationResult

if TYPE_CHECKING:
    from ..core.ground_truth_trigger import GroundTruthResult


@dataclass
class SubTask:
    """A single sub-task within a composite task"""
    plugin_name: str
    intent: str
    validation_info: dict
    answer_tag: str  # "answer1"..."answer4"
    expected_steps: int = 5  # Expected steps for this subtask
    # Note: start_url removed - Agent should decide which URL to visit


class BasePlugin(ABC):
    """
    Base class for all website plugins.

    Each plugin is responsible for:
    1. Providing description and usage hints for the Agent
    2. generate_task(): Generate a sub-task with deterministic seed
    3. validate_answer(): Validate answer against real-time API ground truth
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name (e.g., 'weather', 'stock')"""
        pass

    @property
    @abstractmethod
    def supported_sites(self) -> List[str]:
        """List of supported website domains"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """
        Short description of what this plugin provides.
        Used in system prompt to help Agent understand available tools.
        """
        pass

    @property
    @abstractmethod
    def usage_hint(self) -> str:
        """
        Detailed usage instructions for the Agent.
        Should include:
        - Website URL patterns
        - How to navigate and find information
        - Data format on the website
        """
        pass

    @abstractmethod
    async def generate_task(
        self,
        seed: int,
        template_name: str = None,
        variant: int = None,
    ) -> SubTask:
        """
        Generate a sub-task deterministically based on seed.

        Args:
            seed: Random seed for deterministic generation
            template_name: Optional specific template to use
            variant: Optional variant index for deterministic question type selection.
                     If None, random selection is used. If specified, selects a specific
                     question variant (0-indexed).

        Returns:
            SubTask with intent and validation_info
            Note: Does NOT include start_url - Agent decides navigation
        """
        pass

    @abstractmethod
    async def validate_answer(
        self, answer: str, validation_info: dict
    ) -> ValidationResult:
        """
        Validate answer against real-time API ground truth.

        Args:
            answer: The answer string from the agent
            validation_info: Parameters for validation (from SubTask)

        Returns:
            ValidationResult with score and details
        """
        pass

    @abstractmethod
    async def get_ground_truth(
        self, validation_info: dict
    ) -> Union["GroundTruthResult", Any]:
        """
        Get ground truth value for LLM-based validation.

        Args:
            validation_info: Parameters for fetching ground truth (from SubTask)

        Returns:
            GroundTruthResult with success/failure status, or raw value (legacy)
        """
        pass

    def get_validation_rules(self, validation_info: dict) -> str:
        """
        Get task-specific validation rules for LLM validator.

        Override this method to provide task-specific scoring rules.
        These rules will be included in the LLM validation prompt.

        Args:
            validation_info: Parameters for validation (from SubTask)

        Returns:
            Task-specific validation rules as a string
        """
        return ""  # Default: no specific rules

    def get_ground_truth_trigger(self, validation_info: dict):
        """
        Get trigger condition for ground truth fetching.

        Override this method to delegate to template's trigger method.

        Args:
            validation_info: Parameters for the subtask

        Returns:
            TriggerConfig or None (None means fetch at end as fallback)
        """
        return None

    @property
    def blocked_url_patterns(self) -> List[str]:
        """
        URL patterns to block during evaluation.

        Use this to prevent agents from accessing APIs directly,
        forcing them to interact with the actual website.

        Returns:
            List of URL patterns (supports * wildcard)
            Example: ["*api.example.com*", "*//example.com/api/*"]
        """
        return []

    @property
    def allowed_domains(self) -> List[str]:
        """
        Domains the agent is allowed to visit for this plugin.

        By default, returns supported_sites. Override to add additional
        allowed domains (e.g., CDN domains, authentication domains).

        Returns:
            List of allowed domain names (without protocol)
        """
        return self.supported_sites

    @property
    def cache_sources(self) -> List[str]:
        """
        Cache sources required by this plugin.

        By default, returns [self.name] assuming 1:1 mapping between
        plugin name and cache source. Override for plugins that:
        - Use multiple sources (e.g., hybrid)
        - Use no caching (e.g., taostats with live API)

        Returns:
            List of cache source names
        """
        return [self.name]
