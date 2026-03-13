"""
Comprehensive integration tests for the Open Meteo plugin.

Tests:
1. Plugin discovery and registration
2. URL coordinate extraction (hash, query, edge cases)
3. API client (live call)
4. Template generation (all 4 templates, multiple seeds)
5. GT collector merge logic
6. GT extraction from collected data (end-to-end)
7. Task registry entries
"""

import asyncio
import random
import sys
import traceback

# ── helpers ──────────────────────────────────────────────────────────────

PASS = 0
FAIL = 0


def ok(label):
    global PASS
    PASS += 1
    print(f"  ✓ {label}")


def fail(label, detail=""):
    global FAIL
    FAIL += 1
    msg = f"  ✗ {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


# ── 1. Plugin discovery ─────────────────────────────────────────────────

section("1. Plugin discovery & template registration")

from liveweb_arena.plugins import get_all_plugins
from liveweb_arena.core.validators.base import get_registered_templates

plugins = get_all_plugins()
plugin_names = list(plugins.keys())

if "openmeteo" in plugin_names:
    ok("openmeteo plugin discovered")
else:
    fail("openmeteo plugin NOT discovered", f"found: {plugin_names}")

templates = get_registered_templates()
expected_templates = [
    "openmeteo_current",
    "openmeteo_comparison",
    "openmeteo_hourly_extrema",
    "openmeteo_forecast_trend",
]
for t in expected_templates:
    if t in templates:
        ok(f"template '{t}' registered")
    else:
        fail(f"template '{t}' NOT registered", f"available: {list(templates.keys())[:10]}")


# ── 2. URL coordinate extraction ────────────────────────────────────────

section("2. URL coordinate extraction")

from liveweb_arena.plugins.openmeteo.openmeteo import OpenMeteoPlugin

plugin = OpenMeteoPlugin()

# Hash fragment
lat, lon = plugin._extract_coords(
    "https://open-meteo.com/en/docs#latitude=35.68&longitude=139.65&current=temperature_2m"
)
if lat == 35.68 and lon == 139.65:
    ok("hash fragment extraction: (35.68, 139.65)")
else:
    fail("hash fragment extraction", f"got ({lat}, {lon})")

# Query params
lat, lon = plugin._extract_coords(
    "https://open-meteo.com/en/docs?latitude=40.71&longitude=-74.01"
)
if abs(lat - 40.71) < 0.001 and abs(lon - (-74.01)) < 0.001:
    ok("query param extraction: (40.71, -74.01)")
else:
    fail("query param extraction", f"got ({lat}, {lon})")

# No coords
lat, lon = plugin._extract_coords("https://open-meteo.com/en/docs")
if lat is None and lon is None:
    ok("no coords → (None, None)")
else:
    fail("no coords should return None", f"got ({lat}, {lon})")

# needs_api_data
if plugin.needs_api_data("https://open-meteo.com/en/docs#latitude=35.68&longitude=139.65"):
    ok("needs_api_data=True for URL with coords")
else:
    fail("needs_api_data should be True for URL with coords")

if not plugin.needs_api_data("https://open-meteo.com/en/docs"):
    ok("needs_api_data=False for URL without coords")
else:
    fail("needs_api_data should be False for URL without coords")

# Blocked patterns — empty (docs page JS needs API access for chart rendering)
blocked = plugin.get_blocked_patterns()
if len(blocked) == 0:
    ok("No blocked patterns (docs page JS needs API access)")
else:
    fail("Expected no blocked patterns", f"got: {blocked}")

# Cache key uniqueness — different cities must produce different normalized URLs
from liveweb_arena.core.cache import normalize_url

city1_url = "https://open-meteo.com/en/docs?latitude=35.68&longitude=139.65#latitude=35.68&longitude=139.65"
city2_url = "https://open-meteo.com/en/docs?latitude=51.51&longitude=-0.13#latitude=51.51&longitude=-0.13"
norm1 = normalize_url(city1_url)
norm2 = normalize_url(city2_url)
if norm1 != norm2:
    ok(f"Cache keys differ: '{norm1}' != '{norm2}'")
