"""Base classes for question template framework"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Callable, Type
import random


# Global template registry
_TEMPLATE_REGISTRY: Dict[str, Type["QuestionTemplate"]] = {}


def register_template(name: str):
    """
    Decorator to register a template class.

    Usage:
        @register_template("location_name")
        class LocationNameWeatherTemplate(QuestionTemplate):
            ...
    """
    def decorator(cls: Type["QuestionTemplate"]) -> Type["QuestionTemplate"]:
        _TEMPLATE_REGISTRY[name] = cls
        return cls
    return decorator


def get_registered_templates() -> Dict[str, Type["QuestionTemplate"]]:
    """Get all registered templates"""
    return _TEMPLATE_REGISTRY.copy()


def get_template(name: str) -> Optional[Type["QuestionTemplate"]]:
    """Get a template class by name"""
    return _TEMPLATE_REGISTRY.get(name)


class VariableType(Enum):
    """Types of variables that can be used in question templates"""
    LOCATION = "location"
    DATE = "date"
    METRIC = "metric"
    NUMERIC = "numeric"
    TEXT = "text"
    BOOLEAN = "boolean"


@dataclass
class ValidationResult:
    """Result of answer validation"""
    score: float  # 0.0 - 1.0
    is_correct: bool
    expected: Any
    actual: Any
    details: str


@dataclass
class GeneratedQuestion:
    """A generated question with all metadata for validation"""
    question_text: str  # Natural language question
    start_url: str  # URL to navigate to
    variables: Dict[str, Any]  # Resolved variable values
    validation_info: Dict[str, Any]  # Info needed for validation
    template_name: str  # Name of the template that generated this
    expected_steps: int = 5  # Expected number of steps to complete this question


class Variable(ABC):
    """
    Abstract base class for question variables.

    Variables define a space of possible values that can be sampled
    for question generation. They should NOT use hardcoded enumeration
    but instead define rules for dynamic generation.
    """

    def __init__(self, name: str, var_type: VariableType):
        self.name = name
        self.var_type = var_type

    @abstractmethod
    def sample(self, rng: random.Random) -> Any:
        """
        Sample a value from the variable's domain.

        Args:
            rng: Random number generator for reproducibility

        Returns:
            A sampled value from the variable's domain
        """
        pass

    @abstractmethod
    def get_display_value(self, value: Any) -> str:
        """
        Convert a sampled value to a human-readable string for the question.

        Args:
            value: The sampled value

        Returns:
            Human-readable string representation
        """
        pass

    @abstractmethod
    def get_api_value(self, value: Any) -> str:
        """
        Convert a sampled value to the format needed for API queries.

        Args:
            value: The sampled value

        Returns:
            API-compatible string representation
        """
        pass


class Validator(ABC):
    """
    Abstract base class for answer validators.

    Validators compare agent answers against ground truth
    using specific validation logic (e.g., numeric tolerance,
    exact match, boolean, etc.)
    """

    @abstractmethod
    def validate(self, answer: str, ground_truth: Any) -> ValidationResult:
        """
        Validate an answer against ground truth.

        Args:
            answer: The agent's answer (always a string)
            ground_truth: The expected correct answer

        Returns:
            ValidationResult with score and details
        """
        pass

    @abstractmethod
    def extract_value(self, answer: str) -> Optional[Any]:
        """
        Extract the relevant value from the answer string.

        Args:
            answer: The agent's answer string

        Returns:
            Extracted value or None if extraction failed
        """
        pass


class QuestionTemplate(ABC):
    """
    Abstract base class for question templates.

    A template defines:
    - What variables are used (location, date, metric, etc.)
    - How to generate a natural language question
    - How to construct the start URL
    - How to validate answers

    Templates should be composable for multi-part questions.
    """

    def __init__(self, name: str):
        self.name = name
        self._variables: Dict[str, Variable] = {}
        self._validators: Dict[str, Validator] = {}

    def register_variable(self, variable: Variable):
        """Register a variable for this template"""
        self._variables[variable.name] = variable

    def register_validator(self, metric_name: str, validator: Validator):
        """Register a validator for a specific metric"""
        self._validators[metric_name] = validator

    @abstractmethod
    def generate(self, seed: int) -> GeneratedQuestion:
        """
        Generate a question using the given seed.

        Args:
            seed: Random seed for reproducible generation

        Returns:
            GeneratedQuestion with all metadata
        """
        pass

    @abstractmethod
    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Any:
        """
        Fetch ground truth from real-time API.

        Args:
            validation_info: Information needed to query the API

        Returns:
            Ground truth value
        """
        pass

    @abstractmethod
    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """
        Validate an answer against real-time ground truth.

        Args:
            answer: The agent's answer
            validation_info: Information for validation

        Returns:
            ValidationResult with score and details
        """
        pass

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        """
        Get task-specific validation rules for this template type.

        Override this method to provide specific scoring rules for this task type.
        These rules will be appended to the common validation prompt.

        Args:
            validation_info: Information about the question being validated

        Returns:
            Task-specific validation rules as a string
        """
        # Default: no special rules
        return ""

    def get_expected_steps(self, validation_info: Dict[str, Any]) -> int:
        """
        Get the expected number of steps to complete this question.

        Override this method for complex questions that require more steps.
        This helps set appropriate limits for evaluation.

        Args:
            validation_info: Information about the question

        Returns:
            Expected number of browser interaction steps
        """
        # Default: 10 steps for simple questions
        return 10

    def _sample_variables(self, rng: random.Random) -> Dict[str, Any]:
        """Sample all registered variables"""
        return {
            name: var.sample(rng)
            for name, var in self._variables.items()
        }


@dataclass
class CompositeQuestion:
    """
    A composite question combining multiple sub-questions.

    Used for multi-part questions that require validating
    multiple answers together.
    """
    questions: List[GeneratedQuestion]
    combined_text: str

    def get_validation_infos(self) -> List[Dict[str, Any]]:
        """Get validation info for all sub-questions"""
        return [q.validation_info for q in self.questions]
