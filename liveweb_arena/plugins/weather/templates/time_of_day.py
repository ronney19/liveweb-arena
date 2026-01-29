"""Time-of-day weather query template"""

import random
from typing import Any, Dict, Optional

from liveweb_arena.core.validators.base import QuestionTemplate, GeneratedQuestion, ValidationResult, register_template
from liveweb_arena.core.validators.validators import NumericToleranceValidator
from liveweb_arena.core.ground_truth_trigger import UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
from .variables import (
    LocationVariable, DateVariable, WeatherMetricVariable, TimeOfDayVariable,
    LocationType, MetricType, DateType,
    LocationSpec, DateSpec, MetricSpec, TimeOfDaySpec,
)


@register_template("time_of_day")
class TimeOfDayWeatherTemplate(QuestionTemplate):
    """
    Template for time-of-day specific weather queries.

    Different from other templates by focusing on specific time periods within a day.

    Examples:
    - What will the temperature be tomorrow morning in Tokyo?
    - How windy will it be this evening in London?
    - What's the humidity tomorrow afternoon in New York?
    """

    def __init__(self):
        super().__init__("time_of_day")

        self.register_variable(LocationVariable(allowed_types=[LocationType.CITY_NAME]))
        self.register_variable(DateVariable(
            max_forecast_days=2,
            allowed_types=[DateType.TODAY, DateType.TOMORROW],
        ))
        self.register_variable(TimeOfDayVariable())
        self.register_variable(WeatherMetricVariable(allowed_metrics=[
            MetricType.TEMPERATURE,
            MetricType.FEELS_LIKE,
            MetricType.WIND_SPEED,
            MetricType.HUMIDITY,
        ]))

        # Register validators
        for metric_type in [MetricType.TEMPERATURE, MetricType.FEELS_LIKE]:
            spec = WeatherMetricVariable.METRICS[metric_type]
            self.register_validator(metric_type.value, NumericToleranceValidator(
                full_tolerance=spec.full_tolerance,
                partial_tolerance=spec.partial_tolerance,
                unit=spec.unit,
            ))
        for metric_type in [MetricType.WIND_SPEED, MetricType.HUMIDITY]:
            spec = WeatherMetricVariable.METRICS[metric_type]
            self.register_validator(metric_type.value, NumericToleranceValidator(
                full_tolerance=spec.full_tolerance,
                partial_tolerance=spec.partial_tolerance,
                unit=spec.unit,
            ))

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """
        Generate a time-of-day weather question.

        Args:
            seed: Random seed for reproducible generation
            variant: Optional variant index (0-3) for selecting specific metric type.
                     0=temperature, 1=feels_like, 2=wind_speed, 3=humidity
        """
        rng = random.Random(seed)

        location: LocationSpec = self._variables["location"].sample(rng)
        date: DateSpec = self._variables["date"].sample(rng)
        time_of_day: TimeOfDaySpec = self._variables["time_of_day"].sample(rng)

        # Use variant to select specific metric type if provided
        if variant is not None:
            metric: MetricSpec = self._variables["metric"].sample_by_index(variant)
        else:
            metric: MetricSpec = self._variables["metric"].sample(rng)

        # Build question
        question_text = self._build_question(location, date, time_of_day, metric, rng)

        validation_info = {
            "location": location.api_query,
            "target_date": date.api_date,  # Absolute date for timezone-safe matching
            "time_of_day": time_of_day.time_of_day.value,
            "hourly_indices": time_of_day.hourly_indices,
            "metric_type": metric.metric_type.value,
            "api_field": metric.api_field,
            "unit": metric.unit,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=f"https://wttr.in/{location.api_query}",
            variables={"location": location, "date": date, "time_of_day": time_of_day, "metric": metric},
            validation_info=validation_info,
            template_name=self.name,
        )

    def _build_question(
        self,
        location: LocationSpec,
        date: DateSpec,
        time_of_day: TimeOfDaySpec,
        metric: MetricSpec,
        rng: random.Random,
    ) -> str:
        patterns = [
            "What will the {metric} be {date} {time} in {location}?",
            "What is the {metric} {date} {time} in {location}?",
            "How {metric_adj} will it be {date} {time} in {location}?",
        ]

        # Use adjective form for some metrics
        metric_adj_map = {
            "temperature": "warm",
            "feels_like": "warm (feels like)",
            "wind_speed": "windy",
            "humidity": "humid",
        }
        metric_adj = metric_adj_map.get(metric.metric_type.value, metric.display_name)

        pattern = rng.choice(patterns)
        return pattern.format(
            metric=metric.display_name,
            metric_adj=metric_adj,
            date=date.display_text,
            time=time_of_day.display_name,
            location=location.display_name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        metric_type = validation_info.get("metric_type", "")
        time_of_day = validation_info.get("time_of_day", "")

        if "temp" in metric_type.lower():
            return f"""Task-Specific Rules (Time-of-Day Weather - Temperature):
- Question asks for {time_of_day} temperature (averaged over that period)
- Score 1.0: Values match within 2°C
- Score 0.0: Difference exceeds 2°C"""

        return f"""Task-Specific Rules (Time-of-Day Weather):
- Question asks for {time_of_day} {metric_type}
- Score 1.0: Values match within tolerance
- Score 0.0: Values differ significantly"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Get ground truth from collected API data (no network fallback)."""
        location = validation_info["location"]
        target_date = validation_info["target_date"]  # YYYY-MM-DD format
        hourly_indices = validation_info["hourly_indices"]
        api_field = validation_info["api_field"]
        unit = validation_info.get("unit", "")

        # Get data from collected API data only
        data = None
        from liveweb_arena.core.gt_collector import get_current_gt_collector
        gt_collector = get_current_gt_collector()
        if gt_collector is not None:
            collected = gt_collector.get_collected_api_data()
            city_name = location.split(",")[0].strip() if "," in location else location
            variants = [
                location, city_name,
                city_name.replace('+', ' '), location.replace('+', ' '),
            ]
            for loc_key in variants:
                if loc_key in collected:
                    data = collected[loc_key]
                    break

        if data is None:
            return GroundTruthResult.fail(f"Weather data for '{location}' not collected")

        weather = data.get("weather", [])

        # Find day by date (timezone-safe) instead of using array index
        day_data = None
        for day in weather:
            if day.get("date") == target_date:
                day_data = day
                break

        if day_data is None:
            return GroundTruthResult.fail(f"No data for date: {target_date}")

        hourly = day_data.get("hourly", [])

        # Average values across the time period
        values = []
        for idx in hourly_indices:
            if idx < len(hourly):
                val = hourly[idx].get(api_field)
                if val is not None:
                    values.append(float(val))

        if not values:
            return GroundTruthResult.fail(f"No hourly data for {api_field}")

        avg_value = sum(values) / len(values)
        result = f"{avg_value:.0f}{unit}" if unit else f"{avg_value:.0f}"
        return GroundTruthResult.ok(result)

    async def validate_answer(self, answer: str, validation_info: Dict[str, Any]) -> ValidationResult:
        result = await self.get_ground_truth(validation_info)

        if not result.success:
            return ValidationResult(
                score=0.0, is_correct=False, expected=None, actual=answer,
                details=f"Ground truth unavailable: {result.error}",
            )

        metric_type = validation_info["metric_type"]
        validator = self._validators.get(metric_type)

        if validator is None:
            validator = NumericToleranceValidator(2, 5, validation_info.get("unit", ""))

        return validator.validate(answer, result.value)

    def get_ground_truth_trigger(self, validation_info: Dict[str, Any]) -> tuple:
        """
        Time-of-day weather: fetch when AI visits the specific location's page.

        Uses city name for URL matching (AI may use short URLs).

        Strategy: FIRST - weather data is stable within a single session.
        """
        location = validation_info.get("location", "")
        # Extract city name for flexible matching
        city_name = location.split(",")[0].strip() if location else ""
        trigger = UrlPatternTrigger(
            domains=["wttr.in"],
            url_contains=city_name if city_name else None,
        )
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)

    @classmethod
    def get_cache_source(cls) -> str:
        """Return the cache source name for this template."""
        return "weather"