else:
    fail("CRITICAL: Cache keys are identical — all cities map to same cache entry!", f"both = '{norm1}'")


# ── 3. API client live call ──────────────────────────────────────────────

section("3. API client (live call)")

from liveweb_arena.plugins.openmeteo.api_client import fetch_forecast, OpenMeteoClient


async def test_api():
    data = await fetch_forecast(35.68, 139.65)
    await OpenMeteoClient.close_session()

    # Check structure
    for key in ["current_weather", "hourly", "daily", "latitude", "longitude"]:
        if key in data:
            ok(f"API response has '{key}'")
        else:
            fail(f"API response missing '{key}'", f"keys: {list(data.keys())}")

    cw = data["current_weather"]
    for field in ["temperature", "windspeed", "winddirection", "is_day", "weathercode"]:
        if field in cw:
            ok(f"current_weather has '{field}' = {cw[field]}")
        else:
            fail(f"current_weather missing '{field}'")

    daily = data["daily"]
    for field in ["temperature_2m_max", "temperature_2m_min", "sunrise", "sunset"]:
        if field in daily:
            vals = daily[field]
            if isinstance(vals, list) and len(vals) >= 2:
                ok(f"daily.{field} has {len(vals)} days: {vals[:3]}")
            else:
                fail(f"daily.{field} should be list with ≥2 entries", f"got: {vals}")
        else:
            fail(f"daily missing '{field}'")

    hourly = data["hourly"]
    for field in ["temperature_2m", "relative_humidity_2m", "wind_speed_10m"]:
        if field in hourly:
            vals = hourly[field]
            if isinstance(vals, list) and len(vals) >= 24:
                ok(f"hourly.{field} has {len(vals)} hours")
            else:
                fail(f"hourly.{field} should have ≥24 entries", f"got len={len(vals)}")
        else:
            fail(f"hourly missing '{field}'")


asyncio.run(test_api())


# ── 4. Template generation (all 4 templates, multiple seeds) ─────────────

section("4. Template generation")

from liveweb_arena.plugins.openmeteo.templates.current_weather import OpenMeteoCurrentWeatherTemplate
from liveweb_arena.plugins.openmeteo.templates.comparison import OpenMeteoComparisonTemplate
from liveweb_arena.plugins.openmeteo.templates.hourly_extrema import OpenMeteoHourlyExtremaTemplate
from liveweb_arena.plugins.openmeteo.templates.forecast_trend import OpenMeteoForecastTrendTemplate

seeds = [1, 42, 100, 999, 12345]


def test_template_generation(cls, template_name, expected_fields):
    tmpl = cls()
    for seed in seeds:
        q = tmpl.generate(seed)
        errors = []
        if not q.question_text:
            errors.append("empty question_text")
        if not q.start_url or "open-meteo.com" not in q.start_url:
            errors.append(f"bad start_url: {q.start_url}")
        if q.template_name != template_name:
            errors.append(f"template_name={q.template_name}, expected {template_name}")
        for fld in expected_fields:
            if fld not in q.validation_info:
                errors.append(f"missing validation_info['{fld}']")
        if errors:
            fail(f"{template_name} seed={seed}", "; ".join(errors))
        else:
            ok(f"{template_name} seed={seed}: '{q.question_text[:60]}...'")
    return tmpl


test_template_generation(
    OpenMeteoCurrentWeatherTemplate, "openmeteo_current",
    ["city_name", "coord_key", "metric_field", "unit"]
)

test_template_generation(
    OpenMeteoComparisonTemplate, "openmeteo_comparison",
    ["city1_name", "city1_coord_key", "city2_name", "city2_coord_key"]
)

test_template_generation(
    OpenMeteoHourlyExtremaTemplate, "openmeteo_hourly_extrema",
    ["city_name", "coord_key", "is_max"]
)

