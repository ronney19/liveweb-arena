"""Hourly extrema template for Open Meteo - MEDIUM DIFFICULTY

Asks for the highest or lowest hourly temperature today in a given city.
The agent must scan hourly forecast data and find the extreme value.

Dynamic data: hourly forecasts update continuously.
Time-sensitive: asks about "today" which changes daily.
Computation required: agent must compare across hours, not read a single value.
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

from .variables import CITIES


PATTERNS_HIGH = [
    "What will be the highest temperature today in {city} according to Open-Meteo?",
    "On Open-Meteo, what is the maximum temperature forecast for {city} today?",
    "Using Open-Meteo, find today's peak temperature in {city}.",
]

PATTERNS_LOW = [
    "What will be the lowest temperature today in {city} according to Open-Meteo?",
    "On Open-Meteo, what is the minimum temperature forecast for {city} today?",
    "Using Open-Meteo, find today's lowest temperature in {city}.",
]


@register_template("openmeteo_hourly_extrema")
class OpenMeteoHourlyExtremaTemplate(QuestionTemplate):
    """
    MEDIUM: Find the highest or lowest hourly temperature today.

    Requires scanning hourly forecast data, not just reading a single value.
    40 cities x 2 extrema x 3 patterns = 240 question variants.
    """

    GT_SOURCE = GTSourceType.PAGE_ONLY

    def __init__(self):
        super().__init__("openmeteo_hourly_extrema")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        is_max = (variant % 2 == 0) if variant is not None else rng.choice([True, False])

        city = rng.choice(CITIES)
        patterns = PATTERNS_HIGH if is_max else PATTERNS_LOW
        question_text = rng.choice(patterns).format(city=city.display_name)

        return GeneratedQuestion(
            question_text=question_text,
            start_url=city.docs_url(),
            variables={"city": city.name, "is_max": is_max},
            validation_info={
                "city_name": city.name,
                "coord_key": city.coord_key,
                "is_max": is_max,
            },
            template_name=self.name,
            expected_steps=5,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        city = validation_info.get("city_name", "")
        is_max = validation_info.get("is_max", True)
        extrema = "maximum (highest)" if is_max else "minimum (lowest)"
        return f"""Task-Specific Rules (Open Meteo Hourly Extrema):
- City: {city}
- Looking for: {extrema} temperature today
- Score 1.0: Value within ±1°C of correct answer
- Score 0.5: Value within ±3°C
- Score 0.0: Wrong value or no answer
- Use the daily max/min from Open-Meteo forecast"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        coord_key = validation_info.get("coord_key", "")
        is_max = validation_info.get("is_max", True)
        city_name = validation_info.get("city_name", "")

        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.fail("No GT collector")

        collected = gt_collector.get_collected_api_data()
        data = collected.get(f"openmeteo:{coord_key}")

        if data is None:
            return GroundTruthResult.not_collected(
                f"Agent did not visit Open Meteo page for '{city_name}'"
            )

        daily = data.get("daily")
        if not daily:
            return GroundTruthResult.fail("No daily data in API response")

        if is_max:
            temps = daily.get("temperature_2m_max")
            if not temps:
                return GroundTruthResult.fail("No temperature_2m_max in daily data")
            value = temps[0]  # Today is index 0
        else:
            temps = daily.get("temperature_2m_min")
            if not temps:
                return GroundTruthResult.fail("No temperature_2m_min in daily data")
            value = temps[0]

        return GroundTruthResult.ok(f"{value}°C")

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
