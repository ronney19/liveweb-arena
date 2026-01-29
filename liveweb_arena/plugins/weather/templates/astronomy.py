"""Astronomy-related weather template (sunrise, sunset, moon phase)"""

import random
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)
from liveweb_arena.core.ground_truth_trigger import (
    UrlPatternTrigger, FetchStrategy, TriggerConfig, GroundTruthResult
)
from .variables import (
    LocationVariable, DateVariable,
    LocationType, DateType,
    LocationSpec, DateSpec,
)


class AstronomyMetric(Enum):
    """Types of astronomy metrics"""
    SUNRISE = "sunrise"
    SUNSET = "sunset"
    MOONRISE = "moonrise"
    MOONSET = "moonset"
    MOON_PHASE = "moon_phase"


@dataclass
class AstronomyMetricSpec:
    """Specification of an astronomy metric"""
    metric: AstronomyMetric
    display_name: str
    api_field: str
    is_time: bool  # True for time values, False for text (moon phase)


class AstronomyMetricVariable:
    """Variable for astronomy metric selection"""

    METRICS: Dict[AstronomyMetric, AstronomyMetricSpec] = {
        AstronomyMetric.SUNRISE: AstronomyMetricSpec(
            AstronomyMetric.SUNRISE, "sunrise time", "sunrise", is_time=True
        ),
        AstronomyMetric.SUNSET: AstronomyMetricSpec(
            AstronomyMetric.SUNSET, "sunset time", "sunset", is_time=True
        ),
        AstronomyMetric.MOONRISE: AstronomyMetricSpec(
            AstronomyMetric.MOONRISE, "moonrise time", "moonrise", is_time=True
        ),
        AstronomyMetric.MOONSET: AstronomyMetricSpec(
            AstronomyMetric.MOONSET, "moonset time", "moonset", is_time=True
        ),
        AstronomyMetric.MOON_PHASE: AstronomyMetricSpec(
            AstronomyMetric.MOON_PHASE, "moon phase", "moon_phase", is_time=False
        ),
    }

    def __init__(self, allowed_metrics: List[AstronomyMetric] = None):
        self.allowed_metrics = allowed_metrics or list(AstronomyMetric)

    def sample(self, rng: random.Random) -> AstronomyMetricSpec:
        metric = rng.choice(self.allowed_metrics)
        return self.METRICS[metric]

    def sample_by_index(self, index: int) -> AstronomyMetricSpec:
        metric = self.allowed_metrics[index % len(self.allowed_metrics)]
        return self.METRICS[metric]


