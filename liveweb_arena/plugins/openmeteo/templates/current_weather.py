"""Current weather template for Open Meteo - EASY DIFFICULTY.

Asks for a single current weather metric (temperature, wind speed, or wind
direction) for a given city. The agent starts on the generic Open-Meteo docs
page, searches for the location, and then reads the current value.

Dynamic data: current weather updates every 15 minutes.
Large entity pool: 170 cities x 3 metrics = 510 effective variants (>500).
"""

import random
from typing import Any, Dict, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, TriggerConfig, GroundTruthResult,
)
from liveweb_arena.core.gt_collector import GTSourceType

from .common import DOCS_HOME_URL, get_collected_location_data
from .variables import CITIES, CurrentMetric


PATTERNS = {
    CurrentMetric.TEMPERATURE: [
        "What is the current temperature in {city} according to Open-Meteo?",
        "On Open-Meteo, what is the temperature in {city} right now?",
        "Using the Open-Meteo weather service, find the current temperature in {city}.",
    ],
    CurrentMetric.WIND_SPEED: [
        "What is the current wind speed in {city} according to Open-Meteo?",
        "On Open-Meteo, what is the wind speed in {city} right now?",
        "Using the Open-Meteo weather service, find the current wind speed in {city}.",
    ],
    CurrentMetric.WIND_DIRECTION: [
        "What is the current wind direction in {city} according to Open-Meteo? Answer in degrees.",
        "On Open-Meteo, what direction is the wind blowing in {city} right now? Give the answer in degrees.",
        "Using the Open-Meteo weather service, find the current wind direction in {city} in degrees.",
    ],
}


@register_template("openmeteo_current")
class OpenMeteoCurrentWeatherTemplate(QuestionTemplate):
    """
    EASY: Use location search, then read a single current metric.

    RL value:
    - Form interaction: must search/select a location in the docs UI
    - Dynamic data: weather changes every 15 minutes
    - 170 cities x 3 metrics x 3 patterns = 1530 question variants
    """

    GT_SOURCE = GTSourceType.PAGE_ONLY

    def __init__(self):
        super().__init__("openmeteo_current")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        metrics = list(CurrentMetric)
        metric = metrics[variant % len(metrics)] if variant is not None else rng.choice(metrics)

        city = rng.choice(CITIES)
        pattern = rng.choice(PATTERNS[metric])
        question_text = pattern.format(city=city.display_name)

        return GeneratedQuestion(
            question_text=question_text,
            start_url=DOCS_HOME_URL,
            variables={"city": city.name, "metric": metric.name},
            validation_info={
                "city_name": city.name,
                "coord_key": city.coord_key,
                "metric_field": metric.api_field,
                "metric_label": metric.display_name,
                "unit": metric.unit,
            },
            template_name=self.name,
            expected_steps=6,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        city = validation_info.get("city_name", "")
        label = validation_info.get("metric_label", "")
        unit = validation_info.get("unit", "")
        return f"""Task-Specific Rules (Open Meteo Current Weather):
- City: {city}
- Metric: {label}
- Score 1.0: Value within ±2{unit} of correct answer
- Score 0.5: Value within ±5{unit}
- Score 0.0: Wrong value or no answer
- The answer should reflect the city selected on Open-Meteo, not a guessed climatology
- Data source: Open-Meteo weather service (open-meteo.com)"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        coord_key = validation_info.get("coord_key", "")
        metric_field = validation_info.get("metric_field", "")
        unit = validation_info.get("unit", "")
        city_name = validation_info.get("city_name", "")

        data, failure = get_collected_location_data(coord_key, city_name)
        if failure is not None:
            return failure

        current = data.get("current_weather")
        if not current:
            return GroundTruthResult.fail("No current_weather in API data")

        value = current.get(metric_field)
        if value is None:
            return GroundTruthResult.fail(f"Field '{metric_field}' not in current_weather")

        return GroundTruthResult.ok(f"{value}{unit}")

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
