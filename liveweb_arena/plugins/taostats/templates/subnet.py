"""Subnet information query template for Taostats"""

import random
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template
)
from liveweb_arena.core.validators.validators import NumericToleranceValidator, ExactMatchValidator
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
)
from liveweb_arena.utils.logger import log
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

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Fetch ground truth from cache or Bittensor network via Python SDK"""
        subnet_id = validation_info["subnet_id"]
        metric = validation_info["metric"]

        # Try cache first
        from liveweb_arena.core.snapshot_cache import get_snapshot_cache_manager

        try:
            manager = get_snapshot_cache_manager()
            snapshot = manager.get_current_snapshot()
            if snapshot:
                api_data = snapshot.get_api_data("taostats")
                if api_data:
                    subnets = api_data.get("subnets", {})
                    subnet_data = subnets.get(str(subnet_id))
                    if subnet_data:
                        if metric == "name" and "name" in subnet_data:
                            log("GT", f"CACHE HIT - Taostats subnet {subnet_id}: name", force=True)
                            return GroundTruthResult.ok(subnet_data["name"])
                        elif metric == "owner" and "owner" in subnet_data:
                            log("GT", f"CACHE HIT - Taostats subnet {subnet_id}: owner", force=True)
                            return GroundTruthResult.ok(subnet_data["owner"])
                        elif metric == "price" and "price" in subnet_data:
                            log("GT", f"CACHE HIT - Taostats subnet {subnet_id}: price", force=True)
                            return GroundTruthResult.ok(f"τ{subnet_data['price']:.6f}")

                    # Cache mode but subnet not found
                    log("GT", f"CACHE MISS - Taostats subnet {subnet_id} not in cache ({len(subnets)} subnets cached)", force=True)
                    return GroundTruthResult.fail(f"Subnet {subnet_id} not in cache")
                else:
                    log("GT", "Taostats api_data empty - rebuild cache with --force", force=True)
                    return GroundTruthResult.fail("Taostats api_data empty")
            # No snapshot - fall through to live API
        except Exception as e:
            log("GT", f"CACHE ERROR - Taostats: {e}", force=True)
            return GroundTruthResult.fail(f"Cache error: {e}")

        # No cache - fall back to live Bittensor network (non-cache mode)
        try:
            import bittensor as bt

            # Connect to Bittensor network
            subtensor = bt.Subtensor(network="finney")

            # Get subnet info
            info = subtensor.subnet(subnet_id)

            if info is None:
                return GroundTruthResult.fail(f"Subnet {subnet_id} not found")

            # Extract requested metric
            if metric == "name":
                name = info.subnet_name or info.subnet_identity.subnet_name
                if name:
                    return GroundTruthResult.ok(name)
                return GroundTruthResult.fail("Subnet name not available")
            elif metric == "owner":
                if info.owner_coldkey:
                    return GroundTruthResult.ok(info.owner_coldkey)
                return GroundTruthResult.fail("Owner coldkey not available")
            elif metric == "price":
                if info.price:
                    return GroundTruthResult.ok(f"τ{info.price.tao:.6f}")
                return GroundTruthResult.fail("Price not available")

            return GroundTruthResult.fail(f"Unknown metric: {metric}")

        except Exception as e:
            return GroundTruthResult.retry(f"Bittensor SDK error: {e}")

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate answer against Bittensor ground truth"""
        result = await self.get_ground_truth(validation_info)

        if not result.success:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        ground_truth = result.value
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

    # === Cache Registration Methods ===

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "taostats"

    @classmethod
    def get_cache_urls(cls) -> List[str]:
        """Generate URLs to cache based on subnet IDs."""
        urls = ["https://taostats.io/subnets"]
        # Add all subnet pages (0-128 range to cover all possible subnets)
        for subnet_id in range(129):
            urls.append(f"https://taostats.io/subnets/{subnet_id}")
        return urls

