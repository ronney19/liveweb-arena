"""Location pool and metric definitions for Open Meteo templates."""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class City:
    """A city with coordinates for Open Meteo API."""
    name: str
    country: str
    latitude: float
    longitude: float

    @property
    def display_name(self) -> str:
        return self.name

    @property
    def coord_key(self) -> str:
        """Stable key for GT collector: rounded to 2 decimals."""
        return f"{self.latitude:.2f},{self.longitude:.2f}"

    def docs_url(self) -> str:
        """URL for the Open Meteo docs page with this city's coords.

        Uses query params for cache key uniqueness (normalize_url strips
        hash fragments) and hash fragment for client-side JS form state.
        """
        return (
            f"https://open-meteo.com/en/docs"
            f"?latitude={self.latitude}&longitude={self.longitude}"
            f"#latitude={self.latitude}&longitude={self.longitude}"
            f"&current=temperature_2m,wind_speed_10m,relative_humidity_2m"
            f"&hourly=temperature_2m,precipitation_probability,wind_speed_10m"
            f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset"
        )


# Geographic spread: ~50 cities across 6 regions
CITIES: List[City] = [
    # Asia
    City("Tokyo", "Japan", 35.6762, 139.6503),
    City("Beijing", "China", 39.9042, 116.4074),
    City("Seoul", "South Korea", 37.5665, 126.9780),
    City("Mumbai", "India", 19.0760, 72.8777),
    City("Singapore", "Singapore", 1.3521, 103.8198),
    City("Bangkok", "Thailand", 13.7563, 100.5018),
    City("Hong Kong", "China", 22.3193, 114.1694),
    City("Shanghai", "China", 31.2304, 121.4737),
    City("Delhi", "India", 28.7041, 77.1025),
    City("Jakarta", "Indonesia", -6.2088, 106.8456),
    # Europe
    City("London", "UK", 51.5074, -0.1278),
    City("Paris", "France", 48.8566, 2.3522),
    City("Berlin", "Germany", 52.5200, 13.4050),
    City("Madrid", "Spain", 40.4168, -3.7038),
    City("Rome", "Italy", 41.9028, 12.4964),
    City("Stockholm", "Sweden", 59.3293, 18.0686),
    City("Amsterdam", "Netherlands", 52.3676, 4.9041),
    City("Prague", "Czech Republic", 50.0755, 14.4378),
    City("Athens", "Greece", 37.9838, 23.7275),
    City("Helsinki", "Finland", 60.1699, 24.9384),
    # Americas
    City("New York", "USA", 40.7128, -74.0060),
    City("Los Angeles", "USA", 34.0522, -118.2437),
    City("Chicago", "USA", 41.8781, -87.6298),
    City("Toronto", "Canada", 43.6532, -79.3832),
    City("Mexico City", "Mexico", 19.4326, -99.1332),
    City("Buenos Aires", "Argentina", -34.6037, -58.3816),
    City("Miami", "USA", 25.7617, -80.1918),
    City("Seattle", "USA", 47.6062, -122.3321),
    City("Vancouver", "Canada", 49.2827, -123.1207),
    City("Houston", "USA", 29.7604, -95.3698),
    # Oceania
    City("Sydney", "Australia", -33.8688, 151.2093),
    City("Melbourne", "Australia", -37.8136, 144.9631),
    City("Auckland", "New Zealand", -36.8485, 174.7633),
    City("Brisbane", "Australia", -27.4698, 153.0251),
    # Africa / Middle East
    City("Dubai", "UAE", 25.2048, 55.2708),
    City("Cairo", "Egypt", 30.0444, 31.2357),
    City("Johannesburg", "South Africa", -26.2041, 28.0473),
    City("Istanbul", "Turkey", 41.0082, 28.9784),
    City("Lagos", "Nigeria", 6.5244, 3.3792),
    City("Nairobi", "Kenya", -1.2921, 36.8219),
]

# Pre-built city pairs from different climate zones for comparison templates
CITY_PAIRS: List[Tuple[City, City]] = [
    (CITIES[0], CITIES[10]),   # Tokyo vs London
    (CITIES[1], CITIES[11]),   # Beijing vs Paris
    (CITIES[4], CITIES[12]),   # Singapore vs Berlin
    (CITIES[3], CITIES[35]),   # Mumbai vs Cairo
    (CITIES[20], CITIES[21]),  # New York vs Los Angeles
    (CITIES[22], CITIES[26]),  # Chicago vs Miami
    (CITIES[23], CITIES[24]),  # Toronto vs Mexico City
    (CITIES[27], CITIES[29]),  # Seattle vs Houston
    (CITIES[30], CITIES[0]),   # Sydney vs Tokyo
    (CITIES[34], CITIES[15]),  # Dubai vs Stockholm
    (CITIES[6], CITIES[28]),   # Hong Kong vs Vancouver
    (CITIES[13], CITIES[18]),  # Madrid vs Athens
    (CITIES[14], CITIES[19]),  # Rome vs Helsinki
    (CITIES[16], CITIES[17]),  # Amsterdam vs Prague
    (CITIES[25], CITIES[36]),  # Buenos Aires vs Johannesburg
]


class CurrentMetric(Enum):
    """Metrics available from current_weather endpoint."""
    TEMPERATURE = ("temperature", "current temperature", "°C")
    WIND_SPEED = ("windspeed", "current wind speed", "km/h")

    @property
    def api_field(self) -> str:
        return self.value[0]

    @property
    def display_name(self) -> str:
        return self.value[1]

    @property
    def unit(self) -> str:
        return self.value[2]
