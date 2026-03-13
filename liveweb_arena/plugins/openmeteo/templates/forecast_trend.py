"""Forecast trend template for Open Meteo - MEDIUM DIFFICULTY

Asks whether tomorrow will be warmer or colder than today in a given city.
Requires comparing daily max temperatures across two forecast days.

Dynamic data: forecasts update continuously.
Time-sensitive: "today" and "tomorrow" change daily.
Computation required: must compare two values, not read a single one.
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


PATTERNS = [
    "According to Open-Meteo, will tomorrow be warmer or colder than today in {city}? By how many degrees?",
    "On Open-Meteo, compare the high temperature today vs tomorrow in {city}. Which day is warmer and by how much?",
    "Using Open-Meteo, is tomorrow's high temperature in {city} higher or lower than today's? What is the difference?",
]


@register_template("openmeteo_forecast_trend")
class OpenMeteoForecastTrendTemplate(QuestionTemplate):
    """
    MEDIUM: Compare today's vs tomorrow's high temperature.

    Requires reading daily forecast for two days and computing the difference.
    40 cities x 3 patterns = 120 question variants.
    """

    GT_SOURCE = GTSourceType.PAGE_ONLY

    def __init__(self):
        super().__init__("openmeteo_forecast_trend")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        city = rng.choice(CITIES)
        question_text = rng.choice(PATTERNS).format(city=city.display_name)

        return GeneratedQuestion(
            question_text=question_text,
            start_url=city.docs_url(),
            variables={"city": city.name},
            validation_info={
                "city_name": city.name,
                "coord_key": city.coord_key,
            },
            template_name=self.name,
            expected_steps=5,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        city = validation_info.get("city_name", "")
        return f"""Task-Specific Rules (Open Meteo Forecast Trend):
- City: {city}
- Compare today's high temperature vs tomorrow's high temperature
- Answer should state: warmer/colder + the degree difference
- Score 1.0: Correct direction (warmer/colder) AND difference within ±1°C
- Score 0.5: Correct direction but difference off by more than 1°C
- Score 0.0: Wrong direction or no answer"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        coord_key = validation_info.get("coord_key", "")
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

        max_temps = daily.get("temperature_2m_max")
        if not max_temps or len(max_temps) < 2:
            return GroundTruthResult.fail("Need at least 2 days of temperature_2m_max")

        today_max = float(max_temps[0])
        tomorrow_max = float(max_temps[1])
        diff = tomorrow_max - today_max

        if diff > 0:
            return GroundTruthResult.ok(
                f"Warmer by {abs(diff):.1f}°C (today: {today_max}°C, tomorrow: {tomorrow_max}°C)"
            )
        elif diff < 0:
            return GroundTruthResult.ok(
                f"Colder by {abs(diff):.1f}°C (today: {today_max}°C, tomorrow: {tomorrow_max}°C)"
            )
        else:
            return GroundTruthResult.ok(
                f"Same temperature ({today_max}°C both days)"
            )

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
