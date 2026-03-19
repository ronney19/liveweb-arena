"""Focused tests for the Open Meteo plugin and templates."""

import asyncio

import pytest

from liveweb_arena.core.cache import normalize_url
from liveweb_arena.core.gt_collector import GTCollector, GTSourceType, set_current_gt_collector
from liveweb_arena.core.task_registry import TaskRegistry
from liveweb_arena.core.validators.base import get_registered_templates
from liveweb_arena.plugins import get_all_plugins
from liveweb_arena.plugins.base import SubTask
from liveweb_arena.plugins.openmeteo.openmeteo import OpenMeteoPlugin
from liveweb_arena.plugins.openmeteo.templates.common import DOCS_HOME_URL
from liveweb_arena.plugins.openmeteo.templates.comparison import OpenMeteoComparisonTemplate
from liveweb_arena.plugins.openmeteo.templates.current_weather import OpenMeteoCurrentWeatherTemplate
from liveweb_arena.plugins.openmeteo.templates.forecast_trend import OpenMeteoForecastTrendTemplate
from liveweb_arena.plugins.openmeteo.templates.hourly_extrema import OpenMeteoHourlyExtremaTemplate
from liveweb_arena.plugins.openmeteo.templates.variables import CITIES


@pytest.fixture
def collector():
    gt_collector = GTCollector(
        subtasks=[SubTask(plugin_name="openmeteo", intent="test", validation_info={}, answer_tag="answer1")]
    )
    set_current_gt_collector(gt_collector)
    try:
        yield gt_collector
    finally:
        set_current_gt_collector(None)


def run_async(coro):
    return asyncio.run(coro)


def test_plugin_and_templates_registered():
    assert "openmeteo" in get_all_plugins()
    templates = get_registered_templates()
    for name in [
        "openmeteo_current",
        "openmeteo_comparison",
        "openmeteo_hourly_extrema",
        "openmeteo_forecast_trend",
    ]:
        assert name in templates


def test_coordinate_extraction_and_cache_keys():
    plugin = OpenMeteoPlugin()

    lat, lon = plugin._extract_coords(
        "https://open-meteo.com/en/docs#latitude=35.68&longitude=139.65&current=temperature_2m"
    )
    assert lat == 35.68
    assert lon == 139.65

    lat, lon = plugin._extract_coords(
        "https://open-meteo.com/en/docs?latitude=40.71&longitude=-74.01"
    )
    assert abs(lat - 40.71) < 0.001
    assert abs(lon - (-74.01)) < 0.001

    city1_url = "https://open-meteo.com/en/docs?latitude=35.68&longitude=139.65#latitude=35.68&longitude=139.65"
    city2_url = "https://open-meteo.com/en/docs?latitude=51.51&longitude=-0.13#latitude=51.51&longitude=-0.13"
    assert normalize_url(city1_url) != normalize_url(city2_url)


@pytest.mark.parametrize(
    ("template_cls", "expected_fields"),
    [
        (OpenMeteoCurrentWeatherTemplate, {"city_name", "coord_key", "metric_field", "unit"}),
        (OpenMeteoHourlyExtremaTemplate, {"city_name", "coord_key", "is_max"}),
        (OpenMeteoForecastTrendTemplate, {"city_name", "coord_key"}),
    ],
)
def test_interaction_first_templates_start_from_generic_docs(template_cls, expected_fields):
    question = template_cls().generate(42)
    assert question.start_url == DOCS_HOME_URL
    assert expected_fields.issubset(question.validation_info)
    assert question.expected_steps >= 6


def test_comparison_template_remains_city_specific():
    question = OpenMeteoComparisonTemplate().generate(42)
    assert question.start_url != DOCS_HOME_URL
    assert "latitude=" in question.start_url
    assert "city2_coord_key" in question.validation_info
    # Verify question asks for numeric difference, not binary choice
    assert "difference" in question.question_text.lower() or "degrees" in question.question_text.lower()


def test_current_weather_requires_city_visit():
    result = run_async(
        OpenMeteoCurrentWeatherTemplate().get_ground_truth(
            {
                "city_name": "Tokyo",
                "coord_key": "35.68,139.65",
                "metric_field": "temperature",
                "unit": "°C",
            }
        )
    )
    assert result.success is False
    assert result.is_data_not_collected()


def test_gt_collector_merges_openmeteo_pages(collector):
    fake_api_data = {
        "_location_key": "35.68,139.65",
        "current_weather": {"temperature": 12.5},
        "hourly": {"time": ["2026-03-17T00:00"], "temperature_2m": [12.5]},
        "daily": {"time": ["2026-03-17"], "temperature_2m_max": [16.0], "temperature_2m_min": [9.0]},
    }

    result = collector._merge_api_data(
        "https://open-meteo.com/en/docs?latitude=35.68&longitude=139.65",
        fake_api_data,
    )
    assert "weather[35.68,139.65]" in result
    assert "openmeteo:35.68,139.65" in collector.get_collected_api_data()