@register_template("astronomy")
class AstronomyTemplate(QuestionTemplate):
    """
    Template for astronomy-related weather queries.

    Queries sunrise/sunset times, moon phase, moonrise/moonset.
    Data comes from wttr.in API's astronomy section.

    Examples:
    - What time is sunrise in Tokyo tomorrow?
    - What is the moon phase in London today?
    - When does the sun set in Paris tomorrow?
    """

    QUESTION_PATTERNS = {
        AstronomyMetric.SUNRISE: [
            "What time is sunrise in {location} {date}?",
            "When does the sun rise in {location} {date}?",
            "At what time will the sun rise in {location} {date}?",
        ],
        AstronomyMetric.SUNSET: [
            "What time is sunset in {location} {date}?",
            "When does the sun set in {location} {date}?",
            "At what time will the sun set in {location} {date}?",
        ],
        AstronomyMetric.MOONRISE: [
            "What time is moonrise in {location} {date}?",
            "When does the moon rise in {location} {date}?",
        ],
        AstronomyMetric.MOONSET: [
            "What time is moonset in {location} {date}?",
            "When does the moon set in {location} {date}?",
        ],
        AstronomyMetric.MOON_PHASE: [
            "What is the moon phase in {location} {date}?",
            "What phase is the moon in {location} {date}?",
        ],
    }

    def __init__(self, use_chinese: bool = False):
        super().__init__("astronomy")
        self.use_chinese = use_chinese

        self.register_variable(LocationVariable(
            allowed_types=[LocationType.CITY_NAME],
        ))
        # Only TODAY and TOMORROW - astronomy data is available for 3 days
        self.register_variable(DateVariable(
            max_forecast_days=2,
            allowed_types=[DateType.TODAY, DateType.TOMORROW],
            use_chinese=use_chinese,
        ))
        self._metric_var = AstronomyMetricVariable()

    def generate(self, seed: int, variant: Optional[int] = None) -> GeneratedQuestion:
        """Generate an astronomy question."""
        rng = random.Random(seed)

        location: LocationSpec = self._variables["location"].sample(rng)
        date: DateSpec = self._variables["date"].sample(rng)

        if variant is not None:
            metric = self._metric_var.sample_by_index(variant)
        else:
            metric = self._metric_var.sample(rng)

        question_text = self._build_question(location, date, metric, rng)
        start_url = f"https://wttr.in/{location.api_query}"

        validation_info = {
            "location": location.api_query,
            "target_date": date.api_date,
            "metric_type": metric.metric.value,
            "api_field": metric.api_field,
            "is_time": metric.is_time,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url=start_url,
            variables={"location": location, "date": date, "metric": metric},
            validation_info=validation_info,
            template_name=self.name,
        )

    def _build_question(
        self,
        location: LocationSpec,
        date: DateSpec,
        metric: AstronomyMetricSpec,
        rng: random.Random,
    ) -> str:
        patterns = self.QUESTION_PATTERNS[metric.metric]
        pattern = rng.choice(patterns)
        return pattern.format(
            location=location.display_name,
            date=date.display_text,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        metric_type = validation_info.get("metric_type", "")
        is_time = validation_info.get("is_time", True)

        if is_time:
            return f"""Task-Specific Rules (Astronomy - Time):
- Question asks for {metric_type} time
- Times are in local timezone of the location
- Score 1.0: Times match exactly or within 1 minute
- Score 0.5: Times differ by 2-5 minutes (acceptable rounding)
- Score 0.0: Times differ by more than 5 minutes or format is wrong
- Accept formats: 6:47 AM, 06:47, 6:47am, etc."""

        return f"""Task-Specific Rules (Astronomy - Moon Phase):
- Question asks for moon phase name
- Score 1.0: Phase names match (case-insensitive)
- Score 0.0: Phase names differ
- Valid phases: New Moon, Waxing Crescent, First Quarter, Waxing Gibbous,
  Full Moon, Waning Gibbous, Last Quarter, Waning Crescent"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> GroundTruthResult:
        """Get astronomy data from collected API data (no network fallback)."""
        location = validation_info["location"]
        target_date = validation_info["target_date"]
        api_field = validation_info["api_field"]

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

        # Find day by date
        day_data = None
        for day in weather:
            if day.get("date") == target_date:
                day_data = day
                break

        if day_data is None:
            return GroundTruthResult.fail(f"No data for date: {target_date}")

        astronomy = day_data.get("astronomy", [])
        if not astronomy:
            return GroundTruthResult.fail("No astronomy data available")

        astro_data = astronomy[0]
        value = astro_data.get(api_field)

        if value is None:
            return GroundTruthResult.fail(f"Missing {api_field} data")

        return GroundTruthResult.ok(value)

    async def validate_answer(
        self,
        answer: str,
        validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate astronomy answer"""
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
        is_time = validation_info.get("is_time", True)

        if is_time:
            return self._validate_time(answer, ground_truth)
        else:
            return self._validate_moon_phase(answer, ground_truth)

    def _validate_time(self, answer: str, expected: str) -> ValidationResult:
        """Validate time answer (e.g., '6:47 AM')"""
        # Parse expected time
        expected_minutes = self._parse_time_to_minutes(expected)
        if expected_minutes is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=expected,
                actual=answer,
                details=f"Could not parse expected time: {expected}",
            )

        # Parse answer time
        answer_minutes = self._parse_time_to_minutes(answer)
        if answer_minutes is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=expected,
                actual=answer,
                details="Could not parse time from answer",
            )

        # Calculate difference
        diff = abs(answer_minutes - expected_minutes)
        # Handle wraparound at midnight
        if diff > 12 * 60:
            diff = 24 * 60 - diff

        if diff <= 1:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=expected,
                actual=answer,
                details=f"Times match (diff: {diff} min)",
            )
        elif diff <= 5:
            return ValidationResult(
                score=0.5,
                is_correct=False,
                expected=expected,
                actual=answer,
                details=f"Times close but not exact (diff: {diff} min)",
            )
        else:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=expected,
                actual=answer,
                details=f"Times differ significantly (diff: {diff} min)",
            )

    def _parse_time_to_minutes(self, time_str: str) -> Optional[int]:
        """Parse time string to minutes since midnight"""
        if not time_str:
            return None

        time_str = time_str.strip().upper()

        # Try various formats
        patterns = [
            # 12-hour formats
            r'(\d{1,2}):(\d{2})\s*(AM|PM)',
            r'(\d{1,2}):(\d{2})(AM|PM)',
            r'(\d{1,2})(\d{2})\s*(AM|PM)',
            # 24-hour formats
            r'(\d{1,2}):(\d{2})',
            r'(\d{2})(\d{2})',
        ]

        for pattern in patterns:
            match = re.search(pattern, time_str)
            if match:
                groups = match.groups()
                hour = int(groups[0])
                minute = int(groups[1])

                # Check for AM/PM
                if len(groups) == 3:
                    period = groups[2]
                    if period == 'PM' and hour != 12:
                        hour += 12
                    elif period == 'AM' and hour == 12:
                        hour = 0

                return hour * 60 + minute

        return None

    def _validate_moon_phase(self, answer: str, expected: str) -> ValidationResult:
        """Validate moon phase answer"""
        # Normalize for comparison
        answer_norm = answer.lower().strip()
        expected_norm = expected.lower().strip()

        # Direct match
        if expected_norm in answer_norm or answer_norm in expected_norm:
            return ValidationResult(
                score=1.0,
                is_correct=True,
                expected=expected,
                actual=answer,
                details="Moon phase matches",
            )

        # Check for key terms
        phase_keywords = {
            "new moon": ["new"],
            "waxing crescent": ["waxing", "crescent"],
            "first quarter": ["first", "quarter"],
            "waxing gibbous": ["waxing", "gibbous"],
            "full moon": ["full"],
            "waning gibbous": ["waning", "gibbous"],
            "last quarter": ["last", "quarter", "third"],
            "waning crescent": ["waning", "crescent"],
        }

        for phase, keywords in phase_keywords.items():
            if phase in expected_norm:
                # Check if answer contains the key terms
                if all(kw in answer_norm for kw in keywords):
                    return ValidationResult(
                        score=1.0,
                        is_correct=True,
                        expected=expected,
                        actual=answer,
                        details="Moon phase matches (keyword match)",
                    )

        return ValidationResult(
            score=0.0,
            is_correct=False,
            expected=expected,
            actual=answer,
            details="Moon phase does not match",
        )

    def get_ground_truth_trigger(
        self,
        validation_info: Dict[str, Any]
    ) -> TriggerConfig:
        """Astronomy: fetch when AI visits the location's page."""
        location = validation_info.get("location", "")
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

