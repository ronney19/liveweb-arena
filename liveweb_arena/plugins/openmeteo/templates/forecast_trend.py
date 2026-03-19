"""Forecast trend template for Open Meteo - MEDIUM DIFFICULTY.

Asks whether a daily metric will be higher or lower on one day vs another
in a given city. The agent starts on the generic docs page, finds the
location, then compares the relevant daily values.

Dynamic data: forecasts update continuously.
Time-sensitive: day references change daily.
Computation required: must compare two values, not read a single one.

Effective variants: 170 cities x 3 metrics x 3 day-pairs = 1530 (>500).
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
from .variables import CITIES, DailyMetric


DAY_PAIRS = [
    (0, 1, "today", "tomorrow"),
    (0, 2, "today", "the day after tomorrow"),
    (1, 2, "tomorrow", "the day after tomorrow"),
]

PATTERNS = [
    "According to Open-Meteo, will {day2}'s {metric_label} in {city} be higher or lower than {day1}'s? By how many {unit}?",
    "On Open-Meteo, compare the {metric_label} {day1} vs {day2} in {city}. Which day has a higher value and by how much?",
    "Using Open-Meteo, is {day2}'s {metric_label} in {city} higher or lower than {day1}'s? What is the difference in {unit}?",
]


@register_template("openmeteo_forecast_trend")
class OpenMeteoForecastTrendTemplate(QuestionTemplate):
    """
    MEDIUM: Compare a daily metric across two days.

    Requires reading daily forecast for two days and computing the difference.
    170 cities x 3 metrics x 3 day-pairs = 1530 effective variants.
    """

    GT_SOURCE = GTSourceType.PAGE_ONLY

    def __init__(self):
        super().__init__("openmeteo_forecast_trend")

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        rng = random.Random(seed)

        city = rng.choice(CITIES)

        metrics = list(DailyMetric)
        metric = rng.choice(metrics)

        day_pair = rng.choice(DAY_PAIRS)
        idx1, idx2, day1_label, day2_label = day_pair

        pattern = rng.choice(PATTERNS)
        question_text = pattern.format(
            city=city.display_name,
            metric_label=metric.display_name,
            day1=day1_label,
            day2=day2_label,
            unit=metric.unit,
        )

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
                "day1_idx": idx1,
                "day2_idx": idx2,
                "day1_label": day1_label,
                "day2_label": day2_label,
            },
            template_name=self.name,
            expected_steps=7,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        city = validation_info.get("city_name", "")
        label = validation_info.get("metric_label", "daily maximum temperature")
        unit = validation_info.get("unit", "°C")
        day1 = validation_info.get("day1_label", "today")
        day2 = validation_info.get("day2_label", "tomorrow")
        return f"""Task-Specific Rules (Open Meteo Forecast Trend):
- City: {city}
- Compare {day1}'s {label} vs {day2}'s {label}
- Answer should state: higher/lower + the difference in {unit}
- Score 1.0: Correct direction (higher/lower) AND difference within ±1{unit}
- Score 0.5: Correct direction but difference off by more than 1{unit}
- Score 0.0: Wrong direction or no answer"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        coord_key = validation_info.get("coord_key", "")
        city_name = validation_info.get("city_name", "")
        metric_field = validation_info.get("metric_field", "temperature_2m_max")
        unit = validation_info.get("unit", "°C")
        idx1 = validation_info.get("day1_idx", 0)
        idx2 = validation_info.get("day2_idx", 1)
        day1_label = validation_info.get("day1_label", "today")
        day2_label = validation_info.get("day2_label", "tomorrow")

        data, failure = get_collected_location_data(coord_key, city_name)
        if failure is not None:
            return failure

        daily = data.get("daily")
        if not daily:
            return GroundTruthResult.fail("No daily data in API response")

        values = daily.get(metric_field)
        if not values or len(values) <= max(idx1, idx2):
            return GroundTruthResult.fail(
                f"Need at least {max(idx1, idx2) + 1} days of {metric_field}"
            )

        val1 = float(values[idx1])
        val2 = float(values[idx2])
        diff = val2 - val1

        if diff > 0:
            return GroundTruthResult.ok(
                f"Higher by {abs(diff):.1f}{unit} ({day1_label}: {val1}{unit}, {day2_label}: {val2}{unit})"
            )
        if diff < 0:
            return GroundTruthResult.ok(
                f"Lower by {abs(diff):.1f}{unit} ({day1_label}: {val1}{unit}, {day2_label}: {val2}{unit})"
            )
        return GroundTruthResult.ok(
            f"Same value ({val1}{unit} both days)"
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
