"""Current weather template for Open Meteo - EASY DIFFICULTY

Asks for a single current weather metric (temperature or wind speed)
for a given city. The agent navigates to the Open Meteo docs page
with pre-filled coordinates and reads the current value.

Dynamic data: current weather updates every 15 minutes.
Large entity pool: 40 cities x 2 metrics = 80 question variants per pattern.
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
}


@register_template("openmeteo_current")
class OpenMeteoCurrentWeatherTemplate(QuestionTemplate):
    """
    EASY: Navigate to Open Meteo, read a single current metric.

    RL value:
    - Form interaction: must navigate an interactive docs page
    - Dynamic data: weather changes every 15 minutes
    - 40 cities x 2 metrics x 3 patterns = 240 question variants
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
            start_url=city.docs_url(),
            variables={"city": city.name, "metric": metric.name},
            validation_info={
                "city_name": city.name,
                "coord_key": city.coord_key,
                "metric_field": metric.api_field,
                "metric_label": metric.display_name,
                "unit": metric.unit,
            },
            template_name=self.name,
            expected_steps=5,
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
- Data source: Open-Meteo weather service (open-meteo.com)"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        coord_key = validation_info.get("coord_key", "")
        metric_field = validation_info.get("metric_field", "")
        unit = validation_info.get("unit", "")
        city_name = validation_info.get("city_name", "")

        gt_collector = get_current_gt_collector()
        if gt_collector is None:
            return GroundTruthResult.fail("No GT collector")

        collected = gt_collector.get_collected_api_data()

        # Look for data keyed by coordinate key (set by gt_collector merge)
        data = collected.get(f"openmeteo:{coord_key}")
        if data is None:
            keys = [k for k in collected if k.startswith("openmeteo:")][:5]
            return GroundTruthResult.not_collected(
                f"Agent did not visit Open Meteo page for '{city_name}'. "
                f"Collected keys: {keys}"
            )

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