test_template_generation(
    OpenMeteoForecastTrendTemplate, "openmeteo_forecast_trend",
    ["city_name", "coord_key"]
)


# ── 5. GT collector merge logic ──────────────────────────────────────────

section("5. GT collector merge logic")

from liveweb_arena.core.gt_collector import GTCollector, set_current_gt_collector, get_current_gt_collector
from liveweb_arena.plugins.base import SubTask

# Create a minimal subtask for the GTCollector
mock_subtask = SubTask(
    plugin_name="openmeteo",
    intent="test",
    validation_info={},
    answer_tag="answer1",
)
collector = GTCollector(subtasks=[mock_subtask])
set_current_gt_collector(collector)

# Simulate a page visit with Open Meteo API data
fake_api_data = {
    "_location_key": "35.68,139.65",
    "current_weather": {"temperature": 12.5, "windspeed": 8.3, "winddirection": 180, "is_day": 1, "weathercode": 0},
    "daily": {
        "temperature_2m_max": [15.2, 13.8, 16.1],
        "temperature_2m_min": [8.1, 7.2, 9.0],
    },
    "hourly": {
        "temperature_2m": list(range(24)),
    },
}

result = collector._merge_api_data("https://open-meteo.com/en/docs#latitude=35.68&longitude=139.65", fake_api_data)
if result and "weather" in result:
    ok(f"merge returned: '{result}'")
else:
    fail(f"merge should return weather description", f"got: {result}")

# Check data is stored
stored = collector.get_collected_api_data()
if "openmeteo:35.68,139.65" in stored:
    ok("data stored as 'openmeteo:35.68,139.65'")
    stored_data = stored["openmeteo:35.68,139.65"]
    if stored_data.get("current_weather", {}).get("temperature") == 12.5:
        ok("stored temperature = 12.5")
    else:
        fail("stored temperature wrong", f"got: {stored_data.get('current_weather')}")
else:
    fail("data not stored", f"keys: {list(stored.keys())}")


# ── 6. GT extraction end-to-end ─────────────────────────────────────────

section("6. GT extraction (end-to-end)")


