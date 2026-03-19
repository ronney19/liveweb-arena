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


# Geographic spread: 170 cities across 6 regions
CITIES: List[City] = [
    # ── Asia (original 10) ──
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
    # ── Europe (original 10) ──
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
    # ── Americas (original 10) ──
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
    # ── Oceania (original 4) ──
    City("Sydney", "Australia", -33.8688, 151.2093),
    City("Melbourne", "Australia", -37.8136, 144.9631),
    City("Auckland", "New Zealand", -36.8485, 174.7633),
    City("Brisbane", "Australia", -27.4698, 153.0251),
    # ── Africa / Middle East (original 6) ──
    City("Dubai", "UAE", 25.2048, 55.2708),
    City("Cairo", "Egypt", 30.0444, 31.2357),
    City("Johannesburg", "South Africa", -26.2041, 28.0473),
    City("Istanbul", "Turkey", 41.0082, 28.9784),
    City("Lagos", "Nigeria", 6.5244, 3.3792),
    City("Nairobi", "Kenya", -1.2921, 36.8219),
    # ── Asia (add 15) ──
    City("Taipei", "Taiwan", 25.03, 121.57),
    City("Osaka", "Japan", 34.69, 135.50),
    City("Hanoi", "Vietnam", 21.03, 105.85),
    City("Kuala Lumpur", "Malaysia", 3.14, 101.69),
    City("Kolkata", "India", 22.57, 88.36),
    City("Dhaka", "Bangladesh", 23.81, 90.41),
    City("Karachi", "Pakistan", 24.86, 67.01),
    City("Manila", "Philippines", 14.60, 120.98),
    City("Chengdu", "China", 30.57, 104.07),
    City("Riyadh", "Saudi Arabia", 24.71, 46.68),
    City("Tel Aviv", "Israel", 32.08, 34.78),
    City("Doha", "Qatar", 25.29, 51.53),
    City("Ho Chi Minh City", "Vietnam", 10.82, 106.63),
    City("Ulaanbaatar", "Mongolia", 47.92, 106.91),
    City("Almaty", "Kazakhstan", 43.24, 76.95),
    # ── Europe (add 15) ──
    City("Vienna", "Austria", 48.21, 16.37),
    City("Warsaw", "Poland", 52.23, 21.01),
    City("Zurich", "Switzerland", 47.38, 8.54),
    City("Dublin", "Ireland", 53.35, -6.26),
    City("Lisbon", "Portugal", 38.72, -9.14),
    City("Copenhagen", "Denmark", 55.68, 12.57),
    City("Brussels", "Belgium", 50.85, 4.35),
    City("Bucharest", "Romania", 44.43, 26.10),
    City("Oslo", "Norway", 59.91, 10.75),
    City("Budapest", "Hungary", 47.50, 19.04),
    City("Edinburgh", "UK", 55.95, -3.19),
    City("Milan", "Italy", 45.46, 9.19),
    City("Barcelona", "Spain", 41.39, 2.17),
    City("Munich", "Germany", 48.14, 11.58),
    City("Reykjavik", "Iceland", 64.15, -21.94),
    # ── Americas (add 15) ──
    City("Denver", "USA", 39.74, -104.99),
    City("Boston", "USA", 42.36, -71.06),
    City("Atlanta", "USA", 33.75, -84.39),
    City("Phoenix", "USA", 33.45, -112.07),
    City("San Francisco", "USA", 37.77, -122.42),
    City("Lima", "Peru", -12.05, -77.04),
    City("Santiago", "Chile", -33.45, -70.67),
    City("Bogota", "Colombia", 4.71, -74.07),
    City("Montreal", "Canada", 45.50, -73.57),
    City("Havana", "Cuba", 23.11, -82.37),
    City("Anchorage", "USA", 61.22, -149.90),
    City("Honolulu", "USA", 21.31, -157.86),
    City("Portland", "USA", 45.52, -122.68),
    City("Minneapolis", "USA", 44.98, -93.27),
    City("Nashville", "USA", 36.16, -86.78),
    # ── Africa / Middle East (add 10) ──
    City("Addis Ababa", "Ethiopia", 9.02, 38.75),
    City("Casablanca", "Morocco", 33.57, -7.59),
    City("Accra", "Ghana", 5.60, -0.19),
    City("Dar es Salaam", "Tanzania", -6.79, 39.28),
    City("Algiers", "Algeria", 36.74, 3.06),
    City("Tunis", "Tunisia", 36.81, 10.18),
    City("Kigali", "Rwanda", -1.94, 30.06),
    City("Lusaka", "Zambia", -15.39, 28.32),
    City("Maputo", "Mozambique", -25.97, 32.57),
    City("Kampala", "Uganda", 0.35, 32.58),
    # ── Oceania (add 5) ──
    City("Perth", "Australia", -31.95, 115.86),
    City("Wellington", "New Zealand", -41.29, 174.78),
    City("Christchurch", "New Zealand", -43.53, 172.64),
    City("Adelaide", "Australia", -34.93, 138.60),
    City("Suva", "Fiji", -18.14, 178.44),
    # ── Extreme climates (add 5) ──
    City("Novosibirsk", "Russia", 55.03, 82.92),
    City("Murmansk", "Russia", 68.97, 33.09),
    City("Yakutsk", "Russia", 62.03, 129.73),
    City("Manaus", "Brazil", -3.12, -60.02),
    City("Lhasa", "China", 29.65, 91.17),
    # ── More Americas (add 10) ──
    City("Washington DC", "USA", 38.91, -77.04),
    City("Dallas", "USA", 32.78, -96.80),
    City("Philadelphia", "USA", 39.95, -75.17),
    City("Las Vegas", "USA", 36.17, -115.14),
    City("San Diego", "USA", 32.72, -117.16),
    City("Ottawa", "Canada", 45.42, -75.70),
    City("Guadalajara", "Mexico", 20.67, -103.35),
    City("Medellin", "Colombia", 6.25, -75.56),
    City("Montevideo", "Uruguay", -34.91, -56.19),
    City("Quito", "Ecuador", -0.18, -78.47),
    # ── More Europe (add 10) ──
    City("Marseille", "France", 43.30, 5.37),
    City("Hamburg", "Germany", 53.55, 10.00),
    City("Lyon", "France", 45.76, 4.84),
    City("Krakow", "Poland", 50.06, 19.94),
    City("Sofia", "Bulgaria", 42.70, 23.32),
    City("Belgrade", "Serbia", 44.79, 20.47),
    City("Riga", "Latvia", 56.95, 24.11),
    City("Vilnius", "Lithuania", 54.69, 25.28),
    City("Tallinn", "Estonia", 59.44, 24.75),
    City("Porto", "Portugal", 41.16, -8.63),
    # ── More Asia (add 10) ──
    City("Shenzhen", "China", 22.54, 114.06),
    City("Nagoya", "Japan", 35.18, 136.91),
    City("Lahore", "Pakistan", 31.55, 74.35),
    City("Jeddah", "Saudi Arabia", 21.49, 39.19),
    City("Baku", "Azerbaijan", 40.41, 49.87),
    City("Tashkent", "Uzbekistan", 41.30, 69.28),
    City("Yangon", "Myanmar", 16.87, 96.20),
    City("Phnom Penh", "Cambodia", 11.56, 104.92),
    City("Colombo", "Sri Lanka", 6.93, 79.84),
    City("Kathmandu", "Nepal", 27.72, 85.32),
    # ── More Africa (add 10) ──
    City("Kinshasa", "DRC", -4.44, 15.27),
    City("Luanda", "Angola", -8.84, 13.23),
    City("Dakar", "Senegal", 14.69, -17.44),
    City("Abidjan", "Ivory Coast", 5.36, -4.01),
    City("Harare", "Zimbabwe", -17.83, 31.05),
    City("Rabat", "Morocco", 34.02, -6.84),
    City("Windhoek", "Namibia", -22.56, 17.08),
    City("Antananarivo", "Madagascar", -18.91, 47.52),
    City("Douala", "Cameroon", 4.05, 9.77),
    City("Bamako", "Mali", 12.65, -8.00),
    # ── More Oceania / Pacific (add 5) ──
    City("Darwin", "Australia", -12.46, 130.84),
    City("Nadi", "Fiji", -17.78, 177.94),
    City("Port Moresby", "Papua New Guinea", -6.21, 147.00),
    City("Noumea", "New Caledonia", -22.28, 166.46),
    City("Apia", "Samoa", -13.83, -171.76),
    # ── More extreme / misc (add 10) ──
    City("Tromsoe", "Norway", 69.65, 18.96),
    City("Fairbanks", "USA", 64.84, -147.72),
    City("Ushuaia", "Argentina", -54.80, -68.30),
    City("Barranquilla", "Colombia", 10.96, -74.78),
    City("Marrakech", "Morocco", 31.63, -8.01),
    City("Sapporo", "Japan", 43.06, 141.35),
    City("Vladivostok", "Russia", 43.12, 131.87),
    City("Irkutsk", "Russia", 52.29, 104.28),
    City("Astana", "Kazakhstan", 51.17, 71.43),
    City("Tbilisi", "Georgia", 41.69, 44.80),
    # ── Additional cities to reach 170 ──
    City("Pune", "India", 18.52, 73.86),
    City("Brasilia", "Brazil", -15.79, -47.88),
    City("Thessaloniki", "Greece", 40.64, 22.94),
    City("Naypyidaw", "Myanmar", 19.76, 96.07),
    City("Busan", "South Korea", 35.18, 129.08),
    City("Cusco", "Peru", -13.53, -71.97),
    City("Zanzibar", "Tanzania", -6.16, 39.19),
    City("Reims", "France", 49.25, 4.03),
    City("Split", "Croatia", 43.51, 16.44),
    City("Bergen", "Norway", 60.39, 5.32),
]