def test_hourly_extrema_uses_hourly_series_not_daily_summary(collector):
    collector._merge_api_data(
        "https://open-meteo.com/en/docs?latitude=35.68&longitude=139.65",
        {
            "_location_key": "35.68,139.65",
            "current_weather": {"temperature": 12.5, "time": "2026-03-17T09:00"},
            "daily": {
                "time": ["2026-03-17", "2026-03-18"],
                "temperature_2m_max": [99.0, 50.0],
                "temperature_2m_min": [-99.0, 0.0],
            },
            "hourly": {
                "time": [
                    "2026-03-17T00:00",
                    "2026-03-17T06:00",
                    "2026-03-17T12:00",
                    "2026-03-18T00:00",
                ],
                "temperature_2m": [8.0, 11.5, 14.0, 3.0],
            },
        },
    )

    tmpl = OpenMeteoHourlyExtremaTemplate()
    max_result = run_async(
        tmpl.get_ground_truth({"city_name": "Tokyo", "coord_key": "35.68,139.65", "is_max": True})
    )
    min_result = run_async(
        tmpl.get_ground_truth({"city_name": "Tokyo", "coord_key": "35.68,139.65", "is_max": False})
    )

    assert max_result.success is True
    assert max_result.value == "14.0°C"
    assert min_result.success is True
    assert min_result.value == "8.0°C"


def test_forecast_trend_uses_daily_values_after_city_visit(collector):
    collector._merge_api_data(
        "https://open-meteo.com/en/docs?latitude=35.68&longitude=139.65",
        {
            "_location_key": "35.68,139.65",
            "current_weather": {"temperature": 12.5, "time": "2026-03-17T09:00"},
            "daily": {
                "time": ["2026-03-17", "2026-03-18"],
                "temperature_2m_max": [15.2, 13.8],
            },
            "hourly": {
                "time": ["2026-03-17T00:00", "2026-03-17T06:00"],
                "temperature_2m": [8.0, 11.5],
            },
        },
    )

    result = run_async(
        OpenMeteoForecastTrendTemplate().get_ground_truth(
            {
                "city_name": "Tokyo",
                "coord_key": "35.68,139.65",
                "metric_field": "temperature_2m_max",
                "metric_label": "daily maximum temperature",
                "unit": "°C",
                "day1_idx": 0,
                "day2_idx": 1,
                "day1_label": "today",
                "day2_label": "tomorrow",
            }
        )
    )
    assert result.success is True
    assert "1.4" in result.value
    assert "Lower" in result.value or "lower" in result.value


def test_comparison_gt_returns_numeric_difference(collector):
    collector._merge_api_data(
        "https://open-meteo.com/en/docs?latitude=35.68&longitude=139.65",
        {
            "_location_key": "35.68,139.65",
            "current_weather": {"temperature": 12.5},
        },
    )
    collector._merge_api_data(
        "https://open-meteo.com/en/docs?latitude=51.51&longitude=-0.13",
        {
            "_location_key": "51.51,-0.13",
            "current_weather": {"temperature": 8.3},
        },
    )

    result = run_async(
        OpenMeteoComparisonTemplate().get_ground_truth(
            {
                "city1_name": "Tokyo",
                "city1_coord_key": "35.68,139.65",
                "city2_name": "London",
                "city2_coord_key": "51.51,-0.13",
            }
        )
    )
    assert result.success is True
    assert result.value == "4.2°C"  # 12.5 - 8.3 = 4.2


def test_registry_contains_openmeteo_templates():
    expected = {
        85: ("openmeteo", "openmeteo_current"),
        86: ("openmeteo", "openmeteo_comparison"),
        87: ("openmeteo", "openmeteo_hourly_extrema"),
        88: ("openmeteo", "openmeteo_forecast_trend"),
    }
    for template_id, template_info in expected.items():
        assert TaskRegistry.TEMPLATES[template_id] == template_info

    TaskRegistry._ensure_initialized()
    assert (85,) in TaskRegistry._combinations


def test_city_docs_urls_are_unique_and_parseable():
    plugin = OpenMeteoPlugin()
    seen = set()
    for city in CITIES:
        normalized = normalize_url(city.docs_url())
        assert normalized not in seen
        seen.add(normalized)

        lat, lon = plugin._extract_coords(city.docs_url())
        assert lat is not None
        assert lon is not None


def test_openmeteo_templates_expose_page_only_gt_source():
    assert OpenMeteoCurrentWeatherTemplate().get_gt_source() == GTSourceType.PAGE_ONLY
    assert OpenMeteoComparisonTemplate().get_gt_source() == GTSourceType.PAGE_ONLY
    assert OpenMeteoHourlyExtremaTemplate().get_gt_source() == GTSourceType.PAGE_ONLY
    assert OpenMeteoForecastTrendTemplate().get_gt_source() == GTSourceType.PAGE_ONLY