async def test_gt_extraction():
    # Add a second city for comparison template
    fake_api_data2 = {
        "_location_key": "51.51,-0.13",
        "current_weather": {"temperature": 8.0, "windspeed": 15.2, "winddirection": 270, "is_day": 1, "weathercode": 3},
        "daily": {
            "temperature_2m_max": [10.5, 12.0, 9.8],
            "temperature_2m_min": [4.2, 5.1, 3.8],
        },
        "hourly": {
            "temperature_2m": list(range(24)),
        },
    }
    collector._merge_api_data("https://open-meteo.com/en/docs#latitude=51.51&longitude=-0.13", fake_api_data2)

    # Test openmeteo_current GT
    tmpl_current = OpenMeteoCurrentWeatherTemplate()
    gt = await tmpl_current.get_ground_truth({
        "city_name": "Tokyo",
        "coord_key": "35.68,139.65",
        "metric_field": "temperature",
        "unit": "°C",
    })
    if gt.value and "12.5" in gt.value:
        ok(f"current GT: '{gt.value}'")
    else:
        fail(f"current GT wrong", f"got: {gt}")

    # Test wind speed
    gt_wind = await tmpl_current.get_ground_truth({
        "city_name": "Tokyo",
        "coord_key": "35.68,139.65",
        "metric_field": "windspeed",
        "unit": "km/h",
    })
    if gt_wind.value and "8.3" in gt_wind.value:
        ok(f"wind speed GT: '{gt_wind.value}'")
    else:
        fail(f"wind speed GT wrong", f"got: {gt_wind}")

    # Test openmeteo_comparison GT
    tmpl_comp = OpenMeteoComparisonTemplate()
    gt_comp = await tmpl_comp.get_ground_truth({
        "city1_name": "Tokyo",
        "city1_coord_key": "35.68,139.65",
        "city2_name": "London",
        "city2_coord_key": "51.51,-0.13",
    })
    if gt_comp.value == "Tokyo":
        ok(f"comparison GT: '{gt_comp.value}'")
    else:
        fail(f"comparison GT wrong", f"expected 'Tokyo', got: {gt_comp}")

    # Test openmeteo_hourly_extrema GT (max)
    tmpl_extrema = OpenMeteoHourlyExtremaTemplate()
    gt_max = await tmpl_extrema.get_ground_truth({
        "city_name": "Tokyo",
        "coord_key": "35.68,139.65",
        "is_max": True,
    })
    if gt_max.value and "15.2" in gt_max.value:
        ok(f"hourly extrema (max) GT: '{gt_max.value}'")
    else:
        fail(f"hourly extrema (max) GT wrong", f"got: {gt_max}")

    # Test openmeteo_hourly_extrema GT (min)
    gt_min = await tmpl_extrema.get_ground_truth({
        "city_name": "Tokyo",
        "coord_key": "35.68,139.65",
        "is_max": False,
    })
    if gt_min.value and "8.1" in gt_min.value:
        ok(f"hourly extrema (min) GT: '{gt_min.value}'")
    else:
        fail(f"hourly extrema (min) GT wrong", f"got: {gt_min}")

    # Test openmeteo_forecast_trend GT
    tmpl_trend = OpenMeteoForecastTrendTemplate()
    gt_trend = await tmpl_trend.get_ground_truth({
        "city_name": "Tokyo",
        "coord_key": "35.68,139.65",
    })
    if gt_trend.value and "Colder" in gt_trend.value:
        # Today max=15.2, tomorrow max=13.8 → colder by 1.4°C
        ok(f"forecast trend GT: '{gt_trend.value}'")
    else:
        fail(f"forecast trend GT wrong", f"got: {gt_trend}")

    # Test not_collected case
    gt_missing = await tmpl_current.get_ground_truth({
        "city_name": "Mars",
        "coord_key": "0.00,0.00",
        "metric_field": "temperature",
        "unit": "°C",
    })
    if not gt_missing.success and gt_missing.failure_type is not None:
        ok(f"missing city → not_collected (failure_type={gt_missing.failure_type.value})")
    else:
        fail(f"missing city should fail", f"got: success={gt_missing.success}")


asyncio.run(test_gt_extraction())


# ── 7. Task registry ────────────────────────────────────────────────────

section("7. Task registry entries")

from liveweb_arena.core.task_registry import TaskRegistry

registry_templates = TaskRegistry.TEMPLATES
expected_ids = {85: "openmeteo_current", 86: "openmeteo_comparison", 87: "openmeteo_hourly_extrema", 88: "openmeteo_forecast_trend"}

for tid, expected_name in expected_ids.items():
    if tid in registry_templates:
        actual = registry_templates[tid]
        if actual[0] == "openmeteo" and actual[1] == expected_name:
            ok(f"ID {tid}: {actual}")
        else:
            fail(f"ID {tid} mismatch", f"expected ('openmeteo', '{expected_name}'), got {actual}")
    else:
        fail(f"ID {tid} not in TEMPLATES")

# Check Open Meteo version entry in TEMPLATE_VERSIONS
versions = TaskRegistry.TEMPLATE_VERSIONS
# Find the version entry containing our Open Meteo IDs
om_version_idx = None
for idx, v in enumerate(versions):
    if sorted(v) == [85, 86, 87, 88]:
        om_version_idx = idx
        break
if om_version_idx is not None:
    ok(f"TEMPLATE_VERSIONS[{om_version_idx}] = {versions[om_version_idx]}")
else:
    fail(f"Open Meteo IDs [85,86,87,88] not found in any TEMPLATE_VERSIONS entry")

# Check task_id parsing works for openmeteo templates
stats = TaskRegistry.get_stats()
ok(f"Registry stats: {stats['num_combinations']} combinations, max_task_id={stats['max_task_id']}")

