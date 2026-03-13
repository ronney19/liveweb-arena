"""Weather comparison template for Open Meteo - HARD DIFFICULTY

Compares current temperature between two cities in different climate zones.
Requires the agent to visit two separate pages and compare values.

Dynamic data: temperatures change continuously.
15 city pairs from different climate zones ensure interesting comparisons.
"""

import random
from typing import Any, Dict, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, TriggerConfig, GroundTruthResult,
)
from liveweb_arena.core.gt_collector import GTSourceType, get_current_gt_collector

from .variables import CITY_PAIRS


PATTERNS = [
    "Which city is warmer right now according to Open-Meteo, {city1} or {city2}?",
    "Using Open-Meteo, compare the current temperature in {city1} and {city2}. Which is warmer?",
    "On Open-Meteo, is it hotter in {city1} or {city2} right now?",
    "Between {city1} and {city2}, which city has the higher temperature according to Open-Meteo?",
]


@register_template("openmeteo_comparison")
class OpenMeteoComparisonTemplate(QuestionTemplate):
    """
    HARD: Compare current temperature between two cities.

    Requires visiting two different location pages and comparing values.
    15 city pairs x 4 patterns x 2 orderings = 120 question variants.
    """

    GT_SOURCE = GTSourceType.PAGE_ONLY

    def __init__(self):
        super().__init__("openmeteo_comparison")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        pair = rng.choice(CITY_PAIRS)
        city1, city2 = pair

        # Randomly swap order
        if rng.random() > 0.5:
            city1, city2 = city2, city1

        pattern = rng.choice(PATTERNS)
        question_text = pattern.format(
            city1=city1.display_name,
            city2=city2.display_name,
        )

        return GeneratedQuestion(
            question_text=question_text,
            start_url=city1.docs_url(),
            variables={"city1": city1.name, "city2": city2.name},
            validation_info={
                "city1_name": city1.name,
                "city1_coord_key": city1.coord_key,
                "city2_name": city2.name,
                "city2_coord_key": city2.coord_key,
                "city2_url": city2.docs_url(),
            },
            template_name=self.name,
            expected_steps=8,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        city1 = validation_info.get("city1_name", "City1")
        city2 = validation_info.get("city2_name", "City2")
        return f"""Task-Specific Rules (Open Meteo Weather Comparison):
- Compare current temperature between {city1} and {city2}
- Answer must clearly state which city is warmer
- Score 1.0: Correct city identified
- Score 0.0: Wrong city or unclear answer
- Accept formats: "{city1}", "{city1} is warmer", temperature values with comparison"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        city1_name = validation_info.get("city1_name", "")
        city2_name = validation_info.get("city2_name", "")
        key1 = validation_info.get("city1_coord_key", "")
        key2 = validation_info.get("city2_coord_key", "")

        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.fail("No GT collector")

        collected = gt_collector.get_collected_api_data()

        data1 = collected.get(f"openmeteo:{key1}")
        if data1 is None:
            return GroundTruthResult.not_collected(
                f"Weather data for '{city1_name}' not collected"
            )

        data2 = collected.get(f"openmeteo:{key2}")
        if data2 is None:
            return GroundTruthResult.not_collected(
                f"Weather data for '{city2_name}' not collected"
            )

        cw1 = data1.get("current_weather")
        cw2 = data2.get("current_weather")
        if not cw1 or "temperature" not in cw1:
            return GroundTruthResult.fail(f"No temperature data for '{city1_name}'")
        if not cw2 or "temperature" not in cw2:
            return GroundTruthResult.fail(f"No temperature data for '{city2_name}'")

        temp1 = float(cw1["temperature"])
        temp2 = float(cw2["temperature"])

        if temp1 > temp2:
            return GroundTruthResult.ok(city1_name)
        elif temp2 > temp1:
            return GroundTruthResult.ok(city2_name)
        else:
            # Deterministic tie-break: alphabetically first city
            return GroundTruthResult.ok(min(city1_name, city2_name))

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Not used — the pipeline uses LLM-based validation via get_validation_rules()."""
        return ValidationResult(
            score=0.0, is_correct=False, expected=None, actual=answer,
            details="Use LLM validation",
        )

    def get_ground_truth_trigger(self, validation_info: dict) -> TriggerConfig:
        trigger = UrlPatternTrigger(domains=["open-meteo.com"])
        return TriggerConfig(trigger=trigger)

    @classmethod
    def get_cache_source(cls) -> str:
        return "openmeteo"

    def get_gt_source(self) -> GTSourceType:
        return self.GT_SOURCE
