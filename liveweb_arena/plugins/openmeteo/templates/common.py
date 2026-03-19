"""Shared helpers for Open Meteo templates."""

from typing import Any, Dict, List, Optional, Tuple

from liveweb_arena.core.ground_truth_trigger import GroundTruthResult
from liveweb_arena.core.gt_collector import get_current_gt_collector

DOCS_HOME_URL = "https://open-meteo.com/en/docs"


def get_collected_location_data(
    coord_key: str,
    city_name: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[GroundTruthResult]]:
    """Return collected API data for a visited city, or a GT failure."""
    gt_collector = get_current_gt_collector()
    if gt_collector is None:
        return None, GroundTruthResult.fail("No GT collector")

    collected = gt_collector.get_collected_api_data()
    data = collected.get(f"openmeteo:{coord_key}")
    if data is None:
        keys = [k for k in collected if k.startswith("openmeteo:")][:5]
        return None, GroundTruthResult.not_collected(
            f"Agent did not visit Open Meteo page for '{city_name}'. Collected keys: {keys}"
        )

    return data, None


def get_today_hourly_series(
    data: Dict[str, Any],
    field_name: str,
) -> Tuple[Optional[List[float]], Optional[GroundTruthResult]]:
    """Extract today's hourly values for the given field from API data.

    Returns (values, None) on success, or (None, failure_result) on error.
    """
    hourly = data.get("hourly")
    if not hourly:
        return None, GroundTruthResult.fail("No hourly data in API response")

    times = hourly.get("time")
    series = hourly.get(field_name)
    if not isinstance(times, list) or not isinstance(series, list):
        return None, GroundTruthResult.fail(
            f"Hourly data missing time/{field_name} arrays"
        )
    if len(times) != len(series):
        return None, GroundTruthResult.fail(
            f"Hourly time and {field_name} arrays differ in length"
        )
    if not times:
        return None, GroundTruthResult.fail("Hourly forecast is empty")

    # Determine today's date from the data
    today = None
    current = data.get("current_weather")
    if isinstance(current, dict):
        current_time = current.get("time")
        if isinstance(current_time, str) and "T" in current_time:
            today = current_time.split("T", 1)[0]

    if not today:
        daily = data.get("daily")
        daily_times = daily.get("time") if isinstance(daily, dict) else None
        if isinstance(daily_times, list) and daily_times:
            today = str(daily_times[0]).split("T", 1)[0]

    if not today:
        today = str(times[0]).split("T", 1)[0]

    values: List[float] = []
    for time_str, val in zip(times, series):
        if not isinstance(time_str, str) or not time_str.startswith(today):
            continue
        if val is None:
            continue
        try:
            values.append(float(val))
        except (TypeError, ValueError):
            return None, GroundTruthResult.fail(
                f"Non-numeric value in hourly {field_name}: {val!r}"
            )

    if not values:
        return None, GroundTruthResult.fail(
            f"No hourly {field_name} data found for today ({today})"
        )

    return values, None


def get_today_hourly_temperatures(
    data: Dict[str, Any],
) -> Tuple[Optional[List[float]], Optional[GroundTruthResult]]:
    """Extract today's hourly temperatures from a collected API payload."""
    return get_today_hourly_series(data, "temperature_2m")