# Parse a task_id that should map to an openmeteo template (single-template combo)
# Find the combo index for template ID 85 (openmeteo_current)
for i, combo in enumerate(TaskRegistry._combinations):
    if combo == (85,):
        task_id = i * TaskRegistry.TASK_IDS_PER_COMBO + 1
        config = TaskRegistry.parse_task_id(task_id)
        if config["template_ids"] == (85,):
            ok(f"task_id={task_id} → openmeteo_current (combo_index={i})")
        else:
            fail(f"task_id={task_id} parsed wrong", f"got: {config}")
        break
else:
    fail("Could not find single-template combo for ID 85")


# ── 8. Cross-template consistency ────────────────────────────────────────

section("8. Cross-template consistency")

from liveweb_arena.plugins.openmeteo.templates.variables import CITIES, CITY_PAIRS

# Verify CITY_PAIRS indices are valid
for i, (c1, c2) in enumerate(CITY_PAIRS):
    if c1 not in CITIES:
        fail(f"CITY_PAIRS[{i}][0] = {c1.name} not in CITIES")
    if c2 not in CITIES:
        fail(f"CITY_PAIRS[{i}][1] = {c2.name} not in CITIES")
if FAIL == 0:
    ok(f"All {len(CITY_PAIRS)} city pairs reference valid cities")

# Verify all cities have valid coords
for city in CITIES:
    if not (-90 <= city.latitude <= 90):
        fail(f"{city.name} latitude {city.latitude} out of range")
    if not (-180 <= city.longitude <= 180):
        fail(f"{city.name} longitude {city.longitude} out of range")
    url = city.docs_url()
    lat, lon = plugin._extract_coords(url)
    if lat is None:
        fail(f"{city.name} docs_url() doesn't parse: {url}")
ok(f"All {len(CITIES)} cities have valid coordinates and parseable URLs")

# Verify coord_key roundtrip
for city in CITIES:
    key = city.coord_key
    parts = key.split(",")
    if len(parts) != 2:
        fail(f"{city.name} coord_key format wrong: {key}")
ok(f"All {len(CITIES)} cities have valid coord_keys")

# Verify docs_url() produces unique normalized cache keys for ALL cities
seen_keys = {}
for city in CITIES:
    norm = normalize_url(city.docs_url())
    if norm in seen_keys:
        fail(f"Cache collision: {city.name} and {seen_keys[norm]} normalize to same key")
    seen_keys[norm] = city.name
ok(f"All {len(CITIES)} cities produce unique cache keys")

# Verify coord_key roundtrip: docs_url → _extract_coords → format → matches coord_key
for city in CITIES:
    url = city.docs_url()
    lat, lon = plugin._extract_coords(url)
    reconstructed_key = f"{lat:.2f},{lon:.2f}"
    if reconstructed_key != city.coord_key:
        fail(f"{city.name}: coord_key mismatch: {reconstructed_key} != {city.coord_key}")
ok(f"All {len(CITIES)} cities pass coord_key roundtrip (docs_url → extract → format)")

# Verify all templates have get_gt_source, get_cache_source, get_ground_truth_trigger
for cls in [OpenMeteoCurrentWeatherTemplate, OpenMeteoComparisonTemplate,
            OpenMeteoHourlyExtremaTemplate, OpenMeteoForecastTrendTemplate]:
    tmpl = cls()
    if tmpl.get_gt_source().value == "page_only":
        ok(f"{cls.__name__}.get_gt_source() = PAGE_ONLY")
    else:
        fail(f"{cls.__name__}.get_gt_source() wrong")
    if cls.get_cache_source() == "openmeteo":
        ok(f"{cls.__name__}.get_cache_source() = 'openmeteo'")
    else:
        fail(f"{cls.__name__}.get_cache_source() wrong")

# ── Summary ──────────────────────────────────────────────────────────────

print(f"\n{'=' * 60}")
print(f"  RESULTS: {PASS} passed, {FAIL} failed")
print(f"{'=' * 60}")

if FAIL > 0:
    sys.exit(1)
