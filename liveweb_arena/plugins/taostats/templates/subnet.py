"""Subnet information query template for Taostats"""

import random
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template
)
from liveweb_arena.core.validators.validators import NumericToleranceValidator, ExactMatchValidator
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig
)
from .variables import SubnetVariable, MetricVariable, SubnetSpec, MetricSpec, SubnetMetric


@register_template("taostats_subnet_info")
class SubnetInfoTemplate(QuestionTemplate):
    """
    Template for querying subnet information on Taostats.

    Uses Bittensor Python SDK for ground truth validation.
    Generates diverse questions about Bittensor subnets:
    - 128 subnets × 4 metrics = 512+ unique question combinations
    """

    PATTERNS: Dict[SubnetMetric, List[str]] = {
        SubnetMetric.NAME: [
            "What is the name of {subnet}?",
            "What is {subnet} called on Bittensor?",
            "What's the official name of Bittensor {subnet}?",
            "Find the name of {subnet} on taostats.io.",
            "Look up {subnet} and tell me its name.",
            "What subnet name is registered for {subnet}?",
        ],
        SubnetMetric.OWNER: [
            "Who owns {subnet} on Bittensor?",
            "Who is the owner of {subnet}?",
            "What is the owner address of {subnet}?",
            "Find the owner coldkey address for {subnet}.",
            "What wallet address owns {subnet}?",
            "Look up the owner of {subnet} on taostats.io.",
        ],
        SubnetMetric.PRICE: [
            "What is the alpha price of {subnet}?",
            "What's the current alpha token price for {subnet}?",
            "How much is one alpha token worth on {subnet}?",
            "Find the alpha price for {subnet} on taostats.io.",
            "What's the current price of {subnet}'s alpha token in TAO?",
        ],
    }

    def __init__(self):
        super().__init__("taostats_subnet_info")
        self.register_variable(SubnetVariable())
        self.register_variable(MetricVariable())

        # Register validators
        self.register_validator("name", ExactMatchValidator(case_sensitive=False))
        self.register_validator("owner", ExactMatchValidator(case_sensitive=False))
        self.register_validator("price", NumericToleranceValidator(
            full_tolerance=0.0001, partial_tolerance=0.001, unit="τ"
        ))

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """
        Generate a Taostats subnet info question.

        Args:
            seed: Random seed for reproducible generation
            variant: Optional variant index for selecting metric type.
                     0=NAME, 1=OWNER, 2=PRICE
        """
        rng = random.Random(seed)

        subnet: SubnetSpec = self._variables["subnet"].sample(rng)

        # Use variant to select specific metric if provided
        if variant is not None:
            metric: MetricSpec = self._variables["metric"].sample_by_index(variant)
        else:
            metric: MetricSpec = self._variables["metric"].sample(rng)

        patterns = self.PATTERNS.get(metric.metric, ["{subnet}?"])
        pattern = rng.choice(patterns)
        question_text = pattern.format(subnet=subnet.display_name)

        validation_info = {
            "subnet_id": subnet.subnet_id,
            "metric": metric.metric.value,
            "is_numeric": metric.is_numeric,
            "unit": metric.unit,
            "tolerance_pct": metric.tolerance_pct,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=f"https://taostats.io/subnets/{subnet.subnet_id}",
            variables={"subnet": subnet, "metric": metric},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        metric = validation_info.get("metric", "")

        if metric == "name":
            return """Task-Specific Rules (Subnet Name):
- Score 1.0: Names match (case-insensitive)
- Score 0.0: Different names"""

        if metric == "owner":
            return """Task-Specific Rules (Subnet Owner Address):
- Score 1.0: Agent provides COMPLETE address (48 characters starting with 5, matching expected)
- Score 0.5: Agent provides truncated address with "..." that matches start AND end of expected address
- Score 0.0: Address doesn't match, or agent provides only the default truncated display from webpage without verifying

IMPORTANT: The webpage shows truncated addresses by default (e.g., "5DWgkC...uS9Qad").
Simply copying this truncated format is NOT sufficient for full score.
Agent should click the address to get the full address from the URL or account page."""

        return """Task-Specific Rules (Numeric Value):
- Score 1.0: Values match within tolerance
- Score 0.0: Values differ significantly"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Optional[Any]:
        """Fetch ground truth from Bittensor network via Python SDK"""
        try:
            import bittensor as bt

            subnet_id = validation_info["subnet_id"]
            metric = validation_info["metric"]

            # Connect to Bittensor network
            subtensor = bt.Subtensor(network="finney")

            # Get subnet info
            info = subtensor.subnet(subnet_id)

            if info is None:
                return None

            # Extract requested metric
            if metric == "name":
                return info.subnet_name or info.subnet_identity.subnet_name
            elif metric == "owner":
                return info.owner_coldkey
            elif metric == "price":
                return f"τ{info.price.tao:.6f}" if info.price else None

            return None

        except Exception as e:
            # Return None to trigger LLM validation fallback
            return None

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate answer against Bittensor ground truth"""
        ground_truth = await self.get_ground_truth(validation_info)

        if ground_truth is None:
            # Fallback: signal LLM validation needed
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable, requires LLM validation",
            )

        # Get appropriate validator
        metric = validation_info["metric"]
        validator = self._validators.get(metric)

        if validator is None:
            # Simple string comparison fallback
            is_match = str(ground_truth).lower() in answer.lower()
            return ValidationResult(
                score=1.0 if is_match else 0.0,
                is_correct=is_match,
                expected=ground_truth,
                actual=answer,
                details="String match validation",
            )

        return validator.validate(answer, ground_truth)

    def get_ground_truth_trigger(self, validation_info: dict) -> TriggerConfig:
        """
        Taostats subnet: fetch when AI visits the specific subnet's page.

        Uses subnet_id-specific URL matching (e.g., /subnets/27) to ensure
        ground truth is fetched at the exact moment AI observes that subnet.

        Strategy: FIRST - single subnet query.
        """
        subnet_id = validation_info.get("subnet_id", "")
        url_pattern = f"/subnets/{subnet_id}" if subnet_id else None
        return TriggerConfig(
            trigger=UrlPatternTrigger(
                domains=["taostats.io"],
                url_contains=url_pattern,
            ),
            strategy=FetchStrategy.FIRST,
        )
