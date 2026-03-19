"""Hourly extrema template for Open Meteo - MEDIUM DIFFICULTY.

Asks for the highest or lowest hourly value of a given metric today in a city.
The agent starts on the generic docs page, finds the city, then scans the
hourly forecast series to locate the extreme value.

Dynamic data: hourly forecasts update continuously.
Time-sensitive: asks about "today" which changes daily.
Computation required: agent must compare across hours, not read a single value.

Effective variants: 170 cities x 4 metrics x 2 extrema = 1360 (>500).
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

from .common import DOCS_HOME_URL, get_collected_location_data, get_today_hourly_series
from .variables import CITIES, HourlyMetric


PATTERNS_HIGH = {
    HourlyMetric.TEMPERATURE: [
        "What is the highest hourly temperature forecast for {city} today on Open-Meteo?",
        "Using Open-Meteo, find today's peak hourly temperature in {city}.",
        "On Open-Meteo, what is the warmest hourly temperature expected in {city} today?",
    ],
    HourlyMetric.HUMIDITY: [
        "What is the highest hourly relative humidity forecast for {city} today on Open-Meteo?",
        "Using Open-Meteo, find today's peak hourly humidity in {city}.",
    ],
    HourlyMetric.WIND_SPEED: [
        "What is the highest hourly wind speed forecast for {city} today on Open-Meteo?",
        "Using Open-Meteo, find today's peak hourly wind speed in {city}.",
    ],
    HourlyMetric.PRECIP_PROBABILITY: [
        "What is the highest hourly precipitation probability forecast for {city} today on Open-Meteo?",
        "Using Open-Meteo, find today's peak precipitation probability in {city}.",
    ],
}

PATTERNS_LOW = {
    HourlyMetric.TEMPERATURE: [
        "What is the lowest hourly temperature forecast for {city} today on Open-Meteo?",
        "Using Open-Meteo, find today's lowest hourly temperature in {city}.",
        "On Open-Meteo, what is the coolest hourly temperature expected in {city} today?",
    ],
    HourlyMetric.HUMIDITY: [
        "What is the lowest hourly relative humidity forecast for {city} today on Open-Meteo?",
        "Using Open-Meteo, find today's lowest hourly humidity in {city}.",
    ],
    HourlyMetric.WIND_SPEED: [
        "What is the lowest hourly wind speed forecast for {city} today on Open-Meteo?",
        "Using Open-Meteo, find today's lowest hourly wind speed in {city}.",
    ],
    HourlyMetric.PRECIP_PROBABILITY: [
        "What is the lowest hourly precipitation probability forecast for {city} today on Open-Meteo?",
        "Using Open-Meteo, find today's lowest precipitation probability in {city}.",
    ],
}


@register_template("openmeteo_hourly_extrema")
class OpenMeteoHourlyExtremaTemplate(QuestionTemplate):
    """
    MEDIUM: Find the highest or lowest hourly metric value today.

    Requires scanning hourly forecast data, not just reading a single value.
    170 cities x 4 metrics x 2 extrema = 1360 effective variants.
    """

    GT_SOURCE = GTSourceType.PAGE_ONLY

    def __init__(self):
        super().__init__("openmeteo_hourly_extrema")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        metrics = list(HourlyMetric)
        metric = metrics[variant % len(metrics)] if variant is not None else rng.choice(metrics)
        is_max = rng.choice([True, False])

        city = rng.choice(CITIES)
        patterns = PATTERNS_HIGH[metric] if is_max else PATTERNS_LOW[metric]
        question_text = rng.choice(patterns).format(city=city.display_name)

        return GeneratedQuestion(
            question_text=question_text,
            start_url=DOCS_HOME_URL,
            variables={"city": city.name, "is_max": is_max, "metric": metric.name},
            validation_info={
                "city_name": city.name,
                "coord_key": city.coord_key,
                "is_max": is_max,
                "metric_field": metric.api_field,
                "metric_label": metric.display_name,
                "unit": metric.unit,
            },
            template_name=self.name,
            expected_steps=7,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        city = validation_info.get("city_name", "")
        is_max = validation_info.get("is_max", True)
        label = validation_info.get("metric_label", "hourly temperature")
        unit = validation_info.get("unit", "°C")
        extrema = "maximum (highest)" if is_max else "minimum (lowest)"
        return f"""Task-Specific Rules (Open Meteo Hourly Extrema):
- City: {city}
- Looking for: {extrema} {label} today
- Score 1.0: Value within ±1{unit} of correct answer
- Score 0.5: Value within ±3{unit}
- Score 0.0: Wrong value or no answer
- Use the hourly forecast for today's local date, not the daily summary"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        coord_key = validation_info.get("coord_key", "")
        is_max = validation_info.get("is_max", True)
        city_name = validation_info.get("city_name", "")
        metric_field = validation_info.get("metric_field", "temperature_2m")
        unit = validation_info.get("unit", "°C")

        data, failure = get_collected_location_data(coord_key, city_name)
        if failure is not None:
            return failure

        values, val_failure = get_today_hourly_series(data, metric_field)
        if val_failure is not None:
            return val_failure

        value = max(values) if is_max else min(values)
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
