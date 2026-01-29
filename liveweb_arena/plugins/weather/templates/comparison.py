"""Weather comparison template - MULTI-STEP INTERACTION"""

import random
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
)
from liveweb_arena.core.gt_collector import GTSourceType
from .variables import LocationVariable, LocationSpec, LocationType


# Major city pairs from different climate zones for interesting comparisons
CITY_PAIRS = [
    # Asia vs Europe
    (("Tokyo", "Tokyo"), ("London", "London")),
    (("Beijing", "Beijing"), ("Paris", "Paris")),
    (("Singapore", "Singapore"), ("Berlin", "Berlin")),
    (("Seoul", "Seoul"), ("Rome", "Rome")),
    # Americas
    (("New York", "New+York"), ("Los Angeles", "Los+Angeles")),
    (("Chicago", "Chicago"), ("Miami", "Miami")),
    (("Toronto", "Toronto"), ("Mexico City", "Mexico+City")),
    (("Seattle", "Seattle"), ("Houston", "Houston")),
    # Cross-continental
    (("Sydney", "Sydney"), ("Tokyo", "Tokyo")),
    (("Dubai", "Dubai"), ("Moscow", "Moscow")),
    (("Mumbai", "Mumbai"), ("Cairo", "Cairo")),
    (("Hong Kong", "Hong+Kong"), ("Vancouver", "Vancouver")),
    # Europe
    (("Paris", "Paris"), ("Madrid", "Madrid")),
    (("Amsterdam", "Amsterdam"), ("Athens", "Athens")),
    (("Stockholm", "Stockholm"), ("Lisbon", "Lisbon")),
]


@register_template("weather_comparison")
class WeatherComparisonTemplate(QuestionTemplate):
    """
    Template for comparing weather between two cities - MULTI-STEP INTERACTION.

    Requires the agent to:
    1. Visit first city's weather page
    2. Visit second city's weather page
    3. Compare the temperatures

    Examples:
    - Which city is warmer right now, Tokyo or London?
    - Is it hotter in New York or Los Angeles today?
    - Compare the current temperature in Paris and Berlin.
    """

    GT_SOURCE = GTSourceType.API_ONLY  # Multi-city comparison

    COMPARISON_PATTERNS = [
        "Which city is warmer right now, {city1} or {city2}?",
        "Is it hotter in {city1} or {city2} at this moment?",
        "Compare the current temperature: {city1} vs {city2}. Which is warmer?",
        "Between {city1} and {city2}, which city has higher temperature right now?",
    ]

    def __init__(self):
        super().__init__("weather_comparison")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate a weather comparison question."""
        rng = random.Random(seed)

        # Select a city pair
        pair = rng.choice(CITY_PAIRS)
        city1_name, city1_query = pair[0]
        city2_name, city2_query = pair[1]

        # Randomly swap order
        if rng.random() > 0.5:
            city1_name, city1_query, city2_name, city2_query = city2_name, city2_query, city1_name, city1_query

        pattern = rng.choice(self.COMPARISON_PATTERNS)
        question_text = pattern.format(city1=city1_name, city2=city2_name)

        validation_info = {
            "city1_name": city1_name,
            "city1_query": city1_query,
            "city2_name": city2_name,
            "city2_query": city2_query,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=f"https://wttr.in/{city1_query}",
            variables={"city1_name": city1_name, "city2_name": city2_name},
            validation_info=validation_info,
            template_name=self.name,
            expected_steps=8,  # Need to visit two pages
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        city1 = validation_info.get("city1_name", "City1")
        city2 = validation_info.get("city2_name", "City2")
        return f"""Task-Specific Rules (Weather Comparison):
- Answer must clearly state which city ({city1} or {city2}) is warmer
- Score 1.0: Correct city identified
- Score 0.0: Wrong city or unclear answer
- Accept: "{city1}", "{city1} is warmer", "It's hotter in {city1}", temperature values with comparison"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Get temperatures for both cities from collected API data (no network fallback)."""
        city1_query = validation_info.get("city1_query", "")
        city2_query = validation_info.get("city2_query", "")
        city1_name = validation_info.get("city1_name", "")
        city2_name = validation_info.get("city2_name", "")

        if not city1_query or not city2_query:
            return GroundTruthResult.fail("Missing city queries")

        from liveweb_arena.core.gt_collector import get_current_gt_collector
        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.fail("No GT collector")

        collected = gt_collector.get_collected_api_data()

        # Helper to find city data with variants
        def find_city_data(query, name):
            city_name = query.split(",")[0].strip() if "," in query else query
            variants = [
                query, city_name,
                city_name.replace('+', ' '), query.replace('+', ' '),
            ]
            for loc_key in variants:
                if loc_key in collected:
                    return collected[loc_key]
            return None

        data1 = find_city_data(city1_query, city1_name)
        if data1 is None:
            return GroundTruthResult.fail(f"Weather data for '{city1_name}' not collected")

        data2 = find_city_data(city2_query, city2_name)
        if data2 is None:
            return GroundTruthResult.fail(f"Weather data for '{city2_name}' not collected")

        # Get current temperatures
        temp1 = int(data1.get("current_condition", [{}])[0].get("temp_C", 0))
        temp2 = int(data2.get("current_condition", [{}])[0].get("temp_C", 0))

        if temp1 > temp2:
            return GroundTruthResult.ok(f"{city1_name} ({temp1}°C vs {temp2}°C)")
        elif temp2 > temp1:
            return GroundTruthResult.ok(f"{city2_name} ({temp2}°C vs {temp1}°C)")
        else:
            return GroundTruthResult.ok(f"Same temperature ({temp1}°C)")

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate comparison answer."""
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
        city1_name = validation_info.get("city1_name", "").lower()
        city2_name = validation_info.get("city2_name", "").lower()
        answer_lower = answer.lower()

        # Handle "same temperature" case
        if "same" in ground_truth.lower():
            if "same" in answer_lower or "equal" in answer_lower:
                return ValidationResult(
                    score=1.0,
                    is_correct=True,
                    expected=ground_truth,
                    actual=answer,
                    details="Correctly identified same temperature",
                )

        # Extract winner from ground truth
        winner = ground_truth.split(" (")[0].lower()

        # Check if answer mentions the correct city
        if winner in answer_lower:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=answer,
                details="Correct city identified",
            )

        # Check for partial matches (first word of city name)
        winner_parts = winner.split()
        if any(part in answer_lower for part in winner_parts if len(part) > 3):
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=ground_truth,
                actual=answer,
                details="Correct city identified (partial match)",
            )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=ground_truth,
            actual=answer,
            details="Wrong city or unclear answer",
        )

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> TriggerConfig:
        """Trigger when AI visits the second city's page."""
        city2_query = validation_info.get("city2_query", "")
        trigger = UrlPatternTrigger(
            domains=["wttr.in"],
            url_contains=city2_query.replace("+", " ").split(",")[0] if city2_query else None,
        )
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "weather"

