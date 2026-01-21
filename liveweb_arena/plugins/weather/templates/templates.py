"""Weather question templates for wttr.in"""

import random
from typing import Any, Dict, List, Optional

import httpx

from liveweb_arena.core.validators.base import QuestionTemplate, GeneratedQuestion, ValidationResult, register_template
from liveweb_arena.core.validators.validators import NumericToleranceValidator, BooleanValidator, ExactMatchValidator
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig
)
from .variables import (
    LocationVariable, DateVariable, WeatherMetricVariable,
    LocationType, MetricType,
    LocationSpec, DateSpec, MetricSpec,
)


@register_template("location_name")
class LocationNameWeatherTemplate(QuestionTemplate):
    """
    Question template for location name-based weather queries.

    Examples:
    - What is the temperature in Washington tomorrow?
    - How windy will it be in Berlin next Monday?
    - Will it rain in New York in the next 3 days?
    """

    # Question patterns with placeholders
    QUESTION_PATTERNS = [
        # Temperature questions
        "What is the {metric} in {location} {date}?",
        "What will be the {metric} in {location} {date}?",
        "How hot/cold will it be in {location} {date}?",

        # Numeric metric questions
        "What is the {metric} in {location} {date}?",
        "How much {metric} will there be in {location} {date}?",

        # Boolean questions
        "Will it rain in {location} {date}?",
        "Is there a chance of rain in {location} {date}?",
    ]

    QUESTION_PATTERNS_ZH = [
        "{location}{date}的{metric}是多少？",
        "{date}{location}的{metric}会是多少？",
        "{location}{date}会下雨吗？",
        "{date}{location}的天气怎么样？",
    ]

    def __init__(
        self,
        use_chinese: bool = False,
        allowed_metrics: List[MetricType] = None,
        regions: List[str] = None,
    ):
        """
        Initialize location name weather template.

        Args:
            use_chinese: Use Chinese question patterns
            allowed_metrics: Metrics to use (default: temperature, wind, rain)
            regions: Geographic regions to sample cities from
        """
        super().__init__("location_name")
        self.use_chinese = use_chinese

        # Register variables
        self.register_variable(LocationVariable(
            allowed_types=[LocationType.CITY_NAME],  # Only city names for this template
            regions=regions,
        ))
        self.register_variable(DateVariable(
            max_forecast_days=2,  # wttr.in only provides 3 days (0, 1, 2)
            use_chinese=use_chinese,
        ))
        self.register_variable(WeatherMetricVariable(
            allowed_metrics=allowed_metrics or [
                MetricType.TEMPERATURE,
                MetricType.TEMPERATURE_HIGH,
                MetricType.TEMPERATURE_LOW,
                MetricType.WIND_SPEED,
                MetricType.HUMIDITY,
                MetricType.PRECIPITATION_CHANCE,
                MetricType.HAS_RAIN,
            ]
        ))

        # Register validators for each metric type
        self._setup_validators()

    def _setup_validators(self):
        """Setup validators for each metric type"""
        # Numeric metrics with tolerance
        for metric_type in [
            MetricType.TEMPERATURE, MetricType.TEMPERATURE_HIGH,
            MetricType.TEMPERATURE_LOW, MetricType.FEELS_LIKE,
        ]:
            spec = WeatherMetricVariable.METRICS[metric_type]
            self.register_validator(
                metric_type.value,
                NumericToleranceValidator(
                    full_tolerance=spec.full_tolerance,
                    partial_tolerance=spec.partial_tolerance,
                    unit=spec.unit,
                )
            )

        for metric_type in [
            MetricType.HUMIDITY, MetricType.WIND_SPEED,
            MetricType.PRECIPITATION_CHANCE, MetricType.CLOUD_COVER,
        ]:
            spec = WeatherMetricVariable.METRICS[metric_type]
            self.register_validator(
                metric_type.value,
                NumericToleranceValidator(
                    full_tolerance=spec.full_tolerance,
                    partial_tolerance=spec.partial_tolerance,
                    unit=spec.unit,
                )
            )

        # Boolean validator for rain questions
        self.register_validator(
            MetricType.HAS_RAIN.value,
            BooleanValidator()
        )

        # Exact match for conditions
        self.register_validator(
            MetricType.CONDITION.value,
            ExactMatchValidator(case_sensitive=False)
        )

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """
        Generate a weather question using the given seed.

        Args:
            seed: Random seed for reproducible generation
            variant: Optional variant index (0-6) for selecting specific metric type.
                     0=temperature, 1=temperature_high, 2=temperature_low,
                     3=wind_speed, 4=humidity, 5=precipitation_chance, 6=has_rain
        """
        rng = random.Random(seed)

        # Sample variables
        location_var = self._variables["location"]
        date_var = self._variables["date"]
        metric_var = self._variables["metric"]

        location: LocationSpec = location_var.sample(rng)
        date: DateSpec = date_var.sample(rng)

        # Use variant to select specific metric type if provided
        if variant is not None:
            metric: MetricSpec = metric_var.sample_by_index(variant)
        else:
            metric: MetricSpec = metric_var.sample(rng)

        # Build question text
        question_text = self._build_question(location, date, metric, rng)

        # Build start URL
        start_url = f"https://wttr.in/{location.api_query}"

        # Build validation info
        validation_info = {
            "location": location.api_query,
            "date": date.api_date,
            "forecast_day": date.forecast_day,
            "metric_type": metric.metric_type.value,
            "api_field": metric.api_field,
            "is_boolean": metric.is_boolean,
            "full_tolerance": metric.full_tolerance,
            "partial_tolerance": metric.partial_tolerance,
            "unit": metric.unit,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=start_url,
            variables={
                "location": location,
                "date": date,
                "metric": metric,
            },
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        """Get weather-specific validation rules"""
        metric_type = validation_info.get("metric_type", "")
        is_boolean = validation_info.get("is_boolean", False)

        if is_boolean:
            return """Task-Specific Rules (Weather - Yes/No Question):
- Score 1.0: Both answers are Yes, or both are No
- Score 0.0: Answers disagree (Yes vs No)"""

        if "temp" in metric_type.lower():
            return """Task-Specific Rules (Weather - Temperature):
- Score 1.0: Values match within 2°C
- Score 0.0: Difference exceeds 2°C"""

        if "chance" in metric_type.lower() or "percent" in metric_type.lower():
            return """Task-Specific Rules (Weather - Percentage):
- Score 1.0: Numeric values match exactly OR differ by at most 5%
- Score 0.0: Difference exceeds 5%"""

        # Default for other weather metrics
        return """Task-Specific Rules (Weather):
- Score 1.0: Numeric values match exactly OR differ by at most 10%
- Score 0.0: Difference exceeds 10%"""

    def _build_question(
        self,
        location: LocationSpec,
        date: DateSpec,
        metric: MetricSpec,
        rng: random.Random,
    ) -> str:
        """Build natural language question"""
        patterns = self.QUESTION_PATTERNS_ZH if self.use_chinese else self.QUESTION_PATTERNS

        # Select appropriate pattern based on metric type
        if metric.is_boolean:
            # Use boolean question patterns
            if self.use_chinese:
                pattern = "{location}{date}会下雨吗？"
            else:
                pattern = rng.choice([
                    "Will it rain in {location} {date}?",
                    "Is there a chance of rain in {location} {date}?",
                ])
        else:
            # Use regular metric question patterns
            if self.use_chinese:
                pattern = "{location}{date}的{metric}是多少？"
            else:
                pattern = rng.choice([
                    "What is the {metric} in {location} {date}?",
                    "What will the {metric} be in {location} {date}?",
                ])

        return pattern.format(
            location=location.display_name,
            date=date.display_text,
            metric=metric.display_name,
        )

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Any:
        """Fetch ground truth from wttr.in API"""
        location = validation_info["location"]
        forecast_day = validation_info["forecast_day"]
        api_field = validation_info["api_field"]
        is_boolean = validation_info.get("is_boolean", False)
        unit = validation_info.get("unit", "")

        url = f"https://wttr.in/{location}?format=j1"

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

        weather = data.get("weather", [])
        value = None

        # For metrics shown on HTML page, use only the 4 displayed time slots
        # HTML shows Morning(9:00)/Noon(12:00)/Evening(18:00)/Night(21:00) = indices 3,4,6,7
        display_indices = [3, 4, 6, 7]

        if api_field in ("maxtempC", "mintempC") and forecast_day < len(weather):
            day_data = weather[forecast_day]
            hourly = day_data.get("hourly", [])
            if hourly and len(hourly) >= 8:
                temps = [int(hourly[i].get("tempC", 0)) for i in display_indices if hourly[i].get("tempC")]
                if temps:
                    value = max(temps) if api_field == "maxtempC" else min(temps)
        elif api_field == "chanceofrain" and forecast_day < len(weather):
            # For chance of rain, use MAX of displayed time slots (if any slot has 100%, it will rain)
            day_data = weather[forecast_day]
            hourly = day_data.get("hourly", [])
            if hourly and len(hourly) >= 8:
                chances = [int(hourly[i].get("chanceofrain", 0)) for i in display_indices]
                if chances:
                    value = max(chances)
        elif forecast_day == 0:
            # Current conditions
            current = data.get("current_condition", [{}])[0]
            value = current.get(api_field)

            # If not in current, check today's forecast
            if value is None and weather:
                value = weather[0].get(api_field)
        else:
            # Future forecast
            if forecast_day < len(weather):
                day_data = weather[forecast_day]
                value = day_data.get(api_field)

                # Some fields are in hourly data
                if value is None:
                    hourly = day_data.get("hourly", [])
                    if hourly:
                        # Average over hourly values
                        values = [float(h.get(api_field, 0)) for h in hourly if h.get(api_field)]
                        if values:
                            value = sum(values) / len(values)

        # Convert boolean for rain questions
        if is_boolean and value is not None:
            # Chance of rain > 30% means "will rain"
            return "Yes" if float(value) > 30 else "No"

        # Return value with unit for better AI validation
        if value is not None and unit:
            return f"{value}{unit}"
        return value

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate answer against real-time ground truth"""
        try:
            ground_truth = await self.get_ground_truth(validation_info)
        except Exception as e:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Failed to get ground truth: {e}",
            )

        # Get appropriate validator
        metric_type = validation_info["metric_type"]
        validator = self._validators.get(metric_type)

        if validator is None:
            # Default to numeric tolerance
            validator = NumericToleranceValidator(
                full_tolerance=validation_info.get("full_tolerance", 2),
                partial_tolerance=validation_info.get("partial_tolerance", 5),
                unit=validation_info.get("unit", ""),
            )

        return validator.validate(answer, ground_truth)

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> tuple:
        """
        Weather template: fetch when AI visits the specific location's page.

        Uses city name (first part of location) for URL matching since AI
        often visits shorter URLs like "wttr.in/Nairobi" instead of
        "wttr.in/Nairobi,Kenya".

        Strategy: FIRST - weather data is stable within a single session.
        """
        location = validation_info.get("location", "")
        # Extract city name (first part before comma) for more flexible matching
        # e.g., "Nairobi,Kenya" -> "Nairobi"
        city_name = location.split(",")[0].strip() if location else ""
        trigger = UrlPatternTrigger(
            domains=["wttr.in"],
            url_contains=city_name if city_name else None,
        )
        return TriggerConfig(trigger=trigger, strategy=FetchStrategy.FIRST)


class MultiDayQuestionType:
    """Question types for multi-day weather queries"""
    AVERAGE = "average"  # Ask for average value across days
    DAILY = "daily"      # Ask for each day's value separately


@register_template("multi_day")
class MultiDayWeatherTemplate(QuestionTemplate):
    """
    Question template for multi-day weather queries.

    Supports two question types:
    - AVERAGE: "What is the average high temperature over the next 3 days?" → "19.5°C"
    - DAILY: "What are the high temperatures for each of the next 3 days?" → "Day 1: 19°C, Day 2: 20°C, Day 3: 18°C"

    Examples:
    - Will it rain in New York at any point in the next 3 days?
    - What is the average high temperature in London over the next 2 days?
    - What are the high temperatures for each day in Tokyo over the next 3 days?
    """

    def __init__(self, use_chinese: bool = False):
        super().__init__("multi_day")
        self.use_chinese = use_chinese

        # Register variables
        self.register_variable(LocationVariable(
            allowed_types=[LocationType.CITY_NAME],
        ))
        self.register_variable(WeatherMetricVariable(
            allowed_metrics=[
                MetricType.HAS_RAIN,
                MetricType.TEMPERATURE_HIGH,
                MetricType.TEMPERATURE_LOW,
            ]
        ))

        # Register validators
        self.register_validator(
            MetricType.HAS_RAIN.value,
            BooleanValidator()
        )

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """
        Generate a multi-day weather question.

        Args:
            seed: Random seed for reproducible generation
            variant: Optional variant index for deterministic question type selection.
                     0: HAS_RAIN (boolean)
                     1: TEMPERATURE_HIGH + AVERAGE
                     2: TEMPERATURE_HIGH + DAILY
                     3: TEMPERATURE_LOW + AVERAGE
                     4: TEMPERATURE_LOW + DAILY
        """
        rng = random.Random(seed)

        location_var = self._variables["location"]
        metric_var = self._variables["metric"]

        location: LocationSpec = location_var.sample(rng)

        # Sample number of days (2-3, limited by wttr.in's 3-day forecast)
        num_days = rng.randint(2, 3)

        # Use variant to select specific metric and question type if provided
        if variant is not None:
            variant = variant % 5  # 5 variants total
            if variant == 0:
                metric = metric_var.METRICS[MetricType.HAS_RAIN]
                question_type = None
            elif variant == 1:
                metric = metric_var.METRICS[MetricType.TEMPERATURE_HIGH]
                question_type = MultiDayQuestionType.AVERAGE
            elif variant == 2:
                metric = metric_var.METRICS[MetricType.TEMPERATURE_HIGH]
                question_type = MultiDayQuestionType.DAILY
            elif variant == 3:
                metric = metric_var.METRICS[MetricType.TEMPERATURE_LOW]
                question_type = MultiDayQuestionType.AVERAGE
            else:  # variant == 4
                metric = metric_var.METRICS[MetricType.TEMPERATURE_LOW]
                question_type = MultiDayQuestionType.DAILY
        else:
            metric: MetricSpec = metric_var.sample(rng)
            # For non-boolean metrics, randomly choose between AVERAGE and DAILY
            if metric.is_boolean:
                question_type = None  # Boolean questions have their own format
            else:
                question_type = rng.choice([MultiDayQuestionType.AVERAGE, MultiDayQuestionType.DAILY])

        # Build question with clear semantics
        question_text = self._build_question(location, metric, num_days, question_type)

        start_url = f"https://wttr.in/{location.api_query}"

        validation_info = {
            "location": location.api_query,
            "num_days": num_days,
            "metric_type": metric.metric_type.value,
            "api_field": metric.api_field,
            "is_boolean": metric.is_boolean,
            "question_type": question_type,  # AVERAGE, DAILY, or None for boolean
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=start_url,
            variables={
                "location": location,
                "metric": metric,
                "num_days": num_days,
                "question_type": question_type,
            },
            validation_info=validation_info,
            template_name=self.name,
        )

    def _build_question(
        self,
        location: LocationSpec,
        metric: MetricSpec,
        num_days: int,
        question_type: str,
    ) -> str:
        """Build question text based on type"""
        if metric.is_boolean:
            # Boolean: "Will it rain at any point during the next N days?"
            if self.use_chinese:
                return f"{location.display_name}未来{num_days}天内会下雨吗？"
            else:
                return f"Will it rain in {location.display_name} at any point in the next {num_days} days?"

        if question_type == MultiDayQuestionType.AVERAGE:
            # Average: "What is the average X over the next N days?"
            if self.use_chinese:
                return f"{location.display_name}未来{num_days}天的平均{metric.display_name}是多少？"
            else:
                return f"What is the average {metric.display_name} in {location.display_name} over the next {num_days} days?"

        else:  # DAILY
            # Daily: "What are the X values for each of the next N days?"
            if self.use_chinese:
                return f"{location.display_name}未来{num_days}天每天的{metric.display_name}分别是多少？"
            else:
                return f"What are the {metric.display_name}s for each of the next {num_days} days in {location.display_name}?"

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        """Get multi-day weather-specific validation rules"""
        is_boolean = validation_info.get("is_boolean", False)
        question_type = validation_info.get("question_type")
        metric_type = validation_info.get("metric_type", "")
        num_days = validation_info.get("num_days", 2)

        if is_boolean:
            return """Task-Specific Rules (Multi-Day Weather - Yes/No Question):
- The question asks if it will rain at ANY point during the period
- Score 1.0: Both answers agree (both Yes or both No)
- Score 0.0: Answers disagree"""

        if question_type == MultiDayQuestionType.AVERAGE:
            tolerance = "2°C" if "temp" in metric_type.lower() else "10%"
            return f"""Task-Specific Rules (Multi-Day Weather - Average Value):
- The question asks for the AVERAGE value over {num_days} days
- Expected answer is a single averaged value
- Score 1.0: Values match exactly OR differ by at most {tolerance}
- Score 0.0: Difference exceeds {tolerance}"""

        else:  # DAILY
            tolerance = "2°C" if "temp" in metric_type.lower() else "10%"
            return f"""Task-Specific Rules (Multi-Day Weather - Daily Values):
- The question asks for EACH DAY's value separately over {num_days} days
- Expected answer lists {num_days} values, one per day
- Score 1.0: All {num_days} daily values match (within {tolerance} each)
- Score 0.5: Some values match, some differ
- Score 0.0: Most values are wrong or answer format is completely different
- Compare each day's value independently"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Any:
        """Fetch ground truth for multi-day query"""
        location = validation_info["location"]
        num_days = validation_info["num_days"]
        api_field = validation_info["api_field"]
        is_boolean = validation_info.get("is_boolean", False)
        question_type = validation_info.get("question_type")

        url = f"https://wttr.in/{location}?format=j1"

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

        weather = data.get("weather", [])

        if is_boolean:
            # Check if any day has rain
            for i in range(min(num_days, len(weather))):
                day = weather[i]
                hourly = day.get("hourly", [])
                for h in hourly:
                    chance = float(h.get("chanceofrain", 0))
                    if chance > 30:
                        return "Yes"
            return "No"

        # Collect daily values
        daily_values = []
        daily_dates = []
        for i in range(min(num_days, len(weather))):
            day_data = weather[i]
            date_str = day_data.get("date", f"Day {i+1}")

            # Use only the 4 time slots shown on HTML: indices 3(9:00), 4(12:00), 6(18:00), 7(21:00)
            display_indices = [3, 4, 6, 7]
            hourly = day_data.get("hourly", [])

            if api_field in ("maxtempC", "mintempC"):
                if hourly and len(hourly) >= 8:
                    temps = [int(hourly[idx].get("tempC", 0)) for idx in display_indices if hourly[idx].get("tempC")]
                    if temps:
                        val = max(temps) if api_field == "maxtempC" else min(temps)
                    else:
                        val = day_data.get(api_field)
                else:
                    val = day_data.get(api_field)
            elif api_field == "chanceofrain":
                if hourly and len(hourly) >= 8:
                    chances = [int(hourly[idx].get("chanceofrain", 0)) for idx in display_indices]
                    val = max(chances) if chances else day_data.get(api_field)
                else:
                    val = day_data.get(api_field)
            else:
                val = day_data.get(api_field)

            if val is not None:
                daily_values.append(float(val))
                daily_dates.append(date_str)

        if not daily_values:
            return None

        metric_type = validation_info.get("metric_type", "")
        unit = "°C" if "temp" in metric_type.lower() else ""

        if question_type == MultiDayQuestionType.AVERAGE:
            # Return single average value
            avg = sum(daily_values) / len(daily_values)
            return f"{avg:.1f}{unit}" if unit else avg
        else:
            # Return list of daily values with dates
            # Format: "2026-01-14: 19°C, 2026-01-15: 20°C"
            parts = []
            for date, val in zip(daily_dates, daily_values):
                parts.append(f"{date}: {int(val)}{unit}")
            return ", ".join(parts)

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate answer for multi-day query"""
        try:
            ground_truth = await self.get_ground_truth(validation_info)
        except Exception as e:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details=f"Failed to get ground truth: {e}",
            )

        metric_type = validation_info["metric_type"]
        validator = self._validators.get(metric_type)

        if validator is None:
            if validation_info.get("is_boolean"):
                validator = BooleanValidator()
            else:
                validator = NumericToleranceValidator(2, 5, "°C")

        return validator.validate(answer, ground_truth)

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> tuple:
        """
        Multi-day weather: fetch when AI visits the specific location's page.

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