class CurrentMetric(Enum):
    """Metrics available from current_weather endpoint."""
    TEMPERATURE = ("temperature", "current temperature", "°C")
    WIND_SPEED = ("windspeed", "current wind speed", "km/h")
    WIND_DIRECTION = ("winddirection", "current wind direction", "°")

    @property
    def api_field(self) -> str:
        return self.value[0]

    @property
    def display_name(self) -> str:
        return self.value[1]

    @property
    def unit(self) -> str:
        return self.value[2]


class HourlyMetric(Enum):
    """Metrics available from hourly forecast data."""
    TEMPERATURE = ("temperature_2m", "hourly temperature", "°C")
    HUMIDITY = ("relative_humidity_2m", "hourly relative humidity", "%")
    WIND_SPEED = ("wind_speed_10m", "hourly wind speed", "km/h")
    PRECIP_PROBABILITY = ("precipitation_probability", "hourly precipitation probability", "%")

    @property
    def api_field(self) -> str:
        return self.value[0]

    @property
    def display_name(self) -> str:
        return self.value[1]

    @property
    def unit(self) -> str:
        return self.value[2]


class DailyMetric(Enum):
    """Metrics available from daily forecast data."""
    TEMP_MAX = ("temperature_2m_max", "daily maximum temperature", "°C")
    TEMP_MIN = ("temperature_2m_min", "daily minimum temperature", "°C")
    PRECIP_PROB_MAX = ("precipitation_probability_max", "daily max precipitation probability", "%")

    @property
    def api_field(self) -> str:
        return self.value[0]

    @property
    def display_name(self) -> str:
        return self.value[1]

    @property
    def unit(self) -> str:
        return self.value[2]
